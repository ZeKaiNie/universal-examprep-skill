#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Experiment-only retrieval candidates used by the evidence gate.

Nothing in this module is imported by the production BM25 CLI.  Dense search
and reranking are injected as callables by a maintainer-controlled experiment;
there is no model download, network access, dynamic module import, or fallback
that could accidentally relabel BM25 output as a candidate run.
"""

import math


class CandidateError(ValueError):
    """An experimental backend violated the candidate result contract."""


def _token(value, label):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(char in value for char in ("\x00", "\r", "\n"))):
        raise CandidateError("%s must be a non-empty single-line string" % label)
    return value


def _score(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise CandidateError("%s must be a finite number" % label)
    return float(value)


def _units(values, label):
    if not isinstance(values, (list, tuple)):
        raise CandidateError("%s must be an array" % label)
    result = []
    seen = set()
    for position, value in enumerate(values):
        value = _token(value, "%s[%d]" % (label, position))
        if value in seen:
            raise CandidateError("%s contains duplicate unit %s" % (label, value))
        seen.add(value)
        result.append(value)
    return tuple(result)


def _normalize_ranked_hits(hits, backend):
    if not isinstance(hits, (list, tuple)):
        raise CandidateError("%s results must be an array" % backend)
    result = []
    seen = set()
    for position, raw in enumerate(hits):
        label = "%s[%d]" % (backend, position)
        if not isinstance(raw, dict):
            raise CandidateError("%s must be an object" % label)
        required = {"doc_id", "rank", "unit_ids", "score", "score_kind", "components"}
        if set(raw) != required:
            raise CandidateError("%s fields must be exactly %r" % (label, sorted(required)))
        doc_id = _token(raw["doc_id"], label + ".doc_id")
        if doc_id in seen:
            raise CandidateError("%s contains duplicate doc_id %s" % (backend, doc_id))
        seen.add(doc_id)
        if type(raw["rank"]) is not int or raw["rank"] != position + 1:
            raise CandidateError("%s rank must be contiguous and one-based" % label)
        if not isinstance(raw["components"], (list, tuple)):
            raise CandidateError(label + ".components must be an array")
        result.append({
            "doc_id": doc_id,
            "rank": position + 1,
            "unit_ids": _units(raw["unit_ids"], label + ".unit_ids"),
            "score": _score(raw["score"], label + ".score"),
            "score_kind": _token(raw["score_kind"], label + ".score_kind"),
            "components": [dict(value) for value in raw["components"]],
        })
    return result


def rrf(result_sets, rank_constant=60, window_size=50, top_k=5):
    """Fuse named ranked lists with reciprocal rank fusion.

    ``score(d) = sum(1 / (rank_constant + rank_i(d)))``.  Raw component
    scores are preserved only as provenance; they are never normalized or
    compared.  Exact fused-score ties break by stable ``doc_id``.
    """

    if not isinstance(result_sets, dict) or len(result_sets) < 2:
        raise CandidateError("RRF requires at least two named result sets")
    for label, value in (("rank_constant", rank_constant), ("window_size", window_size),
                         ("top_k", top_k)):
        if type(value) is not int or value < 1:
            raise CandidateError("%s must be a positive integer" % label)

    fused = {}
    identities = {}
    for backend in sorted(result_sets):
        backend_name = _token(backend, "result backend")
        rows = _normalize_ranked_hits(result_sets[backend], backend_name)[:window_size]
        for row in rows:
            doc_id = row["doc_id"]
            identity = row["unit_ids"]
            if doc_id in identities and identities[doc_id] != identity:
                raise CandidateError(
                    "doc_id %s has inconsistent unit_ids across backends" % doc_id)
            identities[doc_id] = identity
            target = fused.setdefault(doc_id, {
                "doc_id": doc_id,
                "unit_ids": identity,
                "score": 0.0,
                "components": [],
            })
            target["score"] += 1.0 / float(rank_constant + row["rank"])
            target["components"].append({
                "backend": backend_name,
                "rank": row["rank"],
                "score": row["score"],
                "score_kind": row["score_kind"],
            })

    ordered = sorted(fused.values(), key=lambda row: (-row["score"], row["doc_id"]))
    output = []
    for rank, row in enumerate(ordered[:top_k], 1):
        output.append({
            "doc_id": row["doc_id"],
            "rank": rank,
            "unit_ids": list(row["unit_ids"]),
            "score": round(row["score"], 12),
            "score_kind": "rrf",
            "components": sorted(row["components"], key=lambda value: value["backend"]),
        })
    return output


def rerank(query, hits, scorer, top_k=None, backend_name="reranker"):
    """Rerank an existing candidate pool without permitting new/omitted IDs.

    ``scorer`` receives ``(query, immutable_hit_tuple)`` and must return a
    mapping from every existing ``doc_id`` to one finite score.  Requiring an
    exact key set makes backend failure loud instead of silently keeping only a
    convenient subset.
    """

    query = _token(query, "query")
    backend_name = _token(backend_name, "backend_name")
    rows = _normalize_ranked_hits(hits, "candidate_pool")
    if top_k is None:
        top_k = len(rows)
    if type(top_k) is not int or top_k < 1:
        raise CandidateError("top_k must be a positive integer")
    if top_k != len(rows):
        raise CandidateError(
            "reranker cannot truncate or expand its bound parent candidate pool")
    if not callable(scorer):
        raise CandidateError("scorer must be callable")
    frozen = tuple({
        "doc_id": row["doc_id"],
        "rank": row["rank"],
        "unit_ids": tuple(row["unit_ids"]),
        "score": row["score"],
        "score_kind": row["score_kind"],
        "components": tuple(dict(value) for value in row["components"]),
    } for row in rows)
    scores = scorer(query, frozen)
    if not isinstance(scores, dict):
        raise CandidateError("reranker scorer must return a doc_id -> score object")
    expected = {row["doc_id"] for row in rows}
    actual = set(scores)
    if actual != expected:
        raise CandidateError("reranker scorer changed candidate identity; missing=%r unknown=%r" % (
            sorted(expected - actual), sorted(actual - expected)))
    rescored = []
    for row in rows:
        value = _score(scores[row["doc_id"]], "reranker score for " + row["doc_id"])
        updated = dict(row)
        updated["reranker_score"] = value
        rescored.append(updated)
    rescored.sort(key=lambda row: (-row["reranker_score"], row["doc_id"]))
    output = []
    for rank, row in enumerate(rescored[:top_k], 1):
        components = list(row["components"])
        components.append({
            "backend": backend_name,
            "rank": rank,
            "score": row["reranker_score"],
            "score_kind": "reranker",
        })
        output.append({
            "doc_id": row["doc_id"],
            "rank": rank,
            "unit_ids": list(row["unit_ids"]),
            "score": row["reranker_score"],
            "score_kind": "reranker",
            "components": components,
        })
    return output


def top_k_stability(repeated_results, top_k=5):
    """Fraction of queries whose top-k *sets* match across every repeat."""

    if type(top_k) is not int or top_k < 1:
        raise CandidateError("top_k must be a positive integer")
    if not isinstance(repeated_results, (list, tuple)) or len(repeated_results) < 2:
        raise CandidateError("stability requires at least two repeated runs")
    normalized = []
    query_ids = None
    for repeat_position, repeat in enumerate(repeated_results):
        if not isinstance(repeat, dict):
            raise CandidateError("repeat %d must be a query_id -> hits object" % repeat_position)
        current_ids = set(repeat)
        if query_ids is None:
            query_ids = current_ids
        elif current_ids != query_ids:
            raise CandidateError("stability repeats cover different query IDs")
        normalized.append({
            query_id: frozenset(
                _token(hit.get("doc_id"), "stability doc_id")
                for hit in (repeat[query_id] or [])[:top_k]
            )
            for query_id in sorted(repeat)
        })
    if not query_ids:
        return 1.0
    stable = sum(
        all(repeat[query_id] == normalized[0][query_id] for repeat in normalized[1:])
        for query_id in sorted(query_ids)
    )
    return round(stable / float(len(query_ids)), 6)


class CallableCandidate:
    """Explicitly injected experimental backend; never dynamically imported."""

    def __init__(self, name, kind, version, search_callable):
        self.name = _token(name, "candidate name")
        self.kind = _token(kind, "candidate kind")
        self.version = _token(version, "candidate version")
        if kind not in ("dense", "rrf", "hybrid", "reranker"):
            raise CandidateError("candidate kind is not experimental: %s" % kind)
        if not callable(search_callable):
            raise CandidateError("search_callable must be callable")
        self._search = search_callable

    def search(self, query, top_k=5):
        query = _token(query, "query")
        if type(top_k) is not int or top_k < 1:
            raise CandidateError("top_k must be a positive integer")
        rows = self._search(query, top_k)
        return _normalize_ranked_hits(rows, self.name)[:top_k]


__all__ = [
    "CallableCandidate", "CandidateError", "rerank", "rrf", "top_k_stability",
]
