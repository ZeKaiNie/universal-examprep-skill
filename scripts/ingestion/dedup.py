"""Deterministic duplicate candidates, exact groups, and source conflicts.

Near similarity is evidence for review, never permission to fold content.  All
scores use integer parts-per-million so persisted facts are stable across
Python versions and platforms.
"""

import hashlib
import json
import os
import re
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .facts import (
    SCORE_SCALE,
    CanonicalGroup,
    CompatibilityKey,
    ConflictMember,
    ConflictResolution,
    DuplicateCandidate,
    FactValidationError,
    SourceConflict,
    SourcePriority,
    UnitRevisionRef,
    immutable_fact_row,
    validate_fact_graph,
)
from .identifiers import canonical_json, file_sha256, safe_workspace_entry
from .models import (
    ChapterPhaseMapping,
    ContentUnit,
    EvidenceRef,
    ReviewIssue,
    ReviewPatch,
    SourceRecord,
    canonicalize_source_revisions,
)
from .storage import (
    IngestionStore,
    atomic_write_jsonl,
    read_json,
    read_jsonl,
    stable_file_sha256,
    stable_read_json,
    stable_read_jsonl,
)


DUPLICATE_CANDIDATES_PATH = ".ingest/duplicate_candidates.jsonl"
CANONICAL_GROUPS_PATH = ".ingest/canonical_groups.jsonl"
SOURCE_CONFLICTS_PATH = ".ingest/source_conflicts.jsonl"
SOURCE_PRIORITIES_PATH = ".ingest/source_priorities.jsonl"
BUILD_MANIFEST_PATH = ".ingest/build_manifest.json"
PARSER_RECEIPTS_PATH = ".ingest/parser_receipts.json"

_KIND_FAMILY = {
    "title": "heading",
    "heading": "heading",
    "text": "concept",
    "list": "concept",
    "caption": "concept",
    "speaker_notes": "concept",
    "formula": "formula",
    "question": "question",
    "answer": "answer",
    "table": "table",
    "code": "code",
    "figure": "visual",
    "diagram": "visual",
    "other": "other",
    "page_anchor": "other",
}
_ANSWER_ASSET_ROLES = frozenset(("answer_context", "worked_solution"))
_PROMPT_ASSET_ROLES = frozenset(("question_context",))
_ATTEMPT_ASSET_ROLES = frozenset(("student_attempt",))
_NUMBER_RE = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])")
_BOOL_RE = re.compile(
    r"(?:\btrue\b|\bfalse\b|\byes\b|\bno\b|\bnot\b|\bnever\b|\balways\b|"
    r"正确|错误|是|否|不|无|从不|总是)",
    re.IGNORECASE,
)
_FORMULA_TOKEN_RE = re.compile(r"\\[A-Za-z]+|[A-Za-z]+|\d+(?:\.\d+)?|[^\s]", re.UNICODE)
_STRUCTURAL_LABEL_RE = re.compile(
    r"^\s*(?:theorem|lemma|proposition|corollary|definition|example|quiz|section|"
    r"figure|fig\.?|table|equation)\s+\d+(?:\s*\.\s*\d+)*(?:\s*[:.\-])?\s*",
    re.IGNORECASE,
)
_HOMEWORK_PROBLEM_LOCATOR_RE = re.compile(
    r"^\s*problem\s+([0-9]+(?:\s*\.\s*[0-9]+)+)\b",
    re.IGNORECASE,
)


def _sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_unit(value):
    if isinstance(value, dict):
        result = value
    else:
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            raise FactValidationError("content unit must be an object or expose to_dict()")
        result = to_dict()
    required = {
        "unit_id", "source_id", "source_sha256", "kind", "text", "latex",
        "chapter_id", "asset_path", "asset_role", "paired_unit_id", "metadata", "provenance",
    }
    missing = sorted(required - set(result))
    if missing:
        raise FactValidationError("content unit is missing fields: %s" % ", ".join(missing))
    if not isinstance(result["metadata"], dict):
        raise FactValidationError("content unit metadata must be an object")
    return result


def _strict_source(value):
    if isinstance(value, dict):
        result = value
    else:
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            raise FactValidationError("source must be an object or expose to_dict()")
        result = to_dict()
    missing = sorted({"source_id", "sha256"} - set(result))
    if missing:
        raise FactValidationError("source is missing fields: %s" % ", ".join(missing))
    return result


def _normalize_whitespace(value):
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return " ".join(value.split())


def normalize_exact(value):
    if not isinstance(value, str):
        raise FactValidationError("dedup payload must be text")
    return _normalize_whitespace(value)


def normalize_near(value):
    return normalize_exact(value).casefold()


@dataclass(frozen=True)
class DedupConfig:
    threshold_ppm: int = 920_000
    min_near_chars: int = 24
    max_near_comparisons: int = 200_000
    algorithm: str = "char3-dice-v1"
    exact_normalizer: str = "dedup-nfc-ws-v1"
    near_normalizer: str = "dedup-nfc-casefold-ws-v1"
    schema_version: int = 1

    FIELDS = (
        "schema_version", "algorithm", "exact_normalizer", "near_normalizer",
        "threshold_ppm", "min_near_chars", "max_near_comparisons",
    )

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FactValidationError("DedupConfig.schema_version must be 1")
        for name in ("algorithm", "exact_normalizer", "near_normalizer"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9.-]*", value):
                raise FactValidationError("DedupConfig.%s must be a portable token" % name)
        if type(self.threshold_ppm) is not int or not 1 <= self.threshold_ppm <= SCORE_SCALE:
            raise FactValidationError("DedupConfig.threshold_ppm must be an integer in [1,1000000]")
        if type(self.min_near_chars) is not int or self.min_near_chars < 3:
            raise FactValidationError("DedupConfig.min_near_chars must be an integer >= 3")
        if type(self.max_near_comparisons) is not int or self.max_near_comparisons < 0:
            raise FactValidationError("DedupConfig.max_near_comparisons must be an integer >= 0")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.FIELDS):
            raise FactValidationError("DedupConfig has an invalid exact schema")
        return cls(**{field: value[field] for field in cls.FIELDS})

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}

    @property
    def config_sha256(self):
        return _sha_text(canonical_json(self.to_dict()))


def _canonical_chapter(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"ch\d+", value):
        raise FactValidationError("chapter_id must be null or ch followed by digits")
    number = int(value[2:])
    if number < 1:
        raise FactValidationError("chapter_id must be >= ch01")
    return "ch%02d" % number


def compatibility_key(unit):
    row = _strict_unit(unit)
    family = _KIND_FAMILY.get(row["kind"])
    if family is None:
        raise FactValidationError("unsupported unit kind for dedup: %r" % row["kind"])
    role = row.get("asset_role")
    if role in _ATTEMPT_ASSET_ROLES:
        source_side = "attempt"
    elif row["kind"] == "question" or role in _PROMPT_ASSET_ROLES:
        source_side = "prompt"
    elif row["kind"] == "answer" or role in _ANSWER_ASSET_ROLES:
        source_side = "answer"
    else:
        source_side = "teaching"
    provenance = "ai_supplemented" if row["provenance"] == "ai_supplemented" else "source_backed"
    return CompatibilityKey(_canonical_chapter(row["chapter_id"]), family, source_side, provenance)


def _asset_signature(unit):
    metadata = unit["metadata"]
    payload = {
        "asset_path": unit.get("asset_path"),
        "asset_role": unit.get("asset_role"),
        "asset_sha256": metadata.get("asset_sha256"),
        "assets": metadata.get("assets"),
        "requires_assets": metadata.get("requires_assets"),
        "maybe_requires_assets": metadata.get("maybe_requires_assets"),
    }
    if all(value in (None, [], False) for value in payload.values()):
        return None
    try:
        return canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise FactValidationError("asset metadata must be strict JSON: %s" % exc) from exc


