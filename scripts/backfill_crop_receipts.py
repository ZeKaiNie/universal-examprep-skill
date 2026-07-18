#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Incrementally upgrade/create audited item crops with current strict receipts.

This command deliberately does not invoke the material builder or parse course
PDFs.  It verifies current ingestion-v2 facts, exact source revisions, a
declared target asset, an exact parent-bbox candidate crop, and one explicit
semantic-purity review per item side.  ``apply`` publishes new immutable crop
assets plus a new builder/compiler material generation, then invokes only the
normal deterministic compiler.

Examples::

    python scripts/backfill_crop_receipts.py validate \
      --workspace <workspace> --annotations crop-backfill.jsonl --json
    python scripts/backfill_crop_receipts.py apply \
      --workspace <workspace> --annotations crop-backfill.jsonl --json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import strict_json
import exam_start
from asset_crops import (
    COMPOSITE_CROP_LAYOUT,
    COMPOSITE_CROP_VARIANT,
    NESTED_COMPOSITE_CROP_VARIANT,
    CROP_CONTENT_SCOPES,
    CROP_SIDES,
    CropContractError,
    CropReceipt,
    SemanticPurityReview,
    canonical_sha256,
    compact_asset_from_receipt,
    crop_receipt_index,
    make_crop_spec_sha256,
    normalize_composite_crop_spec,
    validate_crop_asset_binding,
)
from asset_policy import (
    audit_asset_policy,
    physical_asset_key,
    workspace_asset_identity_key,
    workspace_asset_is_student_attempt,
)
from image_validation import ImageValidationError, png_dimensions
from stable_ids import stable_item_id_problem
from ingestion import (
    ConflictError,
    IngestionStore,
    SourceManifest,
    atomic_write_json,
    is_link_or_reparse,
    make_source_id,
    normalize_workspace_path,
    read_json,
    safe_workspace_entry,
    stable_read_bytes,
    workspace_publication_lock,
    workspace_validation_lock,
)
from ingestion.pipeline import verify_material_build_receipt
from material_generation import (
    MATERIAL_BUILD_PENDING_PATH,
    PARSE_REPORT_PATH,
    SOURCE_RAW_INPUT_PATH,
    build_pending_generation,
)


