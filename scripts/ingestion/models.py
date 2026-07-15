"""Strict, versioned data contracts for the lightweight ingestion core."""

import copy
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .identifiers import (
    file_sha256,
    make_issue_id,
    make_patch_id,
    make_source_id,
    make_unit_id,
    normalize_workspace_path,
    safe_workspace_entry,
    validate_sha256,
    validate_stable_id,
)


SCHEMA_VERSION = 1

SOURCE_STATUSES = frozenset(
    (
        "discovered",
        "parsed",
        "review_required",
        "unsupported",
        "failed",
        "complete",
        "unrecoverable",
        "superseded",
    )
)
UNIT_KINDS = frozenset(
    (
        "title",
        "heading",
        "text",
        "list",
        "table",
        "formula",
        "figure",
        "diagram",
        "caption",
        "code",
        "speaker_notes",
        "question",
        "answer",
        "other",
        "page_anchor",
    )
)
PROVENANCE_VALUES = frozenset(("material", "ai_recovered", "ai_supplemented"))
EXTRACTION_METHODS = frozenset(
    ("native", "heuristic", "ocr", "vision", "manual", "ai_recovered")
)
ISSUE_SEVERITIES = frozenset(("blocking", "warning", "info"))
ASSET_ROLES = frozenset(
    (
        "question_context",
        "answer_context",
        "worked_solution",
        "figure",
        "diagram",
        "table",
        "source_page",
        "other",
    )
)
ISSUE_STATUSES = frozenset(
    (
        "pending",
        "claimed",
        "validated",
        "applied",
        "blocked",
        "resolved",
        "unrecoverable",
        "superseded",
    )
)
PATCH_STATUSES = frozenset(("proposed", "validated", "applied", "rejected"))
PATCH_OPERATIONS = frozenset(
    (
        "add_unit", "replace_unit", "assign_chapter", "pair_qa", "classify_asset",
        "mark_resolved", "mark_unrecoverable",
    )
)
QUIZ_TYPES = frozenset(("choice", "subjective", "diagram", "fill_blank", "true_false", "code"))
QUIZ_SOURCES = frozenset(("teacher", "material", "ai_generated", "mixed", "unknown"))
QUIZ_SOURCE_TYPES = frozenset(
    ("homework", "lecture_quiz", "example", "practice_exam", "exam", "other")
)
CONTENT_METADATA_FIELDS = frozenset(
    (
        "quiz_type",
        "options",
        "keywords",
        "knowledge_point",
        "knowledge_points",
        "source_type",
        "source",
        "source_pages",
        "answer_source_file",
        "answer_source_pages",
        "assets",
        "requires_assets",
        "maybe_requires_assets",
        "answer_value",
        "asset_sha256",
        "source_language",
        "parser_metadata",
    )
)

_REASON_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")


class SchemaValidationError(ValueError):
    """A persisted ingestion object violated its exact schema or invariants."""


def _fail(message):
    raise SchemaValidationError(message)


def _strict_mapping(data, fields, label):
    if not isinstance(data, dict):
        _fail("%s must be an object" % label)
    expected = set(fields)
    actual = set(data)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (label, missing, unknown))


def _schema_version(value, label):
    if type(value) is not int or value != SCHEMA_VERSION:
        _fail("%s.schema_version must be %d" % (label, SCHEMA_VERSION))