def _content_payload(unit):
    row = _strict_unit(unit)
    if row["kind"] == "formula" and isinstance(row.get("latex"), str) and row["latex"].strip():
        content = row["latex"]
    else:
        content = row.get("text")
    if not isinstance(content, str):
        raise FactValidationError("unit text/latex payload must be a string")
    metadata = row["metadata"]
    supplements = {}
    if row["kind"] == "question" and "options" in metadata:
        supplements["options"] = metadata["options"]
    if row["kind"] == "answer" and "answer_value" in metadata:
        supplements["answer_value"] = metadata["answer_value"]
    if supplements:
        try:
            content += "\n" + canonical_json(supplements)
        except (TypeError, ValueError) as exc:
            raise FactValidationError("question/answer metadata must be strict JSON: %s" % exc) from exc
    return content


def exact_fingerprint(unit, config=None, unit_index=None):
    """Fingerprint exact content, visual identity, and paired-answer identity.

    For questions, callers compiling a unit graph must pass ``unit_index`` so
    equal prompt text cannot be folded across different or missing answers.
    """

    config = config or DedupConfig()
    row = _strict_unit(unit)
    payload = {
        "normalizer": config.exact_normalizer,
        "content": normalize_exact(_content_payload(row)),
        "asset_signature": _asset_signature(row),
    }
    if row["kind"] == "question" and unit_index is not None:
        paired_id = row.get("paired_unit_id")
        answer = _paired_answer(row, unit_index)
        if answer is not None:
            payload["paired_answer"] = {
                "status": "bound",
                "fingerprint": exact_fingerprint(answer, config),
            }
        elif paired_id is None:
            payload["paired_answer"] = {"status": "unpaired"}
        else:
            payload["paired_answer"] = {"status": "missing", "paired_unit_id": paired_id}
    return _sha_text(canonical_json(payload))


def _trigrams(value):
    if len(value) < 3:
        return Counter((value,)) if value else Counter()
    return Counter(value[index:index + 3] for index in range(len(value) - 2))


def similarity_ppm(left, right, min_near_chars=24):
    """Return multiset character-trigram Dice similarity as integer ppm."""

    if not isinstance(left, str) or not isinstance(right, str):
        raise FactValidationError("similarity inputs must be strings")
    left = normalize_near(left)
    right = normalize_near(right)
    if left == right:
        return SCORE_SCALE if left else 0
    if min(len(left), len(right)) < min_near_chars:
        return 0
    left_counts = _trigrams(left)
    right_counts = _trigrams(right)
    denominator = sum(left_counts.values()) + sum(right_counts.values())
    if not denominator:
        return 0
    overlap = sum(min(count, right_counts.get(token, 0)) for token, count in left_counts.items())
    return (2 * overlap * SCORE_SCALE) // denominator


def _answer_value(unit):
    metadata = unit["metadata"]
    if "answer_value" in metadata:
        try:
            return canonical_json(metadata["answer_value"])
        except (TypeError, ValueError) as exc:
            raise FactValidationError("answer_value must be strict JSON: %s" % exc) from exc
    return normalize_near(_content_payload(unit))


def _paired_answer(unit, unit_index):
    paired = unit.get("paired_unit_id")
    if not paired:
        return None
    candidate = unit_index.get(paired)
    if candidate is None or candidate.get("kind") != "answer":
        return None
    return candidate


def _homework_page_reference_locator(unit):
    """Return an explicit problem identity for opaque, image-backed prompts.

    Page-reference homework text is only a transport label; the actual prompt
    lives in its asset.  Two labels can be lexically near-identical while
    naming different textbook problems, so their explicit problem numbers are
    semantic identities that must be honored before fuzzy comparison.
    """

    if unit.get("kind") != "question":
        return None
    metadata = unit.get("metadata") or {}
    if (metadata.get("source_type") != "homework"
            or metadata.get("question_text_status") != "page_reference"
            or metadata.get("requires_assets") is not True
            or not unit.get("external_id")):
        return None
    match = _HOMEWORK_PROBLEM_LOCATOR_RE.match(unit.get("text") or "")
    if match is None:
        return None
    return tuple(int(part.strip()) for part in match.group(1).split("."))


def _has_distinct_page_reference_identity(left, right):
    left_locator = _homework_page_reference_locator(left)
    right_locator = _homework_page_reference_locator(right)
    return (
        left_locator is not None
        and right_locator is not None
        and left_locator != right_locator
        and left.get("external_id") != right.get("external_id")
    )


def _numeric_tokens(value):
    return tuple(_NUMBER_RE.findall(normalize_near(value)))


def _numeric_claim_tokens(value):
    """Return numeric tokens after removing a leading structural locator.

    Theorem/section/example numbers identify where a claim appears; they are
    not numeric values asserted by that claim.  Keeping them in conflict
    detection turns two nearby but distinct statements such as Theorem 7.8
    and Theorem 7.9 into a blocking ``numeric_mismatch`` even when neither
    body asserts a different number.  Only the first leading locator is
    removed, so numeric differences in the actual body still fail closed.
    """

    return _numeric_tokens(_STRUCTURAL_LABEL_RE.sub("", value, count=1))


def _boolean_tokens(value):
    return tuple(token.casefold() for token in _BOOL_RE.findall(normalize_near(value)))


def _formula_tokens(value):
    return tuple(_FORMULA_TOKEN_RE.findall(normalize_exact(value)))


def _conflict_signals(left, right, unit_index):
    signals = set()
    left_asset = _asset_signature(left)
    right_asset = _asset_signature(right)
    if left_asset != right_asset and (left_asset is not None or right_asset is not None):
        signals.add("visual_context_mismatch")
    left_content = _content_payload(left)
    right_content = _content_payload(right)
    if compatibility_key(left).kind_family == "formula" and _formula_tokens(left_content) != _formula_tokens(right_content):
        signals.add("formula_mismatch")
    if _numeric_claim_tokens(left_content) != _numeric_claim_tokens(right_content):
        signals.add("numeric_mismatch")
    if _boolean_tokens(left_content) != _boolean_tokens(right_content):
        signals.add("boolean_mismatch")
    if left["kind"] == "answer" and _answer_value(left) != _answer_value(right):
        signals.add("answer_mismatch")
    if left["kind"] == "question":
        left_answer = _paired_answer(left, unit_index)
        right_answer = _paired_answer(right, unit_index)
        if (left_answer is None) != (right_answer is None):
            signals.add("answer_mismatch")
        elif (left_answer is not None and right_answer is not None
              and exact_fingerprint(left_answer) != exact_fingerprint(right_answer)):
            signals.add("answer_mismatch")
    return tuple(sorted(signals))


def _unit_index(units):
    result = {}
    for value in units:
        row = _strict_unit(value)
        unit_id = row["unit_id"]
        if unit_id in result:
            raise FactValidationError("duplicate content unit ID: %s" % unit_id)
        result[unit_id] = row
    return result


def _near_measure(value):
    """Return the trigram-multiset size used by the Dice denominator."""

    return max(len(value) - 2, 1) if value else 0


