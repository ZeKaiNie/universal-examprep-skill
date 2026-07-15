#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Typed, source-bound chapter content for human study guides.

This module deliberately does not render HTML/PDF.  It owns the durable teaching contract at
``notebook/chNN.guide.json`` and a generated, marker-bounded view inside ``notebook/chNN.md``.
Renderers may consume a manifest only after :func:`validate_manifest` succeeds.

CLI::

    python scripts/study_guide_content.py --workspace <ws> validate --chapter 1 [--input draft.json]
    python scripts/study_guide_content.py --workspace <ws> import --chapter 1 --input draft.json

Exit codes: 0 valid/imported; 1 unsafe IO or invalid content; 2 argparse usage error.
"""

import argparse
import copy
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import PurePosixPath

try:
    from ingestion.claims import (
        CLAIM_RECORDS_PATH,
        CLAIM_RECEIPTS_DIR,
        ClaimVerificationReceipt,
        canonical_fact_snapshot_sha256,
        canonical_manifest_sha256,
        load_claim_records,
        validate_guide_claim_coverage,
        verify_claim_records,
    )
    from ingestion.dedup import validate_workspace_fact_integrity
    from ingestion.identifiers import file_sha256
    from ingestion.models import ContentUnit, SchemaValidationError, SourceRecord
    from ingestion.storage import ConflictError, workspace_publication_lock
except ImportError:  # imported as ``scripts.study_guide_content`` from the repo root
    from scripts.ingestion.claims import (
        CLAIM_RECORDS_PATH,
        CLAIM_RECEIPTS_DIR,
        ClaimVerificationReceipt,
        canonical_fact_snapshot_sha256,
        canonical_manifest_sha256,
        load_claim_records,
        validate_guide_claim_coverage,
        verify_claim_records,
    )
    from scripts.ingestion.dedup import validate_workspace_fact_integrity
    from scripts.ingestion.identifiers import file_sha256
    from scripts.ingestion.models import ContentUnit, SchemaValidationError, SourceRecord
    from scripts.ingestion.storage import ConflictError, workspace_publication_lock


SCHEMA_VERSION = 1
LANGUAGES = {"zh", "en", "bilingual"}
PROFILES = {"full", "abridged"}
ORIGINAL_LANGUAGES = {"zh", "en", "mixed", "unknown"}
PROMPT_ASSET_MODES = {"full_prompt", "figure_only", "none"}
SOLUTION_KINDS = {"formula", "concept", "procedure"}
SOURCE_ROLES = {"concept", "formula", "question", "answer", "solution"}
SOURCE_TYPES = {"lecture", "homework", "quiz", "mock_exam", "past_exam", "textbook", "other"}
ANSWER_PROVENANCE = {"material", "ai_supplemented", "ai_generated"}
EXPLANATION_PROVENANCE = {"material", "ai_translation", "ai_supplement"}
EXPLANATION_PROVENANCE_LABELS = {
    "material": ("🟢 来自资料", "🟢 From your materials"),
    "ai_translation": ("🟡 AI翻译，原资料为另一种语言",
                       "🟡 AI translation — source material is in another language"),
    "ai_supplement": ("🟡 AI补充，可能与你老师讲的不完全一致",
                      "🟡 AI supplement — may differ from what your teacher taught"),
}
SEMANTIC_UNIT_KINDS = {
    "title", "heading", "text", "list", "table", "formula", "figure", "diagram",
    "caption", "code", "speaker_notes", "other",
}
SEMANTIC_EXCLUSION_REASON_CODES = {
    "administrative_metadata",
    "duplicate_content",
    "outside_assessed_scope",
    "unrecoverable_source_defect",
}
PROMPT_ASSET_ROLES = {"question_context", "figure", "diagram", "table"}
ANSWER_ASSET_ROLES = {"answer_context", "worked_solution"}
CONCEPT_SOURCE_KINDS = {
    "title", "heading", "text", "list", "table", "figure", "diagram", "caption",
    "code", "speaker_notes", "other",
}
# Existing v4 banks used these narrower assessment tags.  New guide manifests write the stable
# student-facing taxonomy above, while validation compares old rows through this lossless map.
SOURCE_TYPE_ALIASES = {
    "example": "lecture",
    "lecture_quiz": "quiz",
    "practice_exam": "mock_exam",
    "exam": "past_exam",
}
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSONL_BYTES = 128 * 1024 * 1024
MAX_NOTEBOOK_BLOCK_CHARS = 8 * 1024 * 1024
MARKER_PREFIX = "EXAMPREP-STUDY-GUIDE-CONTENT:"
_ID_RE = re.compile(r"^[^\s\[\]#|`/\\]+$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_CLAIM_ID_RE = re.compile(r"^claim_[0-9a-f]{64}$")


class ContentError(ValueError):
    """A manifest or its workspace evidence violates the executable contract."""


def _duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContentError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ContentError("non-finite JSON number is forbidden: %s" % value)


def _is_link_or_reparse(path):
    if os.path.islink(path):
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _guard_workspace(workspace):
    ws = os.path.abspath(workspace)
    if not os.path.isdir(ws):
        raise ContentError("workspace does not exist or is not a directory: %s" % ws)
    if _is_link_or_reparse(ws):
        raise ContentError("workspace is a symlink/reparse point: %s" % ws)
    return ws


def _guard_workspace_child(ws, path, label, require_file=False, allow_missing=False):
    """Reject lexical escapes and every symlink/junction/reparse point below ``ws``.

    Checking only the final file is insufficient on Windows: ``notebook`` itself can be a
    junction to an unrelated directory.  This helper walks the lexical chain, then independently
    verifies realpath containment before any read.
    """
    ws = os.path.abspath(ws)
    target = os.path.abspath(path)
    try:
        if os.path.commonpath((ws, target)) != ws:
            raise ContentError("%s escapes the workspace" % label)
    except ValueError:
        raise ContentError("%s escapes the workspace" % label)
    relative = os.path.relpath(target, ws)
    if relative == os.curdir:
        parts = []
    else:
        parts = relative.split(os.sep)
    cursor = ws
    for part in parts:
        cursor = os.path.join(cursor, part)
        if os.path.lexists(cursor) and _is_link_or_reparse(cursor):
            raise ContentError("%s crosses a symlink/junction/reparse point: %s"
                               % (label, cursor))
    real_ws = os.path.realpath(ws)
    real_target = os.path.realpath(target)
    try:
        if os.path.commonpath((real_ws, real_target)) != real_ws:
            raise ContentError("%s resolves outside the workspace" % label)
    except ValueError:
        raise ContentError("%s resolves outside the workspace" % label)
    if not os.path.exists(target):
        if allow_missing:
            return target
        raise ContentError("%s is missing: %s" % (label, target))
    if require_file and not os.path.isfile(target):
        raise ContentError("%s is not a regular file: %s" % (label, target))
    return target


def _check_controls(value, path="$", seen=None, allow_reserved_marker=False):
    """Reject hidden control data in both keys and values, including escaped JSON NULs."""
    if seen is None:
        seen = set()
    container = isinstance(value, (dict, list))
    ident = id(value) if container else None
    if container:
        if ident in seen:
            raise ContentError("%s contains a recursive container" % path)
        seen.add(ident)
    if isinstance(value, str):
        for index, char in enumerate(value):
            code = ord(char)
            if (code < 0x20 and code not in (0x09, 0x0A, 0x0D)) or code == 0x7F:
                raise ContentError("%s contains forbidden control U+%04X at character %d"
                                   % (path, code, index))
            if code == 0xFFFD:
                raise ContentError("%s contains Unicode replacement character U+FFFD" % path)
        if not allow_reserved_marker and MARKER_PREFIX in value:
            raise ContentError("%s contains the reserved notebook marker prefix" % path)
    elif isinstance(value, dict):
        for key, child in value.items():
            _check_controls(key, "%s.<key>" % path, seen, allow_reserved_marker)
            _check_controls(child, "%s.%s" % (path, key), seen, allow_reserved_marker)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_controls(child, "%s[%d]" % (path, index), seen, allow_reserved_marker)
    if container:
        seen.remove(ident)


def _read_json(path, label):
    if _is_link_or_reparse(path):
        raise ContentError("%s is a symlink/reparse point: %s" % (label, path))
    if not os.path.isfile(path):
        raise ContentError("%s is missing or not a regular file: %s" % (label, path))
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ContentError("cannot stat %s: %s" % (label, exc))
    if size > MAX_JSON_BYTES:
        raise ContentError("%s exceeds %d bytes" % (label, MAX_JSON_BYTES))
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(
                stream,
                object_pairs_hook=_duplicate_object,
                parse_constant=_reject_constant,
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentError("%s is not strict UTF-8 JSON: %s" % (label, exc))
    _check_controls(value, label)
    return value


def _safe_relative_path(value, path, asset=False):
    if not isinstance(value, str) or not value.strip():
        raise ContentError("%s must be a non-empty relative POSIX path" % path)
    if value != value.strip() or "\\" in value:
        raise ContentError("%s must use canonical forward-slash path syntax" % path)
    if _DRIVE_RE.match(value) or _SCHEME_RE.match(value) or value.startswith(("/", "//")):
        raise ContentError("%s must not be absolute, a drive path, or a URL" % path)
    pure = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in pure.parts):
        raise ContentError("%s contains an unsafe path component" % path)
    normalized = "/".join(pure.parts)
    if normalized != value:
        raise ContentError("%s is not a canonical relative POSIX path" % path)
    if asset and not normalized.startswith("references/assets/"):
        raise ContentError("%s must stay under references/assets/" % path)
    return normalized


def _workspace_asset(ws, value, path):
    relative = _safe_relative_path(value, path, asset=True)
    full = os.path.abspath(os.path.join(ws, *relative.split("/")))
    try:
        if os.path.commonpath([ws, full]) != ws:
            raise ContentError("%s escapes the workspace" % path)
    except ValueError:
        raise ContentError("%s escapes the workspace" % path)
    cursor = ws
    for part in relative.split("/"):
        cursor = os.path.join(cursor, part)
        if os.path.lexists(cursor) and _is_link_or_reparse(cursor):
            raise ContentError("%s crosses a symlink/reparse point: %s" % (path, relative))
    if not os.path.isfile(full) or not os.access(full, os.R_OK):
        raise ContentError("%s is missing or unreadable: %s" % (path, relative))
    return relative


def _shape(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ContentError("%s must be an object" % path)
    required = set(required)
    allowed = required | set(optional)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContentError("%s is missing required keys: %s" % (path, ", ".join(missing)))
    if unknown:
        raise ContentError("%s has unknown keys: %s" % (path, ", ".join(unknown)))
    return value


def _list(value, path, nonempty=False):
    if not isinstance(value, list):
        raise ContentError("%s must be an array" % path)
    if nonempty and not value:
        raise ContentError("%s must not be empty" % path)
    return value


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ContentError("%s must be a non-empty string" % path)
    return value


def _identifier(value, path):
    value = _text(value, path)
    if len(value) > 200 or not _ID_RE.match(value):
        raise ContentError("%s is not a safe stable identifier" % path)
    return value


def _unique_strings(value, path, nonempty=False, identifiers=False):
    rows = _list(value, path, nonempty=nonempty)
    output = []
    seen = set()
    for index, item in enumerate(rows):
        item_path = "%s[%d]" % (path, index)
        item = _identifier(item, item_path) if identifiers else _text(item, item_path)
        if item in seen:
            raise ContentError("%s contains duplicate value %r" % (path, item))
        seen.add(item)
        output.append(item)
    return output


def _content_unit_chapter(value):
    match = re.fullmatch(r"ch0*([1-9]\d*)", str(value or ""))
    return int(match.group(1)) if match else None


def _read_content_units(ws):
    """Return ``(structured, rows, by_id)`` for the immutable ingestion IR.

    The presence of ``.ingest`` selects the structured contract.  A partial or unsafe
    structured directory fails closed instead of silently degrading to legacy denominators.
    """
    ingest_dir = os.path.join(ws, ".ingest")
    if not os.path.lexists(ingest_dir):
        return False, [], {}
    if _is_link_or_reparse(ingest_dir) or not os.path.isdir(ingest_dir):
        raise ContentError(".ingest must be a real directory inside the workspace")
    path = os.path.join(ingest_dir, "content_units.jsonl")
    if _is_link_or_reparse(path) or not os.path.isfile(path):
        raise ContentError("structured workspace requires .ingest/content_units.jsonl")
    try:
        if os.path.getsize(path) > MAX_JSONL_BYTES:
            raise ContentError(".ingest/content_units.jsonl exceeds %d bytes" % MAX_JSONL_BYTES)
        with open(path, "r", encoding="utf-8") as stream:
            raw_lines = list(stream)
    except (OSError, UnicodeDecodeError) as exc:
        raise ContentError("cannot read strict UTF-8 content units: %s" % exc)
    rows = []
    by_id = {}
    for line_number, raw in enumerate(raw_lines, 1):
        if not raw.strip():
            continue
        label = ".ingest/content_units.jsonl:%d" % line_number
        try:
            row = json.loads(
                raw,
                object_pairs_hook=_duplicate_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, ContentError) as exc:
            raise ContentError("%s is not strict JSON: %s" % (label, exc))
        _check_controls(row, label)
        if not isinstance(row, dict):
            raise ContentError("%s must be an object" % label)
        required = ("unit_id", "source_file", "page", "kind", "chapter_id", "provenance")
        missing = [key for key in required if key not in row]
        if missing:
            raise ContentError("%s is missing required content-unit keys: %s"
                               % (label, ", ".join(missing)))
        unit_id = _identifier(row["unit_id"], label + ".unit_id")
        if unit_id in by_id:
            raise ContentError("content_units.jsonl repeats unit_id %r" % unit_id)
        _safe_relative_path(row["source_file"], label + ".source_file")
        page = row["page"]
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ContentError("%s.page must be a positive integer" % label)
        _text(row["kind"], label + ".kind")
        if row.get("chapter_id") is not None and _content_unit_chapter(row["chapter_id"]) is None:
            raise ContentError("%s.chapter_id must be canonical chNN or null" % label)
        _text(row["provenance"], label + ".provenance")
        external_id = row.get("external_id")
        if external_id is not None:
            if not isinstance(external_id, str):
                raise ContentError("%s.external_id must be a string when present" % label)
            row = dict(row)
            row["external_id"] = _identifier(external_id, label + ".external_id")
        by_id[unit_id] = row
        rows.append(row)
    return True, rows, by_id


def _semantic_unit_ids(rows, chapter):
    output = []
    by_kind = {}
    for row in rows:
        if _content_unit_chapter(row.get("chapter_id")) != chapter:
            continue
        kind = row.get("kind")
        if (kind not in SEMANTIC_UNIT_KINDS
                or row.get("provenance") not in ("material", "ai_recovered")):
            continue
        output.append(row["unit_id"])
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return output, dict(sorted(by_kind.items()))


def _normalize_exact_text(value):
    """Canonicalize transport whitespace/Unicode, but never paraphrase source content."""
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", value).strip()


def _normalize_exact_latex(value):
    """Normalize only Unicode and insignificant TeX whitespace, not mathematical syntax."""
    if not isinstance(value, str):
        return None
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)).strip()


def _target_languages(language):
    if language == "bilingual":
        return {"zh", "en"}
    return {language}


def _localized(value, language, path):
    _shape(value, path, (), ("zh", "en"))
    required = _target_languages(language)
    if not required.issubset(value):
        raise ContentError("%s must contain %s for language=%s"
                           % (path, "+".join(sorted(required)), language))
    if not value:
        raise ContentError("%s must contain localized text" % path)
    for key, text in value.items():
        _text(text, "%s.%s" % (path, key))
    return value


def _translation(value, language, original_language, path):
    _shape(value, path, (), ("zh", "en"))
    source_languages = {
        "zh": {"zh"},
        "en": {"en"},
        "mixed": {"zh", "en"},
        "unknown": set(),
    }[original_language]
    required = _target_languages(language) - source_languages
    actual = set(value)
    repeated = actual & source_languages
    missing = required - actual
    if repeated or missing:
        raise ContentError(
            "%s must contain the missing target language(s) %s and must not translate the "
            "already-visible original language(s); missing=%s repeated=%s."
            % (path, sorted(required), sorted(missing), sorted(repeated))
        )
    for key, text in value.items():
        _text(text, "%s.%s" % (path, key))
    return value


def _positive_pages(value, path):
    rows = _list(value, path)
    seen = set()
    for index, page in enumerate(rows):
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ContentError("%s[%d] must be a positive integer" % (path, index))
        if page in seen:
            raise ContentError("%s contains duplicate page %d" % (path, page))
        seen.add(page)
    return rows


def _unit_asset_records(unit):
    records = []
    metadata = unit.get("metadata")
    if isinstance(metadata, dict):
        for asset in metadata.get("assets") or []:
            if isinstance(asset, dict) and isinstance(asset.get("path"), str):
                records.append(dict(asset))
    if isinstance(unit.get("asset_path"), str):
        direct = {"path": unit["asset_path"]}
        if isinstance(unit.get("asset_role"), str):
            direct["role"] = unit["asset_role"]
        records.append(direct)
    return records


def _unit_declared_sides(unit):
    """Return independently typed prompt/answer sides declared by a content unit.

    The kind remains authoritative.  Asset-side metadata may refine visual/page evidence, but it
    never turns an arbitrary prose/concept unit into a question or official answer.
    """
    sides = set()
    roles = []
    if isinstance(unit.get("asset_role"), str):
        roles.append(unit["asset_role"])
    roles.extend(record.get("role") for record in _unit_asset_records(unit))
    for role in roles:
        if role == "question_context":
            sides.add("question")
        elif role in ANSWER_ASSET_ROLES:
            sides.add("answer")
    return sides


def _source_role_matches_unit(unit, role):
    """Bind a manifest source role to the ingestion unit's kind/typed side.

    ``page_anchor`` is the sole kind-neutral escape hatch.  It is accepted here only so the
    item-specific legacy-file/page check can bind old bank evidence later; an anchor by itself is
    never sufficient evidence.  Typed visual units may stand in for an old prompt/solution page
    only when their ingestion asset side says so.
    """
    kind = unit.get("kind")
    sides = _unit_declared_sides(unit)
    if role == "concept":
        return kind in CONCEPT_SOURCE_KINDS
    if role == "formula":
        return kind == "formula"
    if role == "question":
        if kind == "question":
            return "answer" not in sides
        return kind == "page_anchor" or (
            kind in {"figure", "diagram", "table"} and "question" in sides
        )
    if role in ("answer", "solution"):
        if kind == "answer":
            return "question" not in sides
        return kind == "page_anchor" or (
            kind in {"figure", "diagram", "table"} and "answer" in sides
        )
    return False


def _source_ref(ws, value, path, unit_index=None, structured=False):
    _shape(
        value,
        path,
        ("source_file", "pages"),
        (
            "source_unit_id", "quote_span", "asset_path", "role",
            "contains_full_prompt", "claim_id",
        ),
    )
    source_file = _safe_relative_path(value["source_file"], path + ".source_file")
    pages = _positive_pages(value["pages"], path + ".pages")
    unit = value.get("source_unit_id")
    if unit is not None:
        unit = _identifier(unit, path + ".source_unit_id")
    if structured and unit is None:
        raise ContentError("%s.source_unit_id is required in a structured workspace" % path)
    if not pages and unit is None:
        raise ContentError("%s needs pages or source_unit_id" % path)
    source_unit = None
    if unit is not None and structured:
        source_unit = (unit_index or {}).get(unit)
        if source_unit is None:
            raise ContentError("%s.source_unit_id does not exist in content_units.jsonl: %s"
                               % (path, unit))
        if source_unit.get("source_file") != source_file:
            raise ContentError("%s source_file disagrees with source unit %s"
                               % (path, unit))
        if pages != [source_unit.get("page")]:
            raise ContentError("%s pages must exactly equal source unit %s page [%s]"
                               % (path, unit, source_unit.get("page")))
    if "quote_span" in value:
        _text(value["quote_span"], path + ".quote_span")
    if "claim_id" in value:
        claim_id = value["claim_id"]
        if not isinstance(claim_id, str) or not _CLAIM_ID_RE.fullmatch(claim_id):
            raise ContentError("%s.claim_id must be claim_<sha256>" % path)
    if "asset_path" in value:
        asset_path = _workspace_asset(ws, value["asset_path"], path + ".asset_path")
        if source_unit is not None:
            bound = {record.get("path") for record in _unit_asset_records(source_unit)}
            if asset_path not in bound:
                raise ContentError("%s.asset_path is not bound to source unit %s"
                                   % (path, unit))
    if "role" in value and value["role"] not in SOURCE_ROLES:
        raise ContentError("%s.role must be one of %s" % (path, sorted(SOURCE_ROLES)))
    if structured:
        role = value.get("role")
        if role is None:
            raise ContentError("%s.role is required in a structured workspace" % path)
        if not _source_role_matches_unit(source_unit, role):
            raise ContentError(
                "%s.role=%s is incompatible with content unit %s kind=%s/metadata side"
                % (path, role, unit, source_unit.get("kind"))
            )
    if "contains_full_prompt" in value:
        if value["contains_full_prompt"] is not True:
            raise ContentError("%s.contains_full_prompt, when present, must be true" % path)
        if "asset_path" not in value or value.get("role") != "question":
            raise ContentError(
                "%s.contains_full_prompt requires a question-role source asset" % path)
    return value


def _source_refs(ws, value, path, nonempty=True, unit_index=None, structured=False,
                 expected_role=None):
    rows = _list(value, path, nonempty=nonempty)
    for index, row in enumerate(rows):
        if expected_role is not None and (
                not isinstance(row, dict) or row.get("role") != expected_role):
            raise ContentError("%s[%d].role must equal %r"
                               % (path, index, expected_role))
        _source_ref(ws, row, "%s[%d]" % (path, index), unit_index, structured)
    return rows


def _chapter_matches(item, chapter):
    wanted = str(chapter)
    return any(item.get(key) is not None and str(item.get(key)) == wanted
               for key in ("chapter", "phase"))


def _canonical_source_type(value, path, manifest=False):
    if not isinstance(value, str) or not value.strip():
        raise ContentError("%s must be a non-empty source_type" % path)
    if value != value.strip():
        raise ContentError("%s must not contain surrounding whitespace" % path)
    canonical = SOURCE_TYPE_ALIASES.get(value, value)
    if canonical not in SOURCE_TYPES:
        raise ContentError("%s must be one of %s%s" % (
            path,
            sorted(SOURCE_TYPES),
            " (legacy aliases are accepted only in source manifests)" if manifest else "",
        ))
    if manifest and value != canonical:
        raise ContentError("%s must use canonical value %r instead of legacy alias %r"
                           % (path, canonical, value))
    return canonical


def _source_item_id(item, path):
    value = item.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ContentError("%s.id must be a string or integer" % path)
    return _identifier(str(value), path + ".id")


def _workspace_array(ws, relative, optional=False):
    full = os.path.join(ws, *relative.split("/"))
    if optional and not os.path.exists(full):
        return []
    _guard_workspace_child(ws, full, relative, require_file=True)
    value = _read_json(full, relative)
    if not isinstance(value, list):
        raise ContentError("%s must contain a JSON array" % relative)
    return value


def _record_item_asset(item_assets, item_id, asset, path):
    if not isinstance(asset, dict):
        raise ContentError("%s must be an object" % path)
    if "path" not in asset:
        raise ContentError("%s.path is required" % path)
    relative = _safe_relative_path(asset["path"], path + ".path", asset=True)
    role = asset.get("role")
    if role is not None and not isinstance(role, str):
        raise ContentError("%s.role must be a string" % path)
    asset_type = asset.get("type")
    if asset_type is not None and not isinstance(asset_type, str):
        raise ContentError("%s.type must be a string" % path)
    contains = asset.get("contains_full_prompt", False)
    if not isinstance(contains, bool):
        raise ContentError("%s.contains_full_prompt must be true or false" % path)
    record = {
        "path": relative,
        "role": role,
        "type": asset_type,
        "contains_full_prompt": contains,
    }
    item_assets.setdefault(item_id, {}).setdefault(relative, []).append(record)


def _record_item_asset_requirements(requirements, item_id, value, path):
    for key in ("requires_assets", "maybe_requires_assets"):
        if key not in value:
            continue
        raw = value[key]
        if type(raw) is not bool:
            raise ContentError("%s.%s must be a JSON boolean true or false" % (path, key))
        if raw:
            requirements.setdefault(item_id, set()).add(key)


def _legacy_item_locations(item, path, answer=False):
    """Return validated legacy ``(source_file, page)`` evidence for one bank row."""
    source_key = "answer_source_file" if answer else "source_file"
    pages_key = "answer_source_pages" if answer else "source_pages"
    source_file = item.get(source_key)
    pages = item.get(pages_key)
    if answer:
        source_file = source_file or item.get("source_file")
        pages = pages or item.get("source_pages")
    if source_file is None or pages is None:
        return set()
    source_file = _safe_relative_path(source_file, path + "." + source_key)
    pages = _positive_pages(pages, path + "." + pages_key)
    return {(source_file, page) for page in pages}


def _item_evidence_bucket(item_evidence, item_id):
    return item_evidence.setdefault(item_id, {
        "question_unit_ids": set(),
        "answer_unit_ids": set(),
        "legacy_question_locations": set(),
        "legacy_answer_locations": set(),
        "legacy_answer_payloads": [],
    })


def _source_inventory(workspace, chapter):
    """Build the exact item denominator plus typed source/asset evidence."""
    ws = _guard_workspace(workspace)
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise ContentError("chapter must be an integer >= 1")
    structured, units, unit_index = _read_content_units(ws)
    teaching = _workspace_array(
        ws, "references/teaching_examples.json", optional=not structured)
    quizzes = _workspace_array(ws, "references/quiz_bank.json")
    output = []
    all_seen = set()
    source_types = {}
    item_assets = {}
    item_asset_requirements = {}
    item_evidence = {}
    counts = {"teaching": 0, "quiz": 0, "content_unit_questions": 0, "unique": 0}
    for label, rows, quiz_layer in (
        ("references/teaching_examples.json", teaching, False),
        ("references/quiz_bank.json", quizzes, True),
    ):
        layer_seen = set()
        for index, row in enumerate(rows):
            item_path = "%s[%d]" % (label, index)
            if not isinstance(row, dict):
                raise ContentError("%s must be an object" % item_path)
            if not _chapter_matches(row, chapter):
                continue
            if quiz_layer:
                gradable = row.get("gradable")
                if gradable is not None and not isinstance(gradable, bool):
                    raise ContentError("%s.gradable must be true or false" % item_path)
            counts["quiz" if quiz_layer else "teaching"] += 1
            item_id = _source_item_id(row, item_path)
            if item_id in layer_seen:
                raise ContentError("%s has duplicate current-chapter id %r" % (label, item_id))
            layer_seen.add(item_id)
            if item_id not in all_seen:
                output.append(item_id)
                all_seen.add(item_id)
            evidence = _item_evidence_bucket(item_evidence, item_id)
            _record_item_asset_requirements(
                item_asset_requirements, item_id, row, item_path)
            evidence["legacy_question_locations"].update(
                _legacy_item_locations(row, item_path, answer=False))
            answer_locations = _legacy_item_locations(row, item_path, answer=True)
            evidence["legacy_answer_locations"].update(answer_locations)
            if answer_locations and row.get("answer") not in (None, "", [], {}):
                evidence["legacy_answer_payloads"].append({
                    "locations": set(answer_locations),
                    "value": row.get("answer"),
                    "source": row.get("source"),
                })
            raw_source_type = row.get("source_type")
            if raw_source_type is not None:
                source_types.setdefault(item_id, set()).add(
                    _canonical_source_type(raw_source_type, item_path + ".source_type"))
            assets = row.get("assets") or []
            if not isinstance(assets, list):
                raise ContentError("%s.assets must be an array" % item_path)
            for asset_index, asset in enumerate(assets):
                _record_item_asset(
                    item_assets, item_id, asset,
                    "%s.assets[%d]" % (item_path, asset_index),
                )
    if structured:
        unit_layer_seen = set()
        for index, unit in enumerate(units):
            if (_content_unit_chapter(unit.get("chapter_id")) != chapter
                    or unit.get("kind") != "question"):
                continue
            if "external_id" not in unit or unit.get("external_id") is None:
                raise ContentError(
                    "current-chapter question content unit %s is missing external_id"
                    % unit.get("unit_id")
                )
            item_id = unit["external_id"]
            if item_id in unit_layer_seen:
                raise ContentError("content_units.jsonl repeats current-chapter question external_id %r"
                                   % item_id)
            unit_layer_seen.add(item_id)
            _item_evidence_bucket(item_evidence, item_id)["question_unit_ids"].add(
                unit["unit_id"])
            counts["content_unit_questions"] += 1
            if item_id not in all_seen:
                output.append(item_id)
                all_seen.add(item_id)
            metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
            _record_item_asset_requirements(
                item_asset_requirements, item_id, metadata,
                ".ingest/content_units.jsonl[%d].metadata" % index)
            raw_source_type = metadata.get("source_type")
            if raw_source_type is not None:
                source_types.setdefault(item_id, set()).add(
                    _canonical_source_type(
                        raw_source_type,
                        ".ingest/content_units.jsonl[%d].metadata.source_type" % index,
                    )
                )
        for unit in units:
            if (_content_unit_chapter(unit.get("chapter_id")) != chapter
                    or unit.get("kind") != "answer" or not unit.get("external_id")):
                continue
            _item_evidence_bucket(item_evidence, unit["external_id"])[
                "answer_unit_ids"].add(unit["unit_id"])
        # Bind assets from both question and answer units sharing the source external ID.
        for index, unit in enumerate(units):
            item_id = unit.get("external_id")
            if item_id not in all_seen:
                continue
            for asset_index, asset in enumerate(_unit_asset_records(unit)):
                _record_item_asset(
                    item_assets, item_id, asset,
                    ".ingest/content_units.jsonl[%d].assets[%d]" % (index, asset_index),
                )
    conflicts = {item_id: sorted(values) for item_id, values in source_types.items()
                 if len(values) > 1}
    if conflicts:
        raise ContentError("teaching/quiz source_type conflict for duplicate item IDs: %s"
                           % conflicts)
    normalized = {item_id: (next(iter(values)) if values else None)
                  for item_id, values in source_types.items()}
    for item_id in output:
        normalized.setdefault(item_id, None)
        item_assets.setdefault(item_id, {})
        item_asset_requirements.setdefault(item_id, set())
    counts["unique"] = len(output)
    return {
        "structured": structured,
        "units": units,
        "unit_index": unit_index,
        "item_ids": output,
        "source_types": normalized,
        "item_assets": item_assets,
        "item_asset_requirements": item_asset_requirements,
        "item_evidence": item_evidence,
        "counts": counts,
    }


def _expected_item_inventory(workspace, chapter):
    inventory = _source_inventory(workspace, chapter)
    return inventory["item_ids"], inventory["source_types"]


def expected_item_ids(workspace, chapter):
    """Return all teaching/quiz items; structured workspaces also include typed question units."""
    return _expected_item_inventory(workspace, chapter)[0]


def expected_item_source_types(workspace, chapter):
    """Canonical source type by expected item; ``None`` means legacy sources omitted the tag."""
    return _expected_item_inventory(workspace, chapter)[1]


def _workspace_language(ws):
    path = os.path.join(ws, "study_state.json")
    if not os.path.exists(path):
        return None
    _guard_workspace_child(ws, path, "study_state.json", require_file=True)
    state = _read_json(path, "study_state.json")
    if not isinstance(state, dict):
        raise ContentError("study_state.json must be an object")
    raw = state.get("language")
    if raw is None:
        return None
    aliases = {"中文": "zh", "English": "en", "双语": "bilingual"}
    language = aliases.get(raw, raw)
    if language not in LANGUAGES:
        raise ContentError("study_state.json.language is not zh/en/bilingual: %r" % raw)
    return language


def _validate_quantity(value, language, path):
    _shape(value, path, ("label",), ("symbol", "value", "unit"))
    _localized(value["label"], language, path + ".label")
    for key in ("symbol", "value", "unit"):
        if key in value:
            _text(value[key], "%s.%s" % (path, key))


def _validate_formula_use(value, language, path):
    _shape(value, path,
           ("formula_id", "why_applicable", "variable_mapping", "substitution"))
    formula_id = _identifier(value["formula_id"], path + ".formula_id")
    _localized(value["why_applicable"], language, path + ".why_applicable")
    mappings = _list(value["variable_mapping"], path + ".variable_mapping")
    seen = set()
    for index, mapping in enumerate(mappings):
        mp = "%s.variable_mapping[%d]" % (path, index)
        _shape(mapping, mp, ("symbol", "maps_to"))
        symbol = _text(mapping["symbol"], mp + ".symbol")
        if symbol in seen:
            raise ContentError("%s.variable_mapping repeats symbol %r" % (path, symbol))
        seen.add(symbol)
        _localized(mapping["maps_to"], language, mp + ".maps_to")
    substitution = _text(value["substitution"], path + ".substitution")
    if "\n" in substitution or "\r" in substitution:
        raise ContentError("%s.substitution must be one line of TeX" % path)
    if substitution.strip().startswith("$") or substitution.strip().endswith("$"):
        raise ContentError("%s.substitution stores TeX without Markdown $ delimiters" % path)
    return formula_id, set(seen)


def _knowledge_ref_unit_ids(refs, expected_role, chapter, unit_index, path):
    """Validate one KP evidence layer and return its semantic unit IDs.

    ``source_unit_ids`` is an audit index, not a second independently authored claim.  In a
    structured workspace it is therefore derived from the concept refs on the knowledge point
    plus the formula refs nested below it.  Only current-chapter material/AI-recovered semantic
    units enter that index; every ref must still satisfy the chapter/provenance gate even when it
    points at non-semantic visual context.
    """
    semantic_ids = set()
    for index, ref in enumerate(refs):
        ref_path = "%s[%d]" % (path, index)
        role = ref.get("role")
        if role != expected_role:
            raise ContentError("%s.role must equal %r" % (ref_path, expected_role))
        unit = (unit_index or {}).get(ref.get("source_unit_id")) or {}
        if _content_unit_chapter(unit.get("chapter_id")) != chapter:
            raise ContentError(
                "%s must reference a current-chapter content unit (chapter %d)"
                % (ref_path, chapter)
            )
        if unit.get("provenance") not in ("material", "ai_recovered"):
            raise ContentError(
                "%s must reference material or ai_recovered evidence" % ref_path
            )
        if unit.get("kind") in SEMANTIC_UNIT_KINDS:
            semantic_ids.add(unit["unit_id"])
    return semantic_ids


def _validate_knowledge_point(ws, value, language, path, formula_ids, formula_symbols,
                              chapter=None, unit_index=None, structured=False):
    required = ["id", "title", "explanation", "formulas", "source_refs", "example_ids"]
    optional = ["explanation_provenance"]
    if structured:
        required.append("source_unit_ids")
    else:
        optional.append("source_unit_ids")
    _shape(value, path, required, optional)
    kp_id = _identifier(value["id"], path + ".id")
    _localized(value["title"], language, path + ".title")
    explanation = _localized(value["explanation"], language, path + ".explanation")
    provenance = value.get("explanation_provenance")
    if provenance is not None:
        _shape(provenance, path + ".explanation_provenance", (), ("zh", "en"))
        if set(provenance) != set(explanation):
            raise ContentError(
                "%s.explanation_provenance must label every authored explanation language"
                % path
            )
        for code, label in provenance.items():
            if label not in EXPLANATION_PROVENANCE:
                raise ContentError(
                    "%s.explanation_provenance.%s must be one of %s"
                    % (path, code, sorted(EXPLANATION_PROVENANCE))
                )
    concept_refs = _source_refs(
        ws, value["source_refs"], path + ".source_refs",
        unit_index=unit_index, structured=structured,
        expected_role="concept" if structured else None)
    source_unit_ids = _unique_strings(
        value.get("source_unit_ids", []), path + ".source_unit_ids", identifiers=True)
    examples = _unique_strings(value["example_ids"], path + ".example_ids", identifiers=True)
    formulas = _list(value["formulas"], path + ".formulas")
    local_formula_ids = set()
    derived_source_unit_ids = set()
    if structured:
        derived_source_unit_ids.update(_knowledge_ref_unit_ids(
            concept_refs, "concept", chapter, unit_index, path + ".source_refs"))
    for index, formula in enumerate(formulas):
        fp = "%s.formulas[%d]" % (path, index)
        _shape(formula, fp,
               ("id", "latex", "explanation", "variables", "applicability", "source_refs"))
        formula_id = _identifier(formula["id"], fp + ".id")
        if formula_id in formula_ids:
            raise ContentError("formula id %r is not globally unique" % formula_id)
        formula_ids.add(formula_id)
        local_formula_ids.add(formula_id)
        latex = _text(formula["latex"], fp + ".latex")
        if "\n" in latex or "\r" in latex:
            raise ContentError("%s.latex must be one line of TeX" % fp)
        if latex.strip().startswith("$") or latex.strip().endswith("$"):
            raise ContentError("%s.latex stores TeX without Markdown $ delimiters" % fp)
        _localized(formula["explanation"], language, fp + ".explanation")
        _localized(formula["applicability"], language, fp + ".applicability")
        variables = _list(formula["variables"], fp + ".variables")
        symbols = set()
        for vi, variable in enumerate(variables):
            vp = "%s.variables[%d]" % (fp, vi)
            _shape(variable, vp, ("symbol", "meaning"))
            symbol = _text(variable["symbol"], vp + ".symbol")
            if symbol in symbols:
                raise ContentError("%s.variables repeats symbol %r" % (fp, symbol))
            symbols.add(symbol)
            _localized(variable["meaning"], language, vp + ".meaning")
        formula_symbols[formula_id] = symbols
        formula_refs = _source_refs(
            ws, formula["source_refs"], fp + ".source_refs",
            unit_index=unit_index, structured=structured,
            expected_role="formula" if structured else None)
        if structured:
            expected_latex = _normalize_exact_latex(latex)
            for ref_index, ref in enumerate(formula_refs):
                source_unit = unit_index.get(ref.get("source_unit_id")) or {}
                source_latex = _normalize_exact_latex(source_unit.get("latex"))
                if source_latex is None or source_latex != expected_latex:
                    raise ContentError(
                        "%s.source_refs[%d] formula unit latex does not exactly match "
                        "the authored formula latex after whitespace/Unicode normalization"
                        % (fp, ref_index)
                    )
            derived_source_unit_ids.update(_knowledge_ref_unit_ids(
                formula_refs, "formula", chapter, unit_index, fp + ".source_refs"))
    if structured and set(source_unit_ids) != derived_source_unit_ids:
        raise ContentError(
            "%s.source_unit_ids must exactly equal semantic current-chapter "
            "material/ai_recovered units derived from concept and formula source_refs; "
            "missing=%s extra=%s"
            % (path, sorted(derived_source_unit_ids - set(source_unit_ids)),
               sorted(set(source_unit_ids) - derived_source_unit_ids))
        )
    return kp_id, examples, local_formula_ids, source_unit_ids


def _validate_answer_provenance(value, language, path, structured=False):
    if isinstance(value, str):
        if structured:
            raise ContentError("%s must be a per-language object in a structured workspace" % path)
        if value not in ANSWER_PROVENANCE:
            raise ContentError("%s must be one of %s" % (path, sorted(ANSWER_PROVENANCE)))
        return {code: value for code in _target_languages(language)}
    _shape(value, path, (), ("zh", "en"))
    required = _target_languages(language)
    missing = required - set(value)
    if missing:
        raise ContentError("%s must label each rendered answer language; missing=%s"
                           % (path, sorted(missing)))
    for code, provenance in value.items():
        if provenance not in ANSWER_PROVENANCE:
            raise ContentError("%s.%s must be one of %s"
                               % (path, code, sorted(ANSWER_PROVENANCE)))
    return dict(value)


def _trace_asset_records(source_trace, unit_index):
    output = {}
    for ref in source_trace:
        asset_path = ref.get("asset_path")
        if not asset_path:
            continue
        side_role = (
            "question_context" if ref.get("role") == "question"
            else "answer_context" if ref.get("role") in ("answer", "solution")
            else None
        )
        if side_role is None:
            continue
        source_unit = (unit_index or {}).get(ref.get("source_unit_id"))
        bound = [record for record in _unit_asset_records(source_unit or {})
                 if record.get("path") == asset_path]
        if not bound:
            bound = [{"path": asset_path}]
        for record in bound:
            merged = dict(record)
            merged["role"] = side_role
            merged["contains_full_prompt"] = bool(
                merged.get("contains_full_prompt") or ref.get("contains_full_prompt"))
            output.setdefault(asset_path, []).append(merged)
    return output


def _validate_knowledge_point_uses(value, kp_ids, language, path, structured=False):
    if value is None:
        if structured:
            raise ContentError("%s is required in a structured workspace" % path)
        return {}
    if not isinstance(value, dict):
        raise ContentError("%s must be an object keyed by knowledge-point ID" % path)
    if set(value) != set(kp_ids):
        raise ContentError("%s keys must exactly equal knowledge_point_ids; missing=%s extra=%s"
                           % (path, sorted(set(kp_ids) - set(value)),
                              sorted(set(value) - set(kp_ids))))
    for kp_id, explanation in value.items():
        _identifier(kp_id, path + ".<key>")
        _localized(explanation, language, "%s.%s" % (path, kp_id))
    return value


def _original_language_codes(original_language):
    return {
        "zh": {"zh"}, "en": {"en"}, "mixed": set(), "unknown": set(),
    }[original_language]


def _explicit_source_language(unit, path, required=False):
    """Read a typed unit's language without inferring it from prose or the walkthrough."""
    metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
    if "source_language" not in metadata:
        if required:
            raise ContentError("%s.metadata.source_language must explicitly be zh or en" % path)
        return None
    declared = metadata["source_language"]
    if declared not in ("zh", "en"):
        raise ContentError("%s.metadata.source_language must be zh or en" % path)
    return declared


