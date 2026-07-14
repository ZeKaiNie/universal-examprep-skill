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
import io
import json
import shutil
import tempfile
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from scripts import ingest as ingest_module
UnsafePathError = ingest_module.UnsafePathError

INGEST = os.path.join(SCRIPTS, "ingest.py")

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

    def test_path_guard_error_is_reported_without_traceback(self):
        output = io.StringIO()
        with mock.patch.object(
            ingest_module,
            "safe_workspace_entry",
            side_effect=UnsafePathError("path contains a symlink entry"),
        ):
            with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                ingest_module._safe_output_tree(self.tmp)
        self.assertEqual(1, stopped.exception.code)
        self.assertIn("符号链接", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

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

    def test_duplicate_quiz_id_rejected_before_any_workspace_write(self):
        bad = json.loads(json.dumps(VALID, ensure_ascii=False))
        bad["quiz_bank"][1]["id"] = "q1"
        result = run_ingest(bad, self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("重复", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "references")))

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

    # ---------- 扩展题型：diagram / fill_blank 等被接受 ----------
    def test_extended_quiz_types_accepted(self):
        data = {"course_name": "数据结构",
                "phases": [{"phase_num": 1, "phase_name": "树", "wiki_filename": "ch1.md", "wiki_content": "x"}],
                "quiz_bank": [
                    {"id": "d1", "type": "diagram", "question": "画出 AVL 插入 [3,2,1]", "answer": "右旋", "diagram_type": "avl_tree"},
                    {"id": "f1", "type": "fill_blank", "question": "栈是____", "answer": "LIFO"},
                ]}
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual({q["type"] for q in bank}, {"diagram", "fill_blank"})

    def test_unknown_quiz_type_fails(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
               "quiz_bank": [{"id": "q1", "type": "essay", "question": "?", "answer": "a"}]}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    # ---------- 缺标准答案：警告但不中止 ----------
    def test_missing_answer_warns_but_succeeds(self):
        data = {"course_name": "X",
                "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
                "quiz_bank": [{"id": "q9", "type": "subjective", "question": "?"}]}
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("q9", r.stdout)  # 点名缺答案的题

    def test_teaching_examples_persist_independently_and_do_not_add_missing_answers(self):
        example = {
            "id": "lecture_example_1_2", "chapter": 1,
            "type": "subjective", "question": "A completed worked demonstration.",
            "answer_status": "unknown", "source": "material",
            "source_file": "ch01.pdf", "source_pages": [3],
            "teaching_role": "worked_example", "assets": [],
        }
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "# Chapter 1\nExample 1.2"}],
            "quiz_bank": [{"id": "q1", "chapter": 1, "type": "subjective",
                           "question": "Assessable question", "answer": "answer"}],
            "teaching_examples": [dict(example)],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = os.path.join(self.tmp, "references", "teaching_examples.json")
        self.assertTrue(os.path.isfile(path))
        saved = json.loads(read(path))
        self.assertEqual(saved, [example])
        report = json.loads(read(self.tmp, "ingest_report.json"))
        self.assertEqual(report["teaching_examples"], 1)
        self.assertEqual(report["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertEqual(report["teaching_examples_by_chapter"], {"1": 1})
        self.assertEqual(report["teaching_example_ids_by_chapter"],
                         {"1": ["lecture_example_1_2"]})
        self.assertEqual(report["missing_answer_ids"], [])
        baseline = json.loads(read(
            self.tmp, "references", "teaching_baseline.json"))
        self.assertEqual(baseline["policy"], "append_only")
        self.assertEqual(baseline["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertFalse(any(name.startswith(".teaching_examples.json.")
                             for name in os.listdir(os.path.join(self.tmp, "references"))))

    def test_teaching_manifest_destination_symlink_is_rejected_without_touching_target(self):
        os.makedirs(os.path.join(self.tmp, "references"))
        outside = os.path.join(os.path.dirname(self.tmp), "outside-teaching.json")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("OUTSIDE-SENTINEL")
        link = os.path.join(self.tmp, "references", "teaching_examples.json")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")
        data = dict(VALID, teaching_examples=[])
        result = run_ingest(data, self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-SENTINEL")

    def test_references_parent_symlink_escape_is_rejected_before_writes(self):
        outside = tempfile.mkdtemp(prefix="outside-references-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        link = os.path.join(self.tmp, "references")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")
        result = run_ingest(dict(VALID, teaching_examples=[]), self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(os.listdir(outside), [])

    def test_quiz_bank_hardlink_is_replaced_without_mutating_outside_inode(self):
        self.assertEqual(run_ingest(VALID, self.tmp).returncode, 0)
        outside = os.path.join(os.path.dirname(self.tmp), "outside-quiz-hardlink.json")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        with open(outside, "w", encoding="utf-8") as stream:
            stream.write("OUTSIDE-HARDLINK-SENTINEL")
        quiz_path = os.path.join(self.tmp, "references", "quiz_bank.json")
        os.remove(quiz_path)
        try:
            os.link(outside, quiz_path)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("hard links unavailable")

        result = run_ingest(VALID, self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-HARDLINK-SENTINEL")
        self.assertFalse(os.path.samefile(outside, quiz_path))
        self.assertEqual(len(json.loads(read(quiz_path))), len(VALID["quiz_bank"]))

    def test_wiki_destination_symlink_is_rejected_without_touching_target(self):
        self.assertEqual(run_ingest(VALID, self.tmp).returncode, 0)
        outside = os.path.join(os.path.dirname(self.tmp), "outside-wiki.md")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        with open(outside, "w", encoding="utf-8") as stream:
            stream.write("OUTSIDE-WIKI-SENTINEL")
        wiki_path = os.path.join(self.tmp, "references", "wiki", "ch1_concepts.md")
        os.remove(wiki_path)
        try:
            os.symlink(outside, wiki_path)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")

        result = run_ingest(VALID, self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-WIKI-SENTINEL")

    def test_legacy_raw_input_without_teaching_examples_does_not_require_manifest(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(
            self.tmp, "references", "teaching_examples.json")))

    def test_legacy_rerun_preserves_existing_teaching_manifest_and_report_baseline(self):
        example = {
            "id": "lecture_example_1_2", "chapter": 1,
            "type": "subjective", "question": "A completed worked demonstration.",
            "answer_status": "unknown", "source": "material",
            "source_file": "ch01.pdf", "source_pages": [3],
            "teaching_role": "worked_example", "assets": [],
        }
        base = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
        }
        first = dict(base, teaching_examples=[example])
        self.assertEqual(run_ingest(first, self.tmp).returncode, 0)
        # A legacy producer omits the new field.  It must not erase either the manifest or the
        # retention baseline used by validate_workspace.py.
        self.assertEqual(run_ingest(base, self.tmp).returncode, 0)
        saved = json.loads(read(self.tmp, "references", "teaching_examples.json"))
        report = json.loads(read(self.tmp, "ingest_report.json"))
        self.assertEqual(saved, [example])
        self.assertEqual(report["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertEqual(report["teaching_example_ids_by_chapter"],
                         {"1": ["lecture_example_1_2"]})
        self.assertEqual(report["teaching_examples"], 1)

    def test_explicit_smaller_snapshot_cannot_shrink_independent_teaching_baseline(self):
        examples = [
            {"id": "ex1", "chapter": 1, "type": "subjective",
             "question": "Chapter 1 demonstration", "source_file": "ch01.pdf",
             "source_pages": [1], "teaching_role": "worked_example", "assets": []},
            {"id": "ex2", "chapter": 2, "type": "subjective",
             "question": "Chapter 2 demonstration", "source_file": "ch02.pdf",
             "source_pages": [1], "teaching_role": "worked_example", "assets": []},
        ]
        data = dict(VALID, teaching_examples=examples)
        self.assertEqual(run_ingest(data, self.tmp).returncode, 0)

        smaller = dict(VALID, teaching_examples=[examples[1]])
        result = run_ingest(smaller, self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        baseline = json.loads(read(
            self.tmp, "references", "teaching_baseline.json"))
        report = json.loads(read(self.tmp, "ingest_report.json"))
        current = json.loads(read(
            self.tmp, "references", "teaching_examples.json"))
        self.assertEqual(baseline["teaching_example_ids"], ["ex1", "ex2"])
        self.assertEqual(baseline["teaching_example_ids_by_chapter"],
                         {"1": ["ex1"], "2": ["ex2"]})
        self.assertEqual(report["teaching_example_ids"], ["ex1", "ex2"])
        self.assertEqual(report["current_teaching_example_ids"], ["ex2"])
        self.assertEqual([item["id"] for item in current], ["ex2"])

    def test_invalid_teaching_example_role_fails_loudly(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
            "teaching_examples": [{
                "id": "e1", "chapter": 1, "teaching_role": "quiz_me",
                "source_file": "ch01.pdf", "source_pages": [1],
            }],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("teaching_role", r.stdout)

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


    # ---------- 回归：缺 id 自动补全 ----------
    def test_missing_ids_auto_filled_with_unique_ids(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"type": "subjective", "question": "Q1", "answer": "A1"},
                {"type": "subjective", "question": "Q2", "answer": "A2"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        ids = [q["id"] for q in bank]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, f"ID 重复: {ids}")

    # ---------- 回归：已有 id 不撞号 ----------
    def test_auto_ids_skip_existing_ids(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q2", "type": "subjective", "question": "已有q2", "answer": "A2"},
                {"type": "subjective", "question": "缺id", "answer": "A"},
                {"type": "subjective", "question": "缺id2", "answer": "B"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        ids = [q["id"] for q in bank]
        self.assertIn("q2", ids)
        # 自动补的 id 不能是 q2
        auto_ids = [i for i in ids if i != "q2"]
        self.assertNotIn("q2", auto_ids, f"撞号: {ids}")
        self.assertEqual(len(set(ids)), 3, f"ID 重复: {ids}")

    def test_auto_ids_preserve_zero_id(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": 0, "type": "subjective", "question": "已有数字 id", "answer": "A"},
                {"type": "subjective", "question": "缺 id", "answer": "B"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(bank[0]["id"], 0)
        self.assertEqual(len({q["id"] for q in bank}), 2, f"ID 重复: {[q['id'] for q in bank]}")

    # ---------- 回归：true_false 中英文规范化 ----------
    def test_true_false_normalize_cn_en(self):
        cases = [
            ("正确", True), ("错误", False),
            ("对", True), ("错", False),
            ("是", True), ("否", False),
            ("真", True), ("假", False),
            ("true", True), ("false", False),
            ("yes", True), ("no", False),
            ("√", True), ("×", False),
        ]
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": f"q{i}", "type": "true_false", "question": raw, "answer": raw}
                for i, (raw, _) in enumerate(cases, 1)
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        for i, (raw, expected) in enumerate(cases):
            q = bank[i]
            self.assertIsInstance(q["answer"], bool,
                                  f"q{i+1} 答案 {raw!r} 应为 bool，实际 {type(q['answer']).__name__}")
            self.assertEqual(q["answer"], expected,
                             f"q{i+1} 答案 {raw!r} 期望 {expected}，实际 {q['answer']}")

    def test_true_false_boolean_false_not_reported_missing(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q1", "type": "true_false", "question": "判断题", "answer": False},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("缺少标准答案", r.stdout)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertIs(bank[0]["answer"], False)

    # ---------- 回归：无法识别的 true_false 答案不被静默强转 ----------
    def test_true_false_unknown_answer_preserved(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q1", "type": "true_false", "question": "未知答案", "answer": "maybe"},
                {"id": "q2", "type": "true_false", "question": "数字", "answer": "1"},
                {"id": "q3", "type": "choice", "question": "无关题型", "options": ["A.1","B.2","C.3","D.4"], "answer": "A"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(bank[0]["answer"], "maybe",
                         f"无法识别的 true_false 答案应保留原值，实际 {bank[0]['answer']!r}")
        self.assertEqual(bank[1]["answer"], "1",
                         f"数字答案应保留原值，实际 {bank[1]['answer']!r}")
        self.assertEqual(bank[2]["answer"], "A",
                         "choice 题型不应受影响")



class IngestReportPersistence(unittest.TestCase):
    def test_missing_answers_persisted_to_workspace(self):
        tmp = tempfile.mkdtemp()
        data = {"course_name": "测试课", "phases": [
            {"phase_num": 1, "phase_name": "第一章", "wiki_filename": "ch01.md",
             "wiki_content": "# 第一章"}],
            "quiz_bank": [{"id": "q1", "type": "subjective", "question": "无答案的题?",
                           "source": "material", "ai_generated": False}]}
        in_path = os.path.join(tmp, "raw.json")
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, INGEST, "-i", in_path, "-o", tmp],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.load(open(os.path.join(tmp, "ingest_report.json"), encoding="utf-8"))
        self.assertIn("q1", rep["missing_answer_ids"])            # 缺答案清单持久化，后续会话可接手

    def test_missing_answer_ids_match_assigned_ids(self):
        tmp = tempfile.mkdtemp()
        data = {"course_name": "测试课", "phases": [
            {"phase_num": 1, "phase_name": "第一章", "wiki_filename": "ch01.md",
             "wiki_content": "# 第一章"}],
            "quiz_bank": [{"type": "subjective", "question": "没 id 也没答案的题?",
                           "source": "material", "ai_generated": False}]}
        in_path = os.path.join(tmp, "raw.json")
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, INGEST, "-i", in_path, "-o", tmp],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.load(open(os.path.join(tmp, "ingest_report.json"), encoding="utf-8"))
        bank = json.load(open(os.path.join(tmp, "references", "quiz_bank.json"), encoding="utf-8"))
        bank_ids = {q["id"] for q in bank}
        self.assertTrue(rep["missing_answer_ids"])                # 补号后清单指向真实题库 id
        self.assertTrue(set(rep["missing_answer_ids"]) <= bank_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
