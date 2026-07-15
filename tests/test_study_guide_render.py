# -*- coding: utf-8 -*-
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import study_guide_render as sgr  # noqa: E402

PY = sys.executable


def fake_math(latex, display="inline"):
    return '<math><mrow><mi>converted</mi></mrow></math>'


class WorkspaceMixin:
    def make_ws(self, wiki=None, teaching=None, quizzes=None, notebook=True, language=None):
        ws = tempfile.mkdtemp(prefix="study-guide-")
        os.makedirs(os.path.join(ws, "references", "wiki"))
        os.makedirs(os.path.join(ws, "references", "assets"))
        with open(os.path.join(ws, "references", "wiki", "ch01.md"), "w", encoding="utf-8") as f:
            f.write(wiki if wiki is not None else "# 集合\n\n并集是 $A \\cup B$。\n")
        with open(os.path.join(ws, "references", "wiki", "ch02.md"), "w", encoding="utf-8") as f:
            f.write("# SECRET-CH2\n")
        pixel = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with open(os.path.join(ws, "references", "assets", "prompt.png"), "wb") as f:
            f.write(pixel)
        with open(os.path.join(ws, "references", "assets", "answer.png"), "wb") as f:
            f.write(pixel)
        if teaching is not None:
            with open(os.path.join(ws, "references", "teaching_examples.json"), "w", encoding="utf-8") as f:
                json.dump(teaching, f, ensure_ascii=False)
        if quizzes is None:
            quizzes = []
        with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(quizzes, f, ensure_ascii=False)
        if notebook:
            os.makedirs(os.path.join(ws, "notebook"))
            with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
                f.write("# 本章笔记\n\n用生活比喻理解集合。\n")
            with open(os.path.join(ws, "notebook", "ch02.md"), "w", encoding="utf-8") as f:
                f.write("SECRET-NOTEBOOK-CH2\n")
        if language is not None:
            with open(os.path.join(ws, "study_state.json"), "w", encoding="utf-8") as f:
                json.dump({"language": language}, f, ensure_ascii=False)
        return ws


