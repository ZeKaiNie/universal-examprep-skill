#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Deterministic tests for the benchmark scoring + stats (stdlib unittest, no Claude).

Run:  python -m unittest discover -s tests
(from the benchmark/ directory)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stats as S
import judge as J


class StatsTest(unittest.TestCase):
    def test_kappa_perfect(self):
        self.assertAlmostEqual(S.cohen_kappa([1, 0, 1, 1], [1, 0, 1, 1]), 1.0)

    def test_kappa_chance(self):
        # totally independent-ish labels -> kappa near 0 or negative
        self.assertLess(S.cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]), 0.0)

    def test_mcnemar_no_discordant(self):
        r = S.mcnemar([1, 0, 1], [1, 0, 1])
        self.assertEqual((r["b"], r["c"]), (0, 0))
        self.assertEqual(r["p_value"], 1.0)

    def test_mcnemar_lopsided_significant(self):
        # baseline hallucinates a lot, skill almost never -> small p
        base = [1] * 12 + [0] * 2
        skill = [0] * 12 + [0, 1]
        r = S.mcnemar(base, skill)
        self.assertGreater(r["b"], r["c"])
        self.assertLess(r["p_value"], 0.05)

    def test_bootstrap_observed_delta(self):
        base = [1, 1, 1, 0]
        skill = [0, 0, 0, 0]
        obs, lo, hi = S.paired_bootstrap_ci(base, skill, n_resamples=2000)
        self.assertAlmostEqual(obs, S.mean(skill) - S.mean(base))
        self.assertLessEqual(lo, obs)
        self.assertLessEqual(obs, hi)


class JudgeTest(unittest.TestCase):
    def test_numeric_correct_within_tolerance(self):
        ok, parsed = J.check_numeric("计算得到答案约为 4 个字节", "4", 0)
        self.assertTrue(ok)
        self.assertEqual(parsed, 4.0)

    def test_numeric_wrong(self):
        ok, _ = J.check_numeric("我认为是 6", "4", 0)
        self.assertFalse(ok)

    def test_numeric_tolerance(self):
        self.assertTrue(J.check_numeric("约 3.14159", "3.14", 0.01)[0])

    def test_unanswerable_abstain_is_not_hallucination(self):
        item = {"id": "u1", "question": "考场在哪?", "gold_answer": "",
                "answer_type": "factual", "answerable": False}
        good = J.judge_answer(item, "材料中未涵盖该信息。", J.mock_judge)
        self.assertEqual(good["hallucinated"], 0)
        self.assertTrue(good["abstained"])

    def test_unanswerable_fabrication_is_hallucination(self):
        item = {"id": "u2", "question": "考场在哪?", "gold_answer": "",
                "answer_type": "factual", "answerable": False}
        bad = J.judge_answer(item, "在 3 号教学楼 305。", J.mock_judge)
        self.assertEqual(bad["hallucinated"], 1)

    def test_numeric_item_uses_deterministic_path(self):
        item = {"id": "n1", "question": "一个字几个字节?", "gold_answer": "4",
                "answer_type": "numeric", "tolerance": 0, "answerable": True}
        self.assertTrue(J.judge_answer(item, "答案是 4", J.mock_judge)["correct"])
        self.assertEqual(J.judge_answer(item, "答案是 8", J.mock_judge)["hallucinated"], 1)

    # ---- regression: the judge must NOT mark exact-match answers as wrong/hallucinated ----
    def _factual(self, gold, span="x"):
        return {"id": "f", "question": "q", "gold_answer": gold, "supporting_span": span,
                "answer_type": "definition", "answerable": True}

    def test_exact_gold_is_correct_without_llm(self):
        # 'Word-RAM。' contains gold 'Word-RAM' -> correct deterministically (the exact failure we found)
        def boom(_):
            raise AssertionError("LLM judge must not be called when the gold matches verbatim")
        r = J.judge_answer(self._factual("Word-RAM"), "Word-RAM。", boom)
        self.assertTrue(r["correct"])
        self.assertEqual(r["hallucinated"], 0)
        self.assertEqual(r["scored_by"], "lexical")

    def test_gold_match_is_case_and_space_insensitive(self):
        r = J.judge_answer(self._factual("Collision"), "**Collision**(碰撞)。", lambda _: "")
        self.assertTrue(r["correct"])

    def test_negated_gold_is_not_a_free_pass(self):
        # '不是 Word-RAM' must NOT count as correct via the lexical path
        r = J.judge_answer(self._factual("Word-RAM"), "这门课用的不是 Word-RAM。", lambda _: '{"correct":0}')
        self.assertFalse(r["correct"])

    def test_judge_parse_failure_is_flagged_not_hallucination(self):
        # an unparseable judge reply must be a judge_error, NOT a silent 'hallucinated=1' (the root bug)
        r = J.judge_answer(self._factual("photosynthesis"), "some open-ended answer", lambda _: "sorry, no JSON here")
        self.assertTrue(r.get("judge_error"))
        self.assertEqual(r["hallucinated"], 0)      # judge failure is not evidence of fabrication
        self.assertFalse(r["correct"])

    def test_no_judge_supplied_is_judge_error(self):
        r = J.judge_answer(self._factual("photosynthesis"), "an answer with no gold match", None)
        self.assertTrue(r.get("judge_error"))
        self.assertEqual(r["hallucinated"], 0)

    def test_llm_path_still_used_when_no_lexical_match(self):
        # no gold substring -> falls through to the (mock) LLM judge and aggregates normally
        r = J.judge_answer(self._factual("photosynthesis", span="plants make sugar from light"),
                           "plants make sugar from light", J.mock_judge)
        self.assertEqual(r["scored_by"], "llm")
        self.assertIn("faithfulness", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
