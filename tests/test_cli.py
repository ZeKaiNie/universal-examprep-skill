# -*- coding: utf-8 -*-
"""End-to-end: the bundled Chinese sample course through a full study session."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from coach import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "zh-data-structures")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")
        self.mat = os.path.join(self.dir, "course")
        shutil.copytree(SAMPLE, self.mat, ignore=shutil.ignore_patterns("exam-cram"))
        self.ws = os.path.join(self.mat, "exam-cram")
        os.environ["EXAM_CRAM_WORKSPACE"] = self.ws
        home = os.path.join(self.dir, "home")
        os.makedirs(home)
        self._pointer = cli.POINTER
        cli.POINTER = os.path.join(home, "last_workspace")

    def tearDown(self):
        cli.POINTER = self._pointer
        os.environ.pop("EXAM_CRAM_WORKSPACE", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def state(self):
        with open(os.path.join(self.ws, "study_state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_full_session(self):
        rc, out = self.run_cli("setup", self.mat, "--days", "2")
        self.assertEqual(rc, 0)
        self.assertIn("章节 (3)", out)
        self.assertIn("题目: 12 (10 有参考答案)", out)
        for name in ("study_state.json", "quiz_bank.json", "index.json", "chapters.json", "progress.md", "notebook.md"):
            self.assertTrue(os.path.exists(os.path.join(self.ws, name)), name)
        self.assertEqual(len(os.listdir(os.path.join(self.ws, "chapters"))), 3)
        self.assertEqual(self.state()["language"], "zh")

        rc, out = self.run_cli("status")
        self.assertIn("进度: [░░░░░░░░░░] 0/3", out)

        rc, out = self.run_cli("next")
        self.assertIn("第 1 章：线性表 (段 1/1)", out)
        self.assertIn("[第1章_线性表.md p.1]", out)
        self.assertIn("本章相关题目 (2)", out)
        self.assertEqual(self.state()["chapters"][0]["part"], 1)

        rc, out = self.run_cli("next")  # chapter text exhausted → examples + hints, no advance
        self.assertIn("本章正文已讲完", out)

        rc, out = self.run_cli("quiz", "-n", "1")
        self.assertIn("[q006]", out)
        rc, out = self.run_cli("check", "q006")
        self.assertIn("参考答案", out)
        self.assertIn("\nB", out)
        rc, out = self.run_cli("answer", "q006", "wrong", "--note", "混淆 n-i 与 n-i+1")
        self.assertEqual(len(self.state()["mistakes"]), 1)
        rc, out = self.run_cli("quiz", "-n", "1")
        self.assertIn("[q006]", out)  # open mistake comes first
        rc, out = self.run_cli("answer", "q006", "right")
        rc, out = self.run_cli("note", "--type", "summary", "顺序表 O(1) 访问，链表 O(1) 插入")
        rc, out = self.run_cli("done")
        self.assertIn("已验证", out)
        self.assertEqual(self.state()["current"], 2)

        rc, out = self.run_cli("done")  # no right answers in ch2
        self.assertIn("已讲完但未验证", out)

        rc, out = self.run_cli("ask", "循环队列 判满")
        self.assertEqual(rc, 0)
        self.assertIn("🟢 [ch2", out)
        rc, out = self.run_cli("ask", "量子纠缠")
        self.assertEqual(rc, 4)

        rc, out = self.run_cli("mistakes")
        self.assertIn("没有待复习的错题", out)
        rc, out = self.run_cli("cheatsheet")
        sheet = open(os.path.join(self.ws, "cheatsheet.md"), encoding="utf-8").read()
        self.assertIn("顺序表 O(1) 访问", sheet)
        self.assertIn("[q006]", sheet)

        rc, out = self.run_cli("goto", "3")
        self.assertEqual(self.state()["current"], 3)
        rc, out = self.run_cli("chapter", "3")
        self.assertIn("树与二叉树", out)
        rc, out = self.run_cli("chapter", "9")
        self.assertEqual(rc, 2)

        # re-running setup keeps progress; --fresh drops it
        rc, out = self.run_cli("setup", self.mat)
        self.assertEqual(self.state()["chapters"][0]["status"], "verified")
        self.assertEqual(self.state()["exam_days"], 2)
        rc, out = self.run_cli("setup", self.mat, "--fresh")
        self.assertEqual(self.state()["chapters"][0]["status"], "todo")

    def test_no_workspace_is_a_clear_error(self):
        os.environ["EXAM_CRAM_WORKSPACE"] = os.path.join(self.dir, "nowhere")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cli.main(["status"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("setup", err.getvalue())

    def test_slice_option_creates_more_parts(self):
        self.run_cli("setup", self.mat, "--slice", "600")
        self.assertGreater(self.state()["chapters"][0]["parts"], 1)
        rc, out = self.run_cli("next")
        self.assertIn("(段 1/", out)
        self.assertIn("继续讲：python coach.py next", out)


if __name__ == "__main__":
    unittest.main()
