import copy
import json
import os
import unittest

from benchmark import retrieval_candidates as candidates
from scripts import retrieval_evaluation as evaluation


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def digest(number):
    return ("%02x" % number) * 32


def bundle(course_ids):
    return [{
        "course_id": course_id,
        "index_sha256": digest(10 + position * 3),
        "content_units_sha256": digest(11 + position * 3),
        "source_manifest_sha256": digest(12 + position * 3),
    } for position, course_id in enumerate(sorted(course_ids))]


def small_gold():
    return {
        "schema_version": 1,
        "gold_id": "small",
        "split": "test",
        "index_bundle": bundle(["c1"]),
        "queries": [
            {
                "query_id": "q001", "course_id": "c1", "query": "alpha concept",
                "language": "en", "answerable": True,
                "relevant_unit_ids": ["u1"], "hard_negative_unit_ids": ["h1"],
                "tags": ["paraphrase"],
                "evidence": [{"unit_id": "u1", "source_id": "s1",
                              "source_sha256": HEX_B, "page": 1}],
            },
            {
                "query_id": "q002", "course_id": "c1", "query": "near miss alpha",
                "language": "en", "answerable": False,
                "relevant_unit_ids": [], "hard_negative_unit_ids": ["u1"],
                "tags": ["near_miss_oos"], "evidence": [],
            },
        ],
    }


def large_gold():
    queries = []
    for index in range(140):
        query_id = "q%03d" % index
        unit = "u%03d" % index
        queries.append({
            "query_id": query_id,
            "course_id": "course-%d" % (index % 3),
            "query": "source-backed query %d" % index,
            "language": ("en", "zh", "bilingual")[index % 3],
            "answerable": True,
            "relevant_unit_ids": [unit],
            "hard_negative_unit_ids": ["h%03d" % index],
            "tags": [evaluation.CRITICAL_SLICES[index % len(evaluation.CRITICAL_SLICES)]],
            "evidence": [{
                "unit_id": unit, "source_id": "source-%d" % (index % 3),
                "source_sha256": ("%x" % ((index % 6) + 1)) * 64, "page": index + 1,
            }],
        })
    for index in range(40):
        queries.append({
            "query_id": "z%03d" % index,
            "course_id": "course-%d" % (index % 3),
            "query": "near-miss unsupported parameter %d" % index,
            "language": ("en", "zh")[index % 2],
            "answerable": False,
            "relevant_unit_ids": [],
            "hard_negative_unit_ids": ["u%03d" % index],
            "tags": ["near_miss_oos"], "evidence": [],
        })
    return {
        "schema_version": 1, "gold_id": "promotion-test", "split": "test",
        "index_bundle": bundle(["course-0", "course-1", "course-2"]),
        "queries": queries,
    }


def component(backend, rank, score, kind):
    return {"backend": backend, "rank": rank, "score": score, "score_kind": kind}


