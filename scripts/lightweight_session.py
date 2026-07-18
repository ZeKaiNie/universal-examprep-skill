#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""On-demand visual-study state machine for the lightweight processing mode.

This module deliberately does *not* parse PDFs, run OCR, build a knowledge base,
call a model, or render a Study Guide.  It records the exact source/page slice the
host should show to its native visual model, validates the resulting page/figure
asset receipt, and binds a taught batch to durable notebook/progress evidence.

The full ingestion route remains a separate, explicit ``processing_mode=full``
choice.  Missing or legacy processing state always fails safe to lightweight.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import exam_start  # noqa: E402
from asset_policy import (  # noqa: E402
    quiz_bank_stat_baseline as _quiz_bank_stat_baseline,
    validate_quiz_bank_stat_baseline as _validate_quiz_bank_stat_baseline,
)
from image_validation import ImageValidationError, png_dimensions  # noqa: E402

try:  # noqa: E402
    from ingestion import (
        ConflictError,
        is_link_or_reparse,
        lightweight_stable_file_sha256,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from ingestion.storage import atomic_write_json
except ImportError:  # pragma: no cover - package import fallback
    from scripts.ingestion import (
        ConflictError,
        is_link_or_reparse,
        lightweight_stable_file_sha256,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from scripts.ingestion.storage import atomic_write_json


SCHEMA_VERSION = 2
STATUS_SCHEMA_VERSION = 2
SESSION_PATH = ".lightweight/session.json"
ASSET_DIRECTORY = ".lightweight/assets"
STATE_PATH = "study_state.json"
MAX_SOURCES = 10000
MAX_BATCHES = 512
MAX_PAGES_PER_BATCH = 8
MAX_ANSWER_DEPENDENCY_PAGES = 4
MAX_ANSWER_DEPENDENCY_SOURCES = 4
CONTACT_SHEET_GROUP_SIZE = 4
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
SUPPORTED_SUFFIXES = frozenset((
    ".pdf", ".pptx", ".docx", ".xlsx", ".png", ".jpg", ".jpeg",
    ".webp", ".gif", ".bmp", ".tif", ".tiff", ".txt", ".md",
))
RASTER_SUFFIXES = frozenset((
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
))
LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES = frozenset((
    ".png", ".jpg", ".jpeg", ".bmp",
))
VISIBLE_ASSET_SUFFIXES = frozenset((".png",))
MAX_JSON_FILE_BYTES = 8 * 1024 * 1024
MAX_VISUAL_ASSET_BYTES = 64 * 1024 * 1024
MAX_FIGURES_PER_PAGE = 32
MAX_MODEL_CALLS = 128
MAX_ASSETS_PER_MODEL_CALL = 8
MAX_DESCRIPTION_CHARS = 1000
PAGE_CONTENT_TYPES = frozenset((
    "text", "formula", "table", "diagram", "image", "question", "answer",
    "figure_question", "other",
))
ANSWER_PROVENANCE_VALUES = frozenset((
    "student_attempt", "official_solution", "none", "unknown",
))
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
SESSION_KEYS = frozenset((
    "schema_version", "session_type", "processing_mode", "workspace",
    "materials", "created_at", "updated_at", "source_inventory", "batches",
    "quiz_bank_baseline", "migration_history",
))
BATCH_KEYS = frozenset((
    "batch_id", "chapter", "source_id", "source_path", "source_revision",
    "pages", "answer_dependencies", "status", "created_at", "updated_at", "visual_receipt",
    "teaching_receipt", "abandonment", "supersedes_batch_id", "supersession",
    "token_strategy",
))
BATCH_OPTIONAL_KEYS = frozenset(("answer_dependency_history",))
MAX_MIGRATION_RECORDS = 8
VISUAL_RECEIPT_SCHEMA_VERSION = 3
MAX_DEPENDENCY_EVENTS = 64
MAX_COMPONENTS_PER_ITEM = 32
TEACHING_ITEM_KINDS = frozenset(("text", "figure", "mixed"))
PROMPT_COMPONENT_ROLES = frozenset((
    "question_text", "figure", "diagram", "table", "given_data",
    "shared_context", "other_prompt",
))
ANSWER_COMPONENT_ROLES = frozenset((
    "answer_text", "worked_solution", "answer_figure", "answer_table",
    "other_answer",
))
VISUAL_PROMPT_COMPONENT_ROLES = frozenset(("figure", "diagram", "table"))


class LightweightSessionError(ValueError):
    """The lightweight session or a requested transition is invalid."""


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _valid_timestamp(value):
    return isinstance(value, str) and bool(UTC_TIMESTAMP_RE.fullmatch(value))


def _current_batches(session, chapter=None):
    """Return only attempts that still declare current learning scope."""
    return [
        batch for batch in session["batches"]
        if batch["status"] not in ("abandoned", "superseded")
        and (chapter is None or batch["chapter"] == chapter)
    ]


def _canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _strict_json(path, label):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LightweightSessionError(
                    "%s contains duplicate JSON key %r" % (label, key)
                )
            result[key] = value
        return result

    def reject_constant(value):
        raise LightweightSessionError(
            "%s contains non-finite JSON value %s" % (label, value)
        )

    try:
        if os.path.getsize(path) > MAX_JSON_FILE_BYTES:
            raise LightweightSessionError(
                "%s exceeds the lightweight JSON size limit" % label
            )
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(
                stream, object_pairs_hook=no_duplicates,
                parse_constant=reject_constant,
            )
    except LightweightSessionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise LightweightSessionError("%s is unreadable strict JSON" % label) from exc


def _resolved(path):
    return str(Path(os.path.abspath(path)).resolve(strict=False))


def _relative_material_path(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LightweightSessionError("source must be a non-empty relative path")
    value = value.replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise LightweightSessionError("source must be relative to materials")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise LightweightSessionError("source contains an unsafe path segment")
    return "/".join(parts)


def _material_path_key(value):
    normalized = _relative_material_path(value).replace("/", os.sep)
    return os.path.normcase(os.path.normpath(normalized))


def _stat_identity(stat_result):
    return [int(stat_result.st_dev), int(stat_result.st_ino)]


def _source_id(relative_path):
    return "lw-src-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:20]


def _visual_render_method(source_path):
    suffix = os.path.splitext(source_path)[1].lower()
    if suffix == ".pdf":
        return "host_native_pdf_page_to_png"
    if suffix == ".png":
        return "original_png"
    if suffix in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES:
        return "host_native_single_raster_to_png"
    raise LightweightSessionError(
        "lightweight mode cannot prove a single page/frame for this raster; switch "
        "to full mode or use an explicit host frame-count path"
    )


def _material_file(materials, relative_path):
    relative_path = _relative_material_path(relative_path)
    target = os.path.abspath(os.path.join(materials, *relative_path.split("/")))
    try:
        inside = os.path.commonpath((os.path.realpath(materials), os.path.realpath(target))) \
            == os.path.realpath(materials)
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(target) or is_link_or_reparse(target):
        raise LightweightSessionError("source is missing, unsafe, or outside materials")
    return target


def _workspace_asset(workspace, value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LightweightSessionError("%s must be a workspace-relative path" % label)
    normalized = value.replace("\\", "/")
    try:
        path = safe_workspace_entry(workspace, normalized)
    except (OSError, TypeError, ValueError) as exc:
        raise LightweightSessionError("%s is outside the workspace" % label) from exc
    if not os.path.isfile(path) or is_link_or_reparse(path):
        raise LightweightSessionError("%s is missing or unsafe" % label)
    return normalized, str(path)


def _workspace_visual_asset_metadata(workspace, value, label):
    normalized, path = _workspace_asset(workspace, value, label)
    if not normalized.startswith(".lightweight/assets/"):
        raise LightweightSessionError(
            "%s must be stored under .lightweight/assets/" % label
        )
    if os.path.splitext(normalized)[1].lower() not in VISIBLE_ASSET_SUFFIXES:
        raise LightweightSessionError(
            "%s must be a canonical PNG image" % label
        )
    try:
        stat_result = os.stat(path, follow_symlinks=False)
        link_count = int(getattr(stat_result, "st_nlink", 1))
    except OSError as exc:
        raise LightweightSessionError("%s is unreadable" % label) from exc
    if link_count != 1:
        raise LightweightSessionError(
            "%s must be an independent file, not a hardlink alias" % label
        )
    size = stat_result.st_size
    if size <= 0 or size > MAX_VISUAL_ASSET_BYTES:
        raise LightweightSessionError(
            "%s must be non-empty and no larger than %d bytes"
            % (label, MAX_VISUAL_ASSET_BYTES)
        )
    return normalized, path, size, stat_result.st_mtime_ns


def _workspace_visual_asset(workspace, value, label):
    normalized, path, _size, _metadata_mtime_ns = _workspace_visual_asset_metadata(
        workspace, value, label
    )
    try:
        payload, snapshot = stable_read_bytes(path)
        if len(payload) > MAX_VISUAL_ASSET_BYTES:
            raise LightweightSessionError(
                "%s exceeds the lightweight visual-asset size limit" % label
            )
        width, height = png_dimensions(payload)
    except (OSError, ImageValidationError) as exc:
        raise LightweightSessionError(
            "%s must be a complete, decodable PNG image" % label
        ) from exc
    if width < 64 or height < 64:
        raise LightweightSessionError(
            "%s is too small to be reliable visual evidence (%dx%d)"
            % (label, width, height)
        )
    return (
        normalized, path, width, height, snapshot["sha256"],
        snapshot["size_bytes"], snapshot["generation"][1],
    )


def _source_inventory(materials):
    rows = []
    for root, dirs, files in os.walk(materials, topdown=True, followlinks=False):
        for directory in list(dirs):
            absolute = os.path.join(root, directory)
            if is_link_or_reparse(absolute):
                dirs.remove(directory)
        for filename in files:
            absolute = os.path.join(root, filename)
            suffix = os.path.splitext(filename)[1].lower()
            if suffix not in SUPPORTED_SUFFIXES:
                continue
            if is_link_or_reparse(absolute) or not os.path.isfile(absolute):
                continue
            relative = os.path.relpath(absolute, materials).replace("\\", "/")
            relative = _relative_material_path(relative)
            stat = os.stat(absolute, follow_symlinks=False)
            rows.append({
                "source_id": _source_id(relative),
                "relative_path": relative,
                "media_type": MEDIA_TYPES[suffix],
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "content_identity": _stat_identity(stat),
                # Startup intentionally does not read source bytes.  The exact hash is
                # computed only when this source enters a requested learning batch.
                "content_sha256": None,
            })
            if len(rows) > MAX_SOURCES:
                raise LightweightSessionError("materials contain too many supported sources")
    rows.sort(key=lambda row: row["relative_path"].casefold())
    return rows


def _parse_pages(value):
    if not isinstance(value, str) or not value.strip():
        raise LightweightSessionError("pages must be a range such as 1-4,7")
    result = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            raise LightweightSessionError("pages contain an empty range")
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise LightweightSessionError("invalid page range %r" % token)
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise LightweightSessionError("page ranges must be positive and ascending")
        result.update(range(start, end + 1))
        if len(result) > MAX_PAGES_PER_BATCH:
            raise LightweightSessionError(
                "one visual batch is limited to %d pages; split it by the current topic"
                % MAX_PAGES_PER_BATCH
            )
    return sorted(result)


def _parse_taught_item_ids(value):
    if not isinstance(value, str) or not value.strip():
        raise LightweightSessionError(
            "taught-item-ids must enumerate the exact visual teaching items"
        )
    values = [item.strip() for item in value.split(",")]
    if (not values or any(not ID_RE.fullmatch(item) for item in values)
            or len(values) != len(set(values))):
        raise LightweightSessionError(
            "taught-item-ids must be unique comma-separated stable IDs"
        )
    return sorted(values)


def _validate_source(row):
    expected = {
        "source_id", "relative_path", "media_type", "size", "mtime_ns",
        "content_identity", "content_sha256",
    }
    if not isinstance(row, dict) or set(row) != expected:
        raise LightweightSessionError("source inventory row fields are invalid")
    relative = _relative_material_path(row.get("relative_path"))
    if row.get("source_id") != _source_id(relative):
        raise LightweightSessionError("source inventory identity is invalid")
    if not isinstance(row.get("media_type"), str) or not row["media_type"]:
        raise LightweightSessionError("source media type is invalid")
    if type(row.get("size")) is not int or row["size"] < 0:
        raise LightweightSessionError("source size is invalid")
    if type(row.get("mtime_ns")) is not int or row["mtime_ns"] < 0:
        raise LightweightSessionError("source mtime is invalid")
    identity = row.get("content_identity")
    if (not isinstance(identity, list) or len(identity) != 2
            or any(type(value) is not int or value < 0 for value in identity)):
        raise LightweightSessionError("source physical identity is invalid")
    digest = row.get("content_sha256")
    if digest is not None and not HEX64.fullmatch(str(digest)):
        raise LightweightSessionError("source content hash is invalid")


def _valid_revision(value):
    return (
        isinstance(value, dict)
        and set(value) == {"sha256", "size", "mtime_ns", "identity"}
        and bool(HEX64.fullmatch(str(value.get("sha256") or "")))
        and type(value.get("size")) is int
        and value["size"] >= 0
        and type(value.get("mtime_ns")) is int
        and value["mtime_ns"] >= 0
        and isinstance(value.get("identity"), list)
        and len(value["identity"]) == 2
        and all(type(part) is int and part >= 0 for part in value["identity"])
    )


def _source_location(source_id, source_path, source_sha256, page):
    return {
        "source_id": source_id,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "page": page,
    }


def _location_key(value):
    return (
        value["source_id"], value["source_path"],
        value["source_sha256"], value["page"],
    )


def _location_sort_key(value):
    return (
        value["source_path"].casefold(), value["source_path"], value["page"],
        value["source_id"], value["source_sha256"],
    )


def _validate_location(value, label="source location"):
    if (not isinstance(value, dict)
            or set(value) != {
                "source_id", "source_path", "source_sha256", "page",
            }):
        raise LightweightSessionError("%s fields are invalid" % label)
    source_path = _relative_material_path(value.get("source_path"))
    if (value.get("source_id") != _source_id(source_path)
            or not HEX64.fullmatch(str(value.get("source_sha256") or ""))
            or type(value.get("page")) is not int
            or value["page"] < 1):
        raise LightweightSessionError("%s identity is invalid" % label)
    return _source_location(
        value["source_id"], source_path, value["source_sha256"], value["page"]
    )


def _canonical_locations(values, label="source locations"):
    if not isinstance(values, list) or not values:
        raise LightweightSessionError("%s must be a non-empty array" % label)
    normalized = [_validate_location(value, label) for value in values]
    keys = [_location_key(value) for value in normalized]
    if len(keys) != len(set(keys)):
        raise LightweightSessionError("%s contain duplicates" % label)
    return sorted(normalized, key=_location_sort_key)


def _validate_crop_bbox(value, label):
    if (not isinstance(value, dict)
            or set(value) != {"x", "y", "width", "height"}):
        raise LightweightSessionError("%s bbox fields are invalid" % label)
    numbers = []
    for key in ("x", "y", "width", "height"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise LightweightSessionError("%s bbox values must be numbers" % label)
        numbers.append(float(number))
    x, y, width, height = numbers
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > 1 or y + height > 1
            or (x == 0 and y == 0 and width == 1 and height == 1)):
        raise LightweightSessionError(
            "%s bbox must be a proper normalized crop inside the parent page" % label
        )
    return {"x": x, "y": y, "width": width, "height": height}


def _dependency_receipt_rows(batch):
    return [
        {
            "source_id": row["source_id"],
            "source_path": row["source_path"],
            "source_revision": dict(row["source_revision"]),
            "pages": list(row["pages"]),
        }
        for row in batch["answer_dependencies"]
    ]


def _dependency_history(batch):
    value = batch.get("answer_dependency_history", [])
    if not isinstance(value, list) or len(value) > MAX_DEPENDENCY_EVENTS:
        raise LightweightSessionError("answer dependency history is invalid")
    return value


def _dependency_event_row(value):
    if value is None:
        return None
    return {
        "source_id": value["source_id"],
        "source_path": value["source_path"],
        "source_revision": dict(value["source_revision"]),
        "pages": list(value["pages"]),
        "registered_at": value["registered_at"],
    }


def _record_dependency_event(batch, event_type, previous, current, reason):
    history = batch.setdefault("answer_dependency_history", [])
    if len(history) >= MAX_DEPENDENCY_EVENTS:
        raise LightweightSessionError("answer dependency history reached its limit")
    now = _utc_now()
    unsigned = {
        "schema_version": 1,
        "event_type": event_type,
        "source_id": (current or previous)["source_id"],
        "source_path": (current or previous)["source_path"],
        "previous": _dependency_event_row(previous),
        "current": _dependency_event_row(current),
        "reason": reason,
        "created_at": now,
    }
    event = dict(unsigned)
    event["receipt_id"] = "lw-dependency-" + hashlib.sha256(
        _canonical_json({"batch_id": batch["batch_id"], **unsigned}).encode("utf-8")
    ).hexdigest()[:24]
    history.append(event)
    return event


def _batch_location_catalog(batch):
    catalog = {}
    for page in batch["pages"]:
        location = _source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], page,
        )
        catalog[_location_key(location)] = {
            "location": location, "role": "primary",
        }
    for dependency in batch["answer_dependencies"]:
        for page in dependency["pages"]:
            location = _source_location(
                dependency["source_id"], dependency["source_path"],
                dependency["source_revision"]["sha256"], page,
            )
            key = _location_key(location)
            if key in catalog:
                raise LightweightSessionError(
                    "answer dependency duplicates a primary learning page"
                )
            catalog[key] = {"location": location, "role": "answer_dependency"}
    return catalog


def _token_strategy(component_scoped=True):
    strategy = {
        "contact_sheet_group_size": CONTACT_SHEET_GROUP_SIZE,
        "contact_sheet_role": "overview_only",
        "staged_model_inputs": "contact_overview_then_selective_detail",
        "model_input_receipt_required": True,
        "source_qualified_locations": True,
        "answer_dependency_page_limit": MAX_ANSWER_DEPENDENCY_PAGES,
        "process_only_requested_pages": True,
        "compress_teaching_output": False,
    }
    strategy[
        "dedicated_teaching_item_component_assets"
        if component_scoped else "dedicated_figure_question_assets"
    ] = True
    return strategy


def _uses_legacy_token_strategy(batch):
    return batch.get("token_strategy") == _token_strategy(component_scoped=False)


def _reject_legacy_active_strategy(batch, action):
    if (batch.get("status") in ("planned", "visual_ready")
            and _uses_legacy_token_strategy(batch)):
        raise LightweightSessionError(
            "legacy figure-only token strategy is quarantined read-only; %s is "
            "forbidden. Auditably abandon batch %s, then plan a new generic "
            "schema-3 attempt"
            % (action, batch.get("batch_id"))
        )


def _reject_any_legacy_active_strategy(session, action):
    for batch in session.get("batches") or []:
        if (batch.get("status") in ("planned", "visual_ready")
                and _uses_legacy_token_strategy(batch)):
            _reject_legacy_active_strategy(batch, action)


def _validate_answer_dependencies(batch, sources_by_id):
    dependencies = batch.get("answer_dependencies")
    if (not isinstance(dependencies, list)
            or len(dependencies) > MAX_ANSWER_DEPENDENCY_SOURCES):
        raise LightweightSessionError("batch answer dependencies are invalid")
    seen_sources = set()
    total_pages = 0
    for row in dependencies:
        if (not isinstance(row, dict)
                or set(row) != {
                    "source_id", "source_path", "source_revision", "pages",
                    "registered_at",
                }):
            raise LightweightSessionError("answer dependency fields are invalid")
        source_id = row.get("source_id")
        source = sources_by_id.get(source_id)
        if source_id in seen_sources or source is None:
            raise LightweightSessionError(
                "answer dependency source is unknown or duplicated"
            )
        seen_sources.add(source_id)
        if (row.get("source_path") != source["relative_path"]
                or not _valid_revision(row.get("source_revision"))
                or not _valid_timestamp(row.get("registered_at"))):
            raise LightweightSessionError("answer dependency identity is invalid")
        suffix = os.path.splitext(row["source_path"])[1].lower()
        if suffix != ".pdf" and suffix not in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES:
            raise LightweightSessionError(
                "answer dependencies support PDF or definitely single-frame "
                "PNG/JPEG/BMP sources only"
            )
        pages = row.get("pages")
        if (not isinstance(pages, list) or not pages
                or pages != sorted(set(pages))
                or any(type(page) is not int or page < 1 for page in pages)):
            raise LightweightSessionError("answer dependency pages are invalid")
        if suffix in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES and pages != [1]:
            raise LightweightSessionError(
                "a standalone answer image has only page-equivalent 1"
            )
        if source_id == batch["source_id"]:
            if row["source_revision"] != batch["source_revision"]:
                raise LightweightSessionError(
                    "same-source answer dependency revision is inconsistent"
                )
            if set(pages).intersection(batch["pages"]):
                raise LightweightSessionError(
                    "answer dependency repeats a primary page; bind that page directly"
                )
        total_pages += len(pages)
    if total_pages > MAX_ANSWER_DEPENDENCY_PAGES:
        raise LightweightSessionError(
            "one batch may register at most %d answer dependency pages"
            % MAX_ANSWER_DEPENDENCY_PAGES
        )


def _validate_dependency_history(batch):
    history = _dependency_history(batch)
    last_by_source = {}
    for index, event in enumerate(history):
        expected = {
            "schema_version", "event_type", "source_id", "source_path",
            "previous", "current", "reason", "created_at", "receipt_id",
        }
        if (not isinstance(event, dict) or set(event) != expected
                or event.get("schema_version") != 1
                or event.get("event_type") not in (
                    "registered", "pages_expanded", "pages_replaced", "removed",
                    "inherited")
                or not ID_RE.fullmatch(str(event.get("source_id") or ""))
                or not isinstance(event.get("source_path"), str)
                or not event["source_path"]
                or not isinstance(event.get("reason"), str)
                or not 5 <= len(event["reason"].strip()) <= 500
                or not _valid_timestamp(event.get("created_at"))
                or not ID_RE.fullmatch(str(event.get("receipt_id") or ""))):
            raise LightweightSessionError(
                "answer dependency history event %d is invalid" % index
            )
        for field in ("previous", "current"):
            row = event.get(field)
            if row is None:
                continue
            if (not isinstance(row, dict) or set(row) != {
                    "source_id", "source_path", "source_revision", "pages",
                    "registered_at",
                    }
                    or row.get("source_id") != event["source_id"]
                    or row.get("source_path") != event["source_path"]
                    or not _valid_revision(row.get("source_revision"))
                    or not isinstance(row.get("pages"), list)
                    or not row["pages"]
                    or row["pages"] != sorted(set(row["pages"]))
                    or any(type(page) is not int or page < 1
                           for page in row["pages"])
                    or not _valid_timestamp(row.get("registered_at"))):
                raise LightweightSessionError(
                    "answer dependency history event %d %s row is invalid"
                    % (index, field)
                )
        if event["previous"] is None and event["event_type"] not in (
                "registered", "inherited"):
            raise LightweightSessionError(
                "answer dependency history event lacks its prior binding"
            )
        if (event["event_type"] in ("registered", "inherited")
                and event["previous"] is not None):
            raise LightweightSessionError(
                "answer dependency registration/inheritance cannot replace a binding"
            )
        if event["current"] is None and event["event_type"] != "removed":
            raise LightweightSessionError(
                "answer dependency history event lacks its current binding"
            )
        if event["event_type"] == "removed" and event["current"] is not None:
            raise LightweightSessionError(
                "answer dependency removal must close its current binding"
            )
        if event["event_type"] == "pages_expanded":
            previous_pages = set(event["previous"]["pages"])
            current_pages = set(event["current"]["pages"])
            if (event["previous"]["source_revision"]
                    != event["current"]["source_revision"]
                    or not previous_pages < current_pages):
                raise LightweightSessionError(
                    "answer dependency expansion must be a strict same-revision superset"
                )
        if (event["event_type"] == "pages_replaced"
                and event["previous"] == event["current"]):
            raise LightweightSessionError(
                "answer dependency replacement history cannot record a no-op"
            )
        source_seen = event["source_id"] in last_by_source
        prior = last_by_source.get(event["source_id"])
        if source_seen and event["previous"] != prior:
            raise LightweightSessionError(
                "answer dependency history chain is discontinuous"
            )
        unsigned = dict(event)
        unsigned.pop("receipt_id")
        expected_id = "lw-dependency-" + hashlib.sha256(
            _canonical_json({"batch_id": batch["batch_id"], **unsigned}).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        if event["receipt_id"] != expected_id:
            raise LightweightSessionError(
                "answer dependency history event digest is invalid"
            )
        last_by_source[event["source_id"]] = event["current"]
    current = {
        row["source_id"]: _dependency_event_row(row)
        for row in batch.get("answer_dependencies") or []
    }
    for source_id, last in last_by_source.items():
        if current.get(source_id) != last:
            raise LightweightSessionError(
                "answer dependency history does not match the current binding"
            )


def _visual_receipt_is_legacy(receipt):
    return isinstance(receipt, dict) and "teaching_scope_protocol" not in receipt


def _visual_receipt_is_schema2(receipt):
    return isinstance(receipt, dict) and receipt.get("schema_version") == 2


def _visual_teaching_item_ids(receipt):
    if _visual_receipt_is_legacy(receipt):
        return []
    if isinstance(receipt, dict) and receipt.get("schema_version") == 3:
        return [
            row.get("teaching_item_id")
            for row in receipt.get("teaching_items") or []
            if isinstance(row, dict)
        ]
    values = []
    for page in receipt.get("pages") or []:
        values.extend(page.get("teaching_item_ids") or [])
    return values


def _validate_answer_provenance(content_types, provenance, label):
    if provenance not in ANSWER_PROVENANCE_VALUES:
        raise LightweightSessionError("%s answer provenance is invalid" % label)
    has_answer = "answer" in content_types
    if (has_answer and provenance == "none") or (
            not has_answer and provenance != "none"):
        raise LightweightSessionError(
            "%s answer provenance contradicts its content types" % label
        )


def _validate_visual_receipt_shape_v2(receipt, batch):
    legacy = _visual_receipt_is_legacy(receipt)
    expected = {
        "schema_version", "receipt_type", "batch_id", "source_sha256",
        "inspection_method", "location_type", "asset_render_method", "pages",
        "answer_dependencies", "dependency_pages", "contact_sheets", "model_calls",
        "created_at", "receipt_id",
    }
    if not legacy:
        expected.add("teaching_scope_protocol")
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise LightweightSessionError("stored visual receipt fields are invalid")
    if batch.get("status") not in (
            "abandoned", "taught", "superseded", "visual_ready"):
        raise LightweightSessionError(
            "schema-2 visual evidence is immutable history; only a quarantined "
            "visual_ready batch may remain active long enough to be abandoned"
        )
    if (receipt.get("schema_version") != 2
            or receipt.get("receipt_type") != "lightweight_visual_batch"
            or receipt.get("batch_id") != batch["batch_id"]
            or receipt.get("source_sha256") != batch["source_revision"]["sha256"]
            or receipt.get("inspection_method") != "model_visual"
            or receipt.get("location_type") != (
                "pdf_page" if batch["source_path"].lower().endswith(".pdf")
                else "raster_page_equivalent"
            )
            or receipt.get("asset_render_method")
            != _visual_render_method(batch["source_path"])
            or receipt.get("answer_dependencies") != _dependency_receipt_rows(batch)
            or (not legacy and receipt.get("teaching_scope_protocol")
                != "explicit_taught_items_and_crop_review")
            or not _valid_timestamp(receipt.get("created_at"))
            or not ID_RE.fullmatch(str(receipt.get("receipt_id") or ""))):
        raise LightweightSessionError("stored visual receipt identity is invalid")

    allowed_locations = _batch_location_catalog(batch)
    primary_keys = {
        key for key, row in allowed_locations.items() if row["role"] == "primary"
    }
    dependency_keys = set(allowed_locations) - primary_keys
    render_ids = set()
    parent_pages = {}
    page_numbers = []
    pages = receipt.get("pages")
    if not isinstance(pages, list) or len(pages) != len(batch["pages"]):
        raise LightweightSessionError("stored visual receipt page coverage is invalid")
    for page in pages:
        expected_page = {
            "page", "render_id", "page_asset", "page_asset_sha256",
            "page_asset_size_bytes", "page_asset_mtime_ns",
            "page_asset_width", "page_asset_height", "visible",
            "content_types", "detail_required", "detail_reason",
            "figure_question_scan", "figure_questions",
        }
        if not legacy:
            expected_page.update({"answer_provenance", "teaching_item_ids"})
        if not isinstance(page, dict) or set(page) != expected_page:
            raise LightweightSessionError("stored visual page fields are invalid")
        number = page.get("page")
        content_types = page.get("content_types")
        figures = page.get("figure_questions")
        teaching_item_ids = page.get("teaching_item_ids") if not legacy else []
        scan = page.get("figure_question_scan")
        content_list = (
            isinstance(content_types, list)
            and all(isinstance(value, str) for value in content_types)
        )
        expected_detail = bool(content_list) and (
            len(batch["pages"]) == 1 or set(content_types) != {"text"}
        )
        expected_detail_reason = (
            "single_page_no_overview" if len(batch["pages"]) == 1
            else "dense_or_nontext_content" if expected_detail
            else "legible_text_only_overview"
        )
        if (type(number) is not int or number not in batch["pages"]
                or not ID_RE.fullmatch(str(page.get("render_id") or ""))
                or page["render_id"] in render_ids
                or not isinstance(page.get("page_asset"), str) or not page["page_asset"]
                or not HEX64.fullmatch(str(page.get("page_asset_sha256") or ""))
                or type(page.get("page_asset_size_bytes")) is not int
                or page["page_asset_size_bytes"] < 1
                or type(page.get("page_asset_mtime_ns")) is not int
                or page["page_asset_mtime_ns"] < 0
                or type(page.get("page_asset_width")) is not int
                or page["page_asset_width"] < 480
                or type(page.get("page_asset_height")) is not int
                or page["page_asset_height"] < 480
                or page.get("visible") is not True
                or not content_list or not content_types
                or len(content_types) != len(set(content_types))
                or any(value not in PAGE_CONTENT_TYPES for value in content_types)
                or page.get("detail_required") is not expected_detail
                or page.get("detail_reason") != expected_detail_reason
                or scan not in ("none", "enumerated")
                or not isinstance(figures, list)
                or len(figures) > MAX_FIGURES_PER_PAGE
                or (not legacy and (
                    not isinstance(teaching_item_ids, list)
                    or len(teaching_item_ids) != len(set(teaching_item_ids))
                    or any(not ID_RE.fullmatch(str(value or ""))
                           for value in teaching_item_ids)))):
            raise LightweightSessionError("stored visual page content is invalid")
        if not legacy:
            _validate_answer_provenance(
                content_types, page.get("answer_provenance"),
                "stored primary page",
            )
        if ((scan == "none" and (figures or "figure_question" in content_types))
                or (scan == "enumerated"
                    and (not figures or "figure_question" not in content_types))):
            raise LightweightSessionError("stored figure-question scan is inconsistent")
        render_ids.add(page["render_id"])
        location = _source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], number,
        )
        parent_pages[_location_key(location)] = {
            "location": location,
            "render_id": page["render_id"],
            "page_asset": page["page_asset"],
            "page_asset_sha256": page["page_asset_sha256"],
            "answer_provenance": (
                page.get("answer_provenance") if not legacy else "unknown"
            ),
        }
        page_numbers.append(number)
    if page_numbers != batch["pages"] or len(set(page_numbers)) != len(page_numbers):
        raise LightweightSessionError("stored visual receipt page coverage is invalid")

    dependency_pages = receipt.get("dependency_pages")
    if not isinstance(dependency_pages, list):
        raise LightweightSessionError("stored answer dependency pages are invalid")
    seen_dependency_keys = set()
    for page in dependency_pages:
        expected_dependency_page = {
            "source_id", "source_path", "source_sha256", "page", "render_id",
            "page_asset", "page_asset_sha256", "page_asset_size_bytes",
            "page_asset_mtime_ns", "page_asset_width", "page_asset_height",
            "visible", "purpose", "asset_render_method",
        }
        if not legacy:
            expected_dependency_page.update({"content_types", "answer_provenance"})
        if not isinstance(page, dict) or set(page) != expected_dependency_page:
            raise LightweightSessionError("stored answer dependency page fields are invalid")
        location = _validate_location({
            key: page.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, "answer dependency page location")
        key = _location_key(location)
        dependency_content_types = page.get("content_types") if not legacy else ["answer"]
        if (key not in dependency_keys or key in seen_dependency_keys
                or not ID_RE.fullmatch(str(page.get("render_id") or ""))
                or page["render_id"] in render_ids
                or not isinstance(page.get("page_asset"), str) or not page["page_asset"]
                or not HEX64.fullmatch(str(page.get("page_asset_sha256") or ""))
                or type(page.get("page_asset_size_bytes")) is not int
                or page["page_asset_size_bytes"] < 1
                or type(page.get("page_asset_mtime_ns")) is not int
                or page["page_asset_mtime_ns"] < 0
                or type(page.get("page_asset_width")) is not int
                or page["page_asset_width"] < 480
                or type(page.get("page_asset_height")) is not int
                or page["page_asset_height"] < 480
                or page.get("visible") is not True
                or page.get("purpose") != "answer_locator_only"
                or page.get("asset_render_method")
                != _visual_render_method(page["source_path"])
                or (not legacy and (
                    not isinstance(dependency_content_types, list)
                    or not dependency_content_types
                    or len(dependency_content_types)
                    != len(set(dependency_content_types))
                    or any(value not in PAGE_CONTENT_TYPES
                           for value in dependency_content_types)))):
            raise LightweightSessionError("stored answer dependency page is invalid")
        if not legacy:
            _validate_answer_provenance(
                dependency_content_types, page.get("answer_provenance"),
                "stored answer dependency page",
            )
        seen_dependency_keys.add(key)
        render_ids.add(page["render_id"])
        parent_pages[key] = {
            "location": location,
            "render_id": page["render_id"],
            "page_asset": page["page_asset"],
            "page_asset_sha256": page["page_asset_sha256"],
            "answer_provenance": (
                page.get("answer_provenance") if not legacy else "unknown"
            ),
        }
    if seen_dependency_keys != dependency_keys:
        raise LightweightSessionError(
            "stored answer dependency page coverage is not exact"
        )

    contacts = receipt.get("contact_sheets")
    if not isinstance(contacts, list):
        raise LightweightSessionError("stored visual receipt contact sheets are invalid")
    for contact in contacts:
        if (not isinstance(contact, dict)
                or set(contact) != {
                    "asset", "asset_sha256", "asset_size_bytes", "asset_mtime_ns",
                    "asset_width", "asset_height", "pages", "purpose",
                }
                or not isinstance(contact.get("asset"), str) or not contact["asset"]
                or not HEX64.fullmatch(str(contact.get("asset_sha256") or ""))
                or type(contact.get("asset_size_bytes")) is not int
                or contact["asset_size_bytes"] < 1
                or type(contact.get("asset_mtime_ns")) is not int
                or contact["asset_mtime_ns"] < 0
                or type(contact.get("asset_width")) is not int
                or type(contact.get("asset_height")) is not int
                or contact.get("purpose") != "overview_only"
                or not isinstance(contact.get("pages"), list) or not contact["pages"]
                or len(contact["pages"]) > CONTACT_SHEET_GROUP_SIZE
                or len(contact["pages"]) != len(set(contact["pages"]))
                or any(type(number) is not int or number not in batch["pages"]
                       for number in contact["pages"])):
            raise LightweightSessionError("stored contact sheet binding is invalid")
        minimum_width, minimum_height = _contact_min_dimensions(len(contact["pages"]))
        if (contact["asset_width"] < minimum_width
                or contact["asset_height"] < minimum_height):
            raise LightweightSessionError(
                "stored contact sheet is too small for legible overview tiles"
            )
    if len(batch["pages"]) == 1 and contacts:
        raise LightweightSessionError(
            "single-page batches must use the page image directly, without a contact sheet"
        )
    if len(batch["pages"]) > 1:
        flattened = [number for contact in contacts for number in contact["pages"]]
        if sorted(flattened) != batch["pages"] or len(flattened) != len(set(flattened)):
            raise LightweightSessionError(
                "stored contact sheets must partition every primary page exactly once"
            )

    def validate_crop(binding, declaration, label):
        expected_crop = {
            "source_id", "source_path", "source_sha256", "page",
            "parent_render_id", "parent_page_asset", "parent_page_asset_sha256",
            "crop_bbox_normalized", "crop_declaration", "asset", "asset_sha256",
            "asset_size_bytes", "asset_mtime_ns", "asset_width", "asset_height",
        }
        if not isinstance(binding, dict) or set(binding) != expected_crop:
            raise LightweightSessionError("stored %s fields are invalid" % label)
        location = _validate_location({
            key: binding.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, "%s location" % label)
        key = _location_key(location)
        parent = parent_pages.get(key)
        if (parent is None
                or binding.get("parent_render_id") != parent["render_id"]
                or binding.get("parent_page_asset") != parent["page_asset"]
                or binding.get("parent_page_asset_sha256")
                != parent["page_asset_sha256"]
                or binding.get("crop_declaration") != declaration
                or not isinstance(binding.get("asset"), str) or not binding["asset"]
                or not HEX64.fullmatch(str(binding.get("asset_sha256") or ""))
                or type(binding.get("asset_size_bytes")) is not int
                or binding["asset_size_bytes"] < 1
                or type(binding.get("asset_mtime_ns")) is not int
                or binding["asset_mtime_ns"] < 0
                or type(binding.get("asset_width")) is not int
                or binding["asset_width"] < 64
                or type(binding.get("asset_height")) is not int
                or binding["asset_height"] < 64):
            raise LightweightSessionError("stored %s binding is invalid" % label)
        _validate_crop_bbox(binding.get("crop_bbox_normalized"), label)
        return location

    question_ids = set()
    all_teaching_item_ids = set()
    prompt_assets = set()
    answer_assets = set()
    answer_location_keys = set()
    all_asset_bindings = []
    for contact in contacts:
        all_asset_bindings.append((contact["asset"], contact["asset_sha256"]))
    for page in pages:
        page_teaching_ids = set(page.get("teaching_item_ids") or [])
        if not legacy:
            if all_teaching_item_ids.intersection(page_teaching_ids):
                raise LightweightSessionError(
                    "stored teaching item IDs must be unique across the batch"
                )
            all_teaching_item_ids.update(page_teaching_ids)
        all_asset_bindings.append((page["page_asset"], page["page_asset_sha256"]))
    for page in dependency_pages:
        all_asset_bindings.append((page["page_asset"], page["page_asset_sha256"]))
    if not legacy and not all_teaching_item_ids:
        raise LightweightSessionError(
            "stored visual batch must enumerate at least one teaching item"
        )
    for page in pages:
        page_teaching_ids = set(page.get("teaching_item_ids") or [])
        page_location_key = _location_key(_source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], page["page"],
        ))
        for figure in page["figure_questions"]:
            if (not isinstance(figure, dict)
                    or set(figure) != {
                        "question_id", "prompt_binding", "answer_binding",
                        "answer_display_phase", "description",
                    }
                    or not ID_RE.fullmatch(str(figure.get("question_id") or ""))
                    or figure["question_id"] in question_ids
                    or figure.get("answer_display_phase") != "solution_or_review_only"
                    or not isinstance(figure.get("description"), str)
                    or not figure["description"].strip()
                    or len(figure["description"]) > MAX_DESCRIPTION_CHARS):
                raise LightweightSessionError("stored figure-question fields are invalid")
            question_ids.add(figure["question_id"])
            if not legacy and figure["question_id"] not in page_teaching_ids:
                raise LightweightSessionError(
                    "every figure question must be an enumerated teaching item"
                )
            prompt_location = validate_crop(
                figure.get("prompt_binding"), "target_question_only", "prompt crop"
            )
            if _location_key(prompt_location) != page_location_key:
                raise LightweightSessionError(
                    "stored prompt crop does not belong to its containing primary page"
                )
            prompt = figure["prompt_binding"]
            prompt_assets.add(prompt["asset"])
            all_asset_bindings.append((prompt["asset"], prompt["asset_sha256"]))
            answer = figure.get("answer_binding")
            if answer is not None:
                answer_location = validate_crop(
                    answer, "target_answer_only", "answer crop"
                )
                answer_parent = parent_pages.get(_location_key(answer_location))
                if (not legacy and (answer_parent is None
                        or answer_parent["answer_provenance"]
                        != "official_solution")):
                    raise LightweightSessionError(
                        "answer crops may bind only pages classified official_solution"
                    )
                answer_location_keys.add(_location_key(answer_location))
                answer_assets.add(answer["asset"])
                all_asset_bindings.append((answer["asset"], answer["asset_sha256"]))
    official_dependency_keys = dependency_keys if legacy else {
        key for key in dependency_keys
        if parent_pages[key]["answer_provenance"] == "official_solution"
    }
    if not official_dependency_keys.issubset(answer_location_keys):
        raise LightweightSessionError(
            "every official-solution dependency page must supply an answer crop"
        )
    if answer_location_keys.intersection(dependency_keys - official_dependency_keys):
        raise LightweightSessionError(
            "unknown/student-attempt dependency pages cannot supply official answers"
        )
    if len({path for path, _digest in all_asset_bindings}) != len(all_asset_bindings):
        raise LightweightSessionError(
            "stored visual roles must use distinct files; identical bytes at distinct "
            "source-qualified locations remain legal"
        )

    asset_catalog = {}
    detail_required_locations = set(dependency_keys)
    for contact in contacts:
        locations = [
            _source_location(
                batch["source_id"], batch["source_path"],
                batch["source_revision"]["sha256"], number,
            )
            for number in contact["pages"]
        ]
        asset_catalog[contact["asset"]] = {
            "role": "contact", "sha256": contact["asset_sha256"],
            "locations": {_location_key(value) for value in locations},
        }
    for page in pages:
        location = parent_pages[next(
            key for key in primary_keys if key[3] == page["page"]
        )]["location"]
        key = _location_key(location)
        asset_catalog[page["page_asset"]] = {
            "role": "page", "sha256": page["page_asset_sha256"],
            "locations": {key},
        }
        if page["detail_required"]:
            detail_required_locations.add(key)
        for figure in page["figure_questions"]:
            prompt = figure["prompt_binding"]
            asset_catalog[prompt["asset"]] = {
                "role": "prompt", "sha256": prompt["asset_sha256"],
                "locations": {_location_key(prompt)},
                "target_item_id": figure["question_id"], "side": "prompt",
            }
            answer = figure["answer_binding"]
            if answer is not None:
                asset_catalog[answer["asset"]] = {
                    "role": "answer", "sha256": answer["asset_sha256"],
                    "locations": {_location_key(answer)},
                    "target_item_id": figure["question_id"], "side": "answer",
                }
    for page in dependency_pages:
        key = _location_key(page)
        asset_catalog[page["page_asset"]] = {
            "role": "dependency_page", "sha256": page["page_asset_sha256"],
            "locations": {key},
        }

    calls = receipt.get("model_calls")
    if not isinstance(calls, list) or not calls or len(calls) > MAX_MODEL_CALLS:
        raise LightweightSessionError("stored visual receipt must bind host model calls")
    call_ids = set()
    used_stage_assets = set()
    reviewed_crop_assets = set()
    overview_assets = set()
    detail_assets = set()
    solution_assets = set()
    crop_review_assets = set()
    covered_locations = set()
    for call in calls:
        stage = call.get("stage") if isinstance(call, dict) else None
        expected_call_fields = {
            "call_id", "stage", "host", "model", "inputs", "locations",
        }
        if stage == "crop_review":
            expected_call_fields.add("crop_review")
        if (not isinstance(call, dict)
                or set(call) != expected_call_fields
                or not ID_RE.fullmatch(str(call.get("call_id") or ""))
                or call["call_id"] in call_ids
                or stage not in ("overview", "detail", "solution", "crop_review")
                or (legacy and stage == "crop_review")
                or not isinstance(call.get("host"), str) or not call["host"].strip()
                or "\n" in call["host"] or "\r" in call["host"]
                or len(call["host"]) > 256
                or not isinstance(call.get("model"), str) or not call["model"].strip()
                or "\n" in call["model"] or "\r" in call["model"]
                or len(call["model"]) > 256
                or not isinstance(call.get("inputs"), list) or not call["inputs"]
                or len(call["inputs"]) > MAX_ASSETS_PER_MODEL_CALL):
            raise LightweightSessionError("stored model-call receipt is invalid")
        call_locations = _canonical_locations(
            call.get("locations"), "stored model-call locations"
        )
        if call["locations"] != call_locations:
            raise LightweightSessionError(
                "stored model-call locations are not in canonical order"
            )
        input_paths = set()
        for binding in call["inputs"]:
            if (not isinstance(binding, dict)
                    or set(binding) != {"asset", "asset_sha256", "locations"}
                    or not isinstance(binding.get("asset"), str) or not binding["asset"]
                    or binding["asset"] in input_paths
                    or binding["asset"] not in asset_catalog
                    or (stage == "crop_review"
                        and binding["asset"] in reviewed_crop_assets)
                    or (stage != "crop_review"
                        and binding["asset"] in used_stage_assets)
                    or binding.get("asset_sha256")
                    != asset_catalog[binding["asset"]]["sha256"]):
                raise LightweightSessionError("stored model-call input binding is invalid")
            input_locations = _canonical_locations(
                binding.get("locations"), "stored model-call input locations"
            )
            if (binding["locations"] != input_locations
                    or {_location_key(value) for value in input_locations}
                    != asset_catalog[binding["asset"]]["locations"]):
                raise LightweightSessionError(
                    "stored model-call input source binding is invalid"
                )
            input_paths.add(binding["asset"])
            if stage == "crop_review":
                reviewed_crop_assets.add(binding["asset"])
            else:
                used_stage_assets.add(binding["asset"])
        inferred_locations = set().union(*(
            asset_catalog[value]["locations"] for value in input_paths
        ))
        call_location_keys = {_location_key(value) for value in call_locations}
        if call_location_keys != inferred_locations:
            raise LightweightSessionError(
                "stored model-call locations do not match its exact assets"
            )
        roles = {asset_catalog[value]["role"] for value in input_paths}
        if stage == "overview":
            if roles != {"contact"} or len(input_paths) != 1:
                raise LightweightSessionError("stored overview call is invalid")
            overview_assets.update(input_paths)
        elif stage == "detail":
            if not roles.issubset({"page", "dependency_page", "prompt"}):
                raise LightweightSessionError("stored detail call is invalid")
            detail_assets.update(input_paths)
        elif stage == "solution":
            if roles != {"answer"}:
                raise LightweightSessionError("stored solution call is invalid")
            solution_assets.update(input_paths)
        else:
            if len(input_paths) != 1 or not roles.issubset({"prompt", "answer"}):
                raise LightweightSessionError("stored crop-review call is invalid")
            asset = next(iter(input_paths))
            binding = asset_catalog[asset]
            review = call.get("crop_review")
            if (not isinstance(review, dict)
                    or set(review) != {
                        "schema_version", "target_item_id", "side", "crop_sha256",
                        "verdict", "detected_item_ids", "reviewer_kind", "reviewer",
                        "reviewed_at", "invocation_id",
                        "unrelated_content_present", "student_attempt_present",
                    }
                    or review.get("schema_version") != 1
                    or review.get("target_item_id") != binding.get("target_item_id")
                    or review.get("side") != binding.get("side")
                    or review.get("crop_sha256") != binding["sha256"]
                    or review.get("verdict") != "target_item_only"
                    or review.get("detected_item_ids")
                    != [binding.get("target_item_id")]
                    or review.get("reviewer_kind") != "model_vision"
                    or review.get("reviewer") != call.get("model")
                    or not _valid_timestamp(review.get("reviewed_at"))
                    or review.get("invocation_id") != call.get("call_id")
                    or review.get("unrelated_content_present") is not False
                    or review.get("student_attempt_present") is not False):
                raise LightweightSessionError(
                    "stored crop review does not prove target-only semantic purity"
                )
            crop_review_assets.add(asset)
        call_ids.add(call["call_id"])
        covered_locations.update(call_location_keys)

    contact_assets = {
        value for value, binding in asset_catalog.items()
        if binding["role"] == "contact"
    }
    detailed_page_locations = set().union(*(
        binding["locations"] for value, binding in asset_catalog.items()
        if value in detail_assets and binding["role"] in {"page", "dependency_page"}
    )) if any(
        value in detail_assets and binding["role"] in {"page", "dependency_page"}
        for value, binding in asset_catalog.items()
    ) else set()
    if (overview_assets != contact_assets
            or not set(allowed_locations).issubset(covered_locations)
            or detail_required_locations != detailed_page_locations
            or not prompt_assets.issubset(detail_assets)
            or solution_assets != answer_assets
            or (not legacy
                and crop_review_assets != (prompt_assets | answer_assets))
            or (legacy and crop_review_assets)):
        raise LightweightSessionError("stored staged model-input coverage is invalid")
    unsigned = dict(receipt)
    unsigned.pop("receipt_id")
    expected_id = "lw-visual-" + hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    if receipt["receipt_id"] != expected_id:
        raise LightweightSessionError("stored visual receipt digest is invalid")


def _validate_visual_receipt_shape_v3(receipt, batch):
    expected = {
        "schema_version", "receipt_type", "batch_id", "source_sha256",
        "inspection_method", "teaching_scope_protocol", "location_type",
        "asset_render_method", "pages", "teaching_items",
        "answer_dependencies", "dependency_pages", "contact_sheets",
        "model_calls", "created_at", "receipt_id",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise LightweightSessionError("stored schema-3 visual receipt fields are invalid")
    if (receipt.get("schema_version") != VISUAL_RECEIPT_SCHEMA_VERSION
            or receipt.get("receipt_type") != "lightweight_visual_batch"
            or receipt.get("batch_id") != batch["batch_id"]
            or receipt.get("source_sha256") != batch["source_revision"]["sha256"]
            or receipt.get("inspection_method") != "model_visual"
            or receipt.get("teaching_scope_protocol")
            != "generic_teaching_item_components_v3"
            or receipt.get("location_type") != (
                "pdf_page" if batch["source_path"].lower().endswith(".pdf")
                else "raster_page_equivalent")
            or receipt.get("asset_render_method")
            != _visual_render_method(batch["source_path"])
            or receipt.get("answer_dependencies") != _dependency_receipt_rows(batch)
            or batch.get("token_strategy")
            != _token_strategy(component_scoped=True)
            or not _valid_timestamp(receipt.get("created_at"))
            or not ID_RE.fullmatch(str(receipt.get("receipt_id") or ""))):
        raise LightweightSessionError("stored schema-3 visual receipt identity is invalid")

    allowed_locations = _batch_location_catalog(batch)
    primary_keys = {
        key for key, row in allowed_locations.items() if row["role"] == "primary"
    }
    dependency_keys = set(allowed_locations) - primary_keys
    parent_pages = {}
    render_ids = set()
    all_assets = []
    all_teaching_ids = set()
    declared_prompt_pairs = set()
    pages = receipt.get("pages")
    if not isinstance(pages, list) or len(pages) != len(batch["pages"]):
        raise LightweightSessionError("stored schema-3 primary page coverage is invalid")
    page_numbers = []
    detail_required_locations = set(dependency_keys)
    for page in pages:
        fields = {
            "page", "render_id", "page_asset", "page_asset_sha256",
            "page_asset_size_bytes", "page_asset_mtime_ns", "page_asset_width",
            "page_asset_height", "visible", "content_types", "answer_provenance",
            "teaching_item_ids", "detail_required", "detail_reason",
        }
        number = page.get("page") if isinstance(page, dict) else None
        content_types = page.get("content_types") if isinstance(page, dict) else None
        teaching_ids = page.get("teaching_item_ids") if isinstance(page, dict) else None
        expected_detail = bool(content_types) and (
            len(batch["pages"]) == 1 or set(content_types) != {"text"}
        ) if isinstance(content_types, list) else False
        expected_reason = (
            "single_page_no_overview" if len(batch["pages"]) == 1
            else "dense_or_nontext_content" if expected_detail
            else "legible_text_only_overview"
        )
        if (not isinstance(page, dict) or set(page) != fields
                or type(number) is not int or number not in batch["pages"]
                or number in page_numbers
                or not ID_RE.fullmatch(str(page.get("render_id") or ""))
                or page["render_id"] in render_ids
                or not isinstance(page.get("page_asset"), str) or not page["page_asset"]
                or not HEX64.fullmatch(str(page.get("page_asset_sha256") or ""))
                or type(page.get("page_asset_size_bytes")) is not int
                or page["page_asset_size_bytes"] < 1
                or type(page.get("page_asset_mtime_ns")) is not int
                or page["page_asset_mtime_ns"] < 0
                or type(page.get("page_asset_width")) is not int
                or page["page_asset_width"] < 480
                or type(page.get("page_asset_height")) is not int
                or page["page_asset_height"] < 480
                or page.get("visible") is not True
                or not isinstance(content_types, list) or not content_types
                or len(content_types) != len(set(content_types))
                or any(value not in PAGE_CONTENT_TYPES for value in content_types)
                or not isinstance(teaching_ids, list)
                or len(teaching_ids) != len(set(teaching_ids))
                or any(not ID_RE.fullmatch(str(value or "")) for value in teaching_ids)
                or page.get("detail_required") is not expected_detail
                or page.get("detail_reason") != expected_reason):
            raise LightweightSessionError("stored schema-3 primary page is invalid")
        _validate_answer_provenance(
            content_types, page.get("answer_provenance"), "stored primary page"
        )
        location = _source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], number,
        )
        key = _location_key(location)
        parent_pages[key] = {
            "location": location, "render_id": page["render_id"],
            "page_asset": page["page_asset"],
            "page_asset_sha256": page["page_asset_sha256"],
            "answer_provenance": page["answer_provenance"],
            "teaching_item_ids": set(teaching_ids),
        }
        if expected_detail:
            detail_required_locations.add(key)
        page_numbers.append(number)
        render_ids.add(page["render_id"])
        all_teaching_ids.update(teaching_ids)
        declared_prompt_pairs.update((key, value) for value in teaching_ids)
        all_assets.append((page["page_asset"], page["page_asset_sha256"]))
    if page_numbers != batch["pages"] or not all_teaching_ids:
        raise LightweightSessionError("stored schema-3 primary pages/items are incomplete")

    dependency_pages = receipt.get("dependency_pages")
    if not isinstance(dependency_pages, list):
        raise LightweightSessionError("stored schema-3 dependency pages are invalid")
    seen_dependency_keys = set()
    for page in dependency_pages:
        fields = {
            "source_id", "source_path", "source_sha256", "page", "render_id",
            "page_asset", "page_asset_sha256", "page_asset_size_bytes",
            "page_asset_mtime_ns", "page_asset_width", "page_asset_height",
            "visible", "purpose", "asset_render_method", "content_types",
            "answer_provenance",
        }
        if not isinstance(page, dict) or set(page) != fields:
            raise LightweightSessionError("stored schema-3 dependency page fields are invalid")
        location = _validate_location({
            key: page.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, "stored answer dependency page")
        key = _location_key(location)
        content_types = page.get("content_types")
        if (key not in dependency_keys or key in seen_dependency_keys
                or not ID_RE.fullmatch(str(page.get("render_id") or ""))
                or page["render_id"] in render_ids
                or not isinstance(page.get("page_asset"), str) or not page["page_asset"]
                or not HEX64.fullmatch(str(page.get("page_asset_sha256") or ""))
                or type(page.get("page_asset_size_bytes")) is not int
                or page["page_asset_size_bytes"] < 1
                or type(page.get("page_asset_mtime_ns")) is not int
                or page["page_asset_mtime_ns"] < 0
                or type(page.get("page_asset_width")) is not int
                or page["page_asset_width"] < 480
                or type(page.get("page_asset_height")) is not int
                or page["page_asset_height"] < 480
                or page.get("visible") is not True
                or page.get("purpose") != "answer_locator_only"
                or page.get("asset_render_method")
                != _visual_render_method(page["source_path"])
                or not isinstance(content_types, list) or not content_types
                or len(content_types) != len(set(content_types))
                or any(value not in PAGE_CONTENT_TYPES for value in content_types)):
            raise LightweightSessionError("stored schema-3 dependency page is invalid")
        _validate_answer_provenance(
            content_types, page.get("answer_provenance"),
            "stored answer dependency page",
        )
        parent_pages[key] = {
            "location": location, "render_id": page["render_id"],
            "page_asset": page["page_asset"],
            "page_asset_sha256": page["page_asset_sha256"],
            "answer_provenance": page["answer_provenance"],
        }
        seen_dependency_keys.add(key)
        render_ids.add(page["render_id"])
        all_assets.append((page["page_asset"], page["page_asset_sha256"]))
    if seen_dependency_keys != dependency_keys:
        raise LightweightSessionError("stored schema-3 dependency coverage is not exact")

    contacts = receipt.get("contact_sheets")
    if not isinstance(contacts, list):
        raise LightweightSessionError("stored schema-3 contact sheets are invalid")
    flattened_contacts = []
    for contact in contacts:
        fields = {
            "asset", "asset_sha256", "asset_size_bytes", "asset_mtime_ns",
            "asset_width", "asset_height", "pages", "purpose",
        }
        if (not isinstance(contact, dict) or set(contact) != fields
                or not isinstance(contact.get("asset"), str) or not contact["asset"]
                or not HEX64.fullmatch(str(contact.get("asset_sha256") or ""))
                or type(contact.get("asset_size_bytes")) is not int
                or contact["asset_size_bytes"] < 1
                or type(contact.get("asset_mtime_ns")) is not int
                or contact["asset_mtime_ns"] < 0
                or type(contact.get("asset_width")) is not int
                or type(contact.get("asset_height")) is not int
                or contact.get("purpose") != "overview_only"
                or not isinstance(contact.get("pages"), list)
                or not contact["pages"] or len(contact["pages"]) > CONTACT_SHEET_GROUP_SIZE
                or len(contact["pages"]) != len(set(contact["pages"]))
                or any(type(page) is not int or page not in batch["pages"]
                       for page in contact["pages"])):
            raise LightweightSessionError("stored schema-3 contact sheet is invalid")
        minimum_width, minimum_height = _contact_min_dimensions(len(contact["pages"]))
        if (contact["asset_width"] < minimum_width
                or contact["asset_height"] < minimum_height):
            raise LightweightSessionError("stored schema-3 contact sheet is too small")
        flattened_contacts.extend(contact["pages"])
        all_assets.append((contact["asset"], contact["asset_sha256"]))
    if ((len(batch["pages"]) == 1 and contacts)
            or (len(batch["pages"]) > 1 and (
                sorted(flattened_contacts) != batch["pages"]
                or len(flattened_contacts) != len(set(flattened_contacts))))):
        raise LightweightSessionError("stored schema-3 contact coverage is invalid")

    def validate_crop(binding, declaration, label):
        fields = {
            "source_id", "source_path", "source_sha256", "page",
            "parent_render_id", "parent_page_asset", "parent_page_asset_sha256",
            "crop_bbox_normalized", "crop_declaration", "asset", "asset_sha256",
            "asset_size_bytes", "asset_mtime_ns", "asset_width", "asset_height",
        }
        if not isinstance(binding, dict) or set(binding) != fields:
            raise LightweightSessionError("stored %s fields are invalid" % label)
        location = _validate_location({
            key: binding.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, label)
        key = _location_key(location)
        parent = parent_pages.get(key)
        if (parent is None
                or binding.get("parent_render_id") != parent["render_id"]
                or binding.get("parent_page_asset") != parent["page_asset"]
                or binding.get("parent_page_asset_sha256")
                != parent["page_asset_sha256"]
                or binding.get("crop_declaration") != declaration
                or not isinstance(binding.get("asset"), str) or not binding["asset"]
                or not HEX64.fullmatch(str(binding.get("asset_sha256") or ""))
                or type(binding.get("asset_size_bytes")) is not int
                or binding["asset_size_bytes"] < 1
                or type(binding.get("asset_mtime_ns")) is not int
                or binding["asset_mtime_ns"] < 0
                or type(binding.get("asset_width")) is not int
                or binding["asset_width"] < 64
                or type(binding.get("asset_height")) is not int
                or binding["asset_height"] < 64):
            raise LightweightSessionError("stored %s binding is invalid" % label)
        _validate_crop_bbox(binding.get("crop_bbox_normalized"), label)
        return key

    teaching_items = receipt.get("teaching_items")
    if (not isinstance(teaching_items, list)
            or len(teaching_items) != len(all_teaching_ids)):
        raise LightweightSessionError("stored schema-3 teaching items are invalid")
    item_ids = set()
    component_ids = set()
    asset_catalog = {}
    prompt_assets = set()
    answer_assets = set()
    answer_location_keys = set()
    prompt_component_pairs = set()
    for item in teaching_items:
        if (not isinstance(item, dict) or set(item) != {
                "teaching_item_id", "kind", "description", "prompt_components",
                "answer_components", "answer_display_phase",
                }):
            raise LightweightSessionError("stored schema-3 teaching item fields are invalid")
        item_id = item.get("teaching_item_id")
        kind = item.get("kind")
        if (not ID_RE.fullmatch(str(item_id or "")) or item_id in item_ids
                or item_id not in all_teaching_ids or kind not in TEACHING_ITEM_KINDS
                or not isinstance(item.get("description"), str)
                or not item["description"].strip()
                or len(item["description"]) > MAX_DESCRIPTION_CHARS
                or item.get("answer_display_phase") != "solution_or_review_only"):
            raise LightweightSessionError("stored schema-3 teaching item is invalid")
        item_ids.add(item_id)
        prompt_roles = set()
        target_visible = False
        for side, field, roles_allowed in (
                ("prompt", "prompt_components", PROMPT_COMPONENT_ROLES),
                ("answer", "answer_components", ANSWER_COMPONENT_ROLES)):
            components = item.get(field)
            if (not isinstance(components, list)
                    or (side == "prompt" and not components)
                    or len(components) > MAX_COMPONENTS_PER_ITEM):
                raise LightweightSessionError("stored schema-3 components are invalid")
            for component in components:
                if (not isinstance(component, dict) or set(component) != {
                        "component_id", "component_role", "required_context_ids",
                        "allowed_detected_item_ids", "binding",
                        }):
                    raise LightweightSessionError("stored schema-3 component fields are invalid")
                component_id = component.get("component_id")
                role = component.get("component_role")
                contexts = component.get("required_context_ids")
                detected = component.get("allowed_detected_item_ids")
                if (not ID_RE.fullmatch(str(component_id or ""))
                        or component_id in component_ids or role not in roles_allowed
                        or not isinstance(contexts, list)
                        or contexts != sorted(set(contexts))
                        or any(not ID_RE.fullmatch(str(value or "")) for value in contexts)
                        or item_id in contexts
                        or detected not in ([item_id] + list(contexts), list(contexts))
                        or not detected
                        or (side == "answer" and item_id not in detected)):
                    raise LightweightSessionError("stored schema-3 component scope is invalid")
                key = validate_crop(
                    component.get("binding"),
                    "declared_%s_component_only" % side,
                    "%s component crop" % side,
                )
                if side == "prompt" and (key not in primary_keys
                        or (key, item_id) not in declared_prompt_pairs):
                    raise LightweightSessionError(
                        "stored prompt component parent page does not declare its teaching item"
                    )
                if side == "answer" and parent_pages[key]["answer_provenance"] \
                        != "official_solution":
                    raise LightweightSessionError("stored answer component parent is not official")
                binding = component["binding"]
                asset_catalog[binding["asset"]] = {
                    "role": side, "sha256": binding["asset_sha256"],
                    "locations": {key}, "target_item_id": item_id,
                    "component_id": component_id, "component_role": role,
                    "required_context_ids": contexts,
                    "allowed_detected_item_ids": detected,
                }
                (prompt_assets if side == "prompt" else answer_assets).add(
                    binding["asset"]
                )
                if side == "answer":
                    answer_location_keys.add(key)
                else:
                    prompt_roles.add(role)
                    target_visible = target_visible or item_id in detected
                    prompt_component_pairs.add((key, item_id))
                component_ids.add(component_id)
                all_assets.append((binding["asset"], binding["asset_sha256"]))
        has_visual = bool(prompt_roles & VISUAL_PROMPT_COMPONENT_ROLES)
        has_nonvisual = bool(prompt_roles - VISUAL_PROMPT_COMPONENT_ROLES)
        if (not target_visible
                or (kind == "text" and (has_visual or not has_nonvisual))
                or (kind == "figure" and (not has_visual or has_nonvisual))
                or (kind == "mixed" and (not has_visual or not has_nonvisual))):
            raise LightweightSessionError("stored teaching item kind/scope is dishonest")
    if item_ids != all_teaching_ids:
        raise LightweightSessionError("stored schema-3 teaching item coverage is incomplete")
    if prompt_component_pairs != declared_prompt_pairs:
        raise LightweightSessionError(
            "stored page teaching-item declarations and prompt components are not bijective"
        )
    official_dependency_keys = {
        key for key in dependency_keys
        if parent_pages[key]["answer_provenance"] == "official_solution"
    }
    if (not official_dependency_keys.issubset(answer_location_keys)
            or answer_location_keys.intersection(
                dependency_keys - official_dependency_keys)):
        raise LightweightSessionError("stored schema-3 answer component coverage is invalid")
    if len({path for path, unused_digest in all_assets}) != len(all_assets):
        raise LightweightSessionError("stored schema-3 visual assets must use distinct files")

    for contact in contacts:
        asset_catalog[contact["asset"]] = {
            "role": "contact", "sha256": contact["asset_sha256"],
            "locations": {
                key for key in primary_keys if key[3] in contact["pages"]
            },
        }
    for page in pages:
        key = next(key for key in primary_keys if key[3] == page["page"])
        asset_catalog[page["page_asset"]] = {
            "role": "page", "sha256": page["page_asset_sha256"],
            "locations": {key},
        }
    for page in dependency_pages:
        key = _location_key(page)
        asset_catalog[page["page_asset"]] = {
            "role": "dependency_page", "sha256": page["page_asset_sha256"],
            "locations": {key},
        }

    calls = receipt.get("model_calls")
    if not isinstance(calls, list) or not calls or len(calls) > MAX_MODEL_CALLS:
        raise LightweightSessionError("stored schema-3 model calls are invalid")
    call_ids = set()
    used_stage_assets = set()
    reviewed_assets = set()
    overview_assets = set()
    detail_assets = set()
    solution_assets = set()
    crop_review_assets = set()
    covered_locations = set()
    for call in calls:
        stage = call.get("stage") if isinstance(call, dict) else None
        fields = {"call_id", "stage", "host", "model", "inputs", "locations"}
        if stage == "crop_review":
            fields.add("crop_review")
        if (not isinstance(call, dict) or set(call) != fields
                or not ID_RE.fullmatch(str(call.get("call_id") or ""))
                or call["call_id"] in call_ids
                or stage not in ("overview", "detail", "solution", "crop_review")
                or not isinstance(call.get("host"), str) or not call["host"].strip()
                or not isinstance(call.get("model"), str) or not call["model"].strip()
                or not isinstance(call.get("inputs"), list) or not call["inputs"]
                or len(call["inputs"]) > MAX_ASSETS_PER_MODEL_CALL):
            raise LightweightSessionError("stored schema-3 model call identity is invalid")
        call_locations = _canonical_locations(call.get("locations"), "stored call locations")
        if call_locations != call["locations"]:
            raise LightweightSessionError("stored schema-3 call locations are not canonical")
        input_assets = set()
        for model_input in call["inputs"]:
            if (not isinstance(model_input, dict) or set(model_input) != {
                    "asset", "asset_sha256", "locations",
                    }
                    or model_input.get("asset") in input_assets
                    or model_input.get("asset") not in asset_catalog
                    or model_input.get("asset_sha256")
                    != asset_catalog[model_input["asset"]]["sha256"]):
                raise LightweightSessionError("stored schema-3 model input is invalid")
            input_locations = _canonical_locations(
                model_input.get("locations"), "stored model input locations"
            )
            if (input_locations != model_input["locations"]
                    or {_location_key(value) for value in input_locations}
                    != asset_catalog[model_input["asset"]]["locations"]):
                raise LightweightSessionError("stored schema-3 model input location is invalid")
            input_assets.add(model_input["asset"])
        inferred = set().union(*(
            asset_catalog[value]["locations"] for value in input_assets
        ))
        if {_location_key(value) for value in call_locations} != inferred:
            raise LightweightSessionError("stored schema-3 call/source binding is invalid")
        roles = {asset_catalog[value]["role"] for value in input_assets}
        if stage == "overview" and (roles != {"contact"} or len(input_assets) != 1):
            raise LightweightSessionError("stored schema-3 overview call is invalid")
        if stage == "detail":
            if not roles.issubset({"page", "dependency_page", "prompt"}):
                raise LightweightSessionError("stored schema-3 detail call is invalid")
            if "prompt" in roles and (roles != {"prompt"} or len({
                    asset_catalog[value]["target_item_id"] for value in input_assets
                    }) != 1):
                raise LightweightSessionError("stored detail call mixes teaching targets")
        if stage == "solution" and (roles != {"answer"} or len({
                asset_catalog[value]["target_item_id"] for value in input_assets
                }) != 1):
            raise LightweightSessionError("stored schema-3 solution call is invalid")
        if stage == "crop_review":
            if len(input_assets) != 1 or not roles.issubset({"prompt", "answer"}):
                raise LightweightSessionError("stored schema-3 crop review input is invalid")
            asset = next(iter(input_assets))
            binding = asset_catalog[asset]
            review = call.get("crop_review")
            review_fields = {
                "schema_version", "target_item_id", "side", "component_id",
                "component_role", "crop_sha256", "required_context_ids",
                "allowed_detected_item_ids", "verdict", "unrelated_content_present",
                "student_attempt_present", "detected_item_ids", "reviewer_kind",
                "reviewer", "reviewed_at", "invocation_id",
            }
            if (not isinstance(review, dict) or set(review) != review_fields
                    or review.get("schema_version") != 2
                    or review.get("target_item_id") != binding["target_item_id"]
                    or review.get("side") != binding["role"]
                    or review.get("component_id") != binding["component_id"]
                    or review.get("component_role") != binding["component_role"]
                    or review.get("crop_sha256") != binding["sha256"]
                    or review.get("required_context_ids")
                    != binding["required_context_ids"]
                    or review.get("allowed_detected_item_ids")
                    != binding["allowed_detected_item_ids"]
                    or review.get("detected_item_ids")
                    != binding["allowed_detected_item_ids"]
                    or review.get("verdict") != "declared_scope_only"
                    or review.get("unrelated_content_present") is not False
                    or review.get("student_attempt_present") is not False
                    or review.get("reviewer_kind") != "model_vision"
                    or review.get("reviewer") != call["model"]
                    or not _valid_timestamp(review.get("reviewed_at"))
                    or review.get("invocation_id") != call["call_id"]):
                raise LightweightSessionError("stored schema-3 crop review is invalid")
            if asset in reviewed_assets:
                raise LightweightSessionError("stored schema-3 crop has duplicate reviews")
            reviewed_assets.add(asset)
            crop_review_assets.add(asset)
        else:
            if input_assets.intersection(used_stage_assets):
                raise LightweightSessionError("stored schema-3 stage asset is reused")
            used_stage_assets.update(input_assets)
        call_ids.add(call["call_id"])
        covered_locations.update(inferred)
        if stage == "overview":
            overview_assets.update(input_assets)
        elif stage == "detail":
            detail_assets.update(input_assets)
        elif stage == "solution":
            solution_assets.update(input_assets)
    contact_assets = {
        value for value, row in asset_catalog.items() if row["role"] == "contact"
    }
    detail_page_assets = {
        value for value in detail_assets
        if asset_catalog[value]["role"] in {"page", "dependency_page"}
    }
    detailed_locations = set().union(*(
        asset_catalog[value]["locations"] for value in detail_page_assets
    )) if detail_page_assets else set()
    if (overview_assets != contact_assets
            or not set(allowed_locations).issubset(covered_locations)
            or detailed_locations != detail_required_locations
            or not prompt_assets.issubset(detail_assets)
            or solution_assets != answer_assets
            or crop_review_assets != (prompt_assets | answer_assets)):
        raise LightweightSessionError("stored schema-3 staged model coverage is invalid")
    unsigned = dict(receipt)
    unsigned.pop("receipt_id")
    expected_id = "lw-visual-" + hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    if receipt["receipt_id"] != expected_id:
        raise LightweightSessionError("stored schema-3 visual receipt digest is invalid")


def _validate_visual_receipt_shape(receipt, batch):
    if isinstance(receipt, dict) and receipt.get("schema_version") == 3:
        return _validate_visual_receipt_shape_v3(receipt, batch)
    return _validate_visual_receipt_shape_v2(receipt, batch)


def _validate_teaching_receipt_shape(receipt, batch):
    legacy = _visual_receipt_is_legacy(batch.get("visual_receipt"))
    expected = {
        "schema_version", "receipt_type", "batch_id", "visual_receipt_id",
        "phase", "notebook_entry", "notebook_entry_sha256",
        "explanation_detail", "created_at", "receipt_id",
    }
    if not legacy:
        expected.update({"inspected_pages", "taught_item_ids"})
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise LightweightSessionError("stored teaching receipt fields are invalid")
    if (receipt.get("schema_version") != 2
            or receipt.get("receipt_type") != "lightweight_taught_batch"
            or receipt.get("batch_id") != batch["batch_id"]
            or receipt.get("visual_receipt_id") != batch["visual_receipt"]["receipt_id"]
            or receipt.get("phase") != batch["chapter"]
            or receipt.get("explanation_detail") != "unabridged_beginner_friendly"
            or (not legacy and (
                receipt.get("inspected_pages") != batch["pages"]
                or receipt.get("taught_item_ids")
                != sorted(_visual_teaching_item_ids(batch["visual_receipt"]))))
            or not isinstance(receipt.get("notebook_entry"), str)
            or not receipt["notebook_entry"]
            or not HEX64.fullmatch(str(receipt.get("notebook_entry_sha256") or ""))
            or not _valid_timestamp(receipt.get("created_at"))
            or not ID_RE.fullmatch(str(receipt.get("receipt_id") or ""))):
        raise LightweightSessionError("stored teaching receipt identity is invalid")
    unsigned = dict(receipt)
    unsigned.pop("receipt_id")
    expected_id = "lw-taught-" + hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    if receipt["receipt_id"] != expected_id:
        raise LightweightSessionError("stored teaching receipt digest is invalid")


def _validate_session(session, workspace, materials):
    if not isinstance(session, dict) or set(session) != SESSION_KEYS:
        raise LightweightSessionError("lightweight session fields are invalid")
    if (session.get("schema_version") != SCHEMA_VERSION
            or session.get("session_type") != "on_demand_visual"
            or session.get("processing_mode") != "lightweight"):
        raise LightweightSessionError("lightweight session schema/type is invalid")
    if (not _valid_timestamp(session.get("created_at"))
            or not _valid_timestamp(session.get("updated_at"))):
        raise LightweightSessionError("lightweight session timestamps are invalid")
    try:
        _validate_quiz_bank_stat_baseline(session.get("quiz_bank_baseline"))
    except ValueError as exc:
        raise LightweightSessionError("quiz bank init baseline is invalid") from exc
    migrations = session.get("migration_history")
    if not isinstance(migrations, list) or len(migrations) > MAX_MIGRATION_RECORDS:
        raise LightweightSessionError("lightweight migration history is invalid")
    for migration in migrations:
        expected_migration_keys = {
            "from_schema", "migration", "prior_session_sha256", "created_at",
            "receipt_id",
        }
        if (not isinstance(migration, dict)
                or set(migration) != expected_migration_keys
                or migration.get("from_schema") != 1
                or migration.get("migration") != "evidence_free_reinitialization"
                or not HEX64.fullmatch(str(migration.get("prior_session_sha256") or ""))
                or not _valid_timestamp(migration.get("created_at"))
                or not ID_RE.fullmatch(str(migration.get("receipt_id") or ""))):
            raise LightweightSessionError("lightweight migration receipt is invalid")
        unsigned = dict(migration)
        unsigned.pop("receipt_id")
        expected_migration_id = "lw-migrate-" + hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest()[:24]
        if migration["receipt_id"] != expected_migration_id:
            raise LightweightSessionError("lightweight migration receipt digest is invalid")
    if (_resolved(session.get("workspace")) != _resolved(workspace)
            or _resolved(session.get("materials")) != _resolved(materials)):
        raise LightweightSessionError("lightweight session belongs to another path pair")
    sources = session.get("source_inventory")
    batches = session.get("batches")
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise LightweightSessionError("source inventory is invalid")
    if not isinstance(batches, list) or len(batches) > MAX_BATCHES:
        raise LightweightSessionError("batch ledger is invalid")
    source_ids = set()
    source_path_keys = set()
    sources_by_id = {}
    for source in sources:
        _validate_source(source)
        if source["source_id"] in source_ids:
            raise LightweightSessionError("source inventory contains duplicates")
        path_key = _material_path_key(source["relative_path"])
        if path_key in source_path_keys:
            raise LightweightSessionError(
                "source inventory contains host-equivalent path aliases"
            )
        source_ids.add(source["source_id"])
        source_path_keys.add(path_key)
        sources_by_id[source["source_id"]] = source
    batch_ids = set()
    active_batches = []
    visual_call_ids = set()
    visual_render_ids = set()
    for batch in batches:
        if (not isinstance(batch, dict)
                or not BATCH_KEYS <= set(batch)
                or set(batch) - BATCH_KEYS - BATCH_OPTIONAL_KEYS):
            raise LightweightSessionError("batch fields are invalid")
        if (not ID_RE.fullmatch(str(batch.get("batch_id") or ""))
                or batch["batch_id"] in batch_ids):
            raise LightweightSessionError("batch identity is invalid or duplicated")
        batch_ids.add(batch["batch_id"])
        if type(batch.get("chapter")) is not int or batch["chapter"] < 1:
            raise LightweightSessionError("batch chapter is invalid")
        if (not _valid_timestamp(batch.get("created_at"))
                or not _valid_timestamp(batch.get("updated_at"))):
            raise LightweightSessionError("batch timestamps are invalid")
        if batch.get("source_id") not in source_ids:
            raise LightweightSessionError("batch refers to an unknown source")
        if batch.get("source_path") != sources_by_id[batch["source_id"]]["relative_path"]:
            raise LightweightSessionError("batch source path disagrees with its identity")
        pages = batch.get("pages")
        if (not isinstance(pages, list) or not pages
                or pages != sorted(set(pages))
                or any(type(page) is not int or page < 1 for page in pages)
                or len(pages) > MAX_PAGES_PER_BATCH):
            raise LightweightSessionError("batch pages are invalid")
        if batch.get("status") not in (
                "planned", "visual_ready", "taught", "abandoned", "superseded"):
            raise LightweightSessionError("batch status is invalid")
        if batch["status"] in ("planned", "visual_ready"):
            active_batches.append(batch["batch_id"])
        revision = batch.get("source_revision")
        if not _valid_revision(revision):
            raise LightweightSessionError("batch source revision is invalid")
        _validate_answer_dependencies(batch, sources_by_id)
        _validate_dependency_history(batch)
        if batch.get("token_strategy") not in (
                _token_strategy(component_scoped=True),
                _token_strategy(component_scoped=False)):
            raise LightweightSessionError("batch token strategy is invalid")
        if (isinstance(batch.get("visual_receipt"), dict)
                and batch["visual_receipt"].get("schema_version") == 3
                and batch.get("token_strategy")
                != _token_strategy(component_scoped=True)):
            raise LightweightSessionError(
                "schema-3 visual evidence requires the generic teaching-item "
                "component token strategy"
            )
        supersedes = batch.get("supersedes_batch_id")
        if supersedes is not None and (
                not isinstance(supersedes, str) or not ID_RE.fullmatch(supersedes)
                or supersedes == batch["batch_id"]):
            raise LightweightSessionError("batch predecessor identity is invalid")
        if batch["status"] == "planned":
            if batch.get("visual_receipt") is not None or batch.get("teaching_receipt") is not None:
                raise LightweightSessionError("planned batch contains premature evidence")
            if batch.get("abandonment") is not None or batch.get("supersession") is not None:
                raise LightweightSessionError("active batch contains abandonment evidence")
        elif batch["status"] == "abandoned":
            abandonment = batch.get("abandonment")
            if (not isinstance(abandonment, dict)
                    or set(abandonment) != {
                        "prior_status", "reason", "created_at", "receipt_id",
                    }
                    or abandonment.get("prior_status") not in ("planned", "visual_ready")
                    or not isinstance(abandonment.get("reason"), str)
                    or not 5 <= len(abandonment["reason"].strip()) <= 500
                    or not _valid_timestamp(abandonment.get("created_at"))
                    or not ID_RE.fullmatch(str(abandonment.get("receipt_id") or ""))):
                raise LightweightSessionError("abandoned batch receipt is invalid")
            unsigned = dict(abandonment)
            unsigned.pop("receipt_id")
            expected_abandonment_id = "lw-abandon-" + hashlib.sha256(
                _canonical_json({"batch_id": batch["batch_id"], **unsigned}).encode("utf-8")
            ).hexdigest()[:24]
            if abandonment["receipt_id"] != expected_abandonment_id:
                raise LightweightSessionError("abandoned batch receipt digest is invalid")
            if abandonment["prior_status"] == "planned":
                if batch.get("visual_receipt") is not None:
                    raise LightweightSessionError("abandoned planned batch has visual evidence")
            else:
                _validate_visual_receipt_shape(batch.get("visual_receipt"), batch)
                for call in batch["visual_receipt"]["model_calls"]:
                    if call["call_id"] in visual_call_ids:
                        raise LightweightSessionError(
                            "host visual invocation IDs must not be reused across batches"
                        )
                    visual_call_ids.add(call["call_id"])
                for page in (
                    batch["visual_receipt"]["pages"]
                    + batch["visual_receipt"]["dependency_pages"]
                ):
                    if page["render_id"] in visual_render_ids:
                        raise LightweightSessionError(
                            "host render invocation IDs must not be reused across batches"
                        )
                    visual_render_ids.add(page["render_id"])
            if batch.get("teaching_receipt") is not None:
                raise LightweightSessionError("abandoned batch cannot contain teaching evidence")
            if batch.get("supersession") is not None:
                raise LightweightSessionError("abandoned batch cannot contain supersession evidence")
        elif batch["status"] == "superseded":
            if batch.get("abandonment") is not None:
                raise LightweightSessionError("superseded batch cannot contain abandonment evidence")
            _validate_visual_receipt_shape(batch.get("visual_receipt"), batch)
            _validate_teaching_receipt_shape(batch.get("teaching_receipt"), batch)
            supersession = batch.get("supersession")
            expected_supersession_keys = {
                "prior_status", "reason", "successor_batch_id",
                "preserved_progress_event", "created_at", "receipt_id",
            }
            if (not isinstance(supersession, dict)
                    or set(supersession) != expected_supersession_keys
                    or supersession.get("prior_status") != "taught"
                    or not isinstance(supersession.get("reason"), str)
                    or not 5 <= len(supersession["reason"].strip()) <= 500
                    or not ID_RE.fullmatch(
                        str(supersession.get("successor_batch_id") or ""))
                    or supersession.get("preserved_progress_event")
                    != _progress_event(batch)
                    or not _valid_timestamp(supersession.get("created_at"))
                    or not ID_RE.fullmatch(str(supersession.get("receipt_id") or ""))):
                raise LightweightSessionError("superseded batch receipt is invalid")
            unsigned = dict(supersession)
            unsigned.pop("receipt_id")
            expected_supersession_id = "lw-supersede-" + hashlib.sha256(
                _canonical_json({"batch_id": batch["batch_id"], **unsigned}).encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
            if supersession["receipt_id"] != expected_supersession_id:
                raise LightweightSessionError("superseded batch receipt digest is invalid")
            for call in batch["visual_receipt"]["model_calls"]:
                if call["call_id"] in visual_call_ids:
                    raise LightweightSessionError(
                        "host visual invocation IDs must not be reused across batches"
                    )
                visual_call_ids.add(call["call_id"])
            for page in (
                batch["visual_receipt"]["pages"]
                + batch["visual_receipt"]["dependency_pages"]
            ):
                if page["render_id"] in visual_render_ids:
                    raise LightweightSessionError(
                        "host render invocation IDs must not be reused across batches"
                    )
                visual_render_ids.add(page["render_id"])
        else:
            if batch.get("abandonment") is not None or batch.get("supersession") is not None:
                raise LightweightSessionError("non-abandoned batch contains abandonment evidence")
            _validate_visual_receipt_shape(batch.get("visual_receipt"), batch)
            for call in batch["visual_receipt"]["model_calls"]:
                if call["call_id"] in visual_call_ids:
                    raise LightweightSessionError(
                        "host visual invocation IDs must not be reused across batches"
                    )
                visual_call_ids.add(call["call_id"])
            for page in (
                batch["visual_receipt"]["pages"]
                + batch["visual_receipt"]["dependency_pages"]
            ):
                if page["render_id"] in visual_render_ids:
                    raise LightweightSessionError(
                        "host render invocation IDs must not be reused across batches"
                    )
                visual_render_ids.add(page["render_id"])
            if batch["status"] == "visual_ready":
                if batch.get("teaching_receipt") is not None:
                    raise LightweightSessionError("visual-ready batch contains teaching evidence")
            else:
                _validate_teaching_receipt_shape(batch.get("teaching_receipt"), batch)
    batches_by_id = {batch["batch_id"]: batch for batch in batches}
    for batch in batches:
        predecessor_id = batch.get("supersedes_batch_id")
        if predecessor_id is None:
            continue
        predecessor = batches_by_id.get(predecessor_id)
        if (predecessor is None or predecessor["status"] != "superseded"
                or predecessor["supersession"]["successor_batch_id"]
                != batch["batch_id"]
                or predecessor["chapter"] != batch["chapter"]
                or predecessor["source_id"] != batch["source_id"]
                or predecessor["pages"] != batch["pages"]
                or _answer_dependency_scope(predecessor["answer_dependencies"])
                != _answer_dependency_scope(batch["answer_dependencies"])):
            raise LightweightSessionError("batch supersession chain is invalid")
    for batch in batches:
        if batch["status"] != "superseded":
            continue
        successor = batches_by_id.get(batch["supersession"]["successor_batch_id"])
        if successor is None or successor.get("supersedes_batch_id") != batch["batch_id"]:
            raise LightweightSessionError("superseded batch has no exact successor")
        seen_chain = {batch["batch_id"]}
        cursor = successor
        while cursor.get("supersession") is not None:
            if cursor["batch_id"] in seen_chain:
                raise LightweightSessionError("batch supersession chain contains a cycle")
            seen_chain.add(cursor["batch_id"])
            cursor = batches_by_id.get(cursor["supersession"]["successor_batch_id"])
            if cursor is None:
                raise LightweightSessionError("batch supersession chain is broken")
        if cursor["batch_id"] in seen_chain:
            raise LightweightSessionError("batch supersession chain contains a cycle")
    current_slice_keys = set()
    for batch in _current_batches(session):
        key = (batch["chapter"], batch["source_id"], tuple(batch["pages"]))
        if key in current_slice_keys:
            raise LightweightSessionError(
                "one source/page slice has multiple unsuperseded attempts"
            )
        current_slice_keys.add(key)
    if len(active_batches) > 1:
        raise LightweightSessionError(
            "lightweight session must have at most one active batch"
        )
    return session


def _session_path(workspace):
    return safe_workspace_entry(workspace, SESSION_PATH)


def _ensure_asset_directory(workspace):
    """Create the canonical lightweight asset directory without following aliases."""

    try:
        lightweight_dir = safe_workspace_entry(workspace, ".lightweight")
        asset_dir = safe_workspace_entry(workspace, ASSET_DIRECTORY)
        for path, label in (
                (lightweight_dir, ".lightweight"),
                (asset_dir, ASSET_DIRECTORY)):
            if os.path.lexists(path):
                if is_link_or_reparse(path) or not os.path.isdir(path):
                    raise LightweightSessionError(
                        "%s must be a regular workspace-local directory" % label
                    )
                continue
            os.mkdir(path)
            if is_link_or_reparse(path) or not os.path.isdir(path):
                raise LightweightSessionError(
                    "%s could not be created as a safe workspace-local directory"
                    % label
                )
        # Re-resolve every segment after creation so a linked parent cannot be
        # accepted merely because it appeared between the two mkdir calls.
        asset_dir = safe_workspace_entry(workspace, ASSET_DIRECTORY)
    except LightweightSessionError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise LightweightSessionError(
            "could not initialize the workspace-local lightweight asset directory"
        ) from exc
    return str(asset_dir)


def _load_session(workspace, materials):
    path = _session_path(workspace)
    if not os.path.isfile(path) or is_link_or_reparse(path):
        raise LightweightSessionError(
            "lightweight session is missing; run lightweight_session.py init"
        )
    return _validate_session(_strict_json(path, "lightweight session"), workspace, materials)


def quiz_bank_baseline(workspace):
    """Return the immutable stat-only bank baseline captured at first init."""

    session = _load_session_from_workspace(_resolved(workspace))
    return dict(session["quiz_bank_baseline"])


def _save_session(workspace, session):
    session["updated_at"] = _utc_now()
    _validate_session(session, session["workspace"], session["materials"])
    destination = _session_path(workspace)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if is_link_or_reparse(os.path.dirname(destination)):
        raise LightweightSessionError(".lightweight directory is link-backed")
    atomic_write_json(destination, session)


def _start_gate(workspace, materials):
    gate = exam_start.check_start_gate(workspace, materials)
    if not gate.get("ready_to_start"):
        raise LightweightSessionError(
            "workspace start gate is blocked; rerun exam_start.py confirm first"
        )
    if gate.get("processing_mode") != "lightweight":
        raise LightweightSessionError(
            "processing_mode is full; use ingest_course.py instead of the lightweight path"
        )
    return gate


def _source_row(session, relative_path):
    relative_path = _relative_material_path(relative_path)
    requested_key = _material_path_key(relative_path)
    matches = [
        row for row in session["source_inventory"]
        if _material_path_key(row["relative_path"]) == requested_key
    ]
    if len(matches) != 1:
        raise LightweightSessionError("source is not uniquely present in the inventory")
    return matches[0]


def _batch(session, batch_id):
    matches = [row for row in session["batches"] if row["batch_id"] == batch_id]
    if len(matches) != 1:
        raise LightweightSessionError("batch_id is unknown")
    return matches[0]


def _revision(path, source_row=None, force_hash=False):
    try:
        before = os.stat(path, follow_symlinks=False)
        if (not force_hash and isinstance(source_row, dict)
                and HEX64.fullmatch(str(source_row.get("content_sha256") or ""))
                and source_row.get("size") == before.st_size
                and source_row.get("mtime_ns") == before.st_mtime_ns
                and source_row.get("content_identity") == _stat_identity(before)):
            return {
                "sha256": source_row["content_sha256"],
                "size": before.st_size,
                "mtime_ns": before.st_mtime_ns,
                "identity": _stat_identity(before),
            }
        digest, metadata = lightweight_stable_file_sha256(path)
    except (OSError, TypeError, ValueError) as exc:
        raise LightweightSessionError(
            "selected source changed while it was being bound"
        ) from exc
    if source_row is not None:
        source_row["content_sha256"] = digest
        source_row["size"] = metadata["size_bytes"]
        source_row["mtime_ns"] = metadata["mtime_ns"]
        source_row["content_identity"] = list(metadata["identity"])
    return {
        "sha256": digest,
        "size": metadata["size_bytes"],
        "mtime_ns": metadata["mtime_ns"],
        "identity": list(metadata["identity"]),
    }


def _revision_matches(path, expected, exact):
    if exact:
        return _revision(path) == expected
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise LightweightSessionError("selected source metadata is unreadable") from exc
    return (
        current.st_size == expected.get("size")
        and current.st_mtime_ns == expected.get("mtime_ns")
        and _stat_identity(current) == expected.get("identity")
    )


def _batch_source_revision_bindings(batch):
    rows = [{
        "source_id": batch["source_id"],
        "source_path": batch["source_path"],
        "source_revision": batch["source_revision"],
    }]
    rows.extend({
        "source_id": row["source_id"],
        "source_path": row["source_path"],
        "source_revision": row["source_revision"],
    } for row in batch["answer_dependencies"])
    unique = {}
    for row in rows:
        existing = unique.get(row["source_id"])
        if existing is not None and existing != row:
            raise LightweightSessionError(
                "batch binds conflicting revisions for one source"
            )
        unique[row["source_id"]] = row
    return list(unique.values())


def _answer_dependency_scope(rows):
    return [
        {
            "source_id": row["source_id"],
            "source_path": row["source_path"],
            "pages": list(row["pages"]),
        }
        for row in rows
    ]


def _assert_batch_sources_current(materials, batch, exact=True, cache=None):
    for row in _batch_source_revision_bindings(batch):
        source = _material_file(materials, row["source_path"])
        cache_key = (
            os.path.normcase(os.path.realpath(source)),
            row["source_revision"]["sha256"], bool(exact),
        )
        if cache is not None and cache_key in cache:
            matches = cache[cache_key]
        else:
            matches = _revision_matches(
                source, row["source_revision"], exact=exact
            )
            if cache is not None:
                cache[cache_key] = matches
        if not matches:
            raise LightweightSessionError(
                "source revision changed after binding: %s" % row["source_path"]
            )


def _notebook_entry_binding(workspace, value, chapter):
    """Resolve one stable notebook entry anchor and hash only that entry block."""
    if not isinstance(value, str) or not value.strip():
        raise LightweightSessionError(
            "notebook evidence must be notebook/chNN.md#entry-anchor"
        )
    raw_path, separator, raw_anchor = value.strip().replace("\\", "/").partition("#")
    anchor = unquote(raw_anchor).strip() if separator else ""
    if not anchor or any(char in anchor for char in ("\x00", "\r", "\n", "#")):
        raise LightweightSessionError(
            "notebook evidence must include one safe, existing entry anchor"
        )
    notebook_path, notebook_absolute = _workspace_asset(
        workspace, raw_path, "notebook entry"
    )
    if (not notebook_path.startswith("notebook/")
            or notebook_path.lower() == "notebook/index.md"
            or not re.fullmatch(r"notebook/ch0*%d\.md" % chapter,
                                notebook_path, re.IGNORECASE)):
        raise LightweightSessionError(
            "notebook evidence must be the current chapter file notebook/chNN.md#anchor"
        )
    try:
        import notebook as notebook_engine
        with open(notebook_absolute, "r", encoding="utf-8") as stream:
            pre, blocks = notebook_engine.parse_chapter(stream.read())
        anchors = notebook_engine.anchors_for(pre, blocks)
    except (ImportError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise LightweightSessionError("notebook entry cannot be parsed") from exc
    matches = [block for block, actual in zip(blocks, anchors) if actual == anchor]
    if len(matches) != 1:
        raise LightweightSessionError(
            "notebook entry anchor does not identify exactly one durable entry"
        )
    block_text = "\n".join(matches[0]["lines"]).rstrip() + "\n"
    return (
        "%s#%s" % (notebook_path, anchor),
        _sha256_bytes(block_text.encode("utf-8")),
    )


def _batch_id(source_id, revision, chapter, pages):
    identity = {
        "source_id": source_id,
        "source_sha256": revision["sha256"],
        "chapter": chapter,
        "pages": pages,
    }
    return "lw-batch-" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:24]


def _contact_sheet_groups(pages):
    if len(pages) <= 1:
        return []
    return [
        pages[index:index + CONTACT_SHEET_GROUP_SIZE]
        for index in range(0, len(pages), CONTACT_SHEET_GROUP_SIZE)
    ]


def _contact_min_dimensions(page_count):
    """Require roughly 768px per tile in the fixed row-major overview grid."""
    columns = 1 if page_count == 1 else 2
    rows = (page_count + columns - 1) // columns
    return 768 * columns, 768 * rows


def _crop_manifest_template(declaration):
    return {
        "source_id": "<bound source id>",
        "source_path": "<bound source path>",
        "source_sha256": "<bound source sha256>",
        "page": "<bound source page>",
        "parent_render_id": "<exact parent page render id>",
        "parent_page_asset": "<exact parent rendered page asset>",
        "parent_page_asset_sha256": "<exact parent page asset sha256>",
        "crop_bbox_normalized": {
            "x": "<0..1>", "y": "<0..1>",
            "width": "<0..1>", "height": "<0..1>",
        },
        "crop_declaration": declaration,
        "asset": "<distinct declared-scope component-crop PNG>",
    }


def _teaching_component_contract(side):
    roles = (
        sorted(PROMPT_COMPONENT_ROLES) if side == "prompt"
        else sorted(ANSWER_COMPONENT_ROLES)
    )
    return {
        "component_id": "<stable component id unique in this batch>",
        "component_role": "<%s>" % "|".join(roles),
        "required_context_ids": [],
        "allowed_detected_item_ids": [
            (
                "<target first + sorted contexts, or sorted contexts for a "
                "prompt context-only crop>"
                if side == "prompt"
                else "<target first + sorted contexts; answer must contain target>"
            )
        ],
        "binding": _crop_manifest_template(
            "declared_%s_component_only" % side
        ),
    }


def _teaching_item_contract():
    return {
        "teaching_item_id": "<stable current-batch id>",
        "kind": "<text|figure|mixed>",
        "description": "<what this exact teaching item asks>",
        "prompt_components": [_teaching_component_contract("prompt")],
        "answer_components": [],
        "answer_display_phase": "solution_or_review_only",
    }


def _visual_manifest_template(batch):
    dependency_pages = []
    for dependency in batch["answer_dependencies"]:
        for page in dependency["pages"]:
            dependency_pages.append({
                "source_id": dependency["source_id"],
                "source_path": dependency["source_path"],
                "source_sha256": dependency["source_revision"]["sha256"],
                "page": page,
                "render_id": "<unique host render invocation id>",
                "page_asset": "<workspace-relative rendered answer-locator page PNG>",
                "visible": True,
                "purpose": "answer_locator_only",
                "content_types": ["answer"],
                "answer_provenance": (
                    "<student_attempt|official_solution|none|unknown>"
                ),
            })
    dependency_pages.sort(key=lambda value: (
        value["source_path"].casefold(), value["source_path"], value["page"]
    ))
    return {
        "schema_version": VISUAL_RECEIPT_SCHEMA_VERSION,
        "batch_id": batch["batch_id"],
        "source_sha256": batch["source_revision"]["sha256"],
        "inspection_method": "model_visual",
        "pages": [
            {
                "page": page,
                "render_id": "<unique host render invocation id>",
                "page_asset": "<workspace-relative visible page PNG>",
                "visible": True,
                "content_types": [],
                "answer_provenance": (
                    "<student_attempt|official_solution|none|unknown>"
                ),
                "teaching_item_ids": [
                    "<every item with a prompt component on this page; IDs may repeat across pages>"
                ],
                "detail_required": "<true when single-page or not text-only>",
                "detail_reason": (
                    "<single_page_no_overview|dense_or_nontext_content|"
                    "legible_text_only_overview>"
                ),
            }
            for page in batch["pages"]
        ],
        "teaching_items": [_teaching_item_contract()],
        "dependency_pages": dependency_pages,
        "contact_sheets": [
            {
                "asset": "<workspace-relative contact sheet for pages %s>"
                         % "-".join(str(value) for value in group),
                "pages": group,
                "purpose": "overview_only",
            }
            for group in (
                _contact_sheet_groups(batch["pages"])
                if len(batch["pages"]) > 1 else []
            )
        ],
        "model_calls": [
            {
                "call_id": "<unique host visual invocation id>",
                "stage": "<overview|detail|solution>",
                "host": "<agent/tool host>",
                "model": "<visual model identity>",
                "input_assets": ["<exact workspace-relative image sent>"],
                "locations": [{
                    "source_id": "<source id>",
                    "source_path": "<source path>",
                    "source_sha256": "<source sha256>",
                    "page": "<source page>",
                }],
            },
            {
                "call_id": "<unique independent crop-review invocation id>",
                "stage": "crop_review",
                "host": "<agent/tool host>",
                "model": "<visual model identity>",
                "input_assets": ["<exactly one prompt or answer crop>"],
                "locations": ["<the crop's exact source-qualified location>"],
                "crop_review": {
                    "schema_version": 2,
                    "target_item_id": "<exact teaching/question item id>",
                    "side": "<prompt|answer>",
                    "component_id": "<exact teaching component id>",
                    "component_role": "<declared component role>",
                    "crop_sha256": "<exact crop SHA-256>",
                    "required_context_ids": [],
                    "allowed_detected_item_ids": [
                        "<prompt may be target+contexts or context-only; answer must include target>"
                    ],
                    "verdict": "declared_scope_only",
                    "unrelated_content_present": False,
                    "student_attempt_present": False,
                    "detected_item_ids": [
                        "<exact allowed_detected_item_ids for this component>"
                    ],
                    "reviewer_kind": "model_vision",
                    "reviewer": "<same visual model identity>",
                    "reviewed_at": "<UTC timestamp>",
                    "invocation_id": "<same crop-review call_id>",
                },
            },
        ],
    }


def _visual_assets_stale(workspace, batch, digest_cache=None, exact=True):
    receipt = batch.get("visual_receipt")
    if not isinstance(receipt, dict):
        return False
    bindings = []
    for page in receipt.get("pages") or []:
        if not isinstance(page, dict):
            return True
        bindings.append((
            page.get("page_asset"), page.get("page_asset_sha256"),
            page.get("page_asset_size_bytes"), page.get("page_asset_mtime_ns"),
        ))
        for figure in page.get("figure_questions") or []:
            if not isinstance(figure, dict):
                return True
            prompt = figure.get("prompt_binding")
            if not isinstance(prompt, dict):
                return True
            bindings.append((
                prompt.get("asset"), prompt.get("asset_sha256"),
                prompt.get("asset_size_bytes"), prompt.get("asset_mtime_ns"),
            ))
            answer = figure.get("answer_binding")
            if answer is not None:
                if not isinstance(answer, dict):
                    return True
                bindings.append((
                    answer.get("asset"), answer.get("asset_sha256"),
                    answer.get("asset_size_bytes"), answer.get("asset_mtime_ns"),
                ))
    if receipt.get("schema_version") == 3:
        for item in receipt.get("teaching_items") or []:
            if not isinstance(item, dict):
                return True
            for field in ("prompt_components", "answer_components"):
                for component in item.get(field) or []:
                    if not isinstance(component, dict):
                        return True
                    binding = component.get("binding")
                    if not isinstance(binding, dict):
                        return True
                    bindings.append((
                        binding.get("asset"), binding.get("asset_sha256"),
                        binding.get("asset_size_bytes"),
                        binding.get("asset_mtime_ns"),
                    ))
    for page in receipt.get("dependency_pages") or []:
        if not isinstance(page, dict):
            return True
        bindings.append((
            page.get("page_asset"), page.get("page_asset_sha256"),
            page.get("page_asset_size_bytes"), page.get("page_asset_mtime_ns"),
        ))
    for contact in receipt.get("contact_sheets") or []:
        if not isinstance(contact, dict):
            return True
        bindings.append((
            contact.get("asset"), contact.get("asset_sha256"),
            contact.get("asset_size_bytes"), contact.get("asset_mtime_ns"),
        ))
    for relative, expected_sha256, expected_size, expected_mtime_ns in bindings:
        try:
            if not exact:
                (_normalized, _absolute, actual_size,
                 actual_mtime_ns) = _workspace_visual_asset_metadata(
                    workspace, relative, "visual asset"
                )
                if (actual_size != expected_size
                        or actual_mtime_ns != expected_mtime_ns):
                    return True
                continue
            if digest_cache is not None and relative in digest_cache:
                actual_sha256, actual_size, actual_mtime_ns = digest_cache[relative]
            else:
                (_normalized, absolute, _width, _height, actual_sha256,
                 actual_size, actual_mtime_ns) = _workspace_visual_asset(
                    workspace, relative, "visual asset")
                if digest_cache is not None:
                    digest_cache[relative] = (
                        actual_sha256, actual_size, actual_mtime_ns,
                    )
            if (actual_sha256 != expected_sha256 or actual_size != expected_size
                    or actual_mtime_ns != expected_mtime_ns):
                return True
        except (OSError, LightweightSessionError):
            return True
    return False


def _load_session_from_workspace(workspace):
    gate = exam_start.check_registered_workspace_gate(workspace)
    candidates = gate.get("candidate_materials") if isinstance(gate, dict) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise LightweightSessionError(
            "workspace must have exactly one confirmed materials registration"
        )
    materials = _resolved(candidates[0])
    exact_gate = exam_start.check_start_gate(workspace, materials)
    if (not exact_gate.get("ready_to_start")
            or exact_gate.get("processing_mode") != "lightweight"):
        raise LightweightSessionError(
            "registered workspace/materials pair no longer authorizes lightweight use"
        )
    raw = _strict_json(_session_path(workspace), "lightweight session")
    if not isinstance(raw, dict) or not isinstance(raw.get("materials"), str):
        raise LightweightSessionError("lightweight session has no canonical materials root")
    if _resolved(raw["materials"]) != materials:
        raise LightweightSessionError(
            "lightweight session pair disagrees with the sole confirmed registration"
        )
    return _validate_session(raw, workspace, materials)


def _progress_event(batch):
    receipt = batch["teaching_receipt"]
    event = {
        "batch_id": batch["batch_id"],
        "visual_receipt_id": batch["visual_receipt"]["receipt_id"],
        "teaching_receipt_id": receipt["receipt_id"],
        "notebook_entry": receipt["notebook_entry"],
        "notebook_entry_sha256": receipt["notebook_entry_sha256"],
        "source_sha256": batch["source_revision"]["sha256"],
    }
    if _visual_receipt_is_legacy(batch["visual_receipt"]):
        event["pages"] = list(batch["pages"])
    else:
        event["inspected_pages"] = list(receipt["inspected_pages"])
        event["taught_item_ids"] = list(receipt["taught_item_ids"])
    return event


def _state_batch_event_occurrences(state, batch_id):
    """Return every phase/event occurrence for one lightweight batch ID."""
    phase_evidence = state.get("phase_evidence")
    if phase_evidence is None:
        return []
    if not isinstance(phase_evidence, dict):
        raise LightweightSessionError("study_state phase_evidence must be an object")
    occurrences = []
    for phase_key, record in phase_evidence.items():
        if not isinstance(record, dict):
            raise LightweightSessionError(
                "study_state phase_evidence[%r] must be an object" % phase_key
            )
        events = record.get("lightweight_batches", [])
        if not isinstance(events, list):
            raise LightweightSessionError(
                "study_state phase_evidence[%r].lightweight_batches must be an array"
                % phase_key
            )
        occurrences.extend(
            (str(phase_key), event) for event in events
            if isinstance(event, dict) and event.get("batch_id") == batch_id
        )
    return occurrences


def _progress_event_error_in_session(workspace, session, phase, event,
                                     source_revision_cache=None,
                                     asset_digest_cache=None,
                                     exact_live=True):
    try:
        matches = [
            batch for batch in session["batches"]
            if isinstance(event, dict) and batch["batch_id"] == event.get("batch_id")
        ]
        if len(matches) != 1:
            return "batch_id does not identify exactly one session batch"
        batch = matches[0]
        if batch["chapter"] != phase:
            return "batch chapter does not match phase %d" % phase
        if batch["status"] == "superseded":
            preserved = (batch.get("supersession") or {}).get(
                "preserved_progress_event"
            )
            return None if event == preserved else (
                "superseded progress event disagrees with its preserved audit binding"
            )
        if batch["status"] != "taught" or not isinstance(batch.get("teaching_receipt"), dict):
            return "batch has not reached the taught state"
        expected = _progress_event(batch)
        if event != expected:
            return "progress event disagrees with the exact batch/receipt binding"
        _assert_batch_sources_current(
            session["materials"], batch, exact=exact_live,
            cache=source_revision_cache,
        )
        if _visual_assets_stale(
                workspace, batch, digest_cache=asset_digest_cache,
                exact=exact_live):
            return "visual evidence changed or is no longer readable"
        notebook_entry, digest = _notebook_entry_binding(
            workspace, event["notebook_entry"], phase
        )
        if (notebook_entry != event["notebook_entry"]
                or digest != event["notebook_entry_sha256"]):
            return "durable notebook entry changed after teaching"
        return None
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return str(exc)


def progress_event_error(workspace, phase, event):
    """Return a live-binding error for one canonical progress event, or ``None``."""
    try:
        workspace = _resolved(workspace)
        session = _load_session_from_workspace(workspace)
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return str(exc)
    return _progress_event_error_in_session(workspace, session, phase, event)


def phase_identity_problems(workspace, phase, events):
    """Check immutable session/event identity without reading old source/assets/notebook."""
    try:
        workspace = _resolved(workspace)
        session = _load_session_from_workspace(workspace)
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return [str(exc)]
    declared = _current_batches(session, phase)
    archived = {
        batch["batch_id"]: batch["supersession"]["preserved_progress_event"]
        for batch in session["batches"]
        if batch["chapter"] == phase and batch["status"] == "superseded"
    }
    problems = []
    unfinished = [
        batch["batch_id"] for batch in declared if batch["status"] != "taught"
    ]
    if unfinished:
        problems.append(
            "completed historical phase has unfinished lightweight batches: %s"
            % ", ".join(unfinished)
        )
    expected = {
        batch["batch_id"]: batch for batch in declared if batch["status"] == "taught"
    }
    if not isinstance(events, list):
        return problems + ["lightweight progress events must be an array"]
    seen = set()
    for event in events:
        batch_id = event.get("batch_id") if isinstance(event, dict) else None
        if not isinstance(batch_id, str) or batch_id in seen:
            problems.append("lightweight progress events contain an invalid/duplicate batch_id")
            continue
        seen.add(batch_id)
        if batch_id in archived:
            if event != archived[batch_id]:
                problems.append(
                    "superseded progress event for %s disagrees with its audit receipt"
                    % batch_id
                )
            continue
        batch = expected.get(batch_id)
        if batch is None:
            problems.append("progress event references unknown/non-taught batch %s" % batch_id)
        elif event != _progress_event(batch):
            problems.append("progress event for %s disagrees with its immutable receipt" % batch_id)
    missing = sorted(set(expected) - seen)
    if missing:
        problems.append("taught batches missing progress events: %s" % ", ".join(missing))
    archived_missing = sorted(set(archived) - seen)
    if archived_missing:
        problems.append(
            "superseded batches missing preserved progress events: %s"
            % ", ".join(archived_missing)
        )
    return problems


def full_switch_problem(workspace):
    """Return why a lightweight session cannot be switched to full, if anything."""
    workspace = _resolved(workspace)
    path = _session_path(workspace)
    if not os.path.lexists(path):
        return None
    if not os.path.isfile(path) or is_link_or_reparse(path):
        return "lightweight session ledger is unsafe; repair it before switching modes"
    try:
        session = _load_session_from_workspace(workspace)
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return "lightweight session ledger is invalid: %s" % exc
    active = [
        batch["batch_id"] for batch in session["batches"]
        if batch["status"] in ("planned", "visual_ready")
    ]
    if active:
        return (
            "abandon active lightweight batch(es) before switching to full: %s"
            % ", ".join(active)
        )
    return None


def phase_switch_problem(workspace, target_phase):
    """Return why current active lightweight work forbids leaving its phase."""
    if type(target_phase) is not int or target_phase < 1:
        return "target lightweight phase must be a positive integer"
    workspace = _resolved(workspace)
    path = _session_path(workspace)
    if not os.path.lexists(path):
        return None
    try:
        session = _load_session_from_workspace(workspace)
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return "lightweight session ledger is invalid: %s" % exc
    active = [
        batch for batch in _current_batches(session)
        if batch["status"] in ("planned", "visual_ready")
        and batch["chapter"] != target_phase
    ]
    if active:
        return (
            "finish or abandon active lightweight batch %s before leaving phase %d"
            % (active[0]["batch_id"], active[0]["chapter"])
        )
    return None


def phase_completion_problems(workspace, phase, events):
    """Validate the complete declared lightweight scope for one learning phase."""
    try:
        workspace = _resolved(workspace)
        session = _load_session_from_workspace(workspace)
    except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
        return [str(exc)]
    declared = _current_batches(session, phase)
    if not declared:
        return [
            "当前阶段没有声明任何按需视觉批次；不能把未查看的材料标为已覆盖"
        ]
    unfinished = [batch["batch_id"] for batch in declared if batch["status"] != "taught"]
    problems = []
    if unfinished:
        problems.append(
            "当前阶段仍有未完成的 lightweight batch: %s" % ", ".join(unfinished)
        )
    expected_ids = {
        batch["batch_id"] for batch in declared if batch["status"] == "taught"
    }
    archived_ids = {
        batch["batch_id"] for batch in session["batches"]
        if batch["chapter"] == phase and batch["status"] == "superseded"
    }
    archived_expected = {
        batch["batch_id"]: batch["supersession"]["preserved_progress_event"]
        for batch in session["batches"]
        if batch["chapter"] == phase and batch["status"] == "superseded"
    }
    archived_seen = {}
    for event in events:
        batch_id = event.get("batch_id") if isinstance(event, dict) else None
        if batch_id in archived_expected:
            if batch_id in archived_seen or event != archived_expected[batch_id]:
                problems.append(
                    "superseded batch %s has a conflicting/duplicate progress event"
                    % batch_id
                )
            archived_seen[batch_id] = event
    missing_archived = sorted(set(archived_expected) - set(archived_seen))
    if missing_archived:
        problems.append(
            "superseded batches lost preserved progress events: %s"
            % ", ".join(missing_archived)
        )
    current_events = [
        event for event in events
        if isinstance(event, dict) and event.get("batch_id") not in archived_ids
    ]
    event_ids = {event.get("batch_id") for event in current_events}
    if event_ids != expected_ids or len(current_events) != len(event_ids):
        problems.append(
            "lightweight progress events 必须与当前阶段所有 taught batches 精确一一对应"
        )
    source_revision_cache = {}
    asset_digest_cache = {}
    for event in current_events:
        error = _progress_event_error_in_session(
            workspace, session, phase, event,
            source_revision_cache=source_revision_cache,
            asset_digest_cache=asset_digest_cache,
        )
        if error:
            problems.append("batch %r: %s" % (
                event.get("batch_id") if isinstance(event, dict) else None, error
            ))
    return problems


def workspace_health(workspace, state, live_current_taught=True, exact_live=False,
                     _session_snapshot=None):
    """Return validator-facing live health without parsing any unselected material."""
    workspace = _resolved(workspace)
    path = _session_path(workspace)
    if not os.path.isfile(path) or is_link_or_reparse(path):
        return {
            "errors": [],
            "warnings": [
                "lightweight session has not been initialized; no material is taught yet"
            ],
            "stats": {"lightweight_session": "not_initialized"},
        }
    errors = []
    warnings = []
    if _session_snapshot is None:
        try:
            session = _load_session_from_workspace(workspace)
        except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
            return {
                "errors": ["lightweight session is invalid: %s" % exc],
                "warnings": [],
                "stats": {"lightweight_session": "invalid"},
            }
    else:
        session = _session_snapshot
    if not isinstance(state, dict):
        return {
            "errors": ["study_state.json must contain a JSON object"],
            "warnings": [],
            "stats": {"lightweight_session": "state_invalid"},
        }
    current_phase = state.get("current_phase")
    active = [
        batch for batch in session["batches"]
        if batch["status"] in ("planned", "visual_ready")
    ]
    if active and active[0]["chapter"] != current_phase:
        errors.append(
            "active lightweight batch chapter=%d differs from current_phase=%r"
            % (active[0]["chapter"], current_phase)
        )
    event_index = {}
    phase_evidence = state.get("phase_evidence")
    if phase_evidence is None:
        phase_evidence = {}
    if not isinstance(phase_evidence, dict):
        errors.append("study_state.phase_evidence must be an object")
        phase_evidence = {}
    for phase_key, record in phase_evidence.items():
        if not isinstance(record, dict):
            errors.append("study_state.phase_evidence[%r] must be an object" % phase_key)
            continue
        events = record.get("lightweight_batches")
        if events is None:
            events = []
        if not isinstance(events, list):
            errors.append(
                "study_state.phase_evidence[%r].lightweight_batches must be an array"
                % phase_key
            )
            continue
        for event in events:
            if isinstance(event, dict) and isinstance(event.get("batch_id"), str):
                event_index.setdefault(event["batch_id"], []).append(
                    (phase_key, event)
                )
    if active:
        current_record = phase_evidence.get(str(current_phase))
        if (isinstance(current_record, dict)
                and any(current_record.get(field) is not None for field in (
                    "status", "completed_at", "completion_mode",
                ))):
            errors.append(
                "active lightweight work conflicts with a completed current-phase "
                "progress record; finish/reopen the coordinated transition"
            )
    counts = {
        "planned": 0, "visual_ready": 0, "taught": 0,
        "abandoned": 0, "superseded": 0, "stale": 0,
        "unchecked_historical": 0,
    }
    stale_batches = []
    source_revision_cache = {}
    asset_digest_cache = {}
    for batch in session["batches"]:
        counts[batch["status"]] += 1
        live_taught = (
            live_current_taught and batch["status"] == "taught"
            and batch["chapter"] == current_phase
        )
        if batch["status"] in ("planned", "visual_ready"):
            try:
                _assert_batch_sources_current(
                    session["materials"], batch, exact=exact_live,
                    cache=source_revision_cache,
                )
                if _visual_assets_stale(workspace, batch, exact=exact_live):
                    raise LightweightSessionError("visual evidence changed or is unreadable")
            except (OSError, TypeError, ValueError, LightweightSessionError) as exc:
                counts["stale"] += 1
                stale_batches.append(batch["batch_id"])
                errors.append("batch %s is stale: %s" % (batch["batch_id"], exc))
        elif batch["status"] == "taught" and batch["chapter"] != current_phase:
            counts["unchecked_historical"] += 1
        linked = event_index.pop(batch["batch_id"], [])
        if batch["status"] == "superseded":
            preserved = batch["supersession"]["preserved_progress_event"]
            if len(linked) != 1 or linked[0][1] != preserved:
                errors.append(
                    "superseded batch %s must retain exactly its preserved progress event"
                    % batch["batch_id"]
                )
        elif batch["status"] == "taught":
            if len(linked) != 1:
                errors.append(
                    "taught batch %s must have exactly one study_state progress event"
                    % batch["batch_id"]
                )
            else:
                phase_key, event = linked[0]
                try:
                    phase = int(phase_key)
                except (TypeError, ValueError):
                    phase = -1
                if phase != batch["chapter"] or event != _progress_event(batch):
                    problem = "progress event disagrees with its immutable batch receipt"
                elif live_taught:
                    problem = _progress_event_error_in_session(
                        workspace, session, phase, event,
                        source_revision_cache=source_revision_cache,
                        asset_digest_cache=asset_digest_cache,
                        exact_live=exact_live,
                    )
                else:
                    problem = None
                if problem:
                    if live_taught:
                        counts["stale"] += 1
                        stale_batches.append(batch["batch_id"])
                    errors.append("batch %s progress event: %s" % (
                        batch["batch_id"], problem
                    ))
        elif linked:
            errors.append(
                "unfinished batch %s must not have a completion progress event"
                % batch["batch_id"]
            )
    for batch_id in sorted(event_index):
        errors.append(
            "study_state references an unknown lightweight batch %s" % batch_id
        )
    if active:
        warnings.append(
            "lightweight batch %s is still %s" % (
                active[0]["batch_id"], active[0]["status"]
            )
        )
    current_counts = {
        "planned": 0, "visual_ready": 0, "taught": 0, "abandoned": 0,
        "superseded": 0,
    }
    for batch in session["batches"]:
        if batch["chapter"] == current_phase:
            current_counts[batch["status"]] += 1
    return {
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "lightweight_session": "invalid" if errors else "ready",
            "lightweight_batches": counts,
            "lightweight_current_phase_batches": current_counts,
            "lightweight_active_batch": active[0]["batch_id"] if active else None,
            "lightweight_stale_batches": sorted(set(stale_batches)),
            "lightweight_live_check": "exact" if exact_live else "metadata_only",
        },
    }


def _legacy_has_durable_evidence(raw, state):
    batches = raw.get("batches") if isinstance(raw, dict) else None
    if not isinstance(batches, list):
        return True
    for batch in batches:
        if not isinstance(batch, dict):
            return True
        if (batch.get("status") not in (None, "planned")
                or batch.get("visual_receipt") is not None
                or batch.get("teaching_receipt") is not None
                or batch.get("abandonment") is not None):
            return True
    evidence = state.get("phase_evidence") if isinstance(state, dict) else None
    if evidence is None:
        return False
    if not isinstance(evidence, dict):
        return True
    for record in evidence.values():
        if not isinstance(record, dict):
            return True
        events = record.get("lightweight_batches")
        if events not in (None, []):
            return True
    return False


def _evidence_free_schema1_reinitialization(workspace, materials, raw):
    if (not isinstance(raw, dict) or raw.get("schema_version") != 1
            or raw.get("processing_mode") != "lightweight"
            or not isinstance(raw.get("workspace"), str)
            or not isinstance(raw.get("materials"), str)
            or _resolved(raw["workspace"]) != workspace
            or _resolved(raw["materials"]) != materials):
        raise LightweightSessionError(
            "only an exact-pair schema-1 lightweight session can be migrated"
        )
    state_path = safe_workspace_entry(workspace, STATE_PATH)
    state = _strict_json(state_path, "study_state.json")
    if _legacy_has_durable_evidence(raw, state):
        raise LightweightSessionError(
            "schema-1 session contains durable or declared evidence; it cannot be "
            "silently reinterpreted"
        )
    now = _utc_now()
    unsigned = {
        "from_schema": 1,
        "migration": "evidence_free_reinitialization",
        "prior_session_sha256": _sha256_bytes(
            _canonical_json(raw).encode("utf-8")
        ),
        "created_at": now,
    }
    migration = dict(unsigned)
    migration["receipt_id"] = "lw-migrate-" + hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": SCHEMA_VERSION,
        "session_type": "on_demand_visual",
        "processing_mode": "lightweight",
        "workspace": workspace,
        "materials": materials,
        "created_at": now,
        "updated_at": now,
        "source_inventory": _source_inventory(materials),
        "batches": [],
        "quiz_bank_baseline": _quiz_bank_stat_baseline(workspace),
        "migration_history": [migration],
    }


def cmd_init(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    gate = _start_gate(workspace, materials)
    with workspace_publication_lock(workspace):
        gate = _start_gate(workspace, materials)
        path = _session_path(workspace)
        existed = os.path.isfile(path)
        if existed:
            try:
                session = _load_session(workspace, materials)
            except LightweightSessionError:
                if is_link_or_reparse(path):
                    raise LightweightSessionError(
                        "legacy lightweight session is link-backed and cannot be migrated"
                    )
                raw = _strict_json(path, "lightweight session")
                if not args.migrate_session:
                    if isinstance(raw, dict) and raw.get("schema_version") == 1:
                        raise LightweightSessionError(
                            "schema-1 lightweight session requires explicit "
                            "init --migrate-session; migration is allowed only when "
                            "no durable evidence exists"
                        )
                    raise
                session = _evidence_free_schema1_reinitialization(
                    workspace, materials, raw
                )
                asset_directory = _ensure_asset_directory(workspace)
                _save_session(workspace, session)
                return {
                    "process_success": True,
                    "created": False,
                    "migrated": True,
                    "workspace": workspace,
                    "materials": materials,
                    "processing_mode": "lightweight",
                    "asset_directory": asset_directory,
                    "source_count": len(session["source_inventory"]),
                    "batch_count": 0,
                    "migration_receipt": session["migration_history"][-1],
                    "next_action": "plan only the pages for the current learning topic",
                }
            _reject_any_legacy_active_strategy(session, "init/refresh")
            asset_directory = _ensure_asset_directory(workspace)
            if not args.refresh:
                return {
                    "process_success": True,
                    "created": False,
                    "workspace": workspace,
                    "materials": materials,
                    "processing_mode": "lightweight",
                    "asset_directory": asset_directory,
                    "source_count": len(session["source_inventory"]),
                    "batch_count": len(session["batches"]),
                    "next_action": "plan only the pages for the current learning topic",
                }
            refreshed = _source_inventory(materials)
            old_by_key = {
                _material_path_key(row["relative_path"]): row
                for row in session["source_inventory"]
            }
            for candidate in refreshed:
                prior = old_by_key.get(
                    _material_path_key(candidate["relative_path"])
                )
                if (prior is not None
                        and prior.get("size") == candidate["size"]
                        and prior.get("mtime_ns") == candidate["mtime_ns"]
                        and prior.get("content_identity")
                        == candidate["content_identity"]
                        and HEX64.fullmatch(
                            str(prior.get("content_sha256") or ""))):
                    candidate["content_sha256"] = prior["content_sha256"]
            refreshed_ids = {row["source_id"] for row in refreshed}
            used_ids = {batch["source_id"] for batch in session["batches"]}
            used_ids.update(
                dependency["source_id"]
                for batch in session["batches"]
                for dependency in batch["answer_dependencies"]
            )
            refreshed_by_key = {
                _material_path_key(row["relative_path"]): row for row in refreshed
            }
            for row in session["source_inventory"]:
                if row["source_id"] not in used_ids or row["source_id"] in refreshed_ids:
                    continue
                replacement = refreshed_by_key.get(
                    _material_path_key(row["relative_path"])
                )
                if replacement is not None:
                    refreshed.remove(replacement)
                refreshed.append(row)
            refreshed.sort(key=lambda row: row["relative_path"].casefold())
            session["source_inventory"] = refreshed
        else:
            now = _utc_now()
            session = {
                "schema_version": SCHEMA_VERSION,
                "session_type": "on_demand_visual",
                "processing_mode": "lightweight",
                "workspace": workspace,
                "materials": materials,
                "created_at": now,
                "updated_at": now,
                "source_inventory": _source_inventory(materials),
                "batches": [],
                # Stat only: startup never opens/parses/hashes quiz content.
                "quiz_bank_baseline": _quiz_bank_stat_baseline(workspace),
                "migration_history": [],
            }
            asset_directory = _ensure_asset_directory(workspace)
        _save_session(workspace, session)
    return {
        "process_success": True,
        "created": not existed,
        "workspace": workspace,
        "materials": materials,
        "processing_mode": gate["processing_mode"],
        "asset_directory": asset_directory,
        "source_count": len(session["source_inventory"]),
        "batch_count": len(session["batches"]),
        "startup_work": "inventory_only_no_content_read",
        "next_action": "plan only the pages for the current learning topic",
    }


def _status_file_generation(path, label):
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise LightweightSessionError("%s is unreadable" % label) from exc
    if is_link_or_reparse(path) or not os.path.isfile(path):
        raise LightweightSessionError("%s must be a regular non-link file" % label)
    return (
        int(value.st_dev), int(value.st_ino), int(value.st_size),
        int(value.st_mtime_ns),
    )


def _read_status_snapshot(workspace, materials, verify_live):
    gate = _start_gate(workspace, materials)
    session = _load_session(workspace, materials)
    state = _strict_json(
        safe_workspace_entry(workspace, STATE_PATH), "study_state.json"
    )
    health = workspace_health(
        workspace, state, live_current_taught=True,
        exact_live=bool(verify_live), _session_snapshot=session,
    )
    return gate, session, state, health


def _status_lock_generation(lock_path):
    if not os.path.lexists(lock_path):
        return ("absent",)
    return ("present",) + _status_file_generation(
        lock_path, ".study_state.lock"
    )


def _status_item_teaching_evidence(visual, teaching, batch_status):
    """Summarize crop-scoped teaching evidence separately from parent-page risk."""

    if not visual:
        return {
            "answer_taint_status": "not_visual_reviewed",
            "answer_taint_scope": "item_components",
            "item_crop_review_status": "not_visual_reviewed",
            "item_component_count": 0,
            "official_answer_component_count": 0,
            "teaching_publication_status": "not_published",
            "item_teaching_status": "not_visual_reviewed",
        }
    if (_visual_receipt_is_legacy(visual)
            or visual.get("schema_version") != VISUAL_RECEIPT_SCHEMA_VERSION):
        publication = (
            "published_superseded_history"
            if teaching and batch_status == "superseded"
            else "published_taught"
            if teaching and batch_status == "taught"
            else "not_published"
        )
        return {
            "answer_taint_status": "historical_unclassified",
            "answer_taint_scope": "item_components",
            "item_crop_review_status": "historical_unclassified",
            "item_component_count": None,
            "official_answer_component_count": None,
            "teaching_publication_status": publication,
            "item_teaching_status": "historical_unclassified",
        }

    items = visual.get("teaching_items") or []
    prompt_count = sum(
        len(item.get("prompt_components") or [])
        for item in items if isinstance(item, dict)
    )
    answer_count = sum(
        len(item.get("answer_components") or [])
        for item in items if isinstance(item, dict)
    )
    answer_status = (
        "official_answer_components_clean"
        if answer_count else "no_answer_components"
    )
    publication = (
        "published_superseded_history"
        if teaching and batch_status == "superseded"
        else "published_taught"
        if teaching and batch_status == "taught"
        else "not_published"
    )
    phase = (
        "published" if publication == "published_taught"
        else "superseded_history"
        if publication == "published_superseded_history"
        else "reviewed"
    )
    return {
        # Every schema-3 component was independently crop-reviewed and the
        # stored receipt validator already proved student_attempt_present=false.
        "answer_taint_status": answer_status,
        "answer_taint_scope": "item_components",
        "item_crop_review_status": "clean_declared_scope",
        "item_component_count": prompt_count + answer_count,
        "official_answer_component_count": answer_count,
        "teaching_publication_status": publication,
        "item_teaching_status": "%s_%s" % (phase, answer_status),
    }


def cmd_status(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    lock_path = safe_workspace_entry(workspace, ".study_state.lock")
    session_path = _session_path(workspace)
    state_path = safe_workspace_entry(workspace, STATE_PATH)
    snapshot_mode = None
    # Status never opens the coordination lock: doing so would require write
    # permission on Windows.  It instead accepts only a generation-stable
    # session/state/lock snapshot.  A paused two-file publication can at worst
    # produce fail-closed health errors; it cannot be reported as healthy.
    for _attempt in range(3):
        before = (
            _status_file_generation(session_path, "lightweight session"),
            _status_file_generation(state_path, "study_state.json"),
            _status_lock_generation(lock_path),
        )
        gate, session, state, health = _read_status_snapshot(
            workspace, materials, args.verify_live
        )
        after = (
            _status_file_generation(session_path, "lightweight session"),
            _status_file_generation(state_path, "study_state.json"),
            _status_lock_generation(lock_path),
        )
        if before == after:
            snapshot_mode = "read_only_generation_snapshot"
            break
    if snapshot_mode is None:
        raise ConflictError("status could not capture a stable read-only snapshot")

    # Formatting below is deliberately outside the lock and performs no writes.
    if snapshot_mode is not None:
        counts = health["stats"].get("lightweight_batches", {})
        stale_batches = health["stats"].get("lightweight_stale_batches", [])
        recent = []
        for batch in session["batches"][-12:]:
            visual = batch.get("visual_receipt") or {}
            teaching = batch.get("teaching_receipt") or {}
            provenance_counts = {
                value: 0 for value in sorted(ANSWER_PROVENANCE_VALUES)
            }
            for page in (visual.get("pages") or []) + (
                    visual.get("dependency_pages") or []):
                provenance = page.get("answer_provenance")
                if provenance in provenance_counts:
                    provenance_counts[provenance] += 1
            page_taint_status = (
                "not_visual_reviewed"
                if not visual
                else "legacy_unclassified"
                if _visual_receipt_is_legacy(visual)
                else "student_attempt_or_unknown_present"
                if (provenance_counts["student_attempt"]
                    or provenance_counts["unknown"])
                else "official_or_not_applicable"
            )
            item_evidence = _status_item_teaching_evidence(
                visual, teaching, batch["status"]
            )
            recent_batch = {
                "batch_id": batch["batch_id"],
                "chapter": batch["chapter"],
                "source_path": batch["source_path"],
                "pages": list(batch["pages"]),
                "answer_dependency_sources": len(batch["answer_dependencies"]),
                "answer_dependency_pages": sum(
                    len(value["pages"]) for value in batch["answer_dependencies"]
                ),
                "status": batch["status"],
                "updated_at": batch["updated_at"],
                "visual_receipt_id": visual.get("receipt_id"),
                "schema2_quarantined": bool(
                    batch["status"] == "visual_ready"
                    and _visual_receipt_is_schema2(visual)
                ),
                "required_action": (
                    "abandon this read-only schema-2 attempt, then plan a new schema-3 attempt"
                    if (batch["status"] == "visual_ready"
                        and _visual_receipt_is_schema2(visual))
                    else None
                ),
                "teaching_receipt_id": teaching.get("receipt_id"),
                "inspected_pages": teaching.get("inspected_pages"),
                "taught_item_ids": teaching.get("taught_item_ids"),
                # Compatibility count: this is intentionally page-scoped, not
                # the verdict for the reviewed item crops used in teaching.
                "answer_provenance_counts": provenance_counts,
                "full_page_answer_provenance_counts": provenance_counts,
                "full_page_answer_taint_status": page_taint_status,
                "full_page_context_usage": (
                    "ordinary_visual_context"
                    if page_taint_status == "official_or_not_applicable"
                    else "locator_detail_only"
                ),
                "supersedes_batch_id": batch.get("supersedes_batch_id"),
                "successor_batch_id": (
                    (batch.get("supersession") or {}).get("successor_batch_id")
                ),
                "stale": batch["batch_id"] in set(stale_batches),
            }
            recent_batch.update(item_evidence)
            recent.append(recent_batch)
    return {
        "process_success": True,
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "answer_taint_contract_version": 2,
        "workspace": workspace,
        "materials": materials,
        "processing_mode": gate["processing_mode"],
        "source_count": len(session["source_inventory"]),
        "batch_counts": counts,
        "stale_batches": stale_batches,
        "health_errors": health["errors"],
        "health_warnings": health["warnings"],
        "live_check": health["stats"].get("lightweight_live_check"),
        "snapshot_mode": snapshot_mode,
        "recent_batches": recent,
        "study_state_path": str(safe_workspace_entry(workspace, STATE_PATH)),
        "session_path": str(_session_path(workspace)),
    }


def cmd_plan(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    pages = _parse_pages(args.pages)
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        _reject_any_legacy_active_strategy(session, "planning")
        state_path = safe_workspace_entry(workspace, STATE_PATH)
        state = _strict_json(state_path, "study_state.json")
        current_phase = state.get("current_phase") if isinstance(state, dict) else None
        if current_phase != args.chapter:
            raise LightweightSessionError(
                "lightweight planning is limited to current_phase=%r; requested chapter=%d"
                % (current_phase, args.chapter)
            )
        try:
            import update_progress as progress
        except (ImportError, OSError) as exc:
            raise LightweightSessionError(
                "cannot load progress publisher for lightweight planning"
            ) from exc
        shape_errors = progress._phase_evidence_shape_errors(state)
        if shape_errors:
            raise LightweightSessionError(
                "study_state phase evidence is damaged: %s" % shape_errors[0]
            )
        phase_evidence = state.get("phase_evidence")
        if phase_evidence is None:
            phase_evidence = {}
            state["phase_evidence"] = phase_evidence
        if not isinstance(phase_evidence, dict):
            raise LightweightSessionError(
                "study_state phase_evidence must be an object"
            )
        phase_record = phase_evidence.get(str(args.chapter))
        if phase_record is None:
            phase_record = {}
            phase_evidence[str(args.chapter)] = phase_record
        if not isinstance(phase_record, dict):
            raise LightweightSessionError(
                "current phase evidence record must be an object"
            )
        checklist = state.get("phase_checklist")
        if checklist is None:
            checklist = []
            state["phase_checklist"] = checklist
        if not isinstance(checklist, list):
            raise LightweightSessionError("study_state phase_checklist must be an array")
        phase_rows = []
        for index, checklist_row in enumerate(checklist):
            if (not isinstance(checklist_row, dict)
                    or not isinstance(checklist_row.get("text"), str)
                    or not checklist_row["text"].strip()
                    or (checklist_row.get("done") is not None
                        and type(checklist_row["done"]) is not bool)):
                raise LightweightSessionError(
                    "study_state phase_checklist[%d] is invalid" % index
                )
            if progress.phase_number_from_check(checklist_row["text"]) == args.chapter:
                phase_rows.append(checklist_row)
        if len(phase_rows) > 1:
            raise LightweightSessionError(
                "phase_checklist contains multiple rows for the current phase"
            )
        completion_fields = ("status", "completed_at", "completion_mode")
        completion_presence = [
            phase_record.get(field) is not None for field in completion_fields
        ]
        if any(completion_presence) and not all(completion_presence):
            raise LightweightSessionError(
                "current phase completion record is partial; repair it before planning"
            )
        completed_record = all(completion_presence)
        checklist_completed = bool(phase_rows and phase_rows[0].get("done") is True)
        if completed_record and (
                phase_record.get("completion_mode") != "lightweight"
                or not checklist_completed):
            raise LightweightSessionError(
                "current phase completion badge disagrees with its lightweight checklist"
            )
        completion_badge_present = completed_record or checklist_completed
        row = _source_row(session, args.source)
        source = _material_file(materials, row["relative_path"])
        suffix = os.path.splitext(source)[1].lower()
        if suffix != ".pdf" and suffix not in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES:
            raise LightweightSessionError(
                "lightweight mode plans PDF pages or definitely single-frame "
                "PNG/JPEG/BMP only; switch to full mode or an explicit host "
                "frame-count path for GIF/WebP/TIFF and other formats"
            )
        if suffix in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES and pages != [1]:
            raise LightweightSessionError("a standalone image has only page-equivalent 1")
        # Planning is a publication boundary: stream the selected source once.
        # Inventory metadata is only a startup/status optimization and cannot
        # authorize a same-size/same-mtime replacement here.
        revision = _revision(source, row, force_hash=True)
        base_identity = _batch_id(row["source_id"], revision, args.chapter, pages)
        logical_slice = [
            batch for batch in _current_batches(session, args.chapter)
            if batch["source_id"] == row["source_id"] and batch["pages"] == pages
        ]
        if logical_slice and logical_slice[0]["source_revision"] != revision:
            if logical_slice[0]["status"] == "taught":
                raise LightweightSessionError(
                    "this taught slice binds an older source revision; use "
                    "replace-taught with a concrete reason"
                )
            raise LightweightSessionError(
                "this active slice binds an older source revision; abandon it before "
                "planning the current revision"
            )
        same_slice = [
            batch for batch in session["batches"]
            if (batch["source_id"] == row["source_id"]
                and batch["source_revision"] == revision
                and batch["chapter"] == args.chapter
                and batch["pages"] == pages)
        ]
        reusable = [
            batch for batch in same_slice
            if batch["status"] not in ("abandoned", "superseded")
        ]
        if reusable:
            if len(reusable) != 1:
                raise LightweightSessionError(
                    "the same source/page slice has multiple non-abandoned batches"
                )
            batch = reusable[0]
            created = False
        else:
            active = [
                candidate for candidate in session["batches"]
                if candidate["status"] in ("planned", "visual_ready")
            ]
            if active:
                raise LightweightSessionError(
                    "finish the active lightweight batch %s before planning another"
                    % active[0]["batch_id"]
                )
            if len(session["batches"]) >= MAX_BATCHES:
                raise LightweightSessionError("lightweight batch ledger reached its limit")
            identity = (
                base_identity if not same_slice
                else "%s-r%d" % (base_identity, len(same_slice) + 1)
            )
            now = _utc_now()
            batch = {
                "batch_id": identity,
                "chapter": args.chapter,
                "source_id": row["source_id"],
                "source_path": row["relative_path"],
                "source_revision": revision,
                "pages": pages,
                "answer_dependencies": [],
                "answer_dependency_history": [],
                "status": "planned",
                "created_at": now,
                "updated_at": now,
                "visual_receipt": None,
                "teaching_receipt": None,
                "abandonment": None,
                "supersedes_batch_id": None,
                "supersession": None,
                "token_strategy": _token_strategy(component_scoped=True),
            }
            session["batches"].append(batch)
            _save_session(workspace, session)
            created = True
        reopen_needed = (
            completion_badge_present
            and (created or batch["status"] in ("planned", "visual_ready"))
        )
        completion_reopened = False
        if reopen_needed:
            for field in completion_fields:
                phase_record.pop(field, None)
            if phase_rows:
                phase_rows[0]["done"] = False
            phase_record["updated_at"] = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            )
            try:
                progress.save(
                    workspace, state,
                    "lightweight plan %s reopened phase completion"
                    % batch["batch_id"],
                    quiet=True,
                )
            except SystemExit as exc:
                raise LightweightSessionError(
                    "batch plan is published but completion reopening failed; "
                    "rerun the exact same plan command"
                ) from exc
            completion_reopened = True
    return {
        "process_success": True,
        "created": created,
        "completion_reopened": completion_reopened,
        "batch": batch,
        "visual_work_order": {
            "source": source,
            "pages": pages,
            "render_with": "host_native_pdf_or_image_visual_tool",
            "do_not_run": [
                "ingest_course", "OCR", "Docling", "MinerU", "Study Guide render",
            ],
            "contact_sheet_groups": _contact_sheet_groups(pages),
            "contact_sheet_limit": (
                "for a multi-page batch, overview sheets must partition every page exactly "
                "once; reopen only dense pages/items for detail"
            ),
            "required_before_teaching": [
                "inspect every requested page visually",
                "save one visible page asset per page",
                "save one or more target/context-scoped prompt components for every teaching item",
                "enumerate stable teaching_item_ids before teaching",
                "repeat an item ID on every primary page supplying one of its prompt components",
                "classify every teaching item honestly as text, figure, or mixed",
                "declare required_context_ids and exact allowed_detected_item_ids per component",
                "ensure figure/mixed items include a visible figure/diagram/table component",
                "classify answer provenance on every primary/dependency page",
                "run one independent model_vision crop_review for every prompt/answer component",
                "register only the exact extra answer page when a solution lives elsewhere",
                "cover every registered official-solution page with answer components",
                "keep answer components hidden until the solution/review stage",
            ],
            "answer_dependency_limit": {
                "sources": MAX_ANSWER_DEPENDENCY_SOURCES,
                "pages_total": MAX_ANSWER_DEPENDENCY_PAGES,
            },
            "output_detail": "do not shorten explanations or worked solutions",
        },
        "visual_manifest_template": _visual_manifest_template(batch),
        "teaching_item_contract": _teaching_item_contract(),
        "next_action": (
            "inspect primary pages; adjust exact external solution pages while planned, "
            "then import one schema-3 component-scoped visual receipt"
        ),
    }


def cmd_register_answer_dependency(args):
    """Bind only the exact extra source pages needed to locate an official answer."""
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    requested_pages = _parse_pages(args.pages)
    if len(requested_pages) > MAX_ANSWER_DEPENDENCY_PAGES:
        raise LightweightSessionError(
            "one answer dependency request is limited to %d pages"
            % MAX_ANSWER_DEPENDENCY_PAGES
        )
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        _reject_legacy_active_strategy(batch, "answer-dependency registration")
        if batch["status"] != "planned":
            raise LightweightSessionError(
                "answer dependencies may be registered only before visual evidence "
                "is frozen; abandon visual_ready and create a new attempt if needed"
            )
        state = _strict_json(
            safe_workspace_entry(workspace, STATE_PATH), "study_state.json"
        )
        if not isinstance(state, dict) or state.get("current_phase") != batch["chapter"]:
            raise LightweightSessionError(
                "answer dependency registration is limited to the active learning phase"
            )
        row = _source_row(session, args.source)
        source = _material_file(materials, row["relative_path"])
        suffix = os.path.splitext(source)[1].lower()
        if suffix != ".pdf" and suffix not in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES:
            raise LightweightSessionError(
                "answer dependencies support PDF or definitely single-frame "
                "PNG/JPEG/BMP only; GIF/WebP/TIFF require full mode or an explicit "
                "host frame-count path"
            )
        if suffix in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES and requested_pages != [1]:
            raise LightweightSessionError(
                "a standalone answer image has only page-equivalent 1"
            )
        if (row["source_id"] == batch["source_id"]
                and set(requested_pages).intersection(batch["pages"])):
            raise LightweightSessionError(
                "a primary page is already rendered; bind an answer crop to that page "
                "without registering it again"
            )
        revision = _revision(source, row, force_hash=True)
        existing = [
            value for value in batch["answer_dependencies"]
            if value["source_id"] == row["source_id"]
        ]
        if len(existing) > 1:
            raise LightweightSessionError(
                "batch contains ambiguous answer dependency source bindings"
            )
        changed = False
        previous_dependency = None
        if existing:
            dependency = existing[0]
            if (dependency["source_path"] != row["relative_path"]
                    or dependency["source_revision"] != revision):
                raise LightweightSessionError(
                    "answer source changed after registration; abandon this batch and "
                    "create a new attempt"
                )
            combined = sorted(set(dependency["pages"]).union(requested_pages))
            if combined != dependency["pages"]:
                previous_dependency = _dependency_event_row(dependency)
                dependency["pages"] = combined
                changed = True
        else:
            if len(batch["answer_dependencies"]) >= MAX_ANSWER_DEPENDENCY_SOURCES:
                raise LightweightSessionError(
                    "answer dependency source limit reached; split the learning scope"
                )
            dependency = {
                "source_id": row["source_id"],
                "source_path": row["relative_path"],
                "source_revision": revision,
                "pages": list(requested_pages),
                "registered_at": _utc_now(),
            }
            batch["answer_dependencies"].append(dependency)
            batch["answer_dependencies"].sort(
                key=lambda value: (
                    value["source_path"].casefold(), value["source_path"]
                )
            )
            changed = True
        total_pages = sum(
            len(value["pages"]) for value in batch["answer_dependencies"]
        )
        if total_pages > MAX_ANSWER_DEPENDENCY_PAGES:
            raise LightweightSessionError(
                "one batch may inspect at most %d extra answer pages; split the scope"
                % MAX_ANSWER_DEPENDENCY_PAGES
            )
        if changed:
            _record_dependency_event(
                batch,
                "registered" if previous_dependency is None else "pages_expanded",
                previous_dependency, dependency,
                ("registered exact answer pages additively"
                 if previous_dependency is None
                 else "expanded exact answer pages additively"),
            )
            batch["updated_at"] = _utc_now()
            _assert_batch_sources_current(materials, batch, exact=True)
            _save_session(workspace, session)
        else:
            _assert_batch_sources_current(materials, batch, exact=True)
    return {
        "process_success": True,
        "changed": changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "registered_dependency": dependency,
        "dependency_work_order": {
            "source": source,
            "pages": list(dependency["pages"]),
            "purpose": "answer_locator_only",
            "render_with": "host_native_pdf_or_image_visual_tool",
            "instruction": (
                "render only these registered pages, crop only declared-scope answer "
                "components, "
                "and use the full page only for the detail/locator model call"
            ),
        },
        "visual_manifest_template": _visual_manifest_template(batch),
        "teaching_item_contract": _teaching_item_contract(),
        "next_action": (
            "finish declared-scope prompt/answer component crops and import one source-qualified "
            "visual receipt"
        ),
    }


def cmd_set_answer_dependency(args):
    """Replace one registered dependency's exact pages and bind its live revision."""
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    requested_pages = _parse_pages(args.pages)
    reason = " ".join(str(args.reason or "").split())
    if not 5 <= len(reason) <= 500:
        raise LightweightSessionError(
            "dependency replacement reason must be a concrete 5-500 character explanation"
        )
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        _reject_legacy_active_strategy(batch, "answer-dependency replacement")
        if batch["status"] != "planned":
            raise LightweightSessionError(
                "answer dependency pages may be replaced only while the batch is planned"
            )
        state = _strict_json(
            safe_workspace_entry(workspace, STATE_PATH), "study_state.json"
        )
        if not isinstance(state, dict) or state.get("current_phase") != batch["chapter"]:
            raise LightweightSessionError(
                "answer dependency replacement is limited to the active learning phase"
            )
        row = _source_row(session, args.source)
        matches = [
            value for value in batch["answer_dependencies"]
            if value["source_id"] == row["source_id"]
        ]
        if len(matches) != 1:
            raise LightweightSessionError(
                "set-answer-dependency requires exactly one existing binding for that source"
            )
        source = _material_file(materials, row["relative_path"])
        suffix = os.path.splitext(source)[1].lower()
        if suffix != ".pdf" and suffix not in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES:
            raise LightweightSessionError(
                "answer dependencies support PDF or definitely single-frame PNG/JPEG/BMP only"
            )
        if suffix in LIGHTWEIGHT_SINGLE_RASTER_SUFFIXES and requested_pages != [1]:
            raise LightweightSessionError(
                "a standalone answer image has only page-equivalent 1"
            )
        if (row["source_id"] == batch["source_id"]
                and set(requested_pages).intersection(batch["pages"])):
            raise LightweightSessionError(
                "a primary page cannot also be an answer dependency"
            )
        other_page_count = sum(
            len(value["pages"]) for value in batch["answer_dependencies"]
            if value["source_id"] != row["source_id"]
        )
        if other_page_count + len(requested_pages) > MAX_ANSWER_DEPENDENCY_PAGES:
            raise LightweightSessionError(
                "replacement exceeds the batch answer dependency page limit"
            )
        previous = _dependency_event_row(matches[0])
        live_revision = _revision(source, row, force_hash=True)
        semantically_unchanged = (
            previous["source_revision"] == live_revision
            and previous["pages"] == requested_pages
        )
        current = previous if semantically_unchanged else {
            "source_id": row["source_id"],
            "source_path": row["relative_path"],
            "source_revision": live_revision,
            "pages": list(requested_pages),
            "registered_at": _utc_now(),
        }
        changed = not semantically_unchanged
        if changed:
            index = batch["answer_dependencies"].index(matches[0])
            batch["answer_dependencies"][index] = current
            batch["answer_dependencies"].sort(
                key=lambda value: (
                    value["source_path"].casefold(), value["source_path"]
                )
            )
            event = _record_dependency_event(
                batch, "pages_replaced", previous, current, reason
            )
            batch["updated_at"] = _utc_now()
            _assert_batch_sources_current(materials, batch, exact=True)
            _save_session(workspace, session)
        else:
            event = None
            _assert_batch_sources_current(materials, batch, exact=True)
    return {
        "process_success": True,
        "changed": changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "registered_dependency": current,
        "dependency_event": event,
        "visual_manifest_template": _visual_manifest_template(batch),
        "next_action": "render only the replacement dependency pages, then record schema-3 visual evidence",
    }


def cmd_remove_answer_dependency(args):
    """Remove one planned dependency source while preserving an audit receipt."""
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    reason = " ".join(str(args.reason or "").split())
    if not 5 <= len(reason) <= 500:
        raise LightweightSessionError(
            "dependency removal reason must be a concrete 5-500 character explanation"
        )
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        _reject_legacy_active_strategy(batch, "answer-dependency removal")
        if batch["status"] != "planned":
            raise LightweightSessionError(
                "answer dependencies may be removed only while the batch is planned"
            )
        state = _strict_json(
            safe_workspace_entry(workspace, STATE_PATH), "study_state.json"
        )
        if not isinstance(state, dict) or state.get("current_phase") != batch["chapter"]:
            raise LightweightSessionError(
                "answer dependency removal is limited to the active learning phase"
            )
        row = _source_row(session, args.source)
        matches = [
            value for value in batch["answer_dependencies"]
            if value["source_id"] == row["source_id"]
        ]
        if not matches:
            prior_removal = next((
                event for event in reversed(_dependency_history(batch))
                if event["source_id"] == row["source_id"]
            ), None)
            if prior_removal is None or prior_removal["event_type"] != "removed":
                raise LightweightSessionError(
                    "remove-answer-dependency requires a currently bound source or "
                    "an exact retry of its last audited removal"
                )
            if prior_removal["reason"] != reason:
                raise LightweightSessionError(
                    "this dependency is already removed; an idempotent retry must "
                    "repeat the exact recorded reason"
                )
            previous = prior_removal["previous"]
            event = None
            changed = False
            _assert_batch_sources_current(materials, batch, exact=True)
        elif len(matches) != 1:
            raise LightweightSessionError(
                "remove-answer-dependency requires exactly one existing source binding"
            )
        else:
            previous = _dependency_event_row(matches[0])
            batch["answer_dependencies"].remove(matches[0])
            event = _record_dependency_event(
                batch, "removed", previous, None, reason
            )
            batch["updated_at"] = _utc_now()
            _assert_batch_sources_current(materials, batch, exact=True)
            _save_session(workspace, session)
            changed = True
    return {
        "process_success": True,
        "changed": changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "removed_dependency": previous,
        "dependency_event": event,
        "visual_manifest_template": _visual_manifest_template(batch),
        "next_action": "record schema-3 visual evidence only for the remaining exact locations",
    }


def _visual_receipt(workspace, batch, manifest_path):
    manifest = _strict_json(manifest_path, "visual manifest")
    expected = {
        "schema_version", "batch_id", "source_sha256", "inspection_method",
        "pages", "teaching_items", "dependency_pages", "contact_sheets",
        "model_calls",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise LightweightSessionError("visual manifest fields are invalid")
    if (manifest.get("schema_version") != VISUAL_RECEIPT_SCHEMA_VERSION
            or manifest.get("batch_id") != batch["batch_id"]
            or manifest.get("source_sha256") != batch["source_revision"]["sha256"]
            or manifest.get("inspection_method") != "model_visual"):
        raise LightweightSessionError("visual manifest identity/method is invalid")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != len(batch["pages"]):
        raise LightweightSessionError("visual manifest must cover every planned page once")
    page_numbers = []
    normalized_pages = []
    contact_paths = set()
    contact_sheets = manifest.get("contact_sheets")
    if not isinstance(contact_sheets, list):
        raise LightweightSessionError("contact_sheets must be an array")
    if len(batch["pages"]) == 1 and contact_sheets:
        raise LightweightSessionError(
            "single-page batches must use the page image directly, without a contact sheet"
        )
    normalized_contacts = []
    for contact in contact_sheets:
        if (not isinstance(contact, dict)
                or set(contact) != {"asset", "pages", "purpose"}
                or contact.get("purpose") != "overview_only"):
            raise LightweightSessionError("contact sheet contract is invalid")
        (asset, absolute, asset_width, asset_height, contact_sha256,
         contact_size, contact_mtime_ns) = _workspace_visual_asset(
            workspace, contact.get("asset"), "contact sheet")
        sheet_pages = contact.get("pages")
        if (not isinstance(sheet_pages, list) or not sheet_pages
                or any(type(page) is not int or page not in batch["pages"]
                       for page in sheet_pages)
                or len(sheet_pages) != len(set(sheet_pages))
                or len(sheet_pages) > CONTACT_SHEET_GROUP_SIZE):
            raise LightweightSessionError("contact sheet pages are invalid")
        minimum_width, minimum_height = _contact_min_dimensions(len(sheet_pages))
        if asset_width < minimum_width or asset_height < minimum_height:
            raise LightweightSessionError(
                "contact sheet is too small for legible per-page overview tiles"
            )
        contact_real = os.path.normcase(os.path.realpath(absolute))
        if contact_real in contact_paths:
            raise LightweightSessionError(
                "contact sheets must use distinct files"
            )
        contact_paths.add(contact_real)
        normalized_contacts.append({
            "asset": asset,
            "asset_sha256": contact_sha256,
            "asset_size_bytes": contact_size,
            "asset_mtime_ns": contact_mtime_ns,
            "asset_width": asset_width,
            "asset_height": asset_height,
            "pages": sheet_pages,
            "purpose": "overview_only",
        })
    if len(batch["pages"]) > 1:
        flattened = [page for contact in normalized_contacts for page in contact["pages"]]
        if sorted(flattened) != batch["pages"] or len(flattened) != len(set(flattened)):
            raise LightweightSessionError(
                "multi-page batches require contact sheets that partition every page exactly once"
            )
    render_ids = set()
    dedicated_paths = set()
    page_paths = set()
    parent_pages = {}
    allowed_locations = _batch_location_catalog(batch)
    dependency_keys = {
        key for key, row in allowed_locations.items()
        if row["role"] == "answer_dependency"
    }
    raw_dependency_pages = manifest.get("dependency_pages")
    if not isinstance(raw_dependency_pages, list):
        raise LightweightSessionError("dependency_pages must be an array")
    normalized_dependencies = []
    seen_dependency_keys = set()
    for dependency_page in raw_dependency_pages:
        expected_dependency_keys = {
            "source_id", "source_path", "source_sha256", "page", "render_id",
            "page_asset", "visible", "purpose", "content_types",
            "answer_provenance",
        }
        if (not isinstance(dependency_page, dict)
                or set(dependency_page) != expected_dependency_keys):
            raise LightweightSessionError("answer dependency page fields are invalid")
        location = _validate_location({
            key: dependency_page.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, "answer dependency page location")
        location_key = _location_key(location)
        render_id = dependency_page.get("render_id")
        dependency_content_types = dependency_page.get("content_types")
        if (location_key not in dependency_keys
                or location_key in seen_dependency_keys
                or not ID_RE.fullmatch(str(render_id or ""))
                or render_id in render_ids
                or dependency_page.get("visible") is not True
                or dependency_page.get("purpose") != "answer_locator_only"
                or not isinstance(dependency_content_types, list)
                or not dependency_content_types
                or len(dependency_content_types) != len(set(dependency_content_types))
                or any(value not in PAGE_CONTENT_TYPES
                       for value in dependency_content_types)):
            raise LightweightSessionError("answer dependency page identity is invalid")
        _validate_answer_provenance(
            dependency_content_types, dependency_page.get("answer_provenance"),
            "answer dependency page",
        )
        (page_asset, page_absolute, page_width, page_height, page_sha256,
         page_size, page_mtime_ns) = _workspace_visual_asset(
            workspace, dependency_page.get("page_asset"),
            "answer dependency page asset",
        )
        if page_width < 480 or page_height < 480:
            raise LightweightSessionError(
                "answer dependency page assets must be at least 480 pixels"
            )
        page_real = os.path.normcase(os.path.realpath(page_absolute))
        if page_real in contact_paths or page_real in page_paths:
            raise LightweightSessionError(
                "answer dependency pages must be distinct from every other page/contact"
            )
        page_paths.add(page_real)
        render_ids.add(render_id)
        seen_dependency_keys.add(location_key)
        normalized = {
            **location,
            "render_id": render_id,
            "page_asset": page_asset,
            "page_asset_sha256": page_sha256,
            "page_asset_size_bytes": page_size,
            "page_asset_mtime_ns": page_mtime_ns,
            "page_asset_width": page_width,
            "page_asset_height": page_height,
            "visible": True,
            "purpose": "answer_locator_only",
            "content_types": list(dependency_content_types),
            "answer_provenance": dependency_page["answer_provenance"],
            "asset_render_method": _visual_render_method(location["source_path"]),
        }
        normalized_dependencies.append(normalized)
        parent_pages[location_key] = {
            "location": location,
            "render_id": render_id,
            "page_asset": page_asset,
            "page_asset_sha256": page_sha256,
            "answer_provenance": dependency_page["answer_provenance"],
        }
    if seen_dependency_keys != dependency_keys:
        raise LightweightSessionError(
            "dependency_pages must cover every registered answer page exactly once"
        )
    normalized_dependencies.sort(key=lambda value: _location_sort_key(value))

    primary_page_inputs = {}
    for page in pages:
        expected_page_keys = {
            "page", "render_id", "page_asset", "visible", "content_types",
            "answer_provenance", "teaching_item_ids",
            "detail_required", "detail_reason",
        }
        if not isinstance(page, dict) or set(page) != expected_page_keys:
            raise LightweightSessionError("visual page fields are invalid")
        number = page.get("page")
        render_id = page.get("render_id")
        if (type(number) is not int or number not in batch["pages"]
                or number in primary_page_inputs
                or not ID_RE.fullmatch(str(render_id or ""))
                or render_id in render_ids
                or page.get("visible") is not True):
            raise LightweightSessionError("primary page render identity is invalid")
        (page_asset, page_absolute, page_width, page_height, page_sha256,
         page_size, page_mtime_ns) = _workspace_visual_asset(
            workspace, page.get("page_asset"), "page asset"
        )
        if page_width < 480 or page_height < 480:
            raise LightweightSessionError(
                "page assets must be at least 480 pixels in both dimensions"
            )
        page_real = os.path.normcase(os.path.realpath(page_absolute))
        if page_real in contact_paths or page_real in page_paths:
            raise LightweightSessionError(
                "every requested page needs an image with distinct path and bytes"
            )
        page_paths.add(page_real)
        render_ids.add(render_id)
        location = _source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], number,
        )
        parent_pages[_location_key(location)] = {
            "location": location,
            "render_id": render_id,
            "page_asset": page_asset,
            "page_asset_sha256": page_sha256,
            "answer_provenance": page.get("answer_provenance"),
        }
        primary_page_inputs[number] = (
            render_id, page_asset, page_width, page_height, page_sha256,
            page_size, page_mtime_ns,
        )

    all_teaching_item_ids = set()
    answer_location_keys = set()
    declared_prompt_pairs = set()
    prompt_component_pairs = set()

    def normalize_crop(binding, declaration, label):
        expected_crop_keys = {
            "source_id", "source_path", "source_sha256", "page",
            "parent_render_id", "parent_page_asset", "parent_page_asset_sha256",
            "crop_bbox_normalized", "crop_declaration", "asset",
        }
        if not isinstance(binding, dict) or set(binding) != expected_crop_keys:
            raise LightweightSessionError("%s fields are invalid" % label)
        location = _validate_location({
            key: binding.get(key)
            for key in ("source_id", "source_path", "source_sha256", "page")
        }, "%s location" % label)
        location_key = _location_key(location)
        parent = parent_pages.get(location_key)
        if (location_key not in allowed_locations or parent is None
                or binding.get("parent_render_id") != parent["render_id"]
                or binding.get("parent_page_asset") != parent["page_asset"]
                or binding.get("parent_page_asset_sha256")
                != parent["page_asset_sha256"]
                or binding.get("crop_declaration") != declaration):
            raise LightweightSessionError(
                "%s must bind its exact registered source and rendered parent page"
                % label
            )
        bbox = _validate_crop_bbox(binding.get("crop_bbox_normalized"), label)
        (asset, absolute, width, height, digest, size,
         mtime_ns) = _workspace_visual_asset(
            workspace, binding.get("asset"), "%s asset" % label
        )
        real_path = os.path.normcase(os.path.realpath(absolute))
        if (real_path in contact_paths or real_path in page_paths
                or real_path in dedicated_paths):
            raise LightweightSessionError(
                "%s must be a distinct declared-scope component crop file" % label
            )
        dedicated_paths.add(real_path)
        return ({
            **location,
            "parent_render_id": parent["render_id"],
            "parent_page_asset": parent["page_asset"],
            "parent_page_asset_sha256": parent["page_asset_sha256"],
            "crop_bbox_normalized": bbox,
            "crop_declaration": declaration,
            "asset": asset,
            "asset_sha256": digest,
            "asset_size_bytes": size,
            "asset_mtime_ns": mtime_ns,
            "asset_width": width,
            "asset_height": height,
        }, location_key)

    for page in pages:
        expected_page_keys = {
            "page", "render_id", "page_asset", "visible", "content_types",
            "answer_provenance", "teaching_item_ids",
            "detail_required", "detail_reason",
        }
        if not isinstance(page, dict) or set(page) != expected_page_keys:
            raise LightweightSessionError("visual page fields are invalid")
        number = page.get("page")
        if type(number) is not int or number not in batch["pages"]:
            raise LightweightSessionError("visual page number is invalid")
        (render_id, page_asset, page_width, page_height, page_sha256,
         page_size, page_mtime_ns) = primary_page_inputs[number]
        content_types = page.get("content_types")
        teaching_item_ids = page.get("teaching_item_ids")
        content_list = (
            isinstance(content_types, list)
            and all(isinstance(value, str) for value in content_types)
        )
        expected_detail = bool(content_list) and (
            len(batch["pages"]) == 1 or set(content_types) != {"text"}
        )
        expected_detail_reason = (
            "single_page_no_overview" if len(batch["pages"]) == 1
            else "dense_or_nontext_content" if expected_detail
            else "legible_text_only_overview"
        )
        if (not isinstance(content_types, list) or not content_types
                or len(content_types) != len(set(content_types))
                or any(value not in PAGE_CONTENT_TYPES for value in content_types)
                or not isinstance(teaching_item_ids, list)
                or len(teaching_item_ids) != len(set(teaching_item_ids))
                or any(not ID_RE.fullmatch(str(value or ""))
                       for value in teaching_item_ids)
                or page.get("detail_required") is not expected_detail
                or page.get("detail_reason") != expected_detail_reason):
            raise LightweightSessionError("page content classification is invalid")
        _validate_answer_provenance(
            content_types, page.get("answer_provenance"), "primary page"
        )
        page_teaching_ids = set(teaching_item_ids)
        primary_location_key = _location_key(_source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], number,
        ))
        parent_pages[primary_location_key]["teaching_item_ids"] = page_teaching_ids
        declared_prompt_pairs.update(
            (primary_location_key, value) for value in page_teaching_ids
        )
        all_teaching_item_ids.update(page_teaching_ids)
        page_numbers.append(number)
        normalized_pages.append({
            "page": number,
            "render_id": render_id,
            "page_asset": page_asset,
            "page_asset_sha256": page_sha256,
            "page_asset_size_bytes": page_size,
            "page_asset_mtime_ns": page_mtime_ns,
            "page_asset_width": page_width,
            "page_asset_height": page_height,
            "visible": True,
            "content_types": content_types,
            "answer_provenance": page["answer_provenance"],
            "teaching_item_ids": list(teaching_item_ids),
            "detail_required": expected_detail,
            "detail_reason": expected_detail_reason,
        })
    if not all_teaching_item_ids:
        raise LightweightSessionError(
            "visual batch must enumerate at least one teaching item"
        )
    if sorted(page_numbers) != batch["pages"] or len(set(page_numbers)) != len(page_numbers):
        raise LightweightSessionError("visual manifest page coverage is not bijective")
    raw_teaching_items = manifest.get("teaching_items")
    if (not isinstance(raw_teaching_items, list) or not raw_teaching_items
            or len(raw_teaching_items) != len(all_teaching_item_ids)):
        raise LightweightSessionError(
            "teaching_items must exactly cover explicit teaching_item_ids"
        )
    normalized_teaching_items = []
    seen_item_ids = set()
    seen_component_ids = set()
    for item in raw_teaching_items:
        if (not isinstance(item, dict) or set(item) != {
                "teaching_item_id", "kind", "description",
                "prompt_components", "answer_components",
                "answer_display_phase",
                }):
            raise LightweightSessionError("teaching item fields are invalid")
        item_id = item.get("teaching_item_id")
        kind = item.get("kind")
        description = item.get("description")
        if (not ID_RE.fullmatch(str(item_id or ""))
                or item_id in seen_item_ids
                or item_id not in all_teaching_item_ids
                or kind not in TEACHING_ITEM_KINDS
                or not isinstance(description, str) or not description.strip()
                or len(description) > MAX_DESCRIPTION_CHARS
                or item.get("answer_display_phase")
                != "solution_or_review_only"):
            raise LightweightSessionError("teaching item identity/kind is invalid")
        seen_item_ids.add(item_id)

        def normalize_components(values, side):
            allowed_roles = (
                PROMPT_COMPONENT_ROLES if side == "prompt"
                else ANSWER_COMPONENT_ROLES
            )
            if (not isinstance(values, list)
                    or (side == "prompt" and not values)
                    or len(values) > MAX_COMPONENTS_PER_ITEM):
                raise LightweightSessionError(
                    "%s components are invalid" % side
                )
            output = []
            for component in values:
                if (not isinstance(component, dict) or set(component) != {
                        "component_id", "component_role", "required_context_ids",
                        "allowed_detected_item_ids", "binding",
                        }):
                    raise LightweightSessionError(
                        "%s component fields are invalid" % side
                    )
                component_id = component.get("component_id")
                component_role = component.get("component_role")
                contexts = component.get("required_context_ids")
                allowed_detected = component.get("allowed_detected_item_ids")
                if (not ID_RE.fullmatch(str(component_id or ""))
                        or component_id in seen_component_ids
                        or component_role not in allowed_roles
                        or not isinstance(contexts, list)
                        or contexts != sorted(set(contexts))
                        or any(not ID_RE.fullmatch(str(value or ""))
                               for value in contexts)
                        or item_id in contexts):
                    raise LightweightSessionError(
                        "%s component identity/context is invalid" % side
                    )
                target_and_context = [item_id] + list(contexts)
                context_only = list(contexts)
                if (allowed_detected not in (target_and_context, context_only)
                        or not allowed_detected
                        or (side == "answer" and item_id not in allowed_detected)):
                    raise LightweightSessionError(
                        "%s component allowed_detected_item_ids must be target first "
                        "+ sorted contexts; only prompt components may instead be "
                        "non-empty context-only" % side
                    )
                binding, location_key = normalize_crop(
                    component.get("binding"),
                    "declared_%s_component_only" % side,
                    "%s component crop" % side,
                )
                if side == "prompt":
                    if (location_key not in {
                                key for key, value in allowed_locations.items()
                                if value["role"] == "primary"}
                            or (location_key, item_id) not in declared_prompt_pairs):
                        raise LightweightSessionError(
                            "prompt component parent pages must declare their teaching item"
                        )
                    prompt_component_pairs.add((location_key, item_id))
                else:
                    if parent_pages[location_key]["answer_provenance"] \
                            != "official_solution":
                        raise LightweightSessionError(
                            "answer components may bind only official_solution parents"
                        )
                    answer_location_keys.add(location_key)
                seen_component_ids.add(component_id)
                output.append({
                    "component_id": component_id,
                    "component_role": component_role,
                    "required_context_ids": list(contexts),
                    "allowed_detected_item_ids": list(allowed_detected),
                    "binding": binding,
                })
            return output

        prompt_components = normalize_components(
            item.get("prompt_components"), "prompt"
        )
        answer_components = normalize_components(
            item.get("answer_components"), "answer"
        )
        if not any(
                item_id in row["allowed_detected_item_ids"]
                for row in prompt_components):
            raise LightweightSessionError(
                "at least one prompt component must visibly contain the target item"
            )
        prompt_roles = {row["component_role"] for row in prompt_components}
        has_visual = bool(prompt_roles & VISUAL_PROMPT_COMPONENT_ROLES)
        has_nonvisual = bool(prompt_roles - VISUAL_PROMPT_COMPONENT_ROLES)
        if ((kind == "text" and (has_visual or not has_nonvisual))
                or (kind == "figure" and (not has_visual or has_nonvisual))
                or (kind == "mixed" and (not has_visual or not has_nonvisual))):
            raise LightweightSessionError(
                "teaching item kind disagrees with its prompt component roles"
            )
        normalized_teaching_items.append({
            "teaching_item_id": item_id,
            "kind": kind,
            "description": description.strip(),
            "prompt_components": prompt_components,
            "answer_components": answer_components,
            "answer_display_phase": "solution_or_review_only",
        })
    if seen_item_ids != all_teaching_item_ids:
        raise LightweightSessionError(
            "teaching_items do not exactly cover page teaching_item_ids"
        )
    if prompt_component_pairs != declared_prompt_pairs:
        raise LightweightSessionError(
            "page teaching-item declarations and prompt components must cover each "
            "other exactly"
        )
    normalized_teaching_items.sort(key=lambda row: row["teaching_item_id"])
    official_dependency_keys = {
        key for key in dependency_keys
        if parent_pages[key]["answer_provenance"] == "official_solution"
    }
    if not official_dependency_keys.issubset(answer_location_keys):
        raise LightweightSessionError(
            "each official-solution dependency page must provide a declared-scope "
            "answer component crop"
        )
    if answer_location_keys.intersection(dependency_keys - official_dependency_keys):
        raise LightweightSessionError(
            "unknown/student-attempt dependency pages cannot bind official answers"
        )
    normalized_pages.sort(key=lambda row: row["page"])
    asset_catalog = {}
    detail_required_locations = set(dependency_keys)
    primary_locations = {
        page: _source_location(
            batch["source_id"], batch["source_path"],
            batch["source_revision"]["sha256"], page,
        )
        for page in batch["pages"]
    }
    for contact in normalized_contacts:
        asset_catalog[contact["asset"]] = {
            "role": "contact", "sha256": contact["asset_sha256"],
            "locations": {
                _location_key(primary_locations[number])
                for number in contact["pages"]
            },
        }
    prompt_assets = set()
    answer_assets = set()
    for page in normalized_pages:
        number = page["page"]
        page_location_key = _location_key(primary_locations[number])
        asset_catalog[page["page_asset"]] = {
            "role": "page", "sha256": page["page_asset_sha256"],
            "locations": {page_location_key},
        }
        if page["detail_required"]:
            detail_required_locations.add(page_location_key)
    for item in normalized_teaching_items:
        for component in item["prompt_components"]:
            prompt = component["binding"]
            asset_catalog[prompt["asset"]] = {
                "role": "prompt", "sha256": prompt["asset_sha256"],
                "locations": {_location_key(prompt)},
                "target_item_id": item["teaching_item_id"], "side": "prompt",
                "component_id": component["component_id"],
                "component_role": component["component_role"],
                "required_context_ids": component["required_context_ids"],
                "allowed_detected_item_ids": component["allowed_detected_item_ids"],
            }
            prompt_assets.add(prompt["asset"])
        for component in item["answer_components"]:
            answer = component["binding"]
            asset_catalog[answer["asset"]] = {
                "role": "answer", "sha256": answer["asset_sha256"],
                "locations": {_location_key(answer)},
                "target_item_id": item["teaching_item_id"], "side": "answer",
                "component_id": component["component_id"],
                "component_role": component["component_role"],
                "required_context_ids": component["required_context_ids"],
                "allowed_detected_item_ids": component["allowed_detected_item_ids"],
            }
            answer_assets.add(answer["asset"])
    for page in normalized_dependencies:
        asset_catalog[page["page_asset"]] = {
            "role": "dependency_page", "sha256": page["page_asset_sha256"],
            "locations": {_location_key(page)},
        }
    raw_calls = manifest.get("model_calls")
    if (not isinstance(raw_calls, list) or not raw_calls
            or len(raw_calls) > MAX_MODEL_CALLS):
        raise LightweightSessionError(
            "model_calls must declare the exact image inputs used by the host visual model"
        )
    normalized_calls = []
    call_ids = set()
    used_stage_assets = set()
    reviewed_crop_assets = set()
    overview_assets = set()
    detail_assets = set()
    solution_assets = set()
    crop_review_assets = set()
    covered_locations = set()
    for call in raw_calls:
        stage = call.get("stage") if isinstance(call, dict) else None
        expected_call_keys = {
            "call_id", "stage", "host", "model", "input_assets", "locations",
        }
        if stage == "crop_review":
            expected_call_keys.add("crop_review")
        if (not isinstance(call, dict)
                or set(call) != expected_call_keys):
            raise LightweightSessionError("model call fields are invalid")
        call_id = call.get("call_id")
        host = call.get("host")
        model = call.get("model")
        inputs = call.get("input_assets")
        call_locations = _canonical_locations(
            call.get("locations"), "model call locations"
        )
        if (not ID_RE.fullmatch(str(call_id or "")) or call_id in call_ids
                or stage not in ("overview", "detail", "solution", "crop_review")
                or not isinstance(host, str) or not host.strip()
                or host != host.strip()
                or "\n" in host or "\r" in host or len(host) > 256
                or not isinstance(model, str) or not model.strip()
                or model != model.strip()
                or "\n" in model or "\r" in model or len(model) > 256
                or not isinstance(inputs, list) or not inputs
                or len(inputs) > MAX_ASSETS_PER_MODEL_CALL
                or any(not isinstance(value, str) or value not in asset_catalog
                       for value in inputs)
                or len(inputs) != len(set(inputs))
                or (stage == "crop_review"
                    and any(value in reviewed_crop_assets for value in inputs))
                or (stage != "crop_review"
                    and any(value in used_stage_assets for value in inputs))):
            raise LightweightSessionError(
                "model call identity/input/source locations are invalid"
            )
        roles = {asset_catalog[value]["role"] for value in inputs}
        inferred_locations = set().union(
            *(asset_catalog[value]["locations"] for value in inputs)
        )
        call_location_keys = {_location_key(value) for value in call_locations}
        if call_location_keys != inferred_locations:
            raise LightweightSessionError(
                "model call locations must equal the source-qualified locations "
                "bound to its exact input assets"
            )
        if stage == "overview" and (roles != {"contact"} or len(inputs) != 1):
            raise LightweightSessionError(
                "overview calls must use exactly one contact sheet"
            )
        if stage == "detail" and not roles.issubset({
                "page", "dependency_page", "prompt",
        }):
            raise LightweightSessionError(
                "detail calls may use only primary/dependency pages or prompt crops"
            )
        if stage == "detail" and "prompt" in roles:
            prompt_targets = {
                asset_catalog[value]["target_item_id"] for value in inputs
            }
            if roles != {"prompt"} or len(prompt_targets) != 1:
                raise LightweightSessionError(
                    "one detail call may combine only one target item's reviewed "
                    "prompt components"
                )
        if stage == "solution" and roles != {"answer"}:
            raise LightweightSessionError(
                "solution calls may use only answer-side item crops"
            )
        if stage == "solution" and len({
                asset_catalog[value]["target_item_id"] for value in inputs}) != 1:
            raise LightweightSessionError(
                "one solution call may combine only one target item's answer components"
            )
        normalized_review = None
        if stage == "crop_review":
            if len(inputs) != 1 or not roles.issubset({"prompt", "answer"}):
                raise LightweightSessionError(
                    "crop_review calls must use exactly one prompt/answer crop"
                )
            binding = asset_catalog[inputs[0]]
            review = call.get("crop_review")
            expected_review_keys = {
                "schema_version", "target_item_id", "side", "component_id",
                "component_role", "crop_sha256", "required_context_ids",
                "allowed_detected_item_ids",
                "verdict", "unrelated_content_present", "student_attempt_present",
                "detected_item_ids", "reviewer_kind", "reviewer", "reviewed_at",
                "invocation_id",
            }
            if (not isinstance(review, dict) or set(review) != expected_review_keys
                    or review.get("schema_version") != 2
                    or review.get("target_item_id") != binding["target_item_id"]
                    or review.get("side") != binding["side"]
                    or review.get("component_id") != binding["component_id"]
                    or review.get("component_role") != binding["component_role"]
                    or review.get("crop_sha256") != binding["sha256"]
                    or review.get("required_context_ids")
                    != binding["required_context_ids"]
                    or review.get("allowed_detected_item_ids")
                    != binding["allowed_detected_item_ids"]
                    or review.get("verdict") != "declared_scope_only"
                    or review.get("unrelated_content_present") is not False
                    or review.get("student_attempt_present") is not False
                    or review.get("detected_item_ids")
                    != binding["allowed_detected_item_ids"]
                    or review.get("reviewer_kind") != "model_vision"
                    or review.get("reviewer") != model
                    or not _valid_timestamp(review.get("reviewed_at"))
                    or review.get("invocation_id") != call_id):
                raise LightweightSessionError(
                    "crop_review must prove exactly its declared target/context scope"
                )
            normalized_review = dict(review)
        call_ids.add(call_id)
        if stage == "crop_review":
            reviewed_crop_assets.update(inputs)
            crop_review_assets.update(inputs)
        else:
            used_stage_assets.update(inputs)
        covered_locations.update(call_location_keys)
        if stage == "overview":
            overview_assets.update(inputs)
        elif stage == "detail":
            detail_assets.update(inputs)
        elif stage == "solution":
            solution_assets.update(inputs)
        normalized_call = {
            "call_id": call_id,
            "stage": stage,
            "host": host.strip(),
            "model": model.strip(),
            "inputs": [
                {
                    "asset": value,
                    "asset_sha256": asset_catalog[value]["sha256"],
                    "locations": sorted(
                        [allowed_locations[key]["location"]
                         for key in asset_catalog[value]["locations"]],
                        key=_location_sort_key,
                    ),
                }
                for value in inputs
            ],
            "locations": call_locations,
        }
        if normalized_review is not None:
            normalized_call["crop_review"] = normalized_review
        normalized_calls.append(normalized_call)
    contact_assets = {
        value for value, binding in asset_catalog.items()
        if binding["role"] == "contact"
    }
    detail_page_assets = {
        value for value in detail_assets
        if asset_catalog[value]["role"] in {"page", "dependency_page"}
    }
    detailed_locations = set().union(
        *(asset_catalog[value]["locations"] for value in detail_page_assets)
    ) if detail_page_assets else set()
    if overview_assets != contact_assets:
        raise LightweightSessionError(
            "every contact sheet must be sent exactly once in an overview model call"
        )
    if not set(allowed_locations).issubset(covered_locations):
        raise LightweightSessionError(
            "model calls do not visually cover every registered source location"
        )
    if detail_required_locations != detailed_locations:
        raise LightweightSessionError(
            "detail model calls must exactly match primary pages requiring detail plus "
            "the explicitly registered answer dependency pages"
        )
    if not prompt_assets.issubset(detail_assets):
        raise LightweightSessionError(
            "every teaching-item prompt component must be a detail model input"
        )
    if solution_assets != answer_assets:
        raise LightweightSessionError(
            "every answer-side crop must be used exactly once in a solution-stage call"
        )
    if crop_review_assets != (prompt_assets | answer_assets):
        raise LightweightSessionError(
            "every prompt/answer crop requires exactly one independent crop_review call"
        )
    payload = {
        "schema_version": VISUAL_RECEIPT_SCHEMA_VERSION,
        "receipt_type": "lightweight_visual_batch",
        "batch_id": batch["batch_id"],
        "source_sha256": batch["source_revision"]["sha256"],
        "inspection_method": "model_visual",
        "teaching_scope_protocol": "generic_teaching_item_components_v3",
        "location_type": (
            "pdf_page" if batch["source_path"].lower().endswith(".pdf")
            else "raster_page_equivalent"
        ),
        "asset_render_method": _visual_render_method(batch["source_path"]),
        "pages": normalized_pages,
        "teaching_items": normalized_teaching_items,
        "answer_dependencies": _dependency_receipt_rows(batch),
        "dependency_pages": normalized_dependencies,
        "contact_sheets": normalized_contacts,
        "model_calls": normalized_calls,
        "created_at": (
            batch["visual_receipt"]["created_at"]
            if isinstance(batch.get("visual_receipt"), dict)
            else _utc_now()
        ),
    }
    payload["receipt_id"] = "lw-visual-" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()[:24]
    _validate_visual_receipt_shape(payload, batch)
    return payload


def cmd_record_visual(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        _reject_legacy_active_strategy(batch, "visual receipt import")
        if batch["status"] in ("abandoned", "superseded"):
            raise LightweightSessionError(
                "a terminal batch is closed; use its current successor or plan a new slice"
            )
        if _visual_receipt_is_schema2(batch.get("visual_receipt")):
            raise LightweightSessionError(
                "schema-2 visual evidence is quarantined read-only; abandon this "
                "visual_ready attempt, then plan a new schema-3 attempt"
            )
        _assert_batch_sources_current(materials, batch, exact=True)
        receipt = _visual_receipt(workspace, batch, args.manifest)
        if batch["status"] in ("visual_ready", "taught"):
            if batch["visual_receipt"] != receipt:
                raise LightweightSessionError(
                    "visual evidence is frozen once ready; abandon and create a new "
                    "attempt instead of replacing it"
                )
            changed = False
        else:
            batch["visual_receipt"] = receipt
            batch["status"] = "visual_ready"
            batch["updated_at"] = _utc_now()
            _assert_batch_sources_current(materials, batch, exact=True)
            if _visual_assets_stale(workspace, batch):
                raise LightweightSessionError(
                    "visual assets changed before receipt publication"
                )
            _save_session(workspace, session)
            changed = True
    return {
        "process_success": True,
        "changed": changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "visual_receipt": receipt,
        "next_action": (
            "teach in full detail; show every required prompt component before explaining it, "
            "persist notebook/progress, then mark-taught"
        ),
    }


def cmd_abandon(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    reason = " ".join(str(args.reason or "").split())
    if len(reason) < 5 or len(reason) > 500:
        raise LightweightSessionError(
            "abandon reason must be a concrete 5-500 character explanation"
        )
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        if batch["status"] == "superseded":
            raise LightweightSessionError(
                "a superseded batch is immutable audited history"
            )
        if batch["status"] == "taught":
            raise LightweightSessionError(
                "a taught batch is durable progress and cannot be abandoned"
            )
        if batch["status"] == "abandoned":
            if batch["abandonment"]["reason"] != reason:
                raise LightweightSessionError(
                    "an abandoned batch cannot replace its recorded reason"
                )
            changed = False
        else:
            prior_status = batch["status"]
            unsigned = {
                "prior_status": prior_status,
                "reason": reason,
                "created_at": _utc_now(),
            }
            receipt = dict(unsigned)
            receipt["receipt_id"] = "lw-abandon-" + hashlib.sha256(
                _canonical_json({"batch_id": batch["batch_id"], **unsigned}).encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
            batch["status"] = "abandoned"
            batch["abandonment"] = receipt
            batch["updated_at"] = _utc_now()
            _save_session(workspace, session)
            changed = True
    return {
        "process_success": True,
        "changed": changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "abandonment": batch["abandonment"],
        "next_action": "plan a replacement batch only for the learner's current topic",
    }


def cmd_replace_taught(args):
    """Atomically archive one taught attempt and open its exact-slice successor."""
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    reason = " ".join(str(args.reason or "").split())
    if not 5 <= len(reason) <= 500:
        raise LightweightSessionError(
            "replacement reason must be a concrete 5-500 character explanation"
        )
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        old = _batch(session, args.batch_id)
        state = _strict_json(
            safe_workspace_entry(workspace, STATE_PATH), "study_state.json"
        )
        if old["status"] not in ("taught", "superseded"):
            raise LightweightSessionError("replace-taught requires a taught batch")
        if (not isinstance(state, dict)
                or state.get("current_phase") != old["chapter"]):
            raise LightweightSessionError(
                "replace-taught is limited to the current learning phase"
            )
        try:
            import update_progress as progress
        except (ImportError, OSError) as exc:
            raise LightweightSessionError(
                "cannot load progress publisher for taught replacement"
            ) from exc
        shape_errors = progress._phase_evidence_shape_errors(state)
        if shape_errors:
            raise LightweightSessionError(
                "study_state phase evidence is damaged: %s" % shape_errors[0]
            )
        phase_evidence = state.get("phase_evidence") if isinstance(state, dict) else None
        if not isinstance(phase_evidence, dict):
            raise LightweightSessionError(
                "replace-taught requires valid study_state phase evidence"
            )
        phase_record = phase_evidence.get(str(old["chapter"]), {})
        if not isinstance(phase_record, dict):
            raise LightweightSessionError(
                "replace-taught phase evidence record is invalid"
            )
        events = phase_record.get("lightweight_batches", [])
        expected_old_event = (
            (old.get("supersession") or {}).get("preserved_progress_event")
            if old["status"] == "superseded" else _progress_event(old)
        )
        matching_events = [
            event for event in events if isinstance(event, dict)
            and event.get("batch_id") == old["batch_id"]
        ] if isinstance(events, list) else []
        all_occurrences = _state_batch_event_occurrences(
            state, old["batch_id"]
        )
        if (len(matching_events) != 1 or matching_events[0] != expected_old_event
                or all_occurrences != [(str(old["chapter"]), expected_old_event)]):
            raise LightweightSessionError(
                "taught replacement requires the exact published progress event"
            )
        checklist = state.get("phase_checklist")
        if checklist is None:
            checklist = []
            state["phase_checklist"] = checklist
        if not isinstance(checklist, list):
            raise LightweightSessionError(
                "study_state phase_checklist must be an array"
            )
        phase_rows = []
        for index, row in enumerate(checklist):
            if (not isinstance(row, dict)
                    or not isinstance(row.get("text"), str)
                    or not row["text"].strip()
                    or (row.get("done") is not None
                        and not isinstance(row["done"], bool))):
                raise LightweightSessionError(
                    "study_state phase_checklist[%d] is invalid" % index
                )
            if progress.phase_number_from_check(row["text"]) == old["chapter"]:
                phase_rows.append(row)
        if len(phase_rows) > 1:
            raise LightweightSessionError(
                "phase_checklist contains multiple rows for the current phase"
            )
        if old["status"] == "superseded":
            if old["supersession"]["reason"] != reason:
                raise LightweightSessionError(
                    "a superseded batch cannot replace its recorded reason"
                )
            successor = _batch(
                session, old["supersession"]["successor_batch_id"]
            )
            changed = False
        else:
            active = [
                batch for batch in _current_batches(session)
                if batch["status"] in ("planned", "visual_ready")
            ]
            if active:
                raise LightweightSessionError(
                    "finish or abandon active batch %s before replacing taught work"
                    % active[0]["batch_id"]
                )
            if len(session["batches"]) >= MAX_BATCHES:
                raise LightweightSessionError("lightweight batch ledger reached its limit")
            row = _source_row(session, old["source_path"])
            source = _material_file(materials, row["relative_path"])
            revision = _revision(source, row, force_hash=True)
            successor_dependencies = []
            dependency_revision_cache = {old["source_id"]: revision}
            for prior_dependency in old["answer_dependencies"]:
                dependency_revision = dependency_revision_cache.get(
                    prior_dependency["source_id"]
                )
                if dependency_revision is None:
                    dependency_row = _source_row(
                        session, prior_dependency["source_path"]
                    )
                    dependency_source = _material_file(
                        materials, dependency_row["relative_path"]
                    )
                    dependency_revision = _revision(
                        dependency_source, dependency_row, force_hash=True
                    )
                    dependency_revision_cache[
                        prior_dependency["source_id"]
                    ] = dependency_revision
                successor_dependencies.append({
                    "source_id": prior_dependency["source_id"],
                    "source_path": prior_dependency["source_path"],
                    "source_revision": dependency_revision,
                    "pages": list(prior_dependency["pages"]),
                    "registered_at": prior_dependency["registered_at"],
                })
            base = _batch_id(
                old["source_id"], revision, old["chapter"], old["pages"]
            )
            used = {batch["batch_id"] for batch in session["batches"]}
            successor_id = base
            attempt = 2
            while successor_id in used:
                successor_id = "%s-r%d" % (base, attempt)
                attempt += 1
            now = _utc_now()
            successor = {
                "batch_id": successor_id,
                "chapter": old["chapter"],
                "source_id": old["source_id"],
                "source_path": old["source_path"],
                "source_revision": revision,
                "pages": list(old["pages"]),
                "answer_dependencies": successor_dependencies,
                "answer_dependency_history": [],
                "status": "planned",
                "created_at": now,
                "updated_at": now,
                "visual_receipt": None,
                "teaching_receipt": None,
                "abandonment": None,
                "supersedes_batch_id": old["batch_id"],
                "supersession": None,
                "token_strategy": _token_strategy(component_scoped=True),
            }
            for dependency in successor_dependencies:
                _record_dependency_event(
                    successor, "inherited", None, dependency,
                    "preserved and revision-revalidated by replace-taught",
                )
            _assert_batch_sources_current(
                materials, successor, exact=True, cache={}
            )
            unsigned = {
                "prior_status": "taught",
                "reason": reason,
                "successor_batch_id": successor_id,
                "preserved_progress_event": expected_old_event,
                "created_at": now,
            }
            supersession = dict(unsigned)
            supersession["receipt_id"] = "lw-supersede-" + hashlib.sha256(
                _canonical_json({"batch_id": old["batch_id"], **unsigned}).encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
            old["status"] = "superseded"
            old["supersession"] = supersession
            old["updated_at"] = now
            session["batches"].append(successor)
            _save_session(workspace, session)
            changed = True
        terminal_successor = successor
        seen_successors = {old["batch_id"]}
        while terminal_successor["status"] == "superseded":
            successor_id = terminal_successor["supersession"]["successor_batch_id"]
            if successor_id in seen_successors:
                raise LightweightSessionError("replacement successor chain contains a cycle")
            seen_successors.add(successor_id)
            terminal_successor = _batch(session, successor_id)
        progress_changed = False
        # A retry after the successor was already taught must be a true no-op:
        # do not reopen a newly valid completion badge.  Before that point, a
        # superseded attempt cannot continue to satisfy phase completion.
        if terminal_successor["status"] != "taught":
            for field in ("status", "completed_at", "completion_mode"):
                if field in phase_record:
                    phase_record.pop(field)
                    progress_changed = True
            if phase_rows and phase_rows[0].get("done"):
                phase_rows[0]["done"] = False
                progress_changed = True
        if progress_changed:
            phase_record["updated_at"] = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            )
            try:
                progress.save(
                    workspace, state,
                    "lightweight batch %s superseded; phase completion reopened"
                    % old["batch_id"],
                    quiet=True,
                )
            except SystemExit as exc:
                raise LightweightSessionError(
                    "replacement is published but progress reopening failed; "
                    "rerun replace-taught"
                ) from exc
    return {
        "process_success": True,
        "changed": changed or progress_changed,
        "superseded_batch_id": old["batch_id"],
        "successor_batch_id": successor["batch_id"],
        "successor_batch": successor,
        "preserved_progress_event": expected_old_event,
        "next_action": "redo visual review and teaching for the planned successor",
    }


def cmd_mark_taught(args):
    workspace = _resolved(args.workspace)
    materials = _resolved(args.materials)
    _start_gate(workspace, materials)
    with workspace_publication_lock(workspace):
        _start_gate(workspace, materials)
        session = _load_session(workspace, materials)
        batch = _batch(session, args.batch_id)
        _reject_legacy_active_strategy(batch, "teaching completion")
        if batch["status"] not in ("visual_ready", "taught") or not batch["visual_receipt"]:
            raise LightweightSessionError("visual evidence must be ready before teaching closes")
        if _visual_receipt_is_schema2(batch["visual_receipt"]):
            raise LightweightSessionError(
                "schema-2 visual evidence is quarantined read-only and cannot close "
                "teaching; abandon this attempt and plan a new schema-3 attempt"
            )
        _assert_batch_sources_current(materials, batch, exact=True)
        if _visual_assets_stale(workspace, batch):
            raise LightweightSessionError(
                "visual page/component evidence changed after review; record it again"
            )
        state_path = safe_workspace_entry(workspace, STATE_PATH)
        if not os.path.isfile(state_path) or is_link_or_reparse(state_path):
            raise LightweightSessionError("study_state.json is missing or unsafe")
        state = _strict_json(state_path, "study_state.json")
        if (not isinstance(state, dict)
                or exam_start.i18n.workspace_processing_mode(state) != "lightweight"):
            raise LightweightSessionError(
                "study_state.json no longer authorizes lightweight teaching"
            )
        if state.get("current_phase") != batch["chapter"]:
            raise LightweightSessionError(
                "batch chapter=%d no longer matches study_state.current_phase=%r"
                % (batch["chapter"], state.get("current_phase"))
            )
        checklist = state.get("phase_checklist")
        if checklist is None:
            checklist = []
            state["phase_checklist"] = checklist
        if not isinstance(checklist, list):
            raise LightweightSessionError(
                "study_state phase_checklist must be an array"
            )
        for index, row in enumerate(checklist):
            if not isinstance(row, dict):
                raise LightweightSessionError(
                    "study_state phase_checklist[%d] must be an object" % index
                )
            if not isinstance(row.get("text"), str) or not row["text"].strip():
                raise LightweightSessionError(
                    "study_state phase_checklist[%d].text must be a non-empty string"
                    % index
                )
            if row.get("done") is not None and not isinstance(row["done"], bool):
                raise LightweightSessionError(
                    "study_state phase_checklist[%d].done must be boolean" % index
                )
        try:
            import update_progress as progress
        except (ImportError, OSError) as exc:
            raise LightweightSessionError(
                "cannot load progress publisher for the taught transition"
            ) from exc
        shape_errors = progress._phase_evidence_shape_errors(state)
        if shape_errors:
            raise LightweightSessionError(
                "study_state phase evidence is damaged: %s" % shape_errors[0]
            )
        notebook_entry, notebook_entry_sha256 = _notebook_entry_binding(
            workspace, args.notebook_entry, batch["chapter"]
        )
        legacy_visual = _visual_receipt_is_legacy(batch["visual_receipt"])
        taught_item_ids = None
        if not legacy_visual:
            taught_item_ids = _parse_taught_item_ids(args.taught_item_ids)
            expected_item_ids = sorted(
                _visual_teaching_item_ids(batch["visual_receipt"])
            )
            if taught_item_ids != expected_item_ids:
                raise LightweightSessionError(
                    "mark-taught must bind every enumerated visual teaching item exactly"
                )
        receipt = {
            "schema_version": 2,
            "receipt_type": "lightweight_taught_batch",
            "batch_id": batch["batch_id"],
            "visual_receipt_id": batch["visual_receipt"]["receipt_id"],
            "phase": batch["chapter"],
            "notebook_entry": notebook_entry,
            "notebook_entry_sha256": notebook_entry_sha256,
            "explanation_detail": "unabridged_beginner_friendly",
            "created_at": (
                batch["teaching_receipt"]["created_at"]
                if isinstance(batch.get("teaching_receipt"), dict)
                else _utc_now()
            ),
        }
        if not legacy_visual:
            receipt["inspected_pages"] = list(batch["pages"])
            receipt["taught_item_ids"] = taught_item_ids
        receipt["receipt_id"] = "lw-taught-" + hashlib.sha256(
            _canonical_json(receipt).encode("utf-8")
        ).hexdigest()[:24]
        event = {
            "batch_id": batch["batch_id"],
            "visual_receipt_id": batch["visual_receipt"]["receipt_id"],
            "teaching_receipt_id": receipt["receipt_id"],
            "notebook_entry": receipt["notebook_entry"],
            "notebook_entry_sha256": receipt["notebook_entry_sha256"],
            "source_sha256": batch["source_revision"]["sha256"],
        }
        if legacy_visual:
            event["pages"] = list(batch["pages"])
        else:
            event["inspected_pages"] = list(batch["pages"])
            event["taught_item_ids"] = list(taught_item_ids)
        record = state.setdefault("phase_evidence", {}).setdefault(
            str(batch["chapter"]), {}
        )
        events = record.setdefault("lightweight_batches", [])
        if not isinstance(events, list):
            raise LightweightSessionError(
                "study_state lightweight progress events must be an array"
            )
        same_batch = [
            existing for existing in events
            if isinstance(existing, dict)
            and existing.get("batch_id") == batch["batch_id"]
        ]
        occurrences = _state_batch_event_occurrences(state, batch["batch_id"])
        expected_occurrences = (
            [(str(batch["chapter"]), event)] if same_batch else []
        )
        if (same_batch and (len(same_batch) != 1 or same_batch[0] != event)):
            raise LightweightSessionError(
                "study_state contains a conflicting progress event for this batch"
            )
        if occurrences != expected_occurrences:
            raise LightweightSessionError(
                "study_state contains a duplicate or cross-phase progress event "
                "for this batch"
            )
        phase_rows = [
            row for row in checklist
            if (isinstance(row, dict)
                and progress.phase_number_from_check(row.get("text") or "")
                == batch["chapter"])
        ]
        if len(phase_rows) > 1:
            raise LightweightSessionError(
                "phase_checklist contains multiple rows for the current phase"
            )
        progress_changed = not same_batch
        if batch["status"] == "taught":
            if batch["teaching_receipt"] != receipt:
                raise LightweightSessionError("a taught batch cannot replace its evidence")
            changed = False
        else:
            batch["teaching_receipt"] = receipt
            batch["status"] = "taught"
            batch["updated_at"] = _utc_now()
            _assert_batch_sources_current(materials, batch, exact=True)
            if _visual_assets_stale(workspace, batch):
                raise LightweightSessionError(
                    "visual assets changed before taught receipt publication"
                )
            rebound_entry, rebound_digest = _notebook_entry_binding(
                workspace, args.notebook_entry, batch["chapter"]
            )
            if (rebound_entry != notebook_entry
                    or rebound_digest != notebook_entry_sha256):
                raise LightweightSessionError(
                    "notebook entry changed before taught receipt publication"
                )
            _save_session(workspace, session)
            changed = True
        if _progress_event(batch) != event:
            raise LightweightSessionError("taught progress event publication drifted")
        if progress_changed:
            events.append(event)
            events.sort(key=lambda value: value["batch_id"])
            record["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            record.pop("status", None)
            record.pop("completed_at", None)
            record.pop("completion_mode", None)
            checklist = state.setdefault("phase_checklist", [])
            phase_rows = [
                row for row in checklist
                if (isinstance(row, dict)
                    and progress.phase_number_from_check(row.get("text") or "")
                    == batch["chapter"])
            ]
            if len(phase_rows) > 1:
                raise LightweightSessionError(
                    "phase_checklist contains multiple rows for the current phase"
                )
            if not phase_rows:
                language = exam_start.i18n.workspace_language(state)
                label = (
                    "Phase %d (lightweight selected scope)" % batch["chapter"]
                    if language == "en"
                    else "阶段 %d（轻量按需范围）" % batch["chapter"]
                )
                phase_rows = [{"text": label, "done": False}]
                checklist.append(phase_rows[0])
            else:
                phase_rows[0]["done"] = False
            try:
                progress.save(
                    workspace, state,
                    "lightweight batch %s → taught evidence" % batch["batch_id"],
                    quiet=True,
                )
            except SystemExit as exc:
                raise LightweightSessionError(
                    "batch is taught but progress publication failed; rerun mark-taught"
                ) from exc
        else:
            # Recovery path for a prior run that published the event but was interrupted
            # before a canonical checklist row existed.
            checklist = state.setdefault("phase_checklist", [])
            phase_rows = [
                row for row in checklist
                if (isinstance(row, dict)
                    and progress.phase_number_from_check(row.get("text") or "")
                    == batch["chapter"])
            ]
            if len(phase_rows) > 1:
                raise LightweightSessionError(
                    "phase_checklist contains multiple rows for the current phase"
                )
            if not phase_rows:
                language = exam_start.i18n.workspace_language(state)
                checklist.append({
                    "text": (
                        "Phase %d (lightweight selected scope)" % batch["chapter"]
                        if language == "en"
                        else "阶段 %d（轻量按需范围）" % batch["chapter"]
                    ),
                    "done": False,
                })
                try:
                    progress.save(
                        workspace, state,
                        "repair lightweight phase checklist row", quiet=True,
                    )
                except SystemExit as exc:
                    raise LightweightSessionError(
                        "progress checklist repair failed; rerun mark-taught"
                    ) from exc
                progress_changed = True
    return {
        "process_success": True,
        "changed": changed or progress_changed,
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "teaching_receipt": receipt,
        "progress_event": event,
        "next_action": "plan the next pages only when the learner reaches them",
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="Lightweight on-demand visual-study state machine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def paths(command):
        command.add_argument("--workspace", required=True)
        command.add_argument("--materials", required=True)
        command.add_argument("--json", action="store_true", dest="as_json")

    initialize = sub.add_parser("init", help="inventory filenames only; parse no content")
    paths(initialize)
    initialize.add_argument("--refresh", action="store_true")
    initialize.add_argument(
        "--migrate-session", action="store_true",
        help=(
            "explicitly reinitialize an evidence-free schema-1 session as the "
            "current schema-2 session; visual receipts remain schema 3"
        ),
    )

    status = sub.add_parser("status", help="show page-batch state without parsing material")
    paths(status)
    status.add_argument(
        "--verify-live", action="store_true",
        help="explicitly stream-hash current source/assets instead of metadata-only checks",
    )

    plan = sub.add_parser("plan", help="bind the exact source pages needed now")
    paths(plan)
    plan.add_argument("--chapter", required=True, type=int)
    plan.add_argument("--source", required=True, help="path relative to materials")
    plan.add_argument(
        "--pages", required=True,
        help="comma-separated positive pages/ranges, e.g. 1-4,7",
    )

    dependency = sub.add_parser(
        "register-answer-dependency",
        help="bind only exact extra page(s) needed for an external solution crop",
    )
    paths(dependency)
    dependency.add_argument("--batch-id", required=True)
    dependency.add_argument("--source", required=True, help="path relative to materials")
    dependency.add_argument(
        "--pages", required=True,
        help=(
            "comma-separated positive pages/ranges, e.g. 1-3,7; at most 4 "
            "answer-dependency pages total"
        ),
    )

    dependency_set = sub.add_parser(
        "set-answer-dependency",
        help="replace/narrow one registered answer source to an exact live page set",
    )
    paths(dependency_set)
    dependency_set.add_argument("--batch-id", required=True)
    dependency_set.add_argument("--source", required=True, help="path relative to materials")
    dependency_set.add_argument(
        "--pages", required=True,
        help=(
            "replacement exact page set using comma-separated positive pages/ranges, "
            "e.g. 1-3,7; at most 4 answer-dependency pages total"
        ),
    )
    dependency_set.add_argument("--reason", required=True)

    dependency_remove = sub.add_parser(
        "remove-answer-dependency",
        help="remove one registered answer source with an auditable reason",
    )
    paths(dependency_remove)
    dependency_remove.add_argument("--batch-id", required=True)
    dependency_remove.add_argument("--source", required=True, help="path relative to materials")
    dependency_remove.add_argument("--reason", required=True)

    visual = sub.add_parser(
        "record-visual", help="import host/model visual evidence for one planned batch"
    )
    paths(visual)
    visual.add_argument("--batch-id", required=True)
    visual.add_argument("--manifest", required=True)

    abandon = sub.add_parser(
        "abandon", help="close one unfinished batch with an auditable reason"
    )
    paths(abandon)
    abandon.add_argument("--batch-id", required=True)
    abandon.add_argument("--reason", required=True)

    taught = sub.add_parser(
        "mark-taught", help="bind a visually ready batch to notebook/progress evidence"
    )
    paths(taught)
    taught.add_argument("--batch-id", required=True)
    taught.add_argument("--notebook-entry", required=True)
    taught.add_argument(
        "--taught-item-ids",
        help=(
            "comma-separated exact teaching_item_ids from the visual receipt; required "
            "for new item-scoped schema-3 batches"
        ),
    )

    replacement = sub.add_parser(
        "replace-taught",
        help="archive one taught attempt and plan its exact-slice successor",
    )
    paths(replacement)
    replacement.add_argument("--batch-id", required=True)
    replacement.add_argument("--reason", required=True)
    return parser


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("process_success=%s" % str(payload.get("process_success", False)).lower())
        if payload.get("error") is not None:
            print("error=%s" % payload["error"])
        for key in ("processing_mode", "batch_id", "status", "next_action"):
            if payload.get(key) is not None:
                print("%s=%s" % (key, payload[key]))


def run(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            payload = cmd_init(args)
        elif args.command == "status":
            payload = cmd_status(args)
        elif args.command == "plan":
            if args.chapter < 1:
                raise LightweightSessionError("chapter must be positive")
            payload = cmd_plan(args)
        elif args.command == "register-answer-dependency":
            payload = cmd_register_answer_dependency(args)
        elif args.command == "set-answer-dependency":
            payload = cmd_set_answer_dependency(args)
        elif args.command == "remove-answer-dependency":
            payload = cmd_remove_answer_dependency(args)
        elif args.command == "record-visual":
            payload = cmd_record_visual(args)
        elif args.command == "abandon":
            payload = cmd_abandon(args)
        elif args.command == "replace-taught":
            payload = cmd_replace_taught(args)
        else:
            payload = cmd_mark_taught(args)
        code = 0
    except (ConflictError, LightweightSessionError, OSError,
            RuntimeError, TypeError, ValueError) as exc:
        payload = {
            "process_success": False,
            "error": str(exc),
            "command": args.command,
        }
        code = 2
    _emit(payload, args.as_json)
    return code


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
