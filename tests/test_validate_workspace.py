#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for scripts/validate_workspace.py (Tier-0 unit, stdlib only, no network/LLM).

    python -m unittest discover -s tests -v
"""
import io
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import validate_workspace as V  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")


def run(name):
    """Validate a fixture; return (errors, warnings, stats, exit_code)."""
    errors, warnings, stats = V.validate(os.path.join(FX, name))
    return errors, warnings, stats, V._exit_code(errors)


def err_text(errors):
    return " | ".join(e["msg"] for e in errors)


def warn_text(warnings):
    return " | ".join(w["msg"] for w in warnings)


class TestValidateWorkspace(unittest.TestCase):

    def test_valid_workspace_returns_0(self):
        errors, warnings, stats, code = run("valid_workspace")
        self.assertEqual(code, 0, f"valid workspace had errors: {err_text(errors)}")
        self.assertEqual([e for e in errors], [])
        self.assertEqual(stats.get("quiz_items"), 7)

    def test_missing_quizbank_is_error(self):
        errors, _, _, code = run("invalid_workspace_missing_quizbank")
        self.assertEqual(code, 1)
        self.assertIn("quiz_bank.json", err_text(errors))

    def test_invalid_json_is_exit_2(self):
        errors, _, _, code = run("invalid_workspace_bad_json")
        self.assertEqual(code, 2)
        self.assertTrue(any(e["level"] == "fatal" for e in errors))

    def test_duplicate_quiz_ids_rejected(self):
        errors, _, _, code = run("invalid_workspace_dupe_type")
        self.assertEqual(code, 1)
        self.assertIn("重复的题目 id", err_text(errors))

    def test_unknown_quiz_type_rejected(self):
        errors, *_ = run("invalid_workspace_dupe_type")
        self.assertIn("type 非法", err_text(errors))

    def test_choice_without_options_rejected(self):
        errors, *_ = run("invalid_workspace_dupe_type")
        self.assertIn("choice 题必须有非空 options", err_text(errors))

    def test_subjective_without_keywords_warns(self):
        errors, warnings, _, code = run("warnings_workspace")
        self.assertEqual(code, 0, f"warnings-only workspace must stay valid: {err_text(errors)}")
        self.assertIn("keywords", warn_text(warnings))

    def test_diagram_without_diagram_type_warns(self):
        _, warnings, _, _ = run("warnings_workspace")
        self.assertIn("diagram_type", warn_text(warnings))

    def make_ws(self, quiz, prog=None, plan=None):
        """Build a minimal workspace in a tmpdir with a custom quiz_bank (for the regression cases)."""
        d = tempfile.mkdtemp(prefix="vws-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "references", "wiki"))
        open(os.path.join(d, "references", "wiki", "ch1.md"), "w", encoding="utf-8").write("# ch1\n")
        open(os.path.join(d, "references", "quiz_bank.json"), "w", encoding="utf-8").write(
            json.dumps(quiz, ensure_ascii=False))
        open(os.path.join(d, "study_plan.md"), "w", encoding="utf-8").write(
            plan or "阶段 1 `references/wiki/ch1.md`\n阶段 2\n")
        open(os.path.join(d, "study_progress.md"), "w", encoding="utf-8").write(
            prog or "## 当前复习断点\n阶段 1\n\n## 💡 概念疑难点记录\n")
        return d

    def test_missing_answer_is_warning_not_error(self):
        # ingest.py accepts answer-less questions (warn-not-fail); Tier 1 must stay compatible (Codex r1)
        errors, warnings, _, _ = run("invalid_workspace_provenance")
        self.assertTrue(any("无 answer" in w["msg"] for w in warnings))
        self.assertFalse(any("缺答案必须如实标注" in e["msg"] for e in errors))

    def test_missing_answer_alone_is_exit_0_warning(self):
        d = self.make_ws([{"id": "x", "chapter": 1, "type": "subjective",
                           "question": "q", "keywords": ["a"], "source": "teacher"}])
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertTrue(any("无 answer" in w["msg"] for w in warnings))

    def test_missing_chapter_or_phase_warns_not_error(self):
        # ingest.py does NOT require chapter/phase, so Tier 1 must warn, not hard-fail (Codex r2)
        d = self.make_ws([{"id": "x", "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": "teacher"}])
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertTrue(any("chapter 或 phase" in w["msg"] for w in warnings))

    def test_traversal_before_wiki_segment_rejected(self):
        # "../references/wiki/x.md" escapes BEFORE the matched segment — must still be caught (Codex r2)
        d = self.make_ws([{"id": "x", "chapter": 1, "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": "teacher"}],
                         plan="见 `../references/wiki/ch1.md`\n阶段 1\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("路径穿越" in e["msg"] for e in errors))

    def test_non_string_source_does_not_crash(self):
        # a list/object source must be a structured error, not a TypeError on the set membership (Codex r2)
        d = self.make_ws([{"id": "x", "chapter": 1, "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": ["teacher"]}])
        errors, _, _ = V.validate(d)   # must not raise
        self.assertTrue(any("source 必须是字符串" in e["msg"] for e in errors))

    def test_unreadable_wiki_dir_is_fatal(self):
        # references/wiki/ that exists but can't be listed -> structured fatal (exit 2), not a traceback (Codex r2)
        d = self.make_ws([{"id": "x", "chapter": 1, "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": "teacher"}])
        with mock.patch.object(V.os, "listdir", side_effect=OSError("boom")):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 2)

    # ---- Codex round 3 ----
    def _ok_item(self, **over):
        item = {"id": "x", "chapter": 1, "type": "choice", "question": "q",
                "options": ["A. a"], "answer": "A", "source": "teacher"}
        item.update(over)
        return item

    def test_symlinked_wiki_file_rejected(self):
        d = self.make_ws([self._ok_item()])
        link = os.path.join(d, "references", "wiki", "ch1.md")
        with mock.patch.object(V, "_is_symlink", side_effect=lambda p: p == link):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("符号链接" in e["msg"] for e in errors))

    def test_subdir_wiki_reference_rejected(self):
        d = self.make_ws([self._ok_item()], plan="见 `references/wiki/subdir/ch1.md`\n阶段 1\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("扁平" in e["msg"] for e in errors))

    def test_nan_in_quiz_bank_is_fatal(self):
        d = self.make_ws([self._ok_item()])
        open(os.path.join(d, "references", "quiz_bank.json"), "w", encoding="utf-8").write(
            '[{"id":"x","chapter":1,"type":"choice","question":"q","options":["A"],"answer":NaN}]')
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 2)

    def test_unreadable_plan_is_fatal(self):
        d = self.make_ws([self._ok_item()])
        real = V._read

        def boom(p):
            if p.endswith("study_plan.md"):
                raise OSError("boom")
            return real(p)
        with mock.patch.object(V, "_read", side_effect=boom):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 2)

    def test_zero_numeric_id_is_valid(self):
        d = self.make_ws([self._ok_item(id=0)])
        errors, _, _ = V.validate(d)
        self.assertFalse(any("缺少必需字段 id" in e["msg"] for e in errors))

    # ---- Codex round 4 ----
    def test_choice_answer_not_in_options_rejected(self):
        d = self.make_ws([self._ok_item(answer="Z")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("不在 options 中" in e["msg"] for e in errors))

    def test_symlinked_wiki_root_rejected(self):
        d = self.make_ws([self._ok_item()])
        wdir = os.path.join(d, "references", "wiki")
        with mock.patch.object(V, "_is_symlink", side_effect=lambda p: p == wdir):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("符号链接" in e["msg"] for e in errors))

    def test_invalid_true_false_answer_is_error(self):
        d = self.make_ws([self._ok_item(type="true_false", answer="maybe")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("true_false 的 answer 必须是布尔型" in e["msg"] for e in errors))

    def test_non_string_question_rejected(self):
        d = self.make_ws([self._ok_item(question=[])])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("question 必须是非空字符串" in e["msg"] for e in errors))

    # ---- Codex round 5 ----
    def test_symlinked_reference_parent_rejected(self):
        d = self.make_ws([self._ok_item()])
        wdir = os.path.join(d, "references", "wiki")
        real_rp = os.path.realpath
        def rp(p):
            return "/outside/wiki" if os.path.normcase(p) == os.path.normcase(wdir) else real_rp(p)
        with mock.patch.object(V.os.path, "realpath", side_effect=rp):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("符号链接" in e["msg"] for e in errors))

    def test_non_scalar_chapter_rejected(self):
        d = self.make_ws([self._ok_item(chapter=[1, 2])])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("chapter 必须是整数或字符串" in e["msg"] for e in errors))

    def test_markdown_link_wiki_ref_accepted(self):
        # [ch1](references/wiki/ch1.md) must NOT be flagged as path traversal (false positive r5)
        d = self.make_ws([self._ok_item()], plan="阶段 1：见 [ch1](references/wiki/ch1.md)\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertFalse(any("路径穿越" in e["msg"] for e in errors))

    def test_whitespace_only_required_field_rejected(self):
        d = self.make_ws([self._ok_item(question="   ")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("缺少必需字段 question" in e["msg"] for e in errors))

    # ---- Codex round 6 ----
    def test_prose_and_trailing_period_wiki_ref_accepted(self):
        # CJK-prose-adjacent + trailing sentence period must NOT be misread as path traversal (false positive r6)
        d = self.make_ws([self._ok_item()], plan="阶段 1：见：references/wiki/ch1.md。\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertFalse(any("路径穿越" in e["msg"] or "不符合扁平" in e["msg"] for e in errors))

    def test_traversal_still_caught_after_regex_narrowing(self):
        # regression guard: '../' escape must still be rejected with the narrowed regex
        d = self.make_ws([self._ok_item()], plan="见 ../references/wiki/ch1.md\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("路径穿越" in e["msg"] for e in errors))

    def test_symlinked_quiz_bank_rejected(self):
        d = self.make_ws([self._ok_item()])
        qb = os.path.join(d, "references", "quiz_bank.json")
        with mock.patch.object(V, "_is_symlink", side_effect=lambda p: p == qb):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("quiz_bank.json 经符号链接" in e["msg"] for e in errors))

    def test_missing_wiki_ref_in_plan_is_error(self):
        # study_plan referencing a wiki file that doesn't exist -> hard error (r6; ingest never dangles)
        d = self.make_ws([self._ok_item()], plan="阶段 1 `references/wiki/ghost.md`\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("wiki 文件不存在" in e["msg"] for e in errors))

    def test_boolean_chapter_rejected(self):
        d = self.make_ws([self._ok_item(chapter=True)])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("chapter 必须是整数或字符串" in e["msg"] for e in errors))

    def test_whitespace_answer_counts_as_missing(self):
        d = self.make_ws([self._ok_item(type="subjective", answer="   ")])
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)            # still a warning, not an error (ingest允许无答案)
        self.assertTrue(any("无 answer" in w["msg"] for w in warnings))

    # ---- Codex round 7 ----
    def test_symlinked_progress_file_rejected(self):
        d = self.make_ws([self._ok_item()])
        prog = os.path.join(d, "study_progress.md")
        with mock.patch.object(V, "_is_symlink", side_effect=lambda p: p == prog):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("进度文件 经符号链接" in e["msg"] for e in errors))

    def test_choice_answer_as_option_text_accepted(self):
        # answer stored as option text only ("先进后出" for "A. 先进后出") is valid (common in exported banks)
        d = self.make_ws([self._ok_item(options=["A. 先进后出", "B. 先进先出"], answer="先进后出")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertFalse(any("不在 options 中" in e["msg"] for e in errors))

    def test_bogus_choice_answer_still_rejected(self):
        # regression guard: a truly invalid answer must STILL be rejected after the option-text widening
        d = self.make_ws([self._ok_item(options=["A. 先进后出", "B. 先进先出"], answer="Z")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("不在 options 中" in e["msg"] for e in errors))

    def test_windows_backslash_traversal_rejected(self):
        d = self.make_ws([self._ok_item()], plan="见 references\\wiki\\..\\..\\etc\\passwd.md\n")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("路径穿越" in e["msg"] for e in errors))

    def test_progress_template_has_confusion_section(self):
        # fresh ingest output must carry the 疑难点 section so Tier 1 doesn't warn on canonical output (r7)
        tpl = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "study_progress_template.md")
        with open(tpl, encoding="utf-8") as f:
            self.assertIn("疑难点", f.read())

    def test_phase_field_satisfies_requirement(self):
        d = self.make_ws([{"id": "x", "phase": 1, "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": "teacher"}])
        errors, _, _ = V.validate(d)
        self.assertFalse(any("chapter 或 phase" in e["msg"] for e in errors))

    def test_non_scalar_id_or_type_does_not_crash(self):
        # malformed list/object id or type must be a structured error, NOT a TypeError crash (Codex r1)
        d = self.make_ws([{"id": ["bad"], "chapter": 1, "type": ["choice"],
                           "question": "q", "answer": "a", "source": "teacher"}])
        errors, _, _ = V.validate(d)   # must not raise
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("id 必须是标量" in e["msg"] for e in errors))
        self.assertTrue(any("type 必须是字符串" in e["msg"] for e in errors))

    def test_current_phase_not_in_plan_warns(self):
        d = self.make_ws([{"id": "x", "chapter": 1, "type": "choice", "question": "q",
                           "options": ["A. a"], "answer": "A", "source": "teacher"}],
                         prog="## 当前复习断点\n阶段 99\n\n## 💡 概念疑难点记录\n", plan="阶段 1\n")
        _, warnings, _ = V.validate(d)
        self.assertTrue(any("当前阶段 99" in w["msg"] for w in warnings))

    def test_ai_generated_answer_without_marker_rejected(self):
        errors, *_ = run("invalid_workspace_provenance")
        self.assertTrue(any("AI 生成答案" in e["msg"] for e in errors),
                        "an AI-generated answer mislabeled as teacher must be rejected")

    def test_path_traversal_wiki_reference_rejected(self):
        errors, _, _, code = run("invalid_workspace_traversal")
        self.assertEqual(code, 1)
        self.assertIn("路径穿越", err_text(errors))

    def test_json_output_parses(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = V.main([os.path.join(FX, "valid_workspace"), "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["exit_code"], 0)
        self.assertTrue(payload["ok"])
        for key in ("errors", "warnings", "stats"):
            self.assertIn(key, payload)

    def test_unreadable_workspace_is_exit_2(self):
        errors, _, _, code = run("does_not_exist_dir")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
