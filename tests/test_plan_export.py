# -*- coding: utf-8 -*-
"""Progress footer, day-by-day plan and figure export."""
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "zh-data-structures")


class PlanExportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")
        self.mat = os.path.join(self.dir, "course")
        shutil.copytree(SAMPLE, self.mat, ignore=shutil.ignore_patterns("exam-cram"))
        self.ws = os.path.join(self.mat, "exam-cram")
        os.environ["EXAM_CRAM_WORKSPACE"] = self.ws
        self._pointer = cli.POINTER
        cli.POINTER = os.path.join(self.dir, "home", "last_workspace")
        self._cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self._cwd)
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

    def test_footer_on_every_step(self):
        self.run_cli("setup", self.mat, "--days", "3")
        for argv in (("next",), ("quiz", "-n", "1"), ("check", "q006"), ("answer", "q006", "wrong"),
                     ("note", "--type", "summary", "x"), ("done",), ("status",), ("ask", "循环队列")):
            rc, out = self.run_cli(*argv)
            last = out.rstrip().splitlines()[-1]
            self.assertTrue(last.startswith("📍"), "%s → %r" % (argv, last))
            self.assertIn("→ python coach.py", last)
        rc, out = self.run_cli("status")
        self.assertIn("今天目标: ch2", out)

    def test_plan_splits_days_and_keeps_last_day_for_review(self):
        self.run_cli("setup", self.mat, "--days", "3")
        rc, out = self.run_cli("plan")
        # 3 days → 2 study days (last day reserved): ch1+ch2, then ch3
        self.assertIn("第 1 天: → ch1 线性表 [2题 · 真题 2], ch2 栈和队列 [6题 · 真题 1]", out)
        self.assertIn("第 2 天: ch3 树与二叉树 [4题 · 真题 4]", out)
        self.assertIn("最后一天", out)
        self.assertIn("今天目标: ch1, ch2", out)
        self.assertNotIn("⏰", out)
        rc, out = self.run_cli("plan", "--days", "1")
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.state()["exam_date"], tomorrow)
        self.assertIn("第 1 天: → ch1 线性表 [2题 · 真题 2], ch2 栈和队列 [6题 · 真题 1], ch3 树与二叉树 [4题 · 真题 4]", out)
        # three chapters in one day: point at the chapters past exams ask most
        self.assertIn("先讲真题最多的：ch3, ch1", out)

    def test_plan_without_days(self):
        self.run_cli("setup", self.mat)
        rc, out = self.run_cli("plan")
        self.assertIn("没有设置考试日期", out)

    def test_export_copies_last_listed_figures(self):
        self.run_cli("setup", self.mat)
        ws_fig = os.path.join(self.ws, "figures")
        os.makedirs(ws_fig, exist_ok=True)
        png = os.path.join(ws_fig, "demo.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        rc, out = self.run_cli("export", png, "--to", "shown")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "shown", "demo.png")))
        self.assertIn("shown/demo.png", out)
        # nothing listed yet → clear message, exit 3
        rc, out = self.run_cli("export", "--to", "shown2")
        self.assertEqual(rc, 3)
        # remembered figures from the last command
        s = self.state()
        s["last_figures"] = [png]
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh)
        rc, out = self.run_cli("export", "--to", "shown3")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "shown3", "demo.png")))


if __name__ == "__main__":
    unittest.main()
