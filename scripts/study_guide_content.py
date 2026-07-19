#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Typed, source-bound chapter content for human study guides.

This module deliberately does not render HTML/PDF.  It owns the durable teaching contract at
``notebook/chNN.guide.json`` and a generated, marker-bounded view inside ``notebook/chNN.md``.
Renderers may consume a manifest only after :func:`validate_manifest` succeeds.

CLI::

    python scripts/study_guide_content.py --workspace <ws> validate --chapter 1 [--input draft.json]
    python scripts/study_guide_content.py --workspace <ws> import --chapter 1 --input draft.json

Exit codes: 0 valid/imported; 1 unsafe IO or invalid content; 2 argparse usage or
explicit-full-processing gate failure.
"""

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import unicodedata
from contextlib import contextmanager

try:
    from . import exam_start
except (ImportError, ValueError):
    import exam_start

try:
    from .stable_ids import stable_item_id_problem
except (ImportError, ValueError):
    from stable_ids import stable_item_id_problem

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
    from ingestion.identifiers import file_sha256, normalize_workspace_path
    from ingestion.language import SOURCE_UNIT_LANGUAGE_CODES
    from ingestion.models import (
        ContentUnit,
        EXTRACTION_METHODS,
        PROVENANCE_VALUES,
        QUESTION_TEXT_STATUSES,
        QUIZ_SOURCES,
        QUIZ_SOURCE_TYPES,
        QUIZ_TYPES,
        SchemaValidationError,
        SourceRecord,
        UNIT_KINDS,
    )
    from ingestion.pipeline import verify_material_build_receipt
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
    from scripts.ingestion.identifiers import file_sha256, normalize_workspace_path
    from scripts.ingestion.language import SOURCE_UNIT_LANGUAGE_CODES
    from scripts.ingestion.models import (
        ContentUnit,
        EXTRACTION_METHODS,
        PROVENANCE_VALUES,
        QUESTION_TEXT_STATUSES,
        QUIZ_SOURCES,
        QUIZ_SOURCE_TYPES,
        QUIZ_TYPES,
        SchemaValidationError,
        SourceRecord,
        UNIT_KINDS,
    )
    from scripts.ingestion.pipeline import verify_material_build_receipt
    from scripts.ingestion.storage import ConflictError, workspace_publication_lock

try:
    from asset_policy import (
        audit_asset_policy,
        physical_asset_key,
    )
except ImportError:  # package import from the repository root
    from scripts.asset_policy import (
        audit_asset_policy,
        physical_asset_key,
    )

try:
    from asset_crops import (
        CropContractError,
        load_crop_receipt_report,
        verify_crop_asset_live_binding,
    )
except ImportError:  # package import from the repository root
    from scripts.asset_crops import (
        CropContractError,
        load_crop_receipt_report,
        verify_crop_asset_live_binding,
    )

try:
    from math_text_policy import (
        find_math_layout_hazard,
        find_unrendered_math_hazard,
        first_bare_latex_command,
    )
except ImportError:  # package import from the repository root
    from scripts.math_text_policy import (
        find_math_layout_hazard,
        find_unrendered_math_hazard,
        first_bare_latex_command,
    )

try:
    from study_guide_provenance import (
        PROVENANCE_ICONS as SHARED_PROVENANCE_ICONS,
        ProvenanceConflictError,
        clean_visible_provenance,
        forbidden_explanation_fragment,
        notebook_has_provenance_legend,
        notebook_legend_lines,
    )
except ImportError:  # package import from the repository root
    from scripts.study_guide_provenance import (
        PROVENANCE_ICONS as SHARED_PROVENANCE_ICONS,
        ProvenanceConflictError,
        clean_visible_provenance,
        forbidden_explanation_fragment,
        notebook_has_provenance_legend,
        notebook_legend_lines,
    )


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
ANSWER_EXPLANATION_MODES = {"ordinary", "isolated"}
MIN_ANSWER_EXPLANATION_CHARS = {"zh": 80, "en": 160}
PROVENANCE_EMOJI = dict(SHARED_PROVENANCE_ICONS)
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
# Concept/formula refs may point only at neutral course-material visuals.  Treating
# question_context as concept evidence would blur a typed question into the semantic layer, while
# answer-side roles would let an official solution masquerade as a concept or formula source.
SEMANTIC_ASSET_ROLES = {"figure", "diagram", "table"}
SOURCE_REF_ASSET_ROLE_POLICY = {
    "concept": (SEMANTIC_ASSET_ROLES, "concept"),
    "formula": (SEMANTIC_ASSET_ROLES, "formula"),
    "question": (PROMPT_ASSET_ROLES, "prompt"),
    "answer": (ANSWER_ASSET_ROLES, "answer"),
    "solution": (ANSWER_ASSET_ROLES, "answer"),
}
SOURCE_ITEM_ASSET_ROLES = PROMPT_ASSET_ROLES | ANSWER_ASSET_ROLES | {"student_attempt"}
CONTENT_UNIT_ASSET_ROLES = SOURCE_ITEM_ASSET_ROLES | {"source_page", "other"}
ASSET_TYPES = {"page_image", "crop_image", "diagram", "table_image", "other_image"}
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
# ``content_units.jsonl`` exists in both ingestion-v1 and ingestion-v2 workspaces.  v1 stored
# the student-facing Study Guide vocabulary directly (for example ``lecture``/``quiz``), while
# v2 writes the canonical ingestion vocabulary above and is additionally checked by the typed
# fact-integrity gate.  Whole-file control validation must therefore accept both closed sets;
# accepting the legacy aliases here does not weaken v2 because its ContentUnit validation still
# rejects non-canonical v2 metadata.
CONTENT_UNIT_SOURCE_TYPES = frozenset(QUIZ_SOURCE_TYPES) | frozenset(SOURCE_TYPES)
TEACHING_ROLES = frozenset(("paired_problem", "worked_example"))
ANSWER_STATUSES = frozenset(("unknown",))
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSONL_BYTES = 128 * 1024 * 1024
MAX_NOTEBOOK_BLOCK_CHARS = 8 * 1024 * 1024
MARKER_PREFIX = "EXAMPREP-STUDY-GUIDE-CONTENT:"
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


def _read_json(path, label, check_controls=True):
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
    if check_controls:
        _check_controls(value, label)
    return value


def _safe_relative_path(value, path, asset=False):
    if not isinstance(value, str) or not value.strip():
        raise ContentError("%s must be a non-empty relative POSIX path" % path)
    if value != value.strip() or "\\" in value:
        raise ContentError("%s must use canonical forward-slash path syntax" % path)
    if _DRIVE_RE.match(value) or _SCHEME_RE.match(value) or value.startswith(("/", "//")):
        raise ContentError("%s must not be absolute, a drive path, or a URL" % path)
    try:
        normalized = normalize_workspace_path(value)
    except ValueError as exc:
        raise ContentError("%s contains an unsafe path component or non-portable alias: %s"
                           % (path, exc))
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
    problem = stable_item_id_problem(value)
    if problem:
        raise ContentError("%s is not a safe stable identifier: %s" % (path, problem))
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


def _check_container_keys(value, path):
    """Reject control data in JSON object keys without inspecting authored values."""
    if isinstance(value, dict):
        for key, child in value.items():
            _check_controls(key, "%s.<key>" % path)
            _check_container_keys(child, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_container_keys(child, "%s[%d]" % (path, index))


def _check_content_unit_controls(row, label, chapter):
    """Apply whole-file structural controls and target-chapter authored controls.

    A Study Guide is chapter-scoped.  Foreign-chapter prose must not make the requested
    chapter unauthorable, but the JSONL remains one trust boundary: identifiers, source/path
    anchors, lifecycle fields, and every object key are still checked for every row.  Only the
    authored payload (``text``/``html``/``latex`` and semantic metadata values) is deferred for
    rows outside the requested chapter.
    """
    _check_content_unit_asset_structure(row, label)
    _validate_content_unit_routing_controls(row, label)
    metadata = row.get("metadata")
    _validate_content_unit_metadata_controls(metadata, label + ".metadata")
    if _content_unit_chapter(row.get("chapter_id")) == chapter:
        _check_controls(row, label)
        return
    authored_fields = {"text", "html", "latex", "metadata"}
    structural = {key: value for key, value in row.items() if key not in authored_fields}
    _check_controls(structural, label)
    _check_container_keys(metadata, label + ".metadata")


def _sha256(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContentError("%s must be a lowercase SHA-256 digest" % path)
    return value


def _positive_integer(value, path):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContentError("%s must be a positive integer" % path)
    return value


def _nonnegative_integer(value, path):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContentError("%s must be a non-negative integer" % path)
    return value


def _finite_number(value, path, *, minimum=None, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ContentError("%s must be a finite number" % path)
    number = float(value)
    if positive and number <= 0:
        raise ContentError("%s must be positive" % path)
    if minimum is not None and number < minimum:
        raise ContentError("%s must be at least %s" % (path, minimum))
    return number


def _bbox(value, path):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ContentError("%s must contain four finite numbers" % path)
    x0, y0, x1, y1 = [
        _finite_number(number, "%s[%d]" % (path, index), minimum=0)
        for index, number in enumerate(value)
    ]
    if x1 < x0 or y1 < y0:
        raise ContentError("%s must be ordered [x0,y0,x1,y1]" % path)
    return value


def _validate_content_unit_routing_controls(row, path):
    """Validate non-authored ContentUnit controls for every chapter.

    Legacy ingestion-v1 rows may omit the newer typed fields, but any field they do carry is
    still routing/revision data.  Foreign-chapter prose is intentionally not interpreted here;
    accepting a foreign body must never make its source identity, location, pairing, or lifecycle
    controls untyped.
    """
    kind = row.get("kind")
    if not isinstance(kind, str) or kind not in UNIT_KINDS:
        raise ContentError("%s.kind must be one of %s" % (path, sorted(UNIT_KINDS)))
    provenance = row.get("provenance")
    if not isinstance(provenance, str) or provenance not in PROVENANCE_VALUES:
        raise ContentError(
            "%s.provenance must be one of %s" % (path, sorted(PROVENANCE_VALUES))
        )
    if "schema_version" in row and row["schema_version"] != SCHEMA_VERSION:
        raise ContentError("%s.schema_version must be %d" % (path, SCHEMA_VERSION))
    source_id = row.get("source_id")
    if source_id is not None and (
            not isinstance(source_id, str)
            or not re.fullmatch(r"src_[0-9a-f]{64}", source_id)):
        raise ContentError("%s.source_id must be a canonical src_ SHA-256 identifier" % path)
    if row.get("source_sha256") is not None:
        _sha256(row["source_sha256"], path + ".source_sha256")
    if row.get("ordinal") is not None:
        _nonnegative_integer(row["ordinal"], path + ".ordinal")
    if row.get("bbox") is not None:
        _bbox(row["bbox"], path + ".bbox")
    unit_id = row.get("unit_id")
    for field in ("parent_unit_id", "paired_unit_id"):
        value = row.get(field)
        if value is None:
            continue
        _identifier(value, "%s.%s" % (path, field))
        if value == unit_id:
            raise ContentError("%s.%s cannot reference the unit itself" % (path, field))
    section_path = row.get("section_path")
    if section_path is not None:
        if not isinstance(section_path, list):
            raise ContentError("%s.section_path must be an array" % path)
        for index, value in enumerate(section_path):
            _text(value, "%s.section_path[%d]" % (path, index))
            _check_controls(value, "%s.section_path[%d]" % (path, index))
    if row.get("phase_id") is not None:
        _identifier(row["phase_id"], path + ".phase_id")
    method = row.get("method")
    if method is not None and (
            not isinstance(method, str) or method not in EXTRACTION_METHODS):
        raise ContentError("%s.method must be one of %s" % (path, sorted(EXTRACTION_METHODS)))
    confidence = row.get("confidence")
    if confidence is not None:
        confidence = _finite_number(confidence, path + ".confidence", minimum=0)
        if confidence > 1:
            raise ContentError("%s.confidence must be at most 1" % path)


def _validate_content_unit_metadata_controls(metadata, path):
    """Validate known control-plane metadata for every chapter, not authored prose."""
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise ContentError("%s must be an object" % path)
    if metadata.get("answer_source_file") is not None:
        _safe_relative_path(metadata["answer_source_file"], path + ".answer_source_file")
    for field in ("source_pages", "answer_source_pages"):
        if metadata.get(field) is not None:
            _positive_pages(metadata[field], "%s.%s" % (path, field))
    if metadata.get("asset_sha256") is not None:
        _sha256(metadata["asset_sha256"], path + ".asset_sha256")
    for field in ("requires_assets", "maybe_requires_assets", "gradable"):
        if field in metadata and metadata[field] is not None and type(metadata[field]) is not bool:
            raise ContentError("%s.%s must be true or false" % (path, field))
    enum_fields = {
        "quiz_type": QUIZ_TYPES,
        "source_type": CONTENT_UNIT_SOURCE_TYPES,
        "source": QUIZ_SOURCES,
        "source_language": SOURCE_UNIT_LANGUAGE_CODES,
        "question_text_status": QUESTION_TEXT_STATUSES,
    }
    for field, allowed in enum_fields.items():
        value = metadata.get(field)
        if value is not None and (not isinstance(value, str) or value not in allowed):
            raise ContentError(
                "%s.%s must be one of %s" % (path, field, sorted(allowed))
            )
    for field in ("language", "diagram_type", "expected_behavior"):
        value = metadata.get(field)
        if value is None:
            continue
        if (not isinstance(value, str) or not value or value != value.strip()):
            raise ContentError("%s.%s must be a non-empty trimmed string" % (path, field))
        _check_controls(value, "%s.%s" % (path, field))


def _validate_asset_control_record(asset, path, allowed_roles, *, workspace_asset=True,
                                   require_role=False):
    """Validate only stable asset control fields; leave unknown authored fields untouched."""
    if not isinstance(asset, dict):
        raise ContentError("%s must be an object" % path)
    if "path" not in asset:
        raise ContentError("%s.path is required" % path)
    _safe_relative_path(asset["path"], path + ".path", asset=workspace_asset)
    if asset.get("source_file") is not None:
        _safe_relative_path(asset["source_file"], path + ".source_file")
    for field in ("sha256", "source_sha256"):
        if asset.get(field) is not None:
            _sha256(asset[field], "%s.%s" % (path, field))
    if require_role and "role" not in asset:
        raise ContentError("%s.role is required" % path)
    if "role" in asset:
        role = asset["role"]
        if not isinstance(role, str) or role not in allowed_roles:
            raise ContentError("%s.role must be one of %s" % (path, sorted(allowed_roles)))
    if "type" in asset:
        asset_type = asset["type"]
        if not isinstance(asset_type, str) or asset_type not in ASSET_TYPES:
            raise ContentError("%s.type must be one of %s" % (path, sorted(ASSET_TYPES)))
    if "contains_full_prompt" in asset and type(asset["contains_full_prompt"]) is not bool:
        raise ContentError("%s.contains_full_prompt must be true or false" % path)
    for field in ("page", "source_page"):
        if asset.get(field) is not None:
            _positive_integer(asset[field], "%s.%s" % (path, field))
    if asset.get("source_pages") is not None:
        _positive_pages(asset["source_pages"], path + ".source_pages")
    if asset.get("bbox") is not None:
        _bbox(asset["bbox"], path + ".bbox")
    for field in ("width", "height", "w", "h"):
        if asset.get(field) is not None:
            _finite_number(asset[field], "%s.%s" % (path, field), positive=True)
    for field in ("x", "y"):
        if asset.get(field) is not None:
            _finite_number(asset[field], "%s.%s" % (path, field), minimum=0)


def _check_content_unit_asset_structure(row, label):
    if row.get("asset_path") is not None:
        _safe_relative_path(row["asset_path"], label + ".asset_path")
    if row.get("asset_role") is not None:
        if row.get("asset_path") is None:
            raise ContentError("%s.asset_role requires asset_path" % label)
        role = row["asset_role"]
        if not isinstance(role, str) or role not in CONTENT_UNIT_ASSET_ROLES:
            raise ContentError("%s.asset_role must be one of %s"
                               % (label, sorted(CONTENT_UNIT_ASSET_ROLES)))
    metadata = row.get("metadata")
    if not isinstance(metadata, dict) or "assets" not in metadata:
        return
    assets = metadata["assets"]
    if not isinstance(assets, list):
        raise ContentError("%s.metadata.assets must be an array" % label)
    for index, asset in enumerate(assets):
        _validate_asset_control_record(
            asset, "%s.metadata.assets[%d]" % (label, index), CONTENT_UNIT_ASSET_ROLES,
            workspace_asset=False, require_role=True,
        )


def _read_content_units(ws, chapter):
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
        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ContentError("%s.metadata must be an object when present" % label)
        external_id = row.get("external_id")
        if external_id is not None:
            if not isinstance(external_id, str):
                raise ContentError("%s.external_id must be a string when present" % label)
            row = dict(row)
            row["external_id"] = _identifier(external_id, label + ".external_id")
        _check_content_unit_controls(row, label, chapter)
        by_id[unit_id] = row
        rows.append(row)
    for index, row in enumerate(rows, 1):
        label = ".ingest/content_units.jsonl:%d" % index
        for field in ("parent_unit_id", "paired_unit_id"):
            target = row.get(field)
            if target is not None and target not in by_id:
                raise ContentError(
                    "%s.%s references unknown content unit %r" % (label, field, target)
                )
    return True, rows, by_id


def _semantic_unit_ids(rows, chapter):
    output = []
    by_kind = {}
    for row in rows:
        if _content_unit_chapter(row.get("chapter_id")) != chapter:
            continue
        kind = row.get("kind")
        if (kind not in SEMANTIC_UNIT_KINDS
                or row.get("provenance") not in ("material", "ai_recovered")
                or row.get("asset_role") == "student_attempt"):
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


def _localized(value, language, path, skip_math_codes=()):
    _shape(value, path, (), ("zh", "en"))
    required = _target_languages(language)
    if not required.issubset(value):
        raise ContentError("%s must contain %s for language=%s"
                           % (path, "+".join(sorted(required)), language))
    if not value:
        raise ContentError("%s must contain localized text" % path)
    for key, text in value.items():
        text_path = "%s.%s" % (path, key)
        _text(text, text_path)
        if key not in set(skip_math_codes):
            _validate_localized_prose_math(text, text_path)
    return value


def _field_provenance(value, localized_value, path, allowed=EXPLANATION_PROVENANCE):
    """Validate one explicit per-language provenance sidecar.

    Legacy manifests may omit the sidecar entirely.  Once present, however, it
    must describe exactly the authored language keys and use only the declared
    control-plane vocabulary; partial or decorative provenance is rejected.
    """

    _shape(value, path, (), ("zh", "en"))
    if set(value) != set(localized_value):
        raise ContentError(
            "%s must label every authored language exactly; missing=%s extra=%s"
            % (path, sorted(set(localized_value) - set(value)),
               sorted(set(value) - set(localized_value)))
        )
    for code, label in value.items():
        if label not in allowed:
            raise ContentError(
                "%s.%s must be one of %s" % (path, code, sorted(allowed))
            )
    return value


def _validate_detailed_answer_explanation(value, provenance, answer, language,
                                          path):
    """Validate one beginner-facing explanation independent of execution mode."""

    explanation = _localized(value, language, path)
    expected_languages = _target_languages(language)
    if set(explanation) != expected_languages:
        raise ContentError(
            "%s must contain exactly target languages %s"
            % (path, sorted(expected_languages))
        )
    labels = _field_provenance(
        provenance, explanation, path + "_provenance",
        allowed={"ai_supplement"},
    )
    if any(label != "ai_supplement" for label in labels.values()):
        raise ContentError("%s_provenance must be ai_supplement" % path)
    for code in sorted(expected_languages):
        text = explanation[code]
        forbidden_reason = forbidden_explanation_fragment(text)
        if forbidden_reason:
            raise ContentError(
                "%s.%s contains forbidden %s" % (path, code, forbidden_reason)
            )
        meaningful = len(re.sub(r"\s+", "", text))
        minimum = MIN_ANSWER_EXPLANATION_CHARS[code]
        if meaningful < minimum:
            raise ContentError(
                "%s.%s is too short for a detailed beginner explanation; "
                "need at least %d non-whitespace characters"
                % (path, code, minimum)
            )
        supplied_answer = answer.get(code) if isinstance(answer, dict) else None
        if (isinstance(supplied_answer, str)
                and re.sub(r"\s+", "", supplied_answer)
                == re.sub(r"\s+", "", text)):
            raise ContentError(
                "%s.%s merely repeats the answer instead of explaining it"
                % (path, code)
            )
    return explanation


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
        text_path = "%s.%s" % (path, key)
        _text(text, text_path)
        _validate_localized_prose_math(text, text_path)
    return value


def _validate_localized_prose_math(value, path):
    """Reject raw TeX in prose without guessing how the author meant to fix it."""

    command = first_bare_latex_command(value)
    if command:
        raise ContentError(
            "%s contains raw LaTeX command %r outside standard $...$ or $$...$$ "
            "delimiters; wrap intended math explicitly or rewrite the prose without TeX "
            "(automatic rewriting is disabled)" % (path, command)
        )
    # Preserve the more specific flattened-OCR diagnosis for multiline source
    # evidence; the reviewed teaching-field gate owns that recovery path.
    hazard = None if find_math_layout_hazard(value) else find_unrendered_math_hazard(value)
    if hazard:
        raise ContentError(
            "%s contains unrendered math notation %r outside standard $...$ or $$...$$ "
            "delimiters; wrap intended math explicitly or rewrite the prose "
            "(automatic rewriting is disabled)"
            % (path, hazard["snippet"])
        )


def _inline_tex(value, path):
    value = _text(value, path)
    if value != value.strip() or "\n" in value or "\r" in value or "$" in value:
        raise ContentError(
            "%s must be trimmed one-line TeX without Markdown $ delimiters" % path
        )
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
        if (isinstance(metadata, dict)
                and isinstance(metadata.get("asset_sha256"), str)):
            direct["sha256"] = metadata["asset_sha256"]
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
    roles.extend(record.get("role") for record in _unit_asset_records(unit)
                 if isinstance(record.get("role"), str))
    for role in roles:
        if role == "question_context":
            sides.add("question")
        elif role in ANSWER_ASSET_ROLES:
            sides.add("answer")
    return sides


def _bound_asset_records(unit, asset_path):
    """Return every real asset record bound to one exact workspace-relative path."""
    wanted = physical_asset_key(asset_path)
    if wanted is None:
        return []
    return [record for record in _unit_asset_records(unit or {})
            if physical_asset_key(record.get("path")) == wanted]


def _validate_ref_asset_roles(records, ref_role, path, unit_id):
    """Fail closed when an explicit source asset is not typed for the requested side."""
    roles = [record.get("role") for record in records]
    if any(role is not None and not isinstance(role, str) for role in roles):
        raise ContentError(
            "%s.asset_path has a non-string real asset role in source unit %s"
            % (path, unit_id)
        )
    if "student_attempt" in roles:
        raise ContentError(
            "%s.asset_path is bound to student_attempt evidence in source unit %s; "
            "student work cannot support a Guide source ref" % (path, unit_id)
        )
    # Legacy refs may omit role; the structured-workspace gate below emits the canonical
    # "role is required" error.  Once a role is present, every supported role has an explicit
    # asset policy and none may fall through permissively.
    if ref_role is None:
        return
    policy = SOURCE_REF_ASSET_ROLE_POLICY.get(ref_role)
    if policy is None:
        raise ContentError("%s has no explicit asset-role policy for ref role %r"
                           % (path, ref_role))
    expected, side = policy
    if not any(role in expected for role in roles):
        raise ContentError(
            "%s.asset_path has no real %s-side asset role in source unit %s; roles=%s"
            % (path, side, unit_id, roles)
        )
    conflicts = sorted({
        "<missing>" if role is None else str(role)
        for role in roles if role not in expected
    })
    if conflicts:
        raise ContentError(
            "%s.asset_path has conflicting real asset roles in source unit %s; "
            "expected=%s conflicts=%s"
            % (path, unit_id, sorted(expected), conflicts)
        )


def _require_revision_bound_asset(records, path):
    """Require an exact current-byte digest for structured visual evidence."""

    if not any(
            isinstance(record.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            for record in records):
        raise ContentError(
            "%s must be bound to an asset record with an exact sha256 revision "
            "in a structured workspace" % path
        )


def _validate_asset_side_conflicts(records, path):
    """Reject a shared path whose accumulated records disagree about evidence side."""
    raw_roles = [record.get("role") for record in records]
    if any(role is not None and not isinstance(role, str) for role in raw_roles):
        raise ContentError("%s has a non-string asset role" % path)
    roles = set(raw_roles)
    display_roles = sorted("<missing>" if role is None else role for role in roles)
    prompt = roles & PROMPT_ASSET_ROLES
    answer = roles & ANSWER_ASSET_ROLES
    if "student_attempt" in roles and (prompt or answer):
        raise ContentError(
            "%s has conflicting asset-side classification: student_attempt cannot share "
            "a path with prompt/official-answer roles; roles=%s" % (path, display_roles)
        )
    if prompt and answer:
        raise ContentError(
            "%s has conflicting asset-side classification across prompt and official-answer "
            "roles; roles=%s" % (path, display_roles)
        )


def _source_role_matches_unit(unit, role, asset_path_bound=False):
    """Bind a manifest source role to the ingestion unit's kind/typed side.

    ``page_anchor`` is the sole kind-neutral escape hatch.  It is accepted here only so the
    item-specific legacy-file/page check can bind old bank evidence later; an anchor by itself is
    never sufficient evidence.  Typed visual units may stand in for an old prompt/solution page
    only when their ingestion asset side says so.
    """
    # Student work is retained as audit evidence only.  A manifest must not launder a top-level
    # attempt unit into concept/formula/question/answer evidence merely by omitting asset_path.
    if unit.get("asset_role") == "student_attempt":
        return False
    kind = unit.get("kind")
    # Without an exact asset_path binding, a semantic ref would otherwise inherit every visual
    # attached to the unit.  Fail closed when that aggregate includes prompt, answer, solution, or
    # student-work evidence.  An explicit asset_path is checked record-by-record in _source_ref,
    # so a neutral concept figure may still be selected from a unit that also carries an unrelated
    # answer asset.
    if not asset_path_bound and role in ("concept", "formula"):
        aggregate_roles = {
            record.get("role") for record in _unit_asset_records(unit)
            if isinstance(record.get("role"), str)
        }
        disallowed = {"question_context", "student_attempt"} | ANSWER_ASSET_ROLES
        if aggregate_roles & disallowed:
            return False
    sides = _unit_declared_sides(unit)
    if role == "concept":
        return kind in CONCEPT_SOURCE_KINDS
    if role == "formula":
        return kind == "formula"
    if role == "question":
        if kind == "question":
            # Pipeline units intentionally retain every item asset in metadata.  The text kind,
            # not unrelated nested assets, identifies the question.  Explicit ref assets are
            # checked path-by-path above this role-only compatibility layer.
            return True
        return kind == "page_anchor" or (
            kind in {"figure", "diagram", "table"} and "question" in sides
        )
    if role in ("answer", "solution"):
        if kind == "answer":
            return True
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
    if "role" in value:
        role = value["role"]
        if not isinstance(role, str) or role not in SOURCE_ROLES:
            raise ContentError("%s.role must be one of %s" % (path, sorted(SOURCE_ROLES)))
    if value.get("role") == "formula" and "quote_span" in value:
        quote_span = value["quote_span"]
        if structured and source_unit is not None:
            matches_latex = (
                _normalize_exact_latex(quote_span)
                == _normalize_exact_latex(source_unit.get("latex"))
            )
            matches_text = (
                _normalize_exact_text(quote_span)
                == _normalize_exact_text(source_unit.get("text"))
            )
            if not matches_latex and not matches_text:
                raise ContentError(
                    "%s.quote_span must exactly match the bound formula unit text or "
                    "latex payload after whitespace/Unicode normalization" % path
                )
    if "asset_path" in value:
        asset_path = _workspace_asset(ws, value["asset_path"], path + ".asset_path")
        if source_unit is not None:
            bound = _bound_asset_records(source_unit, asset_path)
            if not bound:
                raise ContentError("%s.asset_path is not bound to source unit %s"
                                   % (path, unit))
            _validate_ref_asset_roles(bound, value.get("role"), path, unit)
            if (structured
                    and _ingestion_pipeline_version(ws) == "ingestion-v2"):
                _require_revision_bound_asset(bound, path + ".asset_path")
    if structured:
        role = value.get("role")
        if role is None:
            raise ContentError("%s.role is required in a structured workspace" % path)
        if not _source_role_matches_unit(
                source_unit, role, asset_path_bound="asset_path" in value):
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


def _source_item_chapters(value, path):
    chapters = []
    for key in ("chapter", "phase"):
        if value.get(key) is None:
            continue
        raw = value[key]
        if isinstance(raw, bool):
            raise ContentError("%s.%s must be a positive chapter integer" % (path, key))
        if isinstance(raw, int):
            number = raw
        elif isinstance(raw, str) and re.fullmatch(r"[1-9]\d*", raw):
            number = int(raw)
        else:
            raise ContentError("%s.%s must be a positive chapter integer" % (path, key))
        if number < 1:
            raise ContentError("%s.%s must be a positive chapter integer" % (path, key))
        chapters.append(number)
    if len(set(chapters)) > 1:
        raise ContentError("%s chapter and phase locators disagree" % path)
    return set(chapters)


_SOURCE_ITEM_CONTROL_FIELDS = {
    "id", "chapter", "phase", "type", "source_type", "source", "status",
    "answer_status", "question_text_status", "teaching_role", "role", "language",
    "source_language", "answer_source_language", "diagram_type", "difficulty", "gradable",
    "ai_generated",
    "requires_assets", "maybe_requires_assets", "source_file", "answer_source_file",
    "source_pages", "answer_source_pages",
}

_SOURCE_ASSET_CONTROL_FIELDS = {
    "path", "source_file", "sha256", "source_sha256", "role", "type", "bbox",
    "contains_full_prompt", "page", "source_page", "source_pages", "width", "height",
    "x", "y", "w", "h", "source_bbox_pdf_points", "crop_receipt_id",
    "crop_receipt_schema_version", "crop_spec_sha256",
    "semantic_purity_sha256", "semantic_purity_schema_version",
    "required_context_ids", "content_scope", "isolation",
}


def _source_item_structure(value, path):
    # Future producer versions may add authored prose, translations, OCR, or annotations under
    # names unknown to this version.  Foreign-chapter rows are therefore checked through a
    # control-plane allowlist, not an authored-field denylist.  Object keys remain part of the
    # trust boundary everywhere; target-chapter rows receive a whole-row check in
    # ``_workspace_array`` below.
    _check_container_keys(value, path)
    control_plane = {
        key: value[key] for key in _SOURCE_ITEM_CONTROL_FIELDS if key in value
    }
    _check_controls(control_plane, path)
    enum_fields = {
        "type": QUIZ_TYPES,
        "source": QUIZ_SOURCES,
        "question_text_status": QUESTION_TEXT_STATUSES,
        "teaching_role": TEACHING_ROLES,
        "source_language": SOURCE_UNIT_LANGUAGE_CODES,
        "answer_source_language": SOURCE_UNIT_LANGUAGE_CODES,
        "answer_status": ANSWER_STATUSES,
    }
    for key, allowed in enum_fields.items():
        current = value.get(key)
        if current is not None and (
                not isinstance(current, str) or current not in allowed):
            raise ContentError(
                "%s.%s must be one of %s" % (path, key, sorted(allowed))
            )
    if value.get("source_type") is not None:
        _canonical_source_type(value["source_type"], path + ".source_type")
    for key in ("status", "role", "language", "diagram_type"):
        current = value.get(key)
        if current is None:
            continue
        if not isinstance(current, str) or not current or current != current.strip():
            raise ContentError("%s.%s must be a non-empty trimmed string" % (path, key))
    difficulty = value.get("difficulty")
    if difficulty is not None and (
            isinstance(difficulty, bool) or not isinstance(difficulty, int)
            or not 1 <= difficulty <= 5):
        raise ContentError("%s.difficulty must be an integer from 1 to 5" % path)
    for key in ("source_file", "answer_source_file"):
        if value.get(key) is not None:
            _safe_relative_path(value[key], "%s.%s" % (path, key))
    for key in ("source_pages", "answer_source_pages"):
        if value.get(key) is not None:
            _positive_pages(value[key], "%s.%s" % (path, key))
    for key in ("gradable", "ai_generated", "requires_assets", "maybe_requires_assets"):
        if key in value and type(value[key]) is not bool:
            raise ContentError("%s.%s must be true or false" % (path, key))
    assets = value.get("assets")
    if assets is None:
        return
    if not isinstance(assets, list):
        raise ContentError("%s.assets must be an array" % path)
    for index, asset in enumerate(assets):
        asset_path = "%s.assets[%d]" % (path, index)
        if not isinstance(asset, dict):
            raise ContentError("%s must be an object" % asset_path)
        asset_controls = {
            key: asset[key] for key in _SOURCE_ASSET_CONTROL_FIELDS if key in asset
        }
        _check_controls(asset_controls, asset_path)
        _validate_asset_control_record(asset, asset_path, SOURCE_ITEM_ASSET_ROLES)


def _workspace_array(ws, relative, chapter, optional=False):
    full = os.path.join(ws, *relative.split("/"))
    if optional and not os.path.exists(full):
        return []
    _guard_workspace_child(ws, full, relative, require_file=True)
    value = _read_json(full, relative, check_controls=False)
    if not isinstance(value, list):
        raise ContentError("%s must contain a JSON array" % relative)
    seen = set()
    for index, row in enumerate(value):
        row_path = "%s[%d]" % (relative, index)
        if not isinstance(row, dict):
            raise ContentError("%s must be an object" % row_path)
        item_id = _source_item_id(row, row_path)
        if item_id in seen:
            raise ContentError("%s has duplicate id %r" % (relative, item_id))
        seen.add(item_id)
        chapters = _source_item_chapters(row, row_path)
        _source_item_structure(row, row_path)
        if chapter in chapters:
            _check_controls(row, row_path)
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
    for field in (
        "sha256", "source_file", "source_sha256", "source_page",
        "source_bbox_pdf_points", "crop_receipt_id",
        "crop_receipt_schema_version", "crop_spec_sha256",
        "semantic_purity_sha256", "semantic_purity_schema_version",
        "required_context_ids", "content_scope", "isolation",
    ):
        if field in asset:
            record[field] = copy.deepcopy(asset[field])
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


def _asset_policy_snapshot_from_rows(teaching, quizzes, units, *, workspace=None):
    """Validate and bind the complete, unfiltered workspace asset-policy inputs."""

    audit = audit_asset_policy(
        quiz_rows=quizzes,
        teaching_rows=teaching,
        content_units=units,
        workspace=workspace,
    )
    problems = audit["invalid_declarations"] + audit["conflicts"]
    if problems:
        raise ContentError("global asset policy failed: %s" % "; ".join(problems))
    canonical = json.dumps(
        {
            "quiz_rows": quizzes,
            "teaching_rows": teaching,
            "content_units": units,
            "tainted_keys": sorted(audit["tainted_keys"]),
            "tainted_identity_keys": sorted(audit["tainted_identity_keys"]),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return audit["tainted_keys"], hashlib.sha256(canonical).hexdigest()


def _validate_global_asset_policy(teaching, quizzes, units):
    """Apply attempt taint and same-item side rules before chapter filtering."""

    return _asset_policy_snapshot_from_rows(teaching, quizzes, units)[0]


def _source_inventory(workspace, chapter):
    """Build the exact item denominator plus typed source/asset evidence."""
    ws = _guard_workspace(workspace)
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise ContentError("chapter must be an integer >= 1")
    structured, units, unit_index = _read_content_units(ws, chapter)
    teaching = _workspace_array(
        ws, "references/teaching_examples.json", chapter, optional=not structured)
    quizzes = _workspace_array(ws, "references/quiz_bank.json", chapter)
    tainted_asset_keys, asset_policy_sha256 = _asset_policy_snapshot_from_rows(
        teaching, quizzes, units, workspace=ws
    )
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
                    or unit.get("kind") != "question"
                    or unit.get("asset_role") == "student_attempt"):
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
                    or unit.get("kind") != "answer" or not unit.get("external_id")
                    or unit.get("asset_role") == "student_attempt"):
                continue
            _item_evidence_bucket(item_evidence, unit["external_id"])[
                "answer_unit_ids"].add(unit["unit_id"])
        # Bind assets from both question and answer units sharing the source external ID.
        for index, unit in enumerate(units):
            if _content_unit_chapter(unit.get("chapter_id")) != chapter:
                continue
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
        physical_records = {}
        for relative, records in item_assets[item_id].items():
            key = physical_asset_key(relative)
            if key is None:
                raise ContentError("item %s has an unsafe asset path: %s" % (item_id, relative))
            physical_records.setdefault(key, []).extend(records)
        for records in physical_records.values():
            _validate_asset_side_conflicts(records, "item %s physical asset" % item_id)
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
        "tainted_asset_keys": tainted_asset_keys,
        "asset_policy_sha256": asset_policy_sha256,
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


def _workspace_answer_explanation_mode(ws):
    """Read the safe effective state choice; missing/invalid state is ordinary."""

    path = os.path.join(ws, "study_state.json")
    if not os.path.exists(path):
        return "ordinary"
    _guard_workspace_child(ws, path, "study_state.json", require_file=True)
    state = _read_json(path, "study_state.json")
    if not isinstance(state, dict):
        raise ContentError("study_state.json must be an object")
    return exam_start.i18n.workspace_answer_explanation_mode(state)


def _validate_quantity(value, language, path, require_provenance=False):
    required = ["label"]
    optional = ["symbol", "value", "unit", "provenance"]
    if require_provenance:
        required.append("provenance")
        optional.remove("provenance")
    _shape(value, path, required, optional)
    label = _localized(value["label"], language, path + ".label")
    if "provenance" in value:
        _field_provenance(
            value["provenance"], label, path + ".provenance")
    for key in ("symbol", "value", "unit"):
        if key in value:
            if key == "symbol":
                _inline_tex(value[key], "%s.%s" % (path, key))
            else:
                _text(value[key], "%s.%s" % (path, key))


def _validate_formula_use(value, language, path, require_provenance=False):
    required = [
        "formula_id", "why_applicable", "variable_mapping", "substitution",
    ]
    optional = ["why_applicable_provenance", "substitution_provenance"]
    if require_provenance:
        required.extend(("why_applicable_provenance", "substitution_provenance"))
        optional = []
    _shape(value, path, required, optional)
    formula_id = _identifier(value["formula_id"], path + ".formula_id")
    why_applicable = _localized(
        value["why_applicable"], language, path + ".why_applicable")
    if "why_applicable_provenance" in value:
        _field_provenance(
            value["why_applicable_provenance"], why_applicable,
            path + ".why_applicable_provenance")
    mappings = _list(value["variable_mapping"], path + ".variable_mapping")
    seen = set()
    for index, mapping in enumerate(mappings):
        mp = "%s.variable_mapping[%d]" % (path, index)
        mapping_required = ["symbol", "maps_to"]
        mapping_optional = ["maps_to_provenance"]
        if require_provenance:
            mapping_required.append("maps_to_provenance")
            mapping_optional = []
        _shape(mapping, mp, mapping_required, mapping_optional)
        symbol = _inline_tex(mapping["symbol"], mp + ".symbol")
        if symbol in seen:
            raise ContentError("%s.variable_mapping repeats symbol %r" % (path, symbol))
        seen.add(symbol)
        maps_to = _localized(mapping["maps_to"], language, mp + ".maps_to")
        if "maps_to_provenance" in mapping:
            _field_provenance(
                mapping["maps_to_provenance"], maps_to,
                mp + ".maps_to_provenance")
    substitution = _text(value["substitution"], path + ".substitution")
    if "\n" in substitution or "\r" in substitution:
        raise ContentError("%s.substitution must be one line of TeX" % path)
    if substitution.strip().startswith("$") or substitution.strip().endswith("$"):
        raise ContentError("%s.substitution stores TeX without Markdown $ delimiters" % path)
    if "substitution_provenance" in value:
        provenance = value["substitution_provenance"]
        if provenance != "ai_supplement":
            raise ContentError(
                "%s.substitution_provenance must equal ai_supplement" % path
            )
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


def _require_current_chapter_refs(refs, chapter, unit_index, path):
    """Apply the chapter/provenance gate to any structured Guide evidence layer."""
    for index, ref in enumerate(refs):
        ref_path = "%s[%d]" % (path, index)
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


def _validate_knowledge_point(ws, value, language, path, formula_ids, formula_symbols,
                              chapter=None, unit_index=None, structured=False,
                              require_provenance=False):
    required = ["id", "title", "explanation", "formulas", "source_refs", "example_ids"]
    optional = [
        "explanation_provenance", "example_note",
        "teaching_explanation", "teaching_explanation_provenance",
    ]
    if require_provenance:
        required.append("explanation_provenance")
        optional.remove("explanation_provenance")
    if structured:
        required.append("source_unit_ids")
    else:
        optional.append("source_unit_ids")
    _shape(value, path, required, optional)
    kp_id = _identifier(value["id"], path + ".id")
    _localized(value["title"], language, path + ".title")
    provenance = value.get("explanation_provenance")
    material_evidence_codes = {
        code for code, label in provenance.items() if label == "material"
    } if isinstance(provenance, dict) and "teaching_explanation" in value else set()
    explanation = _localized(
        value["explanation"], language, path + ".explanation",
        skip_math_codes=material_evidence_codes,
    )
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
    if ("teaching_explanation" in value) != (
            "teaching_explanation_provenance" in value):
        raise ContentError(
            "%s teaching_explanation and teaching_explanation_provenance must appear together"
            % path
        )
    if "teaching_explanation" in value:
        teaching = _localized(
            value["teaching_explanation"], language, path + ".teaching_explanation")
        teaching_provenance = value["teaching_explanation_provenance"]
        _shape(
            teaching_provenance, path + ".teaching_explanation_provenance",
            (), ("zh", "en"))
        if (set(teaching_provenance) != set(teaching)
                or any(label != "ai_supplement"
                       for label in teaching_provenance.values())):
            raise ContentError(
                "%s.teaching_explanation_provenance must label every language "
                "ai_supplement" % path
            )
        for code, text in teaching.items():
            hazard = find_math_layout_hazard(text)
            if hazard:
                raise ContentError(
                    "%s.teaching_explanation.%s still contains %s OCR math layout; "
                    "the reviewed teaching copy itself must use readable typeset math"
                    % (path, code, hazard["code"])
                )
    effective_provenance = provenance or {code: "material" for code in explanation}
    for code, label in effective_provenance.items():
        hazard = find_math_layout_hazard(explanation[code]) if label == "material" else None
        if hazard and "teaching_explanation" not in value:
            raise ContentError(
                "%s.explanation.%s contains %s OCR math layout; keep the exact evidence "
                "but add a reviewed teaching_explanation with typeset math"
                % (path, code, hazard["code"])
            )
    concept_refs = _source_refs(
        ws, value["source_refs"], path + ".source_refs",
        unit_index=unit_index, structured=structured,
        expected_role="concept" if structured else None)
    source_unit_ids = _unique_strings(
        value.get("source_unit_ids", []), path + ".source_unit_ids", identifiers=True)
    examples = _unique_strings(value["example_ids"], path + ".example_ids", identifiers=True)
    if "example_note" in value:
        _localized(value["example_note"], language, path + ".example_note")
        if examples:
            raise ContentError(
                "%s.example_note is allowed only when example_ids is empty" % path
            )
    formulas = _list(value["formulas"], path + ".formulas")
    local_formula_ids = set()
    derived_source_unit_ids = set()
    if structured:
        derived_source_unit_ids.update(_knowledge_ref_unit_ids(
            concept_refs, "concept", chapter, unit_index, path + ".source_refs"))
    for index, formula in enumerate(formulas):
        fp = "%s.formulas[%d]" % (path, index)
        formula_required = [
            "id", "latex", "explanation", "variables", "applicability", "source_refs",
        ]
        formula_optional = ["explanation_provenance", "applicability_provenance"]
        if require_provenance:
            formula_required.extend((
                "explanation_provenance", "applicability_provenance",
            ))
            formula_optional = []
        _shape(formula, fp, formula_required, formula_optional)
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
        formula_explanation = _localized(
            formula["explanation"], language, fp + ".explanation")
        formula_applicability = _localized(
            formula["applicability"], language, fp + ".applicability")
        if "explanation_provenance" in formula:
            _field_provenance(
                formula["explanation_provenance"], formula_explanation,
                fp + ".explanation_provenance")
        if "applicability_provenance" in formula:
            _field_provenance(
                formula["applicability_provenance"], formula_applicability,
                fp + ".applicability_provenance")
        variables = _list(formula["variables"], fp + ".variables")
        symbols = set()
        for vi, variable in enumerate(variables):
            vp = "%s.variables[%d]" % (fp, vi)
            variable_required = ["symbol", "meaning"]
            variable_optional = ["meaning_provenance"]
            if require_provenance:
                variable_required.append("meaning_provenance")
                variable_optional = []
            _shape(variable, vp, variable_required, variable_optional)
            symbol = _inline_tex(variable["symbol"], vp + ".symbol")
            if symbol in symbols:
                raise ContentError("%s.variables repeats symbol %r" % (fp, symbol))
            symbols.add(symbol)
            meaning = _localized(variable["meaning"], language, vp + ".meaning")
            if "meaning_provenance" in variable:
                _field_provenance(
                    variable["meaning_provenance"], meaning,
                    vp + ".meaning_provenance")
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
        if ref.get("role") not in ("question", "answer", "solution"):
            continue
        source_unit = (unit_index or {}).get(ref.get("source_unit_id"))
        bound = _bound_asset_records(source_unit, asset_path)
        for record in bound:
            merged = dict(record)
            merged["contains_full_prompt"] = bool(
                merged.get("contains_full_prompt") or ref.get("contains_full_prompt"))
            output.setdefault(asset_path, []).append(merged)
    return output


def _ingestion_source_root(ws):
    """Return the current compiler-bound materials root for live crop checks."""

    path = os.path.join(ws, ".ingest", "build_manifest.json")
    _guard_workspace_child(
        ws, path, ".ingest/build_manifest.json", require_file=True)
    document = _read_json(path, ".ingest/build_manifest.json")
    source_root = document.get("source_root") if isinstance(document, dict) else None
    if not isinstance(source_root, str) or not source_root.strip():
        raise ContentError(
            ".ingest/build_manifest.json.source_root is required for live Study "
            "Guide crop verification"
        )
    source_root = os.path.abspath(source_root)
    if not os.path.isdir(source_root):
        raise ContentError(
            ".ingest/build_manifest.json.source_root is no longer a directory"
        )
    return source_root


def _validate_v2_live_crop_receipts(ws, chapter, walkthroughs, inventory):
    """Close every rendered page-shaped item asset to current crop/source bytes.

    Authoring packets already perform this check, but their result is not a
    capability token.  Canonical import and visual rendering call this function
    again so a hand-authored protocol flag, stale compact declaration, replaced
    crop, or changed source PDF cannot cross the publication boundary.
    """

    expected_chapter_id = "ch%02d" % chapter
    pending = []
    seen_declarations = set()
    for walk_index, walk in enumerate(walkthroughs):
        item_id = walk["item_id"]
        source_assets = inventory["item_assets"].get(item_id, {})
        trace_assets = _trace_asset_records(
            walk.get("source_trace") or [], inventory["unit_index"])
        for side, field, allowed_roles in (
            ("prompt", "prompt_asset_paths", PROMPT_ASSET_ROLES),
            ("answer", "answer_asset_paths", ANSWER_ASSET_ROLES),
        ):
            for asset_index, relative in enumerate(walk.get(field) or []):
                label = "$.walkthroughs[%d].%s[%d]" % (
                    walk_index, field, asset_index)
                records = (
                    list(source_assets.get(relative) or [])
                    + list(trace_assets.get(relative) or [])
                )
                page_records = [
                    record for record in records
                    if isinstance(record, dict) and (
                        record.get("type") in ("page_image", "crop_image")
                        or bool(record.get("contains_full_prompt"))
                    )
                ]
                if not page_records:
                    continue
                for record_index, record in enumerate(page_records):
                    record_label = "%s record %d" % (label, record_index)
                    if record.get("role") not in allowed_roles:
                        raise ContentError(
                            "%s is page-shaped but is not independently typed for "
                            "the %s side" % (record_label, side)
                        )
                    if not isinstance(record.get("crop_receipt_id"), str):
                        raise ContentError(
                            "%s is page-shaped and requires a current target-item "
                            "crop receipt; authoring_protocol_version is not proof"
                            % record_label
                        )
                    if side == "answer" and (
                            record.get("isolation") != "target_item_only"
                            or bool(record.get("required_context_ids"))):
                        raise ContentError(
                            "%s answer-side page crop must use "
                            "isolation=target_item_only with no required context"
                            % record_label
                        )
                    canonical = json.dumps(
                        record, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    )
                    identity = (item_id, side, canonical)
                    if identity in seen_declarations:
                        continue
                    seen_declarations.add(identity)
                    pending.append((item_id, side, record_label, copy.deepcopy(record)))

    if not pending:
        return {
            "required": True,
            "status": "verified",
            "verified_asset_count": 0,
            "crop_receipt_ids": [],
            "verified_asset_bindings": [],
        }

    try:
        unused_report, receipt_index = load_crop_receipt_report(ws)
    except (CropContractError, OSError, TypeError, ValueError) as exc:
        raise ContentError(
            "live Study Guide crop receipt inventory is invalid: %s" % exc
        ) from exc
    source_root = _ingestion_source_root(ws)
    verified_ids = []
    verified_bindings = {}
    for item_id, side, label, asset in pending:
        try:
            receipt = verify_crop_asset_live_binding(
                ws,
                source_root,
                asset,
                receipt_index,
                expected_item_id=item_id,
                expected_chapter_id=expected_chapter_id,
            )
        except (CropContractError, OSError, TypeError, ValueError) as exc:
            raise ContentError(
                "%s failed live target-item crop verification: %s" % (label, exc)
            ) from exc
        if receipt.side != side:
            raise ContentError(
                "%s receipt side=%s does not match the rendered %s side"
                % (label, receipt.side, side)
            )
        if side == "answer" and (
                receipt.isolation != "target_item_only"
                or receipt.semantic_purity.required_context_ids):
            raise ContentError(
                "%s answer receipt must remain target_item_only with no required "
                "context" % label
            )
        verified_ids.append(receipt.crop_receipt_id)
        binding = {
            "path": receipt.output_path,
            "crop_receipt_id": receipt.crop_receipt_id,
            "sha256": receipt.output_sha256,
            "width": receipt.output_width,
            "height": receipt.output_height,
        }
        previous = verified_bindings.get(receipt.output_path)
        if previous is not None and previous != binding:
            raise ContentError(
                "%s resolves to conflicting live crop bindings" % receipt.output_path
            )
        verified_bindings[receipt.output_path] = binding
    return {
        "required": True,
        "status": "verified",
        "verified_asset_count": len(pending),
        "crop_receipt_ids": sorted(set(verified_ids)),
        "verified_asset_bindings": [
            verified_bindings[path] for path in sorted(verified_bindings)
        ],
    }


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
    if declared == "zxx":
        if required:
            raise ContentError(
                "%s.metadata.source_language=zxx has no natural language; "
                "a question/prompt requires explicit zh or en" % path)
        return None
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
                          profile=None, item_asset_requirements=None, chapter=None,
                          require_answer_explanation=False,
                          require_answer_explanation_receipt=False,
                          require_provenance=False):
    required = [
        "item_id", "source_type", "answer_provenance", "knowledge_point_ids", "title",
        "original_language", "prompt_asset_mode", "prompt_asset_paths", "answer_asset_paths",
        "translation", "what_asked", "known_quantities", "unknown_quantities",
        "formula_uses", "steps", "answer", "source_trace",
    ]
    optional = [
        "prompt_text", "solution_kind", "no_formula_reason", "knowledge_point_uses",
        "notebook_anchor", "notebook_block_sha256",
        "teaching_answer", "teaching_answer_provenance",
        "translation_provenance", "what_asked_provenance",
        "knowledge_point_uses_provenance", "steps_provenance",
        "self_check_provenance", "no_formula_reason_provenance",
        "self_check", "answer_explanation", "answer_explanation_provenance",
        "answer_explanation_receipt",
    ]
    if require_answer_explanation:
        required.extend((
            "answer_explanation", "answer_explanation_provenance",
        ))
        optional = [key for key in optional if key not in (
            "answer_explanation", "answer_explanation_provenance",
        )]
    if require_answer_explanation_receipt:
        if not require_answer_explanation:
            raise ContentError(
                "%s internal contract requires an explanation with its receipt" % path
            )
        required.append("answer_explanation_receipt")
        optional.remove("answer_explanation_receipt")
    if structured:
        required.extend(("solution_kind", "knowledge_point_uses", "notebook_anchor"))
        optional = [key for key in optional
                    if key not in ("solution_kind", "knowledge_point_uses", "notebook_anchor")]
    if require_provenance:
        provenance_required = (
            "knowledge_point_uses", "translation_provenance",
            "what_asked_provenance", "knowledge_point_uses_provenance",
            "steps_provenance",
        )
        required.extend(key for key in provenance_required if key not in required)
        optional = [key for key in optional if key not in provenance_required]
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
    notebook_block_sha256 = value.get("notebook_block_sha256")
    if notebook_block_sha256 is not None:
        _sha256(notebook_block_sha256, path + ".notebook_block_sha256")
        if notebook_anchor is None:
            raise ContentError(
                "%s.notebook_block_sha256 requires notebook_anchor" % path
            )
    source_type = _canonical_source_type(value["source_type"], path + ".source_type",
                                         manifest=True)
    if require_provenance and not isinstance(value["answer_provenance"], dict):
        raise ContentError(
            "%s.answer_provenance must be a per-language object for "
            "authoring_protocol_version=2" % path
        )
    answer_provenance = _validate_answer_provenance(
        value["answer_provenance"], language, path + ".answer_provenance", structured)
    kp_ids = _unique_strings(value["knowledge_point_ids"], path + ".knowledge_point_ids",
                             nonempty=True, identifiers=True)
    knowledge_point_uses = _validate_knowledge_point_uses(
        value.get("knowledge_point_uses"), kp_ids, language,
        path + ".knowledge_point_uses", structured or require_provenance)
    if "knowledge_point_uses_provenance" in value:
        uses_provenance = value["knowledge_point_uses_provenance"]
        if not isinstance(uses_provenance, dict):
            raise ContentError(
                "%s.knowledge_point_uses_provenance must be an object keyed by "
                "knowledge-point ID" % path
            )
        if set(uses_provenance) != set(knowledge_point_uses):
            raise ContentError(
                "%s.knowledge_point_uses_provenance keys must exactly equal "
                "knowledge_point_uses; missing=%s extra=%s"
                % (path,
                   sorted(set(knowledge_point_uses) - set(uses_provenance)),
                   sorted(set(uses_provenance) - set(knowledge_point_uses)))
            )
        for kp_id, provenance in uses_provenance.items():
            _field_provenance(
                provenance, knowledge_point_uses[kp_id],
                "%s.knowledge_point_uses_provenance.%s" % (path, kp_id))
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
    if structured:
        if chapter is None:
            raise ContentError("%s requires the active chapter in a structured workspace" % path)
        # Question/answer evidence receives stricter item binding below, but extra
        # concept/formula/page refs are still authoritative Guide evidence.  Gate
        # the entire trace so a foreign or AI-supplemented unit cannot ride along
        # unexamined (or later acquire a current-chapter ClaimSubject).
        _require_current_chapter_refs(
            source_trace, chapter, unit_index, path + ".source_trace")
    trace_assets = _trace_asset_records(source_trace, unit_index)
    source_assets = item_assets or {}
    for index, asset in enumerate(prompt_assets):
        _workspace_asset(ws, asset, "%s.prompt_asset_paths[%d]" % (path, index))
        records = list(source_assets.get(asset) or []) + list(trace_assets.get(asset) or [])
        _validate_asset_side_conflicts(
            records, "%s.prompt_asset_paths[%d]" % (path, index)
        )
        if (structured
                and _ingestion_pipeline_version(ws) == "ingestion-v2"):
            _require_revision_bound_asset(
                records, "%s.prompt_asset_paths[%d]" % (path, index)
            )
        if not any(record.get("role") in PROMPT_ASSET_ROLES for record in records):
            raise ContentError("%s.prompt_asset_paths[%d] is not bound to a prompt-side source asset"
                               % (path, index))
    for index, asset in enumerate(answer_assets):
        _workspace_asset(ws, asset, "%s.answer_asset_paths[%d]" % (path, index))
        records = list(source_assets.get(asset) or []) + list(trace_assets.get(asset) or [])
        _validate_asset_side_conflicts(
            records, "%s.answer_asset_paths[%d]" % (path, index)
        )
        if (structured
                and _ingestion_pipeline_version(ws) == "ingestion-v2"):
            _require_revision_bound_asset(
                records, "%s.answer_asset_paths[%d]" % (path, index)
            )
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
    translation = _translation(
        value["translation"], language, original, path + ".translation")
    if "translation_provenance" in value:
        translation_provenance = _field_provenance(
            value["translation_provenance"], translation,
            path + ".translation_provenance", allowed={"ai_translation"})
        if any(label != "ai_translation"
               for label in translation_provenance.values()):
            raise ContentError(
                "%s.translation_provenance must label generated translations "
                "ai_translation" % path
            )
    what_asked = _localized(
        value["what_asked"], language, path + ".what_asked")
    if "what_asked_provenance" in value:
        _field_provenance(
            value["what_asked_provenance"], what_asked,
            path + ".what_asked_provenance")
    for field in ("known_quantities", "unknown_quantities"):
        rows = _list(value[field], "%s.%s" % (path, field))
        for index, quantity in enumerate(rows):
            _validate_quantity(
                quantity, language, "%s.%s[%d]" % (path, field, index),
                require_provenance=require_provenance,
            )
    formula_uses = _list(value["formula_uses"], path + ".formula_uses")
    solution_kind = value.get("solution_kind") or ("formula" if formula_uses else "concept")
    if solution_kind not in SOLUTION_KINDS:
        raise ContentError("%s.solution_kind must be one of %s"
                           % (path, sorted(SOLUTION_KINDS)))
    no_formula_reason = value.get("no_formula_reason")
    if "no_formula_reason_provenance" in value and no_formula_reason is None:
        raise ContentError(
            "%s.no_formula_reason_provenance requires no_formula_reason" % path
        )
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
            if structured or require_provenance:
                raise ContentError("%s.no_formula_reason is required for non-formula solutions" % path)
        else:
            localized_reason = _localized(
                no_formula_reason, language, path + ".no_formula_reason")
            if require_provenance and "no_formula_reason_provenance" not in value:
                raise ContentError(
                    "%s.no_formula_reason_provenance is required for "
                    "authoring_protocol_version=2" % path
                )
            if "no_formula_reason_provenance" in value:
                _field_provenance(
                    value["no_formula_reason_provenance"], localized_reason,
                    path + ".no_formula_reason_provenance")
    used_formula_ids = []
    mapped_symbols = []
    for index, formula_use in enumerate(formula_uses):
        formula_id, symbols = _validate_formula_use(
            formula_use, language, "%s.formula_uses[%d]" % (path, index),
            require_provenance=require_provenance,
        )
        used_formula_ids.append(formula_id)
        mapped_symbols.append((formula_id, symbols, index))
    steps = _list(value["steps"], path + ".steps", nonempty=True)
    for index, step in enumerate(steps):
        _localized(step, language, "%s.steps[%d]" % (path, index))
    if "steps_provenance" in value:
        steps_provenance = _list(
            value["steps_provenance"], path + ".steps_provenance")
        if len(steps_provenance) != len(steps):
            raise ContentError(
                "%s.steps_provenance must align one-to-one with steps" % path
            )
        for index, provenance in enumerate(steps_provenance):
            _field_provenance(
                provenance, steps[index],
                "%s.steps_provenance[%d]" % (path, index))
    material_evidence_codes = {
        code for code, label in answer_provenance.items() if label == "material"
    } if "teaching_answer" in value else set()
    answer = _localized(
        value["answer"], language, path + ".answer",
        skip_math_codes=material_evidence_codes,
    )
    if ("teaching_answer" in value) != ("teaching_answer_provenance" in value):
        raise ContentError(
            "%s teaching_answer and teaching_answer_provenance must appear together" % path
        )
    if "teaching_answer" in value:
        teaching_answer = _localized(
            value["teaching_answer"], language, path + ".teaching_answer")
        teaching_provenance = value["teaching_answer_provenance"]
        _shape(
            teaching_provenance, path + ".teaching_answer_provenance",
            (), ("zh", "en"))
        if (set(teaching_provenance) != set(teaching_answer)
                or any(label not in ("ai_supplemented", "ai_generated")
                       for label in teaching_provenance.values())):
            raise ContentError(
                "%s.teaching_answer_provenance must label every language "
                "ai_supplemented or ai_generated" % path
            )
        for code, text in teaching_answer.items():
            hazard = find_math_layout_hazard(text)
            if hazard:
                raise ContentError(
                    "%s.teaching_answer.%s still contains %s OCR math layout; "
                    "the reviewed teaching copy itself must use readable typeset math"
                    % (path, code, hazard["code"])
                )
    for code, label in answer_provenance.items():
        hazard = find_math_layout_hazard(answer[code]) if label == "material" else None
        if hazard and "teaching_answer" not in value:
            raise ContentError(
                "%s.answer.%s contains %s OCR math layout; keep the exact evidence but "
                "add a reviewed teaching_answer with typeset math"
                % (path, code, hazard["code"])
            )
    if ("self_check" in value) != ("self_check_provenance" in value):
        raise ContentError(
            "%s legacy self_check and self_check_provenance must appear together" % path
        )
    if "self_check" in value:
        self_check = _localized(
            value["self_check"], language, path + ".self_check")
        _field_provenance(
            value["self_check_provenance"], self_check,
            path + ".self_check_provenance")
    has_explanation = "answer_explanation" in value
    has_explanation_provenance = "answer_explanation_provenance" in value
    if has_explanation != has_explanation_provenance:
        raise ContentError(
            "%s answer_explanation and answer_explanation_provenance must "
            "appear together" % path
        )
    if has_explanation:
        _validate_detailed_answer_explanation(
            value["answer_explanation"],
            value["answer_explanation_provenance"],
            value.get("teaching_answer", answer), language,
            path + ".answer_explanation",
        )
        has_explanation_receipt = "answer_explanation_receipt" in value
        if require_answer_explanation_receipt != has_explanation_receipt:
            raise ContentError(
                "%s answer_explanation_receipt presence does not match "
                "answer_explanation_mode" % path
            )
        if has_explanation_receipt:
            receipt = value["answer_explanation_receipt"]
            _shape(
                receipt, path + ".answer_explanation_receipt",
                (
                    "request_id", "request_sha256", "response_sha256",
                    "provider_receipt", "provider_receipt_sha256",
                    "response_event_sha256",
                ),
            )
            if not isinstance(receipt["request_id"], str) or not re.fullmatch(
                    r"answer_explanation_[0-9a-f]{64}", receipt["request_id"]):
                raise ContentError(
                    "%s.answer_explanation_receipt.request_id is invalid" % path
                )
            for field in (
                "request_sha256", "response_sha256", "provider_receipt_sha256",
                "response_event_sha256",
            ):
                _sha256(
                    receipt[field],
                    "%s.answer_explanation_receipt.%s" % (path, field),
                )
            provider = receipt["provider_receipt"]
            _shape(
                provider, path + ".answer_explanation_receipt.provider_receipt",
                (
                    "schema_version", "request_id", "request_sha256",
                    "instruction_sha256", "model_input_sha256",
                    "attachment_set_sha256", "provider_reported", "provider",
                    "model", "invocation_id", "isolation_mode", "tool_access",
                    "normalized_response_sha256",
                ),
            )
            if provider["schema_version"] != 2:
                raise ContentError(
                    "%s provider receipt schema_version must equal 2" % path
                )
            if provider["request_id"] != receipt["request_id"]:
                raise ContentError("%s provider receipt request_id disagrees" % path)
            for field in (
                "request_sha256", "instruction_sha256", "model_input_sha256",
                "attachment_set_sha256", "normalized_response_sha256",
            ):
                _sha256(
                    provider[field], "%s provider receipt.%s" % (path, field)
                )
            if type(provider["provider_reported"]) is not bool:
                raise ContentError("%s provider_reported must be boolean" % path)
            if provider["provider_reported"]:
                if (not isinstance(provider["provider"], str)
                        or not provider["provider"].strip()):
                    raise ContentError(
                        "%s provider name is required when reported" % path
                    )
                if (not isinstance(provider["model"], str)
                        or not provider["model"].strip()):
                    raise ContentError(
                        "%s model name is required when reported" % path
                    )
            elif provider["provider"] is not None or provider["model"] is not None:
                raise ContentError("%s provider/model must be null when unreported" % path)
            if provider["isolation_mode"] not in ("fresh_context", "stateless_api"):
                raise ContentError("%s provider isolation_mode is invalid" % path)
            if provider["tool_access"] != "disabled":
                raise ContentError(
                    "%s answer explanation provider tools must be disabled" % path
                )
            if (
                provider["request_sha256"] != receipt["request_sha256"]
                or provider["normalized_response_sha256"]
                    != receipt["response_sha256"]
            ):
                raise ContentError(
                    "%s answer explanation provider receipt hashes disagree" % path
                )
    elif any(field in value for field in (
            "answer_explanation_provenance", "answer_explanation_receipt")):
        raise ContentError(
            "%s answer explanation fields must appear together" % path
        )
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
        "notebook_block_sha256": notebook_block_sha256,
    }


def _validate_omission(ws, value, language, path, chapter=None, unit_index=None,
                       structured=False):
    _shape(value, path, ("item_id", "knowledge_point_ids", "reason", "source_refs"))
    item_id = _identifier(value["item_id"], path + ".item_id")
    kp_ids = _unique_strings(value["knowledge_point_ids"], path + ".knowledge_point_ids",
                             nonempty=True, identifiers=True)
    _localized(value["reason"], language, path + ".reason")
    refs = _source_refs(ws, value["source_refs"], path + ".source_refs",
                        unit_index=unit_index, structured=structured)
    if structured:
        _require_current_chapter_refs(
            refs, chapter, unit_index, path + ".source_refs"
        )
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
            refs = _source_refs(
                ws, row["source_refs"], row_path + ".source_refs", nonempty=True,
                unit_index=unit_index, structured=structured,
                expected_role="concept" if structured else None)
            if structured:
                _knowledge_ref_unit_ids(
                    refs, "concept", chapter, unit_index, row_path + ".source_refs"
                )
        output.append(unit_id)
    return output


def _notebook_block_sha256(lines):
    """Hash one parsed notebook entry in notebook.py's canonical block form."""

    try:
        try:
            from scripts import notebook as notebook_engine
        except ImportError:  # direct ``python scripts/study_guide_content.py`` execution
            import notebook as notebook_engine
        return notebook_engine.block_sha256({"lines": lines})
    except (ImportError, ValueError) as exc:
        raise ContentError("cannot hash notebook.py walkthrough block: %s" % exc)


