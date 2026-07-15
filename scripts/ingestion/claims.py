"""Exact source-location claims and deterministic verification receipts.

Verification deliberately proves only that a Unicode code-point span exists in
the exact referenced source-unit revision.  It does not claim that the quote
entails, supports, or semantically agrees with the authored claim.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .facts import FactValidationError, UnitRevisionRef, stable_fact_id
from .identifiers import canonical_json, safe_workspace_entry, validate_sha256
from .storage import atomic_write_jsonl


CLAIM_RECORDS_PATH = ".ingest/claim_records.jsonl"
CLAIM_RECEIPTS_DIR = ".ingest/claim_verification_receipts"
CLAIM_NORMALIZER = "claim-nfc-ws-v1"
CLAIM_VERIFIER = "claim-location-v1"
VERIFICATION_SCOPE = "location_only"

_CLAIM_ID_RE = re.compile(r"^claim_[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^claim_receipt_[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_CHAPTER_RE = re.compile(r"^ch(?=[0-9]{2,}$)0*[1-9][0-9]*$")
_LANGUAGES = frozenset(("zh", "en", "bilingual", "source"))
_PAYLOAD_FIELDS = frozenset(("text", "latex"))
_SOURCE_ROLES = frozenset(
    (
        "concept_evidence",
        "formula_evidence",
        "question_evidence",
        "answer_evidence",
        "translation_evidence",
        "context_evidence",
    )
)
_CONCEPT_KINDS = frozenset(
    ("title", "heading", "text", "list", "table", "caption", "code", "speaker_notes", "other")
)
_ANSWER_FIELDS = frozenset(
    ("answer", "answer_text", "solution", "solution_text", "worked_solution", "grading_answer")
)
_PROMPT_FIELDS = frozenset(
    ("prompt", "prompt_text", "question", "question_text", "what_asked", "known_quantities", "unknown_quantities")
)
_ENTITY_ALIASES = {
    "knowledge_point": "knowledge_point",
    "formula": "formula",
    "walkthrough": "walkthrough",
    "teaching_item": "walkthrough",
    "quiz_item": "walkthrough",
    "omission": "omission",
    "semantic_exclusion": "semantic_exclusion",
}
_ENTITY_FIELDS = {
    "knowledge_point": {
        "title": ("localized", "title"),
        "explanation": ("localized", "explanation"),
    },
    "formula": {
        "latex": ("scalar", "latex"),
        "explanation": ("localized", "explanation"),
        "applicability": ("localized", "applicability"),
        "variable_symbol": ("indexed_scalar", "variables", "symbol"),
        "variable_meaning": ("indexed_localized", "variables", "meaning"),
    },
    "walkthrough": {
        "title": ("localized", "title"),
        "translation": ("localized", "translation"),
        "prompt_text": ("original_scalar", "prompt_text"),
        "what_asked": ("localized", "what_asked"),
        "answer": ("localized", "answer"),
        "self_check": ("localized", "self_check"),
        "no_formula_reason": ("localized", "no_formula_reason"),
        "steps": ("indexed_localized", "steps"),
    },
    "omission": {"reason": ("localized", "reason")},
    "semantic_exclusion": {"reason": ("localized", "reason")},
}
_GUIDE_ENTITY_ALIASES = {
    "knowledge_point": "knowledge_point",
    "formula": "formula",
    "walkthrough": "walkthrough",
    "teaching_item": "walkthrough",
    "quiz_item": "walkthrough",
    "omission": "omission",
    "semantic_exclusion": "semantic_exclusion",
}
_GUIDE_REF_CLAIM_ROLES = {
    "concept": frozenset(("concept_evidence", "context_evidence")),
    "formula": frozenset(("formula_evidence",)),
    "question": frozenset(("question_evidence", "translation_evidence", "context_evidence")),
    "answer": frozenset(("answer_evidence", "translation_evidence", "context_evidence")),
    "solution": frozenset(("answer_evidence", "translation_evidence", "context_evidence")),
}
_GUIDE_CONCEPT_KINDS = frozenset(
    (
        "title", "heading", "text", "list", "table", "figure", "diagram", "caption",
        "code", "speaker_notes", "other",
    )
)
_EXPLANATION_PROVENANCE = frozenset(("material", "ai_translation", "ai_supplement"))


class ClaimValidationError(FactValidationError):
    """A claim or location receipt violated its exact schema."""


def _fail(message):
    raise ClaimValidationError(message)


def canonical_fact_snapshot_sha256(snapshot):
    """Hash the exact canonical fact snapshot bound to a claim receipt."""

    if not isinstance(snapshot, dict):
        _fail("fact snapshot must be an object")
    try:
        payload = canonical_json(snapshot)
    except (TypeError, ValueError) as exc:
        _fail("fact snapshot must be strict canonical JSON: %s" % exc)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strict_mapping(value, fields, label):
    if not isinstance(value, dict):
        _fail("%s must be an object" % label)
    expected = set(fields)
    actual = set(value)
    if expected != actual:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (label, sorted(expected - actual), sorted(actual - expected)))


def _schema(value, label):
    if type(value) is not int or value != 1:
        _fail("%s.schema_version must be 1" % label)


def _trimmed(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    return value


def _nonblank(value, label):
    if not isinstance(value, str) or not value or not value.strip():
        _fail("%s must contain non-whitespace text" % label)
    return value


def _token(value, label):
    value = _trimmed(value, label)
    if not _TOKEN_RE.fullmatch(value):
        _fail("%s must be a lowercase portable token" % label)
    return value


def _sha(value, label):
    try:
        return validate_sha256(value, label)
    except ValueError as exc:
        raise ClaimValidationError(str(exc)) from exc


def _chapter(value, label="chapter_id"):
    _trimmed(value, label)
    if not _CHAPTER_RE.fullmatch(value):
        _fail("%s must be canonical chNN" % label)
    if value != "ch%02d" % int(value[2:]):
        _fail("%s must not contain redundant leading zeroes" % label)
    return value


def normalize_claim(value):
    if not isinstance(value, str):
        _fail("claim text must be a string")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return " ".join(value.split())


def payload_sha256(value):
    if not isinstance(value, str):
        _fail("source payload must be a string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_manifest_sha256(manifest):
    """Hash a strict parsed manifest independent of JSON formatting/order."""

    try:
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False)
        encoded = canonical_json(manifest).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ClaimValidationError("guide manifest must contain strict JSON values: %s" % exc) from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ClaimSubject:
    chapter_id: str
    entity_type: str
    entity_id: str
    field: str
    language: str
    claim_index: int

    FIELDS = ("chapter_id", "entity_type", "entity_id", "field", "language", "claim_index")

    def __post_init__(self):
        _chapter(self.chapter_id, "ClaimSubject.chapter_id")
        _token(self.entity_type, "ClaimSubject.entity_type")
        _trimmed(self.entity_id, "ClaimSubject.entity_id")
        if any(character in self.entity_id for character in ("\x00", "\r", "\n")):
            _fail("ClaimSubject.entity_id must be a single-line identifier")
        _token(self.field, "ClaimSubject.field")
        if self.language not in _LANGUAGES:
            _fail("ClaimSubject.language must be zh, en, bilingual, or source")
        if type(self.claim_index) is not int or self.claim_index < 0:
            _fail("ClaimSubject.claim_index must be an integer >= 0")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ClaimSubject")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


@dataclass(frozen=True)
class ClaimSource:
    unit_ref: UnitRevisionRef
    payload_field: str
    payload_sha256: str
    role: str

    FIELDS = ("unit_ref", "payload_field", "payload_sha256", "role")

    def __post_init__(self):
        if not isinstance(self.unit_ref, UnitRevisionRef):
            object.__setattr__(self, "unit_ref", UnitRevisionRef.from_dict(self.unit_ref))
        if self.payload_field not in _PAYLOAD_FIELDS:
            _fail("ClaimSource.payload_field must be text or latex")
        _sha(self.payload_sha256, "ClaimSource.payload_sha256")
        if self.role not in _SOURCE_ROLES:
            _fail("ClaimSource.role is unsupported")

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ClaimSource")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {
            "unit_ref": self.unit_ref.to_dict(),
            "payload_field": self.payload_field,
            "payload_sha256": self.payload_sha256,
            "role": self.role,
        }


@dataclass(frozen=True)
class QuoteSpan:
    start: int
    end: int
    offset_unit: str
    text: str
    sha256: str

    FIELDS = ("start", "end", "offset_unit", "text", "sha256")

    def __post_init__(self):
        if type(self.start) is not int or self.start < 0:
            _fail("QuoteSpan.start must be an integer >= 0")
        if type(self.end) is not int or self.end <= self.start:
            _fail("QuoteSpan.end must be an integer greater than start")
        if self.offset_unit != "unicode_codepoint":
            _fail("QuoteSpan.offset_unit must be unicode_codepoint")
        _nonblank(self.text, "QuoteSpan.text")
        _sha(self.sha256, "QuoteSpan.sha256")
        if payload_sha256(self.text) != self.sha256:
            _fail("QuoteSpan.sha256 does not match QuoteSpan.text")

    @classmethod
    def create(cls, start, end, text):
        return cls(start, end, "unicode_codepoint", text, payload_sha256(text))

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "QuoteSpan")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


@dataclass(frozen=True)
class ClaimRecord:
    schema_version: int
    claim_id: str
    subject: ClaimSubject
    claim_text: str
    normalized_claim: str
    normalizer: str
    source: ClaimSource
    quote: QuoteSpan
    verification_scope: str

    FIELDS = (
        "schema_version", "claim_id", "subject", "claim_text", "normalized_claim",
        "normalizer", "source", "quote", "verification_scope",
    )

    def __post_init__(self):
        if not isinstance(self.subject, ClaimSubject):
            object.__setattr__(self, "subject", ClaimSubject.from_dict(self.subject))
        if not isinstance(self.source, ClaimSource):
            object.__setattr__(self, "source", ClaimSource.from_dict(self.source))
        if not isinstance(self.quote, QuoteSpan):
            object.__setattr__(self, "quote", QuoteSpan.from_dict(self.quote))
        self.validate()

    @classmethod
    def create(cls, subject, claim_text, source, quote):
        subject = subject if isinstance(subject, ClaimSubject) else ClaimSubject.from_dict(subject)
        source = source if isinstance(source, ClaimSource) else ClaimSource.from_dict(source)
        quote = quote if isinstance(quote, QuoteSpan) else QuoteSpan.from_dict(quote)
        normalized = normalize_claim(claim_text)
        immutable = {
            "subject": subject.to_dict(),
            "claim_text": claim_text,
            "normalized_claim": normalized,
            "normalizer": CLAIM_NORMALIZER,
            "source": source.to_dict(),
            "quote": quote.to_dict(),
            "verification_scope": VERIFICATION_SCOPE,
        }
        return cls(
            1,
            stable_fact_id("claim", immutable),
            subject,
            claim_text,
            normalized,
            CLAIM_NORMALIZER,
            source,
            quote,
            VERIFICATION_SCOPE,
        )

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ClaimRecord")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def _immutable_payload(self):
        return {
            "subject": self.subject.to_dict(),
            "claim_text": self.claim_text,
            "normalized_claim": self.normalized_claim,
            "normalizer": self.normalizer,
            "source": self.source.to_dict(),
            "quote": self.quote.to_dict(),
            "verification_scope": self.verification_scope,
        }

    def validate(self):
        _schema(self.schema_version, "ClaimRecord")
        if not isinstance(self.claim_id, str) or not _CLAIM_ID_RE.fullmatch(self.claim_id):
            _fail("ClaimRecord.claim_id must be claim_<sha256>")
        _trimmed(self.claim_text, "ClaimRecord.claim_text")
        if not self.normalized_claim or self.normalized_claim != normalize_claim(self.claim_text):
            _fail("ClaimRecord.normalized_claim does not match its claim text")
        if self.normalizer != CLAIM_NORMALIZER:
            _fail("ClaimRecord.normalizer must be %s" % CLAIM_NORMALIZER)
        if self.verification_scope != VERIFICATION_SCOPE:
            _fail("ClaimRecord.verification_scope must be location_only")
        if self.claim_id != stable_fact_id("claim", self._immutable_payload()):
            _fail("ClaimRecord.claim_id does not match its immutable fields")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "subject": self.subject.to_dict(),
            "claim_text": self.claim_text,
            "normalized_claim": self.normalized_claim,
            "normalizer": self.normalizer,
            "source": self.source.to_dict(),
            "quote": self.quote.to_dict(),
            "verification_scope": self.verification_scope,
        }


def _claim_records(values, allow_empty=False):
    if not isinstance(values, (list, tuple)):
        _fail("claim records must be an array")
    rows = [value if isinstance(value, ClaimRecord) else ClaimRecord.from_dict(value) for value in values]
    if not rows and not allow_empty:
        _fail("claim records must not be empty")
    ids = [row.claim_id for row in rows]
    if len(set(ids)) != len(ids):
        _fail("claim records contain duplicate claim_id")
    subjects = [
        (
            row.subject.chapter_id, row.subject.entity_type, row.subject.entity_id,
            row.subject.field, row.subject.language, row.subject.claim_index,
        )
        for row in rows
    ]
    if len(set(subjects)) != len(subjects):
        _fail("claim records contain duplicate subject claim positions")
    return tuple(sorted(rows, key=lambda row: row.claim_id))


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value):
    _fail("non-finite JSON constant is not allowed: %s" % value)


def read_claim_jsonl(path, allow_empty=False):
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        _fail("claim JSONL must be a regular non-symlink file: %s" % source)
    rows = []
    with open(source, "r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(
                    json.loads(
                        line,
                        object_pairs_hook=_object_without_duplicates,
                        parse_constant=_reject_constant,
                    )
                )
            except (json.JSONDecodeError, ClaimValidationError) as exc:
                _fail("invalid claim JSONL line %d: %s" % (line_number, exc))
    return _claim_records(rows, allow_empty=allow_empty)


def load_claim_records(workspace, relative_path=CLAIM_RECORDS_PATH, allow_empty=False):
    path = safe_workspace_entry(Path(workspace).resolve(), relative_path)
    return read_claim_jsonl(path, allow_empty=allow_empty)


def import_claim_records(workspace, records, relative_path=CLAIM_RECORDS_PATH):
    """Strictly validate then atomically replace the authoritative claim sidecar."""

    root = Path(workspace).resolve()
    if not root.is_dir():
        _fail("workspace must be an existing directory")
    rows = _claim_records(records)
    destination = safe_workspace_entry(root, relative_path)
    atomic_write_jsonl(destination, [row.to_dict() for row in rows])
    return rows


def _quote_from_proposal(payload, proposal, label):
    if not isinstance(proposal, dict):
        _fail("%s.quote must be an object" % label)
    fields = set(proposal)
    if fields == {"start", "end"}:
        start = proposal["start"]
        end = proposal["end"]
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            _fail("%s.quote start/end must be a valid [start,end) code-point span" % label)
        if end > len(payload):
            _fail("%s.quote span exceeds payload length" % label)
        text = payload[start:end]
        if not text.strip():
            _fail("%s.quote span must contain non-whitespace text" % label)
        return QuoteSpan.create(start, end, text)
    if fields not in ({"text"}, {"text", "start"}):
        _fail("%s.quote must contain exactly text, text+start, or start+end" % label)
    text = proposal["text"]
    _nonblank(text, "%s.quote.text" % label)
    if "start" in proposal:
        start = proposal["start"]
        if type(start) is not int or start < 0:
            _fail("%s.quote.start must be an integer >= 0" % label)
        end = start + len(text)
        if end > len(payload) or payload[start:end] != text:
            _fail("%s.quote.text is not present at the explicit code-point start" % label)
        return QuoteSpan.create(start, end, text)
    positions = []
    cursor = 0
    while True:
        found = payload.find(text, cursor)
        if found < 0:
            break
        positions.append(found)
        cursor = found + 1
        if len(positions) > 1:
            break
    if not positions:
        _fail("%s.quote.text does not occur in the selected payload" % label)
    if len(positions) != 1:
        _fail("%s.quote.text is ambiguous; provide an explicit code-point start" % label)
    start = positions[0]
    return QuoteSpan.create(start, start + len(text), text)


def compile_claim_proposals(proposals, units, sources=()):
    """Compile ergonomic proposals into full revision-bound ClaimRecords."""

    if not isinstance(proposals, (list, tuple)) or not proposals:
        _fail("claim proposals must be a non-empty array")
    units_by_id = _unit_index(units)
    sources_by_id = _source_index(sources)
    records = []
    expected = {"subject", "source_unit_id", "payload_field", "role", "claim_text", "quote"}
    for index, proposal in enumerate(proposals):
        label = "claim proposals[%d]" % index
        if not isinstance(proposal, dict) or set(proposal) != expected:
            actual = set(proposal) if isinstance(proposal, dict) else set()
            _fail(
                "%s schema mismatch; missing=%r unknown=%r"
                % (label, sorted(expected - actual), sorted(actual - expected))
            )
        unit_id = proposal["source_unit_id"]
        unit = units_by_id.get(unit_id)
        if unit is None:
            _fail("%s.source_unit_id does not exist in current content units" % label)
        payload_field = proposal["payload_field"]
        if payload_field not in _PAYLOAD_FIELDS:
            _fail("%s.payload_field must be text or latex" % label)
        payload = unit.get(payload_field)
        if not isinstance(payload, str) or not payload:
            _fail("%s selected payload is absent or empty" % label)
        source = ClaimSource(
            UnitRevisionRef.from_unit(unit),
            payload_field,
            payload_sha256(payload),
            proposal["role"],
        )
        record = ClaimRecord.create(
            ClaimSubject.from_dict(proposal["subject"]),
            proposal["claim_text"],
            source,
            _quote_from_proposal(payload, proposal["quote"], label),
        )
        verify_claim(record, units_by_id, sources_by_id)
        records.append(record)
    return _claim_records(records)


def _unit_index(units):
    result = {}
    for value in units:
        row = value.to_dict() if callable(getattr(value, "to_dict", None)) else value
        if not isinstance(row, dict) or "unit_id" not in row:
            _fail("unit must be an object with unit_id")
        if row["unit_id"] in result:
            _fail("duplicate live unit ID: %s" % row["unit_id"])
        result[row["unit_id"]] = row
    return result


def _source_index(sources):
    result = {}
    for value in sources:
        row = value.to_dict() if callable(getattr(value, "to_dict", None)) else value
        if not isinstance(row, dict) or not {"source_id", "sha256"}.issubset(row):
            _fail("source must include source_id and sha256")
        if row["source_id"] in result:
            _fail("duplicate live source ID: %s" % row["source_id"])
        result[row["source_id"]] = row
    return result


def _verify_role(record, unit):
    kind = unit.get("kind")
    role = record.source.role
    if role == "concept_evidence" and kind not in _CONCEPT_KINDS:
        _fail("concept_evidence must reference a teaching-content unit")
    if role == "formula_evidence" and kind != "formula":
        _fail("formula_evidence must reference a formula unit")
    if role == "question_evidence" and kind != "question":
        _fail("question_evidence must reference a question unit")
    if role == "answer_evidence" and kind != "answer":
        _fail("answer_evidence must reference an answer unit")
    field = record.subject.field
    if field in _PROMPT_FIELDS and (kind == "answer" or unit.get("asset_role") in ("answer_context", "worked_solution")):
        _fail("prompt claims cannot cite answer-side content")
    if kind == "answer" and field not in _ANSWER_FIELDS and role != "translation_evidence":
        _fail("answer units may only support explicitly answer-side claim fields")


def verify_claim(record, units, sources=()):
    """Verify one claim's exact revision, payload hash, and code-point slice."""

    row = record if isinstance(record, ClaimRecord) else ClaimRecord.from_dict(record)
    units_by_id = units if isinstance(units, dict) else _unit_index(units)
    unit = units_by_id.get(row.source.unit_ref.unit_id)
    if unit is None:
        _fail("claim references missing unit %s" % row.source.unit_ref.unit_id)
    if UnitRevisionRef.from_unit(unit) != row.source.unit_ref:
        _fail("claim unit revision is stale: %s" % row.source.unit_ref.unit_id)
    sources_by_id = sources if isinstance(sources, dict) else _source_index(sources)
    if sources_by_id:
        source = sources_by_id.get(row.source.unit_ref.source_id)
        if source is None:
            _fail("claim references a source absent from the source manifest")
        if source.get("sha256") != row.source.unit_ref.source_sha256:
            _fail("claim source revision is stale")
    payload = unit.get(row.source.payload_field)
    if not isinstance(payload, str) or not payload:
        _fail("claim payload field is absent or empty")
    if payload_sha256(payload) != row.source.payload_sha256:
        _fail("claim payload_sha256 does not match the live payload")
    start = row.quote.start
    end = row.quote.end
    if end > len(payload):
        _fail("claim quote span exceeds the Unicode code-point payload length")
    if payload[start:end] != row.quote.text:
        _fail("claim quote is not the exact live Unicode code-point slice")
    _verify_role(row, unit)
    return row.claim_id


