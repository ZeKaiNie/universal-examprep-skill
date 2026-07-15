#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict, dependency-free evidence gate for optional retrieval backends.

The production retriever remains :mod:`retrieve` and BM25 remains its default.
This module owns only evaluation schemas, deterministic metrics, paired
comparisons, and the three-state promotion decision used by the maintainer
benchmark.  It deliberately imports no dense model, reranker, or network client.
"""

import hashlib
import json
import math
import random


SCHEMA_VERSION = 1
DECISION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
DECISION_NO_GO = "NO_GO"
DECISION_GO_OPTIONAL = "GO_OPTIONAL"
DECISIONS = frozenset((DECISION_INSUFFICIENT, DECISION_NO_GO, DECISION_GO_OPTIONAL))

LANGUAGES = frozenset(("zh", "en", "bilingual"))
BACKEND_KINDS = frozenset(("bm25", "dense", "rrf", "hybrid", "reranker"))
CRITICAL_SLICES = (
    "paraphrase",
    "cross_language",
    "formula_symbol",
    "question_or_figure",
    "rare_term",
)

DEFAULT_THRESHOLDS = {
    "min_total_queries": 180,
    "min_answerable_queries": 120,
    "min_oos_queries": 40,
    "min_courses": 3,
    "min_answerable_per_course": 30,
    "min_queries_per_critical_slice": 20,
    "baseline_recall_at_5_floor": 0.90,
    "baseline_slice_floor": 0.85,
    "min_delta_recall_at_5": 0.05,
    "candidate_recall_at_5_floor": 0.92,
    "candidate_slice_floor": 0.85,
    "max_slice_regression": 0.02,
    "max_mrr_regression": 0.01,
    "max_false_accepts": 0,
    "max_hard_negative_intrusion_at_5": 0.05,
    "max_p95_latency_ms": 500.0,
    "max_latency_multiple": 10.0,
    "max_index_size_multiple": 10.0,
    "max_index_bytes_per_100k_docs": 1024 * 1024 * 1024,
    "min_top5_stability": 0.99,
    "max_p_value": 0.05,
    "reranker_min_delta_mrr": 0.05,
    "reranker_min_delta_recall_at_1": 0.05,
}

_ONLY_RAISE_THRESHOLDS = frozenset((
    "min_total_queries", "min_answerable_queries", "min_oos_queries",
    "min_courses", "min_answerable_per_course",
    "min_queries_per_critical_slice", "min_delta_recall_at_5",
    "candidate_recall_at_5_floor", "candidate_slice_floor",
    "min_top5_stability", "reranker_min_delta_mrr",
    "reranker_min_delta_recall_at_1",
))
_ONLY_LOWER_THRESHOLDS = frozenset(DEFAULT_THRESHOLDS) - _ONLY_RAISE_THRESHOLDS
_INTEGER_THRESHOLDS = frozenset((
    "min_total_queries", "min_answerable_queries", "min_oos_queries",
    "min_courses", "min_answerable_per_course",
    "min_queries_per_critical_slice", "max_false_accepts",
))

GOLD_FIELDS = ("schema_version", "gold_id", "split", "index_bundle", "queries")
INDEX_BUNDLE_FIELDS = (
    "course_id", "index_sha256", "content_units_sha256", "source_manifest_sha256",
)
QUERY_FIELDS = (
    "query_id", "course_id", "query", "language", "answerable",
    "relevant_unit_ids", "hard_negative_unit_ids", "tags", "evidence",
)
EVIDENCE_FIELDS = ("unit_id", "source_id", "source_sha256", "page")
RUN_FIELDS = (
    "schema_version", "run_id", "gold_sha256", "index_bundle",
    "index_bundle_sha256", "backend", "parent", "fusion", "resources", "results",
)
BACKEND_FIELDS = ("name", "kind", "version", "config_sha256")
PARENT_FIELDS = ("backend", "run_sha256")
FUSION_FIELDS = ("method", "rank_constant", "window_size", "top_k", "inputs")
FUSION_INPUT_FIELDS = ("backend", "result_sha256")
RESOURCE_FIELDS = (
    "query_count", "indexed_docs", "index_size_bytes", "p95_latency_ms",
    "top5_stability",
)
RESULT_FIELDS = ("query_id", "abstain", "abstain_reason", "latency_ms", "hits")
HIT_FIELDS = (
    "doc_id", "rank", "unit_ids", "score", "score_kind", "components",
)
COMPONENT_FIELDS = ("backend", "rank", "score", "score_kind")


class RetrievalEvaluationError(ValueError):
    """A retrieval gold set, run receipt, or comparison is invalid."""


def _fail(message):
    raise RetrievalEvaluationError(message)


def _strict_object(value, fields, label):
    if not isinstance(value, dict):
        _fail("%s must be an object" % label)
    expected = set(fields)
    actual = set(value)
    if actual != expected:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (
            label, sorted(expected - actual), sorted(actual - expected)))


def _token(value, label):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(char in value for char in ("\x00", "\r", "\n"))):
        _fail("%s must be a non-empty, trimmed, single-line string" % label)
    return value


def _sha256(value, label):
    value = _token(value, label)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        _fail("%s must be a lowercase SHA-256 digest" % label)
    return value


def _number(value, label, minimum=None, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        _fail("%s must be a finite number" % label)
    result = float(value)
    if minimum is not None and result < minimum:
        _fail("%s must be >= %s" % (label, minimum))
    if maximum is not None and result > maximum:
        _fail("%s must be <= %s" % (label, maximum))
    return result


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("%s must be an integer >= %d" % (label, minimum))
    return value


def _unique_tokens(values, label, allow_empty=True):
    if not isinstance(values, list):
        _fail("%s must be an array" % label)
    result = []
    seen = set()
    for index, value in enumerate(values):
        value = _token(value, "%s[%d]" % (label, index))
        if value in seen:
            _fail("%s contains duplicate value %s" % (label, value))
        seen.add(value)
        result.append(value)
    if not allow_empty and not result:
        _fail("%s must not be empty" % label)
    return tuple(result)


def canonical_sha256(value):
    """Return the stable SHA-256 of a strict, canonical JSON value."""

    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RetrievalEvaluationError("value is not canonical JSON: %s" % exc) from exc
    return hashlib.sha256(payload).hexdigest()


def _validate_index_bundle(value, label):
    if not isinstance(value, list) or not value:
        _fail("%s must be a non-empty array" % label)
    courses = {}
    previous = None
    artifact_identities = set()
    normalized_rows = []
    for position, raw in enumerate(value):
        item_label = "%s[%d]" % (label, position)
        _strict_object(raw, INDEX_BUNDLE_FIELDS, item_label)
        course_id = _token(raw["course_id"], item_label + ".course_id")
        if course_id in courses:
            _fail("%s contains duplicate course_id %s" % (label, course_id))
        if previous is not None and course_id <= previous:
            _fail("%s must be sorted by course_id" % label)
        previous = course_id
        row = {
            "course_id": course_id,
            "index_sha256": _sha256(raw["index_sha256"], item_label + ".index_sha256"),
            "content_units_sha256": _sha256(
                raw["content_units_sha256"], item_label + ".content_units_sha256"),
            "source_manifest_sha256": _sha256(
                raw["source_manifest_sha256"], item_label + ".source_manifest_sha256"),
        }
        identity = (
            row["index_sha256"], row["content_units_sha256"],
            row["source_manifest_sha256"],
        )
        if identity in artifact_identities:
            _fail("%s reuses one artifact/index shard for multiple courses" % label)
        artifact_identities.add(identity)
        courses[course_id] = row
        normalized_rows.append(row)
    return courses, tuple(normalized_rows), canonical_sha256(value)


def validate_gold(document):
    """Validate and normalize a retrieval gold document."""

    _strict_object(document, GOLD_FIELDS, "gold")
    if document["schema_version"] != SCHEMA_VERSION:
        _fail("gold.schema_version must be %d" % SCHEMA_VERSION)
    gold_id = _token(document["gold_id"], "gold.gold_id")
    split = _token(document["split"], "gold.split")
    if split not in ("dev", "test"):
        _fail("gold.split must be dev or test")
    index_bundle, index_bundle_rows, index_bundle_sha256 = _validate_index_bundle(
        document["index_bundle"], "gold.index_bundle")
    if not isinstance(document["queries"], list):
        _fail("gold.queries must be an array")

    queries = {}
    previous = None
    for position, raw in enumerate(document["queries"]):
        label = "gold.queries[%d]" % position
        _strict_object(raw, QUERY_FIELDS, label)
        query_id = _token(raw["query_id"], label + ".query_id")
        if query_id in queries:
            _fail("gold contains duplicate query_id %s" % query_id)
        if previous is not None and query_id <= previous:
            _fail("gold queries must be sorted by query_id")
        previous = query_id
        course_id = _token(raw["course_id"], label + ".course_id")
        query = _token(raw["query"], label + ".query")
        language = _token(raw["language"], label + ".language")
        if language not in LANGUAGES:
            _fail("%s.language must be one of %r" % (label, sorted(LANGUAGES)))
        if type(raw["answerable"]) is not bool:
            _fail(label + ".answerable must be a boolean")
        answerable = raw["answerable"]
        relevant = _unique_tokens(
            raw["relevant_unit_ids"], label + ".relevant_unit_ids",
            allow_empty=not answerable,
        )
        if not answerable and relevant:
            _fail(label + " unanswerable query must not declare relevant units")
        hard_negative = _unique_tokens(
            raw["hard_negative_unit_ids"], label + ".hard_negative_unit_ids")
        overlap = sorted(set(relevant).intersection(hard_negative))
        if overlap:
            _fail("%s relevant/hard-negative units overlap: %r" % (label, overlap))
        tags = _unique_tokens(raw["tags"], label + ".tags", allow_empty=False)

        if not isinstance(raw["evidence"], list):
            _fail(label + ".evidence must be an array")
        evidence = []
        evidence_units = set()
        for evidence_position, item in enumerate(raw["evidence"]):
            item_label = "%s.evidence[%d]" % (label, evidence_position)
            _strict_object(item, EVIDENCE_FIELDS, item_label)
            unit_id = _token(item["unit_id"], item_label + ".unit_id")
            if unit_id in evidence_units:
                _fail("%s contains duplicate unit evidence %s" % (label, unit_id))
            evidence_units.add(unit_id)
            evidence.append({
                "unit_id": unit_id,
                "source_id": _token(item["source_id"], item_label + ".source_id"),
                "source_sha256": _sha256(
                    item["source_sha256"], item_label + ".source_sha256"),
                "page": _integer(item["page"], item_label + ".page", minimum=1),
            })
        if evidence_units != set(relevant):
            _fail("%s evidence unit IDs must exactly equal relevant_unit_ids" % label)

        queries[query_id] = {
            "query_id": query_id,
            "course_id": course_id,
            "query": query,
            "language": language,
            "answerable": answerable,
            "relevant_unit_ids": relevant,
            "hard_negative_unit_ids": hard_negative,
            "tags": tags,
            "evidence": tuple(evidence),
        }
    query_courses = {query["course_id"] for query in queries.values()}
    bundle_courses = set(index_bundle)
    if query_courses != bundle_courses:
        _fail("gold query course IDs must exactly equal index bundle course IDs; "
              "missing_queries=%r unknown_queries=%r" % (
                  sorted(bundle_courses - query_courses),
                  sorted(query_courses - bundle_courses),
              ))
    return {
        "schema_version": SCHEMA_VERSION,
        "gold_id": gold_id,
        "split": split,
        "index_bundle": index_bundle,
        "index_bundle_rows": index_bundle_rows,
        "index_bundle_sha256": index_bundle_sha256,
        "queries": queries,
        "canonical_sha256": canonical_sha256(document),
    }


def _validate_component(raw, label):
    _strict_object(raw, COMPONENT_FIELDS, label)
    return {
        "backend": _token(raw["backend"], label + ".backend"),
        "rank": _integer(raw["rank"], label + ".rank", minimum=1),
        "score": _number(raw["score"], label + ".score"),
        "score_kind": _token(raw["score_kind"], label + ".score_kind"),
    }


def _validate_run_shape(document):
    """Validate one run's self-contained schema, without trusting provenance."""

    _strict_object(document, RUN_FIELDS, "run")
    if document["schema_version"] != SCHEMA_VERSION:
        _fail("run.schema_version must be %d" % SCHEMA_VERSION)
    run_id = _token(document["run_id"], "run.run_id")
    gold_sha256 = _sha256(document["gold_sha256"], "run.gold_sha256")
    index_bundle, index_bundle_rows, calculated_bundle_sha256 = _validate_index_bundle(
        document["index_bundle"], "run.index_bundle")
    declared_bundle_sha256 = _sha256(
        document["index_bundle_sha256"], "run.index_bundle_sha256")
    if declared_bundle_sha256 != calculated_bundle_sha256:
        _fail("run.index_bundle_sha256 disagrees with run.index_bundle")

    _strict_object(document["backend"], BACKEND_FIELDS, "run.backend")
    backend = {
        "name": _token(document["backend"]["name"], "run.backend.name"),
        "kind": _token(document["backend"]["kind"], "run.backend.kind"),
        "version": _token(document["backend"]["version"], "run.backend.version"),
        "config_sha256": _sha256(
            document["backend"]["config_sha256"], "run.backend.config_sha256"),
    }
    if backend["kind"] not in BACKEND_KINDS:
        _fail("run.backend.kind must be one of %r" % sorted(BACKEND_KINDS))

    parent = document["parent"]
    if parent is not None:
        _strict_object(parent, PARENT_FIELDS, "run.parent")
        parent = {
            "backend": _token(parent["backend"], "run.parent.backend"),
            "run_sha256": _sha256(parent["run_sha256"], "run.parent.run_sha256"),
        }
    if backend["kind"] == "reranker" and parent is None:
        _fail("reranker run must bind a parent candidate receipt")
    if backend["kind"] != "reranker" and parent is not None:
        _fail("only a reranker run may bind a parent receipt")

    fusion = document["fusion"]
    if fusion is not None:
        _strict_object(fusion, FUSION_FIELDS, "run.fusion")
        method = _token(fusion["method"], "run.fusion.method")
        if method != "rrf":
            _fail("run.fusion.method must be rrf")
        rank_constant = _integer(
            fusion["rank_constant"], "run.fusion.rank_constant", minimum=1)
        window_size = _integer(fusion["window_size"], "run.fusion.window_size", minimum=1)
        top_k = _integer(fusion["top_k"], "run.fusion.top_k", minimum=1)
        if not isinstance(fusion["inputs"], list) or len(fusion["inputs"]) < 2:
            _fail("run.fusion.inputs must contain at least two input receipts")
        inputs = []
        names = set()
        for position, item in enumerate(fusion["inputs"]):
            label = "run.fusion.inputs[%d]" % position
            _strict_object(item, FUSION_INPUT_FIELDS, label)
            name = _token(item["backend"], label + ".backend")
            if name in names:
                _fail("run.fusion.inputs contains duplicate backend %s" % name)
            names.add(name)
            inputs.append({
                "backend": name,
                "result_sha256": _sha256(
                    item["result_sha256"], label + ".result_sha256"),
            })
        fusion = {
            "method": method,
            "rank_constant": rank_constant,
            "window_size": window_size,
            "top_k": top_k,
            "inputs": tuple(inputs),
        }
    if backend["kind"] in ("rrf", "hybrid") and fusion is None:
        _fail("RRF/hybrid run must carry a fusion receipt")
    if backend["kind"] not in ("rrf", "hybrid") and fusion is not None:
        _fail("only RRF/hybrid runs may carry a fusion receipt")

    _strict_object(document["resources"], RESOURCE_FIELDS, "run.resources")
    resources = {
        "query_count": _integer(
            document["resources"]["query_count"], "run.resources.query_count"),
        "indexed_docs": _integer(
            document["resources"]["indexed_docs"], "run.resources.indexed_docs"),
        "index_size_bytes": _integer(
            document["resources"]["index_size_bytes"], "run.resources.index_size_bytes"),
        "p95_latency_ms": _number(
            document["resources"]["p95_latency_ms"],
            "run.resources.p95_latency_ms", minimum=0.0),
        "top5_stability": _number(
            document["resources"]["top5_stability"],
            "run.resources.top5_stability", minimum=0.0, maximum=1.0),
    }
    if not isinstance(document["results"], list):
        _fail("run.results must be an array")
    if resources["query_count"] != len(document["results"]):
        _fail("run.resources.query_count must equal len(run.results)")

    results = {}
    previous = None
    latencies = []
    for position, raw in enumerate(document["results"]):
        label = "run.results[%d]" % position
        _strict_object(raw, RESULT_FIELDS, label)
        query_id = _token(raw["query_id"], label + ".query_id")
        if query_id in results:
            _fail("run contains duplicate query_id %s" % query_id)
        if previous is not None and query_id <= previous:
            _fail("run results must be sorted by query_id")
        previous = query_id
        if type(raw["abstain"]) is not bool:
            _fail(label + ".abstain must be a boolean")
        reason = raw["abstain_reason"]
        if reason is not None:
            reason = _token(reason, label + ".abstain_reason")
        latency = _number(raw["latency_ms"], label + ".latency_ms", minimum=0.0)
        latencies.append(latency)
        if not isinstance(raw["hits"], list):
            _fail(label + ".hits must be an array")
        if raw["abstain"] and (raw["hits"] or reason is None):
            _fail(label + " abstention requires no hits and a reason")
        if not raw["abstain"] and (not raw["hits"] or reason is not None):
            _fail(label + " non-abstention requires hits and null reason")

        hits = []
        doc_ids = set()
        for hit_position, hit in enumerate(raw["hits"]):
            hit_label = "%s.hits[%d]" % (label, hit_position)
            _strict_object(hit, HIT_FIELDS, hit_label)
            doc_id = _token(hit["doc_id"], hit_label + ".doc_id")
            if doc_id in doc_ids:
                _fail("%s contains duplicate doc_id %s" % (label, doc_id))
            doc_ids.add(doc_id)
            rank = _integer(hit["rank"], hit_label + ".rank", minimum=1)
            if rank != hit_position + 1:
                _fail(hit_label + ".rank must be contiguous and one-based")
            units = _unique_tokens(hit["unit_ids"], hit_label + ".unit_ids")
            if not isinstance(hit["components"], list):
                _fail(hit_label + ".components must be an array")
            components = tuple(
                _validate_component(value, "%s.components[%d]" % (hit_label, index))
                for index, value in enumerate(hit["components"])
            )
            hits.append({
                "doc_id": doc_id,
                "rank": rank,
                "unit_ids": units,
                "score": _number(hit["score"], hit_label + ".score"),
                "score_kind": _token(hit["score_kind"], hit_label + ".score_kind"),
                "components": components,
            })
        results[query_id] = {
            "query_id": query_id,
            "abstain": raw["abstain"],
            "abstain_reason": reason,
            "latency_ms": latency,
            "hits": tuple(hits),
        }

    calculated_p95 = percentile(latencies, 0.95)
    if abs(calculated_p95 - resources["p95_latency_ms"]) > 0.001:
        _fail("run.resources.p95_latency_ms disagrees with result latencies")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "gold_sha256": gold_sha256,
        "index_bundle": index_bundle,
        "index_bundle_rows": index_bundle_rows,
        "index_bundle_sha256": declared_bundle_sha256,
        "backend": backend,
        "parent": parent,
        "fusion": fusion,
        "resources": resources,
        "results": results,
        "canonical_sha256": canonical_sha256(document),
    }