def _official_walkthrough_entries(ws, chapter):
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
        result[anchor] = {
            "item_id": block["id"],
            "notebook_block_sha256": _notebook_block_sha256(block["lines"]),
        }
    return result


def _official_walkthrough_anchors(ws, chapter):
    return {
        anchor: row["item_id"]
        for anchor, row in _official_walkthrough_entries(ws, chapter).items()
    }


def _validate_walkthrough_notebook_anchors(ws, chapter, walkthroughs, require_all=False):
    official = _official_walkthrough_entries(ws, chapter)
    validated = 0
    for index, walk in enumerate(walkthroughs):
        path = "$.walkthroughs[%d].notebook_anchor" % index
        anchor = walk.get("notebook_anchor")
        if anchor is None:
            if require_all:
                raise ContentError("%s is required before importing a true Study Guide" % path)
            continue
        _identifier(anchor, path)
        entry = official.get(anchor)
        if entry is None:
            raise ContentError(
                "%s does not identify a pre-existing notebook.py walkthrough entry" % path)
        entry_id = entry["item_id"]
        if entry_id != walk.get("item_id"):
            raise ContentError("%s belongs to notebook entry %r, not walkthrough item %r"
                               % (path, entry_id, walk.get("item_id")))
        expected_block_sha256 = walk.get("notebook_block_sha256")
        if (expected_block_sha256 is not None
                and entry["notebook_block_sha256"] != expected_block_sha256):
            raise ContentError(
                "$.walkthroughs[%d].notebook_block_sha256 disagrees with the live "
                "official notebook.py walkthrough block" % index
            )
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
    if (not isinstance(document, dict)
            or type(document.get("schema_version")) is not int
            or document.get("schema_version") not in (1, 2)):
        raise ContentError(".ingest/build_manifest.json has an invalid schema_version")
    try:
        verify_material_build_receipt(
            ws,
            build_manifest=document,
            required=document["schema_version"] == 2,
        )
    except Exception as exc:
        raise ContentError(
            ".ingest/build_manifest.json material generation is invalid: %s"
            % exc
        ) from exc
    version = document.get("pipeline_version")
    if version not in ("ingestion-v1", "ingestion-v2"):
        raise ContentError(".ingest/build_manifest.json pipeline_version is unsupported")
    if version == "ingestion-v2" and document["schema_version"] != 2:
        raise ContentError(
            "ingestion-v2 requires build manifest schema_version 2; schema 1 is "
            "legacy/read-only and cannot claim the current generation gate"
        )
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
            workspace=ws,
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