def make_run(gold, kind="bm25", misses=(), false_accept_oos=False,
             latency=1.0, index_size=1000, stability=1.0,
             fusion_inputs=None, parent=None):
    misses = set(misses)
    results = []
    for query in gold["queries"]:
        query_id = query["query_id"]
        if not query["answerable"]:
            if false_accept_oos:
                hits = [{
                    "doc_id": "oos-" + query_id, "rank": 1,
                    "unit_ids": list(query["hard_negative_unit_ids"]),
                    "score": 1.0, "score_kind": kind,
                    "components": [component(kind, 1, 1.0, kind)],
                }]
            else:
                hits = []
        elif query_id in misses:
            hits = [{
                "doc_id": "noise-" + query_id, "rank": 1,
                "unit_ids": ["noise-unit-" + query_id],
                "score": 0.1, "score_kind": kind,
                "components": [component(kind, 1, 0.1, kind)],
            }]
        else:
            hits = [{
                "doc_id": "doc-" + query_id, "rank": 1,
                "unit_ids": list(query["relevant_unit_ids"]),
                "score": 1.0, "score_kind": kind,
                "components": [component(kind, 1, 1.0, kind)],
            }]
        results.append({
            "query_id": query_id, "abstain": not hits,
            "abstain_reason": "no_hit_above_gate" if not hits else None,
            "latency_ms": latency, "hits": hits,
        })
    fusion = None
    if kind in ("rrf", "hybrid"):
        fusion_inputs = list(fusion_inputs or ())
        fusion = {
            "method": "rrf", "rank_constant": 60, "window_size": 50, "top_k": 5,
            "inputs": [{
                "backend": item["backend"]["name"],
                "result_sha256": evaluation.canonical_sha256(item),
            } for item in sorted(fusion_inputs, key=lambda row: row["backend"]["name"])],
        }
        if fusion_inputs:
            for result in results:
                result_sets = {}
                for source in fusion_inputs:
                    source_result = next(
                        row for row in source["results"]
                        if row["query_id"] == result["query_id"])
                    result_sets[source["backend"]["name"]] = source_result["hits"]
                result["hits"] = candidates.rrf(
                    result_sets, rank_constant=fusion["rank_constant"],
                    window_size=fusion["window_size"], top_k=fusion["top_k"])
                result["abstain"] = not result["hits"]
                result["abstain_reason"] = (
                    "no_hit_above_gate" if result["abstain"] else None)
    parent_binding = None
    if kind == "reranker" and parent is not None:
        parent_binding = {
            "backend": parent["backend"]["name"],
            "run_sha256": evaluation.canonical_sha256(parent),
        }
    return {
        "schema_version": 1,
        "run_id": "%s-run" % kind,
        "gold_sha256": evaluation.canonical_sha256(gold),
        "index_bundle": copy.deepcopy(gold["index_bundle"]),
        "index_bundle_sha256": evaluation.canonical_sha256(gold["index_bundle"]),
        "backend": {
            "name": kind + "-test", "kind": kind, "version": "1",
            "config_sha256": HEX_B,
        },
        "parent": parent_binding,
        "fusion": fusion,
        "resources": {
            "query_count": len(results), "indexed_docs": 200,
            "index_size_bytes": index_size, "p95_latency_ms": latency,
            "top5_stability": stability,
        },
        "results": results,
    }


class GoldSchemaTest(unittest.TestCase):
    def test_valid_gold_normalizes(self):
        gold = evaluation.validate_gold(small_gold())
        self.assertEqual(["q001", "q002"], list(gold["queries"]))
        self.assertEqual(["c1"], list(gold["index_bundle"]))
        self.assertEqual(evaluation.canonical_sha256(small_gold()), gold["canonical_sha256"])

    def test_gold_requires_real_distinct_course_shards_and_exact_query_courses(self):
        bad = small_gold()
        duplicate = copy.deepcopy(bad["index_bundle"][0])
        duplicate["course_id"] = "c2"
        bad["index_bundle"].append(duplicate)
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "reuses one artifact/index shard"):
            evaluation.validate_gold(bad)
        bad = small_gold()
        bad["index_bundle"].append({
            "course_id": "c2", "index_sha256": digest(30),
            "content_units_sha256": digest(31),
            "source_manifest_sha256": digest(32),
        })
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "course IDs must exactly equal"):
            evaluation.validate_gold(bad)

    def test_unknown_and_duplicate_queries_fail_closed(self):
        bad = small_gold()
        bad["unknown"] = True
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "schema mismatch"):
            evaluation.validate_gold(bad)
        bad = small_gold()
        bad["queries"].append(copy.deepcopy(bad["queries"][0]))
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "duplicate query_id"):
            evaluation.validate_gold(bad)

    def test_positive_evidence_must_exactly_match_relevant_units(self):
        bad = small_gold()
        bad["queries"][0]["evidence"] = []
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "exactly equal"):
            evaluation.validate_gold(bad)

    def test_relevant_and_hard_negative_overlap_rejected(self):
        bad = small_gold()
        bad["queries"][0]["hard_negative_unit_ids"] = ["u1"]
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "overlap"):
            evaluation.validate_gold(bad)

    def test_unanswerable_query_cannot_have_positive_label(self):
        bad = small_gold()
        bad["queries"][1]["relevant_unit_ids"] = ["u2"]
        with self.assertRaises(evaluation.RetrievalEvaluationError):
            evaluation.validate_gold(bad)

    def test_index_binding_rejects_unmapped_and_answer_side_units(self):
        index = {"docs": [
            {"id": "d1", "unit_ids": ["u1"], "kind": "prose"},
            {"id": "d2", "unit_ids": ["h1"], "kind": "concept"},
        ]}
        self.assertEqual(2, evaluation.validate_index_bindings(small_gold(), index)[
            "indexed_labeled_units"])
        bad = copy.deepcopy(index)
        bad["docs"][0]["kind"] = "answer"
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "answer-side"):
            evaluation.validate_index_bindings(small_gold(), bad)
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "do not resolve"):
            evaluation.validate_index_bindings(small_gold(), {"docs": [index["docs"][0]]})