def _require_same_run_basis(left, right, label):
    for field in ("gold_sha256", "index_bundle_sha256"):
        if left[field] != right[field]:
            _fail("%s %s does not match the candidate run" % (label, field))
    if set(left["results"]) != set(right["results"]):
        _fail("%s query IDs do not match the candidate run" % label)


def _validate_reranker_parent(run, parent_document):
    if parent_document is None:
        _fail("reranker parent receipt is required for provenance validation")
    parent = _validate_run_shape(parent_document)
    binding = run["parent"]
    if binding["run_sha256"] != parent["canonical_sha256"]:
        _fail("reranker parent receipt hash does not match the bound parent")
    if binding["backend"] != parent["backend"]["name"]:
        _fail("reranker parent backend does not match the bound parent")
    _require_same_run_basis(parent, run, "reranker parent")
    for query_id in sorted(run["results"]):
        child_hits = {hit["doc_id"]: hit for hit in run["results"][query_id]["hits"]}
        parent_hits = {hit["doc_id"]: hit for hit in parent["results"][query_id]["hits"]}
        if set(child_hits) != set(parent_hits):
            _fail("reranker query %s changed its parent doc ID pool; missing=%r unknown=%r" % (
                query_id, sorted(set(parent_hits) - set(child_hits)),
                sorted(set(child_hits) - set(parent_hits)),
            ))
        for doc_id in sorted(child_hits):
            if child_hits[doc_id]["unit_ids"] != parent_hits[doc_id]["unit_ids"]:
                _fail("reranker query %s changed unit identity for doc %s" % (
                    query_id, doc_id))