def _canonical_json_sha256(value):
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContentError("answer explanation receipt is not canonical JSON: %s" % exc)
    return hashlib.sha256(payload).hexdigest()


def _validate_answer_explanation_gate(ws, chapter, manifest,
                                      allow_legacy_isolated=False):
    """Recompute the isolated per-item receipt and compare every visible field."""

    contract = manifest["answer_explanation_contract"]
    _shape(
        contract, "$.answer_explanation_contract",
        ("receipt_id", "receipt_sha256", "contract_id", "prompt_id", "prompt_sha256"),
    )
    _sha256(contract["receipt_sha256"],
            "$.answer_explanation_contract.receipt_sha256")
    _sha256(contract["prompt_sha256"],
            "$.answer_explanation_contract.prompt_sha256")
    try:
        try:
            import study_guide_explain as explain
        except ImportError:
            from scripts import study_guide_explain as explain
        receipt = explain.load_final_receipt(
            ws, chapter,
            allow_legacy_isolated=allow_legacy_isolated,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ContentError(
            "isolated answer explanation receipt is missing, incomplete, or stale: %s" % exc
        ) from exc
    expected_contract = {
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": _canonical_json_sha256(receipt),
        "contract_id": receipt["contract_id"],
        "prompt_id": receipt["prompt_id"],
        "prompt_sha256": receipt["prompt_sha256"],
    }
    if contract != expected_contract:
        raise ContentError(
            "$.answer_explanation_contract disagrees with the current isolated receipt"
        )
    live_items = {row["item_id"]: row for row in receipt["items"]}
    if set(live_items) != {row["item_id"] for row in manifest["walkthroughs"]}:
        raise ContentError(
            "isolated answer explanation receipt does not exactly cover walkthroughs"
        )
    receipt_fields = (
        "request_id", "request_sha256", "response_sha256", "provider_receipt",
        "provider_receipt_sha256", "response_event_sha256",
    )
    for index, walk in enumerate(manifest["walkthroughs"]):
        live = live_items[walk["item_id"]]
        if walk["answer_explanation"] != live["answer_explanation"]:
            raise ContentError(
                "$.walkthroughs[%d].answer_explanation disagrees with its isolated receipt"
                % index
            )
        if (walk["answer_explanation_provenance"]
                != live["answer_explanation_provenance"]):
            raise ContentError(
                "$.walkthroughs[%d].answer_explanation_provenance drifted" % index
            )
        expected_item_receipt = {
            key: live[key] for key in receipt_fields
        }
        if walk["answer_explanation_receipt"] != expected_item_receipt:
            raise ContentError(
                "$.walkthroughs[%d].answer_explanation_receipt drifted" % index
            )
    return expected_contract


def validate_manifest(workspace, chapter, manifest, _enforce_v2_claims=True,
                      _allow_legacy_isolated=False,
                      _enforce_v2_crop_receipts=False):
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
    pipeline_version = _ingestion_pipeline_version(ws) if structured else None
    top_required = [
        "schema_version", "chapter", "language", "profile", "knowledge_points",
        "walkthroughs", "omissions",
    ]
    top_optional = [
        "authoring_protocol_version", "answer_explanation_mode",
        "answer_explanation_contract",
    ]
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
    protocol_version = manifest.get("authoring_protocol_version")
    if protocol_version is not None and protocol_version != 2:
        raise ContentError("$.authoring_protocol_version must equal 2 when present")
    has_explanation_contract = "answer_explanation_contract" in manifest
    explanation_mode_explicit = "answer_explanation_mode" in manifest
    legacy_isolated_mode_inferred = False
    if explanation_mode_explicit:
        answer_explanation_mode = manifest["answer_explanation_mode"]
        if answer_explanation_mode not in ANSWER_EXPLANATION_MODES:
            raise ContentError(
                "$.answer_explanation_mode must be ordinary or isolated"
            )
        state_explanation_mode = _workspace_answer_explanation_mode(ws)
        if answer_explanation_mode != state_explanation_mode:
            raise ContentError(
                "$.answer_explanation_mode=%s does not match "
                "study_state.json.answer_explanation_mode=%s"
                % (answer_explanation_mode, state_explanation_mode)
            )
    elif (protocol_version == 2 and has_explanation_contract
          and _allow_legacy_isolated):
        # Historical protocol-v2 manifests predate the explicit mode field but
        # already carry the complete isolated contract.  Keep that exact route
        # readable; newly compiled artifacts always bind the state explicitly.
        answer_explanation_mode = "isolated"
        legacy_isolated_mode_inferred = True
    elif protocol_version == 2:
        raise ContentError(
            "authoring_protocol_version=2 requires answer_explanation_mode; "
            "a historical mode-less isolated contract is accepted only by the "
            "explicit read-only canonical validation path"
        )
    else:
        answer_explanation_mode = None
    if protocol_version != 2 and (
            explanation_mode_explicit or has_explanation_contract):
        raise ContentError(
            "answer_explanation_mode/contract require authoring_protocol_version=2"
        )
    if answer_explanation_mode == "isolated" and not has_explanation_contract:
        raise ContentError(
            "isolated answer_explanation_mode requires a complete contract"
        )
    if answer_explanation_mode == "ordinary" and has_explanation_contract:
        raise ContentError(
            "ordinary answer_explanation_mode forbids an isolated contract"
        )
    if pipeline_version == "ingestion-v2" and protocol_version != 2:
        raise ContentError(
            "ingestion-v2 Study Guides require authoring_protocol_version=2 "
            "and detailed per-item answer explanations"
        )

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
            formula_symbols, chapter, inventory["unit_index"], structured,
            require_provenance=protocol_version == 2)
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
            inventory["item_asset_requirements"].get(row.get("item_id"), set()),
            chapter=chapter,
            require_answer_explanation=protocol_version == 2,
            require_answer_explanation_receipt=(
                answer_explanation_mode == "isolated"),
            require_provenance=protocol_version == 2)
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
            if not expected_symbols:
                raise ContentError(
                    "walkthrough %r formula_uses[%d] uses formula %s but that formula "
                    "defines no variables; every used formula needs an explicit variable "
                    "mapping for student-facing substitution"
                    % (item_id, formula_use_index, formula_id)
                )
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
            chapter, inventory["unit_index"], structured)
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

    answer_explanation_verification = None
    if protocol_version == 2:
        if any("self_check" in row or "self_check_provenance" in row
               for row in walkthrough_rows):
            raise ContentError(
                "authoring_protocol_version=2 forbids deprecated self_check fields"
            )
        if answer_explanation_mode == "isolated":
            answer_explanation_verification = _validate_answer_explanation_gate(
                ws, chapter, manifest,
                allow_legacy_isolated=legacy_isolated_mode_inferred,
            )

    crop_receipt_verification = None
    if (_enforce_v2_crop_receipts and structured
            and pipeline_version == "ingestion-v2"):
        crop_receipt_verification = _validate_v2_live_crop_receipts(
            ws, chapter, walkthrough_rows, inventory)

    claim_verification = None
    if (structured and _enforce_v2_claims
            and pipeline_version == "ingestion-v2"):
        claim_verification = _validate_v2_claim_gate(
            ws, chapter, manifest, inventory
        )
    elif structured and pipeline_version == "ingestion-v1":
        # Keep the legacy compatibility route explicit in machine output.  An
        # existing canonical v1 Guide remains readable, but new import,
        # relocalization, and visual publication are forbidden; it must never
        # be mistaken for having passed the v2 claim/receipt gate.
        claim_verification = {
            "required": False,
            "status": "not_applicable",
            "reason": "legacy_ingestion_v1",
            "verification_scope": None,
        }

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
        "ingestion_pipeline_version": pipeline_version,
        "authoring_protocol_version": protocol_version,
        "answer_explanation_mode": answer_explanation_mode,
        "answer_explanation_mode_explicit": explanation_mode_explicit,
        "legacy_isolated_mode_inferred": legacy_isolated_mode_inferred,
        "expected_item_counts": dict(inventory["counts"]),
        "semantic_unit_counts": {
            "expected": len(expected_semantic),
            "knowledge_point_mapped": len(mapped_semantic),
            "excluded": len(excluded_semantic),
            "by_kind": semantic_by_kind,
        },
        "notebook_anchor_count": notebook_anchor_count,
        "asset_policy_sha256": inventory["asset_policy_sha256"],
    }
    if claim_verification is not None:
        report["claim_verification"] = claim_verification
    if answer_explanation_verification is not None:
        report["answer_explanation_verification"] = (
            answer_explanation_verification)
    if crop_receipt_verification is not None:
        report["crop_receipt_verification"] = crop_receipt_verification
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


