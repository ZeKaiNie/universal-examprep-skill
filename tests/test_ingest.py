#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/ingest.py 的端到端测试，覆盖正常流程与多种极端输入。

仅用 Python 标准库（unittest + subprocess），无需安装任何依赖。
运行方式：
    python -m unittest discover -s tests
  或
    python tests/test_ingest.py
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INGEST = os.path.join(REPO_ROOT, "scripts", "ingest.py")

VALID = {
    "course_name": "数据结构",
    "phases": [
        {"phase_num": 1, "phase_name": "基础概念篇", "wiki_filename": "ch1_concepts.md", "wiki_content": "# 第一章\n概念"},
        {"phase_num": 2, "phase_name": "线性表", "wiki_filename": "ch2_list.md", "wiki_content": "# 第二章\n线性表"},
    ],
    "quiz_bank": [
        {"id": "q1", "chapter": 1, "type": "choice", "question": "栈的特点?", "options": ["A.FIFO", "B.LIFO", "C.随机", "D.无序"], "answer": "B", "explanation": "后进先出"},
        {"id": "q2", "chapter": 2, "type": "subjective", "question": "解释链表", "answer": "由节点和指针构成", "keywords": ["指针", "节点"]},
    ],
}


def run_ingest(input_obj, out_dir, *extra):
    """把 input_obj 写成临时 JSON 并调用 ingest.py，返回 CompletedProcess。"""
    fd, in_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(in_path, "w", encoding="utf-8") as f:
        json.dump(input_obj, f, ensure_ascii=False)
    try:
        return subprocess.run(
            [sys.executable, INGEST, "-i", in_path, "-o", out_dir, *extra],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.remove(in_path)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class IngestEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---------- 正常流程 ----------
    def test_valid_input_generates_all_files(self):
        r = run_ingest(VALID, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in ("references/wiki/ch1_concepts.md", "references/wiki/ch2_list.md",
                    "references/quiz_bank.json", "study_plan.md", "study_progress.md"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), f"缺少 {rel}")
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(len(bank), 2)  # 题库是合法 JSON 数组

    def test_anchors_replaced_with_generated_content(self):
        run_ingest(VALID, self.tmp)
        plan = read(self.tmp, "study_plan.md")
        prog = read(self.tmp, "study_progress.md")
        # 锚点被替换、未原样残留
        self.assertNotIn("<!-- PHASE_TABLE -->", plan)
        self.assertNotIn("<!-- PHASE_CHECKLIST -->", prog)
        self.assertNotIn("{CURRENT_PHASE}", prog)
        # 生成了按实际章节渲染的内容
        self.assertIn("| **阶段 1** | 基础概念篇 |", plan)
        self.assertIn("阶段 1：基础概念篇", prog)
        self.assertIn("- [ ] **阶段 2**：线性表", prog)

    # ---------- 极端输入：结构性错误应中止（退出码 1） ----------
    def test_missing_phase_key_fails_loudly(self):
        bad = {"course_name": "X", "phases": [{"phase_num": 1, "wiki_filename": "a.md", "wiki_content": "x"}], "quiz_bank": []}
        r = run_ingest(bad, self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("phase_name", r.stdout)  # 明确指出缺哪个字段
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "references", "wiki", "chNone_notes.md")))

    def test_empty_phases_fails(self):
        self.assertEqual(run_ingest({"course_name": "X", "phases": [], "quiz_bank": []}, self.tmp).returncode, 1)

    def test_phases_not_a_list_fails(self):
        self.assertEqual(run_ingest({"course_name": "X", "phases": "oops", "quiz_bank": []}, self.tmp).returncode, 1)

    def test_path_traversal_filename_rejected(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "../../evil.md", "wiki_content": "x"}],
               "quiz_bank": []}
        r = run_ingest(bad, self.tmp)
        self.assertEqual(r.returncode, 1)
        # 确认没有写到 wiki 目录之外
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.md")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.md")))

    def test_duplicate_wiki_filename_rejected(self):
        bad = {"course_name": "X", "phases": [
            {"phase_num": 1, "phase_name": "A", "wiki_filename": "dup.md", "wiki_content": "x"},
            {"phase_num": 2, "phase_name": "B", "wiki_filename": "dup.md", "wiki_content": "y"},
        ], "quiz_bank": []}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    def test_choice_question_missing_options_fails(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
               "quiz_bank": [{"id": "q1", "type": "choice", "question": "?", "answer": "A"}]}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    def test_invalid_json_fails(self):
        in_path = os.path.join(self.tmp, "bad.json")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        r = subprocess.run([sys.executable, INGEST, "-i", in_path, "-o", self.tmp],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)

    # ---------- 缺标准答案：警告但不中止 ----------
    def test_missing_answer_warns_but_succeeds(self):
        data = {"course_name": "X",
                "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
                "quiz_bank": [{"id": "q9", "type": "subjective", "question": "?"}]}
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("q9", r.stdout)  # 点名缺答案的题

    # ---------- 幂等：保护 study_progress.md ----------
    def test_rerun_does_not_clobber_progress(self):
        run_ingest(VALID, self.tmp)
        prog_path = os.path.join(self.tmp, "study_progress.md")
        with open(prog_path, "a", encoding="utf-8") as f:
            f.write("\n学生的错题记录-请勿删除\n")
        r = run_ingest(VALID, self.tmp)  # 再跑一次，不加 --force
        self.assertEqual(r.returncode, 0)
        self.assertIn("学生的错题记录-请勿删除", read(prog_path))  # 进度保留

    def test_force_backs_up_then_regenerates(self):
        run_ingest(VALID, self.tmp)
        prog_path = os.path.join(self.tmp, "study_progress.md")
        with open(prog_path, "a", encoding="utf-8") as f:
            f.write("\n旧内容标记\n")
        r = run_ingest(VALID, self.tmp, "--force")
        self.assertEqual(r.returncode, 0)
        backups = [n for n in os.listdir(self.tmp) if n.startswith("study_progress.md.bak-")]
        self.assertTrue(backups, "未创建备份文件")
        self.assertIn("旧内容标记", read(self.tmp, backups[0]))   # 旧内容进了备份
        self.assertNotIn("旧内容标记", read(prog_path))            # 新文件已重置


if __name__ == "__main__":
    unittest.main(verbosity=2)
