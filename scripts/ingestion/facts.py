"""Strict, revision-bound facts for duplicate folding and source conflicts.

The existing :mod:`ingestion.models` records deliberately keep ``unit_id``
stable across source revisions.  The facts in this module therefore bind every
unit reference to both the source digest and a digest of the complete compiled
unit.  They are small, deterministic, stdlib-only value objects; persistence
and review-ledger integration live elsewhere.
"""

import hashlib
import itertools
import re
from dataclasses import dataclass

from .identifiers import canonical_json, normalize_workspace_path, validate_sha256


SCHEMA_VERSION = 1
SCORE_SCALE = 1_000_000

MATCH_KINDS = frozenset(("exact", "near"))
GROUP_DERIVATIONS = frozenset(("exact_auto", "reviewed_near"))
KIND_FAMILIES = frozenset(
    ("concept", "formula", "question", "answer", "table", "code", "visual", "heading", "other")
)
SOURCE_SIDES = frozenset(("teaching", "prompt", "answer"))
PROVENANCE_CLASSES = frozenset(("source_backed", "ai_supplemented"))
PRIORITY_TIERS = frozenset(
    ("teacher_official", "course_official", "course_material", "student_notes", "unknown")
)
PRIORITY_BASES = frozenset(("user", "embedded_metadata", "review", "unspecified"))
CONFLICT_KINDS = frozenset(
    (
        "answer_mismatch",
        "boolean_mismatch",
        "numeric_mismatch",
        "formula_mismatch",
        "provenance_mismatch",
        "visual_context_mismatch",
        "textual_divergence",
    )
)
CONFLICT_STATUSES = frozenset(
    (
        "unresolved",
        "resolved_keep_both",
        "resolved_preferred",
        "resolved_not_conflict",
        "unrecoverable",
        "superseded",
    )
)
RESOLUTION_ACTIONS = frozenset(("keep_both", "prefer_source", "not_conflict", "unrecoverable"))

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_INGEST_ID_RE = re.compile(r"^(src|unit|issue|patch)_[0-9a-f]{64}$")
_FACT_ID_RE = re.compile(r"^(candidate|group|conflict|claim)_[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_CHAPTER_RE = re.compile(r"^ch(?=[0-9]{2,}$)0*[1-9][0-9]*$")


class FactValidationError(ValueError):
    """A duplicate/conflict fact violated its exact schema or invariants."""


def _fail(message):
    raise FactValidationError(message)


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


def _schema(value, label):
    if type(value) is not int or value != SCHEMA_VERSION:
        _fail("%s.schema_version must be %d" % (label, SCHEMA_VERSION))


def _text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    return value


def _token(value, label):
    value = _text(value, label)
    if not _TOKEN_RE.fullmatch(value):
        _fail("%s must be a lowercase portable identifier" % label)
    return value


def _enum(value, allowed, label):
    if value not in allowed:
        _fail("%s must be one of %s" % (label, ", ".join(sorted(allowed))))
    return value


def _sha(value, label):
    try:
        return validate_sha256(value, label)
    except ValueError as exc:
        raise FactValidationError(str(exc)) from exc


def _id(value, prefix, label):
    if not isinstance(value, str):
        _fail("%s must be an identifier" % label)
    pattern = _FACT_ID_RE if prefix in ("candidate", "group", "conflict", "claim") else _INGEST_ID_RE
    if not pattern.fullmatch(value) or not value.startswith(prefix + "_"):
        _fail("%s must use a valid %s_ identifier" % (label, prefix))
    return value


def _nullable_id(value, prefix, label):
    if value is None:
        return None
    return _id(value, prefix, label)


def _integer(value, label, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        suffix = "" if maximum is None else " and <= %d" % maximum
        _fail("%s must be an integer >= %d%s" % (label, minimum, suffix))
    return value


def _canonical_tokens(values, label, allow_empty=True):
    if not isinstance(values, (list, tuple)):
        _fail("%s must be an array" % label)
    result = tuple(sorted(_token(value, "%s[]" % label) for value in values))
    if len(set(result)) != len(result):
        _fail("%s must contain unique values" % label)
    if not allow_empty and not result:
        _fail("%s must not be empty" % label)
    return result


def stable_fact_id(prefix, payload):
    """Return a deterministic content ID for one immutable fact payload."""

    if prefix not in ("candidate", "group", "conflict", "claim"):
        raise ValueError("unsupported fact ID prefix: %s" % prefix)
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return "%s_%s" % (prefix, digest)


def unit_payload_sha256(unit):
    """Digest the full strict-JSON representation of a compiled content unit."""

    if not isinstance(unit, dict):
        to_dict = getattr(unit, "to_dict", None)
        if not callable(to_dict):
            raise FactValidationError("unit must be an object or expose to_dict()")
        unit = to_dict()
    try:
        encoded = canonical_json(unit).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FactValidationError("unit must contain strict JSON values: %s" % exc) from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class UnitRevisionRef:
    unit_id: str
    source_id: str
    source_sha256: str
    unit_sha256: str

    FIELDS = ("unit_id", "source_id", "source_sha256", "unit_sha256")

    def __post_init__(self):
        _id(self.unit_id, "unit", "UnitRevisionRef.unit_id")
        _id(self.source_id, "src", "UnitRevisionRef.source_id")
        _sha(self.source_sha256, "UnitRevisionRef.source_sha256")
        _sha(self.unit_sha256, "UnitRevisionRef.unit_sha256")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "UnitRevisionRef")
        return cls(**{field: value[field] for field in cls.FIELDS})

    @classmethod
    def from_unit(cls, value):
        unit = value.to_dict() if callable(getattr(value, "to_dict", None)) else value
        if not isinstance(unit, dict):
            _fail("UnitRevisionRef.from_unit requires a content-unit object")
        missing = [field for field in ("unit_id", "source_id", "source_sha256") if field not in unit]
        if missing:
            _fail("content unit is missing revision fields: %s" % ", ".join(missing))
        return cls(
            unit_id=unit["unit_id"],
            source_id=unit["source_id"],
            source_sha256=unit["source_sha256"],
            unit_sha256=unit_payload_sha256(unit),
        )

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


def _ref_sort_key(ref):
    return (ref.unit_id, ref.source_id, ref.source_sha256, ref.unit_sha256)


def _revision_refs(values, label, minimum=0):
    if not isinstance(values, (list, tuple)):
        _fail("%s must be an array" % label)
    refs = [value if isinstance(value, UnitRevisionRef) else UnitRevisionRef.from_dict(value) for value in values]
    refs.sort(key=_ref_sort_key)
    if len(refs) < minimum:
        _fail("%s must contain at least %d entries" % (label, minimum))
    if len({_ref_sort_key(ref) for ref in refs}) != len(refs):
        _fail("%s contains duplicate unit revisions" % label)
    if len({ref.unit_id for ref in refs}) != len(refs):
        _fail("%s cannot contain two revisions of the same unit_id" % label)
    return tuple(refs)


@dataclass(frozen=True)
class FactEvidenceRef:
    path: str
    sha256: str

    FIELDS = ("path", "sha256")

    def __post_init__(self):
        try:
            canonical = normalize_workspace_path(self.path)
        except ValueError as exc:
            raise FactValidationError("FactEvidenceRef.path: %s" % exc) from exc
        if canonical != self.path:
            _fail("FactEvidenceRef.path must use canonical POSIX separators")
        _sha(self.sha256, "FactEvidenceRef.sha256")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "FactEvidenceRef")
        return cls(path=value["path"], sha256=value["sha256"])

    def to_dict(self):
        return {"path": self.path, "sha256": self.sha256}


