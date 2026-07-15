import unittest

from benchmark import retrieval_candidates as candidates


def hit(doc_id, rank, score, units=None, kind="bm25"):
    return {
        "doc_id": doc_id, "rank": rank, "unit_ids": list(units or (doc_id,)),
        "score": score, "score_kind": kind, "components": [],
    }


class RRFTest(unittest.TestCase):
    def test_rrf_formula_and_deterministic_tie_break(self):
        sparse = [hit("shared", 1, 9), hit("b", 2, 8), hit("a", 3, 7)]
        dense = [hit("shared", 1, 0.9), hit("a", 2, 0.8), hit("b", 3, 0.7)]
        fused = candidates.rrf({"sparse": sparse, "dense": dense}, rank_constant=60,
                               window_size=3, top_k=3)
        self.assertEqual("shared", fused[0]["doc_id"])
        self.assertEqual(1, fused[0]["rank"])
        self.assertAlmostEqual(2 / 61, fused[0]["score"], places=10)
        # a and b have the same fused score; stable doc_id decides.
        self.assertEqual(["shared", "a", "b"], [row["doc_id"] for row in fused])

    def test_window_excludes_tail_and_top_k_limits(self):
        left = [hit("a", 1, 1), hit("tail", 2, 0.5)]
        right = [hit("b", 1, 1), hit("tail", 2, 0.5)]
        fused = candidates.rrf({"left": left, "right": right}, window_size=1, top_k=1)
        self.assertEqual(1, len(fused))
        self.assertNotEqual("tail", fused[0]["doc_id"])

    def test_duplicate_and_inconsistent_identity_fail_closed(self):
        duplicate = [hit("a", 1, 1), hit("a", 2, 0.5)]
        with self.assertRaisesRegex(candidates.CandidateError, "duplicate doc_id"):
            candidates.rrf({"one": duplicate, "two": [hit("b", 1, 1)]})
        with self.assertRaisesRegex(candidates.CandidateError, "inconsistent unit_ids"):
            candidates.rrf({
                "one": [hit("a", 1, 1, ["u1"])],
                "two": [hit("a", 1, 1, ["u2"])],
            })

    def test_needs_two_result_sets_and_positive_parameters(self):
        with self.assertRaises(candidates.CandidateError):
            candidates.rrf({"only": [hit("a", 1, 1)]})
        with self.assertRaises(candidates.CandidateError):
            candidates.rrf({"a": [], "b": []}, rank_constant=0)


class RerankerTest(unittest.TestCase):
    def test_reranker_reorders_only_existing_pool(self):
        pool = [hit("a", 1, 1), hit("b", 2, 0.5)]
        result = candidates.rerank(
            "query", pool, lambda query, rows: {"a": 0.1, "b": 0.9})
        self.assertEqual(["b", "a"], [row["doc_id"] for row in result])
        self.assertEqual([1, 2], [row["rank"] for row in result])
        self.assertTrue(all(row["score_kind"] == "reranker" for row in result))

    def test_reranker_cannot_introduce_or_omit_ids(self):
        pool = [hit("a", 1, 1), hit("b", 2, 0.5)]
        for scores in ({"a": 1}, {"a": 1, "b": 2, "c": 3}):
            with self.assertRaisesRegex(candidates.CandidateError, "candidate identity"):
                candidates.rerank("query", pool, lambda query, rows, value=scores: value)
        with self.assertRaisesRegex(candidates.CandidateError, "cannot truncate"):
            candidates.rerank(
                "query", pool, lambda query, rows: {"a": 1, "b": 2}, top_k=1)

    def test_non_finite_score_rejected(self):
        with self.assertRaisesRegex(candidates.CandidateError, "finite"):
            candidates.rerank(
                "query", [hit("a", 1, 1)], lambda query, rows: {"a": float("nan")})


class StabilityAndInjectionTest(unittest.TestCase):
    def test_top_k_stability_counts_exact_sets(self):
        repeats = [
            {"q1": [hit("a", 1, 1)], "q2": [hit("b", 1, 1)]},
            {"q1": [hit("a", 1, 1)], "q2": [hit("c", 1, 1)]},
            {"q1": [hit("a", 1, 1)], "q2": [hit("b", 1, 1)]},
        ]
        self.assertEqual(0.5, candidates.top_k_stability(repeats))

    def test_stability_rejects_missing_queries(self):
        with self.assertRaisesRegex(candidates.CandidateError, "different query IDs"):
            candidates.top_k_stability([{"q1": []}, {"q2": []}])

    def test_callable_candidate_is_explicit_and_bounded(self):
        backend = candidates.CallableCandidate(
            "dense-test", "dense", "1", lambda query, top_k: [hit("a", 1, 0.9, kind="dense")])
        self.assertEqual("a", backend.search("query")[0]["doc_id"])
        with self.assertRaises(candidates.CandidateError):
            candidates.CallableCandidate("bad", "bm25", "1", lambda q, k: [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