def _load_and_validate_manifest_current(ws, chapter, path=None,
                                        allow_legacy_isolated=False,
                                        enforce_v2_crop_receipts=False):
    source = os.path.abspath(path) if path else manifest_path(ws, chapter)
    _guard_workspace_child(
        ws, source, "study-guide content manifest", require_file=True)
    manifest = _read_json(source, "study-guide content manifest")
    report = validate_manifest(
        ws, chapter, manifest,
        _allow_legacy_isolated=allow_legacy_isolated,
        _enforce_v2_crop_receipts=enforce_v2_crop_receipts,
    )
    report["input_path"] = source
    return manifest, report


def load_and_validate_manifest(workspace, chapter, path=None,
                               allow_legacy_isolated_canonical=False):
    ws = _guard_workspace(workspace)
    exam_start.require_full_processing(
        ws, purpose="Study Guide content validation")
    if allow_legacy_isolated_canonical and path is not None:
        raise ContentError(
            "historical mode-less isolated compatibility may validate only the "
            "canonical manifest selected by omitting --input"
        )
    manifest, report = _load_and_validate_manifest_current(
        ws, chapter, path,
        allow_legacy_isolated=allow_legacy_isolated_canonical,
        enforce_v2_crop_receipts=True,
    )
    if report.get("legacy_isolated_mode_inferred"):
        if not allow_legacy_isolated_canonical:
            raise ContentError(
                "historical mode-less isolated Guide is read-only and cannot "
                "satisfy a current authoring, completion, rendering, or QA gate"
            )
        canonical = manifest_path(ws, chapter)
        source = report["input_path"]
        if os.path.normcase(os.path.realpath(source)) != os.path.normcase(
                os.path.realpath(canonical)):
            raise ContentError(
                "historical mode-less isolated compatibility may validate only "
                "notebook/chNN.guide.json"
            )
        report["legacy_isolated_compatibility"] = "read_only"
    if report.get("ingestion_pipeline_version") == "ingestion-v1":
        canonical = manifest_path(ws, chapter)
        source = report["input_path"]
        if os.path.normcase(os.path.realpath(source)) != os.path.normcase(
                os.path.realpath(canonical)):
            raise ContentError(
                "ingestion-v1 Study Guide compatibility is read-only and may "
                "validate only the existing canonical notebook/chNN.guide.json; "
                "migrate/re-ingest as ingestion-v2 before authoring a replacement"
            )
        report["legacy_compatibility"] = "read_only"
    return manifest, report