def _evidence_refs(values, label):
    if not isinstance(values, (list, tuple)):
        _fail("%s must be an array" % label)
    refs = [value if isinstance(value, FactEvidenceRef) else FactEvidenceRef.from_dict(value) for value in values]
    refs.sort(key=lambda ref: (ref.path, ref.sha256))
    if len({(ref.path, ref.sha256) for ref in refs}) != len(refs):
        _fail("%s contains duplicate evidence" % label)
    return tuple(refs)


@dataclass(frozen=True)
class CompatibilityKey:
    chapter_id: object
    kind_family: str
    source_side: str
    provenance_class: str

    FIELDS = ("chapter_id", "kind_family", "source_side", "provenance_class")

    def __post_init__(self):
        if self.chapter_id is not None:
            _text(self.chapter_id, "CompatibilityKey.chapter_id")
            if not _CHAPTER_RE.fullmatch(self.chapter_id):
                _fail("CompatibilityKey.chapter_id must be canonical chNN or null")
            if self.chapter_id != "ch%02d" % int(self.chapter_id[2:]):
                _fail("CompatibilityKey.chapter_id must not contain redundant leading zeroes")
        _enum(self.kind_family, KIND_FAMILIES, "CompatibilityKey.kind_family")
        _enum(self.source_side, SOURCE_SIDES, "CompatibilityKey.source_side")
        _enum(self.provenance_class, PROVENANCE_CLASSES, "CompatibilityKey.provenance_class")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "CompatibilityKey")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