class RunAndMetricTest(unittest.TestCase):
    def test_perfect_run_scores_recall_and_abstention(self):
        gold = small_gold()
        metrics = evaluation.evaluate(gold, make_run(gold))
        self.assertEqual(1.0, metrics["overall"]["recall_at_5"])
        self.assertEqual(1.0, metrics["overall"]["mrr"])
        self.assertEqual(0, metrics["oos"]["false_accepts"])
        self.assertEqual(1.0, metrics["oos"]["abstention_rate"])

    def test_oos_false_accept_is_loud_and_missing_query_fails_closed(self):
        gold = small_gold()
        run = make_run(gold, false_accept_oos=True)
        metrics = evaluation.evaluate(gold, run)
        self.assertEqual(1, metrics["oos"]["false_accepts"])
        run = make_run(gold)
        run["results"].pop()
        run["resources"]["query_count"] = 1
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "query IDs must exactly equal"):
            evaluation.evaluate(gold, run)

    def test_hard_negative_intrusion_means_any_hard_negative_in_top_five(self):
        gold = small_gold()
        run = make_run(gold)
        run["results"][0]["hits"].append({
            "doc_id": "hard-after-answer", "rank": 2,
            "unit_ids": ["h1"], "score": 0.5, "score_kind": "bm25",
            "components": [component("bm25", 2, 0.5, "bm25")],
        })
        metrics = evaluation.evaluate(gold, run)
        self.assertEqual(1.0, metrics["overall"]["hard_negative_intrusion_at_5"])

    def test_run_receipt_rejects_p95_drift_and_missing_fusion(self):
        gold = small_gold()
        bad = make_run(gold)
        bad["resources"]["p95_latency_ms"] = 99
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "p95"):
            evaluation.validate_run_receipt(bad)
        bad = make_run(gold, kind="hybrid")
        bad["fusion"] = None
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "fusion"):
            evaluation.validate_run_receipt(bad)

    def test_run_must_bind_exact_gold_and_index(self):
        gold = small_gold()
        bad = make_run(gold)
        bad["gold_sha256"] = HEX_C
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "gold_sha256"):
            evaluation.evaluate(gold, bad)
        bad = make_run(gold)
        bad["index_bundle"][0]["index_sha256"] = digest(40)
        bad["index_bundle_sha256"] = evaluation.canonical_sha256(bad["index_bundle"])
        with self.assertRaisesRegex(evaluation.RetrievalEvaluationError, "index bundle"):
            evaluation.evaluate(gold, bad)

    def test_reranker_binds_parent_and_preserves_each_doc_pool(self):
        gold = small_gold()
        parent = make_run(gold, kind="dense")
        reranked = make_run(gold, kind="reranker", parent=parent)
        self.assertEqual(
            1.0, evaluation.evaluate(gold, reranked, parent_document=parent)["overall"]["mrr"])
        reranked["results"][0]["hits"][0]["doc_id"] = "invented"
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "changed its parent doc ID pool"):
            evaluation.evaluate(gold, reranked, parent_document=parent)

    def test_rrf_requires_real_bound_input_receipts(self):
        gold = small_gold()
        sparse = make_run(gold, kind="bm25")
        dense = make_run(gold, kind="dense")
        fused = make_run(gold, kind="hybrid", fusion_inputs=[sparse, dense])
        evaluation.evaluate(gold, fused, input_documents=[sparse, dense])
        tampered = copy.deepcopy(dense)
        tampered["run_id"] = "tampered"
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "receipt hash mismatch"):
            evaluation.evaluate(gold, fused, input_documents=[sparse, tampered])

    def test_rrf_rejects_cherry_picked_order_score_components_and_output_set(self):
        gold = small_gold()
        sparse = make_run(gold, kind="bm25")
        dense = make_run(gold, kind="dense")
        for source in (sparse, dense):
            source["results"][0]["hits"].insert(0, {
                "doc_id": "noise-q001", "rank": 1,
                "unit_ids": ["noise-unit-q001"],
                "score": 9.0, "score_kind": source["backend"]["kind"],
                "components": [component(
                    source["backend"]["name"], 1, 9.0,
                    source["backend"]["kind"])],
            })
            source["results"][0]["hits"][1]["rank"] = 2
            source["results"][0]["hits"][1]["components"][0]["rank"] = 2

        fused = make_run(gold, kind="hybrid", fusion_inputs=[sparse, dense])
        promoted = copy.deepcopy(fused)
        promoted["results"][0]["hits"] = [copy.deepcopy(
            fused["results"][0]["hits"][1])]
        promoted["results"][0]["hits"][0]["rank"] = 1
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "deterministic RRF"):
            evaluation.evaluate(
                gold, promoted, input_documents=[sparse, dense])

        truncated = copy.deepcopy(fused)
        truncated["results"][0]["hits"].pop()
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "output set"):
            evaluation.evaluate(
                gold, truncated, input_documents=[sparse, dense])

        bad_score = copy.deepcopy(fused)
        bad_score["results"][0]["hits"][0]["score"] += 0.000000000001
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "field score"):
            evaluation.evaluate(
                gold, bad_score, input_documents=[sparse, dense])

        bad_component = copy.deepcopy(fused)
        bad_component["results"][0]["hits"][0]["components"][0]["score"] += 0.1
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "field components"):
            evaluation.evaluate(
                gold, bad_component, input_documents=[sparse, dense])

    def test_rrf_rejects_forged_non_abstention_and_nested_inputs(self):
        gold = small_gold()
        sparse = make_run(gold, kind="bm25")
        dense = make_run(gold, kind="dense")
        for source in (sparse, dense):
            source["results"][1]["abstain"] = True
            source["results"][1]["abstain_reason"] = "no_hit_above_gate"
            source["results"][1]["hits"] = []
        fused = make_run(gold, kind="hybrid", fusion_inputs=[sparse, dense])
        forged = copy.deepcopy(fused)
        forged["results"][1] = {
            "query_id": "q002", "abstain": False, "abstain_reason": None,
            "latency_ms": 1.0,
            "hits": [{
                "doc_id": "invented", "rank": 1, "unit_ids": ["h1"],
                "score": round(2.0 / 61.0, 12), "score_kind": "rrf",
                "components": [
                    component("bm25-test", 1, 1.0, "bm25"),
                    component("dense-test", 1, 1.0, "dense"),
                ],
            }],
        }
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "abstain"):
            evaluation.evaluate(
                gold, forged, input_documents=[sparse, dense])

        nested = make_run(gold, kind="hybrid", fusion_inputs=[sparse, dense])
        outer = make_run(gold, kind="rrf", fusion_inputs=[sparse, nested])
        with self.assertRaisesRegex(
                evaluation.RetrievalEvaluationError, "directly bound leaf"):
            evaluation.evaluate(
                gold, outer, input_documents=[sparse, nested])