def _manifest_array(manifest, field):
    value = manifest.get(field)
    if not isinstance(value, list):
        _fail("guide manifest %s must be an array" % field)
    return value


def _manifest_entity_index(manifest):
    """Index only the authored entity types supported by ClaimSubject."""

    indexes = {name: {} for name in _ENTITY_FIELDS}
    for position, row in enumerate(_manifest_array(manifest, "knowledge_points")):
        if not isinstance(row, dict):
            _fail("guide knowledge_points[%d] must be an object" % position)
        entity_id = row.get("id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            _fail("guide knowledge_points[%d].id must be non-empty text" % position)
        if entity_id in indexes["knowledge_point"]:
            _fail("guide contains duplicate knowledge point id %r" % entity_id)
        indexes["knowledge_point"][entity_id] = row
        formulas = row.get("formulas")
        if not isinstance(formulas, list):
            _fail("guide knowledge point %r formulas must be an array" % entity_id)
        for formula_position, formula in enumerate(formulas):
            if not isinstance(formula, dict):
                _fail("guide formula %s[%d] must be an object" % (entity_id, formula_position))
            formula_id = formula.get("id")
            if not isinstance(formula_id, str) or not formula_id.strip():
                _fail("guide formula id must be non-empty text")
            if formula_id in indexes["formula"]:
                _fail("guide contains duplicate formula id %r" % formula_id)
            indexes["formula"][formula_id] = formula
    for container, entity_type, id_field in (
        ("walkthroughs", "walkthrough", "item_id"),
        ("omissions", "omission", "item_id"),
        ("semantic_exclusions", "semantic_exclusion", "source_unit_id"),
    ):
        rows = manifest.get(container, [])
        if not isinstance(rows, list):
            _fail("guide manifest %s must be an array" % container)
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                _fail("guide %s[%d] must be an object" % (container, position))
            entity_id = row.get(id_field)
            if not isinstance(entity_id, str) or not entity_id.strip():
                _fail("guide %s[%d].%s must be non-empty text" % (container, position, id_field))
            if entity_id in indexes[entity_type]:
                _fail("guide contains duplicate %s id %r" % (entity_type, entity_id))
            indexes[entity_type][entity_id] = row
    return indexes


def _claim_reference(refs, entity_type, entity_id, output, label):
    if refs is None:
        return
    if not isinstance(refs, list):
        _fail("%s must be an array" % label)
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            _fail("%s[%d] must be an object" % (label, index))
        claim_id = ref.get("claim_id")
        if claim_id is None:
            continue
        if not isinstance(claim_id, str) or not _CLAIM_ID_RE.fullmatch(claim_id):
            _fail("%s[%d].claim_id must be claim_<sha256>" % (label, index))
        output.setdefault(claim_id, set()).add((entity_type, entity_id))


def manifest_claim_references(manifest, chapter_id):
    """Return explicit source-ref claim IDs and their authored entity contexts."""

    if not isinstance(manifest, dict):
        _fail("guide manifest must be an object")
    required = {"schema_version", "chapter", "language", "knowledge_points", "walkthroughs", "omissions"}
    missing = sorted(required - set(manifest))
    if missing:
        _fail("guide manifest is missing fields required for claim binding: %r" % missing)
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        _fail("guide manifest schema_version must be 1")
    _chapter(chapter_id)
    if type(manifest["chapter"]) is not int or manifest["chapter"] != int(chapter_id[2:]):
        _fail("guide manifest chapter does not match claim chapter")
    if manifest["language"] not in ("zh", "en", "bilingual"):
        _fail("guide manifest language is invalid")
    indexes = _manifest_entity_index(manifest)
    references = {}
    for entity_id, row in indexes["knowledge_point"].items():
        _claim_reference(
            row.get("source_refs"), "knowledge_point", entity_id, references,
            "knowledge point %s source_refs" % entity_id,
        )
    for entity_id, row in indexes["formula"].items():
        _claim_reference(
            row.get("source_refs"), "formula", entity_id, references,
            "formula %s source_refs" % entity_id,
        )
    for entity_id, row in indexes["walkthrough"].items():
        _claim_reference(
            row.get("source_trace"), "walkthrough", entity_id, references,
            "walkthrough %s source_trace" % entity_id,
        )
    for entity_type, ref_field in (("omission", "source_refs"), ("semantic_exclusion", "source_refs")):
        for entity_id, row in indexes[entity_type].items():
            _claim_reference(
                row.get(ref_field), entity_type, entity_id, references,
                "%s %s %s" % (entity_type, entity_id, ref_field),
            )
    return {
        claim_id: tuple(sorted(contexts))
        for claim_id, contexts in sorted(references.items())
    }


def _localized_authored_text(value, language, label):
    if language not in ("zh", "en"):
        _fail("%s requires ClaimSubject.language zh or en" % label)
    if not isinstance(value, dict) or set(value) - {"zh", "en"}:
        _fail("%s must be a zh/en localized object" % label)
    text = value.get(language)
    if not isinstance(text, str) or not text.strip():
        _fail("%s has no authored %s text" % (label, language))
    return text


def _authored_claim_text(record, entity):
    canonical_type = _ENTITY_ALIASES.get(record.subject.entity_type)
    if canonical_type is None:
        _fail("unknown ClaimSubject.entity_type %r" % record.subject.entity_type)
    spec = _ENTITY_FIELDS[canonical_type].get(record.subject.field)
    if spec is None:
        _fail(
            "unknown ClaimSubject.field %r for entity_type=%s"
            % (record.subject.field, record.subject.entity_type)
        )
    mode = spec[0]
    field = spec[1]
    claim_index = record.subject.claim_index
    if mode in ("localized", "scalar", "original_scalar"):
        if claim_index != 0:
            _fail("scalar/localized authored fields require claim_index=0")
        if field not in entity:
            _fail("guide entity omits authored field %s" % field)
        value = entity[field]
        if mode == "localized":
            return _localized_authored_text(value, record.subject.language, field)
        if not isinstance(value, str) or not value.strip():
            _fail("guide authored field %s must be non-empty text" % field)
        if mode == "scalar" and record.subject.language != "source":
            _fail("language-neutral/source scalar %s requires language=source" % field)
        if mode == "original_scalar":
            original_language = entity.get("original_language")
            if record.subject.language not in ("source", original_language):
                _fail("prompt_text language must be source or match original_language")
        return value
    rows = entity.get(field)
    if not isinstance(rows, list) or claim_index >= len(rows):
        _fail("claim_index does not locate an authored %s entry" % field)
    value = rows[claim_index]
    nested = spec[2] if len(spec) == 3 else None
    if nested is not None:
        if not isinstance(value, dict) or nested not in value:
            _fail("indexed authored entry omits %s" % nested)
        value = value[nested]
    if mode == "indexed_localized":
        return _localized_authored_text(value, record.subject.language, field)
    if record.subject.language != "source":
        _fail("indexed scalar %s requires language=source" % field)
    if not isinstance(value, str) or not value.strip():
        _fail("indexed authored field %s must be non-empty text" % field)
    return value


def validate_claim_subject_bindings(records, manifest, chapter_id):
    """Bind explicitly referenced claim IDs to exact authored guide text.

    This establishes guide membership and text identity only.  It does not
    establish that the source quote entails the authored claim.
    """

    rows = _claim_records(records)
    references = manifest_claim_references(manifest, chapter_id)
    if not references:
        _fail("guide manifest contains no explicit source-ref claim_id")
    by_id = {row.claim_id: row for row in rows}
    unknown = sorted(set(references) - set(by_id))
    if unknown:
        _fail("guide references claim IDs absent from claim_records: %r" % unknown)
    indexes = _manifest_entity_index(manifest)
    bound = []
    for claim_id in sorted(references):
        record = by_id[claim_id]
        if record.subject.chapter_id != chapter_id:
            _fail("guide references a claim from another chapter: %s" % claim_id)
        canonical_type = _ENTITY_ALIASES.get(record.subject.entity_type)
        if canonical_type is None:
            _fail("unknown ClaimSubject.entity_type %r" % record.subject.entity_type)
        contexts = references[claim_id]
        expected_context = (canonical_type, record.subject.entity_id)
        if any(context != expected_context for context in contexts):
            _fail("claim_id is attached to a source ref for the wrong guide entity")
        entity = indexes[canonical_type].get(record.subject.entity_id)
        if entity is None:
            _fail("ClaimSubject.entity_id does not exist in the guide manifest")
        authored = _authored_claim_text(record, entity)
        if authored != record.claim_text:
            _fail("ClaimRecord.claim_text does not exactly equal the authored guide field")
        bound.append(record)
    return tuple(bound)


def _guide_claim_ref_rows(manifest):
    """Yield each claim-capable guide ref together with its authored entity."""

    for knowledge_point in _manifest_array(manifest, "knowledge_points"):
        if not isinstance(knowledge_point, dict):
            _fail("guide knowledge point must be an object")
        kp_id = knowledge_point.get("id")
        for ref in knowledge_point.get("source_refs", []):
            yield "knowledge_point", kp_id, ref
        formulas = knowledge_point.get("formulas", [])
        if not isinstance(formulas, list):
            _fail("guide knowledge point formulas must be an array")
        for formula in formulas:
            if not isinstance(formula, dict):
                _fail("guide formula must be an object")
            for ref in formula.get("source_refs", []):
                yield "formula", formula.get("id"), ref
    for walkthrough in _manifest_array(manifest, "walkthroughs"):
        if not isinstance(walkthrough, dict):
            _fail("guide walkthrough must be an object")
        for ref in walkthrough.get("source_trace", []):
            yield "walkthrough", walkthrough.get("item_id"), ref
    for container, entity_type, id_field in (
        ("omissions", "omission", "item_id"),
        ("semantic_exclusions", "semantic_exclusion", "source_unit_id"),
    ):
        entities = manifest.get(container, [])
        if not isinstance(entities, list):
            _fail("guide manifest %s must be an array" % container)
        for entity in entities:
            if not isinstance(entity, dict):
                _fail("guide %s entry must be an object" % container)
            for ref in entity.get("source_refs", []):
                yield entity_type, entity.get(id_field), ref


def validate_guide_claim_coverage(records, manifest, chapter_id, units):
    """Validate v2 same-ref bindings and the mandatory material-claim surface.

    This is deliberately shared by receipt creation and Study Guide import.  It
    proves exact authored-field membership plus source location/revision only;
    it does not judge whether the quote entails the authored assertion.
    """

    _chapter(chapter_id)
    if not isinstance(manifest, dict):
        _fail("guide manifest must be an object")
    if type(manifest.get("chapter")) is not int or manifest["chapter"] != int(chapter_id[2:]):
        _fail("guide manifest chapter does not match claim chapter")
    language = manifest.get("language")
    if language not in ("zh", "en", "bilingual"):
        _fail("guide manifest language is invalid")
    target_languages = {"zh", "en"} if language == "bilingual" else {language}
    units_by_id = _unit_index(units)

    required_specs = []
    kp_explanation_provenance = {}
    for knowledge_point in _manifest_array(manifest, "knowledge_points"):
        if not isinstance(knowledge_point, dict):
            _fail("guide knowledge point must be an object")
        kp_id = knowledge_point.get("id")
        explanation = knowledge_point.get("explanation")
        if not isinstance(explanation, dict):
            _fail("guide knowledge point explanation must be localized text")
        if not target_languages.issubset(explanation):
            _fail("guide knowledge point explanation lacks a target language")
        raw_provenance = knowledge_point.get("explanation_provenance")
        if raw_provenance is None:
            provenance = {code: "material" for code in explanation}
        else:
            if (not isinstance(raw_provenance, dict)
                    or set(raw_provenance) != set(explanation)
                    or any(value not in _EXPLANATION_PROVENANCE
                           for value in raw_provenance.values())):
                _fail(
                    "knowledge-point explanation_provenance must label every explanation "
                    "language as material, ai_translation, or ai_supplement"
                )
            provenance = dict(raw_provenance)
        kp_explanation_provenance[kp_id] = provenance
        text_languages = set()
        refs = knowledge_point.get("source_refs", [])
        if not isinstance(refs, list):
            _fail("guide knowledge point source_refs must be an array")
        for ref in refs:
            if not isinstance(ref, dict):
                _fail("guide source ref must be an object")
            unit = units_by_id.get(ref.get("source_unit_id")) or {}
            if (unit.get("kind") not in _GUIDE_CONCEPT_KINDS
                    or not isinstance(unit.get("text"), str)
                    or not unit["text"].strip()):
                continue
            metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
            source_language = metadata.get("source_language")
            if source_language not in ("zh", "en"):
                _fail(
                    "ingestion-v2 textual concept unit %s needs explicit metadata.source_language"
                    % unit.get("unit_id")
                )
            if source_language in explanation:
                text_languages.add(source_language)
        material_languages = {
            code for code, value in provenance.items() if value == "material"
        }
        for source_language in sorted(material_languages):
            required_specs.append({
                "entity_type": "knowledge_point",
                "entity_id": kp_id,
                "field": "explanation",
                "language": source_language,
                "source_role": "concept_evidence",
                "ref_roles": frozenset(("concept",)),
                "source_language": source_language,
                "label": "knowledge_point=%s field=explanation language=%s"
                         % (kp_id, source_language),
            })
        for target_language in sorted(target_languages):
            if provenance[target_language] != "ai_translation":
                continue
            bases = (material_languages & text_languages) - {target_language}
            if not bases:
                _fail(
                    "ai_translation explanation needs a claimed material explanation in "
                    "another source language: %s language=%s" % (kp_id, target_language)
                )
        formulas = knowledge_point.get("formulas", [])
        if not isinstance(formulas, list):
            _fail("guide knowledge point formulas must be an array")
        for formula in formulas:
            if not isinstance(formula, dict):
                _fail("guide formula must be an object")
            formula_id = formula.get("id")
            required_specs.append({
                "entity_type": "formula",
                "entity_id": formula_id,
                "field": "latex",
                "language": "source",
                "source_role": "formula_evidence",
                "ref_roles": frozenset(("formula",)),
                "source_language": None,
                "label": "formula=%s field=latex language=source" % formula_id,
            })

    for walkthrough in _manifest_array(manifest, "walkthroughs"):
        if not isinstance(walkthrough, dict):
            _fail("guide walkthrough must be an object")
        item_id = walkthrough.get("item_id")
        if "prompt_text" in walkthrough:
            original_language = walkthrough.get("original_language")
            allowed_languages = {"source"}
            if original_language in ("zh", "en"):
                allowed_languages.add(original_language)
            required_specs.append({
                "entity_type": "walkthrough",
                "entity_id": item_id,
                "field": "prompt_text",
                "language": frozenset(allowed_languages),
                "source_role": "question_evidence",
                "ref_roles": frozenset(("question",)),
                "source_language": None,
                "label": "walkthrough=%s field=prompt_text" % item_id,
            })
        provenance = walkthrough.get("answer_provenance")
        if not isinstance(provenance, dict):
            _fail("guide walkthrough answer_provenance must be a per-language object")
        for answer_language, provenance_label in sorted(provenance.items()):
            if provenance_label != "material":
                continue
            required_specs.append({
                "entity_type": "walkthrough",
                "entity_id": item_id,
                "field": "answer",
                "language": answer_language,
                "source_role": "answer_evidence",
                "ref_roles": frozenset(("answer", "solution")),
                "source_language": None,
                "label": "walkthrough=%s field=answer language=%s"
                         % (item_id, answer_language),
            })

    rows = _claim_records(records, allow_empty=True)
    guide_ref_rows = tuple(_guide_claim_ref_rows(manifest))
    attached_claim_ids = tuple(
        ref.get("claim_id")
        for _entity_type, _entity_id, ref in guide_ref_rows
        if isinstance(ref, dict) and ref.get("claim_id") is not None
    )
    bound = (
        validate_claim_subject_bindings(rows, manifest, chapter_id)
        if rows and attached_claim_ids else ()
    )
    for record in bound:
        subject = record.subject
        if (subject.entity_type == "knowledge_point" and subject.field == "explanation"
                and kp_explanation_provenance.get(subject.entity_id, {}).get(
                    subject.language, "material") != "material"):
            _fail("AI translation/supplement explanation must not carry a material claim_id")
    records_by_id = {row.claim_id: row for row in bound}
    bindings = {}
    seen_claim_ids = set()
    for entity_type, entity_id, ref in guide_ref_rows:
        if not isinstance(ref, dict):
            _fail("guide source ref must be an object")
        claim_id = ref.get("claim_id")
        if claim_id is None:
            continue
        if claim_id in seen_claim_ids:
            _fail("guide repeats claim_id %s; attach each claim exactly once" % claim_id)
        seen_claim_ids.add(claim_id)
        record = records_by_id.get(claim_id)
        if record is None:
            _fail("guide references a claim absent from bound claim records: %s" % claim_id)
        canonical_type = _GUIDE_ENTITY_ALIASES.get(record.subject.entity_type)
        if (canonical_type, record.subject.entity_id) != (entity_type, entity_id):
            _fail("claim %s is attached to the wrong guide entity" % claim_id)
        if record.source.unit_ref.unit_id != ref.get("source_unit_id"):
            _fail("claim %s source unit disagrees with the exact source ref carrying it" % claim_id)
        if "quote_span" in ref and ref["quote_span"] != record.quote.text:
            _fail("claim %s quote_span disagrees with its verified Unicode quote" % claim_id)
        ref_role = ref.get("role")
        if record.source.role not in _GUIDE_REF_CLAIM_ROLES.get(ref_role, frozenset()):
            _fail(
                "claim %s role=%s is incompatible with guide source role=%s"
                % (claim_id, record.source.role, ref_role)
            )
        bindings.setdefault((entity_type, entity_id), []).append((record, ref))

    if not required_specs:
        _fail(
            "ingestion-v2 guide has zero claimable material assertions; recover source text/latex "
            "or resolve the typed no-claimable-text blocker before issuing a receipt"
        )
    if not rows:
        _fail("ingestion-v2 material assertions require claim records")

    covered = []
    for spec in required_specs:
        expected_language = spec["language"]
        found = False
        for record, ref in bindings.get((spec["entity_type"], spec["entity_id"]), []):
            subject = record.subject
            language_matches = (
                subject.language in expected_language
                if isinstance(expected_language, frozenset)
                else subject.language == expected_language
            )
            if (subject.field != spec["field"] or not language_matches
                    or record.source.role != spec["source_role"]
                    or ref.get("role") not in spec["ref_roles"]):
                continue
            expected_source_language = spec["source_language"]
            if expected_source_language is not None:
                unit = units_by_id.get(record.source.unit_ref.unit_id) or {}
                metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
                if metadata.get("source_language") != expected_source_language:
                    continue
            found = True
            break
        if not found:
            _fail(
                "ingestion-v2 material assertion lacks an exact claim_id binding: %s"
                % spec["label"]
            )
        covered.append(spec["label"])
    return tuple(covered)


@dataclass(frozen=True)
class ClaimVerificationReceipt:
    schema_version: int
    receipt_id: str
    chapter_id: str
    verifier: str
    verification_scope: str
    guide_content_sha256: str
    source_manifest_sha256: str
    content_units_sha256: str
    canonical_groups_sha256: str
    source_conflicts_sha256: str
    claim_records_sha256: str
    fact_snapshot_sha256: str
    location_verified_claim_ids: tuple
    verified_claim_count: int

    FIELDS = (
        "schema_version", "receipt_id", "chapter_id", "verifier", "verification_scope",
        "guide_content_sha256", "source_manifest_sha256", "content_units_sha256",
        "canonical_groups_sha256", "source_conflicts_sha256", "claim_records_sha256",
        "fact_snapshot_sha256", "location_verified_claim_ids", "verified_claim_count",
    )

    def __post_init__(self):
        object.__setattr__(self, "location_verified_claim_ids", tuple(sorted(self.location_verified_claim_ids)))
        self.validate()

    @classmethod
    def create(
        cls,
        chapter_id,
        guide_content_sha256,
        source_manifest_sha256,
        content_units_sha256,
        canonical_groups_sha256,
        source_conflicts_sha256,
        claim_records_sha256,
        fact_snapshot_sha256,
        location_verified_claim_ids,
    ):
        ids = tuple(sorted(location_verified_claim_ids))
        immutable = {
            "chapter_id": chapter_id,
            "verifier": CLAIM_VERIFIER,
            "verification_scope": VERIFICATION_SCOPE,
            "guide_content_sha256": guide_content_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "content_units_sha256": content_units_sha256,
            "canonical_groups_sha256": canonical_groups_sha256,
            "source_conflicts_sha256": source_conflicts_sha256,
            "claim_records_sha256": claim_records_sha256,
            "fact_snapshot_sha256": fact_snapshot_sha256,
            "location_verified_claim_ids": list(ids),
            "verified_claim_count": len(ids),
        }
        digest = hashlib.sha256(canonical_json(immutable).encode("utf-8")).hexdigest()
        return cls(1, "claim_receipt_%s" % digest, **immutable)

    @classmethod
    def from_dict(cls, value):
        _strict_mapping(value, cls.FIELDS, "ClaimVerificationReceipt")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def _immutable_payload(self):
        result = self.to_dict()
        result.pop("schema_version")
        result.pop("receipt_id")
        return result

    def validate(self):
        _schema(self.schema_version, "ClaimVerificationReceipt")
        if not isinstance(self.receipt_id, str) or not _RECEIPT_ID_RE.fullmatch(self.receipt_id):
            _fail("receipt_id must be claim_receipt_<sha256>")
        _chapter(self.chapter_id, "ClaimVerificationReceipt.chapter_id")
        if self.verifier != CLAIM_VERIFIER or self.verification_scope != VERIFICATION_SCOPE:
            _fail("receipt must use location-only claim verifier")
        for field in (
            "guide_content_sha256", "source_manifest_sha256", "content_units_sha256",
            "canonical_groups_sha256", "source_conflicts_sha256", "claim_records_sha256",
            "fact_snapshot_sha256",
        ):
            _sha(getattr(self, field), "ClaimVerificationReceipt.%s" % field)
        if not self.location_verified_claim_ids:
            _fail("receipt must verify at least one claim")
        if len(set(self.location_verified_claim_ids)) != len(self.location_verified_claim_ids):
            _fail("receipt claim IDs must be unique")
        for claim_id in self.location_verified_claim_ids:
            if not isinstance(claim_id, str) or not _CLAIM_ID_RE.fullmatch(claim_id):
                _fail("receipt contains an invalid claim_id")
        if type(self.verified_claim_count) is not int or self.verified_claim_count != len(self.location_verified_claim_ids):
            _fail("verified_claim_count must equal location_verified_claim_ids length")
        expected = "claim_receipt_%s" % hashlib.sha256(
            canonical_json(self._immutable_payload()).encode("utf-8")
        ).hexdigest()
        if self.receipt_id != expected:
            _fail("receipt_id does not match exact bound hashes and claim IDs")
        return self

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "chapter_id": self.chapter_id,
            "verifier": self.verifier,
            "verification_scope": self.verification_scope,
            "guide_content_sha256": self.guide_content_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "content_units_sha256": self.content_units_sha256,
            "canonical_groups_sha256": self.canonical_groups_sha256,
            "source_conflicts_sha256": self.source_conflicts_sha256,
            "claim_records_sha256": self.claim_records_sha256,
            "fact_snapshot_sha256": self.fact_snapshot_sha256,
            "location_verified_claim_ids": list(self.location_verified_claim_ids),
            "verified_claim_count": self.verified_claim_count,
        }