@dataclass(frozen=True)
class DuplicateCandidate:
    schema_version: int
    candidate_id: str
    algorithm: str
    normalizer: str
    config_sha256: str
    compatibility_key: CompatibilityKey
    match_kind: str
    score_ppm: int
    threshold_ppm: int
    left: UnitRevisionRef
    right: UnitRevisionRef
    left_fingerprint: str
    right_fingerprint: str
    conflict_signals: tuple
    review_issue_id: object

    FIELDS = (
        "schema_version", "candidate_id", "algorithm", "normalizer", "config_sha256",
        "compatibility_key", "match_kind", "score_ppm", "threshold_ppm", "left", "right",
        "left_fingerprint", "right_fingerprint", "conflict_signals", "review_issue_id",
    )

    def __post_init__(self):
        object.__setattr__(
            self,
            "compatibility_key",
            self.compatibility_key
            if isinstance(self.compatibility_key, CompatibilityKey)
            else CompatibilityKey.from_dict(self.compatibility_key),
        )
        object.__setattr__(
            self, "left", self.left if isinstance(self.left, UnitRevisionRef) else UnitRevisionRef.from_dict(self.left)
        )
        object.__setattr__(
            self, "right", self.right if isinstance(self.right, UnitRevisionRef) else UnitRevisionRef.from_dict(self.right)
        )
        object.__setattr__(
            self, "conflict_signals", _canonical_tokens(self.conflict_signals, "DuplicateCandidate.conflict_signals")
        )
        if not set(self.conflict_signals).issubset(CONFLICT_KINDS):
            _fail("DuplicateCandidate.conflict_signals contains an unsupported signal")
        self.validate()

    @classmethod
    def create(
        cls,
        algorithm,
        normalizer,
        config_sha256,
        compatibility_key,
        match_kind,
        score_ppm,
        threshold_ppm,
        left,
        right,
        left_fingerprint,
        right_fingerprint,
        conflict_signals=(),
        review_issue_id=None,
    ):
        key = compatibility_key if isinstance(compatibility_key, CompatibilityKey) else CompatibilityKey.from_dict(compatibility_key)
        left_ref = left if isinstance(left, UnitRevisionRef) else UnitRevisionRef.from_dict(left)
        right_ref = right if isinstance(right, UnitRevisionRef) else UnitRevisionRef.from_dict(right)
        left_fp = left_fingerprint
        right_fp = right_fingerprint
        if _ref_sort_key(right_ref) < _ref_sort_key(left_ref):
            left_ref, right_ref = right_ref, left_ref
            left_fp, right_fp = right_fp, left_fp
        signals = _canonical_tokens(conflict_signals, "DuplicateCandidate.conflict_signals")
        immutable = {
            "algorithm": algorithm,
            "normalizer": normalizer,
            "config_sha256": config_sha256,
            "compatibility_key": key.to_dict(),
            "match_kind": match_kind,
            "score_ppm": score_ppm,
            "threshold_ppm": threshold_ppm,
            "left": left_ref.to_dict(),
            "right": right_ref.to_dict(),
            "left_fingerprint": left_fp,
            "right_fingerprint": right_fp,
            "conflict_signals": list(signals),
        }
        return cls(
            SCHEMA_VERSION,
            stable_fact_id("candidate", immutable),
            algorithm,
            normalizer,
            config_sha256,
            key,
            match_kind,
            score_ppm,
            threshold_ppm,
            left_ref,
            right_ref,
            left_fp,
            right_fp,
            signals,
            review_issue_id,
        )

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "DuplicateCandidate")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def _immutable_payload(self):
        return {
            "algorithm": self.algorithm,
            "normalizer": self.normalizer,
            "config_sha256": self.config_sha256,
            "compatibility_key": self.compatibility_key.to_dict(),
            "match_kind": self.match_kind,
            "score_ppm": self.score_ppm,
            "threshold_ppm": self.threshold_ppm,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "left_fingerprint": self.left_fingerprint,
            "right_fingerprint": self.right_fingerprint,
            "conflict_signals": list(self.conflict_signals),
        }

    def validate(self):
        _schema(self.schema_version, "DuplicateCandidate")
        _id(self.candidate_id, "candidate", "DuplicateCandidate.candidate_id")
        _token(self.algorithm, "DuplicateCandidate.algorithm")
        _token(self.normalizer, "DuplicateCandidate.normalizer")
        _sha(self.config_sha256, "DuplicateCandidate.config_sha256")
        _enum(self.match_kind, MATCH_KINDS, "DuplicateCandidate.match_kind")
        _integer(self.score_ppm, "DuplicateCandidate.score_ppm", 0, SCORE_SCALE)
        _integer(self.threshold_ppm, "DuplicateCandidate.threshold_ppm", 1, SCORE_SCALE)
        if _ref_sort_key(self.left) >= _ref_sort_key(self.right):
            _fail("DuplicateCandidate left/right must be distinct and canonically ordered")
        _sha(self.left_fingerprint, "DuplicateCandidate.left_fingerprint")
        _sha(self.right_fingerprint, "DuplicateCandidate.right_fingerprint")
        _nullable_id(self.review_issue_id, "issue", "DuplicateCandidate.review_issue_id")
        if self.match_kind == "exact":
            if self.score_ppm != SCORE_SCALE or self.left_fingerprint != self.right_fingerprint:
                _fail("exact duplicate candidates require equal fingerprints and score_ppm=1000000")
            if self.conflict_signals:
                _fail("exact duplicate candidates cannot carry conflict signals")
        else:
            if self.score_ppm < self.threshold_ppm:
                _fail("near duplicate score must meet threshold_ppm")
            if self.left_fingerprint == self.right_fingerprint:
                _fail("equal fingerprints must be represented as match_kind=exact")
        expected = stable_fact_id("candidate", self._immutable_payload())
        if self.candidate_id != expected:
            _fail("DuplicateCandidate.candidate_id does not match its immutable fields")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "algorithm": self.algorithm,
            "normalizer": self.normalizer,
            "config_sha256": self.config_sha256,
            "compatibility_key": self.compatibility_key.to_dict(),
            "match_kind": self.match_kind,
            "score_ppm": self.score_ppm,
            "threshold_ppm": self.threshold_ppm,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "left_fingerprint": self.left_fingerprint,
            "right_fingerprint": self.right_fingerprint,
            "conflict_signals": list(self.conflict_signals),
            "review_issue_id": self.review_issue_id,
        }