class HumanReadableGuide(WorkspaceMixin, unittest.TestCase):
    def _language_fixture(self, language):
        teaching = [{
            "id": "ex1", "chapter": 1, "title": "Union example", "question": "Find $A \\cup B$.",
            "answer": "Combine both sets.", "source": "material", "source_file": "ch01.pdf",
            "source_pages": [4], "answer_source_file": "ch01.pdf", "answer_source_pages": [5],
            "assets": [
                {"path": "references/assets/prompt.png", "role": "question_context"},
                {"path": "references/assets/answer.png", "role": "answer_context"},
            ],
        }]
        quizzes = [{
            "id": "q1", "chapter": 1, "type": "subjective", "question": "Define a union.",
            "answer": "Elements in either set.", "source": "teacher", "source_file": "quiz.pdf",
            "source_pages": [2],
        }]
        ws = self.make_ws(wiki="# Sets\n\nA union is $A \\cup B$.\n", teaching=teaching,
                          quizzes=quizzes, language=language)
        with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("# Notes\n\nThink of combining two labeled boxes.\n")
        return ws

    def test_chinese_state_dispatches_chinese_ui(self):
        ws = self._language_fixture("zh")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn('<html lang="zh-CN">', doc)
            self.assertIn("一、核心概念与课件内容", doc)
            self.assertIn("题面图", doc)
            self.assertIn("🟢 来自资料", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_english_state_has_english_ui_without_chinese_ui_leakage(self):
        ws = self._language_fixture("en")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn('<html lang="en">', doc)
            self.assertIn("1. Core Concepts and Course Materials", doc)
            self.assertIn("Question-side asset", doc)
            self.assertIn("Answer-side asset", doc)
            self.assertIn("🟢 From your materials", doc)
            self.assertIn("Show answer and explanation", doc)
            for leaked in ("核心概念", "题面图", "答案图", "展开答案", "来源标签", "本章"):
                self.assertNotIn(leaked, doc)
            self.assertNotRegex(doc, r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_bilingual_state_mirrors_ui_and_both_canonical_labels(self):
        ws = self._language_fixture("bilingual")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn('<html lang="mul">', doc)
            zh = '<span class="lang-block lang-zh" lang="zh-CN">'
            en = '<span class="lang-block lang-en" lang="en">&gt; EN: '
            self.assertIn(
                '<h2>%s一、核心概念与课件内容</span>%s1. Core Concepts and Course Materials</span></h2>'
                % (zh, en), doc,
            )
            self.assertIn(
                '<div class="asset-label">%s题面图 · question_context</span>'
                '%sQuestion-side asset · question_context</span></div>' % (zh, en), doc,
            )
            self.assertIn(
                '%s题面来源：ch01.pdf · p.4</span>%sQuestion source: ch01.pdf · p.4</span>'
                % (zh, en), doc,
            )
            self.assertIn(
                '%s答案来源：ch01.pdf · p.5</span>%sAnswer source: ch01.pdf · p.5</span>'
                % (zh, en), doc,
            )
            self.assertIn(
                '%s来源标签：🟢 来自资料</span>%sProvenance: 🟢 From your materials</span>'
                % (zh, en), doc,
            )
            self.assertIn(
                '<div class="asset-label">%s答案图 · answer_context</span>'
                '%sAnswer-side asset · answer_context</span></div>' % (zh, en), doc,
            )
            self.assertNotIn("\n&gt; EN:", doc)
            # Persisted course facts render once; only fixed UI is mirrored.  No machine translation
            # pass duplicates or rewrites the source material.
            self.assertEqual(doc.count("A union is"), 1)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_missing_quiz_answers_always_abstain_and_never_inherit_green_provenance(self):
        quizzes = [
            {"id": "missing-no-status", "chapter": 1, "question": "Question one",
             "source": "teacher", "source_file": "quiz.pdf", "source_pages": [1]},
            {"id": "missing-with-status", "chapter": 1, "question": "Question two",
             "answer_status": "unknown", "source": "material"},
            {"id": "missing-ai", "chapter": 1, "question": "Question three",
             "source": "ai_generated"},
            {"id": "missing-blank", "chapter": 1, "question": "Question four",
             "answer": "   ", "source": "teacher"},
        ]
        ws = self.make_ws(wiki="# Facts\n\nCourse fact.\n", quizzes=quizzes, language="en")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            abstention = "The materials do not contain an answer to this question."
            self.assertEqual(doc.count(abstention), 4)
            self.assertNotIn("This item has no displayable answer.", doc)
            self.assertNotIn("🟢 From your materials", doc)
            self.assertIn("Answer source: Source file unknown", doc)
            self.assertIn("Provenance: Source unknown", doc)
            self.assertIn(
                "Provenance: ⚠️ AI-generated answer — not from your teacher or textbook", doc
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_answer_side_material_remains_visible_when_only_text_transcription_is_missing(self):
        quizzes = [{
            "id": "visual-answer", "chapter": 1, "question": "Read the worked page.",
            "source": "material", "answer_source_file": "solutions.pdf",
            "answer_source_pages": [7],
            "assets": [{"path": "references/assets/answer.png",
                        "role": "answer_context"}],
        }]
        ws = self.make_ws(wiki="# Facts\n", quizzes=quizzes, language="en")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn(
                "No structured text answer is available; use the answer-side asset below.", doc)
            self.assertIn("Answer-side asset", doc)
            self.assertIn("data:image/png;base64,", doc)
            self.assertIn("Answer source: solutions.pdf", doc)
            self.assertIn("Provenance: 🟢 From your materials", doc)
            self.assertNotIn("The materials do not contain an answer to this question.", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_noncanonical_persisted_language_fails_loud(self):
        ws = self._language_fixture("Klingon")
        try:
            with self.assertRaises(sgr.GuideError) as ctx:
                sgr.load_chapter_sources(ws, 1)
            self.assertIn("study_state.json.language", str(ctx.exception))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_legacy_display_language_labels_remain_readable(self):
        for persisted, expected_lang in (("中文", "zh-CN"), ("English", "en"), ("双语", "mul")):
            ws = self._language_fixture(persisted)
            try:
                doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
                self.assertIn('<html lang="%s">' % expected_lang, doc)
            finally:
                shutil.rmtree(ws, ignore_errors=True)

    def test_official_progress_cli_language_code_renders(self):
        ws = self._language_fixture(None)
        try:
            update = os.path.join(SCRIPTS, "update_progress.py")
            init = subprocess.run(
                [PY, update, "--workspace", ws, "init"], capture_output=True,
                text=True, encoding="utf-8"
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            set_language = subprocess.run(
                [PY, update, "--workspace", ws, "set", "--language", "English"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(set_language.returncode, 0, set_language.stderr)
            with open(os.path.join(ws, "study_state.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["language"], "en")
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn('<html lang="en">', doc)
            self.assertIn("1. Core Concepts and Course Materials", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_renders_all_layers_math_provenance_and_embedded_images(self):
        teaching = [
            {"id": "ex1", "chapter": 1, "title": "并集例题", "question": "求 $A \\cup B$",
             "answer": "答案是 $$A \\cup B$$", "explanation": "把两个圈合起来。",
             "source": "material", "source_file": "ch01.pdf", "source_pages": [4],
             "answer_source_file": "ch01.pdf", "answer_source_pages": [5],
             "assets": [
                 {"path": "references/assets/prompt.png", "role": "question_context"},
                 {"path": "references/assets/answer.png", "role": "answer_context"},
             ]},
            {"id": "ex2", "chapter": 2, "question": "SECRET-TEACHING-CH2"},
        ]
        quizzes = [
            {"id": "q1", "chapter": 1, "type": "choice", "question": "选择 $x^2$",
             "options": ["$x$", "$x^2$"], "answer": "$x^2$", "explanation": "平方。",
             "source": "teacher", "source_file": "quiz.pdf", "source_pages": [2],
             "assets": [
                 {"path": "references/assets/prompt.png", "role": "figure"},
                 {"path": "references/assets/answer.png", "role": "worked_solution"},
             ]},
            {"id": "q2", "chapter": 2, "type": "subjective", "question": "SECRET-QUIZ-CH2"},
        ]
        ws = self.make_ws(teaching=teaching, quizzes=quizzes)
        try:
            sources = sgr.load_chapter_sources(ws, 1)
            document = sgr.render_study_guide(sources, fake_math)
            for text in ("核心概念与课件内容", "教学例题", "Quiz 与考试练习",
                         "详细讲解与复盘 Notebook", "并集例题", "本章笔记", "🟢 来自资料"):
                self.assertIn(text, document)
            self.assertIn("<math", document)
            self.assertNotIn("$A \\cup B$", document)
            self.assertIn("data:image/png;base64,", document)
            self.assertIn('<details class="quiz-answer">', document)
            self.assertIn("details > :not(summary) { display:block !important; }", document)
            self.assertIn(".card { box-shadow:none; overflow:visible; }", document)
            self.assertIn("main > section + section", document)
            self.assertIn("break-before:page;", document)
            self.assertIn("page-break-before:always;", document)
            self.assertIn("break-inside:avoid-page;", document)
            self.assertIn("page-break-inside:avoid;", document)
            self.assertLess(document.index("题面图"), document.index("答案图"))
            self.assertNotIn("SECRET-CH2", document)
            self.assertNotIn("SECRET-TEACHING-CH2", document)
            self.assertNotIn("SECRET-QUIZ-CH2", document)
            self.assertNotIn("SECRET-NOTEBOOK-CH2", document)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_english_boolean_answers_and_options_use_english_display_values(self):
        quizzes = [{"id": "tf", "chapter": 1, "type": "true_false",
                    "question": "Is the statement true?", "options": [True, False],
                    "answer": True, "source": "material"}]
        ws = self.make_ws(wiki="# Logic\n\nA proposition.", quizzes=quizzes,
                          language="en")
        with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("# Notes\n\nTruth values.\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("<p>True</p>", doc)
            self.assertIn("<li><p>False</p></li>", doc)
            self.assertNotIn("正确", doc)
            self.assertNotIn("错误", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_bare_worked_example_is_a_demonstration_not_a_missing_answer_card(self):
        teaching = [{"id": "ex", "chapter": 1, "teaching_role": "worked_example",
                     "title": "Complete demonstration",
                     "question": "First substitute $x=2$, then obtain $x^2=4$.",
                     "source": "material"}]
        ws = self.make_ws(wiki="# Algebra\n\nA worked demonstration.", teaching=teaching,
                          language="en")
        with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("# Notes\n\nReview the substitution.\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("Worked demonstration", doc)
            self.assertIn("First substitute", doc)
            self.assertNotIn("The materials do not provide a displayable standard answer.", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_empty_layers_are_explicit_and_honest(self):
        ws = self.make_ws(teaching=None, quizzes=[], notebook=False)
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("旧工作区未提供 teaching_examples.json", doc)
            self.assertIn("未虚构补题", doc)
            self.assertIn("notebook 尚无落盘讲解", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_html_is_escaped_and_links_are_not_live(self):
        ws = self.make_ws(wiki="# X\n\n<script>alert(1)</script> [remote](https://evil.example)\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertNotIn("<script>", doc)
            self.assertIn("&lt;script&gt;", doc)
            self.assertNotIn("href=", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_markdown_image_is_embedded(self):
        ws = self.make_ws(wiki="# X\n\n![集合图](references/assets/prompt.png)\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("data:image/png;base64,", doc)
            self.assertNotIn('src="references/assets', doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_visual_index_wiki_parent_asset_shape_is_narrowly_supported(self):
        ws = self.make_ws(wiki="# X\n\n![原页图](../assets/prompt.png)\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("data:image/png;base64,", doc)
            self.assertNotIn("../assets/", doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class MathContract(WorkspaceMixin, unittest.TestCase):
    def test_dollar_standard_delimiters_convert(self):
        text = "$x$\n$$y$$"
        prepared, tokens = sgr.prepare_math(text, fake_math)
        self.assertEqual(len(tokens), 2)
        for raw in ("$x$", "$$y$$"):
            self.assertNotIn(raw, prepared)
        self.assertTrue(all("<math" in value for value in tokens.values()))

    def test_backslash_delimiters_fail_and_require_dollar_migration(self):
        for text in ("\\(z\\)", "\\[w\\]"):
            with self.subTest(text=text):
                with self.assertRaises(sgr.GuideError) as ctx:
                    sgr.prepare_math(text, fake_math)
                self.assertIn("不属于本框架的事实源标准", str(ctx.exception))
                self.assertIn("$...$", str(ctx.exception))

    def test_legacy_parentheses_and_brackets_fail_loud(self):
        for text in ("legacy (A\\cup B)", "legacy [P=\\frac{1}{2}]"):
            with self.subTest(text=text):
                with self.assertRaises(sgr.GuideError) as ctx:
                    sgr.prepare_math(text, fake_math)
                self.assertIn("raw/伪 LaTeX", str(ctx.exception))
                self.assertIn("标准分隔符", str(ctx.exception))

    def test_validator_command_vocabulary_is_fail_loud(self):
        for text in ("x \\mid y", "\\operatorname{Var}(X)"):
            with self.subTest(text=text):
                with self.assertRaises(sgr.GuideError) as ctx:
                    sgr.prepare_math(text, fake_math)
                self.assertIn("raw/伪 LaTeX", str(ctx.exception))

    def test_code_fence_does_not_convert_or_flag_documented_tex(self):
        text = "```latex\n(A\\cup B)\n$raw$\n```\n\n正文 $x$"
        prepared, tokens = sgr.prepare_math(text, fake_math)
        self.assertIn("(A\\cup B)", prepared)
        self.assertEqual(len(tokens), 1)

    def test_inline_code_keeps_tex_literal_and_formula_link_label_renders_without_tokens(self):
        ws = self.make_ws(wiki="# X\n\ncode `\\frac{1}{2}` and [formula $x$](source.pdf)\n")
        try:
            doc = sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("<code>\\frac{1}{2}</code>", doc)
            self.assertIn("class=\"citation\"", doc)
            self.assertIn("<math", doc)
            for prefix in ("STUDYGUIDEPROTECTED", "STUDYGUIDEMATHTOKEN",
                           "STUDYGUIDEOPAQUETOKEN"):
                self.assertNotIn(prefix, doc)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_mathml_sanitizer_rejects_script(self):
        def hostile(_latex, display="inline"):
            return "<math><script>bad</script></math>"
        with self.assertRaises(sgr.GuideError):
            sgr.prepare_math("$x$", hostile)

    def test_mathml_sanitizer_rejects_visible_raw_tex(self):
        def still_raw(_latex, display="inline"):
            return "<math><mtext>A \\cup B</mtext></math>"
        with self.assertRaises(sgr.GuideError) as ctx:
            sgr.prepare_math("$A \\cup B$", still_raw)
        self.assertIn("raw LaTeX", str(ctx.exception))

    def test_unreviewed_installed_mathml_version_fails_before_import(self):
        with mock.patch.object(sgr, "installed_distribution_version", return_value="3.81.0"):
            with self.assertRaises(sgr.MissingMathDependency) as ctx:
                sgr.prepare_math("$x$")
        self.assertEqual(ctx.exception.code, 3)
        self.assertIn("latex2mathml==3.81.0", str(ctx.exception))
        self.assertIn("latex2mathml==3.60.0", str(ctx.exception))

    @unittest.skipUnless(
        importlib.util.find_spec("latex2mathml")
        and sgr.installed_distribution_version("latex2mathml") == "3.60.0",
        "pinned optional latex2mathml==3.60.0 is not installed")
    def test_real_installed_converter_produces_sanitized_mathml(self):
        prepared, tokens = sgr.prepare_math(r"$\frac{1}{2}$")
        self.assertEqual(len(tokens), 1)
        rendered = next(iter(tokens.values()))
        self.assertIn("<math", rendered)
        self.assertNotIn(r"\\frac", rendered)
        self.assertNotIn("<script", rendered.lower())


class PathSafety(WorkspaceMixin, unittest.TestCase):
    def test_corrupt_image_fails_instead_of_rendering_a_broken_figure(self):
        ws = self.make_ws(quizzes=[{"id": "q", "chapter": 1, "question": "x", "answer": "y",
                                    "assets": [{"path": "references/assets/prompt.png",
                                                "role": "question_context"}]}])
        with open(os.path.join(ws, "references", "assets", "prompt.png"), "wb") as f:
            f.write(b"not really a png")
        try:
            with self.assertRaises(sgr.GuideError) as ctx:
                sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("图片内容损坏", str(ctx.exception))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_wiki_parent_compat_rejects_every_broader_traversal_shape(self):
        for bad in ("../../assets/prompt.png", "../other/prompt.png",
                    "../assets/../prompt.png", "../assets/../../outside.png"):
            ws = self.make_ws(wiki="# X\n\n![bad](%s)\n" % bad)
            try:
                with self.assertRaises(sgr.GuideError):
                    sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            finally:
                shutil.rmtree(ws, ignore_errors=True)

    def test_notebook_cannot_use_wiki_parent_asset_exception(self):
        ws = self.make_ws()
        with open(os.path.join(ws, "notebook", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("![notebook image](../assets/prompt.png)\n")
        try:
            with self.assertRaises(sgr.GuideError):
                sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_visual_dependent_item_without_prompt_asset_fails_closed(self):
        ws = self.make_ws(quizzes=[{"id": "q", "chapter": 1, "question": "see figure",
                                    "answer": "x", "requires_assets": True,
                                    "assets": [{"path": "references/assets/answer.png",
                                                "role": "answer_context"}]}])
        try:
            with self.assertRaises(sgr.GuideError) as ctx:
                sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            self.assertIn("缺少可展示题面图", str(ctx.exception))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_rejects_url_absolute_and_parent_asset_paths(self):
        for bad in ("https://evil/x.png", "C:/outside.png", "../outside.png"):
            item = [{"id": "q", "chapter": 1, "question": "x", "answer": "y",
                     "assets": [{"path": bad, "role": "question_context"}]}]
            ws = self.make_ws(quizzes=item)
            try:
                with self.assertRaises(sgr.GuideError):
                    sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
            finally:
                shutil.rmtree(ws, ignore_errors=True)

    def test_rejects_unsafe_provenance_path(self):
        ws = self.make_ws(quizzes=[{"id": "q", "chapter": 1, "question": "x", "answer": "y",
                                    "source_file": "../outside.pdf"}])
        try:
            with self.assertRaises(sgr.GuideError):
                sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_rejects_symlinked_asset(self):
        ws = self.make_ws(quizzes=[{"id": "q", "chapter": 1, "question": "x", "answer": "y",
                                    "assets": [{"path": "references/assets/link.png",
                                                "role": "question_context"}]}])
        outside = tempfile.mktemp(suffix=".png")
        with open(outside, "wb") as f:
            f.write(b"PNG")
        try:
            try:
                os.symlink(outside, os.path.join(ws, "references", "assets", "link.png"))
            except (OSError, NotImplementedError):
                self.skipTest("symlink privilege unavailable")
            with self.assertRaises(sgr.GuideError):
                sgr.render_study_guide(sgr.load_chapter_sources(ws, 1), fake_math)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
            if os.path.exists(outside):
                os.remove(outside)


class CliContract(WorkspaceMixin, unittest.TestCase):
    def test_html_href_allowlist_accepts_only_existing_fragments_and_material_files(self):
        materials = tempfile.mkdtemp(prefix="study-guide-materials-")
        outside_dir = tempfile.mkdtemp(prefix="study-guide-outside-")
        try:
            source_dir = os.path.join(materials, "slides & notes")
            os.makedirs(source_dir)
            source = os.path.join(source_dir, "课件 1.pdf")
            with open(source, "wb") as stream:
                stream.write(b"%PDF-1.4\nsource")
            href = sgr.Path(source).resolve().as_uri() + "#page=4"
            good = (
                '<!doctype html><html><body><section id="kp-speed"></section>'
                '<article id="example-q1"></article><a href="#kp-speed">kp</a>'
                '<a href="#example-q1">example</a><a href="%s">source</a>'
                '</body></html>' % href
            )
            sgr.validate_generated_html(good, materials_root=materials)

            outside = os.path.join(outside_dir, "outside.pdf")
            with open(outside, "wb") as stream:
                stream.write(b"%PDF-1.4\noutside")
            bad_hrefs = (
                "https://example.com/source.pdf#page=1",
                "javascript:alert(1)",
                "data:text/html,boom",
                "slides/source.pdf#page=1",
                "../source.pdf#page=1",
                sgr.Path(outside).resolve().as_uri() + "#page=1",
                sgr.Path(os.path.join(materials, "missing.pdf")).resolve().as_uri() + "#page=1",
            )
            for bad in bad_hrefs:
                with self.subTest(href=bad):
                    document = '<!doctype html><html><body><a href="%s">bad</a></body></html>' % bad
                    with self.assertRaises(sgr.GuideError):
                        sgr.validate_generated_html(document, materials_root=materials)
            with self.assertRaisesRegex(sgr.GuideError, "不存在的同页锚点"):
                sgr.validate_generated_html(
                    '<!doctype html><html><body><a href="#kp-missing">bad</a></body></html>',
                    materials_root=materials,
                )
        finally:
            shutil.rmtree(materials, ignore_errors=True)
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_print_copy_opens_quiz_details_without_opening_persisted_html(self):
        source = '<!doctype html><html><body><details class="quiz-answer"><summary>x</summary>' \
                 '<div>answer</div></details></body></html>'
        ready = sgr._print_ready_html(source)
        self.assertIn('<details open class="quiz-answer">', ready)
        self.assertNotIn('<details open class="quiz-answer">', source)

    def test_print_timeout_cleans_temp_and_target_outputs_and_fails_clearly(self):
        directory = tempfile.mkdtemp(prefix="study-guide-timeout-")
        html_path = os.path.join(directory, "ch01.html")
        pdf_path = os.path.join(directory, "ch01.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<!doctype html><html><body><p>ready</p></body></html>")
        with open(pdf_path, "wb") as f:
            f.write(b"STALE")
        try:
            def time_out_after_partial(command, **_kwargs):
                target_arg = next(arg for arg in command if arg.startswith("--print-to-pdf="))
                with open(target_arg.split("=", 1)[1], "wb") as partial:
                    partial.write(b"%PDF-PARTIAL")
                raise subprocess.TimeoutExpired(cmd=command, timeout=0.01)

            with mock.patch.object(sgr.subprocess, "run", side_effect=time_out_after_partial):
                with self.assertRaises(sgr.GuideError) as ctx:
                    sgr.print_pdf("fake-browser", html_path, pdf_path, timeout=0.01)
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("打印 PDF 超时", str(ctx.exception))
            self.assertIn("已清理临时文件与目标 PDF", str(ctx.exception))
            self.assertFalse(os.path.exists(pdf_path))
            self.assertFalse(any(name.startswith(".study-guide-")
                                 for name in os.listdir(directory)))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def run_cli(self, ws, *extra, env=None):
        merged = dict(os.environ)
        if env:
            merged.update(env)
        return subprocess.run(
            [PY, os.path.join(SCRIPTS, "study_guide_render.py"), "--workspace", ws,
             "--chapter", "1", "--artifact-type", "source_packet"] + list(extra),
            capture_output=True, text=True, encoding="utf-8", env=merged,
        )

    def test_missing_math_dependency_exit_3_and_removes_stale_outputs(self):
        ws = self.make_ws()
        os.makedirs(os.path.join(ws, "study_guide"))
        stale = os.path.join(ws, "study_guide", "ch01.source-packet.html")
        with open(stale, "w", encoding="utf-8") as f:
            f.write("STALE")
        try:
            result = self.run_cli(ws, env={"EXAMPREP_NO_MATHML": "1"})
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("pip install latex2mathml==3.60.0", result.stderr)
            self.assertFalse(os.path.exists(stale))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_legacy_formula_removes_stale_html(self):
        ws = self.make_ws(wiki="# X\n\n(A\\cup B)\n", quizzes=[])
        os.makedirs(os.path.join(ws, "study_guide"))
        stale = os.path.join(ws, "study_guide", "ch01.source-packet.html")
        with open(stale, "w", encoding="utf-8") as f:
            f.write("STALE")
        try:
            result = self.run_cli(ws, env={"EXAMPREP_NO_MATHML": "1"})
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("raw/伪 LaTeX", result.stderr)
            self.assertFalse(os.path.exists(stale))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_no_browser_exit_3_preserves_valid_html_and_removes_stale_pdf(self):
        ws = self.make_ws(wiki="# X\n\nNo formula.\n", quizzes=[])
        os.makedirs(os.path.join(ws, "study_guide"))
        stale_pdf = os.path.join(ws, "study_guide", "ch01.source-packet.pdf")
        with open(stale_pdf, "wb") as f:
            f.write(b"OLD PDF")
        try:
            result = self.run_cli(ws, "--pdf-backend", "browser", "--pdf",
                                  env={"EXAMPREP_NO_BROWSER": "1"})
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("no_browser", result.stderr)
            self.assertTrue(os.path.isfile(os.path.join(
                ws, "study_guide", "ch01.source-packet.html")))
            self.assertFalse(os.path.exists(stale_pdf))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_direct_run_with_injected_converter_writes_atomically(self):
        ws = self.make_ws()
        try:
            self.assertEqual(sgr.run(["--workspace", ws, "--chapter", "1",
                                      "--artifact-type", "source_packet"], fake_math), 0)
            path = os.path.join(ws, "study_guide", "ch01.source-packet.html")
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(os.path.dirname(path))))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_ambiguous_wiki_fails_instead_of_guessing(self):
        ws = self.make_ws(wiki="# X\nNo formula")
        with open(os.path.join(ws, "references", "wiki", "ch1_more.md"), "w", encoding="utf-8") as f:
            f.write("# duplicate")
        try:
            result = self.run_cli(ws)
            self.assertEqual(result.returncode, 2)
            self.assertIn("恰好对应一个", result.stderr)
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TypedManifestPublicationTOCTOU(WorkspaceMixin, unittest.TestCase):
    DOCUMENT = "<!doctype html><html><body><p>typed snapshot</p></body></html>"

    @staticmethod
    def _manifest_path(ws):
        return os.path.join(ws, "notebook", "ch01.guide.json")

    def _write_manifest(self, ws, payload=b'{"generation":1}'):
        path = self._manifest_path(ws)
        with open(path, "wb") as stream:
            stream.write(payload)
        return path, payload

    @staticmethod
    def _gate(ws, digest="a" * 64):
        return {
            "ready_to_use": True,
            "workspace": ws,
            "materials": ws,
            "registered_course": "TOCTOU",
            "runtime_provenance": {"receipt": {
                "runtime_digest": digest,
                "runtime_file_count": 1,
                "skill_version": "test",
                "git_commit": "b" * 40,
                "git_branch": "test",
                "git_dirty": False,
                "python_executable": sys.executable,
            }},
        }

    @staticmethod
    def _validation_report():
        return {
            "ok": True,
            "schema_version": 1,
            "chapter": 1,
            "language": "en",
            "profile": "full",
            "expected_item_ids": ["item-1"],
            "walkthrough_item_ids": ["item-1"],
            "omitted_item_ids": [],
        }

    def _run_typed(self, ws, renderer, gate_side_effect=None):
        import study_guide_content as content
        import study_guide_document as document

        gate_patch = (
            mock.patch.object(sgr, "_start_gate_or_raise", side_effect=gate_side_effect)
            if gate_side_effect is not None else
            mock.patch.object(sgr, "_start_gate_or_raise", return_value=self._gate(ws))
        )
        with gate_patch, \
                mock.patch.object(content, "validate_manifest",
                                  return_value=self._validation_report()), \
                mock.patch.object(document, "render_manifest", side_effect=renderer):
            return sgr.run([
                "--workspace", ws, "--chapter", "1", "--profile", "full",
                "--pdf-backend", "html",
            ], fake_math)

    @staticmethod
    def _assert_no_publication(ws):
        output = os.path.join(ws, "study_guide")
        for name in ("ch01.html", "ch01.pdf", "ch01.receipt.json"):
            if os.path.exists(os.path.join(output, name)):
                raise AssertionError("unexpected mixed-generation publication: %s" % name)

    def test_mocked_manifest_hash_drift_during_render_fails_closed(self):
        ws = self.make_ws(language="en")
        manifest_path, _payload = self._write_manifest(ws)

        def render_and_mutate(*_args, **_kwargs):
            with open(manifest_path, "wb") as stream:
                stream.write(b'{"generation":2}')
            return self.DOCUMENT, {}

        try:
            with self.assertRaisesRegex(sgr.ArtifactDriftError, "SHA-256 changed"):
                self._run_typed(ws, render_and_mutate)
            self._assert_no_publication(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_fact_integrity_snapshot_is_rechecked_before_publication(self):
        ws = self.make_ws(language="en")
        manifest_path, _payload = self._write_manifest(ws)
        manifest_snapshot = sgr._capture_regular_file_snapshot(
            ws, manifest_path, "study-guide content manifest"
        )
        os.makedirs(os.path.join(ws, ".ingest"), exist_ok=True)
        fact_manifest_path = os.path.join(ws, ".ingest", "build_manifest.json")
        fact_manifest_bytes = b'{"schema_version":1}'
        with open(fact_manifest_path, "wb") as stream:
            stream.write(fact_manifest_bytes)
        expected = {
            "schema_version": 1,
            "build_manifest": {
                "path": ".ingest/build_manifest.json",
                "sha256": hashlib.sha256(fact_manifest_bytes).hexdigest(),
            },
            "inputs": {},
            "sidecars": {},
        }
        manifest_snapshot["fact_integrity"] = expected
        start_gate_snapshot = sgr._start_gate_snapshot(self._gate(ws))
        try:
            with mock.patch(
                    "ingestion.dedup.validate_workspace_fact_integrity",
                    return_value={"snapshot": expected},
            ), mock.patch.object(sgr, "_verify_start_gate_snapshot"):
                sgr._verify_publication_inputs(
                    ws, manifest_snapshot, start_gate_snapshot
                )
            with mock.patch(
                    "ingestion.dedup.validate_workspace_fact_integrity",
                    return_value={
                        "snapshot": {"schema_version": 1, "token": "drifted"},
                    },
            ), mock.patch.object(sgr, "_verify_start_gate_snapshot"), \
                    self.assertRaisesRegex(
                        sgr.ArtifactDriftError, "fact inputs changed"
                    ):
                sgr._verify_publication_inputs(
                    ws, manifest_snapshot, start_gate_snapshot
                )
            with open(fact_manifest_path, "wb") as stream:
                stream.write(b'{"schema_version":2}')
            with mock.patch.object(sgr, "_verify_start_gate_snapshot"), \
                    self.assertRaisesRegex(
                        sgr.ArtifactDriftError, "build manifest changed"
                    ):
                sgr._verify_publication_inputs(
                    ws, manifest_snapshot, start_gate_snapshot,
                    deep_fact_check=False,
                )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_concurrent_same_bytes_path_replacement_is_rejected_by_identity(self):
        ws = self.make_ws(language="en")
        manifest_path, payload = self._write_manifest(ws)
        renderer_entered = threading.Event()
        replacement_done = threading.Event()

        def replace_while_rendering():
            if not renderer_entered.wait(5):
                return
            replacement = manifest_path + ".replacement"
            with open(replacement, "wb") as stream:
                stream.write(payload)
            os.replace(replacement, manifest_path)
            replacement_done.set()

        def blocked_renderer(*_args, **_kwargs):
            renderer_entered.set()
            if not replacement_done.wait(5):
                raise AssertionError("concurrent replacement did not finish")
            return self.DOCUMENT, {}

        worker = threading.Thread(target=replace_while_rendering, daemon=True)
        worker.start()
        try:
            with self.assertRaisesRegex(sgr.ArtifactDriftError, "path was replaced"):
                self._run_typed(ws, blocked_renderer)
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self._assert_no_publication(ws)
        finally:
            renderer_entered.set()
            worker.join(5)
            shutil.rmtree(ws, ignore_errors=True)

    def test_manifest_drift_while_receipt_is_staged_prevents_atomic_publish(self):
        ws = self.make_ws(language="en")
        manifest_path, _payload = self._write_manifest(ws)
        original_atomic_json = sgr._atomic_json

        def mutate_before_receipt_stage(path, value, before_publish=None):
            with open(manifest_path, "wb") as stream:
                stream.write(b'{"generation":3}')
            return original_atomic_json(
                path, value, before_publish=before_publish)

        try:
            with mock.patch.object(sgr, "_atomic_json",
                                   side_effect=mutate_before_receipt_stage):
                with self.assertRaisesRegex(sgr.ArtifactDriftError, "SHA-256 changed"):
                    self._run_typed(
                        ws, lambda *_args, **_kwargs: (self.DOCUMENT, {}))
            self._assert_no_publication(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_runtime_identity_drift_during_render_fails_like_manifest_drift(self):
        ws = self.make_ws(language="en")
        self._write_manifest(ws)
        current = {"digest": "a" * 64}

        def gate_provider(_ws):
            return self._gate(ws, current["digest"])

        def render_and_drift_runtime(*_args, **_kwargs):
            current["digest"] = "c" * 64
            return self.DOCUMENT, {}

        try:
            with self.assertRaisesRegex(
                    sgr.ArtifactDriftError, "runtime identity changed"):
                self._run_typed(ws, render_and_drift_runtime, gate_provider)
            self._assert_no_publication(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_receipt_hashes_bind_exact_atomically_published_bytes(self):
        ws = self.make_ws(language="en")
        manifest_path, manifest_payload = self._write_manifest(ws)
        try:
            result = self._run_typed(
                ws, lambda *_args, **_kwargs: (self.DOCUMENT, {}))
            self.assertEqual(result, 0)
            html_path = os.path.join(ws, "study_guide", "ch01.html")
            receipt_path = os.path.join(ws, "study_guide", "ch01.receipt.json")
            with open(html_path, "rb") as stream:
                html_payload = stream.read()
            with open(receipt_path, "r", encoding="utf-8") as stream:
                receipt = json.load(stream)
            self.assertEqual(html_payload, self.DOCUMENT.encode("utf-8"))
            self.assertEqual(receipt["html_sha256"], hashlib.sha256(
                html_payload).hexdigest())
            self.assertEqual(receipt["content_manifest_sha256"], hashlib.sha256(
                manifest_payload).hexdigest())
            self.assertEqual(receipt["content_manifest"], "notebook/ch01.guide.json")
            self.assertTrue(os.path.samefile(manifest_path, self._manifest_path(ws)))
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