def _length_upper_bound(left_measure, threshold_ppm):
    # For right >= left, Dice cannot exceed 2*left/(left+right).
    return ((2 * left_measure * SCORE_SCALE) // threshold_ppm) - left_measure


def build_duplicate_candidates_with_stats(units, config=None):
    """Build deterministic facts plus explicit near-comparison truncation stats."""

    config = config or DedupConfig()
    index = _unit_index(units)
    buckets = defaultdict(list)
    for row in index.values():
        payload = normalize_near(_content_payload(row))
        if not payload:
            continue
        buckets[canonical_json(compatibility_key(row).to_dict())].append(row)
    candidates = []
    stats = {
        "unit_count": len(index),
        "compatibility_bucket_count": len(buckets),
        "exact_fingerprint_group_count": 0,
        "exact_candidate_count": 0,
        "near_representative_count": 0,
        "near_identical_text_variant_count": 0,
        "near_identity_rejected_pair_count": 0,
        "near_eligible_pair_count": 0,
        "near_compared_pair_count": 0,
        "near_skipped_pair_count": 0,
        "near_candidate_count": 0,
        "near_truncated": False,
    }
    for key_json in sorted(buckets):
        rows = sorted(buckets[key_json], key=lambda item: item["unit_id"])
        key = CompatibilityKey.from_dict(json.loads(key_json))
        exact_buckets = defaultdict(list)
        for row in rows:
            exact_buckets[exact_fingerprint(row, config, index)].append(row)

        # A star is a complete, deterministic witness for transitive exact
        # equality and avoids an O(n^2) ledger for repeated headers/footers.
        representatives = []
        for fingerprint in sorted(exact_buckets):
            members = sorted(exact_buckets[fingerprint], key=lambda item: item["unit_id"])
            representatives.append(members[0])
            if len(members) < 2:
                continue
            stats["exact_fingerprint_group_count"] += 1
            left = members[0]
            for right in members[1:]:
                candidates.append(
                    DuplicateCandidate.create(
                        algorithm=config.algorithm,
                        normalizer=config.exact_normalizer,
                        config_sha256=config.config_sha256,
                        compatibility_key=key,
                        match_kind="exact",
                        score_ppm=SCORE_SCALE,
                        threshold_ppm=config.threshold_ppm,
                        left=UnitRevisionRef.from_unit(left),
                        right=UnitRevisionRef.from_unit(right),
                        left_fingerprint=fingerprint,
                        right_fingerprint=fingerprint,
                    )
                )
                stats["exact_candidate_count"] += 1

        # Near work compares one representative from each exact group.  The
        # mathematical length bound cannot remove a pair capable of reaching
        # threshold.  Once the explicit cap is reached, remaining possible
        # pairs are counted, skipped, and surfaced in the compile receipt.
        text_buckets = defaultdict(list)
        for row in representatives:
            normalized = normalize_near(_content_payload(row))
            text_buckets[normalized].append(
                (
                    _near_measure(normalized),
                    row["unit_id"],
                    row,
                    exact_fingerprint(row, config, index),
                )
            )
        stats["near_representative_count"] += len(representatives)
        near_rows = []
        for normalized in sorted(text_buckets):
            variants = sorted(text_buckets[normalized], key=lambda item: item[1])
            near_rows.append(variants[0])
            # Equal normalized text with distinct exact fingerprints normally
            # means different visual identity.  Preserve a linear star of
            # explicit near/conflict evidence even below min_near_chars.
            left_measure, _left_id, left, left_fp = variants[0]
            for _right_measure, _right_id, right, right_fp in variants[1:]:
                if _has_distinct_page_reference_identity(left, right):
                    stats["near_identity_rejected_pair_count"] += 1
                    continue
                candidates.append(
                    DuplicateCandidate.create(
                        algorithm=config.algorithm,
                        normalizer=config.near_normalizer,
                        config_sha256=config.config_sha256,
                        compatibility_key=key,
                        match_kind="near",
                        score_ppm=SCORE_SCALE,
                        threshold_ppm=config.threshold_ppm,
                        left=UnitRevisionRef.from_unit(left),
                        right=UnitRevisionRef.from_unit(right),
                        left_fingerprint=left_fp,
                        right_fingerprint=right_fp,
                        conflict_signals=_conflict_signals(left, right, index),
                    )
                )
                stats["near_identical_text_variant_count"] += 1
                stats["near_candidate_count"] += 1
        near_rows.sort(key=lambda item: (item[0], item[1]))
        measures = [item[0] for item in near_rows]
        for left_index, (left_measure, _left_id, left, left_fp) in enumerate(near_rows):
            if left_measure < config.min_near_chars - 2:
                continue
            upper = bisect_right(
                measures,
                _length_upper_bound(left_measure, config.threshold_ppm),
                lo=left_index + 1,
            )
            eligible_here = max(0, upper - left_index - 1)
            stats["near_eligible_pair_count"] += eligible_here
            remaining = max(0, config.max_near_comparisons - stats["near_compared_pair_count"])
            compare_until = min(upper, left_index + 1 + remaining)
            for right_index in range(left_index + 1, compare_until):
                _right_measure, _right_id, right, right_fp = near_rows[right_index]
                stats["near_compared_pair_count"] += 1
                if _has_distinct_page_reference_identity(left, right):
                    stats["near_identity_rejected_pair_count"] += 1
                    continue
                score = similarity_ppm(
                    _content_payload(left), _content_payload(right), config.min_near_chars
                )
                if score < config.threshold_ppm:
                    continue
                candidates.append(
                    DuplicateCandidate.create(
                        algorithm=config.algorithm,
                        normalizer=config.near_normalizer,
                        config_sha256=config.config_sha256,
                        compatibility_key=key,
                        match_kind="near",
                        score_ppm=score,
                        threshold_ppm=config.threshold_ppm,
                        left=UnitRevisionRef.from_unit(left),
                        right=UnitRevisionRef.from_unit(right),
                        left_fingerprint=left_fp,
                        right_fingerprint=right_fp,
                        conflict_signals=_conflict_signals(left, right, index),
                    )
                )
                stats["near_candidate_count"] += 1
    stats["near_skipped_pair_count"] = (
        stats["near_eligible_pair_count"] - stats["near_compared_pair_count"]
    )
    stats["near_truncated"] = stats["near_skipped_pair_count"] > 0
    return tuple(sorted(candidates, key=lambda item: item.candidate_id)), stats


def build_duplicate_candidates(units, config=None):
    candidates, _stats = build_duplicate_candidates_with_stats(units, config)
    return candidates


def build_exact_groups(candidates):
    """Build only exact-fingerprint groups; near candidates are never folded."""

    rows = [
        value if isinstance(value, DuplicateCandidate) else DuplicateCandidate.from_dict(value)
        for value in candidates
    ]
    buckets = defaultdict(dict)
    for candidate in rows:
        if candidate.match_kind != "exact":
            continue
        key = (
            canonical_json(candidate.compatibility_key.to_dict()),
            candidate.normalizer,
            candidate.left_fingerprint,
        )
        buckets[key][candidate.left.unit_id] = candidate.left
        buckets[key][candidate.right.unit_id] = candidate.right
    groups = []
    for (key_json, normalizer, fingerprint), members in sorted(buckets.items()):
        refs = tuple(members[key] for key in sorted(members))
        if len(refs) < 2:
            continue
        groups.append(
            CanonicalGroup.create(
                derivation="exact_auto",
                normalizer=normalizer,
                compatibility_key=CompatibilityKey.from_dict(json.loads(key_json)),
                member_refs=refs,
                fingerprint_sha256=fingerprint,
            )
        )
    return tuple(sorted(groups, key=lambda item: item.canonical_group_id))


def _priority_index(priorities):
    result = {}
    for value in priorities:
        row = value if isinstance(value, SourcePriority) else SourcePriority.from_dict(value)
        key = (row.source_id, row.source_sha256)
        if key in result:
            raise FactValidationError("duplicate source priority revision: %r" % (key,))
        result[key] = row
    return result


def _conflict_member(unit, fingerprint, unit_index, priorities):
    answer = _paired_answer(unit, unit_index) if unit["kind"] == "question" else None
    priority = priorities.get((unit["source_id"], unit["source_sha256"]))
    return ConflictMember(
        unit_ref=UnitRevisionRef.from_unit(unit),
        claim_fingerprint=fingerprint,
        answer_ref=UnitRevisionRef.from_unit(answer) if answer is not None else None,
        answer_fingerprint=exact_fingerprint(answer) if answer is not None else None,
        priority_rank=priority.rank if priority is not None else 0,
        priority_basis=priority.basis if priority is not None else "unspecified",
    )


def build_source_conflicts(candidates, units, priorities=()):
    """Promote divergent near matches to conflicts only across sources."""

    index = _unit_index(units)
    priority_by_revision = _priority_index(priorities)
    rows = [
        value if isinstance(value, DuplicateCandidate) else DuplicateCandidate.from_dict(value)
        for value in candidates
    ]
    kind_order = (
        "answer_mismatch", "boolean_mismatch", "numeric_mismatch", "formula_mismatch",
        "visual_context_mismatch", "provenance_mismatch", "textual_divergence",
    )
    conflicts = []
    for candidate in rows:
        if candidate.match_kind != "near" or not candidate.conflict_signals:
            continue
        conflict_kind = next(kind for kind in kind_order if kind in candidate.conflict_signals)
        left = index[candidate.left.unit_id]
        right = index[candidate.right.unit_id]
        if left["source_id"] == right["source_id"]:
            continue
        conflicts.append(
            SourceConflict.create(
                candidate_id=candidate.candidate_id,
                conflict_kind=conflict_kind,
                members=(
                    _conflict_member(left, candidate.left_fingerprint, index, priority_by_revision),
                    _conflict_member(right, candidate.right_fingerprint, index, priority_by_revision),
                ),
                reason_codes=candidate.conflict_signals,
            )
        )
    return tuple(sorted(conflicts, key=lambda item: item.conflict_id))


def source_conflict_review_candidates(conflicts, units):
    """Project unresolved source conflicts into typed review-candidate rows.

    The caller remains responsible for creating ``ReviewIssue`` records and
    attaching their stable issue IDs back to the conflict facts.  Rows are
    split per source because a typed issue is revision-bound to one source.
    """

    index = _unit_index(units)
    rows = []
    blocking_kinds = {
        "answer_mismatch", "boolean_mismatch", "numeric_mismatch", "formula_mismatch",
    }
    for value in conflicts:
        conflict = value if isinstance(value, SourceConflict) else SourceConflict.from_dict(value)
        if conflict.status != "unresolved":
            continue
        by_source = defaultdict(list)
        for member in conflict.members:
            live = index.get(member.unit_ref.unit_id)
            if live is None or UnitRevisionRef.from_unit(live) != member.unit_ref:
                raise FactValidationError("conflict review candidate references a stale unit revision")
            by_source[(member.unit_ref.source_id, member.unit_ref.source_sha256)].append(member)
        for source_key in sorted(by_source):
            members = by_source[source_key]
            targets = set()
            pages = set()
            source_files = set()
            for member in members:
                live = index[member.unit_ref.unit_id]
                targets.add(member.unit_ref.unit_id)
                pages.add(live["page"])
                source_files.add(live["source_file"])
                if member.answer_ref is not None:
                    answer = index.get(member.answer_ref.unit_id)
                    if answer is None or UnitRevisionRef.from_unit(answer) != member.answer_ref:
                        raise FactValidationError("conflict review candidate has a stale answer revision")
                    if (answer["source_id"], answer["source_sha256"]) == source_key:
                        targets.add(answer["unit_id"])
                        pages.add(answer["page"])
                        source_files.add(answer["source_file"])
            if len(source_files) != 1:
                raise FactValidationError("one source revision maps to inconsistent source_file values")
            rows.append(
                {
                    "conflict_id": conflict.conflict_id,
                    "candidate_id": conflict.candidate_id,
                    "reason_codes": sorted(set(("source_conflict", conflict.conflict_kind) + conflict.reason_codes)),
                    "source_file": next(iter(source_files)),
                    "pages": sorted(pages),
                    "severity": "blocking" if conflict.conflict_kind in blocking_kinds else "warning",
                    "description": (
                        "Unresolved %s source conflict %s; no source has been selected."
                        % (conflict.conflict_kind, conflict.conflict_id)
                    ),
                    "suggested_action": (
                        "Inspect both revision-bound source locations and apply an explicit "
                        "keep-both, prefer-source, not-conflict, or unrecoverable decision."
                    ),
                    "target_unit_ids": sorted(targets),
                }
            )
    return tuple(sorted(rows, key=lambda row: (row["conflict_id"], row["source_file"])))


def build_source_conflict_review_artifacts(conflicts, units):
    """Create one stable primary ReviewIssue + exact evidence per conflict.

    The evidence records every member location while the issue is bound to the
    lexicographically first source revision, matching the existing one-source
    ReviewIssue contract.  No file is written here; callers can include the
    returned evidence path in their surrounding ingestion transaction.
    """

    index = _unit_index(units)
    blocking_kinds = {
        "answer_mismatch", "boolean_mismatch", "numeric_mismatch", "formula_mismatch",
    }
    artifacts = []
    for value in conflicts:
        conflict = value if isinstance(value, SourceConflict) else SourceConflict.from_dict(value)
        if conflict.status != "unresolved":
            continue
        locations = []
        for member in conflict.members:
            live = index.get(member.unit_ref.unit_id)
            if live is None or UnitRevisionRef.from_unit(live) != member.unit_ref:
                raise FactValidationError("conflict review evidence references a stale unit revision")
            location = {
                "unit_ref": member.unit_ref.to_dict(),
                "source_file": live["source_file"],
                "page": live["page"],
                "kind": live["kind"],
                "asset_path": live.get("asset_path"),
                "answer": None,
            }
            if member.answer_ref is not None:
                answer = index.get(member.answer_ref.unit_id)
                if answer is None or UnitRevisionRef.from_unit(answer) != member.answer_ref:
                    raise FactValidationError("conflict review evidence has a stale answer revision")
                location["answer"] = {
                    "unit_ref": member.answer_ref.to_dict(),
                    "source_file": answer["source_file"],
                    "page": answer["page"],
                    "kind": answer["kind"],
                    "asset_path": answer.get("asset_path"),
                }
            locations.append(location)
        locations.sort(
            key=lambda row: (
                row["source_file"], row["page"], row["unit_ref"]["unit_id"]
            )
        )
        primary = locations[0]
        primary_ref = UnitRevisionRef.from_dict(primary["unit_ref"])
        targets = [primary_ref.unit_id]
        pages = [primary["page"]]
        if primary["answer"] is not None:
            answer_ref = UnitRevisionRef.from_dict(primary["answer"]["unit_ref"])
            if answer_ref.source_id == primary_ref.source_id:
                targets.append(answer_ref.unit_id)
                pages.append(primary["answer"]["page"])
        evidence_payload = {
            "schema_version": 1,
            "kind": "source_conflict",
            "conflict": conflict.to_dict(),
            "locations": locations,
        }
        encoded = (
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        evidence_digest = hashlib.sha256(encoded).hexdigest()
        evidence_path = ".ingest/evidence/%s/%s.json" % (
            primary_ref.source_id,
            evidence_digest,
        )
        evidence = EvidenceRef(evidence_path, evidence_digest)
        issue = ReviewIssue.create(
            primary_ref.source_id,
            primary_ref.source_sha256,
            sorted(set(("source_conflict", conflict.conflict_kind) + conflict.reason_codes)),
            (evidence,),
            "Unresolved %s source conflict %s; no source has been selected."
            % (conflict.conflict_kind, conflict.conflict_id),
            (
                "Inspect every location in the conflict evidence. Correct the underlying unit, "
                "or apply the explicitly permitted terminal ledger decision."
            ),
            pages=pages,
            target_unit_ids=targets,
            severity="blocking" if conflict.conflict_kind in blocking_kinds else "warning",
        )
        artifacts.append(
            {
                "conflict_id": conflict.conflict_id,
                "candidate_id": conflict.candidate_id,
                "issue": issue,
                "evidence_path": evidence_path,
                "evidence_payload": evidence_payload,
            }
        )
    return tuple(sorted(artifacts, key=lambda row: row["conflict_id"]))


def attach_conflict_review_issue_ids(candidates, conflicts, issue_ids_by_conflict):
    """Return facts with stable typed-review issue IDs attached, IDs unchanged."""

    candidate_rows = [
        value if isinstance(value, DuplicateCandidate) else DuplicateCandidate.from_dict(value)
        for value in candidates
    ]
    conflict_rows = [
        value if isinstance(value, SourceConflict) else SourceConflict.from_dict(value)
        for value in conflicts
    ]
    candidate_issue_ids = {}
    attached_conflicts = []
    for conflict in conflict_rows:
        raw_issue = issue_ids_by_conflict.get(conflict.conflict_id)
        if raw_issue is None:
            attached_conflicts.append(conflict)
            continue
        issue_id = raw_issue.issue_id if isinstance(raw_issue, ReviewIssue) else raw_issue
        previous = candidate_issue_ids.get(conflict.candidate_id)
        if previous is not None and previous != issue_id:
            raise FactValidationError("one duplicate candidate cannot attach two review issues")
        candidate_issue_ids[conflict.candidate_id] = issue_id
        attached_conflicts.append(
            SourceConflict(
                conflict.schema_version,
                conflict.conflict_id,
                conflict.candidate_id,
                conflict.conflict_kind,
                conflict.members,
                conflict.reason_codes,
                conflict.status,
                issue_id,
                conflict.resolution,
            )
        )
    attached_candidates = []
    for candidate in candidate_rows:
        issue_id = candidate_issue_ids.get(candidate.candidate_id, candidate.review_issue_id)
        attached_candidates.append(
            DuplicateCandidate(
                candidate.schema_version,
                candidate.candidate_id,
                candidate.algorithm,
                candidate.normalizer,
                candidate.config_sha256,
                candidate.compatibility_key,
                candidate.match_kind,
                candidate.score_ppm,
                candidate.threshold_ppm,
                candidate.left,
                candidate.right,
                candidate.left_fingerprint,
                candidate.right_fingerprint,
                candidate.conflict_signals,
                issue_id,
            )
        )
    return (
        tuple(sorted(attached_candidates, key=lambda row: row.candidate_id)),
        tuple(sorted(attached_conflicts, key=lambda row: row.conflict_id)),
    )


def replay_conflict_review_ledger(conflicts, patches):
    """Replay applied terminal ReviewPatch decisions onto current conflicts.

    ``mark_resolved`` is deliberately limited to warning-only conflicts and
    means ``resolved_keep_both``.  Blocking answer/formula/numeric/boolean
    conflicts must disappear after an evidence-backed unit correction or be
    marked unrecoverable.  Every terminal resolution binds the applied patch ID.
    """

    blocking_kinds = {
        "answer_mismatch", "boolean_mismatch", "numeric_mismatch", "formula_mismatch",
    }
    applied_by_issue = {}
    for value in patches:
        ledger_entry = isinstance(value, dict) and "patch" in value
        if ledger_entry:
            value = value["patch"]
        patch = value if isinstance(value, ReviewPatch) else ReviewPatch.from_dict(value)
        # IngestionStore writes a validated patch inside the append-only ledger;
        # membership in that ledger is the durable proof that apply_patch
        # committed it.  Standalone ReviewPatch values must still explicitly
        # declare ``applied`` so callers cannot replay an uncommitted proposal.
        if not ledger_entry and patch.status != "applied":
            continue
        if patch.issue_id in applied_by_issue:
            raise FactValidationError("review ledger has multiple applied patches for one conflict issue")
        applied_by_issue[patch.issue_id] = patch
    output = []
    for value in conflicts:
        conflict = value if isinstance(value, SourceConflict) else SourceConflict.from_dict(value)
        if conflict.status != "unresolved" or conflict.resolution is not None:
            raise FactValidationError(
                "conflict replay requires an unresolved deterministic base fact"
            )
        patch = applied_by_issue.get(conflict.review_issue_id)
        if patch is None:
            output.append(conflict)
            continue
        if (patch.source_id, patch.source_sha256) not in {
            (member.unit_ref.source_id, member.unit_ref.source_sha256)
            for member in conflict.members
        }:
            raise FactValidationError("conflict terminal patch belongs to an unrelated source revision")
        operation = patch.operations[0]
        if operation["op"] == "mark_resolved":
            if conflict.conflict_kind in blocking_kinds:
                raise FactValidationError(
                    "blocking source conflicts cannot use mark_resolved; correct units or mark_unrecoverable"
                )
            status = "resolved_keep_both"
            action = "keep_both"
        elif operation["op"] == "mark_unrecoverable":
            status = "unrecoverable"
            action = "unrecoverable"
        else:
            # A content-mutating patch should make this revision-bound conflict
            # disappear on recompilation.  If it survives, it remains open.
            output.append(conflict)
            continue
        resolution = ConflictResolution(
            patch.patch_id,
            action,
            None,
            operation["reason"],
        )
        output.append(
            SourceConflict(
                conflict.schema_version,
                conflict.conflict_id,
                conflict.candidate_id,
                conflict.conflict_kind,
                conflict.members,
                conflict.reason_codes,
                status,
                conflict.review_issue_id,
                resolution,
            )
        )
    return tuple(sorted(output, key=lambda row: row.conflict_id))


def _default_priorities(sources):
    rows = []
    seen = set()
    for value in sources:
        source = _strict_source(value)
        key = (source["source_id"], source["sha256"])
        if key in seen:
            raise FactValidationError("duplicate source revision: %r" % (key,))
        seen.add(key)
        rows.append(SourcePriority.create(source["source_id"], source["sha256"]))
    return tuple(sorted(rows, key=lambda item: (item.source_id, item.source_sha256)))


def _reconcile_priorities(sources, priorities):
    source_rows = tuple(_strict_source(value) for value in sources)
    if len({row["source_id"] for row in source_rows}) != len(source_rows):
        raise FactValidationError("sources contain duplicate source IDs")
    current = {(row["source_id"], row["sha256"]): row for row in source_rows}
    supplied = _priority_index(priorities)
    if current:
        stale = sorted(set(supplied) - set(current))
        if stale:
            raise FactValidationError("source priorities contain stale/non-manifest revisions: %r" % stale)
        for key, source in current.items():
            supplied.setdefault(key, SourcePriority.create(source["source_id"], source["sha256"]))
    return tuple(sorted(supplied.values(), key=lambda item: (item.source_id, item.source_sha256)))


def build_dedup_facts(units, sources=(), config=None, priorities=None):
    config = config or DedupConfig()
    unit_rows = tuple(_strict_unit(value) for value in units)
    source_rows = tuple(_strict_source(value) for value in sources)
    if len({row["source_id"] for row in source_rows}) != len(source_rows):
        raise FactValidationError("sources contain duplicate source IDs")
    if source_rows:
        live_sources = {(row["source_id"], row["sha256"]) for row in source_rows}
        missing = sorted(
            {
                (row["source_id"], row["source_sha256"])
                for row in unit_rows
                if (row["source_id"], row["source_sha256"]) not in live_sources
            }
        )
        if missing:
            raise FactValidationError("content units reference non-manifest source revisions: %r" % missing)
    priority_rows = (
        _default_priorities(source_rows)
        if priorities is None
        else _reconcile_priorities(source_rows, priorities)
    )
    candidates, candidate_stats = build_duplicate_candidates_with_stats(unit_rows, config)
    groups = build_exact_groups(candidates)
    conflicts = build_source_conflicts(candidates, unit_rows, priority_rows)
    validate_fact_graph(candidates, groups, conflicts, priority_rows, _unit_index(unit_rows))
    return {
        "config": config,
        "candidates": candidates,
        "canonical_groups": groups,
        "conflicts": conflicts,
        "priorities": tuple(sorted(priority_rows, key=lambda item: (item.source_id, item.source_sha256))),
        "stats": candidate_stats,
        "warnings": (
            ("near_candidate_comparison_cap_reached",)
            if candidate_stats["near_truncated"] else ()
        ),
    }


def _compare_rows(label, expected, actual, key, serializer):
    expected_by_id = {}
    actual_by_id = {}
    for row in expected:
        ident = key(row)
        if ident in expected_by_id:
            raise FactValidationError("fresh %s contains duplicate identity %s" % (label, ident))
        expected_by_id[ident] = serializer(row)
    for row in actual:
        ident = key(row)
        if ident in actual_by_id:
            raise FactValidationError("persisted %s contains duplicate identity %s" % (label, ident))
        actual_by_id[ident] = serializer(row)
    missing = sorted(set(expected_by_id) - set(actual_by_id))
    unexpected = sorted(set(actual_by_id) - set(expected_by_id))
    changed = sorted(
        ident for ident in set(expected_by_id).intersection(actual_by_id)
        if canonical_json(expected_by_id[ident]) != canonical_json(actual_by_id[ident])
    )
    if missing or unexpected or changed:
        raise FactValidationError(
            "%s disagree with deterministic live derivation; missing=%r unexpected=%r changed=%r"
            % (label, missing[:10], unexpected[:10], changed[:10])
        )


def validate_persisted_fact_derivation(
    units,
    sources,
    config,
    candidates,
    canonical_groups,
    conflicts,
    priorities,
    review_patches=(),
):
    """Rebuild the live base graph, replay its ledger, and match sidecars.

    ``config`` and ``priorities`` are required persisted inputs.  This function
    never guesses either input: callers must first bind them to the current
    build manifest.  Candidate/group/conflict identities are compared without
    mutable review overlays, then deterministic issue IDs and the authoritative
    append-only ledger are replayed and final conflicts are compared in full.
    """

    if not isinstance(config, DedupConfig):
        raise FactValidationError("manifest-bound DedupConfig is required")
    unit_rows = tuple(units)
    source_rows = tuple(sources)
    candidate_rows = tuple(
        row if isinstance(row, DuplicateCandidate) else DuplicateCandidate.from_dict(row)
        for row in candidates
    )
    group_rows = tuple(
        row if isinstance(row, CanonicalGroup) else CanonicalGroup.from_dict(row)
        for row in canonical_groups
    )
    conflict_rows = tuple(
        row if isinstance(row, SourceConflict) else SourceConflict.from_dict(row)
        for row in conflicts
    )
    priority_rows = tuple(
        row if isinstance(row, SourcePriority) else SourcePriority.from_dict(row)
        for row in priorities
    )

    base = build_dedup_facts(
        unit_rows,
        source_rows,
        config=config,
        priorities=priority_rows,
    )
    _compare_rows(
        "duplicate candidate base rows",
        base["candidates"],
        candidate_rows,
        lambda row: row.candidate_id,
        immutable_fact_row,
    )
    _compare_rows(
        "canonical group base rows",
        base["canonical_groups"],
        group_rows,
        lambda row: row.canonical_group_id,
        immutable_fact_row,
    )
    _compare_rows(
        "source conflict base rows",
        base["conflicts"],
        conflict_rows,
        lambda row: row.conflict_id,
        immutable_fact_row,
    )
    _compare_rows(
        "source priorities",
        base["priorities"],
        priority_rows,
        lambda row: "%s@%s" % (row.source_id, row.source_sha256),
        lambda row: row.to_dict(),
    )

    issue_ids = {
        artifact["conflict_id"]: artifact["issue"]
        for artifact in build_source_conflict_review_artifacts(base["conflicts"], unit_rows)
    }
    expected_candidates, expected_conflicts = attach_conflict_review_issue_ids(
        base["candidates"], base["conflicts"], issue_ids
    )
    expected_conflicts = replay_conflict_review_ledger(
        expected_conflicts, review_patches
    )
    _compare_rows(
        "duplicate candidate final rows",
        expected_candidates,
        candidate_rows,
        lambda row: row.candidate_id,
        lambda row: row.to_dict(),
    )
    _compare_rows(
        "source conflict final rows",
        expected_conflicts,
        conflict_rows,
        lambda row: row.conflict_id,
        lambda row: row.to_dict(),
    )
    graph_stats = validate_fact_graph(
        candidate_rows,
        group_rows,
        conflict_rows,
        priority_rows,
        _unit_index(unit_rows),
    )
    return dict(
        graph_stats,
        stats=base["stats"],
        warnings=list(base["warnings"]),
    )


def _sidecar_path(workspace, relative_path):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise FactValidationError("workspace must be an existing directory")
    return safe_workspace_entry(root, relative_path)


def load_duplicate_candidates(workspace, relative_path=DUPLICATE_CANDIDATES_PATH):
    return tuple(DuplicateCandidate.from_dict(row) for row in read_jsonl(_sidecar_path(workspace, relative_path)))


def load_canonical_groups(workspace, relative_path=CANONICAL_GROUPS_PATH):
    return tuple(CanonicalGroup.from_dict(row) for row in read_jsonl(_sidecar_path(workspace, relative_path)))


def load_source_conflicts(workspace, relative_path=SOURCE_CONFLICTS_PATH):
    return tuple(SourceConflict.from_dict(row) for row in read_jsonl(_sidecar_path(workspace, relative_path)))


def load_source_priorities(workspace, relative_path=SOURCE_PRIORITIES_PATH):
    return tuple(SourcePriority.from_dict(row) for row in read_jsonl(_sidecar_path(workspace, relative_path)))


def _stable_jsonl_models(path, model):
    rows, snapshot = stable_read_jsonl(path)
    return tuple(model.from_dict(row) for row in rows), snapshot


def _unique_models(rows, model, key, label):
    result = {}
    for raw in rows:
        value = model.from_dict(raw)
        identity = getattr(value, key)
        if identity in result:
            raise FactValidationError("%s contains duplicate %s" % (label, key))
        result[identity] = value
    return result


def _stable_authoritative_inputs(store):
    """Parse every ledger input from the same bytes whose SHA is returned."""

    paths = {
        "source_manifest": store.manifest.path,
        "base_content_units": store.base_units_path,
        "base_chapter_phase_mappings": store.base_mappings_path,
        "content_units": store.units_path,
        "chapter_phase_mappings": store.mappings_path,
        "review_queue": store.review_queue.path,
        "review_patches": store.ledger_path,
        "parser_receipts": safe_workspace_entry(store.workspace, PARSER_RECEIPTS_PATH),
    }
    documents = {}
    captures = {}
    source_document, captures["source_manifest"] = stable_read_json(paths["source_manifest"])
    if (not isinstance(source_document, dict)
            or set(source_document) != {"schema_version", "sources"}
            or source_document.get("schema_version") != 1
            or not isinstance(source_document.get("sources"), list)):
        raise FactValidationError("source manifest has an invalid exact schema")
    sources = _unique_models(
        source_document["sources"], SourceRecord, "source_id", "source manifest"
    )
    if len({row.path for row in sources.values()}) != len(sources):
        raise FactValidationError("source manifest contains duplicate source paths")

    specs = (
        ("base_content_units", ContentUnit, "unit_id"),
        ("base_chapter_phase_mappings", ChapterPhaseMapping, "unit_id"),
        ("content_units", ContentUnit, "unit_id"),
        ("chapter_phase_mappings", ChapterPhaseMapping, "unit_id"),
        ("review_queue", ReviewIssue, "issue_id"),
    )
    for name, model, key in specs:
        rows, captures[name] = stable_read_jsonl(paths[name])
        documents[name] = _unique_models(rows, model, key, name)

    ledger_rows, captures["review_patches"] = stable_read_jsonl(paths["review_patches"])
    ledger = {}
    legacy_ledger_fields = {
        "patch_id", "fingerprint", "issue_id", "source_id", "source_sha256", "patch",
    }
    current_ledger_fields = legacy_ledger_fields.union({"source_revisions"})
    for raw in ledger_rows:
        if (not isinstance(raw, dict)
                or set(raw) not in (legacy_ledger_fields, current_ledger_fields)):
            raise FactValidationError("review patch ledger entry has an invalid exact schema")
        patch = ReviewPatch.from_dict(raw["patch"])
        if (raw["patch_id"] != patch.patch_id
                or raw["issue_id"] != patch.issue_id
                or raw["source_id"] != patch.source_id
                or raw["source_sha256"] != patch.source_sha256
                or raw["fingerprint"] != store._patch_fingerprint(patch)):
            raise FactValidationError("review patch ledger binding is invalid")
        if "source_revisions" in raw:
            try:
                revisions = canonicalize_source_revisions(
                    raw["source_revisions"], "review ledger source_revisions"
                )
            except Exception as exc:
                raise FactValidationError(
                    "review patch ledger source revisions are invalid: %s" % exc
                ) from exc
            if revisions != store._declared_patch_source_revisions(patch):
                raise FactValidationError(
                    "review patch ledger source revisions disagree with the patch"
                )
        if patch.patch_id in ledger:
            raise FactValidationError("review patch ledger contains duplicate patch_id")
        ledger[patch.patch_id] = raw

    parser_document, captures["parser_receipts"] = stable_read_json(paths["parser_receipts"])
    if (not isinstance(parser_document, dict)
            or set(parser_document) != {"schema_version", "receipts"}
            or parser_document.get("schema_version") != 1
            or not isinstance(parser_document.get("receipts"), list)):
        raise FactValidationError("parser_receipts.json has an invalid exact schema")

    documents.update({
        "sources": sources,
        "review_patches": ledger,
        "review_patch_rows": tuple(ledger_rows),
        "parser_receipts": parser_document["receipts"],
    })
    return paths, documents, captures


def _verify_snapshot_ledger(store, documents):
    """Run the existing replay engine against only the captured input objects."""

    sources = documents["sources"]
    issues = documents["review_queue"]
    store.manifest.records = lambda: tuple(sorted(sources.values(), key=lambda row: row.path))
    store.manifest.get = lambda source_id: sources.get(source_id)

    def verify_current(source_id, expected_sha256):
        record = sources.get(source_id)
        if record is None or record.sha256 != expected_sha256:
            raise FactValidationError("snapshot source revision is absent or stale")
        return record

    store.manifest.verify_current = verify_current
    store.base_units = lambda: dict(documents["base_content_units"])
    store.base_mappings = lambda: dict(documents["base_chapter_phase_mappings"])
    store.units = lambda: dict(documents["content_units"])
    store.mappings = lambda: dict(documents["chapter_phase_mappings"])
    store.review_queue.issues = lambda: tuple(issues.values())
    store.review_queue.get = lambda issue_id: issues.get(issue_id)
    store._ledger = lambda: dict(documents["review_patches"])
    store.verify_compiled_matches_ledger()


def validate_workspace_fact_integrity(workspace):
    """Validate one ingestion-v2 fact snapshot against its live workspace inputs.

    This is the fail-closed boundary shared by claim verification and derived
    Study Guide publication.  It intentionally owns the manifest bindings,
    canonical producer configuration, deterministic re-derivation, and review
    ledger replay so callers cannot accidentally trust a merely parseable (or
    freshly re-hashed) fact sidecar.

    Callers that mutate or publish workspace artifacts should hold the
    :class:`IngestionStore` mutation lock for the entire call and subsequent
    write.  The returned hashes/stats are a read snapshot, not a new source of
    truth.
    """

    root = Path(workspace).resolve()
    if not root.is_dir():
        raise FactValidationError("workspace must be an existing directory")
    manifest_path = safe_workspace_entry(root, BUILD_MANIFEST_PATH)
    manifest, manifest_capture = stable_read_json(manifest_path)
    manifest_sha256 = manifest_capture["sha256"]
    if (not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") not in (1, 2)
            or manifest.get("pipeline_version") != "ingestion-v2"):
        raise FactValidationError(
            "workspace fact integrity requires an ingestion-v2 build manifest"
        )

    fact_summary = manifest.get("fact_summary")
    if not isinstance(fact_summary, dict):
        raise FactValidationError("ingestion-v2 build manifest lacks fact_summary")
    expected_paths = {
        "candidates": DUPLICATE_CANDIDATES_PATH,
        "canonical_groups": CANONICAL_GROUPS_PATH,
        "conflicts": SOURCE_CONFLICTS_PATH,
        "priorities": SOURCE_PRIORITIES_PATH,
    }
    if fact_summary.get("paths") != expected_paths:
        raise FactValidationError(
            "fact_summary.paths must exactly bind the four canonical fact sidecars"
        )

    canonical_config = DedupConfig()
    persisted_config = DedupConfig.from_dict(fact_summary.get("config"))
    if persisted_config.to_dict() != canonical_config.to_dict():
        raise FactValidationError(
            "normal ingestion facts must use the canonical default DedupConfig"
        )
    if fact_summary.get("config_sha256") != canonical_config.config_sha256:
        raise FactValidationError(
            "fact_summary.config_sha256 does not bind the canonical DedupConfig"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise FactValidationError("ingestion-v2 build manifest lacks artifact bindings")

    candidates, candidate_capture = _stable_jsonl_models(
        safe_workspace_entry(root, DUPLICATE_CANDIDATES_PATH), DuplicateCandidate
    )
    canonical_groups, group_capture = _stable_jsonl_models(
        safe_workspace_entry(root, CANONICAL_GROUPS_PATH), CanonicalGroup
    )
    conflicts, conflict_capture = _stable_jsonl_models(
        safe_workspace_entry(root, SOURCE_CONFLICTS_PATH), SourceConflict
    )
    priorities, priority_capture = _stable_jsonl_models(
        safe_workspace_entry(root, SOURCE_PRIORITIES_PATH), SourcePriority
    )
    fact_rows = (
        (
            "duplicate_candidates", "candidates", DUPLICATE_CANDIDATES_PATH,
            "duplicate_candidate_count", candidates, candidate_capture,
        ),
        (
            "canonical_groups", "canonical_groups", CANONICAL_GROUPS_PATH,
            "canonical_group_count", canonical_groups, group_capture,
        ),
        (
            "source_conflicts", "conflicts", SOURCE_CONFLICTS_PATH,
            "source_conflict_count", conflicts, conflict_capture,
        ),
        (
            "source_priorities", "priorities", SOURCE_PRIORITIES_PATH,
            "source_priority_count", priorities, priority_capture,
        ),
    )
    sidecars = {}
    sidecar_captures = {}
    for artifact_label, summary_label, relative, count_field, rows, capture in fact_rows:
        artifact = artifacts.get(artifact_label)
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise FactValidationError(
                "build manifest fact artifact %s has an invalid exact schema"
                % artifact_label
            )
        if (artifact.get("path") != relative
                or artifact.get("sha256") != capture["sha256"]):
            raise FactValidationError(
                "build manifest fact artifact %s does not bind the current canonical sidecar"
                % artifact_label
            )
        declared_count = fact_summary.get(count_field)
        if type(declared_count) is not int or declared_count != len(rows):
            raise FactValidationError(
                "fact_summary.%s does not match the current sidecar count" % count_field
            )
        sidecars[artifact_label] = {
            "path": relative,
            "sha256": capture["sha256"],
            "count": len(rows),
            "summary_key": summary_label,
        }
        sidecar_captures[artifact_label] = capture

    source_root = manifest.get("source_root")
    if not isinstance(source_root, str) or not source_root:
        raise FactValidationError("ingestion-v2 build manifest source_root is invalid")
    try:
        store = IngestionStore(root, source_root=source_root)
        input_paths, documents, input_captures = _stable_authoritative_inputs(store)
        inputs = {}
        for artifact_label, path in input_paths.items():
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            artifact = artifacts.get(artifact_label)
            if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
                raise FactValidationError(
                    "build manifest live input %s has an invalid exact artifact schema"
                    % artifact_label
                )
            if (artifact.get("path") != relative
                    or artifact.get("sha256") != input_captures[artifact_label]["sha256"]):
                raise FactValidationError(
                    "build manifest live input %s does not bind the current file"
                    % artifact_label
                )
            inputs[artifact_label] = {
                "path": relative,
                "sha256": input_captures[artifact_label]["sha256"],
            }
        _verify_snapshot_ledger(store, documents)
        sources = tuple(sorted(documents["sources"].values(), key=lambda row: row.path))
        units = tuple(documents["content_units"].values())
        review_patches = documents["review_patch_rows"]
        review_issues = tuple(documents["review_queue"].values())
        page_quality = manifest.get("page_quality")
        if not isinstance(page_quality, list):
            raise FactValidationError("ingestion-v2 build manifest lacks page_quality")
        # Local import avoids a module-import cycle: pipeline imports this module
        # for the canonical fact producer, while this runtime gate reuses its
        # strict parser-receipt validators after both modules are initialized.
        from .pipeline import _validate_parser_review_consistency, _validated_parser_receipts
        parser_receipts = _validated_parser_receipts(
            {"schema_version": 2, "parser_receipts": documents["parser_receipts"]},
            sources,
            page_quality,
        )
        _validate_parser_review_consistency(
            parser_receipts, sources, page_quality, review_issues, review_patches
        )
        source_captures = {}
        for source in sources:
            absolute = safe_workspace_entry(store.source_root, source.path)
            actual_sha256, actual_size = stable_file_sha256(absolute)
            if actual_sha256 != source.sha256 or actual_size != source.size_bytes:
                raise FactValidationError("source revision drifted: %s" % source.path)
            source_captures[source.source_id] = (actual_sha256, actual_size)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise FactValidationError(
            "cannot load authoritative fact inputs and review ledger: %s" % exc
        ) from exc
    for count_field, rows in (
        ("source_count", sources),
        ("unit_count", units),
        ("review_issue_count", review_issues),
    ):
        declared_count = manifest.get(count_field)
        if type(declared_count) is not int or declared_count != len(rows):
            raise FactValidationError(
                "build manifest %s does not match the live fact input count" % count_field
            )

    derived = validate_persisted_fact_derivation(
        units,
        sources,
        canonical_config,
        candidates,
        canonical_groups,
        conflicts,
        priorities,
        review_patches,
    )
    if fact_summary.get("stats") != derived["stats"]:
        raise FactValidationError(
            "fact_summary.stats does not match deterministic live derivation"
        )
    warnings = fact_summary.get("warnings")
    if (not isinstance(warnings, list)
            or any(not isinstance(value, str) for value in warnings)
            or warnings != sorted(set(warnings))):
        raise FactValidationError("fact_summary.warnings must be sorted unique strings")
    expected_warnings = set(derived["warnings"])
    actual_warnings = set(warnings)
    allowed_historical_warnings = {"stale_source_priorities_dropped"}
    if (expected_warnings - actual_warnings
            or actual_warnings - expected_warnings - allowed_historical_warnings):
        raise FactValidationError(
            "fact_summary.warnings does not match deterministic live derivation"
        )

    # Detect a concurrent replacement when a read-only caller did not already
    # hold the workspace lock.  Publication callers still keep the lock through
    # their subsequent receipt/manifest write.
    _manifest_again, manifest_again = stable_read_json(manifest_path)
    if manifest_again != manifest_capture:
        raise FactValidationError("ingestion build manifest changed during fact validation")
    for artifact_label, snapshot in sidecars.items():
        path = safe_workspace_entry(root, snapshot["path"])
        _rows, current = stable_read_jsonl(path)
        if current != sidecar_captures[artifact_label]:
            raise FactValidationError(
                "fact sidecar %s changed during validation" % artifact_label
            )
    for artifact_label, snapshot in inputs.items():
        path = input_paths[artifact_label]
        if artifact_label in ("source_manifest", "parser_receipts"):
            _document, current = stable_read_json(path)
        else:
            _rows, current = stable_read_jsonl(path)
        if current != input_captures[artifact_label]:
            raise FactValidationError(
                "live fact input %s changed during validation" % artifact_label
            )
    try:
        for source in sources:
            absolute = safe_workspace_entry(store.source_root, source.path)
            if stable_file_sha256(absolute) != source_captures[source.source_id]:
                raise FactValidationError("source revision changed: %s" % source.path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise FactValidationError(
            "source revision changed during fact validation: %s" % exc
        ) from exc

    source_revisions = [
        {
            "source_id": source.source_id,
            "path": source.path,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
        }
        for source in sorted(sources, key=lambda row: (row.path, row.source_id))
    ]
    snapshot = {
        "schema_version": 1,
        "pipeline_version": "ingestion-v2",
        "build_manifest": {
            "path": BUILD_MANIFEST_PATH,
            "sha256": manifest_sha256,
        },
        "config": canonical_config.to_dict(),
        "config_sha256": canonical_config.config_sha256,
        "page_quality": {
            "source": BUILD_MANIFEST_PATH + "#page_quality",
            "sha256": _sha_text(canonical_json(page_quality)),
            "count": len(page_quality),
        },
        "inputs": inputs,
        "sidecars": sidecars,
        "stats": dict(derived["stats"]),
        "warnings": list(warnings),
        "counts": {
            "candidate_count": len(candidates),
            "canonical_group_count": len(canonical_groups),
            "conflict_count": len(conflicts),
            "priority_count": len(priorities),
            "source_count": len(sources),
            "unit_count": len(units),
            "review_issue_count": len(review_issues),
            "review_patch_count": len(review_patches),
            "parser_receipt_count": len(parser_receipts),
        },
        "source_revisions": source_revisions,
    }
    return {
        **snapshot,
        "snapshot": snapshot,
        "warnings": tuple(warnings),
        "candidate_count": len(candidates),
        "canonical_group_count": len(canonical_groups),
        "conflict_count": len(conflicts),
        "priority_count": len(priorities),
        "source_count": len(sources),
        "unit_count": len(units),
        "review_issue_count": len(review_issues),
        "review_patch_count": len(review_patches),
        "parser_receipt_count": len(parser_receipts),
        "candidates": candidates,
        "canonical_groups": canonical_groups,
        "conflicts": conflicts,
        "priorities": priorities,
    }


def compile_ingestion_facts(
    workspace,
    units,
    sources,
    config=None,
    priorities=None,
    issue_ids_by_conflict=None,
    review_patches=(),
):
    """Validate all facts, then atomically replace each deterministic sidecar.

    Typed-review issue IDs and terminal decisions are overlays on deterministic
    conflict identity.  They never change candidate/conflict IDs and are always
    replayed from the authoritative append-only review ledger.  A surrounding
    transaction/build manifest owns cross-file generation consistency; this
    helper does not claim one filesystem-atomic switch for all four files.
    """

    units = tuple(units)
    sources = tuple(sources)
    priority_warnings = []
    if priorities is None:
        priority_path = _sidecar_path(workspace, SOURCE_PRIORITIES_PATH)
        if priority_path.exists():
            existing = load_source_priorities(workspace)
            current = {
                (row["source_id"], row["sha256"])
                for row in (_strict_source(value) for value in sources)
            }
            retained = tuple(
                row for row in existing if (row.source_id, row.source_sha256) in current
            )
            if len(retained) != len(existing):
                priority_warnings.append("stale_source_priorities_dropped")
            priorities = retained
    facts = build_dedup_facts(units, sources, config=config, priorities=priorities)
    issue_ids_by_conflict = issue_ids_by_conflict or {}
    if not isinstance(issue_ids_by_conflict, dict):
        raise FactValidationError("issue_ids_by_conflict must be an object")
    candidates, conflicts = attach_conflict_review_issue_ids(
        facts["candidates"], facts["conflicts"], issue_ids_by_conflict
    )
    conflicts = replay_conflict_review_ledger(conflicts, review_patches)
    validate_fact_graph(
        candidates,
        facts["canonical_groups"],
        conflicts,
        facts["priorities"],
        _unit_index(units),
    )
    facts["candidates"] = candidates
    facts["conflicts"] = conflicts
    paths = {
        "candidates": _sidecar_path(workspace, DUPLICATE_CANDIDATES_PATH),
        "canonical_groups": _sidecar_path(workspace, CANONICAL_GROUPS_PATH),
        "conflicts": _sidecar_path(workspace, SOURCE_CONFLICTS_PATH),
        "priorities": _sidecar_path(workspace, SOURCE_PRIORITIES_PATH),
    }
    # Each replacement is fsync + os.replace through the existing persistence
    # primitive.  Validation completed before the first authoritative write.
    for key in ("candidates", "canonical_groups", "conflicts", "priorities"):
        atomic_write_jsonl(paths[key], [row.to_dict() for row in facts[key]])
    return {
        "config": facts["config"].to_dict(),
        "config_sha256": facts["config"].config_sha256,
        "duplicate_candidate_count": len(facts["candidates"]),
        "canonical_group_count": len(facts["canonical_groups"]),
        "source_conflict_count": len(facts["conflicts"]),
        "source_priority_count": len(facts["priorities"]),
        "stats": facts["stats"],
        "warnings": sorted(set(priority_warnings).union(facts["warnings"])),
        "paths": {key: os.path.relpath(path, Path(workspace).resolve()).replace("\\", "/") for key, path in paths.items()},
    }


__all__ = [
    "CANONICAL_GROUPS_PATH",
    "DUPLICATE_CANDIDATES_PATH",
    "SOURCE_CONFLICTS_PATH",
    "SOURCE_PRIORITIES_PATH",
    "DedupConfig",
    "build_dedup_facts",
    "build_duplicate_candidates",
    "build_duplicate_candidates_with_stats",
    "build_exact_groups",
    "build_source_conflicts",
    "build_source_conflict_review_artifacts",
    "attach_conflict_review_issue_ids",
    "source_conflict_review_candidates",
    "compatibility_key",
    "compile_ingestion_facts",
    "exact_fingerprint",
    "load_canonical_groups",
    "load_duplicate_candidates",
    "load_source_conflicts",
    "load_source_priorities",
    "normalize_exact",
    "normalize_near",
    "replay_conflict_review_ledger",
    "similarity_ppm",
    "validate_persisted_fact_derivation",
    "validate_workspace_fact_integrity",
]