def verify_claim_records(
    records,
    units,
    sources,
    chapter_id,
    *,
    manifest,
    guide_content_sha256,
    source_manifest_sha256,
    content_units_sha256,
    canonical_groups_sha256,
    source_conflicts_sha256,
    claim_records_sha256,
    fact_snapshot_sha256,
):
    rows = _claim_records(records)
    _chapter(chapter_id)
    bound_rows = validate_claim_subject_bindings(rows, manifest, chapter_id)
    units_by_id = _unit_index(units)
    sources_by_id = _source_index(sources)
    verified = [verify_claim(row, units_by_id, sources_by_id) for row in bound_rows]
    return ClaimVerificationReceipt.create(
        chapter_id=chapter_id,
        guide_content_sha256=guide_content_sha256,
        source_manifest_sha256=source_manifest_sha256,
        content_units_sha256=content_units_sha256,
        canonical_groups_sha256=canonical_groups_sha256,
        source_conflicts_sha256=source_conflicts_sha256,
        claim_records_sha256=claim_records_sha256,
        fact_snapshot_sha256=fact_snapshot_sha256,
        location_verified_claim_ids=verified,
    )


__all__ = [
    "CLAIM_NORMALIZER",
    "CLAIM_RECEIPTS_DIR",
    "CLAIM_RECORDS_PATH",
    "CLAIM_VERIFIER",
    "VERIFICATION_SCOPE",
    "ClaimRecord",
    "ClaimSource",
    "ClaimSubject",
    "ClaimValidationError",
    "ClaimVerificationReceipt",
    "QuoteSpan",
    "canonical_fact_snapshot_sha256",
    "canonical_manifest_sha256",
    "compile_claim_proposals",
    "import_claim_records",
    "load_claim_records",
    "normalize_claim",
    "payload_sha256",
    "read_claim_jsonl",
    "manifest_claim_references",
    "validate_guide_claim_coverage",
    "validate_claim_subject_bindings",
    "verify_claim",
    "verify_claim_records",
]