@dataclass(frozen=True)
class CanonicalGroup:
    schema_version: int
    canonical_group_id: str
    derivation: str
    normalizer: str
    compatibility_key: CompatibilityKey
    fingerprint_sha256: object
    member_refs: tuple
    display_unit_id: str
    decision_patch_id: object
    conflict_ids: tuple

    FIELDS = (
        "schema_version", "canonical_group_id", "derivation", "normalizer",
        "compatibility_key", "fingerprint_sha256", "member_refs", "display_unit_id",
        "decision_patch_id", "conflict_ids",
    )

    def __post_init__(self):
        object.__setattr__(
            self,
            "compatibility_key",
            self.compatibility_key
            if isinstance(self.compatibility_key, CompatibilityKey)
            else CompatibilityKey.from_dict(self.compatibility_key),
        )
        object.__setattr__(self, "member_refs", _revision_refs(self.member_refs, "CanonicalGroup.member_refs", 2))
        conflict_ids = tuple(sorted(_id(value, "conflict", "CanonicalGroup.conflict_ids[]") for value in self.conflict_ids))
        if len(set(conflict_ids)) != len(conflict_ids):
            _fail("CanonicalGroup.conflict_ids must be unique")
        object.__setattr__(self, "conflict_ids", conflict_ids)
        self.validate()

    @classmethod
    def create(
        cls,
        derivation,
        normalizer,
        compatibility_key,
        member_refs,
        display_unit_id=None,
        fingerprint_sha256=None,
        decision_patch_id=None,
        conflict_ids=(),
    ):
        key = compatibility_key if isinstance(compatibility_key, CompatibilityKey) else CompatibilityKey.from_dict(compatibility_key)
        refs = _revision_refs(member_refs, "CanonicalGroup.member_refs", 2)
        display = display_unit_id or min(ref.unit_id for ref in refs)
        conflicts = tuple(sorted(conflict_ids))
        immutable = {
            "derivation": derivation,
            "normalizer": normalizer,
            "compatibility_key": key.to_dict(),
            "fingerprint_sha256": fingerprint_sha256,
            "member_refs": [ref.to_dict() for ref in refs],
        }
        return cls(
            SCHEMA_VERSION,
            stable_fact_id("group", immutable),
            derivation,
            normalizer,
            key,
            fingerprint_sha256,
            refs,
            display,
            decision_patch_id,
            conflicts,
        )

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "CanonicalGroup")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def _immutable_payload(self):
        return {
            "derivation": self.derivation,
            "normalizer": self.normalizer,
            "compatibility_key": self.compatibility_key.to_dict(),
            "fingerprint_sha256": self.fingerprint_sha256,
            "member_refs": [ref.to_dict() for ref in self.member_refs],
        }

    def validate(self):
        _schema(self.schema_version, "CanonicalGroup")
        _id(self.canonical_group_id, "group", "CanonicalGroup.canonical_group_id")
        _enum(self.derivation, GROUP_DERIVATIONS, "CanonicalGroup.derivation")
        _token(self.normalizer, "CanonicalGroup.normalizer")
        if self.fingerprint_sha256 is not None:
            _sha(self.fingerprint_sha256, "CanonicalGroup.fingerprint_sha256")
        member_ids = {ref.unit_id for ref in self.member_refs}
        if self.display_unit_id not in member_ids:
            _fail("CanonicalGroup.display_unit_id must name a member unit")
        _id(self.display_unit_id, "unit", "CanonicalGroup.display_unit_id")
        _nullable_id(self.decision_patch_id, "patch", "CanonicalGroup.decision_patch_id")
        if self.derivation == "exact_auto":
            if self.fingerprint_sha256 is None:
                _fail("exact_auto groups require fingerprint_sha256")
            if self.decision_patch_id is not None or self.conflict_ids:
                _fail("exact_auto groups cannot carry a review decision or conflicts")
        else:
            if self.fingerprint_sha256 is not None:
                _fail("reviewed_near groups must not claim one exact fingerprint")
            if self.decision_patch_id is None:
                _fail("reviewed_near groups require an explicit decision_patch_id")
        expected = stable_fact_id("group", self._immutable_payload())
        if self.canonical_group_id != expected:
            _fail("CanonicalGroup.canonical_group_id does not match its immutable fields")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "canonical_group_id": self.canonical_group_id,
            "derivation": self.derivation,
            "normalizer": self.normalizer,
            "compatibility_key": self.compatibility_key.to_dict(),
            "fingerprint_sha256": self.fingerprint_sha256,
            "member_refs": [ref.to_dict() for ref in self.member_refs],
            "display_unit_id": self.display_unit_id,
            "decision_patch_id": self.decision_patch_id,
            "conflict_ids": list(self.conflict_ids),
        }