class DecisionTest(unittest.TestCase):
    def test_threshold_overrides_cannot_weaken_promotion_evidence(self):
        gold = large_gold()
        unsafe = (
            {"min_total_queries": 1},
            {"min_delta_recall_at_5": 0.0},
            {"baseline_recall_at_5_floor": 1.0},
            {"max_false_accepts": 1},
            {"max_p_value": 1.0},
        )
        for thresholds in unsafe:
            with self.subTest(thresholds=thresholds), self.assertRaisesRegex(
                    evaluation.RetrievalEvaluationError, "only be made stricter"):
                evaluation.evidence_sufficiency(gold, thresholds)
        stricter = evaluation.evidence_sufficiency(
            gold, {"min_total_queries": 181, "max_p_value": 0.01})
        self.assertFalse(stricter["sufficient"])
        self.assertIn("too_few_total_queries", stricter["reasons"])

    def test_committed_sample_is_explicitly_insufficient(self):
        path = os.path.join(ROOT, "benchmark", "retrieval_gold", "sample.insufficient.json")
        with open(path, encoding="utf-8") as stream:
            sample = json.load(stream)
        decision = evaluation.decide(sample)
        self.assertEqual(evaluation.DECISION_INSUFFICIENT, decision["decision"])
        self.assertIn("too_few_total_queries", decision["reasons"])

    def test_adequate_bm25_is_no_go_even_with_candidate(self):
        gold = large_gold()
        baseline = make_run(gold, kind="bm25")
        dense = make_run(gold, kind="dense")
        candidate = make_run(
            gold, kind="hybrid", latency=5, index_size=5000,
            fusion_inputs=[baseline, dense])
        decision = evaluation.decide(
            gold, baseline, candidate, resamples=500,
            candidate_input_documents=[baseline, dense])
        self.assertEqual(evaluation.DECISION_NO_GO, decision["decision"])
        self.assertEqual(["bm25_adequate_no_heavy_backend_needed"], decision["reasons"])

    def test_material_hybrid_gain_passes_optional_gate(self):
        gold = large_gold()
        misses = ["q%03d" % index for index in range(30)]
        baseline = make_run(gold, kind="bm25", misses=misses)
        dense = make_run(gold, kind="dense")
        candidate = make_run(
            gold, kind="hybrid", latency=5, index_size=5000,
            fusion_inputs=[baseline, dense])
        decision = evaluation.decide(
            gold, baseline, candidate, resamples=1000,
            candidate_input_documents=[baseline, dense])
        self.assertEqual(evaluation.DECISION_GO_OPTIONAL, decision["decision"],
                         decision["reasons"])
        self.assertGreaterEqual(
            decision["report"]["comparison"]["recall_at_5"]["delta"], 0.05)

    def test_safety_regression_forces_no_go(self):
        gold = large_gold()
        baseline = make_run(gold, kind="bm25", misses=["q%03d" % i for i in range(30)])
        dense = make_run(gold, kind="dense", false_accept_oos=True)
        candidate = make_run(
            gold, kind="hybrid", false_accept_oos=True, latency=5, index_size=5000,
            fusion_inputs=[baseline, dense])
        decision = evaluation.decide(
            gold, baseline, candidate, resamples=500,
            candidate_input_documents=[baseline, dense])
        self.assertEqual(evaluation.DECISION_NO_GO, decision["decision"])
        self.assertIn("candidate_oos_false_accepts", decision["reasons"])

    def test_dense_only_result_is_not_promotable(self):
        gold = large_gold()
        baseline = make_run(gold, kind="bm25", misses=["q%03d" % i for i in range(30)])
        dense = make_run(gold, kind="dense", latency=5, index_size=5000)
        decision = evaluation.decide(gold, baseline, dense, resamples=500)
        self.assertEqual(evaluation.DECISION_NO_GO, decision["decision"])
        self.assertIn("candidate_kind_is_experiment_only", decision["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