def _inline_text(lines, prefix, value):
    chunks = value.splitlines() or [value]
    lines.append("%s%s" % (prefix, chunks[0]))
    continuation = "  " + (" " * max(0, len(prefix) - 2))
    for chunk in chunks[1:]:
        lines.append("%s%s" % (continuation, chunk))


def _inline_english_quote(lines, prefix, value):
    chunks = value.splitlines() or [value]
    lines.append("> EN: %s%s" % (prefix, chunks[0]))
    continuation = "  " + (" " * max(0, len(prefix) - 2))
    for chunk in chunks[1:]:
        lines.append("> %s%s" % (continuation, chunk))


def _append_bilingual_view(lines, chinese, english):
    """Append a Chinese view line and its immediately following English mirror."""

    zh_rows = chinese if isinstance(chinese, (list, tuple)) else [chinese]
    en_rows = english if isinstance(english, (list, tuple)) else [english]
    lines.extend(zh_rows)
    for index, row in enumerate(en_rows):
        lines.append(("> EN: %s" if index == 0 else "> %s") % row)


def _provenance_emoji(value, code):
    if isinstance(value, dict):
        value = value.get(code)
    if isinstance(value, (list, tuple)):
        markers = []
        for item in value:
            marker = PROVENANCE_EMOJI.get(item, "")
            if marker and (not markers or markers[-1] != marker):
                markers.append(marker)
        return "".join(markers)
    return PROVENANCE_EMOJI.get(value, "")


