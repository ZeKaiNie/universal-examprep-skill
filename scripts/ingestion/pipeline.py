"""Bridge legacy material extraction into the versioned, reviewable ingestion core.

The existing builder still emits ``raw_input.json`` for compatibility.  This
module adds a loss-resistant ``ingestion`` envelope to that file, then persists
the envelope under ``.ingest/`` when ``ingest.py`` compiles the student-facing
workspace.  Everything here is deterministic and stdlib-only.
"""

import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path

from .identifiers import (
    canonical_json,
    file_sha256,
    normalize_workspace_path,
    safe_workspace_entry,
)
from .models import (
    ASSET_ROLES,
    ChapterPhaseMapping,
    ContentUnit,
    EvidenceRef,
    ReviewIssue,
    ReviewPatch,
    SourceRecord,
    render_answer_value,
)
from .quality import REASON_ORDER, assess_page
from .dedup import (
    CANONICAL_GROUPS_PATH,
    DUPLICATE_CANDIDATES_PATH,
    SOURCE_CONFLICTS_PATH,
    SOURCE_PRIORITIES_PATH,
    build_dedup_facts,
    build_source_conflict_review_artifacts,
    compile_ingestion_facts,
    load_canonical_groups,
    load_source_priorities,
)
from .retrieval_folding import fold_units_for_retrieval
from .storage import (
    IngestionStore,
    _workspace_root,
    atomic_write_json,
    atomic_write_text,
    read_json,
)


PAYLOAD_VERSION = 2
LEGACY_PAYLOAD_VERSION = 1
PARSER_RECEIPTS_PATH = ".ingest/parser_receipts.json"
BUILD_MANIFEST_PATH = ".ingest/build_manifest.json"
UNBOUND_REVIEW_PATH = ".ingest/unbound_review.json"

_FORMULA_RE = re.compile(
    r"(?:"
    r"\$[^$\n]+\$|"
    r"\\(?:frac|dfrac|tfrac|sum|prod|int|sqrt|begin|left|right)\b|"
    r"\bP\s*[\[(][^\])\n]{1,80}[\])]|"
    r"[∪∩∈∉⊂⊆⊃⊇∅∀∃±×÷≠≤≥≈]|"
    r"[A-Za-z0-9)\]}]\s*(?:=|<|>)\s*(?:[A-Za-z0-9({\[+\-]|\\)|"
    r"(?<!\w)\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?(?!\w)"
    r")"
)
_REASON_SAFE_RE = re.compile(r"[^a-z0-9_.-]+")
_ACTIVE_REVIEW_STATUSES = frozenset(("pending", "claimed", "validated", "blocked"))


def _structured_multi_column_hint(elements):
    """Recognize only two clearly separated, vertically overlapping text columns."""

    boxes = []
    for element in elements:
        if not isinstance(element, dict) or element.get("kind") not in (
                "title", "heading", "text", "list", "caption", "code"):
            continue
        bbox = element.get("bbox")
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       for value in bbox)):
            continue
        x0, y0, x1, y1 = (float(value) for value in bbox)
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))
    if len(boxes) < 4:
        return False
    boxes.sort(key=lambda box: (box[0], box[2], box[1], box[3]))
    total_width = max(box[2] for box in boxes) - min(box[0] for box in boxes)
    if total_width <= 0:
        return False
    for split in range(2, len(boxes) - 1):
        left, right = boxes[:split], boxes[split:]
        gap = min(box[0] for box in right) - max(box[2] for box in left)
        if gap < total_width * 0.03:
            continue
        left_y = (min(box[1] for box in left), max(box[3] for box in left))
        right_y = (min(box[1] for box in right), max(box[3] for box in right))
        overlap = min(left_y[1], right_y[1]) - max(left_y[0], right_y[0])
        if overlap >= 0.25 * min(left_y[1] - left_y[0], right_y[1] - right_y[0]):
            return True
    return False


def _valid_supplied_quality(value):
    if (not isinstance(value, dict)
            or set(value) != {"score", "route", "reason_codes"}
            or value.get("route") not in ("fast", "recover", "review")
            or not isinstance(value.get("reason_codes"), list)
            or not all(isinstance(reason, str) and reason for reason in value["reason_codes"])
            or isinstance(value.get("score"), bool)
            or not isinstance(value.get("score"), (int, float))):
        return False
    score = float(value["score"])
    return score == score and 0.0 <= score <= 1.0


def _merge_page_quality(supplied, local):
    """Keep adapter facts while never allowing them to erase local risk evidence."""

    if not _valid_supplied_quality(supplied):
        return local
    route_rank = {"fast": 0, "recover": 1, "review": 2}
    route = max((supplied["route"], local["route"]), key=route_rank.__getitem__)
    reasons = set(supplied["reason_codes"]) | set(local["reason_codes"])
    ordered = [reason for reason in REASON_ORDER if reason in reasons]
    ordered.extend(sorted(reasons - set(ordered)))
    return {
        "score": round(min(float(supplied["score"]), float(local["score"])), 4),
        "route": route,
        "reason_codes": ordered,
    }


def _relative(path, root):
    # Resolve both sides before comparing them.  Windows can expose the same
    # directory as both an 8.3 path (RUNNER~1) and a long path (runneradmin);
    # relpath treats those lexical spellings as unrelated and fabricates a
    # traversal path even though the source is inside the materials root.
    source = Path(path).resolve(strict=False)
    base = Path(root).resolve(strict=False)
    value = os.path.relpath(str(source), str(base)).replace(os.sep, "/")
    return normalize_workspace_path(value)