@dataclass(frozen=True)
class SourcePriority:
    schema_version: int
    source_id: str
    source_sha256: str
    rank: int
    tier: str
    basis: str
    evidence: tuple

    FIELDS = ("schema_version", "source_id", "source_sha256", "rank", "tier", "basis", "evidence")

    def __post_init__(self):
        object.__setattr__(self, "evidence", _evidence_refs(self.evidence, "SourcePriority.evidence"))
        self.validate()

    @classmethod
    def create(cls, source_id, source_sha256, rank=0, tier="unknown", basis="unspecified", evidence=()):
        return cls(SCHEMA_VERSION, source_id, source_sha256, rank, tier, basis, tuple(evidence))

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "SourcePriority")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def validate(self):
        _schema(self.schema_version, "SourcePriority")
        _id(self.source_id, "src", "SourcePriority.source_id")
        _sha(self.source_sha256, "SourcePriority.source_sha256")
        _integer(self.rank, "SourcePriority.rank", 0, 100)
        _enum(self.tier, PRIORITY_TIERS, "SourcePriority.tier")
        _enum(self.basis, PRIORITY_BASES, "SourcePriority.basis")
        if self.basis == "unspecified":
            if self.rank != 0 or self.tier != "unknown" or self.evidence:
                _fail("unspecified source priority must be rank=0, tier=unknown, evidence=[]")
        elif not self.evidence:
            _fail("explicit source priority requires content-addressed evidence")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "rank": self.rank,
            "tier": self.tier,
            "basis": self.basis,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True)
class ConflictMember:
    unit_ref: UnitRevisionRef
    claim_fingerprint: str
    answer_ref: object
    answer_fingerprint: object
    priority_rank: int
    priority_basis: str

    FIELDS = (
        "unit_ref", "claim_fingerprint", "answer_ref", "answer_fingerprint",
        "priority_rank", "priority_basis",
    )

    def __post_init__(self):
        object.__setattr__(
            self,
            "unit_ref",
            self.unit_ref if isinstance(self.unit_ref, UnitRevisionRef) else UnitRevisionRef.from_dict(self.unit_ref),
        )
        if self.answer_ref is not None:
            object.__setattr__(
                self,
                "answer_ref",
                self.answer_ref
                if isinstance(self.answer_ref, UnitRevisionRef)
                else UnitRevisionRef.from_dict(self.answer_ref),
            )
        self.validate()

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ConflictMember")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def validate(self):
        _sha(self.claim_fingerprint, "ConflictMember.claim_fingerprint")
        if (self.answer_ref is None) != (self.answer_fingerprint is None):
            _fail("ConflictMember answer_ref and answer_fingerprint must be present together")
        if self.answer_fingerprint is not None:
            _sha(self.answer_fingerprint, "ConflictMember.answer_fingerprint")
        _integer(self.priority_rank, "ConflictMember.priority_rank", 0, 100)
        _enum(self.priority_basis, PRIORITY_BASES, "ConflictMember.priority_basis")
        if self.priority_basis == "unspecified" and self.priority_rank != 0:
            _fail("unspecified conflict-member priority must have rank=0")
        return self

    def to_dict(self):
        return {
            "unit_ref": self.unit_ref.to_dict(),
            "claim_fingerprint": self.claim_fingerprint,
            "answer_ref": self.answer_ref.to_dict() if self.answer_ref is not None else None,
            "answer_fingerprint": self.answer_fingerprint,
            "priority_rank": self.priority_rank,
            "priority_basis": self.priority_basis,
        }

    def identity_dict(self):
        """Return conflict identity fields, excluding mutable priority context."""

        return {
            "unit_ref": self.unit_ref.to_dict(),
            "claim_fingerprint": self.claim_fingerprint,
            "answer_ref": self.answer_ref.to_dict() if self.answer_ref is not None else None,
            "answer_fingerprint": self.answer_fingerprint,
        }