def _recompute_rrf_hits(inputs, query_id, fusion):
    """Rebuild one complete RRF result from the directly bound leaf receipts."""

    fused = {}
    identities = {}
    for backend in sorted(inputs):
        source_result = inputs[backend]["results"][query_id]
        for source_hit in source_result["hits"][:fusion["window_size"]]:
            doc_id = source_hit["doc_id"]
            unit_ids = source_hit["unit_ids"]
            if doc_id in identities and identities[doc_id] != unit_ids:
                _fail(
                    "RRF/hybrid query %s doc %s has inconsistent unit identity across "
                    "bound inputs" % (query_id, doc_id))
            identities[doc_id] = unit_ids
            target = fused.setdefault(doc_id, {
                "doc_id": doc_id,
                "unit_ids": unit_ids,
                "score": 0.0,
                "components": [],
            })
            target["score"] += 1.0 / float(
                fusion["rank_constant"] + source_hit["rank"])
            target["components"].append({
                "backend": backend,
                "rank": source_hit["rank"],
                "score": source_hit["score"],
                "score_kind": source_hit["score_kind"],
            })

    ordered = sorted(fused.values(), key=lambda row: (-row["score"], row["doc_id"]))
    expected = []
    for rank, row in enumerate(ordered[:fusion["top_k"]], 1):
        expected.append({
            "doc_id": row["doc_id"],
            "rank": rank,
            "unit_ids": row["unit_ids"],
            "score": round(row["score"], 12),
            "score_kind": "rrf",
            "components": tuple(sorted(
                row["components"], key=lambda value: value["backend"])),
        })
    return tuple(expected)