def _unit_exact_payloads(unit):
    output = set()
    for key in ("text", "html", "latex"):
        normalized = _normalize_exact_text(unit.get(key))
        if normalized:
            output.add(normalized)
    return output


def _ref_location(ref):
    pages = ref.get("pages") or []
    return {(ref.get("source_file"), page) for page in pages}


def _legacy_side_ref(ref, unit, locations, side):
    if not (_ref_location(ref) & set(locations or ())):
        return False
    kind = unit.get("kind")
    return kind == "page_anchor" or (
        kind in {"figure", "diagram", "table"} and side in _unit_declared_sides(unit)
    )


def _trusted_material_answer_unit(unit):
    if unit.get("provenance") not in ("material", "ai_recovered"):
        return False
    metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
    return metadata.get("source") not in ("ai_generated", "mixed", "unknown")


def _validate_walkthrough_source_evidence(source_trace, item_id, answer_provenance,
                                           original_language, item_evidence, unit_index, path,
                                           prompt_asset_mode, prompt_text, answer):
    """Require item-bound prompt evidence and per-language official-answer evidence."""
    evidence = (item_evidence or {}).get(item_id) or {}
    question_units = set(evidence.get("question_unit_ids") or ())
    answer_units = set(evidence.get("answer_unit_ids") or ())
    legacy_questions = set(evidence.get("legacy_question_locations") or ())
    legacy_answers = set(evidence.get("legacy_answer_locations") or ())
    question_found = False
    direct_question_found = False
    prompt_text_bound = False
    material_answer_payloads = {"zh": set(), "en": set()}

    for ref in source_trace:
        role = ref.get("role")
        unit = (unit_index or {}).get(ref.get("source_unit_id")) or {}
        unit_id = unit.get("unit_id")
        if role == "question":
            direct = (unit_id in question_units
                      and unit.get("kind") == "question"
                      and unit.get("external_id") == item_id
                      and unit.get("provenance") != "ai_supplemented")
            legacy = _legacy_side_ref(ref, unit, legacy_questions, "question")
            if not (direct or legacy):
                raise ContentError(
                    "%s question source ref is not evidence for current-chapter item %r"
                    % (path, item_id)
                )
            question_found = True
            if direct:
                direct_question_found = True
                source_language = _explicit_source_language(
                    unit, "%s source unit %s" % (path, unit_id), required=True)
                if original_language != source_language:
                    raise ContentError(
                        "%s.original_language=%s disagrees with same-item question unit %s "
                        "metadata.source_language=%s"
                        % (path, original_language, unit_id, source_language)
                    )
                if prompt_text is not None and _normalize_exact_text(prompt_text) in (
                        _unit_exact_payloads(unit)):
                    prompt_text_bound = True
        elif role in ("answer", "solution"):
            direct = (unit_id in answer_units
                      and unit.get("kind") == "answer"
                      and unit.get("external_id") == item_id)
            legacy = _legacy_side_ref(ref, unit, legacy_answers, "answer")
            if not (direct or legacy):
                raise ContentError(
                    "%s %s source ref is not answer evidence for item %r"
                    % (path, role, item_id)
                )
            if direct:
                source_language = _explicit_source_language(
                    unit, "%s source unit %s" % (path, unit_id), required=False)
                if source_language is not None and _trusted_material_answer_unit(unit):
                    material_answer_payloads[source_language].update(
                        _unit_exact_payloads(unit))

    if not question_found:
        raise ContentError(
            "%s.source_trace requires a question source ref bound to current-chapter item %r"
            % (path, item_id)
        )
    if not question_units:
        raise ContentError(
            "%s requires a same-item question content unit with explicit source language; "
            "legacy page evidence alone cannot establish original_language" % path)
    for question_unit_id in sorted(question_units):
        question_unit = (unit_index or {}).get(question_unit_id) or {}
        declared = _explicit_source_language(
            question_unit, "%s source unit %s" % (path, question_unit_id), required=True)
        if original_language != declared:
            raise ContentError(
                "%s.original_language=%s disagrees with same-item question unit %s "
                "metadata.source_language=%s"
                % (path, original_language, question_unit_id, declared)
            )
    if prompt_text is not None and not direct_question_found:
        raise ContentError(
            "%s.prompt_text requires a same-item question content unit, not only legacy page "
            "evidence" % path)
    if prompt_text is not None and not prompt_text_bound:
        raise ContentError(
            "%s.prompt_text must exactly match the same-item question unit content after "
            "whitespace/Unicode normalization" % path)
    # A full-prompt page image replaces duplicate OCR, so it deliberately has no prompt_text.
    if prompt_asset_mode != "full_prompt" and prompt_text is None:
        raise ContentError("%s.prompt_text is required without a full-prompt image" % path)
    unsupported = []
    mismatched = []
    for code, provenance in answer_provenance.items():
        if provenance != "material":
            continue
        payloads = material_answer_payloads.get(code) or set()
        if not payloads:
            unsupported.append(code)
            continue
        if _normalize_exact_text(answer.get(code)) not in payloads:
            mismatched.append(code)
    if unsupported:
        raise ContentError(
            "%s answer_provenance may use material only for language(s) with same-item "
            "answer units carrying explicit metadata.source_language; unsupported=%s; use "
            "ai_supplemented/ai_generated when the source language is unknown or the rendered "
            "answer is a translation"
            % (path, unsupported)
        )
    if mismatched:
        raise ContentError(
            "%s material answer text must exactly match its same-item, same-language answer "
            "unit content after whitespace/Unicode normalization; mismatched=%s; label a "
            "paraphrase or translation ai_supplemented/ai_generated"
            % (path, mismatched)
        )


