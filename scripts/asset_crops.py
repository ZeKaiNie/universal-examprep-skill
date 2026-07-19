# -*- coding: utf-8 -*-
"""Strict deterministic contracts for target/context PDF crops.

The material builder keeps the complete :class:`CropReceipt` in its parse
report and places only the receipt ID plus the source-location controls on the
quiz asset declaration.  This module is deliberately stdlib-only apart from
the repository's PNG validator.  It never renders a whole page as a fallback.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from dataclasses import dataclass

try:
    from ingestion.identifiers import (
        canonical_json,
        make_source_id,
        normalize_workspace_path,
        safe_workspace_entry,
        validate_sha256,
    )
    from image_validation import ImageValidationError, png_dimensions
    import strict_json
except ImportError:  # imported as ``scripts.asset_crops``
    from scripts.ingestion.identifiers import (
        canonical_json,
        make_source_id,
        normalize_workspace_path,
        safe_workspace_entry,
        validate_sha256,
    )
    from scripts.image_validation import ImageValidationError, png_dimensions
    from scripts import strict_json

try:
    from stable_ids import stable_item_id_problem
except ImportError:  # imported as ``scripts.asset_crops``
    from scripts.stable_ids import stable_item_id_problem


LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION = 1
SEMANTIC_PURITY_SCHEMA_VERSION = 2
CROP_SIDES = frozenset(("prompt", "answer"))
CROP_CONTENT_SCOPES = frozenset(
    ("full_prompt", "figure_only", "full_answer", "answer_figure_only")
)
CROP_ISOLATIONS = frozenset((
    "target_item_only", "target_with_required_context",
))
CROP_SELECTION_METHODS = frozenset(("layout_auto", "model_vision", "human"))
SEMANTIC_REVIEWER_KINDS = frozenset(("model_vision", "human"))
SEMANTIC_PURITY_VERDICTS = frozenset((
    "target_item_only", "target_with_required_context",
))
COMPOSITE_CROP_VARIANT = "same_parent_vertical_stack_v1"
NESTED_COMPOSITE_CROP_VARIANT = "same_source_crop_vertical_stack_v2"
COMPOSITE_CROP_VARIANTS = frozenset((
    COMPOSITE_CROP_VARIANT,
    NESTED_COMPOSITE_CROP_VARIANT,
))
COMPOSITE_CROP_LAYOUT = "vertical_stack"
COMPOSITE_HORIZONTAL_ALIGNMENTS = frozenset(("left", "center", "right"))
MAX_COMPOSITE_REGIONS = 32
MAX_COMPOSITE_PIXELS = 100_000_000

PROMPT_CROP_ROLES = frozenset(("question_context",))
ANSWER_CROP_ROLES = frozenset(
    ("answer_context", "worked_solution", "student_attempt")
)

STRICT_CROP_ASSET_FIELDS = frozenset(
    (
        "crop_receipt_id",
        "crop_receipt_schema_version",
        "crop_spec_sha256",
        "semantic_purity_sha256",
        "semantic_purity_schema_version",
        "required_context_ids",
        "source_page",
        "source_bbox_pdf_points",
        "content_scope",
        "isolation",
    )
)
STRICT_CROP_ASSET_REQUIRED = frozenset(
    (
        "path",
        "role",
        "type",
        "sha256",
        "source_file",
        "source_sha256",
        "source_page",
        "source_bbox_pdf_points",
        "crop_receipt_id",
        "crop_spec_sha256",
        "content_scope",
        "isolation",
    )
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_CROP_ID_RE = re.compile(r"^crop_[0-9a-f]{64}$")
_PORTABLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$", re.I)
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$"
)
_MAX_RECEIPT_REPORT_BYTES = 64 * 1024 * 1024


class CropContractError(ValueError):
    """A crop annotation, receipt, or binding violated the exact contract."""


def _fail(message):
    raise CropContractError(message)


def canonical_sha256(value):
    """Hash strict JSON using the ingestion core's canonical encoding."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _strict_mapping(value, fields, label):
    if not isinstance(value, dict):
        _fail("%s must be an object" % label)
    expected = set(fields)
    actual = set(value)
    if expected != actual:
        _fail(
            "%s schema mismatch; missing=%r unknown=%r"
            % (label, sorted(expected - actual), sorted(actual - expected))
        )