def _run_terminal_provenance(values, index):
    current = values[index]
    following = values[index + 1] if index + 1 < len(values) else None
    terminal = {}
    for code in ("zh", "en"):
        current_marker = _provenance_emoji(current, code)
        following_marker = _provenance_emoji(following, code)
        if current_marker and current_marker != following_marker:
            terminal[code] = current.get(code) if isinstance(current, dict) else current
    return terminal


_PROVENANCE_UNSET = object()


def _clean_notebook_visible(value, code, sidecar=None):
    try:
        return clean_visible_provenance(value, code, sidecar)
    except ProvenanceConflictError as exc:
        raise ContentError(str(exc)) from exc


def _notebook_visible_provenance(value, sidecar=None):
    """Resolve sidecar/inline compatibility labels before run collapsing."""

    localized = value if isinstance(value, dict) else {"zh": value, "en": value}
    resolved_by_language = {}
    for code in ("zh", "en"):
        if code not in localized:
            continue
        unused_cleaned, resolved = _clean_notebook_visible(
            localized[code], code, sidecar)
        if resolved:
            resolved_by_language[code] = resolved
    return resolved_by_language or sidecar


def _with_provenance_marker(value, provenance, code,
                            source_provenance=_PROVENANCE_UNSET):
    has_source_provenance = (
        source_provenance is not _PROVENANCE_UNSET
        and source_provenance is not None)
    authoritative = (
        provenance if not has_source_provenance
        else source_provenance
    )
    cleaned, inferred_or_explicit = _clean_notebook_visible(
        value, code, authoritative)
    display = (
        inferred_or_explicit
        if not has_source_provenance and provenance is None
        else provenance
    )
    marker = _provenance_emoji(display, code)
    return "%s%s" % (cleaned, (" " + marker) if marker else "")


def _localized_lines(lines, label_zh, label_en, value, language, bullet="-",
                     provenance=None, source_provenance=_PROVENANCE_UNSET):
    if language == "bilingual":
        _inline_text(
            lines, "%s **%s：** " % (bullet, label_zh),
            _with_provenance_marker(
                value["zh"], provenance, "zh", source_provenance),
        )
        _inline_english_quote(
            lines, "**%s:** " % label_en,
            _with_provenance_marker(
                value["en"], provenance, "en", source_provenance),
        )
    elif language == "zh":
        _inline_text(
            lines, "%s **%s：** " % (bullet, label_zh),
            _with_provenance_marker(
                value["zh"], provenance, "zh", source_provenance),
        )
    elif language == "en":
        _inline_text(
            lines, "%s **%s:** " % (bullet, label_en),
            _with_provenance_marker(
                value["en"], provenance, "en", source_provenance),
        )


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


def _format_source(ref, language="en"):
    # The notebook lives under ``workspace/notebook`` while source_file is relative to the
    # separately confirmed materials root.  A relative Markdown link would therefore point at
    # the wrong tree.  The HTML renderer receives the confirmed root and emits safe file URIs;
    # this durable view keeps an honest, copyable location.  ``pages`` is the
    # historical schema key for every page-equivalent anchor, not proof that a
    # PPTX/XLSX/DOCX location is a physical page.
    suffix = os.path.splitext(ref["source_file"])[1].lower()
    labels = {
        ".pdf": ("第 %d 页", "page %d", "无页码", "no page"),
        ".pptx": ("第 %d 张幻灯片", "slide %d", "无幻灯片锚点", "no slide anchor"),
        ".ppt": ("第 %d 张幻灯片", "slide %d", "无幻灯片锚点", "no slide anchor"),
        ".xlsx": ("第 %d 个工作表", "worksheet %d", "无工作表锚点", "no worksheet anchor"),
        ".xls": ("第 %d 个工作表", "worksheet %d", "无工作表锚点", "no worksheet anchor"),
        ".docx": ("第 %d 个逻辑段", "logical segment %d", "无逻辑段锚点", "no logical-segment anchor"),
        ".doc": ("第 %d 个逻辑段", "logical segment %d", "无逻辑段锚点", "no logical-segment anchor"),
    }
    zh_pattern, en_pattern, zh_empty, en_empty = labels.get(
        suffix, ("位置 %d", "location %d", "无位置锚点", "no location anchor"))
    if language == "zh":
        locations = "、".join(zh_pattern % location for location in ref["pages"]) or zh_empty
    else:
        locations = ", ".join(en_pattern % location for location in ref["pages"]) or en_empty
    result = "%s · %s" % (ref["source_file"], locations)
    if ref.get("source_unit_id"):
        result += (" · 内容单元 %s" if language == "zh" else " · unit %s") % ref[
            "source_unit_id"
        ]
    if ref.get("role"):
        result += (" · 角色 %s" if language == "zh" else " · role %s") % ref["role"]
    return result


def _view_heading(lines, language, level, zh, en):
    marker = "#" * level
    if language == "bilingual":
        _append_bilingual_view(lines, "%s %s" % (marker, zh), "**%s**" % en)
    else:
        lines.append("%s %s" % (marker, zh if language == "zh" else en))