def _validate_walkthrough(ws, value, language, path, item_assets=None,
                          unit_index=None, structured=False, item_evidence=None,
                          profile=None, item_asset_requirements=None):
    required = [
        "item_id", "source_type", "answer_provenance", "knowledge_point_ids", "title",
        "original_language", "prompt_asset_mode", "prompt_asset_paths", "answer_asset_paths",
        "translation", "what_asked", "known_quantities", "unknown_quantities",
        "formula_uses", "steps", "answer", "self_check", "source_trace",
    ]
    optional = [
        "prompt_text", "solution_kind", "no_formula_reason", "knowledge_point_uses",
        "notebook_anchor",
    ]
    if structured:
        required.extend(("solution_kind", "knowledge_point_uses", "notebook_anchor"))
        optional = [key for key in optional
                    if key not in ("solution_kind", "knowledge_point_uses", "notebook_anchor")]
    _shape(
        value,
        path,
        required,
        optional,
    )
    item_id = _identifier(value["item_id"], path + ".item_id")
    notebook_anchor = value.get("notebook_anchor")
    if notebook_anchor is not None:
        _identifier(notebook_anchor, path + ".notebook_anchor")
    source_type = _canonical_source_type(value["source_type"], path + ".source_type",
                                         manifest=True)
    answer_provenance = _validate_answer_provenance(
        value["answer_provenance"], language, path + ".answer_provenance", structured)
    kp_ids = _unique_strings(value["knowledge_point_ids"], path + ".knowledge_point_ids",
                             nonempty=True, identifiers=True)
    _validate_knowledge_point_uses(
        value.get("knowledge_point_uses"), kp_ids, language,
        path + ".knowledge_point_uses", structured)
    _localized(value["title"], language, path + ".title")
    original = value["original_language"]
    if original not in ORIGINAL_LANGUAGES:
        raise ContentError("%s.original_language must be one of %s"
                           % (path, sorted(ORIGINAL_LANGUAGES)))
    mode = value["prompt_asset_mode"]
    if mode not in PROMPT_ASSET_MODES:
        raise ContentError("%s.prompt_asset_mode must be one of %s"
                           % (path, sorted(PROMPT_ASSET_MODES)))
    prompt_assets = _unique_strings(value["prompt_asset_paths"], path + ".prompt_asset_paths")
    answer_assets = _unique_strings(value["answer_asset_paths"], path + ".answer_asset_paths")
    source_trace = _source_refs(
        ws, value["source_trace"], path + ".source_trace",
        unit_index=unit_index, structured=structured)
    trace_assets = _trace_asset_records(source_trace, unit_index)
    source_assets = item_assets or {}
    for index, asset in enumerate(prompt_assets):
        _workspace_asset(ws, asset, "%s.prompt_asset_paths[%d]" % (path, index))
        records = list(source_assets.get(asset) or []) + list(trace_assets.get(asset) or [])
        if not any(record.get("role") in PROMPT_ASSET_ROLES for record in records):
            raise ContentError("%s.prompt_asset_paths[%d] is not bound to a prompt-side source asset"
                               % (path, index))
    for index, asset in enumerate(answer_assets):
        _workspace_asset(ws, asset, "%s.answer_asset_paths[%d]" % (path, index))
        records = list(source_assets.get(asset) or []) + list(trace_assets.get(asset) or [])
        if not any(record.get("role") in ANSWER_ASSET_ROLES for record in records):
            raise ContentError("%s.answer_asset_paths[%d] is not bound to an answer-side source asset"
                               % (path, index))
    if mode in ("full_prompt", "figure_only") and not prompt_assets:
        raise ContentError("%s.prompt_asset_paths is required for prompt_asset_mode=%s"
                           % (path, mode))
    if mode == "none" and prompt_assets:
        raise ContentError("%s.prompt_asset_paths must be empty for prompt_asset_mode=none" % path)
    if profile == "full":
        expected_prompt_assets = {
            asset for asset, records in source_assets.items()
            if any(record.get("role") in PROMPT_ASSET_ROLES for record in records)
        }
        expected_answer_assets = {
            asset for asset, records in source_assets.items()
            if any(record.get("role") in ANSWER_ASSET_ROLES for record in records)
        }
        declared_visual_dependency = sorted(item_asset_requirements or ())
        if declared_visual_dependency and not expected_prompt_assets:
            raise ContentError(
                "%s source item declares %s=true but has no recoverable question-side asset; "
                "return to ingestion/review to recover the source image before building a full "
                "Study Guide"
                % (path, "/".join(declared_visual_dependency))
            )
        if set(prompt_assets) != expected_prompt_assets:
            raise ContentError(
                "%s full profile prompt_asset_paths must exactly cover every known "
                "question-side asset; missing=%s extra=%s"
                % (path, sorted(expected_prompt_assets - set(prompt_assets)),
                   sorted(set(prompt_assets) - expected_prompt_assets))
            )
        if set(answer_assets) != expected_answer_assets:
            raise ContentError(
                "%s full profile answer_asset_paths must exactly cover every known "
                "answer-side asset; missing=%s extra=%s"
                % (path, sorted(expected_answer_assets - set(answer_assets)),
                   sorted(set(answer_assets) - expected_answer_assets))
            )
    if mode == "full_prompt":
        full_prompt_evidence = False
        for asset in prompt_assets:
            records = list(source_assets.get(asset) or []) + list(trace_assets.get(asset) or [])
            if any(
                    (record.get("role") == "question_context"
                     and record.get("type") == "page_image")
                    or bool(record.get("contains_full_prompt"))
                    for record in records):
                full_prompt_evidence = True
                break
        if not full_prompt_evidence:
            raise ContentError(
                "%s full_prompt requires a whole-page question_context asset or explicit "
                "contains_full_prompt evidence" % path)
        if "prompt_text" in value:
            raise ContentError("%s.prompt_text must be omitted when the full original prompt is visible"
                               % path)
    else:
        if "prompt_text" not in value:
            raise ContentError("%s.prompt_text is required unless a full prompt image is visible" % path)
        _text(value["prompt_text"], path + ".prompt_text")
    _translation(value["translation"], language, original, path + ".translation")
    _localized(value["what_asked"], language, path + ".what_asked")
    for field in ("known_quantities", "unknown_quantities"):
        rows = _list(value[field], "%s.%s" % (path, field))
        for index, quantity in enumerate(rows):
            _validate_quantity(quantity, language, "%s.%s[%d]" % (path, field, index))
    formula_uses = _list(value["formula_uses"], path + ".formula_uses")
    solution_kind = value.get("solution_kind") or ("formula" if formula_uses else "concept")
    if solution_kind not in SOLUTION_KINDS:
        raise ContentError("%s.solution_kind must be one of %s"
                           % (path, sorted(SOLUTION_KINDS)))
    no_formula_reason = value.get("no_formula_reason")
    if solution_kind == "formula":
        if not formula_uses:
            raise ContentError("%s solution_kind=formula requires non-empty formula_uses" % path)
        if no_formula_reason is not None:
            raise ContentError("%s.no_formula_reason must be omitted for formula solutions" % path)
    else:
        if formula_uses:
            raise ContentError("%s solution_kind=%s requires empty formula_uses"
                               % (path, solution_kind))
        if no_formula_reason is None:
            if structured:
                raise ContentError("%s.no_formula_reason is required for non-formula solutions" % path)
        else:
            _localized(no_formula_reason, language, path + ".no_formula_reason")
    used_formula_ids = []
    mapped_symbols = []
    for index, formula_use in enumerate(formula_uses):
        formula_id, symbols = _validate_formula_use(
            formula_use, language, "%s.formula_uses[%d]" % (path, index))
        used_formula_ids.append(formula_id)
        mapped_symbols.append((formula_id, symbols, index))
    steps = _list(value["steps"], path + ".steps", nonempty=True)
    for index, step in enumerate(steps):
        _localized(step, language, "%s.steps[%d]" % (path, index))
    _localized(value["answer"], language, path + ".answer")
    _localized(value["self_check"], language, path + ".self_check")
    if structured:
        _validate_walkthrough_source_evidence(
            source_trace, item_id, answer_provenance, original,
            item_evidence, unit_index, path, mode, value.get("prompt_text"), value["answer"],
        )
    if "material" in answer_provenance.values() and not any(
            ref.get("role") in ("answer", "solution") for ref in source_trace):
        raise ContentError("%s material answer_provenance requires an answer/solution source ref" % path)
    return {
        "item_id": item_id,
        "source_type": source_type,
        "knowledge_point_ids": kp_ids,
        "used_formula_ids": used_formula_ids,
        "mapped_symbols": mapped_symbols,
        "solution_kind": solution_kind,
        "notebook_anchor": notebook_anchor,
    }