def _conflict_members(values):
    if not isinstance(values, (list, tuple)):
        _fail("SourceConflict.members must be an array")
    members = [value if isinstance(value, ConflictMember) else ConflictMember.from_dict(value) for value in values]
    members.sort(key=lambda item: _ref_sort_key(item.unit_ref))
    if len(members) < 2:
        _fail("SourceConflict.members must contain at least two entries")
    if len({item.unit_ref.unit_id for item in members}) != len(members):
        _fail("SourceConflict.members must reference unique unit IDs")
    return tuple(members)


@dataclass(frozen=True)
class ConflictResolution:
    patch_id: str
    action: str
    preferred_unit_id: object
    reason: str

    FIELDS = ("patch_id", "action", "preferred_unit_id", "reason")

    def __post_init__(self):
        _id(self.patch_id, "patch", "ConflictResolution.patch_id")
        _enum(self.action, RESOLUTION_ACTIONS, "ConflictResolution.action")
        _nullable_id(self.preferred_unit_id, "unit", "ConflictResolution.preferred_unit_id")
        _text(self.reason, "ConflictResolution.reason")
        if self.action == "prefer_source" and self.preferred_unit_id is None:
            _fail("prefer_source resolution requires preferred_unit_id")
        if self.action != "prefer_source" and self.preferred_unit_id is not None:
            _fail("only prefer_source may carry preferred_unit_id")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ConflictResolution")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


@dataclass(frozen=True)
class SourceConflict:
    schema_version: int
    conflict_id: str
    candidate_id: str
    conflict_kind: str
    members: tuple
    reason_codes: tuple
    status: str
    review_issue_id: object
    resolution: object

    FIELDS = (
        "schema_version", "conflict_id", "candidate_id", "conflict_kind", "members",
        "reason_codes", "status", "review_issue_id", "resolution",
    )

    def __post_init__(self):
        object.__setattr__(self, "members", _conflict_members(self.members))
        object.__setattr__(self, "reason_codes", _canonical_tokens(self.reason_codes, "SourceConflict.reason_codes", False))
        if not set(self.reason_codes).issubset(CONFLICT_KINDS):
            _fail("SourceConflict.reason_codes contains an unsupported conflict reason")
        if self.resolution is not None and not isinstance(self.resolution, ConflictResolution):
            object.__setattr__(self, "resolution", ConflictResolution.from_dict(self.resolution))
        self.validate()

    @classmethod
    def create(
        cls,
        candidate_id,
        conflict_kind,
        members,
        reason_codes,
        status="unresolved",
        review_issue_id=None,
        resolution=None,
    ):
        canonical_members = _conflict_members(members)
        reasons = _canonical_tokens(reason_codes, "SourceConflict.reason_codes", False)
        immutable = {
            "candidate_id": candidate_id,
            "conflict_kind": conflict_kind,
            "members": [member.identity_dict() for member in canonical_members],
            "reason_codes": list(reasons),
        }
        return cls(
            SCHEMA_VERSION,
            stable_fact_id("conflict", immutable),
            candidate_id,
            conflict_kind,
            canonical_members,
            reasons,
            status,
            review_issue_id,
            resolution,
        )

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "SourceConflict")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def _immutable_payload(self):
        return {
            "candidate_id": self.candidate_id,
            "conflict_kind": self.conflict_kind,
            "members": [member.identity_dict() for member in self.members],
            "reason_codes": list(self.reason_codes),
        }

    def validate(self):
        _schema(self.schema_version, "SourceConflict")
        _id(self.conflict_id, "conflict", "SourceConflict.conflict_id")
        _id(self.candidate_id, "candidate", "SourceConflict.candidate_id")
        _enum(self.conflict_kind, CONFLICT_KINDS, "SourceConflict.conflict_kind")
        if self.conflict_kind not in self.reason_codes:
            _fail("SourceConflict.conflict_kind must appear in reason_codes")
        _enum(self.status, CONFLICT_STATUSES, "SourceConflict.status")
        _nullable_id(self.review_issue_id, "issue", "SourceConflict.review_issue_id")
        if self.status in ("unresolved", "superseded"):
            if self.resolution is not None:
                _fail("unresolved/superseded conflicts cannot carry a resolution")
        else:
            if self.resolution is None:
                _fail("resolved/unrecoverable conflicts require an explicit patch resolution")
            expected_action = {
                "resolved_keep_both": "keep_both",
                "resolved_preferred": "prefer_source",
                "resolved_not_conflict": "not_conflict",
                "unrecoverable": "unrecoverable",
            }[self.status]
            if self.resolution.action != expected_action:
                _fail("SourceConflict status disagrees with resolution.action")
            if (self.resolution.preferred_unit_id is not None
                    and self.resolution.preferred_unit_id not in {m.unit_ref.unit_id for m in self.members}):
                _fail("SourceConflict preferred_unit_id must name a conflict member")
        expected = stable_fact_id("conflict", self._immutable_payload())
        if self.conflict_id != expected:
            _fail("SourceConflict.conflict_id does not match its immutable fields")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "conflict_id": self.conflict_id,
            "candidate_id": self.candidate_id,
            "conflict_kind": self.conflict_kind,
            "members": [member.to_dict() for member in self.members],
            "reason_codes": list(self.reason_codes),
            "status": self.status,
            "review_issue_id": self.review_issue_id,
            "resolution": self.resolution.to_dict() if self.resolution is not None else None,
        }