def _nonempty(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    return value


def _portable_id(value, label):
    _nonempty(value, label)
    if not _PORTABLE_ID_RE.fullmatch(value):
        _fail("%s must be a portable identifier" % label)
    return value


def _stable_item_id(value, label):
    problem = stable_item_id_problem(value)
    if problem:
        _fail("%s violates the shared quiz/teaching/Guide identity contract: %s"
              % (label, problem))
    return value


def _enum(value, allowed, label):
    if value not in allowed:
        _fail("%s must be one of %s" % (label, ", ".join(sorted(allowed))))
    return value


def _sha(value, label):
    try:
        return validate_sha256(value, label)
    except ValueError as exc:
        raise CropContractError(str(exc)) from exc


def _path(value, label):
    try:
        normalized = normalize_workspace_path(value)
    except ValueError as exc:
        raise CropContractError("%s: %s" % (label, exc)) from exc
    if normalized != value:
        _fail("%s must use canonical POSIX separators" % label)
    return normalized


def _positive_integer(value, label):
    if type(value) is not int or value < 1:
        _fail("%s must be an integer >= 1" % label)
    return value


def _nonnegative_integer(value, label):
    if type(value) is not int or value < 0:
        _fail("%s must be an integer >= 0" % label)
    return value


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("%s must be a finite number" % label)
    value = float(value)
    if not math.isfinite(value):
        _fail("%s must be a finite number" % label)
    return 0.0 if value == 0 else value


def _bbox(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        _fail("%s must contain four coordinates" % label)
    result = [_finite_number(raw, "%s[%d]" % (label, index))
              for index, raw in enumerate(value)]
    if result[2] <= result[0] or result[3] <= result[1]:
        _fail("%s must have positive width and height" % label)
    return result


def _validate_side_role_scope(side, role, content_scope, label):
    _enum(side, CROP_SIDES, "%s.side" % label)
    _enum(content_scope, CROP_CONTENT_SCOPES, "%s.content_scope" % label)
    if side == "prompt":
        if role not in PROMPT_CROP_ROLES:
            _fail("%s.role is incompatible with side=prompt" % label)
        if content_scope not in ("full_prompt", "figure_only"):
            _fail("%s.content_scope is incompatible with side=prompt" % label)
    else:
        if role not in ANSWER_CROP_ROLES:
            _fail("%s.role is incompatible with side=answer" % label)
        if content_scope not in ("full_answer", "answer_figure_only"):
            _fail("%s.content_scope is incompatible with side=answer" % label)


def _validate_bbox_within(inner, outer, label):
    if not (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    ):
        _fail("%s must stay within the bound page box" % label)


def _pixel_bbox(value, width, height, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        _fail("%s must contain four integer pixel coordinates" % label)
    result = []
    for index, raw in enumerate(value):
        if type(raw) is not int:
            _fail("%s[%d] must be an integer" % (label, index))
        result.append(raw)
    if (result[0] < 0 or result[1] < 0
            or result[2] <= result[0] or result[3] <= result[1]
            or result[2] > width or result[3] > height):
        _fail("%s must be a positive rectangle inside the parent pixels" % label)
    return result


def _mapped_pdf_bbox(pixel_bbox, parent_width, parent_height, page_box):
    page_width = page_box[2] - page_box[0]
    page_height = page_box[3] - page_box[1]
    return [
        page_box[0] + (pixel_bbox[0] / float(parent_width)) * page_width,
        page_box[1] + (pixel_bbox[1] / float(parent_height)) * page_height,
        page_box[0] + (pixel_bbox[2] / float(parent_width)) * page_width,
        page_box[1] + (pixel_bbox[3] / float(parent_height)) * page_height,
    ]


def _rectangles_overlap(left, right):
    return not (
        left[2] <= right[0] or right[2] <= left[0]
        or left[3] <= right[1] or right[3] <= left[1]
    )


def normalize_composite_crop_spec(value, page_box_pdf_points,
                                  target_item_id, required_context_ids):
    """Validate the only supported deterministic multi-region composition.

    This is a receipt control-plane contract, not an image editor.  Every
    region is an unscaled rectangle from one exact parent PNG/source page and
    the output is a fully specified vertical stack.
    """

    legacy_fields = (
        "schema_version", "layout", "region_order", "gap_pixels",
        "background_rgba", "horizontal_alignment", "parent_asset_path",
        "parent_asset_sha256", "parent_width", "parent_height",
        "target_asset_path", "target_asset_sha256",
        "reviewed_candidate_path", "reviewed_candidate_sha256",
        "output_width", "output_height", "regions",
    )
    nested_fields = legacy_fields + ("parent_source_bbox_pdf_points",)
    if not isinstance(value, dict):
        _fail("composite crop spec must be an object")
    composition_schema = value.get("schema_version")
    if type(composition_schema) is not int:
        _fail("composite crop spec.schema_version must be the integer 1 or 2")
    if composition_schema == 1:
        _strict_mapping(value, legacy_fields, "composite crop spec")
    elif composition_schema == 2:
        _strict_mapping(value, nested_fields, "composite crop spec")
    else:
        _fail("composite crop spec.schema_version must be 1 or 2")
    if value["layout"] != COMPOSITE_CROP_LAYOUT:
        _fail("composite crop spec.layout must be vertical_stack")
    gap = _nonnegative_integer(value["gap_pixels"], "composite crop spec.gap_pixels")
    if gap > 4096:
        _fail("composite crop spec.gap_pixels exceeds the 4096-pixel limit")
    background = value["background_rgba"]
    if (not isinstance(background, (list, tuple)) or len(background) != 4
            or any(type(channel) is not int or not 0 <= channel <= 255
                   for channel in background)):
        _fail("composite crop spec.background_rgba must contain four bytes")
    alignment = _enum(
        value["horizontal_alignment"],
        COMPOSITE_HORIZONTAL_ALIGNMENTS,
        "composite crop spec.horizontal_alignment",
    )
    parent_path = _path(
        value["parent_asset_path"], "composite crop spec.parent_asset_path"
    )
    target_path = _path(
        value["target_asset_path"], "composite crop spec.target_asset_path"
    )
    candidate_path = _path(
        value["reviewed_candidate_path"],
        "composite crop spec.reviewed_candidate_path",
    )
    if target_path != parent_path:
        _fail("composite crop target must be the declared parent asset")
    if candidate_path == parent_path:
        _fail("composite crop candidate must be distinct from its parent")
    parent_sha = _sha(
        value["parent_asset_sha256"],
        "composite crop spec.parent_asset_sha256",
    )
    target_sha = _sha(
        value["target_asset_sha256"],
        "composite crop spec.target_asset_sha256",
    )
    candidate_sha = _sha(
        value["reviewed_candidate_sha256"],
        "composite crop spec.reviewed_candidate_sha256",
    )
    if target_sha != parent_sha:
        _fail("composite crop target hash must equal its parent hash")
    parent_width = _positive_integer(
        value["parent_width"], "composite crop spec.parent_width"
    )
    parent_height = _positive_integer(
        value["parent_height"], "composite crop spec.parent_height"
    )
    output_width = _positive_integer(
        value["output_width"], "composite crop spec.output_width"
    )
    output_height = _positive_integer(
        value["output_height"], "composite crop spec.output_height"
    )
    if output_width * output_height > MAX_COMPOSITE_PIXELS:
        _fail("composite crop output exceeds the decoded-pixel safety limit")

    page_box = _bbox(page_box_pdf_points, "composite crop page_box_pdf_points")
    if composition_schema == 1:
        # Historical composites mapped parent pixels through the complete PDF
        # page.  Preserve that exact contract and receipt identity.
        parent_source_bbox = list(page_box)
    else:
        parent_source_bbox = _bbox(
            value["parent_source_bbox_pdf_points"],
            "composite crop spec.parent_source_bbox_pdf_points",
        )
        _validate_bbox_within(
            parent_source_bbox,
            page_box,
            "composite crop spec.parent_source_bbox_pdf_points",
        )
    context_ids = list(required_context_ids or ())
    allowed_ids = [target_item_id] + context_ids
    regions = value["regions"]
    if (not isinstance(regions, list) or not 2 <= len(regions) <= MAX_COMPOSITE_REGIONS):
        _fail(
            "composite crop spec.regions must contain 2..%d regions"
            % MAX_COMPOSITE_REGIONS
        )
    normalized_regions = []
    region_ids = []
    covered_content_ids = set()
    pixel_boxes = []
    for index, row in enumerate(regions):
        label = "composite crop spec.regions[%d]" % index
        _strict_mapping(
            row,
            ("region_id", "content_ids", "bbox_parent_pixels", "bbox_pdf_points"),
            label,
        )
        region_id = _portable_id(row["region_id"], label + ".region_id")
        if region_id in region_ids:
            _fail("composite crop region_id values must be unique")
        content_ids = row["content_ids"]
        if (not isinstance(content_ids, list) or not content_ids
                or any(not isinstance(item, str) or not item or item != item.strip()
                       for item in content_ids)
                or content_ids != sorted(set(content_ids))):
            _fail("%s.content_ids must be a sorted unique non-empty string array" % label)
        if any(item not in allowed_ids for item in content_ids):
            _fail("%s.content_ids names undeclared semantic context" % label)
        covered_content_ids.update(content_ids)
        pixels = _pixel_bbox(
            row["bbox_parent_pixels"], parent_width, parent_height,
            label + ".bbox_parent_pixels",
        )
        if any(_rectangles_overlap(pixels, prior) for prior in pixel_boxes):
            _fail("composite crop parent regions must not overlap")
        pdf_bbox = _bbox(row["bbox_pdf_points"], label + ".bbox_pdf_points")
        _validate_bbox_within(pdf_bbox, page_box, label + ".bbox_pdf_points")
        expected_pdf = _mapped_pdf_bbox(
            pixels, parent_width, parent_height, parent_source_bbox
        )
        if any(abs(left - right) > 1e-6
               for left, right in zip(pdf_bbox, expected_pdf)):
            _fail("%s PDF bbox does not exactly map from parent pixels" % label)
        region_ids.append(region_id)
        pixel_boxes.append(pixels)
        normalized_regions.append({
            "region_id": region_id,
            "content_ids": list(content_ids),
            "bbox_parent_pixels": pixels,
            "bbox_pdf_points": pdf_bbox,
        })
    if covered_content_ids != set(allowed_ids):
        _fail(
            "composite crop regions must cover the target and every required context ID"
        )
    order = value["region_order"]
    if not isinstance(order, list) or order != region_ids:
        _fail("composite crop spec.region_order must exactly match regions order")
    widths = [row["bbox_parent_pixels"][2] - row["bbox_parent_pixels"][0]
              for row in normalized_regions]
    heights = [row["bbox_parent_pixels"][3] - row["bbox_parent_pixels"][1]
               for row in normalized_regions]
    expected_width = max(widths)
    expected_height = sum(heights) + gap * (len(heights) - 1)
    if (output_width, output_height) != (expected_width, expected_height):
        _fail("composite crop output dimensions do not match its deterministic stack")
    result = {
        "schema_version": composition_schema,
        "layout": COMPOSITE_CROP_LAYOUT,
        "region_order": list(region_ids),
        "gap_pixels": gap,
        "background_rgba": list(background),
        "horizontal_alignment": alignment,
        "parent_asset_path": parent_path,
        "parent_asset_sha256": parent_sha,
        "parent_width": parent_width,
        "parent_height": parent_height,
        "target_asset_path": target_path,
        "target_asset_sha256": target_sha,
        "reviewed_candidate_path": candidate_path,
        "reviewed_candidate_sha256": candidate_sha,
        "output_width": output_width,
        "output_height": output_height,
        "regions": normalized_regions,
    }
    if composition_schema >= 2:
        result["parent_source_bbox_pdf_points"] = parent_source_bbox
    return result


@dataclass(frozen=True)
class SemanticPurityReview:
    """Explicit revision-bound verdict for a target and named prerequisites.

    The human/model verdict stays machine-readable in the annotation and full
    crop receipt.  ``evidence_binding_sha256`` binds the host-specific evidence
    packet (preview/bbox for normal ingestion, parent/crop pixels for backfill),
    but never substitutes for the explicit verdict fields below.
    """

    schema_version: int
    target_item_id: str
    side: str
    crop_sha256: str
    verdict: str
    unrelated_content_present: bool
    student_attempt_present: bool
    detected_item_ids: tuple
    reviewer_kind: str
    reviewer: str
    reviewed_at: str
    evidence_binding_sha256: str
    required_context_ids: tuple = ()

    LEGACY_FIELDS = (
        "schema_version",
        "target_item_id",
        "side",
        "crop_sha256",
        "verdict",
        "unrelated_content_present",
        "student_attempt_present",
        "detected_item_ids",
        "reviewer_kind",
        "reviewer",
        "reviewed_at",
        "evidence_binding_sha256",
    )
    FIELDS = LEGACY_FIELDS + ("required_context_ids",)

    def __post_init__(self):
        if not isinstance(self.detected_item_ids, (list, tuple)):
            _fail("SemanticPurityReview.detected_item_ids must be an array")
        object.__setattr__(self, "detected_item_ids", tuple(self.detected_item_ids))
        if not isinstance(self.required_context_ids, (list, tuple)):
            _fail("SemanticPurityReview.required_context_ids must be an array")
        object.__setattr__(
            self, "required_context_ids", tuple(self.required_context_ids)
        )
        self.validate()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            _fail("SemanticPurityReview must be an object")
        if value.get("schema_version") == SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION:
            _strict_mapping(value, cls.LEGACY_FIELDS, "SemanticPurityReview")
            payload = {field: value[field] for field in cls.LEGACY_FIELDS}
            payload["required_context_ids"] = ()
            return cls(**payload)
        _strict_mapping(value, cls.FIELDS, "SemanticPurityReview")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def validate(self, expected_item_id=None, expected_side=None,
                 expected_crop_sha256=None):
        if (type(self.schema_version) is not int
                or self.schema_version not in (
                    SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION,
                    SEMANTIC_PURITY_SCHEMA_VERSION,
                )):
            _fail(
                "SemanticPurityReview.schema_version must be %d (historical) or %d"
                % (
                    SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION,
                    SEMANTIC_PURITY_SCHEMA_VERSION,
                )
            )
        if self.schema_version == SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION:
            _nonempty(
                self.target_item_id,
                "SemanticPurityReview.target_item_id",
            )
        else:
            _stable_item_id(
                self.target_item_id,
                "SemanticPurityReview.target_item_id",
            )
        _enum(self.side, CROP_SIDES, "SemanticPurityReview.side")
        _sha(self.crop_sha256, "SemanticPurityReview.crop_sha256")
        _enum(
            self.verdict,
            SEMANTIC_PURITY_VERDICTS,
            "SemanticPurityReview.verdict",
        )
        if type(self.unrelated_content_present) is not bool:
            _fail(
                "SemanticPurityReview.unrelated_content_present must be a boolean"
            )
        if self.unrelated_content_present:
            _fail("semantic purity review found unrelated content")
        if type(self.student_attempt_present) is not bool:
            _fail(
                "SemanticPurityReview.student_attempt_present must be a boolean"
            )
        if self.student_attempt_present:
            _fail("semantic purity review found student-attempt content")
        contexts = []
        for index, value in enumerate(self.required_context_ids):
            contexts.append(
                (
                    _nonempty(
                        value,
                        "SemanticPurityReview.required_context_ids[%d]" % index,
                    )
                    if self.schema_version
                    == SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION
                    else _stable_item_id(
                        value,
                        "SemanticPurityReview.required_context_ids[%d]" % index,
                    )
                )
            )
        if self.schema_version == SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION:
            if contexts:
                _fail(
                    "historical SemanticPurityReview v1 cannot carry required contexts"
                )
        elif contexts != sorted(set(contexts)):
            _fail(
                "SemanticPurityReview.required_context_ids must be sorted and unique"
            )
        if self.target_item_id in contexts:
            _fail(
                "SemanticPurityReview.required_context_ids cannot repeat the target"
            )
        expected_verdict = (
            "target_with_required_context" if contexts else "target_item_only"
        )
        if self.verdict != expected_verdict:
            _fail(
                "SemanticPurityReview.verdict must be %s for this context set"
                % expected_verdict
            )
        detected = []
        for index, value in enumerate(self.detected_item_ids):
            detected.append(
                (
                    _nonempty(
                        value,
                        "SemanticPurityReview.detected_item_ids[%d]" % index,
                    )
                    if self.schema_version
                    == SEMANTIC_PURITY_LEGACY_SCHEMA_VERSION
                    else _stable_item_id(
                        value,
                        "SemanticPurityReview.detected_item_ids[%d]" % index,
                    )
                )
            )
        if detected != [self.target_item_id] + contexts:
            _fail(
                "SemanticPurityReview.detected_item_ids must contain exactly "
                "the target item followed by required_context_ids"
            )
        _enum(
            self.reviewer_kind,
            SEMANTIC_REVIEWER_KINDS,
            "SemanticPurityReview.reviewer_kind",
        )
        _nonempty(self.reviewer, "SemanticPurityReview.reviewer")
        if not isinstance(self.reviewed_at, str) or not _UTC_TIMESTAMP_RE.fullmatch(
                self.reviewed_at):
            _fail(
                "SemanticPurityReview.reviewed_at must be UTC YYYY-MM-DDTHH:MM:SSZ"
            )
        _sha(
            self.evidence_binding_sha256,
            "SemanticPurityReview.evidence_binding_sha256",
        )
        if (expected_item_id is not None
                and self.target_item_id != str(expected_item_id)):
            _fail("semantic purity target_item_id does not match the crop item")
        if expected_side is not None and self.side != expected_side:
            _fail("semantic purity side does not match the crop side")
        if (expected_crop_sha256 is not None
                and self.crop_sha256 != expected_crop_sha256):
            _fail("semantic purity crop_sha256 does not match the crop bytes")
        return self

    @property
    def semantic_purity_sha256(self):
        return canonical_sha256(self.to_dict())

    def to_dict(self):
        payload = {
            "schema_version": self.schema_version,
            "target_item_id": self.target_item_id,
            "side": self.side,
            "crop_sha256": self.crop_sha256,
            "verdict": self.verdict,
            "unrelated_content_present": self.unrelated_content_present,
            "student_attempt_present": self.student_attempt_present,
            "detected_item_ids": list(self.detected_item_ids),
            "reviewer_kind": self.reviewer_kind,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "evidence_binding_sha256": self.evidence_binding_sha256,
        }
        if self.schema_version >= SEMANTIC_PURITY_SCHEMA_VERSION:
            payload["required_context_ids"] = list(self.required_context_ids)
        return payload


@dataclass(frozen=True)
class CropAnnotation:
    """One model/human selection bound to one exact preview revision."""

    schema_version: int
    item_id: str
    side: str
    role: str
    content_scope: str
    source_id: str
    source_file: str
    source_sha256: str
    source_page: int
    preview_sha256: str
    preview_width: int
    preview_height: int
    bbox_preview_pixels: tuple
    selection_method: str
    reviewer: str
    semantic_purity: object

    FIELDS = (
        "schema_version",
        "item_id",
        "side",
        "role",
        "content_scope",
        "source_id",
        "source_file",
        "source_sha256",
        "source_page",
        "preview_sha256",
        "preview_width",
        "preview_height",
        "bbox_preview_pixels",
        "selection_method",
        "reviewer",
        "semantic_purity",
    )
    LEGACY_FIELDS = FIELDS[:-1]

    def __post_init__(self):
        object.__setattr__(
            self,
            "bbox_preview_pixels",
            tuple(_bbox(self.bbox_preview_pixels, "CropAnnotation.bbox_preview_pixels")),
        )
        if self.semantic_purity is not None and not isinstance(
                self.semantic_purity, SemanticPurityReview):
            object.__setattr__(
                self,
                "semantic_purity",
                SemanticPurityReview.from_dict(self.semantic_purity),
            )
        self.validate()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            _fail("CropAnnotation must be an object")
        if value.get("schema_version") == LEGACY_SCHEMA_VERSION:
            _strict_mapping(value, cls.LEGACY_FIELDS, "CropAnnotation")
            payload = {field: value[field] for field in cls.LEGACY_FIELDS}
            payload["semantic_purity"] = None
            return cls(**payload)
        _strict_mapping(value, cls.FIELDS, "CropAnnotation")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version not in (
                LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
            _fail(
                "CropAnnotation.schema_version must be %d (historical) or %d"
                % (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
            )
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            _nonempty(self.item_id, "CropAnnotation.item_id")
        else:
            _stable_item_id(self.item_id, "CropAnnotation.item_id")
        _validate_side_role_scope(
            self.side, self.role, self.content_scope, "CropAnnotation"
        )
        _path(self.source_file, "CropAnnotation.source_file")
        expected_source_id = make_source_id(self.source_file)
        if self.source_id != expected_source_id:
            _fail("CropAnnotation.source_id does not match source_file")
        _sha(self.source_sha256, "CropAnnotation.source_sha256")
        _positive_integer(self.source_page, "CropAnnotation.source_page")
        _sha(self.preview_sha256, "CropAnnotation.preview_sha256")
        width = _positive_integer(self.preview_width, "CropAnnotation.preview_width")
        height = _positive_integer(self.preview_height, "CropAnnotation.preview_height")
        bbox = _bbox(self.bbox_preview_pixels, "CropAnnotation.bbox_preview_pixels")
        _validate_bbox_within(bbox, [0.0, 0.0, float(width), float(height)],
                              "CropAnnotation.bbox_preview_pixels")
        _enum(
            self.selection_method,
            CROP_SELECTION_METHODS,
            "CropAnnotation.selection_method",
        )
        if self.selection_method == "layout_auto" and self.reviewer != "deterministic-layout":
            _fail(
                "CropAnnotation.reviewer must be deterministic-layout for layout_auto"
            )
        _nonempty(self.reviewer, "CropAnnotation.reviewer")
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            if self.semantic_purity is not None:
                _fail("historical CropAnnotation v1 cannot carry semantic_purity")
            return self
        if self.selection_method == "layout_auto":
            _fail(
                "CropAnnotation v2 requires a model_vision or human semantic review"
            )
        if self.semantic_purity is None:
            _fail("CropAnnotation v2 requires semantic_purity")
        if self.semantic_purity.schema_version != SEMANTIC_PURITY_SCHEMA_VERSION:
            _fail(
                "new CropAnnotation v2 requires SemanticPurityReview schema v%d"
                % SEMANTIC_PURITY_SCHEMA_VERSION
            )
        if self.role == "student_attempt":
            _fail("CropAnnotation v2 cannot promote student_attempt evidence")
        self.semantic_purity.validate(
            expected_item_id=self.item_id,
            expected_side=self.side,
        )
        if self.semantic_purity.reviewer_kind != self.selection_method:
            _fail(
                "CropAnnotation.selection_method must match semantic reviewer_kind"
            )
        if self.semantic_purity.reviewer != self.reviewer:
            _fail("CropAnnotation.reviewer must match semantic reviewer")
        if self.semantic_purity.required_context_ids and self.side != "prompt":
            _fail("CropAnnotation required contexts are prompt-side only")
        return self

    @property
    def annotation_sha256(self):
        return canonical_sha256(self.to_dict())

    def to_dict(self):
        payload = {
            "schema_version": self.schema_version,
            "item_id": self.item_id,
            "side": self.side,
            "role": self.role,
            "content_scope": self.content_scope,
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "source_page": self.source_page,
            "preview_sha256": self.preview_sha256,
            "preview_width": self.preview_width,
            "preview_height": self.preview_height,
            "bbox_preview_pixels": list(self.bbox_preview_pixels),
            "selection_method": self.selection_method,
            "reviewer": self.reviewer,
        }
        if self.schema_version >= SCHEMA_VERSION:
            payload["semantic_purity"] = self.semantic_purity.to_dict()
        return payload


def annotation_bbox_pdf_points(annotation, page_box_pdf_points):
    """Map a preview-bound annotation into the exact PDF page coordinate box."""

    if not isinstance(annotation, CropAnnotation):
        annotation = CropAnnotation.from_dict(annotation)
    page_box = _bbox(page_box_pdf_points, "page_box_pdf_points")
    x0, y0, x1, y1 = annotation.bbox_preview_pixels
    width = float(annotation.preview_width)
    height = float(annotation.preview_height)
    page_width = page_box[2] - page_box[0]
    page_height = page_box[3] - page_box[1]
    result = [
        page_box[0] + (x0 / width) * page_width,
        page_box[1] + (y0 / height) * page_height,
        page_box[0] + (x1 / width) * page_width,
        page_box[1] + (y1 / height) * page_height,
    ]
    _validate_bbox_within(result, page_box, "annotation PDF bbox")
    return result


def crop_spec_payload(
    *,
    item_id,
    chapter_id,
    side,
    role,
    content_scope,
    isolation,
    source_id,
    source_file,
    source_sha256,
    source_page,
    page_box_pdf_points,
    bbox_pdf_points,
    selection_method,
    selection_evidence_sha256,
    renderer_id,
    renderer_version,
    renderer_config_sha256,
    semantic_purity=None,
    crop_variant=None,
    composition=None,
    schema_version=SCHEMA_VERSION,
):
    """Return the normalized immutable crop spec (output-independent)."""

    if schema_version not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        _fail("crop spec schema_version is unsupported")
    if schema_version == LEGACY_SCHEMA_VERSION:
        _nonempty(item_id, "crop spec item_id")
    else:
        _stable_item_id(item_id, "crop spec item_id")
    _portable_id(chapter_id, "crop spec chapter_id")
    _validate_side_role_scope(side, role, content_scope, "crop spec")
    _enum(isolation, CROP_ISOLATIONS, "crop spec isolation")
    source_file = _path(source_file, "crop spec source_file")
    if source_id != make_source_id(source_file):
        _fail("crop spec source_id does not match source_file")
    _sha(source_sha256, "crop spec source_sha256")
    _positive_integer(source_page, "crop spec source_page")
    page_box = _bbox(page_box_pdf_points, "crop spec page_box_pdf_points")
    bbox = _bbox(bbox_pdf_points, "crop spec bbox_pdf_points")
    _validate_bbox_within(bbox, page_box, "crop spec bbox_pdf_points")
    _enum(selection_method, CROP_SELECTION_METHODS, "crop spec selection_method")
    _sha(selection_evidence_sha256, "crop spec selection_evidence_sha256")
    _portable_id(renderer_id, "crop spec renderer_id")
    _nonempty(renderer_version, "crop spec renderer_version")
    _sha(renderer_config_sha256, "crop spec renderer_config_sha256")
    if schema_version == LEGACY_SCHEMA_VERSION:
        if semantic_purity is not None:
            _fail("historical crop spec v1 cannot carry semantic_purity")
        if crop_variant is not None or composition is not None:
            _fail("historical crop spec v1 cannot carry a composite variant")
    else:
        if semantic_purity is None:
            _fail("current crop spec requires semantic_purity")
        if role == "student_attempt":
            _fail("current crop receipt cannot promote student_attempt evidence")
        if not isinstance(semantic_purity, SemanticPurityReview):
            semantic_purity = SemanticPurityReview.from_dict(semantic_purity)
        semantic_purity.validate(
            expected_item_id=item_id,
            expected_side=side,
        )
        if semantic_purity.required_context_ids and side != "prompt":
            _fail("required_context_ids are supported only for prompt crops")
        expected_isolation = (
            "target_with_required_context"
            if semantic_purity.required_context_ids
            else "target_item_only"
        )
        if isolation != expected_isolation:
            _fail(
                "crop spec isolation must be %s for its semantic context set"
                % expected_isolation
            )
        if (crop_variant is None) != (composition is None):
            _fail("crop_variant and composition must appear together")
        if crop_variant is not None:
            if crop_variant not in COMPOSITE_CROP_VARIANTS:
                _fail("unsupported current crop variant")
            if side != "prompt" or role not in PROMPT_CROP_ROLES:
                _fail("composite crops are currently prompt-side only")
            composition = normalize_composite_crop_spec(
                composition,
                page_box,
                item_id,
                semantic_purity.required_context_ids,
            )
            expected_composition_schema = (
                1 if crop_variant == COMPOSITE_CROP_VARIANT else 2
            )
            if composition["schema_version"] != expected_composition_schema:
                _fail(
                    "crop_variant does not match the composite crop spec schema"
                )
            region_union = [
                min(row["bbox_pdf_points"][0] for row in composition["regions"]),
                min(row["bbox_pdf_points"][1] for row in composition["regions"]),
                max(row["bbox_pdf_points"][2] for row in composition["regions"]),
                max(row["bbox_pdf_points"][3] for row in composition["regions"]),
            ]
            if any(abs(left - right) > 1e-6
                   for left, right in zip(bbox, region_union)):
                _fail("composite crop bbox_pdf_points must equal the region union")
            if (semantic_purity.crop_sha256
                    != composition["reviewed_candidate_sha256"]):
                _fail("composite semantic crop hash must equal the candidate hash")
    payload = {
        "schema_version": schema_version,
        "item_id": item_id,
        "chapter_id": chapter_id,
        "side": side,
        "role": role,
        "content_scope": content_scope,
        "isolation": isolation,
        "source_id": source_id,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "source_page": source_page,
        "page_box_pdf_points": page_box,
        "bbox_pdf_points": bbox,
        "selection_method": selection_method,
        "selection_evidence_sha256": selection_evidence_sha256,
        "renderer_id": renderer_id,
        "renderer_version": renderer_version,
        "renderer_config_sha256": renderer_config_sha256,
    }
    if schema_version >= SCHEMA_VERSION:
        payload["semantic_purity"] = semantic_purity.to_dict()
        if crop_variant is not None:
            payload["crop_variant"] = crop_variant
            payload["composition"] = composition
    return payload


def make_crop_spec_sha256(**spec):
    return canonical_sha256(crop_spec_payload(**spec))


@dataclass(frozen=True)
class CropReceipt:
    """Canonical binding from one crop spec to one validated PNG revision."""

    schema_version: int
    crop_receipt_id: str
    crop_spec_sha256: str
    item_id: str
    chapter_id: str
    side: str
    role: str
    content_scope: str
    isolation: str
    source_id: str
    source_file: str
    source_sha256: str
    source_page: int
    page_box_pdf_points: tuple
    bbox_pdf_points: tuple
    selection_method: str
    selection_evidence_sha256: str
    renderer_id: str
    renderer_version: str
    renderer_config_sha256: str
    semantic_purity: object
    output_path: str
    output_sha256: str
    output_width: int
    output_height: int
    supersedes: tuple
    crop_variant: object = None
    composition: object = None

    FIELDS = (
        "schema_version",
        "crop_receipt_id",
        "crop_spec_sha256",
        "item_id",
        "chapter_id",
        "side",
        "role",
        "content_scope",
        "isolation",
        "source_id",
        "source_file",
        "source_sha256",
        "source_page",
        "page_box_pdf_points",
        "bbox_pdf_points",
        "selection_method",
        "selection_evidence_sha256",
        "renderer_id",
        "renderer_version",
        "renderer_config_sha256",
        "semantic_purity",
        "output_path",
        "output_sha256",
        "output_width",
        "output_height",
        "supersedes",
    )
    LEGACY_FIELDS = (
        "schema_version",
        "crop_receipt_id",
        "crop_spec_sha256",
        "item_id",
        "chapter_id",
        "side",
        "role",
        "content_scope",
        "isolation",
        "source_id",
        "source_file",
        "source_sha256",
        "source_page",
        "page_box_pdf_points",
        "bbox_pdf_points",
        "selection_method",
        "selection_evidence_sha256",
        "renderer_id",
        "renderer_version",
        "renderer_config_sha256",
        "output_path",
        "output_sha256",
        "output_width",
        "output_height",
        "supersedes",
    )
    COMPOSITE_FIELDS = FIELDS + ("crop_variant", "composition")

    def __post_init__(self):
        object.__setattr__(
            self,
            "page_box_pdf_points",
            tuple(_bbox(self.page_box_pdf_points, "CropReceipt.page_box_pdf_points")),
        )
        object.__setattr__(
            self,
            "bbox_pdf_points",
            tuple(_bbox(self.bbox_pdf_points, "CropReceipt.bbox_pdf_points")),
        )
        if not isinstance(self.supersedes, (list, tuple)):
            _fail("CropReceipt.supersedes must be a list")
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        if self.semantic_purity is not None and not isinstance(
                self.semantic_purity, SemanticPurityReview):
            object.__setattr__(
                self,
                "semantic_purity",
                SemanticPurityReview.from_dict(self.semantic_purity),
            )
        self.validate()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            _fail("CropReceipt must be an object")
        if value.get("schema_version") == LEGACY_SCHEMA_VERSION:
            _strict_mapping(value, cls.LEGACY_FIELDS, "CropReceipt")
            payload = {field: value[field] for field in cls.LEGACY_FIELDS}
            payload["semantic_purity"] = None
            payload["crop_variant"] = None
            payload["composition"] = None
            return cls(**payload)
        if set(value) == set(cls.FIELDS):
            payload = {field: value[field] for field in cls.FIELDS}
            payload["crop_variant"] = None
            payload["composition"] = None
            return cls(**payload)
        _strict_mapping(value, cls.COMPOSITE_FIELDS, "CropReceipt")
        return cls(**{field: value[field] for field in cls.COMPOSITE_FIELDS})

    @classmethod
    def create(cls, *, output_path, output_sha256, output_width, output_height,
               supersedes=(), **spec):
        normalized_spec = crop_spec_payload(**spec)
        if (normalized_spec.get("schema_version") == SCHEMA_VERSION
                and normalized_spec["semantic_purity"].get("schema_version")
                != SEMANTIC_PURITY_SCHEMA_VERSION):
            _fail(
                "new CropReceipt v2 requires SemanticPurityReview schema v%d"
                % SEMANTIC_PURITY_SCHEMA_VERSION
            )
        spec_sha256 = canonical_sha256(normalized_spec)
        payload = dict(normalized_spec)
        payload.update({
            "crop_spec_sha256": spec_sha256,
            "output_path": output_path,
            "output_sha256": output_sha256,
            "output_width": output_width,
            "output_height": output_height,
            "supersedes": list(supersedes),
        })
        receipt_id = "crop_%s" % canonical_sha256(payload)
        return cls(crop_receipt_id=receipt_id, **payload)

    def _spec(self):
        return crop_spec_payload(
            item_id=self.item_id,
            chapter_id=self.chapter_id,
            side=self.side,
            role=self.role,
            content_scope=self.content_scope,
            isolation=self.isolation,
            source_id=self.source_id,
            source_file=self.source_file,
            source_sha256=self.source_sha256,
            source_page=self.source_page,
            page_box_pdf_points=self.page_box_pdf_points,
            bbox_pdf_points=self.bbox_pdf_points,
            selection_method=self.selection_method,
            selection_evidence_sha256=self.selection_evidence_sha256,
            renderer_id=self.renderer_id,
            renderer_version=self.renderer_version,
            renderer_config_sha256=self.renderer_config_sha256,
            semantic_purity=self.semantic_purity,
            crop_variant=self.crop_variant,
            composition=self.composition,
            schema_version=self.schema_version,
        )

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version not in (
                LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
            _fail(
                "CropReceipt.schema_version must be %d (historical) or %d"
                % (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
            )
        spec = self._spec()
        expected_spec_sha256 = canonical_sha256(spec)
        _sha(self.crop_spec_sha256, "CropReceipt.crop_spec_sha256")
        if self.crop_spec_sha256 != expected_spec_sha256:
            _fail("CropReceipt.crop_spec_sha256 does not match its immutable spec")
        if not _CROP_ID_RE.fullmatch(self.crop_receipt_id or ""):
            _fail("CropReceipt.crop_receipt_id must be crop_<sha256>")
        output_path = _path(self.output_path, "CropReceipt.output_path")
        if not output_path.startswith("references/assets/") or not output_path.lower().endswith(".png"):
            _fail("CropReceipt.output_path must be a PNG under references/assets")
        digest_marker = "crop_%s" % self.crop_spec_sha256[:12]
        if digest_marker not in output_path:
            _fail("CropReceipt.output_path must contain the crop spec digest")
        _sha(self.output_sha256, "CropReceipt.output_sha256")
        _positive_integer(self.output_width, "CropReceipt.output_width")
        _positive_integer(self.output_height, "CropReceipt.output_height")
        supersedes = []
        for index, path in enumerate(self.supersedes):
            supersedes.append(_path(path, "CropReceipt.supersedes[%d]" % index))
        if supersedes != sorted(set(supersedes)):
            _fail("CropReceipt.supersedes must be unique and sorted")
        payload = dict(spec)
        payload.update({
            "crop_spec_sha256": self.crop_spec_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "supersedes": supersedes,
        })
        expected_receipt_id = "crop_%s" % canonical_sha256(payload)
        if self.crop_receipt_id != expected_receipt_id:
            _fail("CropReceipt.crop_receipt_id does not match receipt contents")
        if self.schema_version >= SCHEMA_VERSION:
            self.semantic_purity.validate(
                expected_item_id=self.item_id,
                expected_side=self.side,
                expected_crop_sha256=self.output_sha256,
            )
            if self.crop_variant is None:
                if self.composition is not None:
                    _fail("single-region CropReceipt cannot carry composition")
            else:
                if self.crop_variant not in COMPOSITE_CROP_VARIANTS:
                    _fail("CropReceipt crop_variant is unsupported")
                composition = spec["composition"]
                if (self.output_width != composition["output_width"]
                        or self.output_height != composition["output_height"]):
                    _fail("composite CropReceipt output dimensions drifted")
                if self.output_sha256 != composition["reviewed_candidate_sha256"]:
                    _fail("composite CropReceipt output hash differs from candidate")
                if list(self.supersedes) != [composition["target_asset_path"]]:
                    _fail("composite CropReceipt must supersede its bound parent target")
        return self

    def to_dict(self):
        payload = {
            "schema_version": self.schema_version,
            "crop_receipt_id": self.crop_receipt_id,
            "crop_spec_sha256": self.crop_spec_sha256,
            "item_id": self.item_id,
            "chapter_id": self.chapter_id,
            "side": self.side,
            "role": self.role,
            "content_scope": self.content_scope,
            "isolation": self.isolation,
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "source_page": self.source_page,
            "page_box_pdf_points": list(self.page_box_pdf_points),
            "bbox_pdf_points": list(self.bbox_pdf_points),
            "selection_method": self.selection_method,
            "selection_evidence_sha256": self.selection_evidence_sha256,
            "renderer_id": self.renderer_id,
            "renderer_version": self.renderer_version,
            "renderer_config_sha256": self.renderer_config_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "supersedes": list(self.supersedes),
        }
        if self.schema_version >= SCHEMA_VERSION:
            payload["semantic_purity"] = self.semantic_purity.to_dict()
            if self.crop_variant is not None:
                payload["crop_variant"] = self.crop_variant
                payload["composition"] = self._spec()["composition"]
        return payload


def render_crop_png(renderer, pdf_path, zero_based_page, bbox_pdf_points):
    """Render one clip or fail; intentionally never call ``render_page_png``."""

    clip_renderer = getattr(renderer, "render_page_clip_png", None)
    if not callable(clip_renderer):
        _fail("crop renderer is unavailable; whole-page fallback is forbidden")
    bbox = _bbox(bbox_pdf_points, "bbox_pdf_points")
    try:
        payload = clip_renderer(pdf_path, zero_based_page, bbox)
    except Exception as exc:
        raise CropContractError("crop rendering failed: %s" % exc) from exc
    if not payload:
        _fail("crop renderer returned no bytes; whole-page fallback is forbidden")
    try:
        width, height = png_dimensions(payload)
    except ImageValidationError as exc:
        raise CropContractError("crop renderer returned an invalid PNG: %s" % exc) from exc
    return bytes(payload), width, height


def compact_asset_from_receipt(receipt):
    """Return the loss-resistant asset declaration stored on content units."""

    if not isinstance(receipt, CropReceipt):
        receipt = CropReceipt.from_dict(receipt)
    payload = {
        "path": receipt.output_path,
        "role": receipt.role,
        "type": "crop_image",
        "sha256": receipt.output_sha256,
        "source_file": receipt.source_file,
        "source_sha256": receipt.source_sha256,
        "source_page": receipt.source_page,
        "source_bbox_pdf_points": list(receipt.bbox_pdf_points),
        "crop_receipt_id": receipt.crop_receipt_id,
        "crop_spec_sha256": receipt.crop_spec_sha256,
        "content_scope": receipt.content_scope,
        "isolation": receipt.isolation,
    }
    if receipt.schema_version >= SCHEMA_VERSION:
        payload["crop_receipt_schema_version"] = receipt.schema_version
        payload["semantic_purity_sha256"] = (
            receipt.semantic_purity.semantic_purity_sha256
        )
        if (receipt.semantic_purity.schema_version
                >= SEMANTIC_PURITY_SCHEMA_VERSION):
            payload["semantic_purity_schema_version"] = (
                receipt.semantic_purity.schema_version
            )
            payload["required_context_ids"] = list(
                receipt.semantic_purity.required_context_ids
            )
        payload["contains_full_prompt"] = (
            receipt.side == "prompt"
            and receipt.content_scope == "full_prompt"
        )
    return payload


def validate_crop_asset_declaration(asset):
    """Validate compact P1 fields, while leaving unreceipted legacy crops readable."""

    if not isinstance(asset, dict):
        _fail("crop asset declaration must be an object")
    strict = "crop_receipt_id" in asset
    if asset.get("type") != "crop_image":
        if strict or any(field in asset for field in STRICT_CROP_ASSET_FIELDS):
            _fail("crop receipt controls require type=crop_image")
        return False
    if not strict:
        # Legacy crop metadata is not promoted to the P1 protocol.  Validate any
        # location hints that are present, but do not invent a receipt.
        if "source_page" in asset:
            _positive_integer(asset["source_page"], "crop asset source_page")
        if "source_bbox_pdf_points" in asset:
            _bbox(asset["source_bbox_pdf_points"], "crop asset source_bbox_pdf_points")
        return False
    receipt_schema_version = asset.get(
        "crop_receipt_schema_version", LEGACY_SCHEMA_VERSION
    )
    if type(receipt_schema_version) is not int or receipt_schema_version not in (
            LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        _fail("crop asset crop_receipt_schema_version is unsupported")
    required = set(STRICT_CROP_ASSET_REQUIRED)
    if receipt_schema_version == LEGACY_SCHEMA_VERSION:
        required.discard("crop_receipt_schema_version")
        required.discard("semantic_purity_sha256")
        if "semantic_purity_sha256" in asset:
            _fail("historical crop asset cannot carry semantic_purity_sha256")
        if ("semantic_purity_schema_version" in asset
                or "required_context_ids" in asset):
            _fail("historical crop asset cannot carry semantic context controls")
    missing = sorted(required - set(asset))
    if missing:
        _fail("receipted crop asset is missing fields: %r" % missing)
    _path(asset["path"], "crop asset path")
    _sha(asset["sha256"], "crop asset sha256")
    _path(asset["source_file"], "crop asset source_file")
    _sha(asset["source_sha256"], "crop asset source_sha256")
    _positive_integer(asset["source_page"], "crop asset source_page")
    _bbox(asset["source_bbox_pdf_points"], "crop asset source_bbox_pdf_points")
    if not _CROP_ID_RE.fullmatch(asset["crop_receipt_id"] or ""):
        _fail("crop asset crop_receipt_id must be crop_<sha256>")
    _sha(asset["crop_spec_sha256"], "crop asset crop_spec_sha256")
    if receipt_schema_version >= SCHEMA_VERSION:
        if "contains_full_prompt" not in asset:
            _fail("current crop asset requires contains_full_prompt")
        if asset.get("crop_receipt_schema_version") != SCHEMA_VERSION:
            _fail("current crop asset must name the current receipt schema")
        _sha(
            asset.get("semantic_purity_sha256"),
            "crop asset semantic_purity_sha256",
        )
        semantic_schema = asset.get("semantic_purity_schema_version")
        context_ids = asset.get("required_context_ids")
        if (semantic_schema is None) != (context_ids is None):
            _fail(
                "crop asset semantic_purity_schema_version and "
                "required_context_ids must appear together"
            )
        if semantic_schema is not None:
            if (type(semantic_schema) is not int
                    or semantic_schema != SEMANTIC_PURITY_SCHEMA_VERSION):
                _fail("crop asset semantic_purity_schema_version must equal 2")
            if (not isinstance(context_ids, list)
                    or any(not isinstance(value, str) or not value
                           or value != value.strip() for value in context_ids)
                    or context_ids != sorted(set(context_ids))):
                _fail(
                    "crop asset required_context_ids must be a sorted unique string array"
                )
            for index, value in enumerate(context_ids):
                _stable_item_id(
                    value,
                    "crop asset required_context_ids[%d]" % index,
                )
    digest_marker = "crop_%s" % asset["crop_spec_sha256"][:12]
    if digest_marker not in asset["path"]:
        _fail("crop asset path must contain the crop spec digest")
    side = "prompt" if asset["role"] in PROMPT_CROP_ROLES else "answer"
    _validate_side_role_scope(side, asset["role"], asset["content_scope"], "crop asset")
    _enum(asset["isolation"], CROP_ISOLATIONS, "crop asset isolation")
    context_ids = asset.get("required_context_ids")
    if asset["isolation"] == "target_with_required_context":
        if side != "prompt":
            _fail("target_with_required_context is prompt-side only")
        if not context_ids:
            _fail(
                "target_with_required_context requires non-empty required_context_ids"
            )
    elif context_ids:
        _fail("target_item_only cannot carry required_context_ids")
    contains_full_prompt = asset.get("contains_full_prompt")
    if contains_full_prompt is not None:
        if type(contains_full_prompt) is not bool:
            _fail("crop asset contains_full_prompt must be a boolean")
        if contains_full_prompt != (asset["content_scope"] == "full_prompt"):
            _fail("crop asset contains_full_prompt conflicts with content_scope")
    return True


def validate_crop_asset_binding(asset, receipt):
    """Prove that a compact declaration is an exact view of its full receipt."""

    if not isinstance(receipt, CropReceipt):
        receipt = CropReceipt.from_dict(receipt)
    validate_crop_asset_declaration(asset)
    expected = compact_asset_from_receipt(receipt)
    for field in ("semantic_purity_schema_version", "required_context_ids"):
        if (field in asset) != (field in expected):
            _fail("crop asset %s presence does not match its receipt" % field)
    for field, value in expected.items():
        if asset.get(field) != value:
            _fail("crop asset %s does not match its receipt" % field)
    return True


def _stat_signature(value):
    """Return the fields that must stay fixed across an authoritative read."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        getattr(value, "st_mtime_ns", int(value.st_mtime * 1000000000)),
        getattr(value, "st_ctime_ns", int(value.st_ctime * 1000000000)),
    )


def _stable_file_bytes(path, label, max_bytes=None):
    """Read one regular file and reject replacement or mutation during the read."""

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CropContractError("%s cannot be inspected: %s" % (label, exc)) from exc
    if not stat.S_ISREG(before.st_mode):
        _fail("%s must be a regular file" % label)
    if max_bytes is not None and before.st_size > max_bytes:
        _fail("%s exceeds the %d-byte safety limit" % (label, max_bytes))
    try:
        with open(path, "rb") as stream:
            payload = stream.read()
        after = os.lstat(path)
    except OSError as exc:
        raise CropContractError("%s cannot be read: %s" % (label, exc)) from exc
    if _stat_signature(before) != _stat_signature(after) or len(payload) != after.st_size:
        _fail("%s changed while it was read" % label)
    return payload


def _stable_file_sha256(path, label):
    """Hash one regular file and reject replacement or mutation during hashing."""

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise CropContractError("%s cannot be inspected: %s" % (label, exc)) from exc
    if not stat.S_ISREG(before.st_mode):
        _fail("%s must be a regular file" % label)
    digest = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                total += len(block)
        after = os.lstat(path)
    except OSError as exc:
        raise CropContractError("%s cannot be hashed: %s" % (label, exc)) from exc
    if _stat_signature(before) != _stat_signature(after) or total != after.st_size:
        _fail("%s changed while it was hashed" % label)
    return digest.hexdigest()


def crop_receipt_index(parse_report, require_index_sha256=True):
    """Validate and index the full crop receipts from one strict parse report.

    The compact declaration carried by a question is intentionally insufficient
    on its own.  A caller must resolve its receipt ID through this full index,
    whose canonical digest is emitted by the builder.  Empty legacy reports stay
    readable; a non-empty current receipt list without its index digest fails
    closed by default.
    """

    if not isinstance(parse_report, dict):
        _fail("parse report must be an object")
    rows = parse_report.get("crop_receipts")
    if rows is None:
        if "crop_receipt_index_sha256" in parse_report:
            _fail("parse report has a crop receipt digest but no receipt list")
        return {}
    if not isinstance(rows, list):
        _fail("parse report crop_receipts must be an array")
    by_id = {}
    output_owners = {}
    normalized = []
    for index, row in enumerate(rows):
        try:
            receipt = CropReceipt.from_dict(row)
        except (CropContractError, TypeError, ValueError) as exc:
            raise CropContractError(
                "parse report crop_receipts[%d] is invalid: %s" % (index, exc)
            ) from exc
        if receipt.crop_receipt_id in by_id:
            _fail("parse report repeats crop receipt %s" % receipt.crop_receipt_id)
        if receipt.output_path in output_owners:
            _fail(
                "parse report assigns multiple crop receipts to %s"
                % receipt.output_path
            )
        by_id[receipt.crop_receipt_id] = receipt
        output_owners[receipt.output_path] = receipt.crop_receipt_id
        normalized.append(receipt.to_dict())
    ordered_ids = [row["crop_receipt_id"] for row in normalized]
    if ordered_ids != sorted(ordered_ids):
        _fail("parse report crop_receipts must be sorted by crop_receipt_id")
    actual_index_sha256 = canonical_sha256(normalized)
    declared_index_sha256 = parse_report.get("crop_receipt_index_sha256")
    if declared_index_sha256 is None:
        if require_index_sha256 and normalized:
            _fail("parse report crop_receipt_index_sha256 is missing")
    else:
        _sha(declared_index_sha256, "parse report crop_receipt_index_sha256")
        if declared_index_sha256 != actual_index_sha256:
            _fail("parse report crop receipt index digest does not match its receipts")
    return by_id


def load_crop_receipt_report(workspace, require_index_sha256=True):
    """Strictly load the live parse report and its canonical receipt index."""

    try:
        path = safe_workspace_entry(workspace, ".ingest/parse_report.json")
    except ValueError as exc:
        raise CropContractError("parse report path is unsafe: %s" % exc) from exc
    payload = _stable_file_bytes(
        path, ".ingest/parse_report.json", _MAX_RECEIPT_REPORT_BYTES
    )
    try:
        document = strict_json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise CropContractError(
            ".ingest/parse_report.json is not strict UTF-8 JSON: %s" % exc
        ) from exc
    index = crop_receipt_index(
        document, require_index_sha256=require_index_sha256
    )
    return document, index


def verify_crop_asset_live_binding(
        workspace, source_root, asset, receipt_index=None,
        expected_item_id=None, expected_chapter_id=None,
        require_current_semantic=True):
    """Verify compact asset + full receipt + live output/source revisions.

    This is the authoring/explanation boundary.  It proves that the PNG bytes
    and dimensions still match the full receipt, that the source PDF revision
    still matches that receipt, and that the receipt's immutable crop spec is
    the one named by the compact asset.  It deliberately does not infer a
    materials root; the host must pass its confirmed source root explicitly.
    """

    validate_crop_asset_declaration(asset)
    if "crop_receipt_id" not in asset:
        _fail("live crop verification requires a receipted crop asset")
    if receipt_index is None:
        _unused_report, receipt_index = load_crop_receipt_report(workspace)
    if not isinstance(receipt_index, dict):
        _fail("crop receipt index must be an object")
    receipt = receipt_index.get(asset["crop_receipt_id"])
    if receipt is None:
        _fail(
            "crop asset %s has no matching full parse-report receipt"
            % asset["crop_receipt_id"]
        )
    if not isinstance(receipt, CropReceipt):
        receipt = CropReceipt.from_dict(receipt)
    if require_current_semantic and receipt.schema_version != SCHEMA_VERSION:
        _fail(
            "historical crop receipt v%d is read-only and cannot satisfy current "
            "Study Guide authoring" % receipt.schema_version
        )
    if require_current_semantic:
        if (receipt.semantic_purity.schema_version
                != SEMANTIC_PURITY_SCHEMA_VERSION):
            _fail(
                "historical semantic-purity review v%d is read-only and cannot "
                "satisfy current Study Guide authoring"
                % receipt.semantic_purity.schema_version
            )
        receipt.semantic_purity.validate(
            expected_item_id=expected_item_id or receipt.item_id,
            expected_side=receipt.side,
            expected_crop_sha256=receipt.output_sha256,
        )
    validate_crop_asset_binding(asset, receipt)
    if expected_item_id is not None and receipt.item_id != str(expected_item_id):
        _fail("crop receipt item_id does not match the requested item")
    if expected_chapter_id is not None:
        _portable_id(expected_chapter_id, "expected crop chapter_id")
        if receipt.chapter_id != expected_chapter_id:
            _fail("crop receipt chapter_id does not match the requested chapter")

    try:
        output_path = safe_workspace_entry(workspace, receipt.output_path)
    except ValueError as exc:
        raise CropContractError("crop output path is unsafe: %s" % exc) from exc
    output = _stable_file_bytes(output_path, "crop output %s" % receipt.output_path)
    output_sha256 = hashlib.sha256(output).hexdigest()
    if output_sha256 != receipt.output_sha256:
        _fail("live crop output hash does not match its receipt")
    try:
        output_width, output_height = png_dimensions(output)
    except ImageValidationError as exc:
        raise CropContractError("live crop output is not a valid PNG: %s" % exc) from exc
    if (output_width != receipt.output_width
            or output_height != receipt.output_height):
        _fail("live crop output dimensions do not match its receipt")

    try:
        source_path = safe_workspace_entry(source_root, receipt.source_file)
    except ValueError as exc:
        raise CropContractError("crop source path is unsafe: %s" % exc) from exc
    source_sha256 = _stable_file_sha256(
        source_path, "crop source %s" % receipt.source_file
    )
    if source_sha256 != receipt.source_sha256:
        _fail("live crop source revision does not match its receipt")
    return receipt