def _media_type(path):
    extension = os.path.splitext(path)[1].lower()
    overrides = {
        ".md": "text/markdown",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return overrides.get(extension) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _reason_code(value):
    text = str(value or "review_required").strip().lower().replace(" ", "_")
    text = _REASON_SAFE_RE.sub("_", text).strip("_.-")
    if not text or not text[0].isalpha():
        text = "review_" + (text or "required")
    return text


def _severity(kind):
    kind = _reason_code(kind)
    if any(token in kind for token in (
        "unsupported", "failed", "scanned", "no_text", "undecodable", "missing_answer",
        "encrypted", "unsafe", "leakage", "wiki_empty", "not_materialized",
        "answer_candidate", "alternate_content",
    )):
        return "blocking"
    if any(token in kind for token in ("uncertain", "ambiguous", "defaulted", "unassigned")):
        return "warning"
    return "warning"


def normalize_review_candidates(report, quiz_items=()):
    """Convert legacy warnings/skips/AI hand-off rows into one typed candidate list."""

    report = report if isinstance(report, dict) else {}
    candidates = []
    seen = {}

    def add(
        kind, source_file, description, action, pages=(), severity=None,
        targets=(), external_ids=(),
    ):
        if source_file in ("", "(all)", "all", None):
            source_file = None
        row = {
            "reason_codes": [_reason_code(kind)],
            "source_file": source_file if isinstance(source_file, str) else None,
            "pages": sorted(set(p for p in pages if type(p) is int and p >= 1)),
            "severity": severity or _severity(kind),
            "description": str(description or kind).strip(),
            "suggested_action": str(action or description or kind).strip(),
            "target_unit_ids": sorted(set(targets)),
            "external_ids": sorted(set(
                str(value) for value in external_ids if value not in (None, "")
            )),
        }
        identity = (
            tuple(row["reason_codes"]), row["source_file"], tuple(row["pages"]),
            tuple(row["target_unit_ids"]), tuple(row["external_ids"]),
        )
        existing = seen.get(identity)
        if existing is not None:
            for key in ("description", "suggested_action"):
                if row[key] not in existing[key].split(" | "):
                    existing[key] += " | " + row[key]
            return
        seen[identity] = row
        candidates.append(row)

    for entry in report.get("ai_review", ()):
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind") or "ai_review"
        add(
            kind,
            entry.get("file"),
            "%s: %s" % (kind, entry.get("file") or "unbound material"),
            entry.get("action") or "Inspect the source and add evidence-backed content units.",
            entry.get("pages") or (),
            external_ids=entry.get("external_ids") or (),
        )

    ai_kinds = {
        _reason_code(entry.get("kind") or "ai_review")
        for entry in report.get("ai_review", ()) if isinstance(entry, dict)
    }
    ai_source_pairs = {
        (
            _reason_code(entry.get("kind") or "ai_review"),
            str(entry.get("file")).replace("\\", "/"),
        )
        for entry in report.get("ai_review", ())
        if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    }
    for entry in report.get("skipped", ()):
        if not isinstance(entry, dict):
            continue
        why = entry.get("why") or "source was skipped"
        add(
            "skipped_source",
            entry.get("file"),
            "Source content was skipped: %s" % why,
            "Recover the source with a supported parser or multimodal review, then rebuild.",
            entry.get("pages") or (),
            severity="blocking",
        )

    # A warning without a matching AI row still remains visible.  It is deliberately
    # warning-level unless its machine prefix is known to mean lost content.
    for warning in report.get("warnings", ()):
        if not isinstance(warning, str) or not warning.strip():
            continue
        prefix = warning.split(":", 1)[0].strip()
        # type_defaulted is the actionable, item-level hand-off emitted alongside
        # the broader type_heuristic notice.  Keeping both creates two reviews for
        # the same required inspection without adding evidence.
        if _reason_code(prefix) == "type_heuristic" and "type_defaulted" in ai_kinds:
            continue
        warning_source = None
        if ":" in warning:
            tail = warning.split(":", 1)[1].split("（", 1)[0].strip()
            source_match = re.match(
                r"(.+?\.(?:pdf|docx|pptx|xlsx|txt|md|png|jpe?g|tiff?|bmp|gif|webp))"
                r"(?=\s|\Z|[\uFF08(])",
                tail,
                re.IGNORECASE,
            )
            if source_match:
                warning_source = source_match.group(1).strip()
        if (_reason_code(prefix), warning_source) in ai_source_pairs:
            continue
        add(
            prefix or "parser_warning",
            warning_source,
            warning.strip(),
            "Inspect the parse report and either resolve or explicitly mark this warning unrecoverable.",
        )

    for item in quiz_items or ():
        if (not isinstance(item, dict)
                or item.get("gradable") is False
                or item.get("answer") not in (None, "", [])):
            continue
        source_file = item.get("source_file")
        add(
            "missing_answer",
            source_file,
            "Question %s has no source-backed answer." % (item.get("id") or "(unknown)"),
            "Recover an official answer from the supplied materials or explicitly add an AI-generated answer with provenance.",
            item.get("source_pages") or (),
            severity="blocking",
            external_ids=[item.get("id")],
        )

    return candidates


def _asset_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\\", "/").strip()
    if "/" not in normalized:
        normalized = "references/assets/" + normalized
    return normalize_workspace_path(normalized)


def _phase_map(sections):
    result = {}
    for phase_num, section in enumerate(sections or (), 1):
        chapter = section.get("chapter") or phase_num
        chapter_id = "ch%02d" % int(chapter)
        phase_id = "phase%02d" % phase_num
        for key in section.get("page_keys") or ():
            if isinstance(key, (list, tuple)) and len(key) == 2:
                result[(str(key[0]).replace("\\", "/"), int(key[1]))] = (
                    str(chapter), "Phase %d" % phase_num, chapter_id, phase_id,
                )
    return result


def _record_by_file(records, source_file):
    if not isinstance(source_file, str):
        return None
    normalized = source_file.replace("\\", "/")
    direct = records.get(normalized)
    if direct is not None:
        return direct
    folded = normalized.casefold()
    matches = [record for path, record in records.items() if path.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    basename = os.path.basename(normalized).casefold()
    matches = [record for path, record in records.items() if os.path.basename(path).casefold() == basename]
    return matches[0] if len(matches) == 1 else None


def _validate_auxiliary_source_bindings(units, sources):
    """Bind parser-declared sidecars to exact first-class source revisions."""

    source_by_path = {source.path: source for source in sources}
    source_by_id = {source.source_id: source for source in sources}
    for unit in units:
        parser_metadata = unit.metadata.get("parser_metadata")
        if not isinstance(parser_metadata, dict):
            continue
        if "sidecar" in parser_metadata and (
                unit.kind != "page_anchor"
                or parser_metadata.get("format") != "standalone_raster"):
            raise ValueError(
                "raster sidecar provenance may appear only on its image page anchor"
            )
        if unit.kind != "page_anchor" or parser_metadata.get("format") != "standalone_raster":
            continue
        raster_source = source_by_id.get(unit.source_id)
        if raster_source is None or not raster_source.media_type.startswith("image/"):
            raise ValueError("standalone raster metadata must belong to an image source")
        sidecar = parser_metadata.get("sidecar")
        if sidecar is None:
            continue
        expected_fields = {"source_file", "sha256", "byte_size", "discovery"}
        if not isinstance(sidecar, dict) or set(sidecar) != expected_fields:
            raise ValueError(
                "standalone raster sidecar metadata has an invalid provenance schema"
            )
        source_file = sidecar.get("source_file")
        source = source_by_path.get(source_file)
        if source is None:
            raise ValueError(
                "standalone raster sidecar references an unknown first-class source: %r"
                % source_file
            )
        if source.media_type not in ("text/plain", "text/markdown"):
            raise ValueError("standalone raster sidecar must reference a text source")
        if (sidecar.get("sha256") != source.sha256
                or sidecar.get("byte_size") != source.size_bytes):
            raise ValueError(
                "standalone raster sidecar does not match its source revision: %s"
                % source.path
            )
        if sidecar.get("discovery") not in ("explicit", "automatic"):
            raise ValueError("standalone raster sidecar discovery mode is invalid")
    return True


def _deduplicate_bound_candidates(candidates):
    """Fold candidates only after their real source/target identity is known."""

    output = []
    seen = {}
    for candidate in candidates:
        row = dict(candidate)
        row["reason_codes"] = sorted(set(row.get("reason_codes") or ["review_required"]))
        row["pages"] = sorted(set(row.get("pages") or ()))
        row["target_unit_ids"] = sorted(set(row.get("target_unit_ids") or ()))
        identity = (
            tuple(row["reason_codes"]),
            row.get("source_file"),
            tuple(row["pages"]),
            tuple(row["target_unit_ids"]),
        )
        existing = seen.get(identity)
        if existing is None:
            seen[identity] = row
            output.append(row)
            continue
        if row.get("severity") == "blocking":
            existing["severity"] = "blocking"
        for field in ("description", "suggested_action"):
            incoming = str(row.get(field) or "").strip()
            current = str(existing.get(field) or "").strip()
            if incoming and incoming not in current.split(" | "):
                existing[field] = current + (" | " if current else "") + incoming
    return output


def _contains_unsafe_control_text(value):
    """Return whether a strict-JSON value contains text unsafe for derivatives."""

    if isinstance(value, str):
        return any(
            char == "\ufffd"
            or ((ord(char) < 32 and char not in "\t\n\r") or ord(char) == 0x7F)
            for char in value
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_unsafe_control_text(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_unsafe_control_text(item) for item in value.values())
    return False


def _unit_contains_unsafe_control_text(unit):
    return any(
        _contains_unsafe_control_text(value)
        for value in (
            unit.text, unit.html, unit.latex, unit.metadata, unit.section_path,
        )
    )


def _question_text(item):
    text = str(item.get("question") or "").strip()
    options = item.get("options")
    if isinstance(options, list) and options:
        rendered = []
        for index, option in enumerate(options):
            if isinstance(option, dict):
                rendered.append("%s. %s" % (option.get("label") or index + 1, option.get("text") or ""))
            else:
                rendered.append("%s. %s" % (chr(65 + index), option))
        text = text + "\n" + "\n".join(rendered)
    return text.strip()


def _quiz_ordinal(item, index):
    external_id = item.get("id")
    if external_id is None or not str(external_id).strip():
        return 100000 + index * 10
    digest = hashlib.sha256(str(external_id).encode("utf-8")).hexdigest()
    return 100000 + int(digest[:14], 16) * 2


def _quiz_metadata(item, answer_record=None, answer_value_marker=False):
    """Keep the typed, student-relevant part of a quiz item in the IR.

    The legacy bank remains a derived view.  Keeping these fields on the
    question/answer units lets a reviewed patch rebuild a new item without
    flattening booleans, choice options, or all but the first visual asset.
    """

    metadata = {}
    direct = (
        ("type", "quiz_type"),
        ("options", "options"),
        ("keywords", "keywords"),
        ("knowledge_point", "knowledge_point"),
        ("knowledge_points", "knowledge_points"),
        ("source_type", "source_type"),
        ("source", "source"),
        ("requires_assets", "requires_assets"),
        ("maybe_requires_assets", "maybe_requires_assets"),
        ("gradable", "gradable"),
        ("question_text_status", "question_text_status"),
        ("diagram_type", "diagram_type"),
        ("language", "language"),
        ("expected_behavior", "expected_behavior"),
        ("tests", "tests"),
    )
    for source_key, target_key in direct:
        value = item.get(source_key)
        if value is not None:
            metadata[target_key] = value

    language_key = "answer_source_language" if answer_value_marker else "source_language"
    source_language = item.get(language_key)
    if source_language in ("zh", "en"):
        metadata["source_language"] = source_language

    source_pages = [
        page for page in item.get("source_pages") or ()
        if type(page) is int and page >= 1
    ]
    # ``source_pages`` belongs to the question source.  An answer unit can point
    # at a separate solutions file, so copying question-side page metadata onto
    # that unit violates the store's same-source page-anchor invariant.  The
    # answer side has its own ``answer_source_pages`` field below.
    if source_pages and not answer_value_marker:
        metadata["source_pages"] = source_pages
    answer_pages = [
        page for page in item.get("answer_source_pages") or ()
        if type(page) is int and page >= 1
    ]
    if answer_pages:
        metadata["answer_source_pages"] = answer_pages
    if answer_record is not None:
        metadata["answer_source_file"] = answer_record.path

    assets = []
    for asset in item.get("assets") or ():
        if not isinstance(asset, dict):
            continue
        path = _asset_path(asset.get("path"))
        role = asset.get("role")
        if path and role in ASSET_ROLES:
            normalized = {"path": path, "role": role}
            for hash_field in ("sha256", "source_sha256"):
                value = asset.get(hash_field)
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                    normalized[hash_field] = value
            if normalized not in assets:
                assets.append(normalized)
    if assets:
        metadata["assets"] = assets
    if answer_value_marker:
        metadata["answer_value"] = item.get("answer")
    return metadata


def _default_parser_receipt(record, produced_pages):
    empty_config_hash = hashlib.sha256(b"{}").hexdigest()
    produced_pages = list(produced_pages)
    discovered_page_count = max(produced_pages, default=0)
    full_inventory = produced_pages == list(range(1, discovered_page_count + 1))
    if record.status in ("failed", "unsupported"):
        receipt_status = record.status
    elif produced_pages:
        receipt_status = "success"
    else:
        receipt_status = "review_required"
    return {
        "schema_version": 1,
        "adapter": "core",
        "adapter_version": None,
        "module": "exam-cram-core",
        "distribution": None,
        "source_file": record.path,
        "source_sha256": record.sha256,
        "media_type": record.media_type,
        # A hand-built ingestion envelope may intentionally mount a sparse page
        # slice (for example only answer page 3).  It must identify that slice as
        # requested; only a contiguous 1..N inventory may claim a full parse.
        "requested_pages": [] if full_inventory else produced_pages,
        "produced_pages": produced_pages,
        "discovered_page_count": discovered_page_count,
        "config_sha256": empty_config_hash,
        "policy": {"network": False, "upload": False, "install": False},
        "status": receipt_status,
    }


def _normalize_parser_receipts(records, page_quality, parser_receipts):
    """Bind one exact local-parser receipt to every discovered source revision."""

    pages_by_source = {}
    for row in page_quality:
        pages_by_source.setdefault(row["source_file"], []).append(row["page"])
    provided = {}
    for raw in parser_receipts or ():
        if not isinstance(raw, dict):
            raise ValueError("parser receipt must be an object")
        source_file = raw.get("source_file")
        if not isinstance(source_file, str):
            raise ValueError("parser receipt source_file must be text")
        source_file = source_file.replace("\\", "/")
        if source_file in provided:
            raise ValueError("duplicate parser receipt for %s" % source_file)
        provided[source_file] = dict(raw)
    unknown = sorted(set(provided) - set(records))
    if unknown:
        raise ValueError("parser receipts reference unknown sources: %r" % unknown)
    return [
        provided.get(path) or _default_parser_receipt(
            records[path], sorted(set(pages_by_source.get(path, ())))
        )
        for path in sorted(records)
    ]


def build_payload(
    materials_root,
    source_paths,
    pages,
    sections=(),
    quiz_items=(),
    report=None,
    parser_receipts=(),
):
    """Build the strict ingestion envelope embedded in ``raw_input.json``."""

    # ``abspath`` preserves Windows 8.3 aliases (for example RUNNER~1), while
    # authoritative store paths are resolved to their long form.  Persist one
    # canonical spelling so manifests and later containment checks agree.
    root = str(_workspace_root(materials_root))
    if not os.path.isdir(root):
        raise ValueError("materials_root must be an existing directory")

    report = report if isinstance(report, dict) else {}
    candidates = normalize_review_candidates(report, quiz_items)
    candidate_files = {
        row["source_file"].replace("\\", "/")
        for row in candidates if isinstance(row.get("source_file"), str)
    }
    skipped_files = {
        str(row.get("file")).replace("\\", "/")
        for row in report.get("skipped", ()) if isinstance(row, dict) and row.get("file")
    }

    records = {}
    absolute_by_rel = {}
    for source_path in sorted(set(os.path.abspath(path) for path in source_paths)):
        relative = _relative(source_path, root)
        status = "parsed"
        if relative in skipped_files:
            status = "failed"
        elif relative in candidate_files:
            status = "review_required"
        if any(
            isinstance(row, dict) and row.get("file") == relative
            and "unsupported" in str(row.get("why", "")).lower()
            for row in report.get("skipped", ())
        ):
            status = "unsupported"
        record = SourceRecord.from_file(root, relative, _media_type(source_path), status=status)
        records[relative] = record
        absolute_by_rel[relative] = source_path

    mapping_by_page = _phase_map(sections)
    units = []
    mappings = []
    page_quality = []
    current_heading = {}
    current_sections = {}
    quality_candidates = []
    page_quality_candidates = []

    ordered_pages = sorted(
        (page for page in pages if isinstance(page, dict)),
        key=lambda page: (str(page.get("file", "")).replace("\\", "/"), int(page.get("page") or 0)),
    )
    for page in ordered_pages:
        source_file = str(page.get("file") or "").replace("\\", "/")
        record = _record_by_file(records, source_file)
        page_number = page.get("page")
        if record is None or type(page_number) is not int or page_number < 1:
            continue
        elements = page.get("elements") if isinstance(page.get("elements"), list) else []
        embedded = page.get("embedded_assets") if isinstance(page.get("embedded_assets"), list) else []
        image_assets = [asset for asset in embedded if _asset_path(asset)]
        image_assets.extend(
            element.get("asset") for element in elements
            if isinstance(element, dict) and element.get("kind") == "figure" and element.get("asset")
        )
        text = page.get("text") if isinstance(page.get("text"), str) else ""
        page_language = page.get("source_language")
        if page_language not in ("zh", "en"):
            page_language = None
        table_hint = any(isinstance(e, dict) and e.get("kind") == "table" for e in elements)
        formula_hint = any(isinstance(e, dict) and e.get("kind") == "formula" for e in elements)
        formula_hint = formula_hint or bool(_FORMULA_RE.search(text))
        page_metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        multi_column_hint = (
            page_metadata.get("multi_column_hint") is True
            or _structured_multi_column_hint(elements)
        )
        local_quality = assess_page({
            "page": page_number,
            "text": text,
            "image_count": len(set(asset for asset in image_assets if asset)),
            "image_area_ratio": (
                0.5 if image_assets and len(text.strip()) < 120
                else (0.2 if image_assets else 0.0)
            ),
            "vector_count": 0,
            "multi_column_hint": multi_column_hint,
            "table_hint": table_hint or "\t" in text,
            "formula_hint": formula_hint,
        })
        supplied_quality = page.get("quality_signals")
        quality = _merge_page_quality(supplied_quality, local_quality)
        page_quality.append({
            "source_file": record.path,
            "page": page_number,
            "score": quality["score"],
            "route": quality["route"],
            "reason_codes": quality["reason_codes"],
        })
        if quality["route"] != "fast":
            reasons = list(quality["reason_codes"] or ["quality_recovery"])
            reason_groups = []
            if "formula_hint" in reasons:
                # Formula recovery has a distinct semantic postcondition.  Do
                # not let a control-byte repair on the same page close it.
                reason_groups.append(["formula_hint"])
                reasons = [reason for reason in reasons if reason != "formula_hint"]
            if reasons:
                reason_groups.append(reasons)
            for reason_group in reason_groups:
                formula_recovery = reason_group == ["formula_hint"]
                quality_candidate = {
                    "reason_codes": reason_group,
                    "source_file": record.path,
                    "pages": [page_number],
                    "severity": "blocking" if quality["route"] == "review" else "warning",
                    "description": "Page %d was routed to %s extraction (quality %.4f)." % (
                        page_number, quality["route"], quality["score"]
                    ),
                    "suggested_action": (
                        "Inspect the cited page and add or validate a material-provenance "
                        "formula unit with non-empty LaTeX at this exact source location."
                        if formula_recovery else
                        "Inspect the rendered source page and add or validate evidence-backed "
                        "content units."
                    ),
                    "target_unit_ids": [],
                }
                quality_candidates.append(quality_candidate)
                page_quality_candidates.append(quality_candidate)

        phase = mapping_by_page.get((record.path, page_number))
        chapter, phase_label, chapter_id, phase_id = phase or (None, None, None, None)
        anchor_metadata = {}
        if isinstance(page.get("metadata"), dict):
            anchor_metadata["parser_metadata"] = page["metadata"]
        if page_language:
            anchor_metadata["source_language"] = page_language
        anchor = ContentUnit.create(
            record.source_id, record.sha256, record.path, "page_anchor", "", page_number,
            ordinal=0, chapter_id=chapter_id, phase_id=phase_id,
            metadata=anchor_metadata,
            method="native", confidence=quality["score"], provenance="material",
        )
        units.append(anchor)
        if phase:
            mappings.append(ChapterPhaseMapping.create(
                anchor.unit_id, record.source_id, record.sha256,
                chapter, phase_label, chapter_id, phase_id,
            ))

        source_key = record.path
        section_path = list(current_sections.get(source_key, ()))
        parent_id = current_heading.get(source_key)
        if elements:
            iterable = elements
        elif text.strip():
            iterable = [{
                "kind": "text", "text": text, "ordinal": 0, "bbox": None,
                "asset": None, "source_language": page_language,
            }]
        else:
            iterable = []

        declared_assets = set()
        for local_ordinal, element in enumerate(iterable, 1):
            if not isinstance(element, dict):
                continue
            kind = element.get("kind") if element.get("kind") in (
                "title", "heading", "text", "list", "table", "formula", "figure", "diagram",
                "caption", "code", "speaker_notes", "question", "answer", "other",
            ) else "other"
            element_text = element.get("text") if isinstance(element.get("text"), str) else ""
            asset_path = _asset_path(element.get("asset") or element.get("asset_path"))
            if asset_path:
                declared_assets.add(asset_path)
            role = element.get("asset_role")
            if asset_path and role is None:
                role = "figure"
            element_metadata = {}
            asset_sha256 = element.get("asset_sha256")
            if (asset_path and isinstance(asset_sha256, str)
                    and re.fullmatch(r"[0-9a-f]{64}", asset_sha256)):
                element_metadata["asset_sha256"] = asset_sha256
            if isinstance(element.get("metadata"), dict):
                element_metadata["parser_metadata"] = element["metadata"]
            source_language = element.get("source_language")
            if source_language in ("zh", "en"):
                element_metadata["source_language"] = source_language
            unit_section = tuple(section_path)
            unit_parent = parent_id
            unit = ContentUnit.create(
                record.source_id, record.sha256, record.path, kind, element_text, page_number,
                ordinal=local_ordinal,
                bbox=element.get("bbox"), html=element.get("html"), latex=element.get("latex"),
                parent_unit_id=unit_parent, section_path=unit_section,
                chapter_id=chapter_id, phase_id=phase_id,
                asset_path=asset_path, asset_role=role,
                metadata=element_metadata,
                method=(element.get("method") if element.get("method") in (
                    "native", "heuristic", "ocr", "vision", "manual", "ai_recovered"
                ) else "native"),
                confidence=(
                    float(element["confidence"])
                    if isinstance(element.get("confidence"), (int, float))
                    and not isinstance(element.get("confidence"), bool)
                    and 0 <= float(element["confidence"]) <= 1
                    else quality["score"]
                ),
                provenance="material",
            )
            if ((element_text.strip() or str(element.get("latex") or "").strip())
                    and "source_language" not in unit.metadata):
                quality_candidates.append({
                    "reason_codes": ["source_language_unknown"],
                    "source_file": record.path,
                    "pages": [page_number],
                    "severity": "blocking",
                    "description": (
                        "Semantic source content is mixed, formula-only, or otherwise lacks "
                        "evidence for a zh/en language classification."
                    ),
                    "suggested_action": (
                        "Inspect the cited source location and replace the unit with "
                        "metadata.source_language set to zh or en only when the evidence "
                        "supports that classification."
                    ),
                    "target_unit_ids": [unit.unit_id],
                })
            if kind in ("title", "heading") and element_text.strip():
                level = element.get("level") if type(element.get("level")) is int else 1
                level = max(1, min(6, level))
                section_path = section_path[:level - 1] + [element_text.strip()]
                current_sections[source_key] = tuple(section_path)
                current_heading[source_key] = unit.unit_id
                parent_id = unit.unit_id
            units.append(unit)
            if kind in ("speaker_notes", "answer"):
                answer_candidate = kind == "answer"
                quality_candidates.append({
                    "reason_codes": [
                        "speaker_note_answer_candidate" if answer_candidate
                        else "speaker_notes_review"
                    ],
                    "source_file": record.path,
                    "pages": [page_number],
                    "severity": "blocking" if answer_candidate else "warning",
                    "description": (
                        "Presenter notes contain an answer-like marker and were isolated from "
                        "student-visible prose."
                        if answer_candidate else
                        "Presenter notes were isolated from student-visible prose and need "
                        "classification before use."
                    ),
                    "suggested_action": (
                        "Review the slide and notes, then pair the answer with a bank question "
                        "or mark it unrecoverable."
                        if answer_candidate else
                        "Review the note and explicitly replace/classify it as teaching content, "
                        "answer evidence, or unrecoverable."
                    ),
                    "target_unit_ids": [unit.unit_id],
                })
            if phase:
                mappings.append(ChapterPhaseMapping.create(
                    unit.unit_id, record.source_id, record.sha256,
                    chapter, phase_label, chapter_id, phase_id,
                ))

        extra_ordinal = len(iterable) + 1
        for asset in sorted(set(_asset_path(value) for value in embedded if _asset_path(value))):
            if asset in declared_assets:
                continue
            unit = ContentUnit.create(
                record.source_id, record.sha256, record.path, "figure", "", page_number,
                ordinal=extra_ordinal, parent_unit_id=current_heading.get(source_key),
                section_path=current_sections.get(source_key, ()), chapter_id=chapter_id,
                phase_id=phase_id, asset_path=asset, asset_role="figure",
                method="native", confidence=quality["score"], provenance="material",
            )
            extra_ordinal += 1
            units.append(unit)
            if phase:
                mappings.append(ChapterPhaseMapping.create(
                    unit.unit_id, record.source_id, record.sha256,
                    chapter, phase_label, chapter_id, phase_id,
                ))

    # Questions and official answers become first-class units, so evaluation can
    # measure QA pairing and answer-side leakage without reverse-parsing quiz JSON.
    for index, item in enumerate(quiz_items or (), 1):
        if not isinstance(item, dict):
            continue
        question_record = _record_by_file(records, item.get("source_file"))
        pages_for_question = item.get("source_pages") or []
        if question_record is None or not pages_for_question:
            continue
        page_number = next((p for p in pages_for_question if type(p) is int and p >= 1), None)
        if page_number is None:
            continue
        chapter = item.get("chapter")
        try:
            chapter_id = "ch%02d" % int(chapter) if chapter is not None else None
        except (TypeError, ValueError):
            chapter_id = None
        q_assets = [a for a in item.get("assets") or () if isinstance(a, dict)
                    and a.get("role") == "question_context" and _asset_path(a.get("path"))]
        q_asset = _asset_path(q_assets[0].get("path")) if q_assets else None
        question_ordinal = _quiz_ordinal(item, index)
        question = ContentUnit.create(
            question_record.source_id, question_record.sha256, question_record.path,
            "question", str(item.get("question") or "").strip(),
            page_number, ordinal=question_ordinal,
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            chapter_id=chapter_id, asset_path=q_asset,
            asset_role="question_context" if q_asset else None,
            metadata=_quiz_metadata(item),
            method="heuristic", confidence=0.75, provenance="material",
        )
        if "source_language" not in question.metadata:
            quality_candidates.append({
                "reason_codes": ["source_language_unknown"],
                "source_file": question_record.path,
                "pages": [page_number],
                "severity": "blocking",
                "description": (
                    "Question source language is not explicitly classified as zh or en."
                ),
                "suggested_action": (
                    "Inspect the cited question evidence and replace the unit with "
                    "metadata.source_language set to zh or en."
                ),
                "target_unit_ids": [question.unit_id],
            })

        answer_value = item.get("answer")
        answer = None
        if answer_value not in (None, "", [], {}):
            answer_record = _record_by_file(
                records, item.get("answer_source_file") or item.get("source_file")
            ) or question_record
            answer_pages = item.get("answer_source_pages") or pages_for_question
            answer_page = next((p for p in answer_pages if type(p) is int and p >= 1), page_number)
            a_assets = [a for a in item.get("assets") or () if isinstance(a, dict)
                        and a.get("role") in ("answer_context", "worked_solution")
                        and _asset_path(a.get("path"))]
            a_asset = _asset_path(a_assets[0].get("path")) if a_assets else None
            answer = ContentUnit.create(
                answer_record.source_id, answer_record.sha256, answer_record.path,
                "answer", render_answer_value(answer_value), answer_page,
                ordinal=question_ordinal + 1,
                external_id=str(item.get("id")) if item.get("id") is not None else None,
                chapter_id=chapter_id, asset_path=a_asset,
                asset_role=(a_assets[0].get("role") if a_asset else None),
                metadata=_quiz_metadata(
                    item, answer_record=answer_record, answer_value_marker=True
                ),
                method="heuristic", confidence=0.75,
                provenance=("ai_supplemented" if item.get("source") == "ai_generated" else "material"),
            )
            if "source_language" not in answer.metadata:
                quality_candidates.append({
                    "reason_codes": ["answer_source_language_unknown"],
                    "source_file": answer_record.path,
                    "pages": [answer_page],
                    "severity": "warning",
                    "description": (
                        "Answer source language is not explicitly classified as zh or en."
                    ),
                    "suggested_action": (
                        "Inspect the cited answer evidence and replace the unit with "
                        "metadata.source_language set to zh or en before claiming a "
                        "material answer in that language."
                    ),
                    "target_unit_ids": [answer.unit_id],
                })
            question = question.with_pair(answer.unit_id)
            answer = answer.with_pair(question.unit_id)
        units.append(question)
        if answer is not None:
            units.append(answer)

    _validate_auxiliary_source_bindings(units, records.values())
    all_candidates = candidates + quality_candidates
    page_quality_candidate_ids = {id(candidate) for candidate in page_quality_candidates}
    questions_by_external_id = {
        unit.external_id: unit for unit in units
        if unit.kind == "question" and unit.external_id
    }
    bound, unbound = [], []
    records_by_id = {record.source_id: record for record in records.values()}
    unit_source_by_id = {unit.unit_id: unit.source_id for unit in units}
    for raw_candidate in all_candidates:
        is_page_quality_candidate = id(raw_candidate) in page_quality_candidate_ids
        candidate = dict(raw_candidate)
        external_ids = candidate.pop("external_ids", [])
        resolved_targets = set(candidate.get("target_unit_ids") or ())
        resolved_targets.update(
            questions_by_external_id[external_id].unit_id
            for external_id in external_ids if external_id in questions_by_external_id
        )
        if any(reason in ("type_defaulted", "type_heuristic")
               for reason in candidate.get("reason_codes") or ()):
            resolved_targets.update(
                unit.unit_id for unit in units if unit.kind == "question"
            )
        record = _record_by_file(records, candidate.get("source_file"))
        if record is not None and is_page_quality_candidate:
            pages = {
                page for page in candidate.get("pages") or ()
                if type(page) is int and page >= 1
            }
            page_units = [
                unit for unit in units
                if unit.source_id == record.source_id and (not pages or unit.page in pages)
            ]
            reasons = set(candidate.get("reason_codes") or ())
            if reasons == {"formula_hint"}:
                # An unresolved formula page is intentionally unbound when no
                # formula unit exists, so review may add one but cannot mutate
                # arbitrary prose/question units merely to close the issue.
                page_units = [unit for unit in page_units if unit.kind == "formula"]
            unsafe_units = [
                unit for unit in page_units if _unit_contains_unsafe_control_text(unit)
            ]
            if unsafe_units and reasons & {
                    "nul_or_replacement_char", "nul_byte", "control_character",
                    "garbled_text"}:
                page_units = unsafe_units
            resolved_targets.update(unit.unit_id for unit in page_units)
        if (record is not None
                and set(candidate.get("reason_codes") or ()) & {
                    "nul_or_replacement_char", "nul_byte", "control_character", "garbled_text",
                }):
            pages = {
                page for page in candidate.get("pages") or ()
                if type(page) is int and page >= 1
            }
            resolved_targets.update(
                unit.unit_id for unit in units
                if unit.source_id == record.source_id
                and (not pages or unit.page in pages)
                and _unit_contains_unsafe_control_text(unit)
            )
        candidate["target_unit_ids"] = sorted(resolved_targets)
        if record is not None:
            row = dict(candidate)
            row["source_file"] = record.path
            bound.append(row)
            continue

        # Global/derived-artifact alerts (for example ``(all)``, wiki_empty, or
        # ``references/quiz_bank.json``) still need the same claim/apply/terminal
        # lifecycle as source-local alerts.  Split them deterministically across
        # the affected real sources instead of marooning them in an uncloseable
        # side file.  If targets exist, they narrow the affected sources; otherwise
        # the warning describes the source set and is attached once per source.
        target_ids = [
            unit_id for unit_id in candidate.get("target_unit_ids") or ()
            if unit_id in unit_source_by_id
        ]
        affected_ids = sorted({unit_source_by_id[unit_id] for unit_id in target_ids})
        affected = [records_by_id[source_id] for source_id in affected_ids]
        if not affected:
            affected = sorted(records.values(), key=lambda value: value.path)
        if not affected:
            unbound.append(dict(candidate))
            continue
        for affected_record in affected:
            row = dict(candidate)
            row["source_file"] = affected_record.path
            row["target_unit_ids"] = sorted(
                unit_id for unit_id in target_ids
                if unit_source_by_id[unit_id] == affected_record.source_id
            )
            bound.append(row)

    bound = _deduplicate_bound_candidates(bound)
    reviewed_paths = {row["source_file"] for row in bound}
    for path in sorted(reviewed_paths):
        record = records[path]
        if record.status not in ("failed", "unsupported", "unrecoverable"):
            records[path] = SourceRecord.create(
                record.path,
                record.sha256,
                record.size_bytes,
                record.media_type,
                status="review_required",
            )

    normalized_receipts = _normalize_parser_receipts(
        records, page_quality, parser_receipts
    )
    return {
        "schema_version": PAYLOAD_VERSION,
        "source_root": root,
        "sources": [record.to_dict() for record in sorted(records.values(), key=lambda value: value.path)],
        "content_units": [unit.to_dict() for unit in sorted(units, key=lambda value: value.unit_id)],
        "mappings": [mapping.to_dict() for mapping in sorted(mappings, key=lambda value: value.unit_id)],
        "review_candidates": bound,
        "unbound_review_candidates": unbound,
        "page_quality": sorted(page_quality, key=lambda row: (row["source_file"], row["page"])),
        "parser_receipts": normalized_receipts,
    }


def _strict_payload(payload):
    legacy_expected = {
        "schema_version", "source_root", "sources", "content_units", "mappings",
        "review_candidates", "unbound_review_candidates", "page_quality",
    }
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    expected = (
        legacy_expected
        if version == LEGACY_PAYLOAD_VERSION
        else legacy_expected | {"parser_receipts"}
    )
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("ingestion payload has an invalid top-level schema")
    if version not in (LEGACY_PAYLOAD_VERSION, PAYLOAD_VERSION):
        raise ValueError("unsupported ingestion payload schema_version")
    if not os.path.isdir(payload.get("source_root") or ""):
        raise ValueError("ingestion source_root is missing or no longer exists")
    for key in expected - {"schema_version", "source_root"}:
        if not isinstance(payload.get(key), list):
            raise ValueError("ingestion payload %s must be a list" % key)
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("ingestion payload must contain strict JSON values: %s" % exc) from exc


def _validated_parser_receipts(payload, sources, page_quality):
    if payload["schema_version"] == LEGACY_PAYLOAD_VERSION:
        return None
    expected_fields = {
        "schema_version", "adapter", "adapter_version", "module", "distribution",
        "source_file", "source_sha256", "media_type", "requested_pages",
        "produced_pages", "discovered_page_count", "config_sha256", "policy", "status",
    }
    source_by_path = {source.path: source for source in sources}
    expected_pages = {}
    for row in page_quality:
        if not isinstance(row, dict):
            raise ValueError("page_quality rows must be objects")
        source_file = row.get("source_file")
        page = row.get("page")
        if source_file not in source_by_path or type(page) is not int or page < 1:
            raise ValueError("page_quality references an invalid source/page")
        expected_pages.setdefault(source_file, []).append(page)
    output = []
    seen = set()
    for index, receipt in enumerate(payload["parser_receipts"]):
        if not isinstance(receipt, dict) or set(receipt) != expected_fields:
            raise ValueError("parser receipt %d has an invalid schema" % index)
        source_file = receipt.get("source_file")
        source = source_by_path.get(source_file)
        if source is None or source_file in seen:
            raise ValueError("parser receipt source identity is unknown or duplicated")
        seen.add(source_file)
        if (receipt.get("schema_version") != 1
                or receipt.get("source_sha256") != source.sha256
                or receipt.get("media_type") != source.media_type):
            raise ValueError("parser receipt does not match source revision: %s" % source_file)
        if not isinstance(receipt.get("adapter"), str) or not receipt["adapter"].strip():
            raise ValueError("parser receipt adapter must be non-empty text")
        for field in ("adapter_version", "module", "distribution"):
            if receipt[field] is not None and not isinstance(receipt[field], str):
                raise ValueError("parser receipt %s must be null or text" % field)
        for field in ("requested_pages", "produced_pages"):
            pages = receipt[field]
            if (not isinstance(pages, list)
                    or any(type(page) is not int or page < 1 for page in pages)
                    or pages != sorted(set(pages))):
                raise ValueError("parser receipt %s must be sorted unique pages" % field)
        if receipt["produced_pages"] != sorted(set(expected_pages.get(source_file, ()))):
            raise ValueError("parser receipt produced_pages disagree with page inventory")
        discovered_page_count = receipt.get("discovered_page_count")
        if type(discovered_page_count) is not int or discovered_page_count < 0:
            raise ValueError("parser receipt discovered_page_count must be a non-negative integer")
        requested_pages = receipt["requested_pages"]
        produced_pages = receipt["produced_pages"]
        status = receipt.get("status")
        if status not in ("success", "review_required", "failed", "unsupported"):
            raise ValueError("parser receipt status is invalid")
        if status in ("failed", "unsupported"):
            if produced_pages:
                raise ValueError(
                    "failed/unsupported parser receipts must produce zero page anchors"
                )
        elif requested_pages:
            if (produced_pages != requested_pages
                    or requested_pages[-1] > discovered_page_count):
                raise ValueError(
                    "parser receipt requested page coverage disagrees with discovery inventory"
                )
        elif produced_pages != list(range(1, discovered_page_count + 1)):
            raise ValueError(
                "parser receipt full extraction must prove contiguous discovered pages"
            )
        if (not isinstance(receipt.get("config_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["config_sha256"])):
            raise ValueError("parser receipt config_sha256 is invalid")
        if receipt.get("policy") != {"network": False, "upload": False, "install": False}:
            raise ValueError("parser receipt must prove the local no-install/no-upload policy")
        output.append(dict(receipt))
    if seen != set(source_by_path):
        raise ValueError("parser receipts must account for every source")
    return sorted(output, key=lambda row: row["source_file"])


def _ledger_terminal_issue_outcomes(ledger_entries):
    """Map authoritative applied ledger entries to their queue outcomes."""

    outcomes = {}
    for position, entry in enumerate(ledger_entries or ()):
        if not isinstance(entry, dict) or not isinstance(entry.get("patch"), dict):
            raise ValueError(
                "review ledger entry %d cannot prove a terminal parser issue" % position
            )
        try:
            patch = ReviewPatch.from_dict(entry["patch"])
        except Exception as exc:
            raise ValueError(
                "review ledger entry %d has an invalid embedded patch" % position
            ) from exc
        if (entry.get("patch_id") != patch.patch_id
                or entry.get("issue_id") != patch.issue_id
                or entry.get("source_id") != patch.source_id
                or entry.get("source_sha256") != patch.source_sha256):
            raise ValueError(
                "review ledger entry %d disagrees with its embedded patch" % position
            )
        operation_names = tuple(
            operation["op"] for operation in patch.operations
        )
        if operation_names == ("mark_resolved",):
            outcome = "resolved"
        elif operation_names == ("mark_unrecoverable",):
            outcome = "unrecoverable"
        else:
            outcome = "applied"
        key = (patch.issue_id, patch.source_id, patch.source_sha256)
        if key in outcomes:
            raise ValueError(
                "review ledger has multiple terminal patches for parser issue %s"
                % patch.issue_id
            )
        outcomes[key] = outcome
    return outcomes


def _validate_parser_review_consistency(
    parser_receipts, sources, page_quality, issues, ledger_entries=(),
):
    """Cross-check parser outcomes against persisted source and review truth."""

    if parser_receipts is None:
        return True
    terminal_outcomes = _ledger_terminal_issue_outcomes(ledger_entries)
    source_by_path = {source.path: source for source in sources}
    source_by_id = {source.source_id: source for source in sources}
    receipt_by_path = {row["source_file"]: row for row in parser_receipts}
    if set(receipt_by_path) != set(source_by_path):
        raise ValueError("parser/source inventory is not one-to-one")

    issues_by_source = {source.source_id: [] for source in sources}
    for issue in issues:
        source = source_by_id.get(issue.source_id)
        if source is None or issue.source_sha256 != source.sha256:
            if issue.status in _ACTIVE_REVIEW_STATUSES:
                raise ValueError(
                    "active review issue does not match a current source revision: %s"
                    % issue.issue_id
                )
            continue
        issues_by_source[source.source_id].append(issue)

    for source in sources:
        receipt = receipt_by_path[source.path]
        status = receipt["status"]
        source_issues = issues_by_source[source.source_id]
        active = [
            issue for issue in source_issues
            if issue.status in _ACTIVE_REVIEW_STATUSES
        ]
        if source.status in ("discovered", "parsed"):
            raise ValueError(
                "persisted SourceRecord has an unfinished parser status: %s"
                % source.path
            )
        if source.status == "complete" and active:
            raise ValueError(
                "complete SourceRecord still has active typed review issues: %s"
                % source.path
            )
        if source.status == "review_required" and not active:
            raise ValueError(
                "review_required SourceRecord lacks an exact active typed issue: %s"
                % source.path
            )

        if status in ("failed", "unsupported"):
            if receipt["produced_pages"]:
                raise ValueError(
                    "failed/unsupported parser receipts must produce zero page anchors"
                )
            allowed_source_statuses = {status, "unrecoverable"}
            if source.status not in allowed_source_statuses:
                raise ValueError(
                    "parser receipt status=%s contradicts SourceRecord status=%s for %s"
                    % (status, source.status, source.path)
                )
            blocking_history = [
                issue for issue in source_issues
                if issue.severity == "blocking" and (
                    issue.status in _ACTIVE_REVIEW_STATUSES
                    or terminal_outcomes.get((
                        issue.issue_id, issue.source_id, issue.source_sha256,
                    )) == issue.status
                )
            ]
            if not blocking_history:
                unproven_terminal = any(
                    issue.severity == "blocking"
                    and issue.status not in _ACTIVE_REVIEW_STATUSES
                    for issue in source_issues
                )
                raise ValueError(
                    "%s parser receipt lacks an exact blocking typed issue%s: %s"
                    % (
                        status,
                        (
                            " with an authoritative review ledger patch"
                            if unproven_terminal else ""
                        ),
                        source.path,
                    )
                )
        elif status == "review_required":
            if source.status != "review_required":
                raise ValueError(
                    "review_required parser receipt contradicts SourceRecord status=%s for %s"
                    % (source.status, source.path)
                )
            receipt_locations = set(receipt["requested_pages"] or receipt["produced_pages"])
            if not receipt_locations and receipt["discovered_page_count"]:
                receipt_locations = set(range(1, receipt["discovered_page_count"] + 1))
            exact_active = [
                issue for issue in active
                if issue.severity == "blocking"
                and (not issue.pages or (
                    receipt_locations and set(issue.pages).issubset(receipt_locations)
                ))
            ]
            if not exact_active:
                raise ValueError(
                    "review_required parser receipt lacks an exact active blocking issue: %s"
                    % source.path
                )
        elif source.status in ("failed", "unsupported"):
            raise ValueError(
                "SourceRecord status=%s contradicts successful parser receipt for %s"
                % (source.status, source.path)
            )

    seen_pages = set()
    for row in page_quality:
        if not isinstance(row, dict):
            raise ValueError("page_quality rows must be objects")
        source = source_by_path.get(row.get("source_file"))
        page = row.get("page")
        route = row.get("route")
        reasons = row.get("reason_codes")
        if source is None or type(page) is not int or page < 1:
            raise ValueError("page_quality references an invalid source/location")
        key = (source.path, page)
        if key in seen_pages:
            raise ValueError("page_quality contains duplicate source/location: %r" % (key,))
        seen_pages.add(key)
        if route not in ("fast", "recover", "review"):
            raise ValueError("page_quality route is invalid for %s page %d" % key)
        if (not isinstance(reasons, list)
                or any(not isinstance(reason, str) or not reason for reason in reasons)):
            raise ValueError("page_quality reason_codes must be non-empty strings")
        if route != "review":
            continue
        expected_reasons = tuple(sorted(set(reasons or ["quality_recovery"])))
        exact = [
            issue for issue in issues_by_source[source.source_id]
            if issue.status in _ACTIVE_REVIEW_STATUSES
            and issue.severity == "blocking"
            and issue.pages == (page,)
            and set(expected_reasons).issubset(issue.reason_codes)
        ]
        if not exact:
            raise ValueError(
                "page_quality route=review lacks an exact active blocking issue for "
                "%s page %d reasons=%r" % (source.path, page, expected_reasons)
            )
        if source.status != "review_required":
            raise ValueError(
                "page_quality route=review contradicts SourceRecord status=%s for %s page %d"
                % (source.status, source.path, page)
            )
    return True


def _conflict_review_snapshot(workspace, units, sources):
    """Derive current conflicts plus stable typed-review evidence before writing.

    Existing source-priority facts are retained only for still-live source
    revisions.  Conflict identity excludes that mutable priority context, but
    using the same retained rows for preview and compilation keeps the evidence
    payload and persisted fact byte-for-byte aligned.
    """

    workspace_path = Path(workspace).resolve()
    current_revisions = {
        (source.source_id, source.sha256)
        for source in sources
    }
    priority_path = safe_workspace_entry(workspace_path, SOURCE_PRIORITIES_PATH)
    if priority_path.exists():
        priorities = tuple(
            row for row in load_source_priorities(workspace_path)
            if (row.source_id, row.source_sha256) in current_revisions
        )
    else:
        priorities = None
    facts = build_dedup_facts(
        units, sources, priorities=priorities
    )
    artifacts = build_source_conflict_review_artifacts(
        facts["conflicts"], units
    )
    issue_ids = {
        row["conflict_id"]: row["issue"].issue_id
        for row in artifacts
    }
    return facts["priorities"], artifacts, issue_ids


def _reconcile_conflict_review_snapshot(store, conflict_issues):
    """Replace only the conflict-detector slice without losing other issues."""

    existing_non_conflicts = [
        issue for issue in store.review_queue.issues()
        if "source_conflict" not in set(issue.reason_codes)
    ]
    store.review_queue.reconcile(existing_non_conflicts + list(conflict_issues))


def _persist_payload_unlocked(workspace, payload):
    """Persist and reconcile a validated envelope into ``workspace/.ingest``."""

    _strict_payload(payload)
    workspace = os.path.abspath(workspace)
    source_root = os.path.abspath(payload["source_root"])
    store = IngestionStore(workspace, source_root=source_root)
    # Always derive artifact paths from the roots validated by IngestionStore.
    # On Windows the caller's lexical path can be an 8.3 alias even though
    # ``safe_workspace_entry`` returns the same directory under its long name;
    # mixing those spellings makes Path.relative_to reject a valid child.
    workspace_root = store.workspace
    source_root = str(store.source_root)
    sources = [SourceRecord.from_dict(row) for row in payload["sources"]]
    parser_receipts = _validated_parser_receipts(
        payload, sources, payload["page_quality"]
    )

    source_ids = [source.source_id for source in sources]
    source_paths = [source.path for source in sources]
    if len(set(source_ids)) != len(source_ids) or len(set(source_paths)) != len(source_paths):
        raise ValueError("ingestion payload contains duplicate source identity")

    # Re-hash every source at the compilation boundary: raw_input is not trusted to
    # assert source bytes that have changed since the builder ran.
    for source in sources:
        current = SourceRecord.from_file(
            source_root, source.path, source.media_type, status=source.status
        )
        if current.sha256 != source.sha256 or current.size_bytes != source.size_bytes:
            raise ValueError("source changed after raw_input was built: %s" % source.path)

    units = [ContentUnit.from_dict(row) for row in payload["content_units"]]
    mappings = [ChapterPhaseMapping.from_dict(row) for row in payload["mappings"]]
    units_by_id = {}
    source_by_id = {source.source_id: source for source in sources}
    for unit in units:
        if unit.unit_id in units_by_id:
            raise ValueError("ingestion payload contains duplicate unit_id: %s" % unit.unit_id)
        source = source_by_id.get(unit.source_id)
        if (source is None or unit.source_file != source.path
                or unit.source_sha256 != source.sha256):
            raise ValueError("content unit does not match its source revision: %s" % unit.unit_id)
        units_by_id[unit.unit_id] = unit
    _validate_auxiliary_source_bindings(units, sources)
    mapping_ids = set()
    for mapping in mappings:
        if mapping.unit_id in mapping_ids:
            raise ValueError("ingestion payload contains duplicate chapter mapping")
        unit = units_by_id.get(mapping.unit_id)
        if (unit is None or mapping.source_id != unit.source_id
                or mapping.source_sha256 != unit.source_sha256):
            raise ValueError("chapter mapping does not match its content unit")
        mapping_ids.add(mapping.unit_id)

    source_by_path = {source.path: source for source in sources}
    issues = []
    evidence_specs = []
    issue_ids = set()
    for candidate in payload["review_candidates"]:
        if not isinstance(candidate, dict):
            raise ValueError("review candidate must be an object")
        source = source_by_path.get(candidate.get("source_file"))
        if source is None:
            raise ValueError("review candidate references an unknown source")
        stable_candidate = {
            "reason_codes": sorted(set(candidate.get("reason_codes") or ["review_required"])),
            "source_file": source.path,
            "pages": sorted(set(candidate.get("pages") or ())),
            "target_unit_ids": sorted(set(candidate.get("target_unit_ids") or ())),
        }
        for unit_id in stable_candidate["target_unit_ids"]:
            unit = units_by_id.get(unit_id)
            if unit is None or unit.source_id != source.source_id:
                raise ValueError(
                    "review candidate target does not belong to its source: %s" % unit_id
                )
        evidence_payload = {
            "schema_version": 1,
            "source_id": source.source_id,
            "source_file": source.path,
            "source_sha256": source.sha256,
            "candidate": stable_candidate,
        }
        digest = hashlib.sha256(canonical_json(evidence_payload).encode("utf-8")).hexdigest()
        evidence_rel = ".ingest/evidence/%s/%s.json" % (source.source_id, digest)
        encoded_evidence = (
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        evidence = EvidenceRef(
            path=evidence_rel,
            sha256=hashlib.sha256(encoded_evidence).hexdigest(),
        )
        issue = ReviewIssue.create(
            source.source_id,
            source.sha256,
            candidate.get("reason_codes") or ["review_required"],
            [evidence],
            candidate.get("description") or "Review required",
            candidate.get("suggested_action") or "Inspect the evidence and source.",
            pages=candidate.get("pages") or (),
            target_unit_ids=candidate.get("target_unit_ids") or (),
            severity=candidate.get("severity") or "warning",
        )
        if issue.issue_id in issue_ids:
            raise ValueError("ingestion payload contains duplicate review issue")
        issue_ids.add(issue.issue_id)
        issues.append(issue)
        evidence_specs.append((evidence_rel, evidence_payload))

    fact_priorities = None
    conflict_issue_ids = {}
    if parser_receipts is not None:
        fact_priorities, conflict_artifacts, conflict_issue_ids = (
            _conflict_review_snapshot(workspace_root, units, sources)
        )
        for artifact in conflict_artifacts:
            issue = artifact["issue"]
            if issue.issue_id in issue_ids:
                raise ValueError(
                    "source conflict review issue collides with another detector issue"
                )
            issue_ids.add(issue.issue_id)
            issues.append(issue)
            evidence_specs.append(
                (artifact["evidence_path"], artifact["evidence_payload"])
            )

    unbound_path = safe_workspace_entry(workspace_root, UNBOUND_REVIEW_PATH)
    manifest_path = safe_workspace_entry(workspace_root, BUILD_MANIFEST_PATH)
    parser_receipts_path = safe_workspace_entry(workspace_root, PARSER_RECEIPTS_PATH)
    fact_paths = {
        "duplicate_candidates": safe_workspace_entry(
            workspace_root, DUPLICATE_CANDIDATES_PATH
        ),
        "canonical_groups": safe_workspace_entry(workspace_root, CANONICAL_GROUPS_PATH),
        "source_conflicts": safe_workspace_entry(workspace_root, SOURCE_CONFLICTS_PATH),
        "source_priorities": safe_workspace_entry(workspace_root, SOURCE_PRIORITIES_PATH),
    }
    artifact_paths = {
        "source_manifest": store.manifest.path,
        "base_content_units": store.base_units_path,
        "base_chapter_phase_mappings": store.base_mappings_path,
        "content_units": store.units_path,
        "chapter_phase_mappings": store.mappings_path,
        "review_queue": store.review_queue.path,
        "review_patches": store.ledger_path,
        "unbound_review": unbound_path,
    }
    if parser_receipts is not None:
        artifact_paths["parser_receipts"] = parser_receipts_path
        artifact_paths.update(fact_paths)
    transaction_paths = [
        str(path.relative_to(workspace_root)).replace(os.sep, "/")
        for path in artifact_paths.values()
    ]
    if PARSER_RECEIPTS_PATH not in transaction_paths:
        transaction_paths.append(PARSER_RECEIPTS_PATH)
    for fact_path in fact_paths.values():
        relative = str(fact_path.relative_to(workspace_root)).replace(os.sep, "/")
        if relative not in transaction_paths:
            transaction_paths.append(relative)
    transaction_paths.append(BUILD_MANIFEST_PATH)
    transaction_paths.extend(relative for relative, _payload in evidence_specs)

    with store.ingest_transaction(transaction_paths):
        store.manifest.replace_all(sources)
        store.sync_base(units, mappings)
        for evidence_rel, evidence_payload in evidence_specs:
            atomic_write_json(
                safe_workspace_entry(workspace_root, evidence_rel), evidence_payload
            )
        store.review_queue.reconcile(issues)
        store.refresh_source_statuses()
        if parser_receipts is not None:
            _validate_parser_review_consistency(
                parser_receipts,
                store.manifest.records(),
                payload["page_quality"],
                store.review_queue.issues(),
                store.ledger_entries(),
            )
        if not store.ledger_path.exists():
            from .storage import atomic_write_jsonl
            atomic_write_jsonl(store.ledger_path, [])

        atomic_write_json(unbound_path, {
            "schema_version": 1,
            "entries": payload["unbound_review_candidates"],
        })
        if parser_receipts is not None:
            atomic_write_json(parser_receipts_path, {
                "schema_version": 1,
                "receipts": parser_receipts,
            })
        elif parser_receipts_path.exists():
            parser_receipts_path.unlink()

        if parser_receipts is not None:
            # Fact files use per-file atomic replacement inside this rollback
            # transaction.  The build manifest is written last and binds every
            # exact hash; validators also rederive the live graph, so a crash or
            # mixed-generation set fails closed rather than appearing current.
            fact_summary = compile_ingestion_facts(
                workspace_root,
                store.units().values(),
                sources,
                priorities=fact_priorities,
                issue_ids_by_conflict=conflict_issue_ids,
                review_patches=store.ledger_entries(),
            )
        else:
            fact_summary = None
            for fact_path in fact_paths.values():
                if fact_path.exists():
                    fact_path.unlink()

        build_manifest = {
            "schema_version": 1,
            "pipeline_version": (
                "ingestion-v2" if parser_receipts is not None else "ingestion-v1"
            ),
            "source_root": source_root,
            "source_count": len(sources),
            "page_count": len(payload["page_quality"]),
            "unit_count": len(store.units()),
            "review_issue_count": len(store.review_queue.issues()),
            "unbound_review_count": len(payload["unbound_review_candidates"]),
            "page_quality": payload["page_quality"],
            "fact_summary": fact_summary,
            "artifacts": {
                name: {
                    "path": str(path.relative_to(workspace_root)).replace(os.sep, "/"),
                    "sha256": file_sha256(path),
                }
                for name, path in sorted(artifact_paths.items())
            },
        }
        atomic_write_json(manifest_path, build_manifest)
    return build_manifest


def persist_payload(workspace, payload):
    """Persist one envelope while excluding concurrent review/build mutations."""

    _strict_payload(payload)
    store = IngestionStore(workspace, source_root=payload["source_root"])
    with store.mutation_lock():
        return _persist_payload_unlocked(workspace, payload)


def refresh_build_manifest(workspace, derived_artifacts=None, fact_summary=None):
    """Add compiled artifact hashes after wiki/index/question-bank generation."""

    workspace_path = Path(workspace).resolve()
    path = workspace_path.joinpath(*BUILD_MANIFEST_PATH.split("/"))
    if not path.is_file():
        return None
    manifest = read_json(path)
    source_root = manifest.get("source_root")
    if isinstance(source_root, str) and os.path.isdir(source_root):
        store = IngestionStore(workspace_path, source_root=source_root)
        manifest["source_count"] = len(store.manifest.records())
        manifest["unit_count"] = len(store.units())
        manifest["review_issue_count"] = len(store.review_queue.issues())
        unbound = read_json(
            workspace_path.joinpath(*UNBOUND_REVIEW_PATH.split("/")),
            default={"entries": []},
        )
        manifest["unbound_review_count"] = len(
            unbound.get("entries") if isinstance(unbound, dict)
            and isinstance(unbound.get("entries"), list) else []
        )
        manifest["page_count"] = len(
            manifest.get("page_quality")
            if isinstance(manifest.get("page_quality"), list) else []
        )
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        for row in artifacts.values():
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            absolute = safe_workspace_entry(workspace_path, row["path"])
            if absolute.is_file() and not absolute.is_symlink():
                row["sha256"] = file_sha256(absolute)
    if derived_artifacts is not None:
        derived = {}
        for label, relative in sorted(derived_artifacts.items()):
            normalized = normalize_workspace_path(relative)
            absolute = safe_workspace_entry(workspace_path, normalized)
            if absolute.is_file() and not absolute.is_symlink():
                derived[label] = {"path": normalized, "sha256": file_sha256(absolute)}
        manifest["derived_artifacts"] = derived
    else:
        derived = manifest.get("derived_artifacts")
        if isinstance(derived, dict):
            for row in derived.values():
                if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                    continue
                absolute = safe_workspace_entry(workspace_path, row["path"])
                if absolute.is_file() and not absolute.is_symlink():
                    row["sha256"] = file_sha256(absolute)
    if fact_summary is not None:
        if not isinstance(fact_summary, dict):
            raise ValueError("fact_summary must be an object")
        manifest["fact_summary"] = fact_summary
    atomic_write_json(path, manifest)
    return manifest


_RECOVERY_START = "<!-- INGEST_RECOVERY_START -->"
_RECOVERY_END = "<!-- INGEST_RECOVERY_END -->"
_VISUAL_START = "<!-- INGEST_STRUCTURED_VISUALS_START -->"
_VISUAL_END = "<!-- INGEST_STRUCTURED_VISUALS_END -->"


def _student_ready_body(unit):
    if unit.kind == "formula" and unit.latex:
        latex = unit.latex.strip()
        if (latex.startswith("$$") and latex.endswith("$$")) or (
            latex.startswith("$") and latex.endswith("$")
        ):
            return latex
        return "$$\n%s\n$$" % latex
    if unit.text and unit.text.strip():
        return unit.text.strip()
    if unit.latex and unit.latex.strip():
        latex = unit.latex.strip()
        return latex if latex.startswith("$") and latex.endswith("$") else "$%s$" % latex
    if unit.html and unit.html.strip():
        return unit.html.strip()
    return ""


def _replace_visual_block(text, rendered):
    pattern = re.compile(
        r"\n?%s.*?%s\n?" % (re.escape(_VISUAL_START), re.escape(_VISUAL_END)),
        re.DOTALL,
    )
    if not rendered and pattern.search(text) is None:
        return text
    base = pattern.sub("\n", text).rstrip()
    if not rendered:
        return base + "\n"
    return base + "\n\n" + _VISUAL_START + "\n" + rendered.rstrip() + "\n" + _VISUAL_END + "\n"


def _compile_structured_visuals(workspace_path, units, phases):
    wiki_by_chapter = {
        row.get("chapter_id"): row.get("wiki_file")
        for row in phases if row.get("chapter_id") and row.get("wiki_file")
    }
    counts = {}
    for chapter_id, relative in sorted(wiki_by_chapter.items()):
        path = safe_workspace_entry(workspace_path, relative)
        if not path.is_file() or path.is_symlink():
            raise ValueError("wiki target is missing or unsafe: %s" % relative)
        visuals = [
            unit for unit in units.values()
            if unit.chapter_id == chapter_id
            and unit.kind in ("figure", "diagram")
            and unit.asset_path
            and unit.asset_role not in ("answer_context", "worked_solution", "source_page")
        ]
        visuals.sort(key=lambda unit: (unit.source_file, unit.page, unit.ordinal, unit.unit_id))
        blocks = []
        for unit in visuals:
            asset = safe_workspace_entry(workspace_path, unit.asset_path)
            if not asset.is_file() or asset.is_symlink():
                raise ValueError("structured visual asset is missing or unsafe: %s" % unit.asset_path)
            if not unit.asset_path.startswith("references/assets/"):
                raise ValueError("structured visual must live under references/assets")
            caption = (unit.text or "%s p.%d" % (unit.source_file, unit.page)).replace(
                "]", "）"
            ).replace("\n", " ").strip()
            relative_asset = "../assets/" + unit.asset_path[len("references/assets/"):]
            blocks.append(
                "### %s · p.%d\n\n![%s](%s)\n\n"
                "来源：%s p.%d｜🟢 来自资料"
                % (
                    unit.source_file, unit.page, caption, relative_asset,
                    unit.source_file, unit.page,
                )
            )
        rendered = "## 资料原图\n\n" + "\n\n".join(blocks) if blocks else ""
        original = path.read_text(encoding="utf-8")
        updated = _replace_visual_block(original, rendered)
        if updated != original:
            atomic_write_text(path, updated)
        counts[chapter_id] = len(blocks)
    return counts


def _phase_inventory(workspace_path):
    index_path = workspace_path / "references" / "retrieval_index.json"
    phases = []
    if index_path.is_file() and not index_path.is_symlink():
        try:
            index = read_json(index_path)
            rows = index.get("integrity", {}).get("phases", [])
            if isinstance(rows, list):
                phases = [row for row in rows if isinstance(row, dict)]
        except Exception:
            phases = []
    if phases:
        validated = []
        seen_chapters = set()
        for row in phases:
            relative = normalize_workspace_path(row.get("wiki_file"))
            target = safe_workspace_entry(workspace_path, relative)
            if not target.is_file() or target.is_symlink():
                raise ValueError("phase inventory wiki target is missing or unsafe: %s" % relative)
            chapter_id = row.get("chapter_id")
            if not isinstance(chapter_id, str) or chapter_id in seen_chapters:
                raise ValueError("phase inventory has an invalid/duplicate chapter_id")
            seen_chapters.add(chapter_id)
            current = dict(row)
            current["wiki_file"] = relative
            validated.append(current)
        return validated
    wiki_dir = safe_workspace_entry(workspace_path, "references/wiki")
    if not wiki_dir.is_dir() or wiki_dir.is_symlink():
        raise ValueError("references/wiki is missing or unsafe")
    for number, path in enumerate(sorted(wiki_dir.glob("ch*.md")), 1):
        match = re.match(r"ch0*([1-9]\d*)", path.name, re.IGNORECASE)
        chapter = int(match.group(1)) if match else number
        phases.append({
            "chapter": chapter,
            "chapter_id": "ch%02d" % chapter,
            "phase_num": number,
            "phase_id": "phase%02d" % number,
            "wiki_file": "references/wiki/" + path.name,
        })
    return phases


def _replace_recovery_block(text, rendered):
    pattern = re.compile(
        r"\n?%s.*?%s\n?" % (re.escape(_RECOVERY_START), re.escape(_RECOVERY_END)),
        re.DOTALL,
    )
    if not rendered and pattern.search(text) is None:
        return text
    base = pattern.sub("\n", text).rstrip()
    if not rendered:
        return base + "\n"
    return base + "\n\n" + _RECOVERY_START + "\n" + rendered.rstrip() + "\n" + _RECOVERY_END + "\n"


def _metadata_answer(answer):
    metadata = answer.metadata if answer is not None else {}
    if "answer_value" in metadata:
        return metadata["answer_value"]
    return answer.text if answer is not None else ""


def _metadata_assets(*units):
    by_path = {}
    for unit in units:
        if unit is None:
            continue
        for asset in unit.metadata.get("assets") or ():
            by_path[asset["path"]] = dict(asset)
        if unit.asset_path and unit.asset_role:
            current = dict(by_path.get(unit.asset_path, {"path": unit.asset_path}))
            current["role"] = unit.asset_role
            by_path[unit.asset_path] = current
    return [by_path[path] for path in sorted(by_path)]


def _chapter_number(chapter_id):
    match = re.fullmatch(r"ch0*([1-9]\d*)", str(chapter_id or ""))
    return int(match.group(1)) if match else None


def _new_quiz_item(question, answer):
    metadata = question.metadata
    quiz_type = metadata.get("quiz_type")
    if not quiz_type:
        raise ValueError(
            "review-added question %s needs metadata.quiz_type before it can enter quiz_bank"
            % question.unit_id
        )
    chapter = _chapter_number(question.chapter_id)
    if chapter is None:
        raise ValueError(
            "review-added question %s needs a canonical chapter assignment"
            % question.unit_id
        )
    item = {
        "id": question.external_id,
        "chapter": chapter,
        "type": quiz_type,
        "question": question.text,
        "answer": _metadata_answer(answer),
        "source": metadata.get("source") or (
            "ai_generated" if question.provenance == "ai_supplemented" else "material"
        ),
        "source_file": question.source_file,
        "source_pages": metadata.get("source_pages") or [question.page],
        "question_provenance": question.provenance,
    }
    for key in (
        "options", "keywords", "knowledge_point", "knowledge_points", "source_type",
        "requires_assets", "maybe_requires_assets", "gradable",
        "question_text_status", "diagram_type", "language", "expected_behavior", "tests",
    ):
        if key in metadata:
            item[key] = metadata[key]
    if "source_language" in metadata:
        item["source_language"] = metadata["source_language"]
    assets = _metadata_assets(question, answer)
    if assets:
        item["assets"] = assets
    if answer is not None:
        answer_metadata = answer.metadata
        item["answer_source_file"] = (
            answer_metadata.get("answer_source_file") or answer.source_file
        )
        item["answer_source_pages"] = (
            answer_metadata.get("answer_source_pages") or [answer.page]
        )
        item["answer_provenance"] = answer.provenance
        if "source_language" in answer_metadata:
            item["answer_source_language"] = answer_metadata["source_language"]
        if answer.provenance == "ai_supplemented":
            item["source"] = "ai_generated"
    return item


def _update_quiz_item_from_units(item, question, answer, patched_unit_ids):
    updates = 0
    question_touched = question.unit_id in patched_unit_ids
    answer_touched = answer is not None and answer.unit_id in patched_unit_ids
    if question_touched:
        if question.text and item.get("question") != question.text:
            item["question"] = question.text
            updates += 1
        if item.get("question_provenance") != question.provenance:
            item["question_provenance"] = question.provenance
            updates += 1
        metadata = question.metadata
        translated = {
            "quiz_type": "type",
            "options": "options",
            "keywords": "keywords",
            "knowledge_point": "knowledge_point",
            "knowledge_points": "knowledge_points",
            "source_type": "source_type",
            "source_pages": "source_pages",
            "requires_assets": "requires_assets",
            "maybe_requires_assets": "maybe_requires_assets",
            "gradable": "gradable",
            "question_text_status": "question_text_status",
            "diagram_type": "diagram_type",
            "language": "language",
            "expected_behavior": "expected_behavior",
            "tests": "tests",
            "source_language": "source_language",
        }
        required_metadata = {"quiz_type", "source_pages"}
        for metadata_key, item_key in translated.items():
            if metadata_key == "source_pages":
                value = metadata.get(metadata_key) or [question.page]
            elif metadata_key in metadata:
                value = metadata[metadata_key]
            elif metadata_key not in required_metadata:
                if item_key in item:
                    item.pop(item_key)
                    updates += 1
                continue
            else:
                # quiz type is required in the compiled bank.  A legacy unit
                # without metadata.quiz_type cannot safely erase that identity.
                continue
            if item.get(item_key) != value:
                item[item_key] = value
                updates += 1
        if item.get("source_file") != question.source_file:
            item["source_file"] = question.source_file
            updates += 1
        chapter = _chapter_number(question.chapter_id)
        if chapter is not None and item.get("chapter") != chapter:
            item["chapter"] = chapter
            updates += 1
        if (question.provenance == "ai_supplemented"
                and item.get("source") != "ai_generated"):
            item["source"] = "ai_generated"
            updates += 1
    if answer_touched:
        value = _metadata_answer(answer)
        if item.get("answer") != value:
            item["answer"] = value
            updates += 1
        if item.get("answer_provenance") != answer.provenance:
            item["answer_provenance"] = answer.provenance
            updates += 1
        answer_metadata = answer.metadata
        answer_file = answer_metadata.get("answer_source_file") or answer.source_file
        answer_pages = answer_metadata.get("answer_source_pages") or [answer.page]
        if item.get("answer_source_file") != answer_file:
            item["answer_source_file"] = answer_file
            updates += 1
        if item.get("answer_source_pages") != answer_pages:
            item["answer_source_pages"] = answer_pages
            updates += 1
        if "source_language" in answer_metadata:
            if item.get("answer_source_language") != answer_metadata["source_language"]:
                item["answer_source_language"] = answer_metadata["source_language"]
                updates += 1
        elif "answer_source_language" in item:
            item.pop("answer_source_language")
            updates += 1
        if answer.provenance == "ai_supplemented" and item.get("source") != "ai_generated":
            item["source"] = "ai_generated"
            updates += 1
    if question_touched or answer_touched:
        assets = _metadata_assets(question, answer)
        if assets:
            if item.get("assets") != assets:
                item["assets"] = assets
                updates += 1
        elif "assets" in item:
            item.pop("assets")
            updates += 1
    return updates


def compile_structured_visuals(workspace):
    """Compile safe source-side IR figures into their chapter wiki files."""

    workspace_path = Path(workspace).resolve()
    manifest = read_json(workspace_path.joinpath(*BUILD_MANIFEST_PATH.split("/")))
    store = IngestionStore(workspace_path, source_root=manifest.get("source_root"))
    with store.mutation_lock():
        units, _mappings = store.rebuild_compiled_from_ledger()
        return _compile_structured_visuals(
            workspace_path, units, _phase_inventory(workspace_path)
        )


def _compile_review_outputs_unlocked(workspace):
    """Compile applied IR patches into wiki, quiz bank, and a fresh retrieval index."""

    workspace_path = Path(workspace).resolve()
    manifest = read_json(workspace_path.joinpath(*BUILD_MANIFEST_PATH.split("/")))
    source_root = manifest.get("source_root")
    store = IngestionStore(workspace_path, source_root=source_root)
    # The compiled JSONL files are caches, never authority.  Rebuild them from
    # immutable parser output + the verified append-only ledger before consuming
    # anything, so hand edits cannot be "washed clean" by refreshing hashes.
    units, _mappings = store.rebuild_compiled_from_ledger()
    fact_summary = None
    retrieval_units = list(units.values())
    if manifest.get("pipeline_version") == "ingestion-v2":
        fact_priorities, conflict_artifacts, conflict_issue_ids = (
            _conflict_review_snapshot(
                workspace_path, units.values(), store.manifest.records()
            )
        )
        for artifact in conflict_artifacts:
            atomic_write_json(
                safe_workspace_entry(workspace_path, artifact["evidence_path"]),
                artifact["evidence_payload"],
            )
        _reconcile_conflict_review_snapshot(
            store, [row["issue"] for row in conflict_artifacts]
        )
        store.refresh_source_statuses()
        # Review recompilation is likewise per-file atomic.  refresh_build_manifest
        # publishes the new hashes only after every derivative succeeds; an
        # interruption leaves old hashes and therefore a fail-closed mixed set.
        fact_summary = compile_ingestion_facts(
            workspace_path,
            units.values(),
            store.manifest.records(),
            priorities=fact_priorities,
            issue_ids_by_conflict=conflict_issue_ids,
            review_patches=store.ledger_entries(),
        )
        retrieval_units = fold_units_for_retrieval(
            units.values(), load_canonical_groups(workspace_path)
        )
    patched_unit_ids = store.ledger_touched_unit_ids()
    phases = _phase_inventory(workspace_path)
    wiki_by_chapter = {
        row.get("chapter_id"): row.get("wiki_file")
        for row in phases if row.get("chapter_id") and row.get("wiki_file")
    }

    visual_counts = _compile_structured_visuals(workspace_path, units, phases)
    recovery_counts = {}
    for chapter_id, relative in sorted(wiki_by_chapter.items()):
        path = safe_workspace_entry(workspace_path, relative)
        if not path.is_file() or path.is_symlink():
            raise ValueError("wiki target is missing or unsafe: %s" % relative)
        recovered = [
            unit for unit in units.values()
            if unit.chapter_id == chapter_id
            and unit.unit_id in patched_unit_ids
            and unit.kind not in ("page_anchor", "figure", "diagram", "question", "answer")
        ]
        recovered.sort(key=lambda unit: (unit.source_file, unit.page, unit.ordinal, unit.unit_id))
        blocks = []
        for unit in recovered:
            body = _student_ready_body(unit)
            if not body.strip():
                continue
            provenance_label = (
                "🟡 AI补充，可能与你老师讲的不完全一致"
                if unit.provenance == "ai_supplemented"
                else "🟢 来自资料"
            )
            blocks.append(
                "### %s · p.%d\n\n%s\n\n"
                "来源：%s p.%d｜%s"
                % (
                    unit.source_file, unit.page, body.strip(),
                    unit.source_file, unit.page, provenance_label,
                )
            )
        rendered = (
            "## AI/人工接管补录\n\n"
            "以下内容由已验证的 .ingest/review_patches.jsonl 编译；原始提取正文保持不变。\n\n"
            + "\n\n".join(blocks)
            if blocks else ""
        )
        original = path.read_text(encoding="utf-8")
        updated = _replace_recovery_block(original, rendered)
        if updated != original:
            atomic_write_text(path, updated)
        recovery_counts[chapter_id] = len(blocks)

    quiz_path = workspace_path / "references" / "quiz_bank.json"
    quiz_bank = read_json(quiz_path)
    if not isinstance(quiz_bank, list):
        raise ValueError("references/quiz_bank.json must be an array")
    quiz_by_id = {
        str(item.get("id")): item for item in quiz_bank
        if isinstance(item, dict) and item.get("id") is not None
    }
    quiz_updates = 0
    for question in sorted(units.values(), key=lambda unit: unit.unit_id):
        if question.kind != "question" or not question.external_id:
            continue
        item = quiz_by_id.get(question.external_id)
        answer = units.get(question.paired_unit_id) if question.paired_unit_id else None
        if answer is not None and answer.kind != "answer":
            raise ValueError("question paired_unit_id must refer to an answer unit")
        if item is None:
            if question.unit_id not in patched_unit_ids:
                continue
            item = _new_quiz_item(question, answer)
            quiz_bank.append(item)
            quiz_by_id[question.external_id] = item
            quiz_updates += 1
            continue
        quiz_updates += _update_quiz_item_from_units(
            item, question, answer, patched_unit_ids
        )
    atomic_write_json(quiz_path, quiz_bank)

    ingest_report_path = workspace_path / "ingest_report.json"
    if ingest_report_path.is_file() and not ingest_report_path.is_symlink():
        ingest_report = read_json(ingest_report_path)
        if isinstance(ingest_report, dict):
            ingest_report["missing_answer_ids"] = [
                item.get("id") for item in quiz_bank
                if (isinstance(item, dict)
                    and item.get("gradable") is not False
                    and item.get("answer") in (None, "", []))
            ]
            atomic_write_json(ingest_report_path, ingest_report)

    try:
        import chunk as chunk_module
        import retrieve as retrieve_module
    except ImportError:
        from scripts import chunk as chunk_module
        from scripts import retrieve as retrieve_module

    chunks = []
    for current in chunk_module.chunk_units(retrieval_units):
        chapter_id = current.get("chapter_id")
        if chapter_id not in wiki_by_chapter:
            continue
        current["file"] = (
            "references/quiz_bank.json"
            if current.get("kind") == "question"
            else wiki_by_chapter[chapter_id]
        )
        match = re.match(r"ch0*([1-9]\d*)$", chapter_id)
        current["chapter"] = match.group(1) if match else None
        chunks.append(current)

    question_unit_ids = {}
    for unit in units.values():
        if unit.kind == "question" and unit.external_id:
            question_unit_ids.setdefault(unit.external_id, []).append(unit.unit_id)

    for item in quiz_bank:
        if not isinstance(item, dict):
            continue
        points = item.get("knowledge_points")
        if isinstance(points, str):
            points = [points]
        if not isinstance(points, list):
            point = item.get("knowledge_point")
            points = [point] if isinstance(point, str) else []
        points = [point.strip() for point in points if isinstance(point, str) and point.strip()]
        if not points:
            continue
        chapter = item.get("chapter")
        try:
            chapter_id = "ch%02d" % int(chapter)
        except (TypeError, ValueError):
            chapter_id = None
        chunks.append({
            "id": "concept:%s" % item.get("id"),
            "file": wiki_by_chapter.get(chapter_id, "references/quiz_bank.json"),
            "chapter": str(chapter) if chapter is not None else None,
            "chapter_id": chapter_id,
            "title": "Knowledge points",
            "text": "\n".join(points + [str(item.get("question") or "")]),
            "kind": "concept",
            "source_file": item.get("source_file"),
            "pages": item.get("source_pages") or [],
            "unit_ids": sorted(question_unit_ids.get(str(item.get("id")), ())),
        })

    integrity = {
        "wiki": [
            {"file": row["wiki_file"], "sha256": file_sha256(
                safe_workspace_entry(workspace_path, row["wiki_file"])
            )}
            for row in phases
        ],
        "phases": phases,
        "source_manifest": {
            "file": ".ingest/source_manifest.json",
            "sha256": file_sha256(store.manifest.path),
        },
        "content_units": {
            "file": ".ingest/content_units.jsonl",
            "sha256": file_sha256(store.units_path),
        },
        "canonical_groups": {
            "file": CANONICAL_GROUPS_PATH,
            "sha256": file_sha256(
                safe_workspace_entry(workspace_path, CANONICAL_GROUPS_PATH)
            ),
        } if manifest.get("pipeline_version") == "ingestion-v2" else None,
        "source_conflicts": {
            "file": SOURCE_CONFLICTS_PATH,
            "sha256": file_sha256(
                safe_workspace_entry(workspace_path, SOURCE_CONFLICTS_PATH)
            ),
        } if manifest.get("pipeline_version") == "ingestion-v2" else None,
        "quiz_bank": {
            "file": "references/quiz_bank.json",
            "sha256": file_sha256(quiz_path),
        },
    }
    integrity = {key: value for key, value in integrity.items() if value is not None}
    index = retrieve_module.build_index(chunks, integrity=integrity)
    retrieval_path = workspace_path / "references" / "retrieval_index.json"
    atomic_write_json(retrieval_path, index)

    derived = {
        label: row["path"]
        for label, row in (manifest.get("derived_artifacts") or {}).items()
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    derived.update({
        "quiz_bank": "references/quiz_bank.json",
        "retrieval_index": "references/retrieval_index.json",
        "ingest_report": "ingest_report.json",
    })
    for row in phases:
        derived["wiki:%s" % row["chapter_id"]] = row["wiki_file"]
    refresh_build_manifest(workspace_path, derived, fact_summary=fact_summary)
    return {
        "structured_visuals_by_chapter": visual_counts,
        "recovered_units_by_chapter": recovery_counts,
        "quiz_updates": quiz_updates,
        "retrieval_chunks": len(chunks),
        "fact_summary": fact_summary,
    }


def compile_review_outputs(workspace):
    """Compile reviewed derivatives under the workspace mutation lock."""

    workspace_path = Path(workspace).resolve()
    manifest = read_json(workspace_path.joinpath(*BUILD_MANIFEST_PATH.split("/")))
    store = IngestionStore(workspace_path, source_root=manifest.get("source_root"))
    with store.mutation_lock():
        return _compile_review_outputs_unlocked(workspace_path)
