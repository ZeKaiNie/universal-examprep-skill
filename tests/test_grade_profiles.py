# -*- coding: utf-8 -*-
"""Automatic grading of short answers, the exam-date countdown and model profiles."""
from tests import skillpath  # noqa: F401  (skill folder on sys.path)
import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest

from coach import cli
from coach import questions as qmod
from coach import state as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "zh-data-structures")


def item(answer, qtype="subjective", options=None):
    return {"answer": answer, "type": qtype, "options": options or []}


CHOICE = item("B\n解析：插入位置之后的 n - i + 1 个元素都要后移。", "choice",
              ["A. n - i", "B. n - i + 1", "C. i", "D. n"])


class AnswerKeyTest(unittest.TestCase):
    def test_keys_that_can_be_compared(self):
        self.assertEqual(qmod.answer_key(CHOICE), ("choice", frozenset("B")))
        self.assertEqual(qmod.answer_key(item("答案：BD", "choice", ["A. x", "B. y", "C. z", "D. w"])),
                         ("choice", frozenset("BD")))
        self.assertEqual(qmod.answer_key(item("The correct answer is (C) because ...", "choice", ["A. x", "B. y", "C. z"])),
                         ("choice", frozenset("C")))
        self.assertEqual(qmod.answer_key(item("正确", "true_false")), ("truth", True))
        self.assertEqual(qmod.answer_key(item("False. The heap property ...")), ("truth", False))
        self.assertEqual(qmod.answer_key(item("3\n解析：(1 - 4 + 6) % 6 = 3", "fill_blank")), ("number", (3.0, 0)))
        self.assertEqual(qmod.answer_key(item("Answer: 0.25")), ("number", (0.25, 2)))
        self.assertEqual(qmod.answer_key(item("1/4", "fill_blank")), ("number", (0.25, 0)))
        self.assertEqual(qmod.answer_key(item("后进先出；先进先出", "fill_blank")), ("text", "后进先出先进先出"))
        self.assertEqual(qmod.answer_key(item("n", "fill_blank")), ("text", "n"))  # a variable name, not "no"

    def test_no_key_when_a_mechanical_comparison_would_be_wrong(self):
        for it in (
            item("Solution: O(n2)"),                     # an expression, not a number
            item("Solution: We present both expected and worst-case solutions"),
            item("Solution: A"),                         # a letter without options is not a choice
            item("x = 3 and y = 4"),
            item("前序遍历为 ABDECF，中序遍历为 DBEAFC", "subjective"),
            item(None, "choice", ["A. x", "B. y"]),
            item("见课本第 3 章", "choice", ["A. x", "B. y"]),
        ):
            self.assertIsNone(qmod.answer_key(it), it)


