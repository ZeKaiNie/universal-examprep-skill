# -*- coding: utf-8 -*-
"""Tests for benchmark/aggregate_matrix.py (T3) + report_matrix.py --summary. Pure stdlib; no network,
no LLM, no API keys, no non-stdlib deps. Drives the fixture pipeline and asserts honest aggregation."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # benchmark/
sys.path.insert(0, BENCH)
import aggregate_matrix as A   # noqa: E402

FIX = os.path.join(BENCH, "tests", "fixtures", "matrix_pipeline")
ANS = os.path.join(FIX, "answers.jsonl")
SCO = os.path.join(FIX, "scores.jsonl")
EXP = os.path.join(FIX, "expected_summary.json")


def _run_agg(args):
    return subprocess.run([sys.executable, os.path.join(BENCH, "aggregate_matrix.py")] + args,
                          capture_output=True, text=True, encoding="utf-8")


def _run(argv):
    # run any benchmark script (argv[0] is the script path) under the same interpreter
    return subprocess.run([sys.executable] + argv, capture_output=True, text=True, encoding="utf-8")


class AggregateMatrix(unittest.TestCase):
    def _aggregate(self):
        out = os.path.join(tempfile.mkdtemp(), "s.json")
        A.main(["--answers", ANS, "--scores", SCO, "--primary-course", "courseA",
                "--secondary-course", "courseB", "--judge-model", "fixture-judge", "--out", out])
        with open(out, encoding="utf-8") as f:
            return json.load(f)

    def test_writes_summary_matching_expected(self):
        with open(EXP, encoding="utf-8") as f:
            self.assertEqual(self._aggregate(), json.load(f))   # deterministic, byte-for-byte cell parity

    def test_correctness_counts(self):
        s = self._aggregate()
        self.assertEqual(s["matrix"]["opus|rawfiles"]["correct"], 1.0)       # 1/1 answerable correct
        self.assertEqual(s["matrix"]["sonnet|closedbook"]["correct"], 0.0)   # 1/1 incorrect

    def test_oos_abstention_metrics(self):
        s = self._aggregate()
        self.assertEqual(s["matrix"]["opus|closedbook"]["n_oos"], 1)
        self.assertEqual(s["matrix"]["opus|closedbook"]["abstention_oos"], 1.0)    # abstained on OOS
        self.assertEqual(s["matrix"]["sonnet|closedbook"]["abstention_oos"], 0.0)  # fabricated on OOS
        self.assertEqual(s["matrix"]["opus|closedbook"]["hallucination"], 0.0)

    def test_cost_totals_and_per_question(self):
        s = self._aggregate()
        self.assertAlmostEqual(s["total_cost_usd"], 2.14, places=4)
        self.assertAlmostEqual(s["cost_per_q"]["courseA"]["closedbook"], 0.0092, places=4)
        self.assertAlmostEqual(s["cost_per_q"]["courseA"]["material"], 0.9, places=4)
        self.assertAlmostEqual(s["matrix"]["opus|material"]["cost_usd"], 1.8, places=4)

    def test_failed_cells_surfaced_not_correct(self):
        # the all-infra material cell honestly shows null rates + n_infra_error, never silently 'correct'
        mat = self._aggregate()["matrix"]["opus|material"]
        self.assertEqual(mat["n_infra_error"], 2)
        self.assertEqual(mat["n_answerable"], 0)
        self.assertIsNone(mat["correct"])

    def test_missing_score_is_judge_error_not_dropped(self):
        # sonnet|rawfiles a1 has NO score → judge_error, counted NOT-correct (lower bound), not dropped
        rf = self._aggregate()["matrix"]["sonnet|rawfiles"]
        self.assertEqual(rf["n_judge_error"], 1)
        self.assertEqual(rf["n_answerable"], 1)
        self.assertEqual(rf["correct"], 0.0)

    def test_present_judge_error_score_counted_not_correct(self):
        # a PRESENT {judge_error: true} score (no 'correct' field) must also count NOT-correct (lower
        # bound), NOT be dropped from the denominator. opus|closedbook = a1(correct) + a3(judge_error).
        c = self._aggregate()["matrix"]["opus|closedbook"]
        self.assertEqual(c["n_answerable"], 2)
        self.assertEqual(c["n_judge_error"], 1)
        self.assertEqual(c["correct"], 0.5)   # NOT inflated to 1.0 by dropping the undecided item

    def test_material_arm_present_as_legacy(self):
        s = self._aggregate()
        self.assertIn("material", s["arms"])
        self.assertIn("opus|material", s["matrix"])   # present, but all-infra → legacy/stress, not inflated

    def test_two_courses_represented_honestly(self):
        s = self._aggregate()
        self.assertEqual(sorted(s["course_matrix"]), ["courseA", "courseB"])
        self.assertEqual(s["matrix"], s["course_matrix"]["courseA"])          # primary course → matrix
        self.assertTrue(all(k.startswith("psyc|") for k in s["psyc"]))        # secondary → psyc block
        self.assertEqual(s["models"], ["opus", "sonnet"])
        self.assertEqual(s["courses"], ["courseA", "courseB"])

    def test_default_primary_course_is_largest(self):
        # with no --primary-course, the course with the most distinct items becomes `matrix`
        out = os.path.join(tempfile.mkdtemp(), "s.json")
        A.main(["--answers", ANS, "--scores", SCO, "--out", out])
        with open(out, encoding="utf-8") as f:
            s = json.load(f)
        self.assertEqual(s["matrix"], s["course_matrix"]["courseA"])   # courseA has more items

    def test_missing_input_file_fails(self):
        r = _run_agg(["--answers", os.path.join(FIX, "nope.jsonl"), "--scores", SCO,
                      "--out", os.path.join(tempfile.mkdtemp(), "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("找不到", r.stderr)

    def test_malformed_row_fails(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.jsonl"), "w", encoding="utf-8") as f:
            f.write("{ not valid json }\n")
        r = _run_agg(["--answers", os.path.join(d, "a.jsonl"), "--scores", SCO, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("不是合法 JSON", r.stderr)

    def test_missing_required_field_fails(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.jsonl"), "w", encoding="utf-8") as f:   # missing item_id + answerable
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill"}) + "\n")
        r = _run_agg(["--answers", os.path.join(d, "a.jsonl"), "--scores", SCO, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("缺必需字段", r.stderr)

    def test_unscored_oos_counts_not_abstained(self):
        # symmetric lower bound: a completed OOS item with no abstention verdict counts NOT-abstained
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "o1", "answerable": False}) + "\n")
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "o2", "answerable": False}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "o2", "abstained": True}) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["m|skill"]
        self.assertEqual(cell["n_oos"], 2)
        self.assertEqual(cell["abstention_oos"], 0.5)   # o2 abstained; o1 unscored → not-abstained (1/2)

    def test_string_boolean_score_fails(self):
        # a string-encoded boolean ("false" is truthy) must FAIL, not silently corrupt the rate
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "correct": "false"}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("必须是布尔值", r.stderr)

    def test_accepts_int_0_1_boolean_flags(self):
        # this repo's judge.py emits integer 0/1 flags — must be accepted and counted correctly
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q1", "answerable": True}) + "\n")
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q2", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q1",
                                "correct": 1, "hallucinated": 0, "faithfulness": 1.0}) + "\n")
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q2",
                                "correct": 0, "hallucinated": 1, "faithfulness": 0.0}) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["m|skill"]
        self.assertEqual(cell["correct"], 0.5)        # 1 of 2 correct — 0/1 handled like booleans
        self.assertEqual(cell["hallucination"], 0.5)

    def test_answerable_resolved_from_score_when_absent_on_answer(self):
        # the documented gen.py→judge path: answer rows carry NO `answerable`; it arrives on the score.
        # The aggregator must read it off the score and still apply the answerable/OOS bookkeeping.
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:   # NO answerable on either answer row
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1"}) + "\n")
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "o1"}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:   # answerable lives here
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1",
                                "answerable": True, "correct": True}) + "\n")
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "o1",
                                "answerable": False, "abstained": True}) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["m|skill"]
        self.assertEqual(cell["n_answerable"], 1)        # q1 answerable read off its score
        self.assertEqual(cell["n_oos"], 1)               # o1 OOS read off its score
        self.assertEqual(cell["correct"], 1.0)
        self.assertEqual(cell["abstention_oos"], 1.0)

    def test_answerable_missing_everywhere_fails_loud(self):
        # if NEITHER the answer nor the score carries `answerable`, the item's universe is undefined —
        # must fail loudly rather than silently guess answerable/OOS.
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1"}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1", "correct": True}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("answerable", r.stderr)

    def test_faithfulness_out_of_range_fails(self):
        # faithfulness is a rate in [0,1]; a malformed/custom scorer writing 1.5 must FAIL, not be
        # averaged into a >100% faithfulness rate.
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q",
                                "correct": True, "faithfulness": 1.5}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("faithfulness", r.stderr)

    def test_answerable_answer_side_only_ok(self):
        # only the answer row carries answerable → used as-is (unchanged behavior)
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "correct": True}) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["matrix"]["m|skill"]["n_answerable"], 1)

    def test_answerable_matching_on_both_sides_ok(self):
        # both sides provide answerable and AGREE (bool vs 0/1) → accepted, no conflict
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q",
                                "answerable": 1, "correct": True}) + "\n")   # 1 == True → agrees
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["m|skill"]
        self.assertEqual(cell["n_answerable"], 1)
        self.assertEqual(cell["correct"], 1.0)

    def test_answerable_conflict_between_answer_and_score_fails(self):
        # both sides provide answerable but DISAGREE → fail loud, naming the (course,model,arm,item_id);
        # must NOT silently prefer either side.
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q",
                                "answerable": False, "abstained": True}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("冲突", r.stderr)
        self.assertIn("'q'", r.stderr)            # the offending item is named

    def test_answerable_malformed_on_answer_side_fails(self):
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "answerable": "yes"}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q", "correct": True}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("answers", r.stderr)
        self.assertIn("answerable", r.stderr)

    def test_answerable_malformed_on_score_side_fails(self):
        # answer row omits answerable; score row provides a malformed one → fail loud (score side named)
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q"}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "q",
                                "answerable": "maybe", "correct": True}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("scores", r.stderr)
        self.assertIn("answerable", r.stderr)

    def test_rejudge_export_rows_accepted_by_aggregator(self):
        # the committed bridge: rejudge.export_rows() rows must feed straight into aggregate_matrix.py
        # with NO schema mismatch and NO LLM/private-file dependency. Exercise correct / OOS / infra.
        try:
            import rejudge as RJ
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        cases = [
            ({"course": "algo", "model": "haiku", "arm": "skill", "id": "i1"},
             {"id": "i1", "answerable": True},
             {"correct": True, "hallucinated": 0, "faithfulness": 1.0, "scored_by": "lexical"}, False),
            ({"course": "algo", "model": "haiku", "arm": "skill", "id": "i2"},
             {"id": "i2", "answerable": False},
             {"correct": True, "abstained": True, "faithfulness": 1.0}, False),
            ({"course": "algo", "model": "haiku", "arm": "skill", "id": "i3"},
             {"id": "i3", "answerable": True},
             {"infra_error": True, "correct": False, "abstained": False, "hallucinated": None,
              "faithfulness": None, "scored_by": "infra_error"}, True),
        ]
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as af, open(sc, "w", encoding="utf-8") as sf:
            for row, item, verdict, infra in cases:
                ar, sr = RJ.export_rows(row, item, verdict, infra)
                af.write(json.dumps(ar, ensure_ascii=False) + "\n")
                sf.write(json.dumps(sr, ensure_ascii=False) + "\n")
        out = os.path.join(d, "summary.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])   # accepted with no schema mismatch
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["haiku|skill"]
        self.assertEqual(cell["n_answerable"], 1)        # i1 (i3 infra → excluded)
        self.assertEqual(cell["n_oos"], 1)               # i2
        self.assertEqual(cell["n_infra_error"], 1)       # i3
        self.assertEqual(cell["correct"], 1.0)
        self.assertEqual(cell["abstention_oos"], 1.0)
        self.assertEqual(cell["n_lexical"], 1)           # i1 scored_by 'lexical' preserved

    def test_rejudge_export_rows_pure_and_offline(self):
        # export_rows is pure: deterministic shape, judge's own scored_by preserved, unlabeled
        # deterministic verdict defaults to "deterministic"; no LLM / file / network touched.
        try:
            import rejudge as RJ
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        row = {"course": "algo", "model": "haiku", "arm": "skill", "id": "x"}
        ar, sr = RJ.export_rows(row, {"id": "x", "answerable": True},
                                {"correct": False, "judge_error": True, "faithfulness": None,
                                 "hallucinated": 0, "scored_by": "judge_error"}, False)
        self.assertEqual(ar, {"course": "algo", "model": "haiku", "arm": "skill",
                              "item_id": "x", "answerable": True, "status": "ok"})
        self.assertIs(sr["judge_error"], True)
        self.assertEqual(sr["scored_by"], "judge_error")          # judge label preserved
        self.assertIsNone(sr["faithfulness"])
        ar2, sr2 = RJ.export_rows(row, {"id": "x", "answerable": True},
                                  {"infra_error": True, "scored_by": "infra_error"}, True)
        self.assertEqual(ar2["status"], "infra_error")            # infra → status flagged on answer row
        self.assertEqual(sr2["scored_by"], "infra_error")
        _, sr3 = RJ.export_rows(row, {"id": "x", "answerable": True}, {"correct": True}, False)
        self.assertEqual(sr3["scored_by"], "deterministic")       # unlabeled deterministic path

    def test_rejudge_export_rows_carry_cost(self):
        # the per-answer generation cost must survive the bridge → real cost_per_q / totals, not a fake $0
        try:
            import rejudge as RJ
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        row = {"course": "algo", "model": "haiku", "arm": "skill", "id": "i1", "cost": 0.07}
        ar, sr = RJ.export_rows(row, {"id": "i1", "answerable": True}, {"correct": True}, False)
        self.assertAlmostEqual(ar["cost_usd"], 0.07, places=4)    # cost carried onto the answer row
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as af, open(sc, "w", encoding="utf-8") as sf:
            af.write(json.dumps(ar, ensure_ascii=False) + "\n")
            sf.write(json.dumps(sr, ensure_ascii=False) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            summ = json.load(f)
        self.assertAlmostEqual(summ["total_cost_usd"], 0.07, places=4)        # flows into totals
        self.assertAlmostEqual(summ["cost_per_q"]["algo"]["skill"], 0.07, places=4)
        # a row with NO cost omits cost_usd (aggregator defaults 0) — honest, no crash
        ar2, _ = RJ.export_rows({"course": "algo", "model": "m", "arm": "skill", "id": "x"},
                                {"id": "x", "answerable": True}, {"correct": True}, False)
        self.assertNotIn("cost_usd", ar2)

    def test_rejudge_scores_out_requires_answers_out(self):
        # the bridge must emit BOTH halves; --scores-out alone is refused (the answer row carries the
        # status/cost the aggregator needs). Fires on arg validation BEFORE any private file is read.
        r = _run([os.path.join(BENCH, "rejudge.py"), "--scores-out",
                  os.path.join(tempfile.mkdtemp(), "s.jsonl")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("--answers-out", r.stderr)

    def test_rejudge_scores_out_answers_out_must_differ(self):
        # same filename for both halves would truncate over each other → refuse (exit 2), before any
        # private file is read.
        same = os.path.join(tempfile.mkdtemp(), "both.jsonl")
        r = _run([os.path.join(BENCH, "rejudge.py"), "--scores-out", same, "--answers-out", same])
        self.assertEqual(r.returncode, 2)
        self.assertIn("同一个文件", r.stderr)

    def test_unified_rows_uses_rerun_cost_when_patching_material(self):
        # when a material infra-error answer is patched with a clean rerun, the exported row must use
        # the RERUN's cost, not the stale failed-attempt cost — else total_cost_usd/cost_per_q are wrong.
        try:
            import rejudge as RJ
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        from unittest import mock
        algo_ans = [{"tag": "matrix", "arm": "material", "model": "opus", "id": "m1",
                     "answer": "usage limit reached", "cost": 0.9}]            # failed attempt, $0.9
        gen = [{"course": "algo", "arm": "material", "model": "opus", "id": "m1",
                "answer": "clean rerun answer", "cost": 0.5}]                  # clean rerun, $0.5
        with mock.patch.object(RJ, "load_jsonl", return_value=algo_ans), \
                mock.patch.object(RJ, "_gen_rows", return_value=gen):
            rows = RJ.unified_rows("algo")
        m1 = [r for r in rows if r["id"] == "m1" and r["arm"] == "material"][0]
        self.assertEqual(m1["answer"], "clean rerun answer")    # answer patched from the rerun
        self.assertAlmostEqual(m1["cost"], 0.5, places=4)       # ...and ITS cost, not the stale 0.9

    def test_rejudge_export_fails_loud_on_duplicate_rows(self):
        # a duplicate (course,model,arm,item_id) in the source rows must FAIL LOUD on export — never a
        # silent drop that would diverge from rejudge.aggregate()'s own (duplicate-counting) output.
        try:
            import rejudge as RJ
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        from unittest import mock
        dup = [{"course": "algo", "tag": "matrix", "model": "m", "arm": "skill", "id": "d1", "answer": "x"},
               {"course": "algo", "tag": "matrix", "model": "m", "arm": "skill", "id": "d1", "answer": "y"}]
        gold_item = {"id": "d1", "answerable": True, "answer_type": "factual",
                     "question": "q?", "gold_answer": "zzz", "supporting_span": "s"}
        d = tempfile.mkdtemp()
        argv = ["rejudge.py", "--scores-out", os.path.join(d, "s.jsonl"),
                "--answers-out", os.path.join(d, "a.jsonl")]
        with mock.patch.object(RJ, "unified_rows", return_value=dup), \
                mock.patch.object(RJ, "load_gold",
                                  side_effect=lambda p: {"d1": gold_item} if "algo" in p else {}), \
                mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                RJ.main()
        self.assertEqual(cm.exception.code, 2)

    def test_accepts_gen_row_aliases(self):
        # gen.py answer rows use `id`/`cost`; judge score dicts use `id` — aliased to item_id/cost_usd
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1",
                                "answerable": True, "cost": 0.05}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "id": "q1", "correct": True}) + "\n")
        out = os.path.join(d, "s.json")
        A.main(["--answers", a, "--scores", sc, "--out", out])
        with open(out, encoding="utf-8") as f:
            cell = json.load(f)["matrix"]["m|skill"]
        self.assertEqual(cell["correct"], 1.0)
        self.assertAlmostEqual(cell["cost_usd"], 0.05, places=4)   # `cost` aliased to cost_usd

    def test_orphan_score_fails_loud(self):
        # a score with no matching answer must fail loudly, not be silently ignored
        d = tempfile.mkdtemp()
        a, sc = os.path.join(d, "a.jsonl"), os.path.join(d, "s.jsonl")
        with open(a, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "x", "answerable": True}) + "\n")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "m", "arm": "skill", "item_id": "ORPHAN"}) + "\n")
        r = _run_agg(["--answers", a, "--scores", sc, "--out", os.path.join(d, "o.json")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("没有对应 answer", r.stderr)

    def test_does_not_silently_use_committed_summary(self):
        # behavioral proof: output reflects ONLY the explicit fixture inputs (2 items, opus+sonnet) —
        # if it had silently read results/matrix/summary.json it would show 65 items / haiku.
        s = self._aggregate()
        self.assertEqual(s["n_items"], 3)   # courseA fixture has 3 distinct items, NOT the committed 65
        self.assertEqual(s["models"], ["opus", "sonnet"])
        self.assertNotIn("haiku", s["models"])

    def test_no_network_or_llm_or_dep(self):
        with open(os.path.join(BENCH, "aggregate_matrix.py"), encoding="utf-8") as f:
            src = f.read()
        for dep in ("import requests", "import anthropic", "import openai", "import numpy",
                    "urllib.request", "http.client", "import socket", "subprocess"):
            self.assertNotIn(dep, src)                   # no network / LLM / non-stdlib dep / subprocess

    def test_cell_parity_with_rejudge(self):
        # aggregate_matrix._cell must agree with benchmark/rejudge.aggregate() (prevent drift)
        try:
            import rejudge
        except Exception as e:   # pragma: no cover
            self.skipTest("rejudge import unavailable: %s" % e)
        items = [
            {"answerable": True, "infra_error": False, "judge_error": False, "correct": True,
             "faithfulness": 1.0, "hallucinated": False, "abstained": None, "scored_by": "llm", "cost_usd": 0.0},
            {"answerable": True, "infra_error": False, "judge_error": True, "correct": False,
             "faithfulness": None, "hallucinated": None, "abstained": None, "scored_by": "lexical", "cost_usd": 0.0},
            {"answerable": False, "infra_error": False, "judge_error": False, "correct": None,
             "faithfulness": None, "hallucinated": None, "abstained": True, "scored_by": "lexical", "cost_usd": 0.0},
            {"answerable": True, "infra_error": True, "judge_error": False, "correct": None,
             "faithfulness": None, "hallucinated": None, "abstained": None, "scored_by": None, "cost_usd": 0.0},
        ]
        cell, rj = A._cell(items), rejudge.aggregate(items)
        for k in ("n", "n_answerable", "n_oos", "correct", "faithfulness", "hallucination",
                  "abstention_oos", "n_judge_error", "n_lexical", "n_infra_error"):
            self.assertEqual(cell[k], rj[k], k)


class ReportMatrixExplicitSummary(unittest.TestCase):
    def _render(self, out_dir):
        return subprocess.run([sys.executable, os.path.join(BENCH, "report_matrix.py"),
                               "--summary", EXP, "--out-dir", out_dir],
                              capture_output=True, text=True, encoding="utf-8")

    def test_explicit_summary_rendered_to_outdir(self):
        d = tempfile.mkdtemp()
        r = self._render(d)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = os.path.join(d, "report.html")
        self.assertTrue(os.path.isfile(report))
        with open(report, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("100%", html)   # the fixture's opus correctness flowed through (not the committed numbers)

    def test_render_does_not_touch_results_matrix(self):
        committed = os.path.join(BENCH, "results", "matrix", "report.html")
        before = os.path.getmtime(committed) if os.path.isfile(committed) else None
        self._render(tempfile.mkdtemp())
        after = os.path.getmtime(committed) if os.path.isfile(committed) else None
        self.assertEqual(before, after)   # committed results/matrix/report.html untouched

    def test_explicit_summary_has_not_published_banner(self):
        # an explicit (non-default) --summary render must be banner'd "NOT the published benchmark"
        d = tempfile.mkdtemp()
        self._render(d)
        with open(os.path.join(d, "report.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("NOT the published MIT/PSYC benchmark", html)
        self.assertIn("并非已发布的 MIT 6.006", html)

    def test_explicit_render_uses_own_arms_no_published_prose(self):
        d = tempfile.mkdtemp()
        self._render(d)
        with open(os.path.join(d, "report.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("material", html)            # the summary's OWN material arm rendered
        self.assertNotIn("98%", html)              # no hard-coded published PSYC conclusion
        self.assertNotIn("kappa", html.lower())    # no published narrative leaks into a custom render

    def test_custom_summary_to_default_outdir_refuses(self):
        # rendering a CUSTOM --summary with no --out-dir would overwrite the committed published
        # results/matrix/report.html — must refuse (exit 2) and leave the published report untouched.
        committed = os.path.join(BENCH, "results", "matrix", "report.html")
        before = os.path.getmtime(committed) if os.path.isfile(committed) else None
        r = subprocess.run([sys.executable, os.path.join(BENCH, "report_matrix.py"), "--summary", EXP],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)
        self.assertIn("覆盖已发布报告", r.stderr)
        after = os.path.getmtime(committed) if os.path.isfile(committed) else None
        self.assertEqual(before, after)            # published report not clobbered


if __name__ == "__main__":
    unittest.main(verbosity=2)