ANNOTATION_LEGACY_SCHEMA_VERSION = 1
ANNOTATION_SCHEMA_VERSION = 2
ANNOTATION_RECORD_TYPE = "crop_receipt_backfill"
MAX_ANNOTATION_BYTES = 16 * 1024 * 1024
MAX_ANNOTATIONS = 4096
MAX_CONTROL_BYTES = 128 * 1024 * 1024
MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_PNG_PIXELS = 100_000_000
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CHAPTER_RE = re.compile(r"^ch0*([1-9]\d*)$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

ANNOTATION_COMMON_FIELDS = frozenset((
    "schema_version",
    "record_type",
    "operation",
    "item_id",
    "chapter_id",
    "side",
    "role",
    "content_scope",
    "source_id",
    "source_path",
    "source_sha256",
    "source_page",
    "page_box_pdf_points",
    "parent_asset_path",
    "parent_asset_sha256",
    "parent_width",
    "parent_height",
    "target_asset_path",
    "target_asset_sha256",
    "crop_asset_path",
    "crop_asset_sha256",
    "crop_width",
    "crop_height",
    "semantic_purity",
))
LEGACY_SINGLE_ANNOTATION_FIELDS = ANNOTATION_COMMON_FIELDS | frozenset((
    "bbox_pdf_points",
    "bbox_parent_pixels",
))
LEGACY_COMPOSITE_ANNOTATION_FIELDS = ANNOTATION_COMMON_FIELDS | frozenset((
    "regions",
    "stack",
))
CURRENT_PARENT_FIELDS = ANNOTATION_COMMON_FIELDS | frozenset((
    "parent_source_bbox_pdf_points",
))
CURRENT_PROMOTION_ANNOTATION_FIELDS = CURRENT_PARENT_FIELDS | frozenset((
    "bbox_pdf_points",
))
CURRENT_SINGLE_ANNOTATION_FIELDS = CURRENT_PARENT_FIELDS | frozenset((
    "bbox_pdf_points",
    "bbox_parent_pixels",
))
CURRENT_COMPOSITE_ANNOTATION_FIELDS = CURRENT_PARENT_FIELDS | frozenset((
    "regions",
    "stack",
))
ANNOTATION_FIELDS_BY_SCHEMA_OPERATION = {
    (ANNOTATION_LEGACY_SCHEMA_VERSION, "upgrade_existing"):
        LEGACY_SINGLE_ANNOTATION_FIELDS,
    (ANNOTATION_LEGACY_SCHEMA_VERSION, "create_from_parent"):
        LEGACY_SINGLE_ANNOTATION_FIELDS,
    (ANNOTATION_LEGACY_SCHEMA_VERSION, "create_composite_from_parent"):
        LEGACY_COMPOSITE_ANNOTATION_FIELDS,
    (ANNOTATION_SCHEMA_VERSION, "promote_legacy_crop"):
        CURRENT_PROMOTION_ANNOTATION_FIELDS,
    (ANNOTATION_SCHEMA_VERSION, "create_from_legacy_crop"):
        CURRENT_SINGLE_ANNOTATION_FIELDS,
    (ANNOTATION_SCHEMA_VERSION, "create_composite_from_legacy_crop"):
        CURRENT_COMPOSITE_ANNOTATION_FIELDS,
}

PROMPT_ROLES = frozenset(("question_context",))
ANSWER_ROLES = frozenset(("answer_context", "worked_solution"))
REVIEW_KINDS = frozenset((
    "item_asset_crop_not_materialized",
    "item_asset_crop_semantic_review_required",
))
BACKFILL_OPERATIONS = frozenset(
    operation for _schema, operation in ANNOTATION_FIELDS_BY_SCHEMA_OPERATION
)
COMPOSITE_STACK_FIELDS = frozenset((
    "schema_version", "layout", "region_order", "gap_pixels",
    "background_rgba", "horizontal_alignment",
))


class BackfillError(ValueError):
    """The proposed migration cannot be proven safe against current facts."""


def _fail(message):
    raise BackfillError(message)


def _json_bytes(value):
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BackfillError("control document is not strict JSON: %s" % exc) from exc


def _stable_json_file(path, label, max_bytes=MAX_CONTROL_BYTES):
    payload, snapshot = stable_read_bytes(path)
    if len(payload) > max_bytes:
        _fail("%s exceeds the %d-byte safety limit" % (label, max_bytes))
    try:
        value = strict_json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise BackfillError("%s is not strict UTF-8 JSON: %s" % (label, exc)) from exc
    return value, payload, snapshot


def _canonical_path(value, label):
    try:
        normalized = normalize_workspace_path(value)
    except (TypeError, ValueError) as exc:
        raise BackfillError("%s is unsafe: %s" % (label, exc)) from exc
    if normalized != value:
        _fail("%s must use canonical POSIX separators" % label)
    return normalized


def _sha(value, label):
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        _fail("%s must be a lowercase SHA-256 digest" % label)
    return value


def _positive_int(value, label):
    if type(value) is not int or value < 1:
        _fail("%s must be an integer >= 1" % label)
    return value


def _rect(value, label, integer=False):
    if not isinstance(value, list) or len(value) != 4:
        _fail("%s must contain four coordinates" % label)
    result = []
    for index, raw in enumerate(value):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail("%s[%d] must be numeric" % (label, index))
        if integer and type(raw) is not int:
            _fail("%s[%d] must be an integer pixel coordinate" % (label, index))
        number = int(raw) if integer else float(raw)
        if not integer and not (-float("inf") < number < float("inf")):
            _fail("%s[%d] must be finite" % (label, index))
        result.append(number)
    if result[2] <= result[0] or result[3] <= result[1]:
        _fail("%s must have positive width and height" % label)
    return result


def _rect_within(inner, outer, label):
    if not (
            inner[0] >= outer[0]
            and inner[1] >= outer[1]
            and inner[2] <= outer[2]
            and inner[3] <= outer[3]):
        _fail("%s must stay within page_box_pdf_points" % label)


def _map_parent_pixels_to_pdf(bbox, width, height, parent_source_bbox):
    pdf_width = parent_source_bbox[2] - parent_source_bbox[0]
    pdf_height = parent_source_bbox[3] - parent_source_bbox[1]
    return [
        parent_source_bbox[0] + (bbox[0] / float(width)) * pdf_width,
        parent_source_bbox[1] + (bbox[1] / float(height)) * pdf_height,
        parent_source_bbox[0] + (bbox[2] / float(width)) * pdf_width,
        parent_source_bbox[1] + (bbox[3] / float(height)) * pdf_height,
    ]


def _composite_contract(annotation, page_box, item_id, context_ids, label):
    stack = annotation.get("stack")
    if not isinstance(stack, dict) or set(stack) != COMPOSITE_STACK_FIELDS:
        actual = set(stack) if isinstance(stack, dict) else set()
        _fail(
            "%s.stack schema mismatch; missing=%r unknown=%r"
            % (
                label,
                sorted(COMPOSITE_STACK_FIELDS - actual),
                sorted(actual - COMPOSITE_STACK_FIELDS),
            )
        )
    composition = {
        "schema_version": stack["schema_version"],
        "layout": stack["layout"],
        "region_order": stack["region_order"],
        "gap_pixels": stack["gap_pixels"],
        "background_rgba": stack["background_rgba"],
        "horizontal_alignment": stack["horizontal_alignment"],
        "parent_asset_path": annotation["parent_asset_path"],
        "parent_asset_sha256": annotation["parent_asset_sha256"],
        "parent_width": annotation["parent_width"],
        "parent_height": annotation["parent_height"],
        "target_asset_path": annotation["target_asset_path"],
        "target_asset_sha256": annotation["target_asset_sha256"],
        "reviewed_candidate_path": annotation["crop_asset_path"],
        "reviewed_candidate_sha256": annotation["crop_asset_sha256"],
        "output_width": annotation["crop_width"],
        "output_height": annotation["crop_height"],
        "regions": annotation["regions"],
    }
    if type(stack["schema_version"]) is not int:
        _fail("%s.stack.schema_version must be an integer" % label)
    if annotation["schema_version"] >= ANNOTATION_SCHEMA_VERSION:
        if stack["schema_version"] != 2:
            _fail("%s.stack.schema_version must be 2 for a nested parent" % label)
        composition["parent_source_bbox_pdf_points"] = annotation[
            "parent_source_bbox_pdf_points"
        ]
    elif stack["schema_version"] != 1:
        _fail("%s.stack.schema_version must be 1 for a legacy page parent" % label)
    try:
        return normalize_composite_crop_spec(
            composition, page_box, item_id, context_ids
        )
    except CropContractError as exc:
        raise BackfillError("%s composite contract: %s" % (label, exc)) from exc


def _chapter_id(value, label):
    if isinstance(value, bool) or value is None:
        _fail("%s must name one chapter" % label)
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip().lower()
        match = _CHAPTER_RE.fullmatch(text)
        if match:
            number = int(match.group(1))
        elif text.isdigit():
            number = int(text)
        else:
            _fail("%s must be chNN or a positive chapter number" % label)
    if number < 1:
        _fail("%s must name a positive chapter" % label)
    return "ch%02d" % number


def _optional_chapter_id(value, label):
    if value in (None, ""):
        return None
    try:
        return _chapter_id(value, label)
    except BackfillError:
        # This helper inventories every declaration, including unrelated
        # legacy rows.  An invalid/missing locator becomes non-matching here;
        # the selected crop still has to equal the annotation's exact chapter.
        return None


def _side_for_role(role):
    if role in PROMPT_ROLES:
        return "prompt"
    if role in ANSWER_ROLES:
        return "answer"
    if role == "student_attempt":
        return "student_attempt"
    return None


def _read_annotations(path):
    absolute = os.path.abspath(path)
    if not os.path.isfile(absolute) or is_link_or_reparse(absolute):
        _fail("annotations must be a regular non-link JSONL file")
    payload, snapshot = stable_read_bytes(absolute)
    if len(payload) > MAX_ANNOTATION_BYTES:
        _fail("annotations exceed the %d-byte safety limit" % MAX_ANNOTATION_BYTES)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackfillError("annotations must be UTF-8 JSONL") from exc
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = strict_json.loads(line)
        except (TypeError, ValueError) as exc:
            raise BackfillError(
                "annotations line %d is not strict JSON: %s" % (line_number, exc)
            ) from exc
        operation = row.get("operation") if isinstance(row, dict) else None
        schema_version = row.get("schema_version") if isinstance(row, dict) else None
        expected_fields = ANNOTATION_FIELDS_BY_SCHEMA_OPERATION.get(
            (schema_version, operation)
        )
        if expected_fields is None:
            _fail(
                "annotations line %d schema_version/operation is unsupported; "
                "accepted pairs are %s"
                % (
                    line_number,
                    ", ".join(
                        "%d/%s" % pair
                        for pair in sorted(ANNOTATION_FIELDS_BY_SCHEMA_OPERATION)
                    ),
                )
            )
        if set(row) != expected_fields:
            actual = set(row)
            _fail(
                "annotations line %d schema mismatch; missing=%r unknown=%r"
                % (
                    line_number,
                    sorted(expected_fields - actual),
                    sorted(actual - expected_fields),
                )
            )
        if row.get("record_type") != ANNOTATION_RECORD_TYPE:
            _fail("annotations line %d record_type is invalid" % line_number)
        row = copy.deepcopy(row)
        row["_line_number"] = line_number
        rows.append(row)
        if len(rows) > MAX_ANNOTATIONS:
            _fail("annotations contain more than %d records" % MAX_ANNOTATIONS)
    if not rows:
        _fail("annotations contain no records")
    confirmation, confirmation_snapshot = stable_read_bytes(absolute)
    if confirmation != payload or confirmation_snapshot["sha256"] != snapshot["sha256"]:
        _fail("annotations changed while they were read")
    return rows, snapshot["sha256"]


def _safe_png(workspace, relative, expected_sha, expected_width, expected_height,
              label):
    relative = _canonical_path(relative, label + ".path")
    if not relative.lower().endswith(".png"):
        _fail("%s must be a PNG" % label)
    try:
        absolute = safe_workspace_entry(workspace, relative)
    except (OSError, ValueError) as exc:
        raise BackfillError("%s path is unsafe: %s" % (label, exc)) from exc
    try:
        before = os.lstat(str(absolute))
    except OSError as exc:
        raise BackfillError("%s cannot be inspected: %s" % (label, exc)) from exc
    if (is_link_or_reparse(absolute) or not stat.S_ISREG(before.st_mode)
            or int(getattr(before, "st_nlink", 1)) != 1):
        _fail("%s must be an independent regular non-link file" % label)
    if before.st_size < 1 or before.st_size > MAX_PNG_BYTES:
        _fail("%s exceeds the PNG byte safety limit" % label)
    payload, snapshot = stable_read_bytes(absolute)
    if snapshot["sha256"] != expected_sha:
        _fail("%s sha256 drifted" % label)
    try:
        width, height = png_dimensions(payload)
    except ImageValidationError as exc:
        raise BackfillError("%s is not a valid PNG: %s" % (label, exc)) from exc
    if (width, height) != (expected_width, expected_height):
        _fail("%s dimensions drifted" % label)
    if width * height > MAX_PNG_PIXELS:
        _fail("%s exceeds the decoded pixel safety limit" % label)
    return absolute, payload


def _decode_rgba(payload, expected_format, label):
    try:
        from PIL import Image
    except ImportError as exc:
        raise BackfillError(
            "Pillow is required to verify exact parent/crop pixel geometry; "
            "nothing was installed"
        ) from exc
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != expected_format:
                _fail("%s image is not %s" % (label, expected_format))
            result = image.convert("RGBA")
            result.load()
    except (OSError, ValueError) as exc:
        raise BackfillError("%s PNG decode failed: %s" % (label, exc)) from exc
    return result


def _verify_pixel_crop(parent_payload, crop_payload, bbox, label):
    parent = _decode_rgba(parent_payload, "PNG", label + ".parent")
    crop = _decode_rgba(crop_payload, "PNG", label + ".crop")
    if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > parent.width or bbox[3] > parent.height:
        _fail("%s bbox_parent_pixels escapes the parent image" % label)
    selected = parent.crop(tuple(bbox))
    if selected.size != crop.size or selected.tobytes() != crop.tobytes():
        _fail(
            "%s crop pixels are not exactly the declared parent bbox; rescaled, "
            "padded, or approximate crops are not accepted" % label
        )


def _verify_composite_crop(parent_payload, crop_payload, composition, label):
    """Verify the candidate against an exact unscaled vertical composition."""

    try:
        from PIL import Image
    except ImportError as exc:
        raise BackfillError(
            "Pillow is required to verify exact composite pixels; nothing was installed"
        ) from exc
    parent = _decode_rgba(parent_payload, "PNG", label + ".parent")
    crop = _decode_rgba(crop_payload, "PNG", label + ".crop")
    expected = Image.new(
        "RGBA",
        (composition["output_width"], composition["output_height"]),
        tuple(composition["background_rgba"]),
    )
    y = 0
    for index, region in enumerate(composition["regions"]):
        bbox = tuple(region["bbox_parent_pixels"])
        component = parent.crop(bbox)
        if composition["horizontal_alignment"] == "left":
            x = 0
        elif composition["horizontal_alignment"] == "center":
            x = (expected.width - component.width) // 2
        else:
            x = expected.width - component.width
        expected.paste(component, (x, y))
        y += component.height
        if index + 1 < len(composition["regions"]):
            y += composition["gap_pixels"]
    if y != expected.height:
        _fail("%s deterministic composite height drifted" % label)
    if crop.size != expected.size or crop.tobytes() != expected.tobytes():
        _fail(
            "%s candidate pixels are not the exact unscaled parent-region "
            "vertical stack; editing, OCR, rescaling, padding drift, or "
            "approximate composition is forbidden" % label
        )


def _unit_item_ids(units):
    by_unit = {
        str(row.get("unit_id")): row for row in units
        if isinstance(row, dict) and row.get("unit_id") is not None
    }
    result = {}
    for unit_id, row in by_unit.items():
        value = row.get("external_id")
        if value not in (None, ""):
            result[unit_id] = str(value).strip()
    changed = True
    while changed:
        changed = False
        for unit_id, row in by_unit.items():
            if unit_id in result:
                continue
            paired = row.get("paired_unit_id")
            if paired is not None and str(paired) in result:
                result[unit_id] = result[str(paired)]
                changed = True
    return result


def _canonical_source(value, label):
    if value in (None, ""):
        return None
    return _canonical_path(value, label)


def _source_context(row, side, asset):
    if isinstance(asset.get("source_file"), str):
        source_file = asset["source_file"]
    elif side == "answer":
        source_file = row.get("answer_source_file") or row.get("source_file")
    else:
        source_file = row.get("source_file")
    if type(asset.get("source_page")) is int:
        pages = [asset["source_page"]]
    elif side == "answer":
        pages = row.get("answer_source_pages") or row.get("source_pages") or []
    else:
        pages = row.get("source_pages") or []
    source_file = _canonical_source(source_file, "source item asset source_file")
    return source_file, [page for page in pages if type(page) is int], asset.get(
        "source_sha256"
    )


def _unit_context(row, asset, side, units_by_id):
    owner = row
    explicit_source = asset.get("source_file")
    paired = units_by_id.get(str(row.get("paired_unit_id")))
    preferred_pair = False
    if isinstance(paired, dict):
        # Assets are mirrored across a reciprocal Q/A pair by the compiler.
        # A prompt mirror stored on the answer unit still belongs to the
        # question source/page, and an answer mirror on the question unit still
        # belongs to the answer source/page.  Never let the container row
        # silently rewrite that provenance.
        if side == "prompt" and row.get("kind") == "answer" and paired.get(
                "kind") == "question":
            preferred_pair = True
        elif side == "answer" and row.get("kind") == "question" and paired.get(
                "kind") == "answer":
            preferred_pair = True
        if preferred_pair and (
                explicit_source in (None, "")
                or explicit_source == paired.get("source_file")):
            owner = paired
    source_file = explicit_source or owner.get("source_file")
    page = asset.get("source_page")
    if type(page) is not int:
        page = owner.get("page")
    source_sha256 = asset.get("source_sha256") or owner.get("source_sha256")
    source_file = _canonical_source(
        source_file, "content-unit asset source_file"
    )
    return (
        source_file,
        [page] if type(page) is int else [],
        source_sha256,
        owner is not row,
    )


def _asset_descriptors(raw_input):
    ingestion = raw_input.get("ingestion")
    if not isinstance(ingestion, dict) or not isinstance(
            ingestion.get("content_units"), list):
        _fail("source_raw_input lacks ingestion-v2 content_units")
    descriptors = []

    units = ingestion["content_units"]
    units_by_id = {
        str(row.get("unit_id")): row for row in units
        if isinstance(row, dict) and row.get("unit_id") is not None
    }

    def add_nested(layer, row_index, row, item_id, chapter_id, source_kind,
                   assets, container_label):
        if assets is None:
            return
        if not isinstance(assets, list):
            _fail("%s assets must be an array" % container_label)
        for asset_index, asset in enumerate(assets):
            if not isinstance(asset, dict):
                _fail("%s[%d] must be an object" % (container_label, asset_index))
            role = asset.get("role")
            side = _side_for_role(role)
            mirrored = False
            if source_kind == "unit":
                source_file, pages, source_sha256, mirrored = _unit_context(
                    row, asset, side, units_by_id
                )
            else:
                source_file, pages, source_sha256 = _source_context(
                    row, side, asset
                )
            descriptors.append({
                "layer": layer,
                "row_index": row_index,
                "row": row,
                "item_id": item_id,
                "chapter_id": chapter_id,
                "side": side,
                "role": role,
                "source_file": source_file,
                "source_pages": pages,
                "source_sha256": source_sha256,
                "mirrored_from_pair": mirrored,
                "asset": asset,
                "container": assets,
                "asset_index": asset_index,
                "top_level": False,
                "label": "%s[%d]" % (container_label, asset_index),
            })

    for layer in ("quiz_bank", "teaching_examples"):
        rows = raw_input.get(layer)
        if not isinstance(rows, list):
            _fail("source_raw_input.%s must be an array" % layer)
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                _fail("source_raw_input.%s[%d] must be an object" % (layer, row_index))
            item_id = None if row.get("id") in (None, "") else str(row["id"]).strip()
            chapter_id = _optional_chapter_id(
                row.get("chapter") or row.get("phase"),
                "%s[%d].chapter" % (layer, row_index),
            )
            add_nested(
                layer, row_index, row, item_id, chapter_id, "source",
                row.get("assets"), "%s[%d].assets" % (layer, row_index),
            )

    inherited_ids = _unit_item_ids(units)
    for row_index, row in enumerate(units):
        if not isinstance(row, dict):
            _fail("ingestion.content_units[%d] must be an object" % row_index)
        unit_id = str(row.get("unit_id"))
        item_id = inherited_ids.get(unit_id)
        chapter_id = _optional_chapter_id(
            row.get("chapter_id") or row.get("phase_id"),
            "content_units[%d].chapter_id" % row_index,
        )
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            _fail("content_units[%d].metadata must be an object" % row_index)
        add_nested(
            "content_units", row_index, row, item_id, chapter_id, "unit",
            metadata.get("assets"),
            "content_units[%d].metadata.assets" % row_index,
        )
        top_path = row.get("asset_path")
        top_role = row.get("asset_role")
        if top_path is not None or top_role is not None:
            if top_path is None or top_role is None:
                _fail("content unit top-level asset_path/asset_role is incomplete")
            source_file, pages, source_sha256, mirrored = _unit_context(
                row, {}, _side_for_role(top_role), units_by_id
            )
            descriptors.append({
                "layer": "content_units",
                "row_index": row_index,
                "row": row,
                "item_id": item_id,
                "chapter_id": chapter_id,
                "side": _side_for_role(top_role),
                "role": top_role,
                "source_file": source_file,
                "source_pages": pages,
                "source_sha256": source_sha256,
                "mirrored_from_pair": mirrored,
                "asset": {
                    "path": top_path,
                    "role": top_role,
                    "sha256": metadata.get("asset_sha256"),
                },
                "container": None,
                "asset_index": None,
                "top_level": True,
                "label": "content_units[%d].asset_path" % row_index,
            })
    _bind_content_unit_top_level_asset_hashes(descriptors)
    return descriptors


def _descriptor_asset_sha256(descriptor):
    value = descriptor["asset"].get("sha256")
    return value if isinstance(value, str) else None


def _same_descriptor_binding(left, right):
    """Compare the non-revision facts of two declaration projections."""

    left_path = physical_asset_key(left["asset"].get("path"))
    right_path = physical_asset_key(right["asset"].get("path"))
    return (
        left_path is not None
        and left_path == right_path
        and left["item_id"] == right["item_id"]
        and left["chapter_id"] == right["chapter_id"]
        and left["role"] == right["role"]
        and left["source_file"] == right["source_file"]
        and sorted(set(left["source_pages"]))
        == sorted(set(right["source_pages"]))
    )


def _bind_content_unit_top_level_asset_hashes(descriptors):
    """Bind only a ContentUnit top-level projection to its exact nested twin.

    Historical ContentUnits can expose ``asset_path``/``asset_role`` while
    omitting ``metadata.asset_sha256`` even though the same unit's
    ``metadata.assets`` contains the exact revision.  That is one logical
    declaration represented twice, not permission to infer a missing revision
    for quiz/teaching rows or from another unit.
    """

    for descriptor in descriptors:
        if (not descriptor["top_level"]
                or descriptor["layer"] != "content_units"
                or _descriptor_asset_sha256(descriptor) is not None):
            continue
        candidates = [
            other for other in descriptors
            if other["layer"] == "content_units"
            and not other["top_level"]
            and other["row"] is descriptor["row"]
            and _same_descriptor_binding(descriptor, other)
            and _descriptor_asset_sha256(other) is not None
        ]
        revisions = {
            _descriptor_asset_sha256(other) for other in candidates
        }
        if len(revisions) > 1:
            _fail(
                "%s has conflicting exact same-unit nested asset revisions"
                % descriptor["label"]
            )
        if len(revisions) != 1:
            continue
        source_revisions = {
            other["source_sha256"] for other in [descriptor] + candidates
            if other["source_sha256"] is not None
        }
        if (not source_revisions
                or len(source_revisions) != 1):
            _fail(
                "%s cannot bind its nested asset revision across a missing or "
                "conflicting source revision" % descriptor["label"]
            )
        descriptor["asset"]["sha256"] = next(iter(revisions))


def _require_current_source_revision(descriptors, source_sha256, label):
    """Require one current declared/owner revision and reject every conflict."""

    declared = {
        descriptor["source_sha256"] for descriptor in descriptors
        if descriptor["source_sha256"] is not None
    }
    if not declared:
        _fail(
            "%s has no exact mirror or ContentUnit owner bound to the current "
            "source revision" % label
        )
    if declared != {source_sha256}:
        _fail(
            "%s has stale or conflicting source revisions across exact mirrors"
            % label
        )


def _descriptor_matches_binding(descriptor, *, item_id, chapter_id, role,
                                source_path, source_page,
                                asset_sha256, allow_missing_revision=False):
    declared_sha256 = _descriptor_asset_sha256(descriptor)
    return (
        descriptor["item_id"] == item_id
        and descriptor["chapter_id"] == chapter_id
        and descriptor["role"] == role
        and descriptor["source_file"] == source_path
        and source_page in descriptor["source_pages"]
        and (
            declared_sha256 == asset_sha256
            or (allow_missing_revision and declared_sha256 is None)
        )
    )


def _reconciled_asset(descriptor, descriptors, *, source_sha256, source_page,
                      asset_sha256, allow_missing_revision, label):
    """Inherit only type/bbox from an unambiguous exact declaration mirror."""

    path_key = physical_asset_key(descriptor["asset"].get("path"))
    declared_sha256 = _descriptor_asset_sha256(descriptor)
    if path_key is None or (
            declared_sha256 is None and not allow_missing_revision):
        _fail("%s needs a canonical path and exact asset sha256" % label)
    mirrors = [
        other for other in descriptors
        if physical_asset_key(other["asset"].get("path")) == path_key
        and other["item_id"] == descriptor["item_id"]
        and other["chapter_id"] == descriptor["chapter_id"]
        and other["role"] == descriptor["role"]
        and other["source_file"] == descriptor["source_file"]
        and source_page in other["source_pages"]
        and (
            _descriptor_asset_sha256(other) == asset_sha256
            or (
                allow_missing_revision
                and _descriptor_asset_sha256(other) is None
            )
        )
    ]
    if not mirrors:
        _fail("%s has no exact declaration mirror" % label)
    _require_current_source_revision(mirrors, source_sha256, label)
    result = copy.deepcopy(descriptor["asset"])
    for field in ("type", "source_bbox_pdf_points"):
        values = {}
        for mirror in mirrors:
            if field not in mirror["asset"]:
                continue
            value = mirror["asset"][field]
            values.setdefault(canonical_sha256(value), value)
        if len(values) > 1:
            _fail(
                "%s has ambiguous %s across exact path/hash/role/source/item mirrors"
                % (label, field)
            )
        if len(values) == 1:
            inherited = next(iter(values.values()))
            if field in result and canonical_sha256(result[field]) != canonical_sha256(
                    inherited):
                _fail("%s has contradictory %s" % (label, field))
            result[field] = copy.deepcopy(inherited)
    result["source_sha256"] = source_sha256
    result["sha256"] = asset_sha256
    result["source_file"] = descriptor["source_file"]
    result["source_page"] = source_page
    return result


def _dedupe_compact(container, receipt_id, label):
    indexes = [
        index for index, asset in enumerate(container)
        if isinstance(asset, dict) and asset.get("crop_receipt_id") == receipt_id
    ]
    if len(indexes) < 2:
        return
    captions = {
        asset.get("caption").strip()
        for asset in (container[index] for index in indexes)
        if isinstance(asset.get("caption"), str) and asset.get("caption").strip()
    }
    if len(captions) > 1:
        _fail("%s duplicate mirrors carry conflicting captions" % label)
    keep = indexes[0]
    if captions:
        container[keep]["caption"] = next(iter(captions))
    for index in reversed(indexes[1:]):
        del container[index]


def _replace_descriptors(descriptors, compact, deferred_dedupes):
    """Replace every exact mirror once and defer any index-shifting folds."""

    affected_containers = {}
    for descriptor in descriptors:
        if descriptor["top_level"]:
            continue
        old = descriptor["asset"]
        replacement = copy.deepcopy(compact)
        if (descriptor["layer"] != "content_units"
                and isinstance(old.get("caption"), str) and old["caption"].strip()):
            replacement["caption"] = old["caption"]
        descriptor["container"][descriptor["asset_index"]] = replacement
        affected_containers[id(descriptor["container"])] = (
            descriptor["container"], descriptor["label"]
        )
    for descriptor in descriptors:
        if not descriptor["top_level"]:
            continue
        row = descriptor["row"]
        metadata = row["metadata"]
        row["asset_path"] = None
        row["asset_role"] = None
        metadata.pop("asset_sha256", None)
        assets = metadata.setdefault("assets", [])
        if not isinstance(assets, list):
            _fail("cannot migrate top-level asset into non-array metadata.assets")
        if not any(
                isinstance(asset, dict)
                and asset.get("crop_receipt_id") == compact["crop_receipt_id"]
                for asset in assets):
            assets.append(copy.deepcopy(compact))
        affected_containers[id(assets)] = (assets, descriptor["label"])
    for container, label in affected_containers.values():
        deferred_dedupes[
            (id(container), compact["crop_receipt_id"])
        ] = (container, label, compact["crop_receipt_id"])


def _parser_receipt_for(parser_document, source_path, source_sha256, page):
    if (not isinstance(parser_document, dict)
            or parser_document.get("schema_version") != 1
            or not isinstance(parser_document.get("receipts"), list)):
        _fail("parser_receipts.json has an invalid v1 envelope")
    matches = [
        row for row in parser_document["receipts"]
        if isinstance(row, dict)
        and row.get("source_file") == source_path
        and row.get("source_sha256") == source_sha256
    ]
    if len(matches) != 1:
        _fail("source has missing or duplicate parser receipt")
    receipt = matches[0]
    if receipt.get("media_type") != "application/pdf":
        _fail("crop receipt backfill currently requires a PDF source")
    produced = receipt.get("produced_pages")
    if not isinstance(produced, list) or page not in produced:
        _fail("source page is not bound by the current parser receipt")
    if receipt.get("status") not in ("success", "review_required"):
        _fail("source parser receipt is not usable")
    return receipt


def _review_resolved(row, targets):
    if not isinstance(row, dict) or row.get("kind") not in REVIEW_KINDS:
        return False
    external_ids = row.get("external_ids")
    pages = row.get("pages")
    if not isinstance(external_ids, list) or not isinstance(pages, list):
        return False
    side = row.get("side")
    source_file = row.get("file")
    for target in targets:
        if (target["item_id"] in {str(value) for value in external_ids}
                and target["source_page"] in pages
                and source_file == target["source_path"]
                and side in (None, target["side"])):
            return True
    return False


def _prepare_plan(workspace, annotation_path):
    workspace = os.path.abspath(workspace)
    exam_start.require_full_processing(
        workspace, purpose="incremental strict crop receipt backfill"
    )
    pending_path = safe_workspace_entry(workspace, MATERIAL_BUILD_PENDING_PATH)
    if os.path.lexists(str(pending_path)):
        _fail(
            "%s is present; resume/supersede that material generation before backfill"
            % MATERIAL_BUILD_PENDING_PATH
        )
    interrupted = safe_workspace_entry(workspace, ".ingest/pending_ingest.json")
    if os.path.lexists(str(interrupted)):
        _fail("an interrupted ingestion transaction requires recovery before backfill")

    build_manifest_path = safe_workspace_entry(
        workspace, ".ingest/build_manifest.json"
    )
    build_manifest, build_manifest_bytes, _manifest_snapshot = _stable_json_file(
        build_manifest_path, ".ingest/build_manifest.json"
    )
    if (not isinstance(build_manifest, dict)
            or build_manifest.get("schema_version") != 2
            or build_manifest.get("pipeline_version") != "ingestion-v2"):
        _fail("crop receipt backfill requires a current ingestion-v2 build manifest")
    verify_material_build_receipt(
        workspace,
        build_manifest=build_manifest,
        require_manifest_binding=True,
        required=True,
    )
    source_root = build_manifest.get("source_root")
    if not isinstance(source_root, str) or not os.path.isdir(source_root):
        _fail("build manifest source_root is missing or unreadable")

    raw_path = safe_workspace_entry(workspace, SOURCE_RAW_INPUT_PATH)
    report_path = safe_workspace_entry(workspace, PARSE_REPORT_PATH)
    raw_input, base_raw_bytes, _raw_snapshot = _stable_json_file(
        raw_path, SOURCE_RAW_INPUT_PATH
    )
    parse_report, base_report_bytes, _report_snapshot = _stable_json_file(
        report_path, PARSE_REPORT_PATH
    )
    parser_document, _parser_bytes, _parser_snapshot = _stable_json_file(
        safe_workspace_entry(workspace, ".ingest/parser_receipts.json"),
        ".ingest/parser_receipts.json",
    )
    annotations, annotation_file_sha256 = _read_annotations(annotation_path)
    candidate_raw = copy.deepcopy(raw_input)
    candidate_report = copy.deepcopy(parse_report)
    # This ledger authorizes role changes introduced by *this* material
    # generation.  A compiled parse report may retain receipts consumed by an
    # earlier generation, but replaying those cumulative rows would make the
    # compiler compare historical migrations with this crop-only policy delta.
    # Backfill replaces declarations with new physical crop identities; it does
    # not authorize an in-place asset-role migration.  Leave the compiler's
    # bijective old-policy/candidate-policy check to fail closed if that premise
    # ever stops being true.
    candidate_report["asset_role_promotions"] = []
    descriptors = _asset_descriptors(candidate_raw)

    policy = audit_asset_policy(
        candidate_raw["quiz_bank"],
        candidate_raw["teaching_examples"],
        candidate_raw["ingestion"]["content_units"],
        workspace=workspace,
    )
    if policy["invalid_declarations"]:
        _fail(
            "current candidate asset declarations are invalid: %s"
            % "; ".join(policy["invalid_declarations"][:8])
        )
    # A page-shaped prompt target may currently share a physical identity with
    # a student-attempt declaration.  That is exactly the conflict a verified
    # clean component can repair.  Keep the taint inventory for candidate and
    # answer-side checks, then require the *post-migration* policy to be fully
    # clean before publication; unrelated or unhandled conflicts still fail.

    source_manifest = SourceManifest(workspace, source_root=source_root)
    existing_receipts = crop_receipt_index(
        candidate_report, require_index_sha256=True
    )
    receipts_by_id = {
        receipt_id: receipt for receipt_id, receipt in existing_receipts.items()
    }
    output_owners = {
        receipt.output_path: receipt.crop_receipt_id
        for receipt in receipts_by_id.values()
    }
    declared_paths = {
        physical_asset_key(descriptor["asset"].get("path"))
        for descriptor in descriptors
        if physical_asset_key(descriptor["asset"].get("path")) is not None
    }
    used_targets = set()
    used_candidate_paths = set()
    used_descriptors = set()
    deferred_dedupes = {}
    output_plans = []
    summaries = []

    renderer_config_sha256 = canonical_sha256({
        "schema_version": 1,
        "operation": "verified_pixel_copy",
        "parent_geometry": "integer_bbox_rgba_exact",
        "output_format": "png",
        "whole_page_fallback": False,
    })

    for annotation in annotations:
        line_number = annotation.pop("_line_number")
        label = "annotations line %d" % line_number
        operation = annotation.get("operation")
        if operation not in BACKFILL_OPERATIONS:
            _fail(
                "%s operation must be one of %s"
                % (label, ", ".join(sorted(BACKFILL_OPERATIONS)))
            )
        annotation_schema = annotation.get("schema_version")
        promotion_operation = operation == "promote_legacy_crop"
        nested_parent_operation = operation in (
            "promote_legacy_crop",
            "create_from_legacy_crop",
            "create_composite_from_legacy_crop",
        )
        composite_operation = operation in (
            "create_composite_from_parent",
            "create_composite_from_legacy_crop",
        )
        item_id = annotation.get("item_id")
        item_id_problem = stable_item_id_problem(item_id)
        if item_id_problem:
            _fail(
                "%s item_id violates the shared quiz/teaching/Guide identity "
                "contract: %s" % (label, item_id_problem)
            )
        chapter_id = _chapter_id(annotation.get("chapter_id"), label + ".chapter_id")
        if annotation["chapter_id"] != chapter_id:
            _fail("%s chapter_id must use canonical chNN form" % label)
        side = annotation.get("side")
        if side not in CROP_SIDES:
            _fail("%s side must be prompt or answer" % label)
        role = annotation.get("role")
        if _side_for_role(role) != side:
            _fail("%s role is incompatible with side" % label)
        if annotation.get("content_scope") not in CROP_CONTENT_SCOPES:
            _fail("%s content_scope is invalid" % label)
        if composite_operation and (
                side != "prompt" or role != "question_context"):
            _fail(
                "%s composite backfill is prompt/question_context only" % label
            )
        source_path = _canonical_path(annotation["source_path"], label + ".source_path")
        source_id = annotation.get("source_id")
        if source_id != make_source_id(source_path):
            _fail("%s source_id does not match source_path" % label)
        source_sha256 = _sha(annotation.get("source_sha256"), label + ".source_sha256")
        source_page = _positive_int(annotation.get("source_page"), label + ".source_page")
        source_record = source_manifest.verify_current(source_id, source_sha256)
        if source_record.path != source_path or source_record.media_type != "application/pdf":
            _fail("%s source manifest binding is not the declared PDF" % label)
        _parser_receipt_for(
            parser_document, source_path, source_sha256, source_page
        )

        parent_path = _canonical_path(
            annotation["parent_asset_path"], label + ".parent_asset_path"
        )
        target_path = _canonical_path(
            annotation["target_asset_path"], label + ".target_asset_path"
        )
        crop_path = _canonical_path(
            annotation["crop_asset_path"], label + ".crop_asset_path"
        )
        if operation == "upgrade_existing" and target_path != crop_path:
            _fail(
                "%s upgrade_existing requires target_asset_path=crop_asset_path"
                % label
            )
        if promotion_operation and not (
                parent_path == target_path == crop_path):
            _fail(
                "%s promote_legacy_crop requires parent, target, and crop to "
                "name the same declared legacy crop" % label
            )
        if operation in (
                "create_from_parent", "create_composite_from_parent",
                "create_from_legacy_crop",
                "create_composite_from_legacy_crop",
        ) and target_path == crop_path:
            _fail(
                "%s %s requires a separate reviewed candidate PNG"
                % (label, operation)
            )
        if operation in (
                "create_from_parent", "create_composite_from_parent",
                "create_from_legacy_crop",
                "create_composite_from_legacy_crop",
        ) and target_path != parent_path:
            _fail(
                "%s %s requires target_asset_path to be the bound parent asset"
                % (label, operation)
            )
        if not promotion_operation and parent_path == crop_path:
            _fail("%s parent and crop assets must be distinct" % label)
        parent_width = _positive_int(
            annotation["parent_width"], label + ".parent_width"
        )
        parent_height = _positive_int(
            annotation["parent_height"], label + ".parent_height"
        )
        crop_width = _positive_int(annotation["crop_width"], label + ".crop_width")
        crop_height = _positive_int(annotation["crop_height"], label + ".crop_height")
        parent_sha256 = _sha(
            annotation["parent_asset_sha256"], label + ".parent_asset_sha256"
        )
        target_sha256 = _sha(
            annotation["target_asset_sha256"], label + ".target_asset_sha256"
        )
        crop_sha256 = _sha(
            annotation["crop_asset_sha256"], label + ".crop_asset_sha256"
        )
        if (operation == "upgrade_existing" and target_sha256 != crop_sha256):
            _fail("%s target hash must equal the existing crop hash" % label)
        if promotion_operation and not (
                parent_sha256 == target_sha256 == crop_sha256):
            _fail(
                "%s promote_legacy_crop requires one exact asset revision" % label
            )
        if (operation in (
                "create_from_parent", "create_composite_from_parent",
                "create_from_legacy_crop",
                "create_composite_from_legacy_crop",
        ) and target_sha256 != parent_sha256):
            _fail("%s target hash must equal the bound parent hash" % label)
        _parent_absolute, parent_payload = _safe_png(
            workspace, parent_path, parent_sha256,
            parent_width, parent_height, label + ".parent_asset",
        )
        _crop_absolute, crop_payload = _safe_png(
            workspace, crop_path, crop_sha256,
            crop_width, crop_height, label + ".crop_asset",
        )
        if (not promotion_operation
                and workspace_asset_identity_key(parent_path, workspace) == (
                    workspace_asset_identity_key(crop_path, workspace))):
            _fail("%s parent and crop assets share one physical file identity" % label)
        try:
            semantic = SemanticPurityReview.from_dict(annotation["semantic_purity"])
            semantic.validate(
                expected_item_id=item_id,
                expected_side=side,
                expected_crop_sha256=crop_sha256,
            )
        except CropContractError as exc:
            raise BackfillError("%s semantic_purity: %s" % (label, exc)) from exc
        if semantic.schema_version < 2:
            _fail(
                "%s new strict backfill requires semantic_purity schema v2"
                % label
            )
        candidate_tainted = workspace_asset_is_student_attempt(
            crop_path, workspace, policy
        )
        parent_tainted = workspace_asset_is_student_attempt(
            parent_path, workspace, policy
        )
        target_tainted = workspace_asset_is_student_attempt(
            target_path, workspace, policy
        )
        if candidate_tainted:
            _fail("%s reviewed candidate is student-attempt tainted" % label)
        if side == "answer" and (parent_tainted or target_tainted):
            _fail(
                "%s answer-side parent/target is non-official student-attempt evidence"
                % label
            )

        page_box = _rect(
            annotation["page_box_pdf_points"], label + ".page_box_pdf_points"
        )
        parent_source_bbox = (
            _rect(
                annotation["parent_source_bbox_pdf_points"],
                label + ".parent_source_bbox_pdf_points",
            )
            if nested_parent_operation else list(page_box)
        )
        _rect_within(
            parent_source_bbox,
            page_box,
            label + ".parent_source_bbox_pdf_points",
        )
        composition = None
        if composite_operation:
            composition = _composite_contract(
                annotation,
                page_box,
                item_id,
                semantic.required_context_ids,
                label,
            )
            _verify_composite_crop(
                parent_payload, crop_payload, composition, label
            )
            bbox_pdf = [
                min(row["bbox_pdf_points"][0] for row in composition["regions"]),
                min(row["bbox_pdf_points"][1] for row in composition["regions"]),
                max(row["bbox_pdf_points"][2] for row in composition["regions"]),
                max(row["bbox_pdf_points"][3] for row in composition["regions"]),
            ]
            evidence = {
                "schema_version": annotation_schema,
                "evidence_kind": (
                    "backfill_nested_parent_vertical_stack"
                    if nested_parent_operation
                    else "backfill_same_parent_vertical_stack"
                ),
                "operation": operation,
                "target_item_id": item_id,
                "side": side,
                "source_id": source_id,
                "source_file": source_path,
                "source_sha256": source_sha256,
                "source_page": source_page,
                "page_box_pdf_points": page_box,
                **(
                    {"parent_source_bbox_pdf_points": parent_source_bbox}
                    if nested_parent_operation else {}
                ),
                "bbox_pdf_points": bbox_pdf,
                "composition": composition,
                "crop_asset_sha256": crop_sha256,
            }
        elif promotion_operation:
            bbox_pdf = _rect(
                annotation["bbox_pdf_points"], label + ".bbox_pdf_points"
            )
            if any(abs(left - right) > 1e-6 for left, right in zip(
                    bbox_pdf, parent_source_bbox)):
                _fail(
                    "%s promoted crop bbox must equal its declared parent source bbox"
                    % label
                )
            if (parent_width, parent_height) != (crop_width, crop_height):
                _fail("%s promoted crop dimensions must equal parent dimensions" % label)
            evidence = {
                "schema_version": ANNOTATION_SCHEMA_VERSION,
                "evidence_kind": "backfill_declared_legacy_crop_promotion",
                "operation": operation,
                "target_item_id": item_id,
                "side": side,
                "source_id": source_id,
                "source_file": source_path,
                "source_sha256": source_sha256,
                "source_page": source_page,
                "page_box_pdf_points": page_box,
                "parent_source_bbox_pdf_points": parent_source_bbox,
                "bbox_pdf_points": bbox_pdf,
                "parent_asset_path": parent_path,
                "parent_asset_sha256": parent_sha256,
                "parent_width": parent_width,
                "parent_height": parent_height,
                "target_asset_path": target_path,
                "target_asset_sha256": target_sha256,
                "crop_asset_path": crop_path,
                "crop_asset_sha256": crop_sha256,
                "crop_width": crop_width,
                "crop_height": crop_height,
            }
        else:
            bbox_pixels = _rect(
                annotation["bbox_parent_pixels"],
                label + ".bbox_parent_pixels",
                integer=True,
            )
            if (bbox_pixels[2] - bbox_pixels[0] != crop_width
                    or bbox_pixels[3] - bbox_pixels[1] != crop_height):
                _fail("%s crop dimensions do not equal bbox_parent_pixels" % label)
            if bbox_pixels == [0, 0, parent_width, parent_height]:
                _fail(
                    "%s whole-page crops are not accepted by strict backfill; "
                    "select the reviewed target/context only" % label
                )
            _verify_pixel_crop(parent_payload, crop_payload, bbox_pixels, label)
            bbox_pdf = _rect(
                annotation["bbox_pdf_points"], label + ".bbox_pdf_points"
            )
            expected_bbox_pdf = _map_parent_pixels_to_pdf(
                bbox_pixels,
                parent_width,
                parent_height,
                parent_source_bbox,
            )
            if any(abs(left - right) > 1e-6 for left, right in zip(
                    bbox_pdf, expected_bbox_pdf)):
                _fail("%s bbox_pdf_points does not exactly map from parent pixels" % label)
            evidence = {
                "schema_version": annotation_schema,
                "evidence_kind": (
                    "backfill_nested_parent_pixel_crop"
                    if nested_parent_operation
                    else "backfill_parent_pixel_crop"
                ),
                "operation": operation,
                "target_item_id": item_id,
                "side": side,
                "source_id": source_id,
                "source_file": source_path,
                "source_sha256": source_sha256,
                "source_page": source_page,
                "page_box_pdf_points": page_box,
                **(
                    {"parent_source_bbox_pdf_points": parent_source_bbox}
                    if nested_parent_operation else {}
                ),
                "bbox_pdf_points": bbox_pdf,
                "parent_asset_path": parent_path,
                "parent_asset_sha256": parent_sha256,
                "parent_width": parent_width,
                "parent_height": parent_height,
                "bbox_parent_pixels": bbox_pixels,
                "target_asset_path": target_path,
                "target_asset_sha256": target_sha256,
                "crop_asset_path": crop_path,
                "crop_asset_sha256": crop_sha256,
                "crop_width": crop_width,
                "crop_height": crop_height,
            }
        if semantic.schema_version >= 2:
            evidence["required_context_ids"] = list(
                semantic.required_context_ids
            )
        expected_evidence_binding = canonical_sha256(evidence)
        if semantic.evidence_binding_sha256 != expected_evidence_binding:
            _fail("%s semantic evidence binding is stale or belongs to other evidence" % label)

        target_asset_key = physical_asset_key(target_path)
        candidate_key = physical_asset_key(crop_path)
        annotation_target = (
            operation, item_id, side, target_asset_key, candidate_key
        )
        if annotation_target in used_targets:
            _fail("%s duplicates the same target item/side/crop" % label)
        if candidate_key in used_candidate_paths:
            _fail("%s reuses one reviewed candidate crop across targets" % label)
        used_targets.add(annotation_target)
        used_candidate_paths.add(candidate_key)
        if operation in (
                "create_from_parent", "create_composite_from_parent",
                "create_from_legacy_crop",
                "create_composite_from_legacy_crop",
        ) and candidate_key in declared_paths:
            _fail(
                "%s %s candidate PNG must not already be a raw asset declaration"
                % (label, operation)
            )

        path_matches = [
            descriptor for descriptor in descriptors
            if physical_asset_key(descriptor["asset"].get("path")) == target_asset_key
        ]
        if not path_matches:
            _fail("%s target asset is not declared by the current raw input" % label)
        target_matches = []
        foreign_target_declarations = []
        for descriptor in path_matches:
            matches_target = _descriptor_matches_binding(
                descriptor,
                item_id=item_id,
                chapter_id=chapter_id,
                role=role,
                source_path=source_path,
                source_page=source_page,
                asset_sha256=target_sha256,
                allow_missing_revision=(
                    annotation_schema == ANNOTATION_LEGACY_SCHEMA_VERSION
                ),
            )
            if matches_target:
                target_matches.append(descriptor)
            else:
                foreign_target_declarations.append(descriptor)
        if not target_matches:
            _fail(
                "%s target_asset_path has no exact item/side/source/page declaration"
                % label
            )
        _require_current_source_revision(target_matches, source_sha256, label)
        permitted_taint_parents = (
            nested_parent_operation
            and not promotion_operation
            and side == "prompt"
            and all(
                descriptor["role"] == "student_attempt"
                for descriptor in foreign_target_declarations
            )
        )
        if foreign_target_declarations and (
                operation == "upgrade_existing"
                or promotion_operation
                or (
                    nested_parent_operation
                    and not permitted_taint_parents
                )):
            _fail(
                "%s existing crop path is shared by another/ambiguous item; "
                "it cannot be promoted as target-only" % label
            )
        for descriptor in target_matches:
            descriptor_identity = (descriptor["layer"], descriptor["row_index"],
                                   descriptor["label"])
            if descriptor_identity in used_descriptors:
                _fail("%s declaration is selected by multiple annotations" % label)
            asset = _reconciled_asset(
                descriptor,
                descriptors,
                source_sha256=source_sha256,
                source_page=source_page,
                asset_sha256=target_sha256,
                allow_missing_revision=(
                    annotation_schema == ANNOTATION_LEGACY_SCHEMA_VERSION
                ),
                label=descriptor["label"],
            )
            if "crop_receipt_id" in asset:
                _fail("%s target already has a receipt; backfill is not applicable" % label)
            if asset.get("sha256") != target_sha256:
                _fail("%s target asset declaration hash drifted" % label)
            if asset.get("source_sha256") != source_sha256:
                _fail("%s target declaration source hash drifted" % label)
            asset_type = asset.get("type")
            if operation == "upgrade_existing":
                if asset_type != "crop_image":
                    _fail("%s upgrade_existing target must be a crop_image" % label)
                if asset.get("source_bbox_pdf_points") not in (None, bbox_pdf):
                    _fail("%s existing crop PDF bbox differs from annotation" % label)
            elif nested_parent_operation:
                if asset_type != "crop_image":
                    _fail("%s nested parent target must be a legacy crop_image" % label)
                if asset.get("source_bbox_pdf_points") != parent_source_bbox:
                    _fail(
                        "%s nested parent target source bbox differs from the "
                        "explicit parent_source_bbox_pdf_points" % label
                    )
            else:
                if asset_type not in ("page_image", "crop_image"):
                    _fail(
                        "%s %s target must be a page-shaped image"
                        % (label, operation)
                    )
                if (asset_type == "crop_image"
                        and asset.get("source_bbox_pdf_points") != page_box):
                    _fail(
                        "%s %s crop_image target is not proven to cover the full "
                        "declared PDF page box" % (label, operation)
                    )
            used_descriptors.add(descriptor_identity)

        parent_key = physical_asset_key(parent_path)
        parent_matches = [
            descriptor for descriptor in descriptors
            if physical_asset_key(descriptor["asset"].get("path")) == parent_key
        ]
        if not parent_matches:
            _fail("%s parent asset is not declared by the current raw input" % label)
        parent_bound = False
        for descriptor in parent_matches:
            if not _descriptor_matches_binding(
                    descriptor,
                    item_id=item_id,
                    chapter_id=chapter_id,
                    role=role,
                    source_path=source_path,
                    source_page=source_page,
                    asset_sha256=parent_sha256,
                    allow_missing_revision=(
                        annotation_schema == ANNOTATION_LEGACY_SCHEMA_VERSION
                    )):
                continue
            asset = _reconciled_asset(
                descriptor,
                descriptors,
                source_sha256=source_sha256,
                source_page=source_page,
                asset_sha256=parent_sha256,
                allow_missing_revision=(
                    annotation_schema == ANNOTATION_LEGACY_SCHEMA_VERSION
                ),
                label=descriptor["label"],
            )
            if nested_parent_operation:
                valid_parent = (
                    asset.get("type") == "crop_image"
                    and asset.get("source_bbox_pdf_points") == parent_source_bbox
                )
            else:
                valid_parent = (
                    asset.get("type") == "page_image"
                    or (
                        asset.get("type") == "crop_image"
                        and asset.get("source_bbox_pdf_points") == page_box
                    )
                )
            if valid_parent:
                parent_bound = True
                break
        if not parent_bound:
            _fail(
                "%s parent asset lacks an exact path/hash/role/source/item "
                "declaration with the required source bbox" % label
            )

        clean_annotation = copy.deepcopy(annotation)
        annotation_sha256 = canonical_sha256(clean_annotation)
        if promotion_operation:
            renderer_id = "legacy-crop-promotion"
            renderer_version = "1"
            operation_renderer_config = canonical_sha256({
                "schema_version": 1,
                "operation": "revision_bound_byte_copy",
                "source_geometry": "declared_crop_bbox",
                "output_format": "png",
                "whole_page_fallback": False,
            })
        elif nested_parent_operation:
            renderer_id = "nested-pixel-backfill"
            renderer_version = "1"
            operation_renderer_config = canonical_sha256({
                "schema_version": 1,
                "operation": "verified_nested_pixel_copy",
                "parent_geometry": "declared_source_bbox_integer_rgba_exact",
                "output_format": "png",
                "whole_page_fallback": False,
            })
        else:
            renderer_id = "pixel-backfill"
            renderer_version = "1"
            operation_renderer_config = renderer_config_sha256
        crop_spec = {
            "item_id": item_id,
            "chapter_id": chapter_id,
            "side": side,
            "role": role,
            "content_scope": annotation["content_scope"],
            "isolation": (
                "target_with_required_context"
                if semantic.required_context_ids else "target_item_only"
            ),
            "source_id": source_id,
            "source_file": source_path,
            "source_sha256": source_sha256,
            "source_page": source_page,
            "page_box_pdf_points": page_box,
            "bbox_pdf_points": bbox_pdf,
            "selection_method": semantic.reviewer_kind,
            "selection_evidence_sha256": annotation_sha256,
            "renderer_id": renderer_id,
            "renderer_version": renderer_version,
            "renderer_config_sha256": operation_renderer_config,
            "semantic_purity": semantic,
        }
        if composite_operation:
            composite_variant = (
                NESTED_COMPOSITE_CROP_VARIANT
                if nested_parent_operation else COMPOSITE_CROP_VARIANT
            )
            crop_spec.update({
                "renderer_id": (
                    "nested-pixel-composite-backfill"
                    if nested_parent_operation
                    else "pixel-composite-backfill"
                ),
                "renderer_version": "1",
                "renderer_config_sha256": canonical_sha256({
                    "schema_version": (
                        2 if nested_parent_operation else 1
                    ),
                    "operation": (
                        "same_source_crop_unscaled_vertical_stack"
                        if nested_parent_operation
                        else "same_parent_unscaled_vertical_stack"
                    ),
                    "layout": COMPOSITE_CROP_LAYOUT,
                    "output_format": "png",
                    "color_mode": "RGBA",
                    "whole_page_fallback": False,
                    "ocr": False,
                    "arbitrary_editing": False,
                }),
                "crop_variant": composite_variant,
                "composition": composition,
            })
        crop_spec_sha256 = make_crop_spec_sha256(**crop_spec)
        safe_item = _SAFE_NAME_RE.sub("_", item_id).strip("._") or "item"
        output_path = (
            "references/assets/backfill_%s_%s_p%d_crop_%s.png"
            % (safe_item[:80], side, source_page, crop_spec_sha256[:12])
        )
        output_key = physical_asset_key(output_path)
        if output_key in declared_paths or output_path in output_owners:
            _fail("%s canonical output path is already owned" % label)
        output_absolute = safe_workspace_entry(workspace, output_path)
        if os.path.lexists(str(output_absolute)):
            _fail("%s canonical output path already exists" % label)
        receipt = CropReceipt.create(
            output_path=output_path,
            output_sha256=crop_sha256,
            output_width=crop_width,
            output_height=crop_height,
            supersedes=(target_path,),
            **crop_spec
        )
        compact = compact_asset_from_receipt(receipt)
        validate_crop_asset_binding(compact, receipt)
        _replace_descriptors(target_matches, compact, deferred_dedupes)
        receipts_by_id[receipt.crop_receipt_id] = receipt
        output_owners[output_path] = receipt.crop_receipt_id
        declared_paths.add(output_key)
        output_plans.append({
            "path": output_path,
            "payload": crop_payload,
            "sha256": crop_sha256,
            "operation": operation,
            "target_asset_path": target_path,
            "target_asset_sha256": target_sha256,
            "parent_asset_path": parent_path,
            "parent_asset_sha256": parent_sha256,
            "parent_width": parent_width,
            "parent_height": parent_height,
            "crop_asset_path": crop_path,
            "crop_asset_sha256": crop_sha256,
            "crop_width": crop_width,
            "crop_height": crop_height,
        })
        summaries.append({
            "schema_version": annotation_schema,
            "annotation_file_sha256": annotation_file_sha256,
            "annotation_sha256": annotation_sha256,
            "item_id": item_id,
            "side": side,
            "operation": operation,
            "target_asset_path": target_path,
            "reviewed_candidate_path": crop_path,
            "output_path": output_path,
            "crop_receipt_id": receipt.crop_receipt_id,
            "semantic_purity_sha256": semantic.semantic_purity_sha256,
            "required_context_ids": list(semantic.required_context_ids),
            "parent_source_bbox_pdf_points": parent_source_bbox,
            "crop_variant": (
                composite_variant if composite_operation else None
            ),
        })

    # Folding removes list entries and therefore invalidates the frozen
    # descriptor indexes used by later annotations.  Perform it only after the
    # complete annotation batch has finished all descriptor selection and
    # replacement work.
    for container, dedupe_label, receipt_id in sorted(
            deferred_dedupes.values(), key=lambda row: (row[2], row[1])):
        _dedupe_compact(container, receipt_id, dedupe_label)

    candidate_report["crop_receipts"] = [
        receipts_by_id[receipt_id].to_dict()
        for receipt_id in sorted(receipts_by_id)
    ]
    candidate_report["crop_receipt_index_sha256"] = canonical_sha256(
        candidate_report["crop_receipts"]
    )
    history = candidate_report.setdefault("crop_receipt_backfills", [])
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        _fail("parse_report crop_receipt_backfills history is malformed")
    history.extend(copy.deepcopy(summaries))
    history.sort(key=lambda row: (
        row.get("item_id", ""), row.get("side", ""), row.get("crop_receipt_id", "")
    ))
    reviews = candidate_report.get("ai_review")
    if isinstance(reviews, list):
        candidate_report["ai_review"] = [
            row for row in reviews if not _review_resolved(row, [
                {
                    "item_id": summary["item_id"],
                    "side": summary["side"],
                    "source_path": receipts_by_id[summary["crop_receipt_id"]].source_file,
                    "source_page": receipts_by_id[summary["crop_receipt_id"]].source_page,
                }
                for summary in summaries
            ])
        ]
    warnings = candidate_report.get("warnings")
    if isinstance(warnings, list):
        resolved_warning_text = set()
        for summary in summaries:
            receipt = receipts_by_id[summary["crop_receipt_id"]]
            resolved_warning_text.add(
                "crop_semantic_review_required: %s (%s p.%d)"
                % (summary["item_id"], receipt.source_file, receipt.source_page)
            )
            resolved_warning_text.add(
                "item_asset_crop_not_materialized: %s (%s %s p.%d)"
                % (
                    summary["item_id"], summary["side"],
                    receipt.source_file, receipt.source_page,
                )
            )
        candidate_report["warnings"] = [
            value for value in warnings if value not in resolved_warning_text
        ]
    crop_receipt_index(candidate_report, require_index_sha256=True)

    candidate_policy = audit_asset_policy(
        candidate_raw["quiz_bank"],
        candidate_raw["teaching_examples"],
        candidate_raw["ingestion"]["content_units"],
        workspace=workspace,
        allow_missing_workspace_assets=True,
    )
    if candidate_policy["invalid_declarations"] or candidate_policy["conflicts"]:
        _fail(
            "candidate asset policy fails closed: %s"
            % "; ".join(
                (candidate_policy["invalid_declarations"]
                 + candidate_policy["conflicts"])[:8]
            )
        )

    raw_bytes = _json_bytes(candidate_raw)
    report_bytes = _json_bytes(candidate_report)
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    previous_manifest_sha256 = hashlib.sha256(build_manifest_bytes).hexdigest()
    pending = build_pending_generation(
        raw_sha256,
        report_sha256,
        candidate_raw,
        candidate_report,
        previous_manifest_sha256,
    )
    return {
        "workspace": workspace,
        "annotation_file_sha256": annotation_file_sha256,
        "raw_input": candidate_raw,
        "base_raw_sha256": hashlib.sha256(base_raw_bytes).hexdigest(),
        "raw_bytes": raw_bytes,
        "raw_sha256": raw_sha256,
        "parse_report": candidate_report,
        "base_report_sha256": hashlib.sha256(base_report_bytes).hexdigest(),
        "report_bytes": report_bytes,
        "report_sha256": report_sha256,
        "pending": pending,
        "outputs": output_plans,
        "summaries": summaries,
    }


def _atomic_write_bytes(path, payload):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(destination))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_new_bytes_exclusive(path, payload):
    """Create one new asset without ever replacing a raced-in path."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = None
    created = False
    try:
        descriptor = os.open(str(destination), flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if created and os.path.lexists(str(destination)):
                os.unlink(str(destination))
        except OSError:
            pass
        raise


def _publish_plan(plan):
    workspace = plan["workspace"]
    current_raw, _snapshot = stable_read_bytes(
        safe_workspace_entry(workspace, SOURCE_RAW_INPUT_PATH)
    )
    current_report, _snapshot = stable_read_bytes(
        safe_workspace_entry(workspace, PARSE_REPORT_PATH)
    )
    if hashlib.sha256(current_raw).hexdigest() != plan["base_raw_sha256"]:
        _fail("source_raw_input changed after the locked backfill plan was built")
    if hashlib.sha256(current_report).hexdigest() != plan["base_report_sha256"]:
        _fail("parse_report changed after the locked backfill plan was built")
    for output in plan["outputs"]:
        _parent_absolute, parent_payload = _safe_png(
            workspace,
            output["parent_asset_path"],
            output["parent_asset_sha256"],
            output["parent_width"],
            output["parent_height"],
            "publication parent asset",
        )
        _crop_absolute, crop_payload = _safe_png(
            workspace,
            output["crop_asset_path"],
            output["crop_asset_sha256"],
            output["crop_width"],
            output["crop_height"],
            "publication crop asset",
        )
        if crop_payload != output["payload"]:
            _fail("crop asset bytes changed after the locked plan was built")
    targets = [
        MATERIAL_BUILD_PENDING_PATH,
        SOURCE_RAW_INPUT_PATH,
        PARSE_REPORT_PATH,
    ] + [row["path"] for row in plan["outputs"]]
    store = IngestionStore(workspace)
    with store.ingest_transaction(targets):
        # material pending is the first candidate-generation publication.  The
        # outer pending_ingest transaction marker already blocks validators and
        # supplies crash rollback if the process stops between later writes.
        atomic_write_json(
            safe_workspace_entry(workspace, MATERIAL_BUILD_PENDING_PATH),
            plan["pending"],
        )
        for output in plan["outputs"]:
            destination = safe_workspace_entry(workspace, output["path"])
            if os.path.lexists(str(destination)):
                _fail("canonical output appeared during publication: %s" % output["path"])
            _write_new_bytes_exclusive(destination, output["payload"])
        _atomic_write_bytes(
            safe_workspace_entry(workspace, SOURCE_RAW_INPUT_PATH),
            plan["raw_bytes"],
        )
        _atomic_write_bytes(
            safe_workspace_entry(workspace, PARSE_REPORT_PATH),
            plan["report_bytes"],
        )
        # Re-run the complete policy with live output identities and hashes
        # before committing the bounded publication transaction.
        policy = audit_asset_policy(
            plan["raw_input"]["quiz_bank"],
            plan["raw_input"]["teaching_examples"],
            plan["raw_input"]["ingestion"]["content_units"],
            workspace=workspace,
        )
        if policy["invalid_declarations"] or policy["conflicts"]:
            _fail(
                "live candidate asset policy changed during publication: %s"
                % "; ".join(
                    (policy["invalid_declarations"] + policy["conflicts"])[:8]
                )
            )


def _emit(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(value.get("message") or value.get("status") or value)


def _compiler_control_hashes(workspace):
    result = {}
    for key, relative in (
            ("parser_receipts_sha256", ".ingest/parser_receipts.json"),
            ("source_manifest_sha256", ".ingest/source_manifest.json")):
        payload, _snapshot = stable_read_bytes(
            safe_workspace_entry(workspace, relative)
        )
        if len(payload) > MAX_CONTROL_BYTES:
            _fail("%s exceeds the control-document safety limit" % relative)
        result[key] = hashlib.sha256(payload).hexdigest()
    return result


def _compiler_execution_receipt(command, before, after, compiler_exit_code):
    if type(compiler_exit_code) is not int:
        _fail("compiler execution receipt exit code must be an integer")
    receipt = {
        "schema_version": 1,
        "record_type": "crop_receipt_backfill_compiler_execution",
        "invoked_compiler_command": list(command),
        "compiler_exit_code": compiler_exit_code,
        "before": dict(before),
        "after": dict(after),
    }
    return {
        "receipt": receipt,
        "receipt_sha256": canonical_sha256(receipt),
    }


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Validate or apply source/pixel/semantic-bound single or composite "
            "strict crop receipt backfills without reparsing course PDFs."
        )
    )
    parser.add_argument("command", choices=("validate", "apply"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    try:
        if args.command == "validate":
            with workspace_validation_lock(workspace):
                plan = _prepare_plan(workspace, args.annotations)
            payload = {
                "status": "validated",
                "mode": "dry_run",
                "workspace": workspace,
                "annotation_file_sha256": plan["annotation_file_sha256"],
                "receipt_count": len(plan["summaries"]),
                "generation_id": plan["pending"]["generation_id"],
                "raw_input_sha256": plan["raw_sha256"],
                "parse_report_sha256": plan["report_sha256"],
                "receipts": plan["summaries"],
            }
            _emit(payload, args.as_json)
            return 0

        with workspace_publication_lock(
                workspace, allow_material_generation=True):
            # Rebuild the entire plan while the state->ingestion lock is held;
            # no result from a prior dry run is trusted for publication.
            plan = _prepare_plan(workspace, args.annotations)
            _publish_plan(plan)

        command = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "ingest.py"),
            "--input", os.path.join(workspace, *SOURCE_RAW_INPUT_PATH.split("/")),
            "--output-dir", workspace,
            "--expected-input-sha256", plan["raw_sha256"],
        ]
        before_controls = _compiler_control_hashes(workspace)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        after_controls = _compiler_control_hashes(workspace)
        execution = _compiler_execution_receipt(
            command, before_controls, after_controls, completed.returncode
        )
        if completed.returncode != 0:
            payload = {
                "status": "compiler_failed",
                "workspace": workspace,
                "generation_id": plan["pending"]["generation_id"],
                "receipt_count": len(plan["summaries"]),
                "compiler_exit_code": completed.returncode,
                "compiler_error": (completed.stderr or completed.stdout).strip(),
                "execution_receipt": execution["receipt"],
                "execution_receipt_sha256": execution["receipt_sha256"],
                "recovery": (
                    "material_build_pending.json remains fail-closed; use the "
                    "documented material-build resume/supersede recovery path"
                ),
            }
            _emit(payload, args.as_json)
            return completed.returncode or 1
        payload = {
            "status": "applied",
            "workspace": workspace,
            "generation_id": plan["pending"]["generation_id"],
            "receipt_count": len(plan["summaries"]),
            "compiler_exit_code": 0,
            "execution_receipt": execution["receipt"],
            "execution_receipt_sha256": execution["receipt_sha256"],
            "receipts": plan["summaries"],
        }
        _emit(payload, args.as_json)
        return 0
    except (BackfillError, CropContractError, ConflictError,
            exam_start.FullProcessingRequired, OSError, ValueError) as exc:
        _emit({
            "status": "rejected",
            "workspace": workspace,
            "error": str(exc),
        }, args.as_json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