def _validate_omission(ws, value, language, path, unit_index=None, structured=False):
    _shape(value, path, ("item_id", "knowledge_point_ids", "reason", "source_refs"))
    item_id = _identifier(value["item_id"], path + ".item_id")
    kp_ids = _unique_strings(value["knowledge_point_ids"], path + ".knowledge_point_ids",
                             nonempty=True, identifiers=True)
    _localized(value["reason"], language, path + ".reason")
    _source_refs(ws, value["source_refs"], path + ".source_refs",
                 unit_index=unit_index, structured=structured)
    return item_id, kp_ids


def _validate_semantic_exclusions(ws, value, language, path, profile, unit_index,
                                  chapter, structured):
    rows = _list(value, path)
    output = []
    seen = set()
    for index, row in enumerate(rows):
        row_path = "%s[%d]" % (path, index)
        required = ["source_unit_id", "reason"]
        optional = ["reason_code", "source_refs"]
        if profile == "full":
            required.extend(("reason_code", "source_refs"))
            optional = []
        _shape(row, row_path, required, optional)
        unit_id = _identifier(row["source_unit_id"], row_path + ".source_unit_id")
        if unit_id in seen:
            raise ContentError("%s repeats source_unit_id %r" % (path, unit_id))
        seen.add(unit_id)
        _localized(row["reason"], language, row_path + ".reason")
        target = (unit_index or {}).get(unit_id)
        if structured and target is None:
            raise ContentError("%s.source_unit_id does not exist in content_units.jsonl"
                               % row_path)
        if profile == "full":
            if not structured:
                raise ContentError(
                    "%s profile=full semantic exclusions require structured source evidence"
                    % row_path)
            if target.get("kind") == "formula":
                raise ContentError(
                    "%s cannot exclude a material/ai_recovered formula in profile=full"
                    % row_path)
            reason_code = row["reason_code"]
            if reason_code not in SEMANTIC_EXCLUSION_REASON_CODES:
                raise ContentError("%s.reason_code must be one of %s"
                                   % (row_path, sorted(SEMANTIC_EXCLUSION_REASON_CODES)))
            refs = _source_refs(
                ws, row["source_refs"], row_path + ".source_refs", nonempty=True,
                unit_index=unit_index, structured=structured,
                expected_role="concept" if structured else None)
            if structured:
                _knowledge_ref_unit_ids(
                    refs, "concept", chapter, unit_index, row_path + ".source_refs")
        elif "reason_code" in row and row["reason_code"] not in (
                SEMANTIC_EXCLUSION_REASON_CODES):
            raise ContentError("%s.reason_code must be one of %s"
                               % (row_path, sorted(SEMANTIC_EXCLUSION_REASON_CODES)))
        if profile != "full" and "source_refs" in row:
            _source_refs(
                ws, row["source_refs"], row_path + ".source_refs", nonempty=True,
                unit_index=unit_index, structured=structured)
        output.append(unit_id)
    return output