class GradeTest(unittest.TestCase):
    def test_choice(self):
        for reply, verdict in (("B", "right"), ("b", "right"), ("我选 B", "right"), ("I'd pick B.", "right"),
                               ("n - i + 1", "right"), ("A", "wrong"), ("it's a tricky one, A", "wrong"),
                               ("I am not sure", "unsure"), ("", "unsure")):
            self.assertEqual(qmod.grade_answer(CHOICE, reply)[0], verdict, reply)
        self.assertEqual(qmod.grade_answer(CHOICE, "B")[1], "B")

    def test_true_false(self):
        q = item("错误", "true_false")
        for reply, verdict in (("错", "right"), ("不对", "right"), ("×", "right"), ("false", "right"),
                               ("对的", "wrong"), ("正确", "wrong"), ("不知道呢，可能吧大概是这样子", "unsure")):
            self.assertEqual(qmod.grade_answer(q, reply)[0], verdict, reply)

    def test_numbers(self):
        q = item("0.333\nExplanation ...", "fill_blank")
        for reply, verdict in (("1/3", "right"), ("0.33", "right"), ("33.3%", "right"), ("0.4", "wrong"),
                               ("about 0.333 seconds", "right"), ("0.3 or 0.4", "unsure"), ("one third", "unsure")):
            self.assertEqual(qmod.grade_answer(q, reply)[0], verdict, reply)
        self.assertEqual(qmod.grade_answer(item("11", "fill_blank"), "11 个")[0], "right")
        self.assertEqual(qmod.grade_answer(item("11", "fill_blank"), "12")[0], "wrong")

    def test_text_fill_in_is_never_marked_wrong_automatically(self):
        q = item("后进先出；先进先出", "fill_blank")
        self.assertEqual(qmod.grade_answer(q, "后进先出、先进先出")[0], "right")
        self.assertEqual(qmod.grade_answer(q, "LIFO, FIFO")[0], "unsure")  # same meaning, other words

    def test_open_questions_are_left_to_the_tutor(self):
        self.assertEqual(qmod.grade_answer(item("Solution: We use a heap ..."), "a heap")[0], "unsure")


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")
        self.mat = os.path.join(self.dir, "course")
        shutil.copytree(SAMPLE, self.mat, ignore=shutil.ignore_patterns("exam-cram"))
        self.ws = os.path.join(self.mat, "exam-cram")
        os.environ["EXAM_CRAM_WORKSPACE"] = self.ws
        self._pointer = cli.POINTER
        cli.POINTER = os.path.join(self.dir, "home", "last_workspace")

    def tearDown(self):
        cli.POINTER = self._pointer
        os.environ.pop("EXAM_CRAM_WORKSPACE", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, *argv):
        del cli._SHOWN[:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def state(self):
        with open(os.path.join(self.ws, "study_state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def write_state(self, state):
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)

    def test_grade_records_objective_answers_and_hands_back_the_rest(self):
        self.run_cli("setup", self.mat)
        rc, out = self.run_cli("quiz", "-n", "1")
        self.assertIn("python coach.py grade q006", out.splitlines()[-1])

        rc, out = self.run_cli("grade", "q006", "B")
        self.assertIn("✅", out)
        rc, out = self.run_cli("grade", "q007", "我选", "A")  # several words without quotes still work
        self.assertIn("❌", out)
        self.assertIn("<<<MATERIAL", out)  # the reference answer is shown, fenced, to explain the mistake
        history = [(h["qid"], h["result"]) for h in self.state()["history"]]
        self.assertEqual(history, [("q006", "right"), ("q007", "wrong")])
        self.assertEqual([m["qid"] for m in self.state()["mistakes"]], ["q007"])

        rc, out = self.run_cli("grade", "q004", "a b c * +")
        self.assertIn("python coach.py answer q004 right|wrong", out)
        self.assertIn("参考答案", out)
        self.assertEqual(len(self.state()["history"]), 2)  # not recorded: the tutor decides
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("📍"))

        rc, out = self.run_cli("grade", "q999", "B")
        self.assertEqual(rc, 2)

    def test_countdown_follows_the_calendar(self):
        self.run_cli("setup", self.mat, "--days", "5")
        today = datetime.date.today()
        self.assertEqual(self.state()["exam_date"], (today + datetime.timedelta(days=5)).isoformat())
        self.assertIn("距考试 5 天", self.run_cli("status")[1])

        s = self.state()
        s["exam_date"] = (today + datetime.timedelta(days=2)).isoformat()  # three days later
        self.write_state(s)
        self.assertIn("距考试 2 天", self.run_cli("status")[1])
        s["exam_date"] = today.isoformat()
        self.write_state(s)
        self.assertIn("今天考试", self.run_cli("status")[1])

        self.run_cli("plan", "--date", "2031-01-15")
        self.assertEqual(self.state()["exam_date"], "2031-01-15")
        self.run_cli("setup", self.mat)  # re-setup keeps the date
        self.assertEqual(self.state()["exam_date"], "2031-01-15")

    def test_old_workspaces_get_a_date(self):
        self.run_cli("setup", self.mat)
        s = self.state()
        s.pop("exam_date")
        s.pop("profile")
        s["exam_days"] = 4
        s["created"] = "2026-03-01 09:00"
        self.write_state(s)
        loaded = st.load(self.ws)
        self.assertEqual(loaded["exam_date"], "2026-03-05")
        self.assertEqual(loaded["profile"], "standard")
        self.assertNotIn("exam_days", loaded)
        self.assertEqual(st.days_left(loaded, datetime.date(2026, 3, 3)), 2)
        self.assertEqual(st.days_left(loaded, datetime.date(2026, 3, 9)), 0)

    def test_profiles(self):
        self.run_cli("setup", self.mat, "--profile", "small")
        self.assertEqual(self.state()["slice_chars"], 1800)
        rc, out = self.run_cli("quiz")
        self.assertEqual(out.count("<<<MATERIAL"), 1)  # one question at a time
        self.run_cli("setup", self.mat)  # re-setup keeps the profile
        self.assertEqual(self.state()["profile"], "small")
        self.run_cli("setup", self.mat, "--profile", "large")
        self.assertEqual(self.state()["slice_chars"], 8000)
        rc, out = self.run_cli("quiz")
        self.assertGreaterEqual(out.count("<<<MATERIAL"), 2)
        self.run_cli("setup", self.mat, "--slice", "999")
        self.assertEqual(self.state()["slice_chars"], 999)  # an explicit slice wins


if __name__ == "__main__":
    unittest.main()