def _view_scalar(lines, language, label_zh, label_en, value, bullet="-",
                 code=False, provenance=None,
                 source_provenance=_PROVENANCE_UNSET):
    rendered = "`%s`" % value if code else str(value)
    if language == "bilingual":
        _append_bilingual_view(
            lines,
            "%s **%s：** %s" % (
                bullet, label_zh,
                _with_provenance_marker(
                    rendered, provenance, "zh", source_provenance),
            ),
            "**%s:** %s" % (
                label_en,
                _with_provenance_marker(
                    rendered, provenance, "en", source_provenance),
            ),
        )
    else:
        punctuation = "：" if language == "zh" else ":"
        label = label_zh if language == "zh" else label_en
        lines.append("%s **%s%s** %s" % (
            bullet, label, punctuation,
            _with_provenance_marker(
                rendered, provenance, language, source_provenance),
        ))


def render_notebook_block(manifest):
    """Render a deterministic readable view; all content lines remain inside one notebook entry."""
    language = manifest["language"]
    colon = "：" if language == "zh" else ":"
    lines = []
    _view_heading(lines, language, 3, "知识点", "Knowledge points")
    lines.append("")
    for kp in manifest["knowledge_points"]:
        if language == "bilingual":
            _append_bilingual_view(
                lines,
                "#### `%s` · %s" % (kp["id"], kp["title"]["zh"]),
                "**`%s` · %s**" % (kp["id"], kp["title"]["en"]),
            )
        else:
            lines.append("#### `%s` · %s" % (
                kp["id"], _heading_title(kp["title"], language)))
        lines.append("")
        display_explanation = kp.get("teaching_explanation", kp["explanation"])
        explanation_provenance = kp.get("teaching_explanation_provenance") or (
            kp.get("explanation_provenance") or {
                code: "material" for code in kp["explanation"]
            }
        )
        _localized_lines(
            lines, "解释", "Explanation", display_explanation, language,
            provenance=explanation_provenance,
        )
        if kp["example_ids"]:
            _view_scalar(
                lines, language, "例题", "Examples", ", ".join(kp["example_ids"])
            )
        else:
            note = kp.get("example_note") or {
                "zh": "材料未提供对应例题。",
                "en": "The materials do not provide a corresponding example.",
            }
            _localized_lines(lines, "例题", "Examples", note, language)
        if kp.get("source_unit_ids"):
            _view_scalar(
                lines, language, "覆盖的内容单元", "Covered content units",
                ", ".join(kp["source_unit_ids"]),
            )
        for formula in kp["formulas"]:
            if language == "bilingual":
                _append_bilingual_view(
                    lines, "- **公式 `%s`：**" % formula["id"],
                    "**Formula `%s`:**" % formula["id"],
                )
            else:
                lines.append("- **%s `%s`%s**" % (
                    _view_label(language, "公式", "Formula"), formula["id"],
                    "：" if language == "zh" else ":"))
            lines.extend(["", "$$%s$$" % formula["latex"], ""])
            formula_provenance = [
                _notebook_visible_provenance(
                    formula["explanation"],
                    formula.get("explanation_provenance")),
                _notebook_visible_provenance(
                    formula["applicability"],
                    formula.get("applicability_provenance")),
            ]
            formula_provenance.extend(
                _notebook_visible_provenance(
                    variable["meaning"], variable.get("meaning_provenance"))
                for variable in formula["variables"])
            _localized_lines(
                lines, "公式含义", "Formula meaning", formula["explanation"],
                language,
                provenance=_run_terminal_provenance(formula_provenance, 0),
                source_provenance=formula.get("explanation_provenance"),
            )
            _localized_lines(
                lines, "适用条件", "Applicability", formula["applicability"],
                language,
                provenance=_run_terminal_provenance(formula_provenance, 1),
                source_provenance=formula.get("applicability_provenance"),
            )
            for variable_index, variable in enumerate(formula["variables"]):
                _localized_lines(lines, "变量 %s" % variable["symbol"],
                                 "Variable %s" % variable["symbol"],
                                 variable["meaning"], language,
                                 provenance=_run_terminal_provenance(
                                     formula_provenance, variable_index + 2),
                                 source_provenance=variable.get(
                                     "meaning_provenance"))
        for source in kp["source_refs"]:
            if language == "bilingual":
                _append_bilingual_view(
                    lines, "- **来源：** %s" % _format_source(source, "zh"),
                    "**Source:** %s" % _format_source(source, "en"),
                )
            else:
                _view_scalar(
                    lines, language, "来源", "Source", _format_source(source, language)
                )
        lines.append("")

    _view_heading(lines, language, 3, "例题精讲", "Worked examples")
    lines.append("")
    for walk in manifest["walkthroughs"]:
        if language == "bilingual":
            _append_bilingual_view(
                lines,
                "#### `%s` · %s" % (walk["item_id"], walk["title"]["zh"]),
                "**`%s` · %s**" % (walk["item_id"], walk["title"]["en"]),
            )
        else:
            lines.append("#### `%s` · %s" % (
                walk["item_id"], _heading_title(walk["title"], language)))
        lines.append("")
        _view_scalar(
            lines, language, "知识点", "Knowledge points",
            ", ".join(walk["knowledge_point_ids"]),
        )
        _view_scalar(
            lines, language, "例题来源类型", "Source type", walk["source_type"],
            code=True,
        )
        display_answer = walk.get("teaching_answer", walk["answer"])
        display_answer_provenance = walk.get(
            "teaching_answer_provenance", walk["answer_provenance"])
        provenance = _answer_provenance_by_language(
            display_answer_provenance, language)
        solution_kind = walk.get("solution_kind") or (
            "formula" if walk.get("formula_uses") else "concept")
        _view_scalar(
            lines, language, "解题类型", "Solution kind", solution_kind, code=True
        )
        _view_scalar(
            lines, language, "题面资产模式", "Prompt asset mode",
            walk["prompt_asset_mode"], code=True,
        )
        for asset in walk["prompt_asset_paths"]:
            _view_scalar(
                lines, language, "题面图", "Prompt asset", asset, code=True
            )
        for asset in walk["answer_asset_paths"]:
            _view_scalar(
                lines, language, "答案图", "Answer asset", asset, code=True
            )
        full_prompt_image = walk["prompt_asset_mode"] == "full_prompt"
        if (not full_prompt_image and walk.get("prompt_text")
                and language != "bilingual"):
            _inline_text(lines, "- **%s%s** " % (
                _view_label(language, "原题", "Original prompt"), colon),
                _with_provenance_marker(
                    walk["prompt_text"], "material", language))
        source_languages = {
            "zh": {"zh"}, "en": {"en"}, "mixed": {"zh", "en"}, "unknown": set(),
        }[walk["original_language"]]
        needed_translations = _target_languages(language) - source_languages
        if language == "bilingual" and full_prompt_image:
            # The source image already carries the complete original prompt.
            # Persist only the target-language translation that is absent from
            # that image; repeating OCR/original text (or a placeholder for it)
            # defeats the full-prompt de-duplication contract.
            for key in ("zh", "en"):
                if key not in needed_translations or key not in walk["translation"]:
                    continue
                translated = _with_provenance_marker(
                    walk["translation"][key],
                    (walk.get("translation_provenance") or {}).get(key),
                    key,
                )
                if key == "zh":
                    _inline_text(lines, "- **题面翻译：** ", translated)
                else:
                    _inline_english_quote(
                        lines, "**Prompt translation:** ", translated)
        elif language == "bilingual":
            original_language = walk["original_language"]
            if original_language == "en" and "zh" in walk["translation"]:
                _append_bilingual_view(
                    lines,
                    "- **题面翻译：** %s" % _with_provenance_marker(
                        walk["translation"]["zh"],
                        (walk.get("translation_provenance") or {}).get("zh"),
                        "zh",
                    ),
                    "**Original prompt:** %s" % (
                        _with_provenance_marker(
                            walk.get("prompt_text")
                            or "The original material prompt appears in the prompt asset above.",
                            "material", "en",
                        )
                    ),
                )
            elif original_language == "zh" and "en" in walk["translation"]:
                _append_bilingual_view(
                    lines,
                    "- **原题：** %s" % _with_provenance_marker(
                        walk.get("prompt_text") or "资料原题见上方题面图。",
                        "material", "zh",
                    ),
                    "**Prompt translation:** %s" % _with_provenance_marker(
                        walk["translation"]["en"],
                        (walk.get("translation_provenance") or {}).get("en"),
                        "en",
                    ),
                )
            elif walk.get("prompt_text"):
                _append_bilingual_view(
                    lines, "- **原题（资料原文）：** %s" % (
                        _with_provenance_marker(
                            walk["prompt_text"], "material", "zh")
                    ),
                    "**Original prompt (material text):** %s" % (
                        _with_provenance_marker(
                            walk["prompt_text"], "material", "en")
                    ),
                )
        else:
            for key in ("zh", "en"):
                if key not in needed_translations:
                    continue
                if key in walk["translation"]:
                    name = ("中文" if language != "en" else "Chinese") if key == "zh" else (
                        "英文" if language == "zh" else "English")
                    _inline_text(lines, "- **%s (%s)%s** " % (
                        _view_label(language, "题面翻译", "Prompt translation"), name, colon),
                        _with_provenance_marker(
                            walk["translation"][key],
                            (walk.get("translation_provenance") or {}).get(key),
                            language,
                        ))
        question_provenance = [
            _notebook_visible_provenance(
                walk["what_asked"], walk.get("what_asked_provenance"))]
        question_provenance.extend(
            _notebook_visible_provenance(
                (walk.get("knowledge_point_uses") or {}).get(kp_id),
                (walk.get("knowledge_point_uses_provenance") or {}).get(kp_id))
            for kp_id in walk["knowledge_point_ids"])
        _localized_lines(
            lines, "题目问什么", "What is asked", walk["what_asked"], language,
            provenance=_run_terminal_provenance(question_provenance, 0),
            source_provenance=walk.get("what_asked_provenance"),
        )
        for kp_index, kp_id in enumerate(walk["knowledge_point_ids"]):
            usage = (walk.get("knowledge_point_uses") or {}).get(kp_id)
            if usage:
                _localized_lines(lines, "知识点 %s 如何用" % kp_id,
                                 "How knowledge point %s is used" % kp_id,
                                 usage, language,
                                 provenance=_run_terminal_provenance(
                                     question_provenance, kp_index + 1),
                                 source_provenance=(
                                     walk.get("knowledge_point_uses_provenance")
                                     or {}).get(kp_id))
        for field, zh_label, en_label in (
            ("known_quantities", "已知量", "Known quantity"),
            ("unknown_quantities", "未知量", "Unknown quantity"),
        ):
            quantity_provenance = [
                _notebook_visible_provenance(
                    quantity["label"], quantity.get("provenance"))
                for quantity in walk[field]
            ]
            for quantity_index, quantity in enumerate(walk[field]):
                suffix = ""
                for key in ("symbol", "value", "unit"):
                    if quantity.get(key):
                        suffix += " · %s=%s" % (key, quantity[key])
                localized = dict(quantity["label"])
                if suffix:
                    localized = {key: text + suffix for key, text in localized.items()}
                _localized_lines(
                    lines, zh_label, en_label, localized, language,
                    provenance=_run_terminal_provenance(
                        quantity_provenance, quantity_index),
                    source_provenance=quantity.get("provenance"),
                )
        for formula_use in walk["formula_uses"]:
            _view_scalar(
                lines, language, "使用公式", "Formula used",
                formula_use["formula_id"], code=True,
            )
            use_provenance = [
                _notebook_visible_provenance(
                    formula_use["why_applicable"],
                    formula_use.get("why_applicable_provenance"))]
            use_provenance.extend(
                _notebook_visible_provenance(
                    mapping["maps_to"], mapping.get("maps_to_provenance"))
                for mapping in formula_use["variable_mapping"])
            use_provenance.append(formula_use.get("substitution_provenance"))
            substitution_code = language if language in ("zh", "en") else "zh"
            substitution, unused_substitution_provenance = _clean_notebook_visible(
                formula_use["substitution"], substitution_code,
                formula_use.get("substitution_provenance"),
            )
            if (not use_provenance[-1]
                    and unused_substitution_provenance):
                use_provenance[-1] = unused_substitution_provenance
            _localized_lines(
                lines, "为什么适用", "Why it applies",
                formula_use["why_applicable"], language,
                provenance=_run_terminal_provenance(use_provenance, 0),
                source_provenance=formula_use.get(
                    "why_applicable_provenance"),
            )
            for mapping_index, mapping in enumerate(formula_use["variable_mapping"]):
                _localized_lines(lines, "变量映射 %s" % mapping["symbol"],
                                 "Variable mapping %s" % mapping["symbol"],
                                 mapping["maps_to"], language,
                                 provenance=_run_terminal_provenance(
                                     use_provenance, mapping_index + 1),
                                 source_provenance=mapping.get(
                                     "maps_to_provenance"))
            substitution_display_provenance = _run_terminal_provenance(
                use_provenance, len(use_provenance) - 1)
            if (not substitution_display_provenance
                    and unused_substitution_provenance):
                substitution_display_provenance = unused_substitution_provenance
            _view_scalar(
                lines, language, "代入", "Substitution",
                "$%s$" % substitution,
                provenance=substitution_display_provenance,
                source_provenance=formula_use.get(
                    "substitution_provenance"),
            )
        if not walk["formula_uses"] and walk.get("no_formula_reason"):
            _localized_lines(
                lines, "为什么不用公式", "Why no formula is needed",
                walk["no_formula_reason"], language,
                provenance=walk.get("no_formula_reason_provenance"),
            )
        raw_step_provenance = walk.get("steps_provenance") or [
            None for unused_step in walk["steps"]]
        step_provenance = [
            _notebook_visible_provenance(step, raw_step_provenance[index])
            for index, step in enumerate(walk["steps"])
        ]
        for index, step in enumerate(walk["steps"]):
            _localized_lines(
                lines, "步骤 %d" % (index + 1), "Step %d" % (index + 1),
                step, language,
                provenance=_run_terminal_provenance(step_provenance, index),
                source_provenance=step_provenance[index],
            )
        _localized_lines(
            lines, "答案", "Answer", display_answer, language,
            provenance=provenance,
        )
        if walk.get("answer_explanation"):
            _localized_lines(
                lines, "为什么这个答案成立", "Why this answer works",
                walk["answer_explanation"], language,
                provenance=walk.get("answer_explanation_provenance"),
            )
        for source in walk["source_trace"]:
            if language == "bilingual":
                _append_bilingual_view(
                    lines,
                    "- **来源追踪：** %s" % _format_source(source, "zh"),
                    "**Source trace:** %s" % _format_source(source, "en"),
                )
            else:
                _view_scalar(
                    lines, language, "来源追踪", "Source trace",
                    _format_source(source, language),
                )
        lines.append("")

    semantic_exclusions = manifest.get("semantic_exclusions") or []
    if semantic_exclusions:
        _view_heading(
            lines, language, 3, "非教学语义单元排除", "Excluded semantic units"
        )
        lines.append("")
        for exclusion in semantic_exclusions:
            if language == "bilingual":
                _append_bilingual_view(
                    lines,
                    "- **内容单元 `%s`**" % exclusion["source_unit_id"],
                    "**Source unit `%s`**" % exclusion["source_unit_id"],
                )
            else:
                lines.append("- **`%s`**" % exclusion["source_unit_id"])
            _localized_lines(lines, "排除原因", "Reason excluded",
                             exclusion["reason"], language)

    if manifest["profile"] == "abridged":
        _view_heading(lines, language, 3, "省略清单", "Omission ledger")
        lines.append("")
        for omission in manifest["omissions"]:
            identifier = "`%s` · %s" % (
                omission["item_id"], ", ".join(omission["knowledge_point_ids"])
            )
            if language == "bilingual":
                _append_bilingual_view(
                    lines, "- **%s**" % identifier, "**%s**" % identifier
                )
            else:
                lines.append("- **%s**" % identifier)
            _localized_lines(lines, "省略原因", "Reason omitted", omission["reason"], language)
            for source in omission["source_refs"]:
                if language == "bilingual":
                    _append_bilingual_view(
                        lines, "- **来源：** %s" % _format_source(source, "zh"),
                        "**Source:** %s" % _format_source(source, "en"),
                    )
                else:
                    _view_scalar(
                        lines, language, "来源", "Source",
                        _format_source(source, language),
                    )
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