def _official_walkthrough_anchors(ws, chapter):
    notebook_dir = os.path.join(ws, "notebook")
    _guard_workspace_child(
        ws, notebook_dir, "notebook", allow_missing=True)
    path = os.path.join(ws, "notebook", "ch%02d.md" % chapter)
    _guard_workspace_child(
        ws, path, "notebook/ch%02d.md" % chapter,
        require_file=os.path.exists(path), allow_missing=True)
    text = _read_optional_text(path)
    try:
        try:
            from scripts import notebook as notebook_engine
        except ImportError:  # direct ``python scripts/study_guide_content.py`` execution
            import notebook as notebook_engine
        preamble, blocks = notebook_engine.parse_chapter(text)
        anchors = notebook_engine.anchors_for(preamble, blocks)
        type_by_label = notebook_engine._label_to_type()
    except (AttributeError, ImportError, ValueError) as exc:
        raise ContentError("cannot parse notebook.py walkthrough evidence: %s" % exc)
    result = {}
    for block, anchor in zip(blocks, anchors):
        type_label, timestamp = notebook_engine._block_meta(block["lines"])
        if type_by_label.get(type_label) != "walkthrough" or not timestamp:
            continue
        result[anchor] = block["id"]
    return result


def _validate_walkthrough_notebook_anchors(ws, chapter, walkthroughs, require_all=False):
    official = _official_walkthrough_anchors(ws, chapter)
    validated = 0
    for index, walk in enumerate(walkthroughs):
        path = "$.walkthroughs[%d].notebook_anchor" % index
        anchor = walk.get("notebook_anchor")
        if anchor is None:
            if require_all:
                raise ContentError("%s is required before importing a true Study Guide" % path)
            continue
        _identifier(anchor, path)
        entry_id = official.get(anchor)
        if entry_id is None:
            raise ContentError(
                "%s does not identify a pre-existing notebook.py walkthrough entry" % path)
        if entry_id != walk.get("item_id"):
            raise ContentError("%s belongs to notebook entry %r, not walkthrough item %r"
                               % (path, entry_id, walk.get("item_id")))
        validated += 1
    return validated


def _ingestion_pipeline_version(ws):
    """Return the declared ingestion generation without upgrading legacy workspaces.

    Structured workspaces must declare their generation explicitly.  This
    prevents deleting one field from silently downgrading a v2 workspace around
    its claim gate.  Genuine v1 builds remain supported when they say so.
    """

    path = os.path.join(ws, ".ingest", "build_manifest.json")
    if not os.path.lexists(path):
        raise ContentError(
            "structured workspace requires .ingest/build_manifest.json with explicit pipeline_version"
        )
    _guard_workspace_child(ws, path, ".ingest/build_manifest.json", require_file=True)
    document = _read_json(path, ".ingest/build_manifest.json")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ContentError(".ingest/build_manifest.json has an invalid schema_version")
    version = document.get("pipeline_version")
    if version not in ("ingestion-v1", "ingestion-v2"):
        raise ContentError(".ingest/build_manifest.json pipeline_version is unsupported")
    return version