def _nonempty(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    return value


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("%s must be an integer >= %d" % (label, minimum))
    return value


def _enum(value, allowed, label):
    if value not in allowed:
        _fail("%s must be one of %s" % (label, ", ".join(sorted(allowed))))
    return value


def _stable_id(value, prefix, label):
    try:
        return validate_stable_id(value, prefix, label)
    except ValueError as exc:
        raise SchemaValidationError(str(exc)) from exc


def _sha(value, label):
    try:
        return validate_sha256(value, label)
    except ValueError as exc:
        raise SchemaValidationError(str(exc)) from exc


def _path(value, label):
    try:
        canonical = normalize_workspace_path(value)
    except ValueError as exc:
        raise SchemaValidationError("%s: %s" % (label, exc)) from exc
    if canonical != value:
        _fail("%s must use canonical POSIX separators: %r" % (label, canonical))
    return canonical


def _json_metadata(value):
    """Return an isolated strict-JSON metadata object.

    Content-unit metadata is deliberately small and allow-listed.  This keeps
    patches deterministic while preserving typed quiz answers, all prompt/answer
    assets, and option structure instead of flattening them into display text.
    """

    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail("ContentUnit.metadata must be an object")
    unknown = sorted(set(value) - CONTENT_METADATA_FIELDS)
    if unknown:
        _fail("ContentUnit.metadata contains unknown fields: %r" % unknown)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        copied = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        _fail("ContentUnit.metadata must contain strict JSON values: %s" % exc)
    return copied


def render_answer_value(value):
    """Render a strict-JSON answer deterministically without losing its type."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(", ", ": "),
        )
    except (TypeError, ValueError) as exc:
        _fail("answer_value must be strict JSON: %s" % exc)


@dataclass(frozen=True)
class EvidenceRef:
    """A content-addressed file used as review evidence."""

    path: str
    sha256: str

    FIELDS = ("path", "sha256")

    def __post_init__(self):
        self.validate()

    @classmethod
    def from_file(cls, workspace, path):
        canonical = normalize_workspace_path(path)
        absolute = safe_workspace_entry(workspace, canonical)
        if not absolute.is_file():
            _fail("evidence is not a regular file: %s" % canonical)
        return cls(path=canonical, sha256=file_sha256(absolute))

    @classmethod
    def from_dict(cls, data):
        _strict_mapping(data, cls.FIELDS, "EvidenceRef")
        return cls(path=data["path"], sha256=data["sha256"])

    def validate(self):
        _path(self.path, "EvidenceRef.path")
        _sha(self.sha256, "EvidenceRef.sha256")
        return self

    def to_dict(self):
        return {"path": self.path, "sha256": self.sha256}


def _evidence_tuple(values, label):
    if not isinstance(values, (list, tuple)):
        _fail("%s must be a list" % label)
    refs = []
    for value in values:
        refs.append(value if isinstance(value, EvidenceRef) else EvidenceRef.from_dict(value))
    refs.sort(key=lambda ref: (ref.path, ref.sha256))
    if len({(ref.path, ref.sha256) for ref in refs}) != len(refs):
        _fail("%s contains duplicate evidence" % label)
    return tuple(refs)


@dataclass(frozen=True)
class SourceRecord:
    schema_version: int
    source_id: str
    path: str
    sha256: str
    size_bytes: int
    media_type: str
    status: str

    FIELDS = ("schema_version", "source_id", "path", "sha256", "size_bytes", "media_type", "status")

    def __post_init__(self):
        self.validate()

    @classmethod
    def create(cls, path, sha256, size_bytes, media_type, status="discovered"):
        canonical = normalize_workspace_path(path)
        return cls(
            schema_version=SCHEMA_VERSION,
            source_id=make_source_id(canonical),
            path=canonical,
            sha256=sha256,
            size_bytes=size_bytes,
            media_type=media_type,
            status=status,
        )

    @classmethod
    def from_file(cls, workspace, path, media_type, status="discovered"):
        canonical = normalize_workspace_path(path)
        absolute = safe_workspace_entry(workspace, canonical)
        if not absolute.is_file():
            _fail("source is not a regular file: %s" % canonical)
        return cls.create(canonical, file_sha256(absolute), absolute.stat().st_size, media_type, status)

    @classmethod
    def from_dict(cls, data):
        _strict_mapping(data, cls.FIELDS, "SourceRecord")
        return cls(**{field: data[field] for field in cls.FIELDS})

    def validate(self):
        _schema_version(self.schema_version, "SourceRecord")
        _stable_id(self.source_id, "src", "SourceRecord.source_id")
        _path(self.path, "SourceRecord.path")
        _sha(self.sha256, "SourceRecord.sha256")
        _integer(self.size_bytes, "SourceRecord.size_bytes")
        _nonempty(self.media_type, "SourceRecord.media_type")
        if self.media_type.lower() != self.media_type or "/" not in self.media_type:
            _fail("SourceRecord.media_type must be a lowercase MIME type")
        _enum(self.status, SOURCE_STATUSES, "SourceRecord.status")
        if self.source_id != make_source_id(self.path):
            _fail("SourceRecord.source_id does not match its canonical path")
        return self

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


def _bbox_tuple(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        _fail("ContentUnit.bbox must be null or four numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            _fail("ContentUnit.bbox must contain finite numbers")
        result.append(float(item))
    if min(result) < 0 or result[2] < result[0] or result[3] < result[1]:
        _fail("ContentUnit.bbox must be non-negative [x1,y1,x2,y2]")
    return tuple(result)


@dataclass(frozen=True)
class ContentUnit:
    schema_version: int
    unit_id: str
    source_id: str
    source_sha256: str
    source_file: str
    external_id: object
    kind: str
    text: str
    html: object
    latex: object
    page: int
    ordinal: int
    bbox: object
    parent_unit_id: object
    section_path: tuple
    chapter_id: object
    phase_id: object
    asset_path: object
    asset_role: object
    paired_unit_id: object
    metadata: dict
    method: str
    confidence: float
    provenance: str

    FIELDS = (
        "schema_version",
        "unit_id",
        "source_id",
        "source_sha256",
        "source_file",
        "external_id",
        "kind",
        "text",
        "html",
        "latex",
        "page",
        "ordinal",
        "bbox",
        "parent_unit_id",
        "section_path",
        "chapter_id",
        "phase_id",
        "asset_path",
        "asset_role",
        "paired_unit_id",
        "metadata",
        "method",
        "confidence",
        "provenance",
    )

    def __post_init__(self):
        object.__setattr__(self, "bbox", _bbox_tuple(self.bbox))
        object.__setattr__(self, "section_path", tuple(self.section_path))
        object.__setattr__(self, "metadata", _json_metadata(self.metadata))
        self.validate()

    @classmethod
    def create(
        cls,
        source_id,
        source_sha256,
        source_file,
        kind,
        text,
        page,
        ordinal=0,
        external_id=None,
        bbox=None,
        html=None,
        latex=None,
        parent_unit_id=None,
        section_path=(),
        chapter_id=None,
        phase_id=None,
        asset_path=None,
        asset_role=None,
        paired_unit_id=None,
        metadata=None,
        method="native",
        confidence=1.0,
        provenance="material",
    ):
        canonical_asset = normalize_workspace_path(asset_path) if asset_path is not None else None
        canonical_bbox = _bbox_tuple(bbox)
        return cls(
            schema_version=SCHEMA_VERSION,
            unit_id=make_unit_id(source_id, page, canonical_bbox, kind, ordinal),
            source_id=source_id,
            source_sha256=source_sha256,
            source_file=normalize_workspace_path(source_file),
            external_id=external_id,
            kind=kind,
            text=text,
            html=html,
            latex=latex,
            page=page,
            ordinal=ordinal,
            bbox=canonical_bbox,
            parent_unit_id=parent_unit_id,
            section_path=tuple(section_path),
            chapter_id=chapter_id,
            phase_id=phase_id,
            asset_path=canonical_asset,
            asset_role=asset_role,
            paired_unit_id=paired_unit_id,
            metadata=_json_metadata(metadata),
            method=method,
            confidence=confidence,
            provenance=provenance,
        )

    @classmethod
    def from_dict(cls, data):
        # v4.1 workspaces persisted schema_version=1 before typed quiz metadata
        # was added.  Accept exactly that historical shape and migrate it to an
        # empty metadata object; unknown fields and every other omission remain
        # fail-closed.
        if isinstance(data, dict) and set(data) == set(cls.FIELDS) - {"metadata"}:
            data = dict(data)
            data["metadata"] = {}
        _strict_mapping(data, cls.FIELDS, "ContentUnit")
        return cls(**{field: data[field] for field in cls.FIELDS})

    def validate(self):
        _schema_version(self.schema_version, "ContentUnit")
        _stable_id(self.unit_id, "unit", "ContentUnit.unit_id")
        _stable_id(self.source_id, "src", "ContentUnit.source_id")
        _sha(self.source_sha256, "ContentUnit.source_sha256")
        _path(self.source_file, "ContentUnit.source_file")
        if self.external_id is not None:
            _nonempty(self.external_id, "ContentUnit.external_id")
            if any(char in self.external_id for char in ("\x00", "\n", "\r")):
                _fail("ContentUnit.external_id must be a single-line identifier")
        _enum(self.kind, UNIT_KINDS, "ContentUnit.kind")
        if not isinstance(self.text, str):
            _fail("ContentUnit.text must be a string")
        for value, label in ((self.html, "html"), (self.latex, "latex")):
            if value is not None and not isinstance(value, str):
                _fail("ContentUnit.%s must be null or a string" % label)
        _integer(self.page, "ContentUnit.page", 1)
        _integer(self.ordinal, "ContentUnit.ordinal")
        _bbox_tuple(self.bbox)
        if self.parent_unit_id is not None:
            _stable_id(self.parent_unit_id, "unit", "ContentUnit.parent_unit_id")
            if self.parent_unit_id == self.unit_id:
                _fail("ContentUnit cannot be its own parent")
        if not isinstance(self.section_path, tuple):
            _fail("ContentUnit.section_path must be a list")
        for item in self.section_path:
            _nonempty(item, "ContentUnit.section_path[]")
        for value, label in ((self.chapter_id, "chapter_id"), (self.phase_id, "phase_id")):
            if value is not None:
                _nonempty(value, "ContentUnit.%s" % label)
                if not _REASON_RE.fullmatch(value):
                    _fail("ContentUnit.%s must be a lowercase portable identifier" % label)
        if self.asset_path is not None:
            _path(self.asset_path, "ContentUnit.asset_path")
        if self.asset_role is not None:
            _enum(self.asset_role, ASSET_ROLES, "ContentUnit.asset_role")
            if self.asset_path is None:
                _fail("ContentUnit.asset_role requires asset_path")
        if self.paired_unit_id is not None:
            _stable_id(self.paired_unit_id, "unit", "ContentUnit.paired_unit_id")
            if self.paired_unit_id == self.unit_id:
                _fail("ContentUnit cannot be paired with itself")
        metadata = _json_metadata(self.metadata)
        if metadata:
            non_quiz_fields = {"asset_sha256", "source_language", "parser_metadata"}
            if (self.kind not in ("question", "answer")
                    and set(metadata) - non_quiz_fields):
                _fail(
                    "non-question ContentUnit.metadata contains quiz-only fields: %r"
                    % sorted(set(metadata) - non_quiz_fields)
                )
            asset_sha256 = metadata.get("asset_sha256")
            if asset_sha256 is not None:
                _sha(asset_sha256, "ContentUnit.metadata.asset_sha256")
            parser_metadata = metadata.get("parser_metadata")
            if parser_metadata is not None and not isinstance(parser_metadata, dict):
                _fail("ContentUnit.metadata.parser_metadata must be an object")
            quiz_type = metadata.get("quiz_type")
            if quiz_type is not None:
                _enum(quiz_type, QUIZ_TYPES, "ContentUnit.metadata.quiz_type")
            options = metadata.get("options")
            if options is not None and (not isinstance(options, list) or not options):
                _fail("ContentUnit.metadata.options must be a non-empty list")
            if self.kind == "question" and quiz_type == "choice" and not options:
                _fail("choice question metadata requires options")
            keywords = metadata.get("keywords")
            if keywords is not None and (not isinstance(keywords, list) or not keywords
                                         or not all(isinstance(value, str) and value.strip()
                                                    for value in keywords)):
                _fail("ContentUnit.metadata.keywords must be non-empty strings")
            knowledge_point = metadata.get("knowledge_point")
            if knowledge_point is not None:
                _nonempty(knowledge_point, "ContentUnit.metadata.knowledge_point")
            knowledge_points = metadata.get("knowledge_points")
            if knowledge_points is not None and (
                    not isinstance(knowledge_points, list) or not knowledge_points
                    or not all(isinstance(value, str) and value.strip()
                               for value in knowledge_points)):
                _fail("ContentUnit.metadata.knowledge_points must be non-empty strings")
            source_type = metadata.get("source_type")
            if source_type is not None:
                _enum(source_type, QUIZ_SOURCE_TYPES, "ContentUnit.metadata.source_type")
            source = metadata.get("source")
            if source is not None:
                _enum(source, QUIZ_SOURCES, "ContentUnit.metadata.source")
            for field in ("source_pages", "answer_source_pages"):
                pages = metadata.get(field)
                if pages is not None:
                    if not isinstance(pages, list):
                        _fail("ContentUnit.metadata.%s must be a list" % field)
                    for page in pages:
                        _integer(page, "ContentUnit.metadata.%s[]" % field, 1)
            answer_source_file = metadata.get("answer_source_file")
            if answer_source_file is not None:
                _path(answer_source_file, "ContentUnit.metadata.answer_source_file")
            source_language = metadata.get("source_language")
            if source_language is not None:
                _enum(
                    source_language,
                    frozenset(("zh", "en")),
                    "ContentUnit.metadata.source_language",
                )
            for field in ("requires_assets", "maybe_requires_assets"):
                value = metadata.get(field)
                if value is not None and type(value) is not bool:
                    _fail("ContentUnit.metadata.%s must be a boolean" % field)
            assets = metadata.get("assets")
            if assets is not None:
                if not isinstance(assets, list):
                    _fail("ContentUnit.metadata.assets must be a list")
                for index, asset in enumerate(assets):
                    required = {"path", "role"}
                    optional = {"sha256", "source_sha256"}
                    if (not isinstance(asset, dict) or not required.issubset(asset)
                            or set(asset) - required - optional):
                        _fail(
                            "ContentUnit.metadata.assets[%d] needs path/role and optional hashes"
                            % index
                        )
                    _path(asset["path"], "ContentUnit.metadata.assets[%d].path" % index)
                    _enum(asset["role"], ASSET_ROLES,
                          "ContentUnit.metadata.assets[%d].role" % index)
                    for hash_field in optional:
                        if hash_field in asset:
                            _sha(
                                asset[hash_field],
                                "ContentUnit.metadata.assets[%d].%s" % (index, hash_field),
                            )
            if self.kind == "answer" and "answer_value" in metadata:
                render_answer_value(metadata["answer_value"])
                if quiz_type == "true_false":
                    value = metadata["answer_value"]
                    valid = type(value) is bool or (
                        isinstance(value, str)
                        and value.strip().lower()
                        in {"true", "false", "t", "f", "yes", "no", "真", "假", "对", "错", "是", "否"}
                    )
                    if not valid:
                        _fail("true_false answer metadata requires a boolean value")
        _enum(self.method, EXTRACTION_METHODS, "ContentUnit.method")
        if (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(float(self.confidence))
                or not 0.0 <= float(self.confidence) <= 1.0):
            _fail("ContentUnit.confidence must be a finite number between 0 and 1")
        _enum(self.provenance, PROVENANCE_VALUES, "ContentUnit.provenance")
        if (not self.text and self.asset_path is None and self.html is None and self.latex is None
                and self.kind != "page_anchor"):
            _fail("ContentUnit requires text or an asset_path")
        expected = make_unit_id(self.source_id, self.page, self.bbox, self.kind, self.ordinal)
        if self.unit_id != expected:
            _fail("ContentUnit.unit_id does not match source/location/kind/ordinal")
        return self

    def with_pair(self, paired_unit_id):
        return replace(self, paired_unit_id=paired_unit_id)

    def with_asset_role(self, asset_role):
        return replace(self, asset_role=asset_role)

    def with_chapter(self, chapter_id, phase_id):
        return replace(self, chapter_id=chapter_id, phase_id=phase_id)

    def to_dict(self):
        result = {field: getattr(self, field) for field in self.FIELDS}
        result["bbox"] = list(self.bbox) if self.bbox is not None else None
        result["section_path"] = list(self.section_path)
        result["metadata"] = copy.deepcopy(self.metadata)
        return result


@dataclass(frozen=True)
class ChapterPhaseMapping:
    schema_version: int
    unit_id: str
    source_id: str
    source_sha256: str
    chapter: str
    phase: str
    chapter_id: str
    phase_id: str

    FIELDS = (
        "schema_version", "unit_id", "source_id", "source_sha256",
        "chapter", "phase", "chapter_id", "phase_id",
    )

    def __post_init__(self):
        self.validate()

    @classmethod
    def create(cls, unit_id, source_id, source_sha256, chapter, phase, chapter_id, phase_id):
        return cls(
            SCHEMA_VERSION, unit_id, source_id, source_sha256,
            chapter, phase, chapter_id, phase_id,
        )

    @classmethod
    def from_dict(cls, data):
        _strict_mapping(data, cls.FIELDS, "ChapterPhaseMapping")
        return cls(**{field: data[field] for field in cls.FIELDS})

    def validate(self):
        _schema_version(self.schema_version, "ChapterPhaseMapping")
        _stable_id(self.unit_id, "unit", "ChapterPhaseMapping.unit_id")
        _stable_id(self.source_id, "src", "ChapterPhaseMapping.source_id")
        _sha(self.source_sha256, "ChapterPhaseMapping.source_sha256")
        _nonempty(self.chapter, "ChapterPhaseMapping.chapter")
        _nonempty(self.phase, "ChapterPhaseMapping.phase")
        for value, label in ((self.chapter_id, "chapter_id"), (self.phase_id, "phase_id")):
            _nonempty(value, "ChapterPhaseMapping.%s" % label)
            if not _REASON_RE.fullmatch(value):
                _fail("ChapterPhaseMapping.%s must be a lowercase portable identifier" % label)
        return self

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


@dataclass(frozen=True)
class ReviewIssue:
    schema_version: int
    issue_id: str
    source_id: str
    source_sha256: str
    reason_codes: tuple
    pages: tuple
    evidence: tuple
    target_unit_ids: tuple
    severity: str
    description: str
    suggested_action: str
    status: str

    FIELDS = (
        "schema_version",
        "issue_id",
        "source_id",
        "source_sha256",
        "reason_codes",
        "pages",
        "evidence",
        "target_unit_ids",
        "severity",
        "description",
        "suggested_action",
        "status",
    )

    def __post_init__(self):
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "evidence", _evidence_tuple(self.evidence, "ReviewIssue.evidence"))
        object.__setattr__(self, "target_unit_ids", tuple(self.target_unit_ids))
        self.validate()

    @classmethod
    def create(
        cls,
        source_id,
        source_sha256,
        reason_codes,
        evidence,
        description,
        suggested_action=None,
        pages=(),
        target_unit_ids=(),
        severity="warning",
        status="pending",
    ):
        reasons = tuple(sorted(set(reason_codes)))
        canonical_pages = tuple(sorted(set(pages)))
        refs = _evidence_tuple(evidence, "ReviewIssue.evidence")
        targets = tuple(sorted(set(target_unit_ids)))
        issue_id = make_issue_id(
            source_id,
            source_sha256,
            reasons,
            canonical_pages,
            [ref.to_dict() for ref in refs],
            targets,
        )
        return cls(
            SCHEMA_VERSION,
            issue_id,
            source_id,
            source_sha256,
            reasons,
            canonical_pages,
            refs,
            targets,
            severity,
            description,
            suggested_action or description,
            status,
        )

    @classmethod
    def from_dict(cls, data):
        _strict_mapping(data, cls.FIELDS, "ReviewIssue")
        return cls(**{field: data[field] for field in cls.FIELDS})

    def validate(self):
        _schema_version(self.schema_version, "ReviewIssue")
        _stable_id(self.issue_id, "issue", "ReviewIssue.issue_id")
        _stable_id(self.source_id, "src", "ReviewIssue.source_id")
        _sha(self.source_sha256, "ReviewIssue.source_sha256")
        if not self.reason_codes or tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            _fail("ReviewIssue.reason_codes must be non-empty, unique, and sorted")
        for reason in self.reason_codes:
            if not isinstance(reason, str) or not _REASON_RE.fullmatch(reason):
                _fail("ReviewIssue reason codes must be lowercase machine identifiers")
        if tuple(sorted(set(self.pages))) != self.pages:
            _fail("ReviewIssue.pages must be unique and sorted")
        for page in self.pages:
            _integer(page, "ReviewIssue.pages[]", 1)
        if not self.evidence:
            _fail("ReviewIssue.evidence must not be empty")
        if tuple(sorted(set(self.target_unit_ids))) != self.target_unit_ids:
            _fail("ReviewIssue.target_unit_ids must be unique and sorted")
        for unit_id in self.target_unit_ids:
            _stable_id(unit_id, "unit", "ReviewIssue.target_unit_ids[]")
        _enum(self.severity, ISSUE_SEVERITIES, "ReviewIssue.severity")
        _nonempty(self.description, "ReviewIssue.description")
        _nonempty(self.suggested_action, "ReviewIssue.suggested_action")
        _enum(self.status, ISSUE_STATUSES, "ReviewIssue.status")
        expected = make_issue_id(
            self.source_id,
            self.source_sha256,
            self.reason_codes,
            self.pages,
            [ref.to_dict() for ref in self.evidence],
            self.target_unit_ids,
        )
        if self.issue_id != expected:
            _fail("ReviewIssue.issue_id does not match its immutable evidence fields")
        return self

    def with_status(self, status):
        return replace(self, status=status)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "issue_id": self.issue_id,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "reason_codes": list(self.reason_codes),
            "pages": list(self.pages),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "target_unit_ids": list(self.target_unit_ids),
            "severity": self.severity,
            "description": self.description,
            "suggested_action": self.suggested_action,
            "status": self.status,
        }


def _operation_fields(name):
    return {
        "add_unit": ("op", "unit"),
        "replace_unit": ("op", "unit_id", "unit"),
        "assign_chapter": (
            "op", "unit_id", "chapter", "phase", "chapter_id", "phase_id",
        ),
        "pair_qa": ("op", "question_unit_id", "answer_unit_id"),
        "classify_asset": ("op", "unit_id", "asset_role"),
        "mark_resolved": ("op", "reason"),
        "mark_unrecoverable": ("op", "reason"),
    }[name]


def canonicalize_operation(operation):
    if not isinstance(operation, dict):
        _fail("ReviewPatch operations must be objects")
    name = operation.get("op")
    _enum(name, PATCH_OPERATIONS, "ReviewPatch.operations[].op")
    _strict_mapping(operation, _operation_fields(name), "ReviewPatch operation %s" % name)

    result = copy.deepcopy(operation)
    if name in ("add_unit", "replace_unit"):
        unit = operation["unit"] if isinstance(operation["unit"], ContentUnit) else ContentUnit.from_dict(operation["unit"])
        result["unit"] = unit.to_dict()
    if name == "replace_unit":
        _stable_id(operation["unit_id"], "unit", "replace_unit.unit_id")
        if result["unit"]["unit_id"] != operation["unit_id"]:
            _fail("replace_unit.unit must retain unit_id")
    elif name == "assign_chapter":
        _stable_id(operation["unit_id"], "unit", "assign_chapter.unit_id")
        _nonempty(operation["chapter"], "assign_chapter.chapter")
        _nonempty(operation["phase"], "assign_chapter.phase")
        for key in ("chapter_id", "phase_id"):
            _nonempty(operation[key], "assign_chapter.%s" % key)
            if not _REASON_RE.fullmatch(operation[key]):
                _fail("assign_chapter.%s must be a lowercase portable identifier" % key)
    elif name == "pair_qa":
        _stable_id(operation["question_unit_id"], "unit", "pair_qa.question_unit_id")
        _stable_id(operation["answer_unit_id"], "unit", "pair_qa.answer_unit_id")
        if operation["question_unit_id"] == operation["answer_unit_id"]:
            _fail("pair_qa requires two different units")
    elif name == "classify_asset":
        _stable_id(operation["unit_id"], "unit", "classify_asset.unit_id")
        _enum(operation["asset_role"], ASSET_ROLES, "classify_asset.asset_role")
    elif name in ("mark_resolved", "mark_unrecoverable"):
        _nonempty(operation["reason"], "%s.reason" % name)
    return result


@dataclass(frozen=True)
class ReviewPatch:
    schema_version: int
    patch_id: str
    issue_id: str
    source_id: str
    source_sha256: str
    operations: tuple
    evidence: tuple
    reviewer: str
    created_at: str
    status: str

    FIELDS = (
        "schema_version",
        "patch_id",
        "issue_id",
        "source_id",
        "source_sha256",
        "operations",
        "evidence",
        "reviewer",
        "created_at",
        "status",
    )

    def __post_init__(self):
        operations = tuple(canonicalize_operation(operation) for operation in self.operations)
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "evidence", _evidence_tuple(self.evidence, "ReviewPatch.evidence"))
        self.validate()

    @classmethod
    def create(
        cls,
        issue_id,
        source_id,
        source_sha256,
        operations,
        evidence,
        reviewer="ai",
        created_at=None,
        status="proposed",
    ):
        canonical_operations = tuple(canonicalize_operation(operation) for operation in operations)
        refs = _evidence_tuple(evidence, "ReviewPatch.evidence")
        patch_id = make_patch_id(
            issue_id,
            source_id,
            source_sha256,
            canonical_operations,
            [ref.to_dict() for ref in refs],
        )
        return cls(
            SCHEMA_VERSION,
            patch_id,
            issue_id,
            source_id,
            source_sha256,
            canonical_operations,
            refs,
            reviewer,
            created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            ),
            status,
        )

    @classmethod
    def from_dict(cls, data):
        _strict_mapping(data, cls.FIELDS, "ReviewPatch")
        return cls(**{field: data[field] for field in cls.FIELDS})

    def validate(self):
        _schema_version(self.schema_version, "ReviewPatch")
        _stable_id(self.patch_id, "patch", "ReviewPatch.patch_id")
        _stable_id(self.issue_id, "issue", "ReviewPatch.issue_id")
        _stable_id(self.source_id, "src", "ReviewPatch.source_id")
        _sha(self.source_sha256, "ReviewPatch.source_sha256")
        if not self.operations:
            _fail("ReviewPatch.operations must not be empty")
        mark_count = sum(
            1 for operation in self.operations
            if operation["op"] in ("mark_resolved", "mark_unrecoverable")
        )
        if mark_count and (mark_count != 1 or len(self.operations) != 1):
            _fail("mark_resolved/mark_unrecoverable must be the patch's only operation")
        if not self.evidence:
            _fail("ReviewPatch.evidence must not be empty")
        _nonempty(self.reviewer, "ReviewPatch.reviewer")
        _nonempty(self.created_at, "ReviewPatch.created_at")
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", self.created_at
        ):
            _fail("ReviewPatch.created_at must be an ISO-8601 UTC timestamp ending in Z")
        _enum(self.status, PATCH_STATUSES, "ReviewPatch.status")
        expected = make_patch_id(
            self.issue_id,
            self.source_id,
            self.source_sha256,
            self.operations,
            [ref.to_dict() for ref in self.evidence],
        )
        if self.patch_id != expected:
            _fail("ReviewPatch.patch_id does not match its immutable operations/evidence")
        return self

    def with_status(self, status):
        return replace(self, status=status)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "patch_id": self.patch_id,
            "issue_id": self.issue_id,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "operations": copy.deepcopy(list(self.operations)),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "reviewer": self.reviewer,
            "created_at": self.created_at,
            "status": self.status,
        }