def _validate_fusion_inputs(run, input_documents):
    if input_documents is None:
        _fail("RRF/hybrid input receipts are required for provenance validation")
    if not isinstance(input_documents, (list, tuple)):
        _fail("RRF/hybrid input receipts must be an array")
    inputs = {}
    for position, document in enumerate(input_documents):
        value = _validate_run_shape(document)
        name = value["backend"]["name"]
        if name in inputs:
            _fail("RRF/hybrid input receipts contain duplicate backend %s" % name)
        if value["backend"]["kind"] not in ("bm25", "dense"):
            _fail(
                "RRF/hybrid input %s is not a directly bound leaf receipt; nested "
                "RRF/hybrid/reranker inputs are not supported" % name)
        inputs[name] = value
    declared = {item["backend"]: item["result_sha256"] for item in run["fusion"]["inputs"]}
    if set(inputs) != set(declared):
        _fail("RRF/hybrid real input backends do not exactly match fusion.inputs")
    for name, value in inputs.items():
        if declared[name] != value["canonical_sha256"]:
            _fail("RRF/hybrid input receipt hash mismatch for backend %s" % name)
        _require_same_run_basis(value, run, "RRF/hybrid input %s" % name)
    for query_id, result in run["results"].items():
        expected_hits = _recompute_rrf_hits(inputs, query_id, run["fusion"])
        expected_abstain = not expected_hits
        if result["abstain"] is not expected_abstain:
            _fail(
                "RRF/hybrid query %s abstain disagrees with deterministic RRF" % query_id)
        if len(result["hits"]) != len(expected_hits):
            _fail(
                "RRF/hybrid query %s output set disagrees with deterministic RRF" % query_id)
        for position, (actual, expected) in enumerate(
                zip(result["hits"], expected_hits), 1):
            for field in (
                    "doc_id", "rank", "unit_ids", "score", "score_kind", "components"):
                if actual[field] != expected[field]:
                    _fail(
                        "RRF/hybrid query %s hit %d field %s disagrees with "
                        "deterministic RRF" % (query_id, position, field))