def _validate_v2_claim_gate(ws, chapter, manifest, inventory):
    """Recompute the current location-only receipt and enforce material coverage."""

    chapter_id = "ch%02d" % chapter
    ingest_paths = {
        "source_manifest": ".ingest/source_manifest.json",
        "content_units": ".ingest/content_units.jsonl",
        "canonical_groups": ".ingest/canonical_groups.jsonl",
        "source_conflicts": ".ingest/source_conflicts.jsonl",
        "claim_records": CLAIM_RECORDS_PATH,
        "receipt": "%s/%s.json" % (CLAIM_RECEIPTS_DIR, chapter_id),
    }
    absolute = {}
    for label, relative in ingest_paths.items():
        path = os.path.join(ws, *relative.split("/"))
        _guard_workspace_child(ws, path, relative, require_file=True)
        absolute[label] = path
    try:
        source_document = _read_json(absolute["source_manifest"], ".ingest/source_manifest.json")
        if (not isinstance(source_document, dict)
                or set(source_document) != {"schema_version", "sources"}
                or source_document.get("schema_version") != 1
                or not isinstance(source_document.get("sources"), list)):
            raise ContentError(".ingest/source_manifest.json has an invalid exact schema")
        sources = tuple(SourceRecord.from_dict(row) for row in source_document["sources"])
        units = tuple(ContentUnit.from_dict(row) for row in inventory["units"])
        records = load_claim_records(ws, allow_empty=True)
        fact_integrity = validate_workspace_fact_integrity(ws)
        conflicts = fact_integrity["conflicts"]
        unresolved = sorted(
            conflict.conflict_id for conflict in conflicts
            if conflict.status == "unresolved"
        )
        if unresolved:
            raise ContentError(
                "ingestion-v2 Study Guide is blocked by unresolved source conflicts: %s"
                % unresolved
            )
        coverage = validate_guide_claim_coverage(
            records, manifest, chapter_id, units
        )
        receipt_document = _read_json(
            absolute["receipt"], ".ingest/claim_verification_receipts/%s.json" % chapter_id
        )
        receipt = ClaimVerificationReceipt.from_dict(receipt_document)
        expected = verify_claim_records(
            records,
            units,
            sources,
            chapter_id,
            manifest=manifest,
            guide_content_sha256=canonical_manifest_sha256(manifest),
            source_manifest_sha256=file_sha256(absolute["source_manifest"]),
            content_units_sha256=file_sha256(absolute["content_units"]),
            canonical_groups_sha256=file_sha256(absolute["canonical_groups"]),
            source_conflicts_sha256=file_sha256(absolute["source_conflicts"]),
            claim_records_sha256=file_sha256(absolute["claim_records"]),
            fact_snapshot_sha256=canonical_fact_snapshot_sha256(
                fact_integrity["snapshot"]
            ),
        )
        if receipt.to_dict() != expected.to_dict():
            raise ContentError(
                "claim verification receipt is stale or does not match the current guide/source facts"
            )
    except ContentError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ContentError("ingestion-v2 claim verification failed: %s" % exc)
    return {
        "required": True,
        "verification_scope": "location_only",
        "receipt_id": receipt.receipt_id,
        "verified_claim_count": receipt.verified_claim_count,
        "fact_snapshot_sha256": receipt.fact_snapshot_sha256,
        "required_material_assertion_count": len(coverage),
        "fact_integrity": fact_integrity["snapshot"],
    }


def validate_manifest(workspace, chapter, manifest, _enforce_v2_claims=True):
    """Validate a parsed manifest against the current chapter source slices.

    ``full`` is an exact set equality gate. ``abridged`` must partition the same expected set
    between walkthroughs and a reasoned omission ledger; it may never silently drop an item.
    """
    ws = _guard_workspace(workspace)
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise ContentError("chapter must be an integer >= 1")
    _check_controls(manifest)
    inventory = _source_inventory(ws, chapter)
    structured = inventory["structured"]
    top_required = [
        "schema_version", "chapter", "language", "profile", "knowledge_points",
        "walkthroughs", "omissions",
    ]
    top_optional = []
    if structured:
        top_required.append("semantic_exclusions")
    else:
        top_optional.append("semantic_exclusions")
    _shape(manifest, "$", top_required, top_optional)
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ContentError("$.schema_version must equal %d" % SCHEMA_VERSION)
    if isinstance(manifest["chapter"], bool) or manifest["chapter"] != chapter:
        raise ContentError("$.chapter must equal CLI chapter %d" % chapter)
    language = manifest["language"]
    if language not in LANGUAGES:
        raise ContentError("$.language must be zh/en/bilingual")
    state_language = _workspace_language(ws)
    if state_language is not None and language != state_language:
        raise ContentError("$.language=%s does not match study_state.json.language=%s"
                           % (language, state_language))
    profile = manifest["profile"]
    if profile not in PROFILES:
        raise ContentError("$.profile must be full or abridged")

    expected_order = inventory["item_ids"]
    expected_source_types = inventory["source_types"]
    expected = set(expected_order)
    semantic_order, semantic_by_kind = _semantic_unit_ids(inventory["units"], chapter)
    expected_semantic = set(semantic_order)
    exclusion_ids = _validate_semantic_exclusions(
        ws, manifest.get("semantic_exclusions", []), language, "$.semantic_exclusions",
        profile, inventory["unit_index"], chapter, structured)
    excluded_semantic = set(exclusion_ids)
    knowledge_rows = _list(manifest["knowledge_points"], "$.knowledge_points", nonempty=True)
    kp_ids = set()
    formula_ids = set()
    formula_symbols = {}
    formula_ids_by_kp = {}
    item_kps = {}
    mapped_semantic = set()
    for index, row in enumerate(knowledge_rows):
        kp_id, example_ids, local_formulas, source_unit_ids = _validate_knowledge_point(
            ws, row, language, "$.knowledge_points[%d]" % index, formula_ids,
            formula_symbols, chapter, inventory["unit_index"], structured)
        if kp_id in kp_ids:
            raise ContentError("knowledge point id %r is not unique" % kp_id)
        kp_ids.add(kp_id)
        formula_ids_by_kp[kp_id] = local_formulas
        mapped_semantic.update(source_unit_ids)
        for item_id in example_ids:
            item_kps.setdefault(item_id, set()).add(kp_id)
    semantic_overlap = mapped_semantic & excluded_semantic
    if semantic_overlap:
        raise ContentError("semantic units cannot be both knowledge-point evidence and excluded: %s"
                           % sorted(semantic_overlap))
    semantic_covered = mapped_semantic | excluded_semantic
    if semantic_covered != expected_semantic:
        raise ContentError(
            "knowledge_points.source_unit_ids plus semantic_exclusions must exactly cover current "
            "chapter material/ai_recovered semantic units; missing=%s extra=%s"
            % (sorted(expected_semantic - semantic_covered),
               sorted(semantic_covered - expected_semantic)))
    mapped = set(item_kps)
    if mapped != expected:
        raise ContentError("knowledge_points.example_ids must exactly map expected items; "
                           "missing=%s extra=%s"
                           % (sorted(expected - mapped), sorted(mapped - expected)))

    walkthrough_rows = _list(manifest["walkthroughs"], "$.walkthroughs")
    walkthrough_ids = set()
    for index, row in enumerate(walkthrough_rows):
        result = _validate_walkthrough(
            ws, row, language, "$.walkthroughs[%d]" % index,
            inventory["item_assets"].get(row.get("item_id"), {}),
            inventory["unit_index"], structured, inventory["item_evidence"], profile,
            inventory["item_asset_requirements"].get(row.get("item_id"), set()))
        item_id = result["item_id"]
        source_type = result["source_type"]
        linked_kps = result["knowledge_point_ids"]
        used_formulas = result["used_formula_ids"]
        if item_id in walkthrough_ids:
            raise ContentError("walkthrough item_id %r is not unique" % item_id)
        walkthrough_ids.add(item_id)
        if item_id not in expected:
            raise ContentError("walkthrough %r is not a current-chapter teaching/quiz item"
                               % item_id)
        expected_source_type = expected_source_types.get(item_id)
        if expected_source_type is not None and source_type != expected_source_type:
            raise ContentError("walkthrough %r source_type=%s disagrees with source manifests (%s)"
                               % (item_id, source_type, expected_source_type))
        linked = set(linked_kps)
        if linked != item_kps.get(item_id, set()):
            raise ContentError("walkthrough %r knowledge_point_ids disagree with knowledge point links"
                               % item_id)
        available_formulas = set()
        for kp_id in linked:
            if kp_id not in kp_ids:
                raise ContentError("walkthrough %r references unknown knowledge point %r"
                                   % (item_id, kp_id))
            available_formulas.update(formula_ids_by_kp[kp_id])
        unknown_formulas = set(used_formulas) - available_formulas
        if unknown_formulas:
            raise ContentError("walkthrough %r uses formulas outside its knowledge points: %s"
                               % (item_id, sorted(unknown_formulas)))
        for formula_id, mapped_symbols, formula_use_index in result["mapped_symbols"]:
            if formula_id not in formula_symbols:
                continue
            expected_symbols = formula_symbols[formula_id]
            if mapped_symbols != expected_symbols:
                raise ContentError(
                    "walkthrough %r formula_uses[%d].variable_mapping must exactly cover formula "
                    "%s variables; missing=%s extra=%s"
                    % (item_id, formula_use_index, formula_id,
                       sorted(expected_symbols - mapped_symbols),
                       sorted(mapped_symbols - expected_symbols)))

    notebook_anchor_count = _validate_walkthrough_notebook_anchors(
        ws, chapter, walkthrough_rows, require_all=True)
    if not walkthrough_rows or notebook_anchor_count != len(walkthrough_rows):
        raise ContentError(
            "a true Study Guide requires at least one pre-existing notebook.py walkthrough "
            "anchor for every walkthrough; an empty notebook is source-packet-only"
        )

    omission_rows = _list(manifest["omissions"], "$.omissions")
    omission_ids = set()
    for index, row in enumerate(omission_rows):
        item_id, linked_kps = _validate_omission(
            ws, row, language, "$.omissions[%d]" % index,
            inventory["unit_index"], structured)
        if item_id in omission_ids:
            raise ContentError("omission item_id %r is not unique" % item_id)
        omission_ids.add(item_id)
        if item_id not in expected:
            raise ContentError("omission %r is not an expected current-chapter item" % item_id)
        if set(linked_kps) != item_kps.get(item_id, set()):
            raise ContentError("omission %r knowledge_point_ids disagree with knowledge point links"
                               % item_id)

    overlap = walkthrough_ids & omission_ids
    if overlap:
        raise ContentError("items cannot be both walkthroughs and omissions: %s" % sorted(overlap))
    if profile == "full":
        if omission_ids:
            raise ContentError("profile=full requires an empty omission ledger")
        if walkthrough_ids != expected:
            raise ContentError("profile=full requires exact walkthrough coverage; missing=%s extra=%s"
                               % (sorted(expected - walkthrough_ids),
                                  sorted(walkthrough_ids - expected)))
    else:
        if not omission_ids:
            raise ContentError("profile=abridged requires at least one explicit omission")
        if walkthrough_ids | omission_ids != expected:
            raise ContentError("profile=abridged must partition all expected items; unaccounted=%s"
                               % sorted(expected - walkthrough_ids - omission_ids))

    claim_verification = None
    if (structured and _enforce_v2_claims
            and _ingestion_pipeline_version(ws) == "ingestion-v2"):
        claim_verification = _validate_v2_claim_gate(
            ws, chapter, manifest, inventory
        )

    report = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "chapter": chapter,
        "language": language,
        "profile": profile,
        "expected_item_ids": expected_order,
        "walkthrough_item_ids": [row["item_id"] for row in walkthrough_rows],
        "omitted_item_ids": [row["item_id"] for row in omission_rows],
        "knowledge_point_ids": [row["id"] for row in knowledge_rows],
        "structured_workspace": structured,
        "expected_item_counts": dict(inventory["counts"]),
        "semantic_unit_counts": {
            "expected": len(expected_semantic),
            "knowledge_point_mapped": len(mapped_semantic),
            "excluded": len(excluded_semantic),
            "by_kind": semantic_by_kind,
        },
        "notebook_anchor_count": notebook_anchor_count,
    }
    if claim_verification is not None:
        report["claim_verification"] = claim_verification
    return report