def immutable_fact_row(value):
    """Return the complete identity-bearing base row for one derived fact.

    Review issue IDs, display choices, terminal statuses, and resolutions are
    overlays.  Validators compare these base rows against a fresh deterministic
    derivation before replaying any overlay ledger.
    """

    if isinstance(value, DuplicateCandidate):
        return dict(
            {"schema_version": value.schema_version, "candidate_id": value.candidate_id},
            **value._immutable_payload()
        )
    if isinstance(value, CanonicalGroup):
        return dict(
            {
                "schema_version": value.schema_version,
                "canonical_group_id": value.canonical_group_id,
            },
            **value._immutable_payload()
        )
    if isinstance(value, SourceConflict):
        return dict(
            {"schema_version": value.schema_version, "conflict_id": value.conflict_id},
            **value._immutable_payload()
        )
    _fail("immutable_fact_row requires a candidate, canonical group, or source conflict")


def _unique_by_id(values, attribute, label):
    result = {}
    for value in values:
        ident = getattr(value, attribute)
        if ident in result:
            _fail("%s contains duplicate ID %s" % (label, ident))
        result[ident] = value
    return result


def _verify_live_ref(ref, unit_index, label):
    unit = unit_index.get(ref.unit_id)
    if unit is None:
        _fail("%s references missing unit %s" % (label, ref.unit_id))
    current = UnitRevisionRef.from_unit(unit)
    if current != ref:
        _fail("%s unit revision is stale: %s" % (label, ref.unit_id))