def validate_run_receipt(document, gold_document=None, parent_document=None,
                         input_documents=None):
    """Validate a run plus its frozen gold and real parent/input receipts."""

    run = _validate_run_shape(document)
    if gold_document is not None:
        gold = validate_gold(gold_document)
        if run["gold_sha256"] != gold["canonical_sha256"]:
            _fail("run gold_sha256 does not bind the supplied gold document")
        if run["index_bundle_sha256"] != gold["index_bundle_sha256"]:
            _fail("run index bundle does not bind the gold index bundle")
        if run["index_bundle_rows"] != gold["index_bundle_rows"]:
            _fail("run index bundle rows do not exactly equal the gold bundle")
        expected = set(gold["queries"])
        actual = set(run["results"])
        if actual != expected:
            _fail("run query IDs must exactly equal gold query IDs; missing=%r unknown=%r" % (
                sorted(expected - actual), sorted(actual - expected)))
    if run["backend"]["kind"] == "reranker":
        _validate_reranker_parent(run, parent_document)
    elif parent_document is not None:
        _fail("parent_document is valid only for reranker runs")
    if run["backend"]["kind"] in ("rrf", "hybrid"):
        _validate_fusion_inputs(run, input_documents)
    elif input_documents is not None:
        _fail("input_documents are valid only for RRF/hybrid runs")
    return run


def validate_index_bindings(gold_document, index, course_id=None):
    """Require each course's labels to resolve only through its own index shard."""

    gold = validate_gold(gold_document)
    if course_id is not None:
        course_id = _token(course_id, "course_id")
        if course_id not in gold["index_bundle"]:
            _fail("course_id is not present in the gold index bundle")
    if not isinstance(index, dict) or not isinstance(index.get("docs"), list):
        _fail("retrieval index must contain docs")
    indexed = set()
    answer_indexed = set()
    duplicate_doc_ids = set()
    seen_docs = set()
    for position, doc in enumerate(index["docs"]):
        if not isinstance(doc, dict):
            _fail("retrieval index docs[%d] must be an object" % position)
        doc_id = _token(doc.get("id"), "retrieval index docs[%d].id" % position)
        if doc_id in seen_docs:
            duplicate_doc_ids.add(doc_id)
        seen_docs.add(doc_id)
        units = doc.get("unit_ids") or []
        if not isinstance(units, list):
            _fail("retrieval index docs[%d].unit_ids must be an array" % position)
        for unit_id in units:
            unit_id = _token(unit_id, "retrieval index docs[%d].unit_ids" % position)
            indexed.add(unit_id)
            if doc.get("kind") == "answer":
                answer_indexed.add(unit_id)
    if duplicate_doc_ids:
        _fail("retrieval index contains duplicate doc IDs: %r" % sorted(duplicate_doc_ids))
    labeled = set()
    relevant = set()
    selected_queries = [
        query for query in gold["queries"].values()
        if course_id is None or query["course_id"] == course_id
    ]
    for query in selected_queries:
        relevant.update(query["relevant_unit_ids"])
        labeled.update(query["relevant_unit_ids"])
        labeled.update(query["hard_negative_unit_ids"])
    missing = sorted(labeled - indexed)
    if missing:
        _fail("gold units do not resolve to indexed chunks: %r" % missing)
    leaked_answers = sorted(relevant.intersection(answer_indexed))
    if leaked_answers:
        _fail("gold relevant units resolve through answer-side chunks: %r" % leaked_answers)
    return {"indexed_labeled_units": len(labeled), "indexed_docs": len(seen_docs)}


def _mean(values):
    return sum(values) / float(len(values)) if values else 0.0


def percentile(values, fraction):
    """Nearest-rank percentile with deterministic empty-set behavior."""

    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = max(1, int(math.ceil(float(fraction) * len(ordered))))
    return round(ordered[min(len(ordered), rank) - 1], 6)