def manifest_path(workspace, chapter):
    ws = _guard_workspace(workspace)
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise ContentError("chapter must be an integer >= 1")
    return os.path.join(ws, "notebook", "ch%02d.guide.json" % chapter)


@contextmanager
def _study_guide_mutation_lock(ws):
    """Use the global state->ingestion lock order for Guide publication."""

    try:
        with workspace_publication_lock(ws):
            yield
    except (ConflictError, SchemaValidationError, OSError) as exc:
        raise ContentError("cannot mutate Study Guide: %s" % exc) from exc


def load_and_validate_manifest(workspace, chapter, path=None):
    ws = _guard_workspace(workspace)
    source = os.path.abspath(path) if path else manifest_path(ws, chapter)
    _guard_workspace_child(
        ws, source, "study-guide content manifest", require_file=True)
    manifest = _read_json(source, "study-guide content manifest")
    report = validate_manifest(ws, chapter, manifest)
    report["input_path"] = source
    return manifest, report


def _inline_text(lines, prefix, value):
    chunks = value.splitlines() or [value]
    lines.append("%s%s" % (prefix, chunks[0]))
    continuation = "  " + (" " * max(0, len(prefix) - 2))
    for chunk in chunks[1:]:
        lines.append("%s%s" % (continuation, chunk))


def _localized_lines(lines, label_zh, label_en, value, language, bullet="-"):
    if language in ("zh", "bilingual"):
        _inline_text(lines, "%s **%s：** " % (bullet, label_zh), value["zh"])
    if language in ("en", "bilingual"):
        _inline_text(lines, "%s **%s:** " % (bullet, label_en), value["en"])


def _heading_title(value, language):
    keys = ["zh", "en"] if language == "bilingual" else [language]
    return " / ".join(re.sub(r"\s+", " ", value[key]).strip() for key in keys)


def _view_label(language, zh, en):
    if language == "zh":
        return zh
    if language == "en":
        return en
    return "%s / %s" % (zh, en)


def _answer_provenance_by_language(value, language):
    if isinstance(value, dict):
        return value
    return {code: value for code in _target_languages(language)}


def _format_source(ref):
    # The notebook lives under ``workspace/notebook`` while source_file is relative to the
    # separately confirmed materials root.  A relative Markdown link would therefore point at
    # the wrong tree.  The HTML renderer receives the confirmed root and emits safe file URIs;
    # this durable view keeps an honest, copyable page locator instead.
    pages = ", ".join("p.%d" % page for page in ref["pages"]) or "n/a"
    result = "%s · %s" % (ref["source_file"], pages)
    if ref.get("source_unit_id"):
        result += " · unit %s" % ref["source_unit_id"]
    if ref.get("role"):
        result += " · %s" % ref["role"]
    return result


def render_notebook_block(manifest):
    """Render a deterministic readable view; all content lines remain inside one notebook entry."""
    language = manifest["language"]
    colon = "：" if language == "zh" else ":"
    lines = []
    title = "知识点 / Knowledge points" if language == "bilingual" else (
        "知识点" if language == "zh" else "Knowledge points")
    lines.extend(["### %s" % title, ""])
    for kp in manifest["knowledge_points"]:
        lines.append("#### `%s` · %s" % (kp["id"], _heading_title(kp["title"], language)))
        lines.append("")
        _localized_lines(lines, "解释", "Explanation", kp["explanation"], language)
        explanation_provenance = kp.get("explanation_provenance") or {
            code: "material" for code in kp["explanation"]
        }
        for code in ("zh", "en"):
            if code not in _target_languages(language):
                continue
            label = EXPLANATION_PROVENANCE_LABELS[explanation_provenance[code]][
                0 if code == "zh" else 1]
            lines.append("- **%s (%s)%s** %s" % (
                _view_label(language, "解释来源性质", "Explanation provenance"),
                "中文" if code == "zh" else "English", colon, label))
        lines.append("- **%s%s** %s" % (
            _view_label(language, "例题", "Examples"),
            "：" if language == "zh" else ":",
            ", ".join(kp["example_ids"]) or "—"))
        if kp.get("source_unit_ids"):
            lines.append("- **%s%s** %s" % (
                _view_label(language, "覆盖的内容单元", "Covered content units"),
                "：" if language == "zh" else ":",
                ", ".join(kp["source_unit_ids"])))
        for formula in kp["formulas"]:
            lines.extend(["- **%s `%s`%s**" % (
                _view_label(language, "公式", "Formula"), formula["id"],
                "：" if language == "zh" else ":"), "", "$$%s$$" % formula["latex"], ""])
            _localized_lines(lines, "公式含义", "Formula meaning", formula["explanation"], language)
            _localized_lines(lines, "适用条件", "Applicability", formula["applicability"], language)
            for variable in formula["variables"]:
                _localized_lines(lines, "变量 %s" % variable["symbol"],
                                 "Variable %s" % variable["symbol"],
                                 variable["meaning"], language)
        for source in kp["source_refs"]:
            lines.append("- **%s%s** %s" % (
                _view_label(language, "来源", "Source"),
                "：" if language == "zh" else ":", _format_source(source)))
        lines.append("")

    walkthrough_title = "例题精讲 / Worked examples" if language == "bilingual" else (
        "例题精讲" if language == "zh" else "Worked examples")
    lines.extend(["### %s" % walkthrough_title, ""])
    for walk in manifest["walkthroughs"]:
        lines.append("#### `%s` · %s" % (walk["item_id"], _heading_title(walk["title"], language)))
        lines.append("")
        lines.append("- **%s%s** %s" % (
            _view_label(language, "知识点", "Knowledge points"), colon,
            ", ".join(walk["knowledge_point_ids"])))
        lines.append("- **%s%s** `%s`" % (
            _view_label(language, "例题来源类型", "Source type"), colon,
            walk["source_type"]))
        provenance = _answer_provenance_by_language(walk["answer_provenance"], language)
        for code in ("zh", "en"):
            if code not in _target_languages(language):
                continue
            lines.append("- **%s (%s)%s** `%s`" % (
                _view_label(language, "答案来源性质", "Answer provenance"),
                "中文" if code == "zh" else "English", colon,
                provenance[code]))
        solution_kind = walk.get("solution_kind") or (
            "formula" if walk.get("formula_uses") else "concept")
        lines.append("- **%s%s** `%s`" % (
            _view_label(language, "解题类型", "Solution kind"), colon, solution_kind))
        lines.append("- **%s%s** `%s`" % (
            _view_label(language, "题面资产模式", "Prompt asset mode"), colon,
            walk["prompt_asset_mode"]))
        for asset in walk["prompt_asset_paths"]:
            lines.append("- **%s%s** `%s`" % (
                _view_label(language, "题面图", "Prompt asset"), colon, asset))
        for asset in walk["answer_asset_paths"]:
            lines.append("- **%s%s** `%s`" % (
                _view_label(language, "答案图", "Answer asset"), colon, asset))
        if walk.get("prompt_text"):
            _inline_text(lines, "- **%s%s** " % (
                _view_label(language, "原题", "Original prompt"), colon), walk["prompt_text"])
        source_languages = {
            "zh": {"zh"}, "en": {"en"}, "mixed": {"zh", "en"}, "unknown": set(),
        }[walk["original_language"]]
        needed_translations = _target_languages(language) - source_languages
        for key in ("zh", "en"):
            if key not in needed_translations:
                continue
            if key in walk["translation"]:
                name = ("中文" if language != "en" else "Chinese") if key == "zh" else (
                    "英文" if language == "zh" else "English")
                _inline_text(lines, "- **%s (%s)%s** " % (
                    _view_label(language, "题面翻译", "Prompt translation"), name, colon),
                    walk["translation"][key])
        _localized_lines(lines, "题目问什么", "What is asked", walk["what_asked"], language)
        for kp_id in walk["knowledge_point_ids"]:
            usage = (walk.get("knowledge_point_uses") or {}).get(kp_id)
            if usage:
                _localized_lines(lines, "知识点 %s 如何用" % kp_id,
                                 "How knowledge point %s is used" % kp_id,
                                 usage, language)
        for field, zh_label, en_label in (
            ("known_quantities", "已知量", "Known quantity"),
            ("unknown_quantities", "未知量", "Unknown quantity"),
        ):
            for quantity in walk[field]:
                suffix = ""
                for key in ("symbol", "value", "unit"):
                    if quantity.get(key):
                        suffix += " · %s=%s" % (key, quantity[key])
                localized = dict(quantity["label"])
                if suffix:
                    localized = {key: text + suffix for key, text in localized.items()}
                _localized_lines(lines, zh_label, en_label, localized, language)
        for formula_use in walk["formula_uses"]:
            lines.append("- **%s%s** `%s`" % (
                _view_label(language, "使用公式", "Formula used"), colon,
                formula_use["formula_id"]))
            _localized_lines(lines, "为什么适用", "Why it applies",
                             formula_use["why_applicable"], language)
            for mapping in formula_use["variable_mapping"]:
                _localized_lines(lines, "变量映射 %s" % mapping["symbol"],
                                 "Variable mapping %s" % mapping["symbol"],
                                 mapping["maps_to"], language)
            lines.append("- **%s%s** $%s$" % (
                _view_label(language, "代入", "Substitution"), colon,
                formula_use["substitution"]))
        if not walk["formula_uses"] and walk.get("no_formula_reason"):
            _localized_lines(lines, "为什么不用公式", "Why no formula is needed",
                             walk["no_formula_reason"], language)
        for index, step in enumerate(walk["steps"], 1):
            _localized_lines(lines, "步骤 %d" % index, "Step %d" % index, step, language)
        _localized_lines(lines, "答案", "Answer", walk["answer"], language)
        _localized_lines(lines, "自检", "Self-check", walk["self_check"], language)
        for source in walk["source_trace"]:
            lines.append("- **%s%s** %s" % (
                _view_label(language, "来源追踪", "Source trace"), colon,
                _format_source(source)))
        lines.append("")

    semantic_exclusions = manifest.get("semantic_exclusions") or []
    if semantic_exclusions:
        lines.extend(["### %s" % _view_label(
            language, "非教学语义单元排除", "Excluded semantic units"), ""])
        for exclusion in semantic_exclusions:
            lines.append("- **`%s`**" % exclusion["source_unit_id"])
            _localized_lines(lines, "排除原因", "Reason excluded",
                             exclusion["reason"], language)

    if manifest["profile"] == "abridged":
        lines.extend(["### %s" % _view_label(language, "省略清单", "Omission ledger"), ""])
        for omission in manifest["omissions"]:
            lines.append("- **`%s` · %s**" % (
                omission["item_id"], ", ".join(omission["knowledge_point_ids"])))
            _localized_lines(lines, "省略原因", "Reason omitted", omission["reason"], language)
            for source in omission["source_refs"]:
                lines.append("- **%s%s** %s" % (
                    _view_label(language, "来源", "Source"), colon,
                    _format_source(source)))
    result = "\n".join(lines).rstrip() + "\n"
    if len(result) > MAX_NOTEBOOK_BLOCK_CHARS:
        raise ContentError("generated notebook marker block exceeds %d characters"
                           % MAX_NOTEBOOK_BLOCK_CHARS)
    return result


def _markers(chapter):
    token = "ch%02d" % chapter
    return (
        "<!-- %sBEGIN %s -->" % (MARKER_PREFIX, token),
        "<!-- %sEND %s -->" % (MARKER_PREFIX, token),
        "## [#study-guide-content-%s] Typed study-guide content" % token,
    )