def _merge_notebook(existing, chapter, rendered, language):
    begin, end, header = _markers(chapter)
    lines = (existing or "").splitlines()
    begin_at = [index for index, line in enumerate(lines) if line == begin]
    end_at = [index for index, line in enumerate(lines) if line == end]
    reserved_headers = [index for index, line in enumerate(lines)
                        if re.match(r"^##\s+\[#study-guide-content-ch%02d\](?:\s|$)" % chapter,
                                    line)]
    if not begin_at and not end_at:
        if reserved_headers or any(MARKER_PREFIX in line for line in lines):
            raise ContentError("notebook contains a reserved or malformed study-guide marker")
        if not notebook_has_provenance_legend("\n".join(lines)):
            rendered = "\n".join(notebook_legend_lines(language)) + "\n\n" + rendered
        block = [begin] + rendered.rstrip("\n").splitlines() + [end]
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
    outside = lines[:begin_at[0]] + lines[end_at[0] + 1:]
    if not notebook_has_provenance_legend("\n".join(outside)):
        rendered = "\n".join(notebook_legend_lines(language)) + "\n\n" + rendered
    block = [begin] + rendered.rstrip("\n").splitlines() + [end]
    lines[begin_at[0]:end_at[0] + 1] = block
    return "\n".join(lines).rstrip() + "\n"


def _guard_notebook_targets(ws, chapter):
    directory = os.path.join(ws, "notebook")
    if (not os.path.lexists(directory) or _is_link_or_reparse(directory)
            or not os.path.isdir(directory)):
        raise ContentError("notebook must be a real directory inside the workspace")
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


def _stage_bytes(path, data):
    """Durably stage bytes next to their target without publishing them."""

    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return temporary


def _stage_text(path, text):
    """Durably stage UTF-8 text next to its target without publishing it."""

    return _stage_bytes(path, text.encode("utf-8"))


def _snapshot_public_file(path, label):
    """Capture byte/existence state after the target has passed path preflight."""

    if not os.path.lexists(path):
        return {"path": path, "exists": False, "bytes": None, "label": label}
    if _is_link_or_reparse(path) or not os.path.isfile(path):
        raise ContentError("%s is not a safe regular file: %s" % (label, path))
    try:
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError as exc:
        raise ContentError("cannot snapshot %s: %s" % (label, exc))
    return {"path": path, "exists": True, "bytes": data, "label": label}


def _snapshot_is_current(snapshot):
    path = snapshot["path"]
    if not snapshot["exists"]:
        return not os.path.lexists(path)
    if (_is_link_or_reparse(path) or not os.path.isfile(path)):
        return False
    try:
        with open(path, "rb") as stream:
            return stream.read() == snapshot["bytes"]
    except OSError:
        return False


def _restore_snapshot(snapshot):
    """Restore one public file to its captured bytes/existence using atomic replacement."""

    path = snapshot["path"]
    if not snapshot["exists"]:
        if os.path.lexists(path):
            if _is_link_or_reparse(path) or not os.path.isfile(path):
                raise OSError("rollback target became unsafe: %s" % path)
            os.remove(path)
        return
    temporary = _stage_bytes(path, snapshot["bytes"])
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore_snapshots(snapshots):
    failures = []
    for snapshot in reversed(snapshots):
        try:
            _restore_snapshot(snapshot)
        except BaseException as exc:
            failures.append("%s: %s" % (snapshot["label"], exc))
    if failures:
        raise OSError("; ".join(failures))


def _live_asset_policy_sha256(ws, chapter):
    structured, units, _unit_index = _read_content_units(ws, chapter)
    teaching = _workspace_array(
        ws, "references/teaching_examples.json", chapter, optional=not structured)
    quizzes = _workspace_array(ws, "references/quiz_bank.json", chapter)
    return _asset_policy_snapshot_from_rows(
        teaching, quizzes, units, workspace=ws
    )[1]


def _chapter_artifact_targets(ws, chapter):
    """Preflight and return every derived file invalidated by a successful import."""

    guide_dir = os.path.join(ws, "study_guide")
    if not os.path.lexists(guide_dir):
        return []
    if _is_link_or_reparse(guide_dir) or not os.path.isdir(guide_dir):
        raise ContentError("study_guide must be a real directory before guide import")
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
            raise ContentError("unsafe derived chapter artifact blocks guide import: %s" % name)
        targets.append(path)
    qa_dir = os.path.join(guide_dir, "qa")
    if os.path.lexists(qa_dir):
        if _is_link_or_reparse(qa_dir) or not os.path.isdir(qa_dir):
            raise ContentError("study_guide/qa must be a real directory before guide import")
        pattern = re.compile(r"^ch%02d_p\d+\.png$" % chapter)
        for name in sorted(os.listdir(qa_dir)):
            if not pattern.fullmatch(name):
                continue
            path = os.path.join(qa_dir, name)
            if _is_link_or_reparse(path) or not os.path.isfile(path):
                raise ContentError("unsafe QA page blocks guide import: %s" % name)
            targets.append(path)
    return targets


def _publish_manifest(ws, chapter, manifest, report):
    """Publish notebook+manifest as one rollback-protected logical transaction."""

    # Complete every deterministic/content/path preflight before creating even a private stage.
    md_path, json_path = _guard_notebook_targets(ws, chapter)
    existing = _read_optional_text(md_path)
    rendered = render_notebook_block(manifest)
    updated_notebook = _merge_notebook(
        existing, chapter, rendered, manifest["language"])
    canonical_json = json.dumps(
        manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    expected_facts = (
        (report.get("claim_verification") or {}).get("fact_integrity")
    )
    expected_asset_policy = report.get("asset_policy_sha256")
    if expected_asset_policy is None:
        expected_asset_policy = _live_asset_policy_sha256(ws, chapter)
    derived_targets = _chapter_artifact_targets(ws, chapter)
    authoritative = [
        _snapshot_public_file(md_path, "notebook chapter"),
        _snapshot_public_file(json_path, "canonical guide manifest"),
    ]
    derived = [
        _snapshot_public_file(path, "derived chapter artifact")
        for path in derived_targets
    ]

    stages = []
    try:
        try:
            stages.append(_stage_text(md_path, updated_notebook))
            stages.append(_stage_text(json_path, canonical_json))
        except OSError as exc:
            raise ContentError(
                "cannot stage study-guide content for atomic publication: %s" % exc
            ) from exc

        # This is the last gate before the first public replacement.  It binds the validated
        # ingestion facts, every global asset-policy layer, and the exact public state that will
        # either be committed or restored.
        if expected_facts is not None:
            try:
                current_facts = validate_workspace_fact_integrity(ws)["snapshot"]
            except (OSError, TypeError, ValueError) as exc:
                raise ContentError(
                    "ingestion fact integrity changed before Study Guide import publication: %s"
                    % exc
                ) from exc
            if current_facts != expected_facts:
                raise ContentError(
                    "ingestion fact inputs changed before Study Guide import publication"
                )
        try:
            current_asset_policy = _live_asset_policy_sha256(ws, chapter)
        except (OSError, TypeError, ValueError, ContentError) as exc:
            raise ContentError(
                "asset policy changed before Study Guide import publication: %s" % exc
            ) from exc
        if current_asset_policy != expected_asset_policy:
            raise ContentError(
                "asset policy inputs changed before Study Guide import publication"
            )
        current_derived = _chapter_artifact_targets(ws, chapter)
        if current_derived != derived_targets or not all(
                _snapshot_is_current(snapshot) for snapshot in authoritative + derived):
            raise ContentError(
                "Study Guide public targets changed before import publication"
            )

        try:
            os.replace(stages[0], md_path)
            stages[0] = None
            os.replace(stages[1], json_path)
            stages[1] = None
        except BaseException as exc:
            try:
                _restore_snapshots(authoritative)
            except BaseException as rollback_exc:
                raise ContentError(
                    "cannot atomically publish study-guide content (%s); rollback failed: %s"
                    % (exc, rollback_exc)
                ) from exc
            raise ContentError(
                "cannot atomically publish study-guide content: %s" % exc
            ) from exc

        invalidated = []
        try:
            for snapshot in derived:
                os.remove(snapshot["path"])
                invalidated.append(
                    os.path.relpath(snapshot["path"], ws).replace("\\", "/")
                )
        except BaseException as exc:
            rollback_failures = []
            try:
                _restore_snapshots(derived)
            except BaseException as rollback_exc:
                rollback_failures.append("derived artifacts: %s" % rollback_exc)
            try:
                _restore_snapshots(authoritative)
            except BaseException as rollback_exc:
                rollback_failures.append("authoritative pair: %s" % rollback_exc)
            if rollback_failures:
                raise ContentError(
                    "cannot invalidate stale localized artifact %s; rollback failed: %s"
                    % (exc, "; ".join(rollback_failures))
                ) from exc
            raise ContentError(
                "cannot invalidate stale localized artifact: %s" % exc
            ) from exc
    finally:
        for temporary in stages:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    report.update({
        "imported": True,
        "notebook_path": md_path,
        "manifest_path": json_path,
        "invalidated_artifacts": invalidated,
    })
    return report


def import_manifest(workspace, chapter, input_path):
    """Validate and transactionally publish canonical notebook/JSON content."""
    ws = _guard_workspace(workspace)
    exam_start.require_full_processing(
        ws, purpose="Study Guide content import")
    with _study_guide_mutation_lock(ws):
        exam_start.require_full_processing(
            ws, purpose="Study Guide content import publication")
        manifest, report = _load_and_validate_manifest_current(
            ws, chapter, input_path, enforce_v2_crop_receipts=True)
        pipeline_version = report.get("ingestion_pipeline_version")
        if pipeline_version == "ingestion-v1":
            raise ContentError(
                "ingestion-v1 Study Guide compatibility is read-only; new imports "
                "must migrate/re-ingest as ingestion-v2 so target-item crops and "
                "mode-bound detailed per-item answer explanations are mandatory"
            )
        if pipeline_version != "ingestion-v2":
            raise ContentError(
                "non-structured Study Guide compatibility is read-only; new imports "
                "require an explicit full ingestion-v2 workspace so source revisions, "
                "target-item crops, claims, and answer-explanation receipts can be "
                "verified"
            )
        report["notebook_anchor_count"] = _validate_walkthrough_notebook_anchors(
            ws, chapter, manifest["walkthroughs"], require_all=True)
        return _publish_manifest(ws, chapter, manifest, report)


def relocalize_manifest(workspace, chapter, language, output_path=None):
    """Reject stale-language mutation; rebuild through the v2 authoring chain."""

    ws = _guard_workspace(workspace)
    exam_start.require_full_processing(
        ws, purpose="Study Guide content relocalization")
    if language not in LANGUAGES:
        raise ContentError("language must be zh/en/bilingual")
    with _study_guide_mutation_lock(ws):
        exam_start.require_full_processing(
            ws, purpose="Study Guide content relocalization publication")
        path = manifest_path(ws, chapter)
        original = _read_json(path, "study-guide content manifest")
        if not isinstance(original, dict) or original.get("language") not in LANGUAGES:
            raise ContentError("existing study-guide manifest has no valid canonical language")
        pipeline_version = (
            _ingestion_pipeline_version(ws)
            if os.path.lexists(os.path.join(ws, ".ingest")) else "ingestion-v1"
        )
        if pipeline_version == "ingestion-v2":
            raise ContentError(
                "ingestion-v2 answer explanations, crops, notebook blocks, claims, and "
                "receipts are language-bound; rerun study_guide_author.py prepare, "
                "complete the new-language annotations, and—only when the selected "
                "mode is isolated—run study_guide_explain.py for every item; then "
                "persist/compile/verify/import instead of "
                "relocalizing an old manifest"
            )
        raise ContentError(
            "ingestion-v1 Study Guide compatibility is read-only; the existing "
            "canonical manifest may be inspected but not relocalized or republished. "
            "Migrate/re-ingest as ingestion-v2 and rerun the complete authoring, "
            "crop, selected explanation-mode, claim, and import workflow"
        )


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
        help="deprecated; v2 language changes rerun the complete authoring workflow",
    )
    relocalize.add_argument("--json", action="store_true")
    return parser


def run(argv=None):
    args = _parser().parse_args(argv)
    try:
        workspace = _guard_workspace(args.workspace)
        exam_start.require_full_processing(
            workspace, purpose="Study Guide content command")
        if args.command == "validate":
            _manifest, report = load_and_validate_manifest(
                workspace, args.chapter, args.input,
                allow_legacy_isolated_canonical=args.input is None,
            )
            report["command"] = "validate"
        elif args.command == "import":
            report = import_manifest(workspace, args.chapter, args.input)
            report["command"] = "import"
        else:
            report = relocalize_manifest(
                workspace, args.chapter, args.language, args.output
            )
            report["command"] = "relocalize"
    except exam_start.FullProcessingRequired as exc:
        if getattr(args, "json", False):
            print(json.dumps(
                {"ok": False, "error": str(exc), "exit_code": 2},
                ensure_ascii=False,
            ))
        else:
            sys.stderr.write("study_guide_content: %s\n" % exc)
        return 2
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