def _query_metrics(query, result):
    relevant = set(query["relevant_unit_ids"])
    hard_negative = set(query["hard_negative_unit_ids"])
    hits = result["hits"] if result is not None else ()
    ranked_units = [set(hit["unit_ids"]) for hit in hits]
    if not query["answerable"]:
        false_accept = int(result is not None and not result["abstain"])
        return {
            "query_id": query["query_id"], "answerable": False,
            "recall_at_1": 0.0, "recall_at_5": 0.0,
            "hit_at_1": 0, "hit_at_5": 0, "mrr": 0.0,
            "hard_negative_intrusion_at_5": 0,
            "false_accept": false_accept, "abstained": 1 - false_accept,
            "missing_result": int(result is None),
        }

    def recall_at(limit):
        found = set()
        for units in ranked_units[:limit]:
            found.update(units.intersection(relevant))
        return len(found) / float(len(relevant))

    first_relevant = None
    first_hard_negative = None
    for rank, units in enumerate(ranked_units, 1):
        if first_relevant is None and units.intersection(relevant):
            first_relevant = rank
        if first_hard_negative is None and units.intersection(hard_negative):
            first_hard_negative = rank
    return {
        "query_id": query["query_id"], "answerable": True,
        "recall_at_1": recall_at(1), "recall_at_5": recall_at(5),
        "hit_at_1": int(first_relevant == 1),
        "hit_at_5": int(first_relevant is not None and first_relevant <= 5),
        "mrr": 1.0 / float(first_relevant) if first_relevant is not None else 0.0,
        "hard_negative_intrusion_at_5": int(
            first_hard_negative is not None and first_hard_negative <= 5),
        "false_accept": 0, "abstained": int(result is None or result["abstain"]),
        "missing_result": int(result is None),
    }


def evaluate(gold_document, run_document, parent_document=None, input_documents=None):
    """Evaluate one backend receipt against the exact frozen gold queries."""

    gold = validate_gold(gold_document)
    run = validate_run_receipt(
        run_document, gold_document=gold_document, parent_document=parent_document,
        input_documents=input_documents)

    per_query = []
    slices = {}
    courses = {}
    for query_id in sorted(gold["queries"]):
        query = gold["queries"][query_id]
        row = _query_metrics(query, run["results"].get(query_id))
        per_query.append(row)
        if query["answerable"]:
            courses.setdefault(query["course_id"], []).append(row)
            for tag in query["tags"]:
                slices.setdefault(tag, []).append(row)

    answerable = [row for row in per_query if row["answerable"]]
    oos = [row for row in per_query if not row["answerable"]]

    def aggregate(rows):
        return {
            "queries": len(rows),
            "recall_at_1": round(_mean([row["recall_at_1"] for row in rows]), 6),
            "recall_at_5": round(_mean([row["recall_at_5"] for row in rows]), 6),
            "hit_at_1": round(_mean([row["hit_at_1"] for row in rows]), 6),
            "hit_at_5": round(_mean([row["hit_at_5"] for row in rows]), 6),
            "mrr": round(_mean([row["mrr"] for row in rows]), 6),
            "hard_negative_intrusion_at_5": round(_mean([
                row["hard_negative_intrusion_at_5"] for row in rows]), 6),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "gold_id": gold["gold_id"],
        "run_id": run["run_id"],
        "backend": run["backend"],
        "overall": aggregate(answerable),
        "oos": {
            "queries": len(oos),
            "false_accepts": sum(row["false_accept"] for row in oos),
            "abstention_rate": round(_mean([row["abstained"] for row in oos]), 6),
        },
        "accounting": {
            "gold_queries": len(per_query),
            "returned_queries": len(run["results"]),
            "missing_queries": sum(row["missing_result"] for row in per_query),
        },
        "per_slice": {key: aggregate(value) for key, value in sorted(slices.items())},
        "per_course": {key: aggregate(value) for key, value in sorted(courses.items())},
        "resources": dict(run["resources"]),
        "per_query": per_query,
    }


