#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for scripts/validate_workspace.py (Tier-0 unit, stdlib only, no network/LLM).

    python -m unittest discover -s tests -v
"""
import io
import os
import sys
import json
import base64
import hashlib
import shutil
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import validate_workspace as V  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")

# A real 1x1 RGBA PNG.  Keep visual fixtures structurally valid so tests that
# exercise asset roles/path safety do not accidentally fail at the earlier
# signature gate.
MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)

# Produced by Pillow from Image.new("RGB", (1, 1), "white").save(..., "JPEG").
VALID_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEG"
    "E1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RF"
    "RkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKj"
    "pKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP0"
    "9fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgEC"
    "BAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLR"
    "ChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
    "dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbH"
    "yMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iii"
    "gD//2Q=="
)


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

    def test_extended_compiled_quiz_fields_satisfy_type_validators(self):
        items = [{
            "id": "code-contract",
            "chapter": 1,
            "type": "code",
            "question": "Implement solve(values).",
            "answer": "return sorted(values)",
            "source": "material",
            "gradable": True,
            "question_text_status": "full",
            "language": "python",
            "expected_behavior": "Return ascending values.",
            "tests": ["assert solve([2, 1]) == [1, 2]"],
            "source_language": "en",
            "answer_source_language": "en",
        }, {
            "id": "diagram-contract",
            "chapter": 1,
            "type": "diagram",
            "question": "Draw the final tree.",
            "answer": "A balanced tree.",
            "source": "material",
            "gradable": True,
            "question_text_status": "full",
            "diagram_type": "avl_tree",
        }]
        d = self.make_ws(items)
        errors, warnings, _stats = V.validate(d)
        self.assertEqual([], errors, err_text(errors))
        warning_messages = warn_text(warnings)
        self.assertNotIn("diagram_type", warning_messages)
        self.assertNotIn("expected_behavior/tests", warning_messages)

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

    def write_teaching_examples(self, ws, items):
        path = os.path.join(ws, "references", "teaching_examples.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        return path

    def teaching_example(self, **over):
        item = {
            "id": "lecture_example_1_2", "chapter": 1,
            "type": "subjective", "question": "A completed worked example.",
            "answer_status": "unknown", "source": "material",
            "source_file": "ch01.pdf", "source_pages": [3],
            "teaching_role": "worked_example", "assets": [],
        }
        item.update(over)
        return item

    def test_teaching_manifest_may_overlap_quiz_bank_and_is_counted(self):
        example = self.teaching_example()
        d = self.make_ws([example])
        self.write_teaching_examples(d, [example])
        errors, _, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))
        self.assertEqual(stats["teaching_examples"], 1)
        self.assertEqual(stats["teaching_quiz_overlap"], 1)

    def test_teaching_manifest_retains_example_after_quiz_bank_removal(self):
        example = self.teaching_example()
        d = self.make_ws([])
        self.write_teaching_examples(d, [example])
        with open(os.path.join(d, "ingest_report.json"), "w", encoding="utf-8") as f:
            json.dump({"teaching_example_ids": [example["id"]]}, f)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))
        self.assertFalse(any(example["id"] in w["msg"] for w in warnings))
        self.assertEqual(stats["teaching_examples_retained"], 1)

    def test_expected_example_missing_from_both_layers_is_blocking_error(self):
        d = self.make_ws([])
        self.write_teaching_examples(d, [])
        with open(os.path.join(d, "ingest_report.json"), "w", encoding="utf-8") as f:
            json.dump({"teaching_example_ids": ["lecture_example_1_2"]}, f)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1, err_text(errors))
        self.assertTrue(any("lecture_example_1_2" in e["msg"] for e in errors))
        self.assertEqual(stats["teaching_examples_missing_from_both"], 1)

    def test_append_only_baseline_cannot_be_shrunk_by_rewritten_ingest_report(self):
        d = self.make_ws([])
        self.write_teaching_examples(d, [])
        with open(os.path.join(d, "ingest_report.json"), "w", encoding="utf-8") as f:
            json.dump({"teaching_example_ids": [],
                       "teaching_example_ids_by_chapter": {}}, f)
        with open(os.path.join(d, "references", "teaching_baseline.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "policy": "append_only",
                       "teaching_example_ids": ["lecture_example_1_2"],
                       "teaching_example_ids_by_chapter": {
                           "1": ["lecture_example_1_2"]}}, f)
        errors, _, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1, err_text(errors))
        self.assertTrue(any("lecture_example_1_2" in e["msg"] for e in errors))
        self.assertEqual(stats["teaching_examples_missing_from_both"], 1)

    def test_duplicate_teaching_example_ids_are_rejected(self):
        d = self.make_ws([])
        example = self.teaching_example()
        self.write_teaching_examples(d, [example, dict(example)])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("lecture_example_1_2", err_text(errors))

    def test_teaching_answer_source_file_path_is_validated_without_quiz_copy(self):
        d = self.make_ws([])
        example = self.teaching_example(
            answer_source_file="../outside.pdf", answer_source_pages=[4])
        self.write_teaching_examples(d, [example])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("answer_source_file", err_text(errors))

    def test_teaching_visual_gate_requires_readable_prompt_side_asset(self):
        d = self.make_ws([])
        os.makedirs(os.path.join(d, "references", "assets"))
        asset_path = os.path.join(d, "references", "assets", "answer.png")
        with open(asset_path, "wb") as stream:
            stream.write(MINIMAL_PNG)
        example = self.teaching_example(
            requires_assets=True,
            assets=[{"path": "references/assets/answer.png", "role": "answer_context",
                     "type": "page_image"}])
        self.write_teaching_examples(d, [example])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("题面侧", err_text(errors))

    def test_teaching_visual_gate_rejects_corrupt_jpeg(self):
        d = self.make_ws([])
        relative = "references/assets/prompt.jpg"
        full = os.path.join(d, *relative.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as stream:
            stream.write(b"\xff\xd8\xff\xd9")
        example = self.teaching_example(
            requires_assets=True,
            assets=[{
                "path": relative, "role": "question_context", "type": "page_image",
            }],
        )
        self.write_teaching_examples(d, [example])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("光栅图像损坏", err_text(errors))

    def test_teaching_asset_without_role_is_rejected_before_renderer(self):
        d = self.make_ws([])
        os.makedirs(os.path.join(d, "references", "assets"))
        with open(os.path.join(d, "references", "assets", "a.png"), "wb") as f:
            f.write(b"\x89PNG\r\n")
        example = self.teaching_example(
            assets=[{"path": "references/assets/a.png", "type": "page_image"}])
        self.write_teaching_examples(d, [example])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("role 非法", err_text(errors))

    def test_teaching_visual_flags_must_be_real_booleans(self):
        d = self.make_ws([])
        self.write_teaching_examples(d, [self.teaching_example(maybe_requires_assets="true")])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("maybe_requires_assets 必须是布尔型", err_text(errors))

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

    def test_legacy_non_gradable_item_is_excluded_from_quiz_readiness(self):
        d = self.make_ws([{
            "id": "worked-only", "chapter": 1, "type": "subjective",
            "question": "Completed demonstration", "gradable": False,
            "answer_status": "unknown", "source": "material",
        }])
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))
        self.assertFalse(any("worked-only" in w["msg"] for w in warnings))
        self.assertEqual(stats["quiz_items"], 1)
        self.assertEqual(stats["quiz_items_gradable"], 0)
        self.assertEqual(stats["quiz_items_non_gradable"], 1)
        self.assertEqual(stats["quiz_types"], {})

    def test_quiz_and_teaching_gradable_flags_must_be_booleans(self):
        d = self.make_ws([{
            "id": "bad", "chapter": 1, "type": "subjective", "question": "q",
            "answer": "a", "gradable": "false",
        }])
        self.write_teaching_examples(d, [self.teaching_example(gradable=0)])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertGreaterEqual(err_text(errors).count("gradable 必须是布尔型"), 2)

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
        self.assertIn(payload["readiness"], {"ready", "usable_with_gaps"})
        for key in ("errors", "warnings", "stats"):
            self.assertIn(key, payload)

    def test_readiness_distinguishes_warnings_from_structural_errors(self):
        warn_ws = os.path.join(FX, "warnings_workspace")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = V.main([warn_ws, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["readiness"], "usable_with_gaps")

        broken_ws = os.path.join(FX, "invalid_workspace_missing_quizbank")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = V.main([broken_ws, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertNotEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["readiness"], "blocked")

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
            open(ap, "wb").write(MINIMAL_PNG)
        return d

    def test_p0a_old_quizbank_without_asset_fields_still_valid(self):
        d = self.make_ws([self._ok_item(answer="A")])           # none of the new fields
        self.assertEqual(V._exit_code(V.validate(d)[0]), 0)

    def test_p0a_valid_assets_pass(self):
        errors, _, _ = V.validate(self._ws_asset(self._asset_item()))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_valid_jpeg_asset_passes_shared_raster_gate(self):
        relative = "references/assets/a.jpg"
        item = self._asset_item(assets=[{
            "path": relative, "role": "question_context", "type": "page_image",
        }])
        d = self.make_ws([item])
        full = os.path.join(d, *relative.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as stream:
            stream.write(VALID_JPEG)
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))

    def test_p0a_corrupt_jpeg_asset_fails_shared_raster_gate(self):
        relative = "references/assets/a.jpg"
        item = self._asset_item(assets=[{
            "path": relative, "role": "question_context", "type": "page_image",
        }])
        d = self.make_ws([item])
        full = os.path.join(d, *relative.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as stream:
            stream.write(b"\xff\xd8\xff\xd9")
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("光栅图像损坏", err_text(errors))

    def test_all_advertised_raster_extensions_route_to_shared_gate(self):
        d = tempfile.mkdtemp(prefix="raster-gate-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        malformed = {
            ".png": b"\x89PNG\r\n\x1a\nnot-a-png",
            ".jpg": b"\xff\xd8\xff\xd9",
            ".jpeg": b"\xff\xd8\xff\xd9",
            ".gif": b"GIF89a\x01\x00\x01\x00\x00\x00\x00;",
            ".webp": b"RIFF\x0c\x00\x00\x00WEBPVP8 \x04\x00\x00\x00bad!",
            ".bmp": b"BM" + b"\x00" * 52,
        }
        for extension, payload in malformed.items():
            with self.subTest(extension=extension):
                path = os.path.join(d, "asset" + extension)
                with open(path, "wb") as stream:
                    stream.write(payload)
                self.assertIsNotNone(
                    V._raster_file_validation_error(path, path)
                )
        extensionless = os.path.join(d, "typed-asset")
        with open(extensionless, "wb") as stream:
            stream.write(b"\xff\xd8\xff\xd9")
        self.assertIsNotNone(V._raster_file_validation_error(
            extensionless, "references/assets/typed-asset", "image/jpeg"
        ))

    def test_p0a_explicit_asset_source_overrides_answer_role_fallback(self):
        item = self._asset_item(answer_source_file="official-solutions.pdf")
        asset = {
            "path": "references/assets/a.png",
            "role": "answer_context",
            "source_file": "submitted-homework.pdf",
        }
        field, source_file = V._asset_source_binding(
            asset, asset["role"], item, 1
        )
        self.assertEqual("assets[1].source_file", field)
        self.assertEqual("submitted-homework.pdf", source_file)

    def test_p0a_legacy_answer_asset_uses_answer_source_fallback(self):
        item = self._asset_item(answer_source_file="official-solutions.pdf")
        asset = {"path": "references/assets/a.png", "role": "answer_context"}
        field, source_file = V._asset_source_binding(
            asset, asset["role"], item, 0
        )
        self.assertEqual("answer_source_file", field)
        self.assertEqual("official-solutions.pdf", source_file)

    def test_p0a_asset_source_file_escape_is_rejected(self):
        item = self._asset_item(assets=[{
            "path": "references/assets/a.png",
            "role": "question_context",
            "type": "page_image",
            "source_file": "../outside.pdf",
        }])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("source_file", err_text(errors))

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
        # Asset safety walks every path component through the shared
        # link/junction/reparse detector rather than checking only the leaf.
        with mock.patch.object(V, "is_link_or_reparse", side_effect=lambda p: p == link):
            errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertTrue(any("reparse point" in e["msg"] for e in errors))

    def test_p0a_invalid_asset_role_fails(self):
        item = self._asset_item(assets=[{"path": "references/assets/a.png", "role": "bogus",
                                         "type": "page_image"}])
        errors, _, _ = V.validate(self._ws_asset(item))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("role 非法", err_text(errors))

    def test_p0a_asset_without_role_is_rejected_before_renderer(self):
        item = self._asset_item(
            requires_assets=False, question_text_status="full",
            assets=[{"path": "references/assets/a.png", "type": "page_image"}])
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

    def test_falsely_full_truncated_lecture_prompt_fails_closed(self):
        item = self._ok_item(
            id="lecture_example_1_21",
            type="subjective",
            question=("Example 1.21 Problem Suppose that for the experiment monitoring "
                      "three purchasing decisions in"),
            answer="They are not independent.",
            keywords=["independent"],
            source="material",
            source_type="example",
            source_file="ch01.pdf",
            source_pages=[77],
            requires_assets=False,
            question_text_status="full",
        )
        errors, _, _ = V.validate(self.make_ws([item]))
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("交叉引用处被截断", err_text(errors))
        self.assertIn("question_text_status=full", err_text(errors))

    def test_complete_lecture_prompt_with_inline_quiz_reference_passes(self):
        item = self._ok_item(
            id="lecture_example_1_25",
            type="subjective",
            question=("Example 1.25 Problem Use Matlab to generate 12 random student test scores "
                      "T as described in Quiz 1.3."),
            answer="Use randi and shift the result.",
            keywords=["randi"],
            source="material",
            source_type="example",
            source_file="ch01.pdf",
            source_pages=[91],
            requires_assets=False,
            question_text_status="full",
        )
        errors, _, _ = V.validate(self.make_ws([item]))
        self.assertEqual(V._exit_code(errors), 0, err_text(errors))
        self.assertNotIn("交叉引用处被截断", err_text(errors))

    def test_falsely_full_truncated_teaching_example_fails_closed(self):
        d = self.make_ws([self._ok_item()])
        self.write_teaching_examples(d, [self.teaching_example(
            id="lecture_example_1_25",
            question=("Example 1.25 Problem Use Matlab to generate 12 random student test scores "
                      "T as described in"),
            requires_assets=False,
            question_text_status="full",
        )])
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("教学例题[lecture_example_1_25]", err_text(errors))
        self.assertIn("交叉引用处被截断", err_text(errors))

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

    def test_student_attempt_role_is_valid_but_never_satisfies_question_gate(self):
        item = self._asset_item(assets=[{
            "path": "references/assets/a.png", "role": "student_attempt",
            "type": "crop_image", "caption": "submitted work",
        }])
        errors, _, _ = V.validate(self._ws_asset(item))
        messages = err_text(errors)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertNotIn("role 非法", messages)
        self.assertIn("student_attempt", messages)

    def _policy_asset_workspace(self, items, teaching=None):
        ws = self.make_ws(items)
        os.makedirs(os.path.join(ws, "references", "assets"), exist_ok=True)
        for name in ("shared.png", "other.png"):
            with open(os.path.join(ws, "references", "assets", name), "wb") as stream:
                stream.write(MINIMAL_PNG)
        if teaching is not None:
            self.write_teaching_examples(ws, teaching)
        return ws

    def test_student_attempt_taint_is_order_independent_across_two_quiz_items(self):
        official = self._ok_item(
            id="official", assets=[{
                "path": "references/assets/shared.png", "role": "question_context",
                "type": "crop_image",
            }])
        attempt = self._ok_item(
            id="attempt", assets=[{
                "path": "references/assets/shared.png", "role": "student_attempt",
                "type": "crop_image",
            }])
        for rows in ([official, attempt], [attempt, official]):
            errors, _, _ = V.validate(self._policy_asset_workspace(rows))
            self.assertIn("student-attempt asset policy conflict", err_text(errors))

    def test_student_attempt_taint_crosses_quiz_and_teaching_layers(self):
        official = self._ok_item(assets=[{
            "path": "references/assets/shared.png", "role": "answer_context",
            "type": "crop_image",
        }])
        teaching = self.teaching_example(assets=[{
            "path": "references/assets/shared.png", "role": "student_attempt",
            "type": "crop_image",
        }])
        errors, _, _ = V.validate(self._policy_asset_workspace([official], [teaching]))
        self.assertIn("student-attempt asset policy conflict", err_text(errors))

    def test_public_policy_snapshot_cannot_accept_or_reuse_partial_rows(self):
        official = self._ok_item(id="official", assets=[{
            "path": "references/assets/shared.png", "role": "question_context",
            "type": "crop_image",
        }])
        attempt = self.teaching_example(id="attempt", assets=[{
            "path": "references/assets/shared.png", "role": "student_attempt",
            "type": "crop_image",
        }])
        ws = self._policy_asset_workspace([official], [attempt])

        first = V.workspace_asset_policy_snapshot(ws)
        self.assertTrue(first["tainted_keys"])
        self.assertTrue(first["conflicts"])

        # The old public kwargs let an arbitrary caller replace every live layer with an empty
        # hand-built cache.  The safe public API no longer has such an override surface.
        with self.assertRaises(TypeError):
            V.workspace_asset_policy_snapshot(
                ws, quiz_rows=[], teaching_rows=[], content_units=[]
            )

        # Mutating a returned diagnostic copy also cannot affect the next live snapshot.
        first["quiz_rows"].clear()
        first["teaching_rows"].clear()
        second = V.workspace_asset_policy_snapshot(ws)
        self.assertTrue(second["tainted_keys"])
        self.assertTrue(second["conflicts"])

    def test_same_item_slash_alias_cannot_be_prompt_and_answer(self):
        item = self._ok_item(assets=[
            {"path": "references/assets/shared.png", "role": "question_context",
             "type": "crop_image"},
            {"path": "references\\assets\\shared.png", "role": "worked_solution",
             "type": "crop_image"},
        ])
        errors, _, _ = V.validate(self._policy_asset_workspace([item]))
        self.assertIn("both prompt and official answer", err_text(errors))

    @unittest.skipUnless(os.name == "nt", "Windows physical identity folds path case")
    def test_same_item_windows_case_alias_cannot_be_prompt_and_answer(self):
        item = self._ok_item(assets=[
            {"path": "references/assets/shared.png", "role": "question_context",
             "type": "crop_image"},
            {"path": "references/assets/SHARED.png", "role": "worked_solution",
             "type": "crop_image"},
        ])
        errors, _, _ = V.validate(self._policy_asset_workspace([item]))
        self.assertIn("both prompt and official answer", err_text(errors))

    def test_different_items_may_reuse_one_official_path_on_opposite_sides(self):
        prompt = self._ok_item(id="prompt", assets=[{
            "path": "references/assets/shared.png", "role": "question_context",
            "type": "crop_image",
        }])
        answer = self._ok_item(id="answer", assets=[{
            "path": "references/assets/shared.png", "role": "worked_solution",
            "type": "crop_image",
        }])
        errors, _, _ = V.validate(self._policy_asset_workspace([prompt, answer]))
        self.assertEqual([], errors, err_text(errors))

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

    # ---- v4.1 Step 4: phase evidence gate ----

    def _phase_gate_ws(self, status=None, checkpoint=True, done=True):
        quiz = [self._ok_item(id="q1"),
                self._ok_item(id="ex1", source_type="example", question="example")]
        d = self.make_ws(quiz, plan="## 阶段 1：基础 `references/wiki/ch1.md`\n")
        os.makedirs(os.path.join(d, "notebook"))
        open(os.path.join(d, "notebook", "ch01.md"), "w", encoding="utf-8").write(
            "# 第一章\n\n## [#ex1] 例题一\n")
        teaching_path = os.path.join(d, "references", "teaching_examples.json")
        with open(teaching_path, "w", encoding="utf-8") as f:
            json.dump([self.teaching_example(id="ex1")], f, ensure_ascii=False)

        def digest(rel):
            with open(os.path.join(d, *rel.split("/")), "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        materials_root = os.path.join(os.path.dirname(d), "materials")
        os.makedirs(materials_root, exist_ok=True)
        source_pdf = os.path.join(materials_root, "course.pdf")
        open(source_pdf, "wb").write(b"%PDF-validator-source")
        integrity = {
            "schema_version": 2,
            "generated_at": "2026-07-13T00:00:00Z",
            "mode": {"materials_scan": True, "apply_questions": False,
                     "apply_wiki": False, "backend": "fake",
                     "materials_root": os.path.abspath(materials_root)},
            "inputs": {
                "references/quiz_bank.json": {
                    "sha256": digest("references/quiz_bank.json")},
                "references/teaching_examples.json": {
                    "sha256": digest("references/teaching_examples.json")},
                "references/wiki/ch1.md": {
                    "sha256": digest("references/wiki/ch1.md")},
            },
            "materials": {"course.pdf": {
                "sha256": hashlib.sha256(open(source_pdf, "rb").read()).hexdigest()}},
            "material_inventory_sha256": hashlib.sha256(
                json.dumps(["course.pdf"], ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")).hexdigest(),
        }
        figure_index = {"integrity": integrity, "wiki_visual_coverage": {
            "detected": 0, "embedded": 0, "missing": 0,
            "deferred_answer_count": 0, "deferred_answer_pages": [],
            "manual_answer_exposure_count": 0, "manual_answer_exposure_pages": [],
            "shared_prompt_answer_count": 0, "shared_prompt_answer_pages": [],
            "shared_prompt_answer_blocker_count": 0,
            "shared_prompt_answer_blocker_pages": [],
            "per_chapter": {}, "pages": []}}
        image_index = {"integrity": integrity, "prompt_suspects": [], "answer_suspects": []}
        outputs = {}
        for name, value in (("figure_page_index.json", figure_index),
                            ("image_question_index.json", image_index)):
            payload = {key: val for key, val in value.items() if key != "integrity"}
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
            outputs[name] = {"sha256": hashlib.sha256(raw).hexdigest()}
        integrity["outputs"] = outputs
        with open(os.path.join(d, "references", "figure_page_index.json"),
                  "w", encoding="utf-8") as f:
            json.dump(figure_index, f)
        with open(os.path.join(d, "references", "image_question_index.json"),
                  "w", encoding="utf-8") as f:
            json.dump(image_index, f)
        record = {"wiki": ["references/wiki/ch1.md"],
                  "visual": ["references/figure_page_index.json"],
                  "teaching_examples": ["ex1"],
                  "notebook": ["notebook/ch01.md#ex1-例题一"]}
        if checkpoint:
            record["checkpoint"] = [{"id": "q1", "outcome": "passed"},
                                    {"id": "ex1", "outcome": "wrong"}]
        if status is not None:
            record["status"] = status
        state = {"version": 1, "current_phase": 1, "scope": None, "mode": None,
                 "time_budget": "le1d", "language": "zh", "preferences": {},
                 "mistake_archive": [], "confusion_log": [], "knowledge_window": [],
                 "phase_checklist": [{"text": "阶段 1：基础", "done": done}],
                 "phase_evidence": {} if status is None else {"1": record}}
        open(os.path.join(d, "study_state.json"), "w", encoding="utf-8").write(
            json.dumps(state, ensure_ascii=False))
        return d

    def test_phase_gate_rejects_done_manifest_phase_without_evidence(self):
        d = self._phase_gate_ws(status=None)
        errors, _, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 1)
        self.assertIn("phase_evidence", err_text(errors))

    def test_artifact_mode_unknown_warns_and_fails_safe_to_chat(self):
        d = self._phase_gate_ws(status="covered_unverified", checkpoint=False)
        path = os.path.join(d, "study_state.json")
        state = json.load(open(path, encoding="utf-8"))
        state["artifact_mode"] = "ultra-render"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        errors, warnings, stats = V.validate(d)
        self.assertFalse(any("artifact_mode" in e["msg"] for e in errors), err_text(errors))
        self.assertTrue(any("artifact_mode" in w["msg"] and "chat" in w["msg"]
                            for w in warnings), warnings)
        self.assertEqual(stats["artifact_mode_effective"], "chat")

    def test_artifact_mode_non_string_is_invalid(self):
        d = self._phase_gate_ws(status="covered_unverified", checkpoint=False)
        path = os.path.join(d, "study_state.json")
        state = json.load(open(path, encoding="utf-8"))
        state["artifact_mode"] = ["visual"]
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        errors, _, stats = V.validate(d)
        self.assertTrue(any("artifact_mode 必须是字符串" in e["msg"] for e in errors),
                        err_text(errors))
        self.assertEqual(stats["artifact_mode_effective"], "chat")

    def test_phase_gate_accepts_covered_unverified_without_checkpoint(self):
        d = self._phase_gate_ws(status="covered_unverified", checkpoint=False)
        errors, _, _ = V.validate(d)
        phase_errors = [e for e in errors if "phase_evidence" in e["msg"]]
        self.assertEqual(phase_errors, [], err_text(errors))

    def test_phase_gate_verified_requires_checkpoint(self):
        d = self._phase_gate_ws(status="verified", checkpoint=False)
        errors, _, _ = V.validate(d)
        self.assertTrue(any("phase_evidence" in e["msg"] and "checkpoint" in e["msg"]
                            for e in errors), err_text(errors))

    def test_phase_gate_verified_rejects_all_wrong_or_skipped(self):
        d = self._phase_gate_ws(status="verified", checkpoint=True)
        st_path = os.path.join(d, "study_state.json")
        st = json.load(open(st_path, encoding="utf-8"))
        st["phase_evidence"]["1"]["checkpoint"] = [
            {"id": "q1", "outcome": "wrong"}, {"id": "ex1", "outcome": "skipped"}]
        open(st_path, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False))
        errors, _, _ = V.validate(d)
        self.assertTrue(any("phase_evidence" in e["msg"] and "passed" in e["msg"]
                            for e in errors), err_text(errors))

    def test_phase_gate_checkpoint_id_only_is_invalid_schema(self):
        d = self._phase_gate_ws(status="verified", checkpoint=True)
        st_path = os.path.join(d, "study_state.json")
        st = json.load(open(st_path, encoding="utf-8"))
        st["phase_evidence"]["1"]["checkpoint"] = ["q1", "ex1"]
        open(st_path, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False))
        errors, _, _ = V.validate(d)
        self.assertTrue(any("只有 ID 不能证明答对" in e["msg"] for e in errors), err_text(errors))

    def test_phase_evidence_malformed_shape_is_error(self):
        d = self._phase_gate_ws(status="covered_unverified", checkpoint=False, done=False)
        st_path = os.path.join(d, "study_state.json")
        st = json.load(open(st_path, encoding="utf-8"))
        st["phase_evidence"] = []
        open(st_path, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False))
        errors, _, _ = V.validate(d)
        self.assertTrue(any("phase_evidence 必须是对象" in e["msg"] for e in errors), err_text(errors))

    # ---- v4.1: wiki visual completeness is warning-level, separate from runnable schema ----

    def test_wiki_visual_coverage_gap_warns_without_invalidating_workspace(self):
        d = self.make_ws([self._ok_item()])
        idx = {
            "generated_by": "build_visual_index.py",
            "wiki_visual_coverage": {
                "detected": 3, "embedded": 1, "missing": 2,
                "missing_pages": [
                    {"wiki_file": "ch1.md", "source_file": "ch01.pdf", "page": 4},
                    {"wiki_file": "ch1.md", "source_file": "ch01.pdf", "page": 5}],
            },
        }
        with open(os.path.join(d, "references", "figure_page_index.json"), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("wiki 视觉覆盖缺口", warn_text(warnings))
        self.assertEqual(stats["wiki_visual_detected"], 3)
        self.assertEqual(stats["wiki_visual_missing"], 2)

    def test_legacy_visual_index_without_coverage_warns_but_stays_valid(self):
        d = self.make_ws([self._ok_item()])
        with open(os.path.join(d, "references", "figure_page_index.json"), "w", encoding="utf-8") as f:
            json.dump({"generated_by": "old", "files": {}}, f)
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("未包含 wiki_visual_coverage", warn_text(warnings))

    def test_manual_answer_only_wiki_exposure_is_reported_as_completeness_gap(self):
        d = self.make_ws([self._ok_item()])
        idx = {"generated_by": "build_visual_index.py", "wiki_visual_coverage": {
            "detected": 0, "embedded": 0, "missing": 0,
            "deferred_answer_count": 1,
            "manual_answer_exposure_count": 1,
            "manual_answer_exposure_pages": [{
                "wiki_file": "ch1.md", "source_file": "solutions.pdf", "page": 9,
                "status": "deferred_answer", "coverage_issue": "manual_answer_exposure"}],
        }}
        with open(os.path.join(d, "references", "figure_page_index.json"),
                  "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("提前暴露", warn_text(warnings))
        self.assertEqual(stats["wiki_visual_deferred_answer"], 1)
        self.assertEqual(stats["wiki_manual_answer_exposure"], 1)

    def test_shared_prompt_answer_blocker_downgrades_static_readiness(self):
        d = self.make_ws([self._ok_item()])
        idx = {"generated_by": "build_visual_index.py", "wiki_visual_coverage": {
            "detected": 0, "embedded": 0, "missing": 0,
            "deferred_answer_count": 0, "manual_answer_exposure_count": 0,
            "shared_prompt_answer_count": 1,
            "shared_prompt_answer_blocker_count": 1,
            "shared_prompt_answer_blocker_pages": [{
                "wiki_file": "ch1.md", "source_file": "lecture.pdf", "page": 7,
                "status": "shared_prompt_answer",
                "blocker": "audited_question_side_crop_required"}],
        }}
        with open(os.path.join(d, "references", "figure_page_index.json"),
                  "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("题面与答案共页", warn_text(warnings))
        self.assertEqual(stats["wiki_visual_shared_prompt_answer"], 1)
        self.assertEqual(stats["wiki_shared_prompt_answer_blockers"], 1)
        self.assertEqual(V._readiness(errors, warnings), "usable_with_gaps")

    def test_wiki_nul_text_warns_with_filename(self):
        d = self.make_ws([self._ok_item()])
        wiki = os.path.join(d, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n<!-- ch01.pdf p.50 -->\nA\x00\x00B\n")
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("NUL", warn_text(warnings))
        self.assertIn("ch1.md", warn_text(warnings))

    def test_standard_dollar_math_is_not_reported_as_raw_latex(self):
        d = self.make_ws([self._ok_item()])
        wiki = os.path.join(d, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n$P(A \\cup B)$\n\n$$P(A)=\\frac{1}{2}$$\n")
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertNotIn("raw/伪分隔 LaTeX", warn_text(warnings))
        self.assertNotIn("raw_latex_files", stats)

    def test_pseudo_delimited_latex_warns_and_downgrades_readiness(self):
        d = self.make_ws([self._ok_item()])
        wiki = os.path.join(d, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\n(A \\cup B)\n[P(A)=\\frac{1}{2}.]\n")
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("raw/伪分隔 LaTeX", warn_text(warnings))
        self.assertEqual(stats["raw_latex_files"], 1)
        self.assertEqual(stats["raw_latex_occurrences"], 2)
        self.assertEqual(V._readiness(errors, warnings), "usable_with_gaps")

    def test_latex_in_code_examples_is_ignored(self):
        d = self.make_ws([self._ok_item()])
        wiki = os.path.join(d, "references", "wiki", "ch1.md")
        with open(wiki, "w", encoding="utf-8") as f:
            f.write("# ch1\nUse `\\frac{a}{b}` in source.\n```tex\n\\sum_i x_i\n```\n")
        errors, warnings, _ = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertNotIn("raw/伪分隔 LaTeX", warn_text(warnings))

    def test_notebook_raw_latex_is_linted_too(self):
        d = self.make_ws([self._ok_item()])
        os.makedirs(os.path.join(d, "notebook"))
        with open(os.path.join(d, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("# notes\nP(A)=\\frac{1}{2}\n")
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertIn("notebook/ch01.md", warn_text(warnings))
        self.assertEqual(stats["raw_latex_files"], 1)

    def test_prompt_and_answer_visual_suspects_are_counted_and_warned(self):
        d = self.make_ws([self._ok_item()])
        idx = {
            "suspects": [{"id": "q_prompt"}],
            "prompt_suspects": [{"id": "q_prompt"}],
            "answer_suspects": [{"id": "q_answer"}, {"id": "q_answer_2"}],
        }
        with open(os.path.join(d, "references", "image_question_index.json"), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        errors, warnings, stats = V.validate(d)
        self.assertEqual(V._exit_code(errors), 0)
        self.assertEqual(stats["visual_prompt_suspects"], 1)
        self.assertEqual(stats["visual_answer_suspects"], 2)
        joined = warn_text(warnings)
        self.assertIn("题面侧视觉疑漏", joined)
        self.assertIn("答案侧视觉疑漏", joined)


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