def _merge_notebook(existing, chapter, rendered):
    begin, end, header = _markers(chapter)
    lines = (existing or "").splitlines()
    begin_at = [index for index, line in enumerate(lines) if line == begin]
    end_at = [index for index, line in enumerate(lines) if line == end]
    reserved_headers = [index for index, line in enumerate(lines)
                        if re.match(r"^##\s+\[#study-guide-content-ch%02d\](?:\s|$)" % chapter,
                                    line)]
    block = [begin] + rendered.rstrip("\n").splitlines() + [end]
    if not begin_at and not end_at:
        if reserved_headers or any(MARKER_PREFIX in line for line in lines):
            raise ContentError("notebook contains a reserved or malformed study-guide marker")
        section = [header, ""] + block
        if lines and any(line.strip() for line in lines):
            while lines and not lines[-1].strip():
                lines.pop()
            lines.extend([""] + section)
        else:
            lines = section
        return "\n".join(lines).rstrip() + "\n"
    if len(begin_at) != 1 or len(end_at) != 1 or begin_at[0] >= end_at[0]:
        raise ContentError("notebook has duplicate, unbalanced, or reversed study-guide markers")
    if len(reserved_headers) != 1:
        raise ContentError("notebook marker block must have exactly one reserved entry heading")
    previous_nonblank = begin_at[0] - 1
    while previous_nonblank >= 0 and not lines[previous_nonblank].strip():
        previous_nonblank -= 1
    if previous_nonblank < 0 or lines[previous_nonblank] != header:
        raise ContentError("notebook marker block is detached from its reserved entry heading")
    lines[begin_at[0]:end_at[0] + 1] = block
    return "\n".join(lines).rstrip() + "\n"


def _guard_notebook_targets(ws, chapter):
    directory = os.path.join(ws, "notebook")
    if os.path.lexists(directory):
        if _is_link_or_reparse(directory) or not os.path.isdir(directory):
            raise ContentError("notebook must be a real directory inside the workspace")
    else:
        try:
            os.mkdir(directory)
        except OSError as exc:
            raise ContentError("cannot create notebook directory: %s" % exc)
    md_path = os.path.join(directory, "ch%02d.md" % chapter)
    json_path = os.path.join(directory, "ch%02d.guide.json" % chapter)
    for path in (md_path, json_path):
        if os.path.lexists(path) and (_is_link_or_reparse(path) or not os.path.isfile(path)):
            raise ContentError("output target is not a safe regular file: %s" % path)
    return md_path, json_path


def _read_optional_text(path):
    if not os.path.exists(path):
        return ""
    if _is_link_or_reparse(path) or not os.path.isfile(path):
        raise ContentError("notebook chapter is not a safe regular file: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContentError("cannot read UTF-8 notebook chapter: %s" % exc)
    _check_controls(text, "notebook chapter", allow_reserved_marker=True)
    return text


def _atomic_write_text(path, text, before_publish=None):
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        os.replace(temporary, path)
        if os.name != "nt":
            try:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _publish_manifest(ws, chapter, manifest, report):
    md_path, json_path = _guard_notebook_targets(ws, chapter)
    existing = _read_optional_text(md_path)
    rendered = render_notebook_block(manifest)
    updated_notebook = _merge_notebook(existing, chapter, rendered)
    canonical_json = json.dumps(
        manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    expected_facts = (
        (report.get("claim_verification") or {}).get("fact_integrity")
    )

    def recheck_before_manifest_publish():
        if expected_facts is None:
            return
        try:
            current = validate_workspace_fact_integrity(ws)["snapshot"]
        except (OSError, TypeError, ValueError) as exc:
            raise ContentError(
                "ingestion fact integrity changed before Study Guide import publication: %s"
                % exc
            ) from exc
        if current != expected_facts:
            raise ContentError(
                "ingestion fact inputs changed before Study Guide import publication"
            )
    # Ordering is intentional: a failed notebook update must never publish a manifest that claims
    # durable tutor evidence.  Both individual replacements are atomic and fail loud.
    try:
        _atomic_write_text(md_path, updated_notebook)
        _atomic_write_text(
            json_path,
            canonical_json,
            before_publish=recheck_before_manifest_publish,
        )
    except OSError as exc:
        raise ContentError("cannot atomically publish study-guide content: %s" % exc)
    report.update({
        "imported": True,
        "notebook_path": md_path,
        "manifest_path": json_path,
    })
    return report


def import_manifest(workspace, chapter, input_path):
    """Validate, update the notebook marker first, then atomically publish canonical JSON."""
    ws = _guard_workspace(workspace)
    with _study_guide_mutation_lock(ws):
        manifest, report = load_and_validate_manifest(ws, chapter, input_path)
        report["notebook_anchor_count"] = _validate_walkthrough_notebook_anchors(
            ws, chapter, manifest["walkthroughs"], require_all=True)
        report["invalidated_artifacts"] = _invalidate_chapter_artifacts(ws, chapter)
        return _publish_manifest(ws, chapter, manifest, report)


def _invalidate_chapter_artifacts(ws, chapter):
    guide_dir = os.path.join(ws, "study_guide")
    if not os.path.lexists(guide_dir):
        return []
    if _is_link_or_reparse(guide_dir) or not os.path.isdir(guide_dir):
        raise ContentError("study_guide must be a real directory before relocalization")
    names = [
        "ch%02d.receipt.json" % chapter,
        "ch%02d.pdf" % chapter,
        "ch%02d.html" % chapter,
    ]
    targets = []
    for name in names:
        path = os.path.join(guide_dir, name)
        if not os.path.lexists(path):
            continue
        if _is_link_or_reparse(path) or not os.path.isfile(path):
            raise ContentError("unsafe derived chapter artifact blocks relocalization: %s" % name)
        targets.append(path)
    qa_dir = os.path.join(guide_dir, "qa")
    if os.path.lexists(qa_dir):
        if _is_link_or_reparse(qa_dir) or not os.path.isdir(qa_dir):
            raise ContentError("study_guide/qa must be a real directory before relocalization")
        pattern = re.compile(r"^ch%02d_p\d+\.png$" % chapter)
        for name in sorted(os.listdir(qa_dir)):
            if not pattern.fullmatch(name):
                continue
            path = os.path.join(qa_dir, name)
            if _is_link_or_reparse(path) or not os.path.isfile(path):
                raise ContentError("unsafe QA page blocks relocalization: %s" % name)
            targets.append(path)
    invalidated = []
    for path in targets:
        try:
            os.remove(path)
        except OSError as exc:
            raise ContentError("cannot invalidate stale localized artifact %s: %s"
                               % (os.path.basename(path), exc))
        invalidated.append(os.path.relpath(path, ws).replace("\\", "/"))
    return invalidated


def relocalize_manifest(workspace, chapter, language, output_path=None):
    """Project already-authored locale blocks into a selected language.

    This performs no translation.  In ingestion-v2 a language change alters the
    canonical guide hash and may add new material-claim slots, so the command
    writes a staging draft when ``output_path`` is supplied.  The caller then
    refreshes claims/receipt and imports that exact draft.  v1 keeps the legacy
    one-command publish behavior.
    """

    ws = _guard_workspace(workspace)
    if language not in LANGUAGES:
        raise ContentError("language must be zh/en/bilingual")
    with _study_guide_mutation_lock(ws):
        path = manifest_path(ws, chapter)
        original = _read_json(path, "study-guide content manifest")
        if not isinstance(original, dict) or original.get("language") not in LANGUAGES:
            raise ContentError("existing study-guide manifest has no valid canonical language")
        manifest = copy.deepcopy(original)
        walks = manifest.get("walkthroughs")
        if not isinstance(walks, list):
            raise ContentError("existing study-guide manifest walkthroughs must be an array")
        for index, walk in enumerate(walks):
            if not isinstance(walk, dict):
                raise ContentError("walkthroughs[%d] must be an object" % index)
            original_language = walk.get("original_language")
            if original_language not in ORIGINAL_LANGUAGES:
                raise ContentError("walkthroughs[%d].original_language is invalid" % index)
            translation = walk.get("translation")
            if not isinstance(translation, dict):
                raise ContentError("walkthroughs[%d].translation must be an object" % index)
            source_languages = {
                "zh": {"zh"}, "en": {"en"}, "mixed": {"zh", "en"}, "unknown": set(),
            }[original_language]
            needed = _target_languages(language) - source_languages
            missing = needed - set(translation)
            if missing:
                raise ContentError(
                    "cannot relocalize walkthrough %r to %s; authored prompt translation is missing: %s"
                    % (walk.get("item_id"), language, sorted(missing)))
            # Preserve authored non-source locale blocks so a later language switch is reversible.
            walk["translation"] = dict(translation)
        manifest["language"] = language
        pipeline_version = (
            _ingestion_pipeline_version(ws)
            if os.path.lexists(os.path.join(ws, ".ingest")) else "ingestion-v1"
        )
        if pipeline_version == "ingestion-v2":
            if output_path is None:
                raise ContentError(
                    "ingestion-v2 relocalize requires --output staging JSON; refresh its claims "
                    "and chNN receipt, then run study_guide_content import"
                )
            if os.path.isabs(output_path):
                raise ContentError(
                    "ingestion-v2 relocalize --output must be a workspace-relative notebook draft"
                )
            relative_output = _safe_relative_path(
                output_path, "relocalized staging manifest"
            )
            staging_pattern = r"notebook/ch%02d(?:\.[A-Za-z0-9_-]+)*\.draft\.json" % chapter
            if not re.fullmatch(staging_pattern, relative_output):
                raise ContentError(
                    "ingestion-v2 relocalize --output must match "
                    "notebook/ch%02d.*.draft.json" % chapter
                )
            destination = os.path.join(ws, *relative_output.split("/"))
            _guard_workspace_child(
                ws, destination, "relocalized staging manifest", allow_missing=True
            )
            if destination == os.path.abspath(path):
                raise ContentError("relocalized staging output must not overwrite the canonical manifest")
            report = validate_manifest(
                ws, chapter, manifest, _enforce_v2_claims=False
            )
            parent = os.path.dirname(destination)
            os.makedirs(parent, exist_ok=True)
            _guard_workspace_child(ws, parent, "relocalized staging directory")
            _atomic_write_text(
                destination,
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
            report.update({
                "relocalized_from": original["language"],
                "relocalized_to": language,
                "prepared": True,
                "imported": False,
                "artifact_ready": False,
                "claim_verification": {
                    "required": True,
                    "status": "pending",
                    "verification_scope": "location_only",
                },
                "staging_path": destination,
                "invalidated_artifacts": [],
            })
            return report
        if output_path is not None:
            raise ContentError("--output is only needed for ingestion-v2 relocalization")
        report = validate_manifest(ws, chapter, manifest)
        report["relocalized_from"] = original["language"]
        report["relocalized_to"] = language
        report["invalidated_artifacts"] = _invalidate_chapter_artifacts(ws, chapter)
        return _publish_manifest(ws, chapter, manifest, report)


def _parser():
    parser = argparse.ArgumentParser(
        description="Validate/import typed chapter teaching content for study guides.")
    parser.add_argument("--workspace", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate without writing")
    validate.add_argument("--chapter", required=True, type=int)
    validate.add_argument("--input", default=None,
                          help="draft JSON (default: notebook/chNN.guide.json)")
    validate.add_argument("--json", action="store_true")
    importer = subparsers.add_parser("import", help="validate and publish notebook + JSON")
    importer.add_argument("--chapter", required=True, type=int)
    importer.add_argument("--input", required=True)
    importer.add_argument("--json", action="store_true")
    relocalize = subparsers.add_parser(
        "relocalize", help="reuse already-authored locale blocks after a state language switch")
    relocalize.add_argument("--chapter", required=True, type=int)
    relocalize.add_argument("--language", required=True, choices=sorted(LANGUAGES))
    relocalize.add_argument(
        "--output",
        help="ingestion-v2 staging JSON inside the workspace; verify claims, then import it",
    )
    relocalize.add_argument("--json", action="store_true")
    return parser


def run(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            _manifest, report = load_and_validate_manifest(
                args.workspace, args.chapter, args.input)
            report["command"] = "validate"
        elif args.command == "import":
            report = import_manifest(args.workspace, args.chapter, args.input)
            report["command"] = "import"
        else:
            report = relocalize_manifest(
                args.workspace, args.chapter, args.language, args.output
            )
            report["command"] = "relocalize"
    except ContentError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            sys.stderr.write("study_guide_content: %s\n" % exc)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report.get("prepared") and not report.get("imported"):
            print(
                "[+] chapter %d unsigned staging prepared; claim verification pending "
                "(%s, %d walkthroughs, %d omissions)"
                % (report["chapter"], report["profile"],
                   len(report["walkthrough_item_ids"]), len(report["omitted_item_ids"]))
            )
        else:
            action = "imported" if report.get("imported") else "valid"
            print("[+] chapter %d study-guide content %s (%s, %d walkthroughs, %d omissions)"
                  % (report["chapter"], action, report["profile"],
                     len(report["walkthrough_item_ids"]), len(report["omitted_item_ids"])))
    return 0


if __name__ == "__main__":
    sys.exit(run())