def _paired_bootstrap(left, right, resamples=5000, seed=20260714):
    if len(left) != len(right):
        _fail("paired comparison vectors have different lengths")
    observed = _mean(right) - _mean(left)
    if not left:
        return {"delta": 0.0, "ci95": [0.0, 0.0], "resamples": resamples}
    rng = random.Random(seed)
    values = []
    for _unused in range(resamples):
        indexes = [rng.randrange(len(left)) for _item in left]
        values.append(_mean([right[index] for index in indexes])
                      - _mean([left[index] for index in indexes]))
    values.sort()
    low = values[max(0, int(0.025 * resamples))]
    high = values[min(resamples - 1, int(0.975 * resamples) - 1)]
    return {
        "delta": round(observed, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "resamples": resamples,
    }


def _mcnemar_exact(left, right):
    if len(left) != len(right):
        _fail("paired binary vectors have different lengths")
    baseline_only = sum(1 for a, b in zip(left, right) if a and not b)
    candidate_only = sum(1 for a, b in zip(left, right) if b and not a)
    discordant = baseline_only + candidate_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = min(baseline_only, candidate_only)
        probability = sum(math.comb(discordant, value) for value in range(tail + 1))
        p_value = min(1.0, 2.0 * probability / float(2 ** discordant))
    return {
        "baseline_only": baseline_only,
        "candidate_only": candidate_only,
        "discordant": discordant,
        "p_value": round(p_value, 8),
    }


def compare(gold_document, baseline_document, candidate_document, resamples=5000,
            baseline_input_documents=None, candidate_input_documents=None):
    """Return paired candidate-minus-baseline effects on the same gold set."""

    baseline = evaluate(
        gold_document, baseline_document, input_documents=baseline_input_documents)
    candidate_kind = _validate_run_shape(candidate_document)["backend"]["kind"]
    candidate = evaluate(
        gold_document, candidate_document,
        parent_document=baseline_document if candidate_kind == "reranker" else None,
        input_documents=candidate_input_documents)
    left_rows = [row for row in baseline["per_query"] if row["answerable"]]
    right_rows = [row for row in candidate["per_query"] if row["answerable"]]
    if [row["query_id"] for row in left_rows] != [row["query_id"] for row in right_rows]:
        _fail("paired runs do not cover the same answerable query IDs")

    comparison = {}
    for field in ("recall_at_1", "recall_at_5", "mrr"):
        comparison[field] = _paired_bootstrap(
            [row[field] for row in left_rows],
            [row[field] for row in right_rows],
            resamples=resamples,
        )
    comparison["hit_at_1_mcnemar"] = _mcnemar_exact(
        [row["hit_at_1"] for row in left_rows],
        [row["hit_at_1"] for row in right_rows],
    )
    comparison["hit_at_5_mcnemar"] = _mcnemar_exact(
        [row["hit_at_5"] for row in left_rows],
        [row["hit_at_5"] for row in right_rows],
    )
    comparison["slice_delta_recall_at_5"] = {
        tag: round(
            candidate["per_slice"].get(tag, {}).get("recall_at_5", 0.0)
            - baseline["per_slice"].get(tag, {}).get("recall_at_5", 0.0), 6)
        for tag in sorted(set(baseline["per_slice"]) | set(candidate["per_slice"]))
    }
    return {"baseline": baseline, "candidate": candidate, "comparison": comparison}


def _thresholds(overrides):
    result = dict(DEFAULT_THRESHOLDS)
    if overrides is None:
        return result
    if not isinstance(overrides, dict):
        _fail("thresholds must be an object")
    unknown = sorted(set(overrides) - set(result))
    if unknown:
        _fail("unknown retrieval thresholds: %r" % unknown)
    for name, value in overrides.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            _fail("retrieval threshold %s must be a finite non-negative number" % name)
        if name in _INTEGER_THRESHOLDS and type(value) is not int:
            _fail("retrieval threshold %s must be an integer" % name)
        baseline = DEFAULT_THRESHOLDS[name]
        if name in _ONLY_RAISE_THRESHOLDS and value < baseline:
            _fail("retrieval threshold %s may only be made stricter (>= %s)" % (
                name, baseline))
        if name in _ONLY_LOWER_THRESHOLDS and value > baseline:
            _fail("retrieval threshold %s may only be made stricter (<= %s)" % (
                name, baseline))
    result.update(overrides)
    return result


def evidence_sufficiency(gold_document, thresholds=None):
    """Check whether a frozen test set can support a promotion decision."""

    gold = validate_gold(gold_document)
    limits = _thresholds(thresholds)
    queries = list(gold["queries"].values())
    answerable = [query for query in queries if query["answerable"]]
    oos = [query for query in queries if not query["answerable"]]
    reasons = []
    if gold["split"] != "test":
        reasons.append("gold_split_not_test")
    if len(queries) < limits["min_total_queries"]:
        reasons.append("too_few_total_queries")
    if len(answerable) < limits["min_answerable_queries"]:
        reasons.append("too_few_answerable_queries")
    if len(oos) < limits["min_oos_queries"]:
        reasons.append("too_few_near_miss_oos_queries")
    course_counts = {}
    for query in answerable:
        course_counts[query["course_id"]] = course_counts.get(query["course_id"], 0) + 1
    if len(course_counts) < limits["min_courses"]:
        reasons.append("too_few_courses")
    underfilled_courses = sorted(
        course for course, count in course_counts.items()
        if count < limits["min_answerable_per_course"])
    if underfilled_courses:
        reasons.append("course_answerable_floor_not_met")
    slice_counts = {
        tag: sum(tag in query["tags"] for query in answerable)
        for tag in CRITICAL_SLICES
    }
    underfilled_slices = sorted(
        tag for tag, count in slice_counts.items()
        if count < limits["min_queries_per_critical_slice"])
    if underfilled_slices:
        reasons.append("critical_slice_floor_not_met")
    return {
        "sufficient": not reasons,
        "reasons": reasons,
        "counts": {
            "total": len(queries), "answerable": len(answerable), "oos": len(oos),
            "courses": course_counts, "critical_slices": slice_counts,
            "underfilled_courses": underfilled_courses,
            "underfilled_slices": underfilled_slices,
        },
    }


def _operability_failures(baseline, candidate, limits):
    failures = []
    resources = candidate["resources"]
    base_resources = baseline["resources"]
    if resources["p95_latency_ms"] > limits["max_p95_latency_ms"]:
        failures.append("candidate_p95_latency_too_high")
    if (base_resources["p95_latency_ms"] > 0
            and resources["p95_latency_ms"]
            > base_resources["p95_latency_ms"] * limits["max_latency_multiple"]):
        failures.append("candidate_latency_multiple_too_high")
    if (base_resources["index_size_bytes"] > 0
            and resources["index_size_bytes"]
            > base_resources["index_size_bytes"] * limits["max_index_size_multiple"]):
        failures.append("candidate_index_size_multiple_too_high")
    if resources["indexed_docs"] > 0:
        normalized = (resources["index_size_bytes"] * 100000.0
                      / resources["indexed_docs"])
        if normalized > limits["max_index_bytes_per_100k_docs"]:
            failures.append("candidate_index_bytes_per_100k_docs_too_high")
    if resources["top5_stability"] < limits["min_top5_stability"]:
        failures.append("candidate_top5_stability_too_low")
    return failures


def _safety_and_slice_failures(report, limits):
    baseline = report["baseline"]
    candidate = report["candidate"]
    failures = []
    if candidate["accounting"]["missing_queries"]:
        failures.append("candidate_missing_query_results")
    if candidate["oos"]["false_accepts"] > limits["max_false_accepts"]:
        failures.append("candidate_oos_false_accepts")
    candidate_intrusion = candidate["overall"]["hard_negative_intrusion_at_5"]
    baseline_intrusion = baseline["overall"]["hard_negative_intrusion_at_5"]
    if candidate_intrusion > limits["max_hard_negative_intrusion_at_5"]:
        failures.append("candidate_hard_negative_intrusion_too_high")
    if candidate_intrusion > baseline_intrusion + 1e-12:
        failures.append("candidate_hard_negative_intrusion_regressed")
    for tag in CRITICAL_SLICES:
        candidate_slice = candidate["per_slice"].get(tag)
        baseline_slice = baseline["per_slice"].get(tag)
        if candidate_slice is None or baseline_slice is None:
            failures.append("critical_slice_missing:%s" % tag)
            continue
        if candidate_slice["recall_at_5"] < limits["candidate_slice_floor"]:
            failures.append("candidate_slice_below_floor:%s" % tag)
        if (candidate_slice["recall_at_5"]
                < baseline_slice["recall_at_5"] - limits["max_slice_regression"]):
            failures.append("candidate_slice_regressed:%s" % tag)
    failures.extend(_operability_failures(baseline, candidate, limits))
    return failures


def decide(gold_document, baseline_document=None, candidate_document=None,
           thresholds=None, resamples=5000, baseline_input_documents=None,
           candidate_input_documents=None):
    """Return a fail-closed three-state backend promotion decision."""

    limits = _thresholds(thresholds)
    sufficiency = evidence_sufficiency(gold_document, limits)
    base = {
        "schema_version": SCHEMA_VERSION,
        "decision": DECISION_INSUFFICIENT,
        "sufficiency": sufficiency,
        "reasons": list(sufficiency["reasons"]),
        "thresholds": limits,
    }
    if not sufficiency["sufficient"]:
        return base
    if baseline_document is None:
        base["reasons"] = ["baseline_receipt_missing"]
        return base
    if candidate_document is None:
        base["reasons"] = ["candidate_receipt_missing"]
        return base

    report = compare(
        gold_document, baseline_document, candidate_document, resamples=resamples,
        baseline_input_documents=baseline_input_documents,
        candidate_input_documents=candidate_input_documents)
    base["report"] = report
    baseline = report["baseline"]
    candidate = report["candidate"]
    comparison = report["comparison"]
    if baseline["backend"]["kind"] != "bm25":
        base.update({"decision": DECISION_NO_GO,
                     "reasons": ["baseline_is_not_bm25"]})
        return base

    if candidate["backend"]["kind"] == "reranker":
        failures = _safety_and_slice_failures(report, limits)
        recall_one = comparison["recall_at_1"]
        mrr = comparison["mrr"]
        if recall_one["delta"] < limits["reranker_min_delta_recall_at_1"]:
            failures.append("reranker_recall_at_1_gain_too_small")
        if recall_one["ci95"][0] <= 0:
            failures.append("reranker_recall_at_1_ci_not_positive")
        if mrr["delta"] < limits["reranker_min_delta_mrr"]:
            failures.append("reranker_mrr_gain_too_small")
        if mrr["ci95"][0] <= 0:
            failures.append("reranker_mrr_ci_not_positive")
        if comparison["hit_at_1_mcnemar"]["p_value"] >= limits["max_p_value"]:
            failures.append("reranker_hit_at_1_not_significant")
        if candidate["overall"]["recall_at_5"] < baseline["overall"]["recall_at_5"]:
            failures.append("reranker_recall_at_5_regressed")
        base.update({
            "decision": DECISION_NO_GO if failures else DECISION_GO_OPTIONAL,
            "reasons": failures or ["reranker_passed_optional_gate"],
        })
        return base

    baseline_slices_adequate = all(
        baseline["per_slice"].get(tag, {}).get("recall_at_5", 0.0)
        >= limits["baseline_slice_floor"] for tag in CRITICAL_SLICES)
    if (baseline["overall"]["recall_at_5"] >= limits["baseline_recall_at_5_floor"]
            and baseline_slices_adequate):
        base.update({"decision": DECISION_NO_GO,
                     "reasons": ["bm25_adequate_no_heavy_backend_needed"]})
        return base
    if candidate["backend"]["kind"] not in ("rrf", "hybrid"):
        base.update({"decision": DECISION_NO_GO,
                     "reasons": ["candidate_kind_is_experiment_only"]})
        return base

    failures = _safety_and_slice_failures(report, limits)
    recall_five = comparison["recall_at_5"]
    if recall_five["delta"] < limits["min_delta_recall_at_5"]:
        failures.append("candidate_recall_at_5_gain_too_small")
    if recall_five["ci95"][0] <= 0:
        failures.append("candidate_recall_at_5_ci_not_positive")
    if comparison["hit_at_5_mcnemar"]["p_value"] >= limits["max_p_value"]:
        failures.append("candidate_hit_at_5_not_significant")
    if candidate["overall"]["recall_at_5"] < limits["candidate_recall_at_5_floor"]:
        failures.append("candidate_recall_at_5_below_floor")
    if comparison["mrr"]["delta"] < -limits["max_mrr_regression"]:
        failures.append("candidate_mrr_regressed")
    base.update({
        "decision": DECISION_NO_GO if failures else DECISION_GO_OPTIONAL,
        "reasons": failures or ["hybrid_passed_optional_gate"],
    })
    return base


__all__ = [
    "BACKEND_KINDS", "CRITICAL_SLICES", "DECISIONS", "DECISION_GO_OPTIONAL",
    "DECISION_INSUFFICIENT", "DECISION_NO_GO", "DEFAULT_THRESHOLDS",
    "RetrievalEvaluationError", "canonical_sha256", "compare", "decide",
    "evidence_sufficiency", "evaluate", "percentile", "validate_gold",
    "validate_index_bindings", "validate_run_receipt",
]
