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
        tpl = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "locales", "zh", "templates", "study_progress_template.md")
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
                         prog="## 当前复习断点\n阶段 99\n\n## 💡 概念疑难点记录\n", plan="## 阶段 1：栈\n")
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

    # ---- P0A: asset-aware quiz schema + fail-closed ----
    def _asset_item(self, **over):
        item = {"id": "aq", "chapter": 1, "type": "diagram", "diagram_type": "venn",
                "question": "Shade the requested Venn regions.", "answer": "...", "source": "material",
                "source_file": "ch01.pdf", "source_pages": [12],
                "answer_source_file": "ch01.pdf", "answer_source_pages": [13],
                "assets": [{"path": "references/assets/a.png", "role": "question_context",
                            "type": "page_image", "caption": "v"}],
                "requires_assets": True, "question_text_status": "page_reference"}
        item.update(over)
        return item

    def _ws_asset(self, item, create=True):
        d = self.make_ws([item])
        if create:
            ap = os.path.join(d, "references", "assets", "a.png")
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            open(ap, "wb").write(b"\x89PNG\r\n")   # validator checks existence, not image validity
        return d

    def test_p0a_old_quizbank_without_asset_fields_still_valid(self):
        d = self.make_ws([self._ok_item(answer="A")])           # none of the new fields
        self.assertEqual(V._exit_code(V.validate(d)[0]), 0)

    def test_p0a_valid_assets_pass(self):
        errors, _, _ = V.validate(self._ws_asset(self._asset_item()))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_requires_assets_but_none_fails(self):
        errors, _, _ = V.validate(self._ws_asset(self._asset_item(assets=[]), create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("fail-closed", err_text(errors))

    def test_p0a_missing_asset_file_fails_when_required(self):
        errors, _, _ = V.validate(self._ws_asset(self._asset_item(), create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("不存在", err_text(errors))

    def test_p0a_asset_path_traversal_fails(self):
        item = self._asset_item(assets=[{"path": "../outside.png", "role": "figure", "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("穿越", err_text(errors))

    def test_p0a_asset_absolute_path_fails(self):
        item = self._asset_item(assets=[{"path": "/etc/x.png", "role": "figure", "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("绝对路径", err_text(errors))

    def test_p0a_asset_url_fetch_fails(self):
        item = self._asset_item(assets=[{"path": "https://x/y.png", "role": "figure", "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("URL", err_text(errors))

    def test_p0a_asset_symlink_escape_fails(self):
        d = self._ws_asset(self._asset_item())
        link = os.path.join(d, "references", "assets", "a.png")
        with mock.patch.object(V, "_is_symlink", side_effect=lambda p: p == link):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("符号链接" in e["msg"] for e in errors))

    def test_p0a_invalid_asset_role_fails(self):
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": "bogus",
                                         "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("role 非法", err_text(errors))

    def test_p0a_invalid_asset_type_fails(self):
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": "figure",
                                         "type": "bogus"}])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("type 非法", err_text(errors))

    def test_p0a_source_pages_must_be_positive_ints(self):
        for bad in ([0], [-1], ["12"], [1.5], [True], []):
            errors, _, _ = V.validate(self._ws_asset(self._asset_item(source_pages=bad)))
            self.assertEqual(V._exit_code(errors), 1, f"source_pages={bad!r} should fail")
            self.assertIn("正整数", err_text(errors))

    def test_p0a_stub_without_context_fails(self):
        item = self._ok_item(answer="A", question_text_status="stub")   # no source_pages / assets
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("stub", err_text(errors))

    def test_p0a_page_reference_without_source_fails(self):
        item = self._ok_item(answer="A", question_text_status="page_reference")  # no source_file/pages
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("page_reference", err_text(errors))

    def test_p0a_valid_page_reference_with_assets_passes(self):
        # a page_reference item WITH source_file+source_pages+valid assets must not trip either
        # the page_reference-missing-source error or the requires_assets fail-closed error
        item = self._asset_item(question_text_status="page_reference")
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))
        self.assertNotIn("page_reference", err_text(errors))
        self.assertNotIn("fail-closed", err_text(errors))

    def test_p0a_requires_false_with_assets_is_valid(self):
        item = self._asset_item(requires_assets=False, question_text_status="full")
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_requires_true_on_non_diagram_type_is_valid(self):
        item = self._asset_item(type="subjective", keywords=["x"])   # not a diagram, but figure-dependent
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_fixture_venn_item_validates(self):
        errors, warnings, stats, code = run("valid_workspace_assets")
        self.assertEqual(code, 0, err_text(errors))
        self.assertEqual(stats.get("quiz_types", {}).get("diagram"), 1)

    def test_p0a_examquiz_skill_has_failclosed_rule(self):
        with open(os.path.join(ROOT, "skills", "exam-quiz", "SKILL.md"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("requires_assets", txt)
        self.assertIn("fail-closed", txt)

    def test_p0a_fileformat_documents_asset_fields(self):
        with open(os.path.join(ROOT, "docs", "file-format.md"), encoding="utf-8") as f:
            txt = f.read()
        for field in ("requires_assets", "question_text_status", "source_pages", "assets"):
            self.assertIn(field, txt)

    def test_p0a_no_new_dependencies(self):
        with open(os.path.join(ROOT, "scripts", "validate_workspace.py"), encoding="utf-8") as f:
            src = f.read()
        for dep in ("pypdf", "pdfplumber", "pypdfium", "import requests", "import numpy", "import PIL"):
            self.assertNotIn(dep, src)

    # ---- Codex round-2 hardening (5 × P2) ----
    def test_p0a_requires_assets_string_false_is_rejected(self):
        # "false" must NOT be coerced truthy, and a non-boolean requires_assets is a schema error
        item = self._asset_item(requires_assets="false")
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("requires_assets 必须是布尔型", err_text(errors))

    def test_p0a_answer_side_only_asset_fails_question_gate(self):
        # a required item whose ONLY asset is answer-side can't be shown before asking -> fail-closed
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": "answer_context",
                                         "type": "page_image", "caption": "sol"}])
        errors, _, _ = V.validate(self._ws_asset(item))   # file exists, but role is answer-side
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("题面侧", err_text(errors))

    def test_p0a_malformed_role_does_not_crash(self):
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": ["x"],
                                         "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item))   # must report, not raise TypeError
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("role 非法", err_text(errors))

    def test_p0a_malformed_type_does_not_crash(self):
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": "figure",
                                         "type": {"k": 1}}])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("type 非法", err_text(errors))

    def test_p0a_malformed_question_text_status_does_not_crash(self):
        item = self._asset_item(question_text_status=["page_reference"])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("question_text_status 非法", err_text(errors))

    def test_p0a_stub_with_only_missing_asset_fails(self):
        # stub + an asset that is declared but missing on disk + no source_pages -> not standalone
        item = self._asset_item(question_text_status="stub", requires_assets=False,
                                source_pages=None,
                                assets=[{"path": "references/assets/a.png", "role": "figure",
                                         "type": "page_image"}])
        item.pop("source_pages", None)
        errors, _, _ = V.validate(self._ws_asset(item, create=False))  # asset file NOT created
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("stub", err_text(errors))

    # ---- Codex round-3 hardening (3 × P2) ----
    def test_p0a_stub_with_only_answer_side_asset_fails(self):
        # stub with no source_pages and ONLY an answer-side asset (exists) is not standalone
        item = self._asset_item(question_text_status="stub", requires_assets=False, source_pages=None,
                                assets=[{"path": "references/assets/a.png", "role": "answer_context",
                                         "type": "page_image"}])
        item.pop("source_pages", None)
        errors, _, _ = V.validate(self._ws_asset(item))   # file exists, but answer-side only
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("stub", err_text(errors))

    def test_p0a_page_reference_nonstring_source_file_fails(self):
        for bad in ({"f": "ch01.pdf"}, ["ch01.pdf"], "   "):
            item = self._asset_item(question_text_status="page_reference", source_file=bad)
            errors, _, _ = V.validate(self._ws_asset(item))
            self.assertEqual(V._exit_code(errors), 1, "source_file=%r should fail" % (bad,))

    def test_p0a_unreadable_required_asset_fails(self):
        item = self._asset_item()                          # requires_assets=true, question_context
        d = self._ws_asset(item)                           # asset file exists on disk
        ap = os.path.join(d, "references", "assets", "a.png")
        real_access = os.access
        with mock.patch.object(V.os, "access",
                               side_effect=lambda p, m: False if os.path.abspath(p) == os.path.abspath(ap)
                               else real_access(p, m)):
            errors, _, _ = V.validate(d)                   # exists but mocked unreadable -> fail-closed
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("不可读", err_text(errors))

    # ---- Codex round-4 hardening (P2) ----
    def test_p0a_source_file_escape_paths_fail(self):
        # incl. C:lecture.pdf — a drive-RELATIVE path (no slash) that still resolves outside materials
        for bad in ("../../etc/passwd", "/etc/passwd", "C:\\\\Windows\\\\x.pdf", "C:lecture.pdf", "http://x/y.pdf"):
            item = self._asset_item(question_text_status="page_reference", source_file=bad)
            errors, _, _ = V.validate(self._ws_asset(item))
            self.assertEqual(V._exit_code(errors), 1, "source_file=%r should fail" % bad)
            self.assertIn("不安全", err_text(errors))

    def test_p0a_subdir_source_file_ok(self):
        # a subdir provenance name (from the P0B builder) is fine — not traversal
        item = self._asset_item(question_text_status="page_reference", source_file="lecture/ch01.pdf")
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_stub_with_source_pages_but_no_source_file_fails(self):
        item = self._asset_item(question_text_status="stub", requires_assets=False,
                                source_file=None, source_pages=[12], assets=None)
        item.pop("source_file", None)
        item.pop("assets", None)
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)   # source_pages alone (no source_file) is ambiguous
        self.assertIn("stub", err_text(errors))

    # ---- P0-V1: visual-first future-compatible gate ----
    def test_p0v1_maybe_requires_assets_valid_question_side_passes(self):
        item = self._asset_item(requires_assets=False, maybe_requires_assets=True)
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0v1_maybe_requires_assets_missing_file_fails(self):
        item = self._asset_item(requires_assets=False, maybe_requires_assets=True)
        errors, _, _ = V.validate(self._ws_asset(item, create=False))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("maybe_requires_assets=true", err_text(errors))
        self.assertIn("不存在", err_text(errors))

    def test_p0v1_maybe_requires_assets_answer_side_only_fails(self):
        item = self._asset_item(requires_assets=False, maybe_requires_assets=True,
                                assets=[{"path": "references/assets/a.png", "role": "worked_solution",
                                         "type": "page_image", "caption": "solution"}])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("题面侧", err_text(errors))

    def test_p0v1_maybe_requires_assets_non_bool_rejected(self):
        item = self._asset_item(requires_assets=False, maybe_requires_assets="true")
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("maybe_requires_assets 必须是布尔型", err_text(errors))


class CheatsheetTraceLint(unittest.TestCase):
    """v4-P5 溯源 lint：cheatsheet.md 每个顶层要点必须携带可解析的 notebook/mistakes/wiki 锚点。"""

    # 笔记本章文件的真实形态：notebook.py 的条目标题 `## [#q1] 链表访问代价` → GitHub 锚
    # `q1-链表访问代价`；另有一个手写小节标题（锚 `手写小节`）与围栏内的假标题（不产生锚）。
    NB_CONTENT = ("# nb\n\n## [#q1] 链表访问代价\n\n> 精讲 · 2026-07-01 12:00\n\n正文。\n\n---\n\n"
                  "## 手写小节\n\n补充说明。\n\n```\n## [#fake] 围栏内不是标题\n```\n")

    def _ws(self, cheatsheet=None, walkthrough=None, notebook_files=(),
            notebook_content=NB_CONTENT):
        d = tempfile.mkdtemp(prefix="vws-cs-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "references", "wiki"))
        open(os.path.join(d, "references", "wiki", "ch1.md"), "w", encoding="utf-8").write("# ch1\n")
        open(os.path.join(d, "references", "quiz_bank.json"), "w", encoding="utf-8").write(json.dumps(
            [{"id": "q1", "chapter": 1, "type": "subjective", "question": "q",
              "keywords": ["a"], "answer": "a", "source": "teacher"}], ensure_ascii=False))
        open(os.path.join(d, "study_plan.md"), "w", encoding="utf-8").write(
            "阶段 1 `references/wiki/ch1.md`\n")
        open(os.path.join(d, "study_progress.md"), "w", encoding="utf-8").write(
            "## 当前复习断点\n阶段 1\n\n## 💡 概念疑难点记录\n")
        for nf in notebook_files:
            full = os.path.join(d, *nf.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w", encoding="utf-8").write(notebook_content)
        if cheatsheet is not None:
            open(os.path.join(d, "cheatsheet.md"), "w", encoding="utf-8").write(cheatsheet)
        if walkthrough is not None:
            open(os.path.join(d, "walkthrough.md"), "w", encoding="utf-8").write(walkthrough)
        return d

    def test_traced_bullets_pass(self):
        d = self._ws(cheatsheet=(
            "# 小抄\n\n## 必背\n"
            "- 链表访问 O(n)（[→](notebook/ch01.md#q1-链表访问代价)）\n"
            "- 快排不稳定（[→](references/wiki/ch1.md)）\n"),
            notebook_files=("notebook/ch01.md",))
        errors, warnings, stats = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))
        self.assertEqual(stats.get("cheatsheet_bullets"), 2)

    def test_untraced_bullet_is_error(self):
        d = self._ws(cheatsheet="# 小抄\n- 凭空要点，没有任何来源链接\n")
        errors, _, _ = V.validate(d)
        self.assertTrue(any("无溯源链接" in e["msg"] for e in errors), err_text(errors))

    def test_dead_link_target_is_error(self):
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](notebook/ch99.md#gone)）\n")
        errors, _, _ = V.validate(d)
        self.assertTrue(any("链接目标不存在" in e["msg"] for e in errors), err_text(errors))

    # ---- Codex 评审回归：锚点也要校验，#typo 指向存在的文件同样是死链 ----

    def test_bad_anchor_on_existing_file_is_error(self):
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](notebook/ch01.md#typo)）\n",
                     notebook_files=("notebook/ch01.md",))
        errors, _, _ = V.validate(d)
        self.assertTrue(any("坏锚点" in e["msg"] and "#typo" in e["msg"] for e in errors),
                        err_text(errors))

    def test_mistakes_anchor_validated_too(self):
        good = self._ws(cheatsheet="# 小抄\n- 要点（[→](mistakes/ch01.md#q1-链表访问代价)）\n",
                        notebook_files=("mistakes/ch01.md",))
        errors, _, _ = V.validate(good)
        self.assertEqual([e for e in errors], [], err_text(errors))
        bad = self._ws(cheatsheet="# 小抄\n- 要点（[→](mistakes/ch01.md#q99)）\n",
                       notebook_files=("mistakes/ch01.md",))
        errors, _, _ = V.validate(bad)
        self.assertTrue(any("坏锚点" in e["msg"] for e in errors), err_text(errors))

    def test_handwritten_heading_anchor_passes(self):
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](notebook/ch01.md#手写小节)）\n",
                     notebook_files=("notebook/ch01.md",))
        errors, _, _ = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))

    def test_fenced_fake_heading_is_not_an_anchor(self):
        # 围栏内的 `## [#fake] …` 是内容不是标题——链接它必须判坏锚
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](notebook/ch01.md#fake-围栏内不是标题)）\n",
                     notebook_files=("notebook/ch01.md",))
        errors, _, _ = V.validate(d)
        self.assertTrue(any("坏锚点" in e["msg"] for e in errors), err_text(errors))

    def test_wiki_target_stays_file_level(self):
        # references/wiki 目标：文件存在即可，锚点不管（章节文件没有保证的标题结构）
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](references/wiki/ch1.md#任意锚)）\n")
        errors, _, _ = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))

    def test_anchor_matches_notebook_engine_slug(self):
        # 锚点词汇与 notebook 引擎同源：entry_anchor 生成的锚必过 lint
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import notebook as N
        anchor = N.entry_anchor("q1", "链表访问代价")
        d = self._ws(cheatsheet="# 小抄\n- 要点（[→](notebook/ch01.md#%s)）\n" % anchor,
                     notebook_files=("notebook/ch01.md",))
        errors, _, _ = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))

    def test_fenced_and_nested_bullets_exempt(self):
        d = self._ws(cheatsheet=(
            "# 小抄\n- 顶层要点（[→](references/wiki/ch1.md)）\n"
            "  - 缩进子弹不查溯源\n"
            "```\n- 围栏内示例也不查\n```\n"))
        errors, _, stats = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))
        self.assertEqual(stats.get("cheatsheet_bullets"), 1)

    def test_legacy_walkthrough_warns(self):
        d = self._ws(walkthrough="# 旧小抄\n")
        errors, warnings, _ = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))
        self.assertTrue(any("walkthrough.md" in w["msg"] for w in warnings))

    def test_no_cheatsheet_no_new_messages(self):
        d = self._ws()
        errors, warnings, _ = V.validate(d)
        self.assertEqual([e for e in errors], [], err_text(errors))
        self.assertFalse(any("walkthrough" in w["msg"] for w in warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