def validate_fact_graph(candidates, groups, conflicts=(), priorities=(), unit_index=None):
    """Cross-check candidate/group/conflict facts against each other and live units.

    Exact groups may use connected components because equality of one normalized
    fingerprint is transitive.  Reviewed-near groups require an explicit
    candidate for every member pair, preventing unsafe A~B~C chain folding.
    """

    candidate_rows = [
        value if isinstance(value, DuplicateCandidate) else DuplicateCandidate.from_dict(value)
        for value in candidates
    ]
    group_rows = [value if isinstance(value, CanonicalGroup) else CanonicalGroup.from_dict(value) for value in groups]
    conflict_rows = [
        value if isinstance(value, SourceConflict) else SourceConflict.from_dict(value)
        for value in conflicts
    ]
    priority_rows = [
        value if isinstance(value, SourcePriority) else SourcePriority.from_dict(value)
        for value in priorities
    ]
    candidates_by_id = _unique_by_id(candidate_rows, "candidate_id", "candidates")
    groups_by_id = _unique_by_id(group_rows, "canonical_group_id", "groups")
    conflicts_by_id = _unique_by_id(conflict_rows, "conflict_id", "conflicts")
    priorities_by_source = {}
    for priority in priority_rows:
        key = (priority.source_id, priority.source_sha256)
        if key in priorities_by_source:
            _fail("priorities contains duplicate source revision")
        priorities_by_source[key] = priority

    pair_candidates = {}
    for candidate in candidate_rows:
        key = tuple(sorted((candidate.left.unit_id, candidate.right.unit_id)))
        if key in pair_candidates:
            _fail("candidates contains more than one fact for unit pair %r" % (key,))
        pair_candidates[key] = candidate
        if unit_index is not None:
            _verify_live_ref(candidate.left, unit_index, "candidate %s" % candidate.candidate_id)
            _verify_live_ref(candidate.right, unit_index, "candidate %s" % candidate.candidate_id)

    grouped_units = {}
    for group in group_rows:
        if unit_index is not None:
            for ref in group.member_refs:
                _verify_live_ref(ref, unit_index, "group %s" % group.canonical_group_id)
        for ref in group.member_refs:
            previous = grouped_units.get(ref.unit_id)
            if previous is not None:
                _fail("unit %s belongs to overlapping groups %s and %s"
                      % (ref.unit_id, previous, group.canonical_group_id))
            grouped_units[ref.unit_id] = group.canonical_group_id

        ids = sorted(ref.unit_id for ref in group.member_refs)
        if group.derivation == "exact_auto":
            # Exact equality is transitive.  Require a connected evidence graph,
            # not O(n^2) pair facts for a large repeated-header/footer group.
            adjacency = {unit_id: set() for unit_id in ids}
            for (left_id, right_id), candidate in pair_candidates.items():
                if left_id not in adjacency or right_id not in adjacency:
                    continue
                if candidate.match_kind != "exact":
                    _fail("exact_auto group contains a non-exact candidate edge")
                if candidate.left_fingerprint != group.fingerprint_sha256:
                    _fail("exact_auto group fingerprint disagrees with its candidates")
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
            reached = set()
            stack = [ids[0]]
            while stack:
                current = stack.pop()
                if current in reached:
                    continue
                reached.add(current)
                stack.extend(sorted(adjacency[current] - reached, reverse=True))
            if reached != set(ids):
                _fail("exact_auto group lacks a connected exact-candidate evidence graph")
        else:
            for left_id, right_id in itertools.combinations(ids, 2):
                candidate = pair_candidates.get((left_id, right_id))
                if candidate is None or candidate.match_kind not in ("exact", "near"):
                    _fail("reviewed_near group lacks an all-pairs candidate for %s/%s"
                          % (left_id, right_id))
                if candidate.conflict_signals:
                    linked = set(group.conflict_ids)
                    related = {
                        conflict.conflict_id for conflict in conflict_rows
                        if conflict.candidate_id == candidate.candidate_id
                        and conflict.status == "resolved_not_conflict"
                    }
                    if not linked.intersection(related):
                        _fail("reviewed_near group contains unresolved/unwaived conflict signals")

    for conflict in conflict_rows:
        candidate = candidates_by_id.get(conflict.candidate_id)
        if candidate is None:
            _fail("conflict %s references an unknown candidate" % conflict.conflict_id)
        if candidate.review_issue_id != conflict.review_issue_id:
            _fail("conflict %s review issue disagrees with its candidate" % conflict.conflict_id)
        expected_members = {candidate.left.unit_id, candidate.right.unit_id}
        if {member.unit_ref.unit_id for member in conflict.members} != expected_members:
            _fail("conflict members must exactly equal the candidate pair")
        if unit_index is not None:
            for member in conflict.members:
                _verify_live_ref(member.unit_ref, unit_index, "conflict %s" % conflict.conflict_id)
                if member.answer_ref is not None:
                    _verify_live_ref(member.answer_ref, unit_index, "conflict %s answer" % conflict.conflict_id)
        if conflict.status not in ("resolved_not_conflict", "superseded"):
            containing_groups = {grouped_units.get(unit_id) for unit_id in expected_members}
            containing_groups.discard(None)
            if len(containing_groups) == 1 and all(unit_id in grouped_units for unit_id in expected_members):
                _fail("conflict %s places both members inside one canonical group"
                      % conflict.conflict_id)

    return {
        "candidate_count": len(candidates_by_id),
        "canonical_group_count": len(groups_by_id),
        "conflict_count": len(conflicts_by_id),
        "source_priority_count": len(priorities_by_source),
        "grouped_unit_count": len(grouped_units),
    }


__all__ = [
    "CONFLICT_KINDS",
    "CONFLICT_STATUSES",
    "GROUP_DERIVATIONS",
    "KIND_FAMILIES",
    "MATCH_KINDS",
    "PRIORITY_BASES",
    "PRIORITY_TIERS",
    "PROVENANCE_CLASSES",
    "SCHEMA_VERSION",
    "SCORE_SCALE",
    "SOURCE_SIDES",
    "CanonicalGroup",
    "CompatibilityKey",
    "ConflictMember",
    "ConflictResolution",
    "DuplicateCandidate",
    "FactEvidenceRef",
    "FactValidationError",
    "SourceConflict",
    "SourcePriority",
    "UnitRevisionRef",
    "immutable_fact_row",
    "stable_fact_id",
    "unit_payload_sha256",
    "validate_fact_graph",
]
