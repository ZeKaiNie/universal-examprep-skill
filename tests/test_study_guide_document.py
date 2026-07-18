# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import study_guide_document as sgd  # noqa: E402
import study_guide_render as sgr  # noqa: E402
import study_guide_qa as sgqa  # noqa: E402
from study_guide_render import GuideError  # noqa: E402


ALT_VALID_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


def fake_math(latex, display="inline"):
    del latex, display
    return '<math><mrow><mi>converted</mi></mrow></math>'


def both(zh, en):
    return {"zh": zh, "en": en}


class TypedStudyGuideDocumentTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="typed-guide-document-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.materials = tempfile.mkdtemp(prefix="typed-guide-materials-")
        self.addCleanup(shutil.rmtree, self.materials, ignore_errors=True)
        os.makedirs(os.path.join(self.materials, "lecture"))
        with open(os.path.join(self.materials, "lecture", "ch01.pdf"), "wb") as stream:
            stream.write(b"%PDF-1.4\nfixture")
        os.makedirs(os.path.join(self.ws, "references", "assets"))
        pixel = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with open(os.path.join(self.ws, "references", "assets", "prompt.png"), "wb") as stream:
            stream.write(pixel)
        self._json("study_state.json", {
            "language": "bilingual",
            "artifact_mode": "visual",
            "processing_mode": "full",
            "answer_explanation_mode": "ordinary",
        })
        self._json("references/teaching_examples.json", [
            {
                "id": "ex-lecture-1", "chapter": 1, "source_type": "lecture",
                "assets": [{
                    "path": "references/assets/prompt.png",
                    "role": "question_context", "type": "page_image",
                    "contains_full_prompt": True,
                }],
            },
        ])
        self._json("references/quiz_bank.json", [])
        os.makedirs(os.path.join(self.ws, "notebook"))
        with open(os.path.join(self.ws, "notebook", "ch01.md"), "w",
                  encoding="utf-8") as stream:
            stream.write(
                "# Notes\n\n## [#ex-lecture-1] ex-lecture-1\n\n"
                "> Walkthrough · 2026-07-14 10:00\n\n"
                "Pre-existing official seven-step walkthrough.\n"
            )
        self.start_gate = {
            "ready_to_use": True,
            "ready_to_ingest": True,
            "processing_mode": "full",
            "answer_explanation_mode": "ordinary",
            "workspace": self.ws,
            "materials": self.materials,
            "registered_course": "fixture",
            "runtime_provenance": {"receipt": {
                "runtime_digest": "a" * 64,
                "runtime_file_count": 42,
                "skill_version": "5.0.0-test",
                "git_commit": "b" * 40,
                "git_branch": "codex/test",
                "git_dirty": False,
                "python_executable": sys.executable,
            }},
        }
        self.real_start_gate = sgr._start_gate_or_raise
        self.real_require_full_processing = sgd.exam_start.require_full_processing
        gate_patch = mock.patch.object(
            sgr, "_start_gate_or_raise", return_value=self.start_gate)
        gate_patch.start()
        self.addCleanup(gate_patch.stop)
        document_gate_patch = mock.patch.object(
            sgd.exam_start, "require_full_processing",
            return_value=self.start_gate,
        )
        document_gate_patch.start()
        self.addCleanup(document_gate_patch.stop)
        real_document_validator = sgd.validate_manifest

        def validate_synthetic_document_fixture(*args, **kwargs):
            report = real_document_validator(*args, **kwargs)
            # This class predates the redistributable ingestion-v2 Gold Set and
            # tests document layout rather than ingestion provenance.  Preserve
            # all real content validation, while explicitly labelling only this
            # synthetic fixture for the lower-level document renderer.
            if report.get("ingestion_pipeline_version") is None:
                report = dict(report)
                report["ingestion_pipeline_version"] = "ingestion-v2"
            return report

        document_manifest_patch = mock.patch.object(
            sgd, "validate_manifest",
            side_effect=validate_synthetic_document_fixture,
        )
        document_manifest_patch.start()
        self.addCleanup(document_manifest_patch.stop)
        qa_gate_patch = mock.patch.object(
            sgqa.exam_start, "check_registered_workspace_gate",
            return_value=self.start_gate,
        )
        qa_gate_patch.start()
        self.addCleanup(qa_gate_patch.stop)

    def _json(self, relative, value):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)

    def _declare_foreign_student_attempt(self):
        relative = "references/assets/foreign-attempt.png"
        shutil.copyfile(
            os.path.join(self.ws, "references", "assets", "prompt.png"),
            os.path.join(self.ws, *relative.split("/")),
        )
        self._json("references/quiz_bank.json", [{
            "id": "foreign-attempt", "chapter": 2, "type": "subjective",
            "question": "A student's unrelated submission.",
            "answer": "Not course evidence.", "source": "material",
            "source_type": "other",
            "assets": [{
                "path": relative, "role": "student_attempt", "type": "page_image",
            }],
        }])
        return relative

    def _source(self, role):
        return {"source_file": "lecture/ch01.pdf", "pages": [4],
                "source_unit_id": "unit-p4", "role": role}

    def _render(self, manifest=None):
        return sgd.render_manifest(
            self.ws, manifest or self.manifest(), fake_math,
            materials_root=self.materials)

    def _run_synthetic_publication_fixture(self, argv):
        """Exercise publication layout without mislabelling this v1-less fixture.

        Crop/provenance publication gates have dedicated ingestion-v2 tests.  This
        older synthetic fixture exists only to test HTML/PDF/receipt mechanics, so
        provide the already-validated snapshot explicitly instead of teaching the
        production loader to accept a self-declared protocol version.
        """

        path = os.path.join(self.ws, "notebook", "ch01.guide.json")
        with open(path, "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        report = sgd.validate_manifest(self.ws, 1, manifest)
        report["ingestion_pipeline_version"] = "ingestion-v2"
        report["input_path"] = path
        snapshot = sgr._capture_regular_file_snapshot(
            self.ws, path, "synthetic typed manifest")
        snapshot["fact_integrity"] = None
        with mock.patch.object(
                sgr, "_load_typed_manifest_snapshot",
                return_value=(manifest, report, snapshot)):
            return sgr.run(argv, fake_math)

    def manifest(self):
        formula = {
            "id": "f-speed", "latex": r"v=\frac{d}{t}",
            "explanation": both("速度等于路程除以时间。", "Speed is distance divided by time."),
            "variables": [
                {"symbol": "v", "meaning": both("速度", "speed")},
                {"symbol": "d", "meaning": both("路程", "distance")},
                {"symbol": "t", "meaning": both("时间", "time")},
            ],
            "applicability": both("路程和时间使用一致单位。", "Distance and time use compatible units."),
            "explanation_provenance": both(
                "ai_supplement", "ai_supplement"),
            "applicability_provenance": both(
                "ai_supplement", "ai_supplement"),
            "source_refs": [self._source("formula")],
        }
        for variable in formula["variables"]:
            variable["meaning_provenance"] = both(
                "ai_supplement", "ai_supplement")
        walk = {
            "item_id": "ex-lecture-1", "source_type": "lecture",
            "answer_provenance": {"zh": "ai_supplemented", "en": "material"},
            "knowledge_point_ids": ["kp-speed"],
            "knowledge_point_uses": {
                "kp-speed": both("把题目中的路程和时间代入平均速度关系。",
                                 "Map the prompt's distance and time into average speed."),
            },
            "knowledge_point_uses_provenance": {
                "kp-speed": both("ai_supplement", "ai_supplement"),
            },
            "notebook_anchor": "ex-lecture-1-ex-lecture-1",
            "title": both("速度公式例题", "Speed-formula example"),
            "original_language": "en", "prompt_asset_mode": "full_prompt",
            "prompt_asset_paths": ["references/assets/prompt.png"], "answer_asset_paths": [],
            "translation": {"zh": "一辆车 2 小时行驶 100 千米，求速度。"},
            "translation_provenance": {"zh": "ai_translation"},
            "what_asked": both("求平均速度。", "Find the average speed."),
            "what_asked_provenance": both(
                "ai_supplement", "ai_supplement"),
            "known_quantities": [
                {"label": both("路程", "Distance"), "symbol": "d", "value": "100", "unit": "km"},
                {"label": both("时间", "Time"), "symbol": "t", "value": "2", "unit": "h"},
            ],
            "unknown_quantities": [{"label": both("速度", "Speed"), "symbol": "v"}],
            "solution_kind": "formula",
            "formula_uses": [{
                "formula_id": "f-speed",
                "why_applicable": both("题目给出了路程和时间。", "The prompt gives distance and time."),
                "variable_mapping": [
                    {"symbol": "v", "maps_to": both("待求的平均速度", "the unknown average speed")},
                    {"symbol": "d", "maps_to": both("100 千米", "100 kilometres")},
                    {"symbol": "t", "maps_to": both("2 小时", "2 hours")},
                ],
                "substitution": r"v=100/2=50\ \mathrm{km/h}",
                "why_applicable_provenance": both(
                    "ai_supplement", "ai_supplement"),
                "substitution_provenance": "ai_supplement",
            }],
            "steps": [
                both("先写出速度公式。", "Write the speed formula."),
                both("代入 100 和 2，得到 50。", "Substitute 100 and 2 to get 50."),
            ],
            "steps_provenance": [
                both("ai_supplement", "ai_supplement"),
                both("ai_supplement", "ai_supplement"),
            ],
            "answer": both("平均速度是 50 千米/小时。", "The average speed is 50 km/h."),
            "answer_explanation": both(
                "速度表示平均每一小时走过多少路程。题目给出总路程一百千米和总时间两小时，所以先把一百除以二，得到五十。单位也要一起相除：千米除以小时就是千米每小时。因此五十不是总路程，而是车辆在这两小时内平均每小时对应的路程；把五十千米每小时乘回两小时，会重新得到一百千米，这说明计算含义与题目数据一致。",
                "Speed describes how much distance corresponds to one hour on average. The prompt supplies a total distance of 100 kilometres and a total time of 2 hours, so the applicable relationship is distance divided by time. Substituting the given values produces 100 divided by 2, which equals 50. The units divide in the same way: kilometres divided by hours becomes kilometres per hour. Therefore 50 is not the total distance; it means that each hour accounts for 50 kilometres on average over this trip. Multiplying 50 kilometres per hour by the stated 2 hours reconstructs the original 100 kilometres, so the numerical result, its unit, and its meaning all agree with the prompt.",
            ),
            "answer_explanation_provenance": {
                "zh": "ai_supplement", "en": "ai_supplement",
            },
            "source_trace": [self._source("question"), self._source("answer")],
        }
        for quantity in walk["known_quantities"] + walk["unknown_quantities"]:
            quantity["provenance"] = both("ai_supplement", "ai_supplement")
        for mapping in walk["formula_uses"][0]["variable_mapping"]:
            mapping["maps_to_provenance"] = both(
                "ai_supplement", "ai_supplement")
        return {
            "schema_version": 1,
            "authoring_protocol_version": 2,
            "answer_explanation_mode": "ordinary",
            "chapter": 1, "language": "bilingual", "profile": "full",
            "knowledge_points": [{
                "id": "kp-speed", "title": both("平均速度", "Average speed"),
                "explanation": both("把总路程平均分配到每个单位时间。",
                                     "Distribute total distance over each unit of time."),
                "explanation_provenance": {
                    "zh": "ai_translation", "en": "material",
                },
                "formulas": [formula], "source_refs": [self._source("concept")],
                "example_ids": ["ex-lecture-1"],
            }],
            "walkthroughs": [walk], "omissions": [],
        }

    def test_full_prompt_image_is_not_followed_by_duplicate_original_text(self):
        document, report = self._render()
        self.assertEqual(["ex-lecture-1"], report["walkthrough_item_ids"])
        self.assertEqual(1, document.count('data-item-id="ex-lecture-1"'))
        self.assertIn("题面翻译", document)
        self.assertIn("一辆车 2 小时", document)
        self.assertNotIn("<h4>原题 / Original prompt</h4>", document)
        self.assertNotIn("\\frac", document)
        self.assertNotIn("<details", document)
        self.assertIn("课件 / 讲义 / Lecture / handout", document)
        self.assertIn("④ 选公式：为什么能用 / ④ Choose the formula", document)
        self.assertIn("代入数字 / 条件 / Substitute values / conditions", document)
        self.assertIn("⑦ 来源追踪 / ⑦ Source trace", document)
        self.assertIn("🟡 AI补充，可能与你老师讲的不完全一致", document)
        self.assertIn("🌐 AI翻译，原资料为另一种语言", document)
        self.assertIn("🟢 来自资料", document)
        self.assertIn("🟢 From your materials", document)
        self.assertEqual(1, document.count("AI补充，可能与你老师讲的不完全一致"))
        self.assertEqual(1, document.count("AI translation — source material is in another language"))
        self.assertIn('id="provenance-legend"', document)
        self.assertIn("⑥ 为什么这个答案成立", document)
        self.assertNotIn("答案自检", document)
        self.assertNotIn("50×2=100", document)
        article = document.index('<article class="example-card"')
        header = document.index('<header class="example-header">', article)
        prompt = document.index('<section class="prompt-zone">', article)
        uses = document.index('<section class="kp-uses">', article)
        walkthrough = document.index('<section class="walkthrough-zone">', article)
        self.assertLess(header, prompt)
        self.assertLess(prompt, uses)
        self.assertLess(uses, walkthrough)
        self.assertIn('<div class="prompt-intro"><h4>', document)
        self.assertIn('</h4><figure class="source-asset">', document)
        self.assertIn('<div class="final-answer"><h4>', document)
        self.assertIn('<div class="answer-pair"><section class="answer-language lang-zh"',
                      document)
        self.assertIn('<div class="closing-pair"><div class="source-box">', document)
        self.assertNotIn('class="self-check"', document)
        expected_uri = Path(os.path.join(
            self.materials, "lecture", "ch01.pdf")).resolve().as_uri() + "#page=4"
        self.assertIn('href="%s"' % expected_uri, document)

    def test_student_view_uses_teaching_fields_and_hides_machine_ids_and_ocr_quotes(self):
        manifest = self.manifest()
        kp = manifest["knowledge_points"][0]
        kp["explanation"]["en"] = "RAW CONCEPT OCR P[A] =\nm\nX\ni=1"
        kp["teaching_explanation"] = both(
            "把互斥分支的概率加权相加。",
            "Add the mutually exclusive branches with their probability weights.")
        kp["teaching_explanation_provenance"] = {
            "zh": "ai_supplement", "en": "ai_supplement"}
        walk = manifest["walkthroughs"][0]
        walk["answer"]["en"] = "RAW ANSWER OCR = 4\n9 ."
        walk["teaching_answer"] = both(
            "答案是 $4/9$。", "The answer is $4/9$.")
        walk["teaching_answer_provenance"] = {
            "zh": "ai_supplemented", "en": "ai_supplemented"}
        walk["source_trace"][0]["quote_span"] = "DUPLICATED FULL QUESTION OCR"
        walk["source_trace"].append(dict(walk["source_trace"][0]))

        document, _report = self._render(manifest)
        self.assertNotIn("RAW CONCEPT OCR", document)
        self.assertNotIn("RAW ANSWER OCR", document)
        self.assertNotIn("DUPLICATED FULL QUESTION OCR", document)
        self.assertNotIn(">unit unit-p4<", document)
        self.assertNotIn("<code>f-speed</code>", document)
        self.assertNotIn("<li><code>kp-speed</code>", document)
        self.assertNotIn(">ex-lecture-1 ·", document)
        self.assertNotIn('<span class="en-prefix">EN · </span><p>', document)
        self.assertIn('<p><span class="en-prefix">EN · </span>', document)
        self.assertIn("<math", document)
        renderer = sgd.MarkdownRenderer(self.ws, fake_math, language="双语")
        duplicate = dict(walk["source_trace"][0])
        trace = sgd._source_trace(
            renderer, [duplicate, dict(duplicate)], "bilingual", self.materials)
        self.assertEqual(1, trace.count("<li "))

    def test_formula_fragments_not_used_by_examples_stay_in_evidence_not_student_cards(self):
        manifest = self.manifest()
        walk = manifest["walkthroughs"][0]
        walk["formula_uses"] = []
        walk["solution_kind"] = "concept"
        walk["no_formula_reason"] = both(
            "这道题按定义作答，不需要套公式。",
            "This item is answered from the definition without a formula.")
        walk["no_formula_reason_provenance"] = both(
            "ai_supplement", "ai_supplement")

        document, _report = self._render(manifest)

        self.assertEqual(1, document.count('data-formula-id="f-speed"'))
        example = document[document.index('<article class="example-card"'):]
        self.assertNotIn('data-formula-id="f-speed"', example)
        self.assertNotIn('<section class="formula-use">', example)
        self.assertIn("⑦ 来源追踪", document)

    def test_typed_tex_symbols_render_as_inline_mathml_in_every_symbol_slot(self):
        manifest = self.manifest()
        formula = manifest["knowledge_points"][0]["formulas"][0]
        formula["variables"][0]["symbol"] = r"\alpha"
        walk = manifest["walkthroughs"][0]
        walk["formula_uses"][0]["variable_mapping"][0]["symbol"] = r"\alpha"
        walk["known_quantities"][0]["symbol"] = r"\beta"

        calls = []

        def recording_math(latex, display="inline"):
            calls.append((latex, display))
            return '<math><mrow><mi>converted</mi></mrow></math>'

        document = sgd.render_manifest(
            self.ws, manifest, recording_math, materials_root=self.materials
        )[0]

        self.assertEqual(2, calls.count((r"\alpha", "inline")))
        self.assertEqual(1, calls.count((r"\beta", "inline")))
        self.assertNotIn(r"<code>\alpha</code>", document)
        self.assertNotIn(r"<code>\beta</code>", document)
        self.assertGreaterEqual(document.count('class="math-inline" role="math"'), 3)

    def test_source_inventory_is_localized_in_hero_for_every_language(self):
        cases = {
            "zh": (
                ("课件 / 讲义：1", "模拟考试：0 — 当前工作区/资料集中未提供。",
                 "往年考试：0 — 当前工作区/资料集中未提供。"),
                ("Lecture / handout: 1", "Not provided in the current workspace/material set."),
            ),
            "en": (
                ("Lecture / handout: 1",
                 "Mock exam: 0 — Not provided in the current workspace/material set.",
                 "Past exam: 0 — Not provided in the current workspace/material set."),
                ("课件 / 讲义：1", "当前工作区/资料集中未提供。"),
            ),
            "bilingual": (
                ("课件 / 讲义：1", "Lecture / handout: 1",
                 "模拟考试：0 — 当前工作区/资料集中未提供。",
                 "Mock exam: 0 — Not provided in the current workspace/material set.",
                 "往年考试：0 — 当前工作区/资料集中未提供。",
                 "Past exam: 0 — Not provided in the current workspace/material set."),
                (),
            ),
        }
        for language, (expected, excluded) in cases.items():
            with self.subTest(language=language):
                self._json("study_state.json", {
                    "language": language,
                    "artifact_mode": "visual",
                    "processing_mode": "full",
                    "answer_explanation_mode": "ordinary",
                })
                manifest = self.manifest()
                manifest["language"] = language
                if language != "bilingual":
                    walk = manifest["walkthroughs"][0]
                    walk["answer_explanation"] = {
                        language: walk["answer_explanation"][language]}
                    walk["answer_explanation_provenance"] = {
                        language: walk["answer_explanation_provenance"][language]}
                document, report = self._render(manifest)
                match = re.search(
                    r'<p class="source-inventory">.*?</p>', document, re.S)
                self.assertIsNotNone(match)
                inventory = match.group(0)
                for text in expected:
                    self.assertIn(text, inventory)
                for text in excluded:
                    self.assertNotIn(text, inventory)
                self.assertLess(document.index(inventory), document.index('<ol class="route">'))
                self.assertEqual(["ex-lecture-1"], report["expected_item_ids"])
                self.assertEqual(["ex-lecture-1"], report["walkthrough_item_ids"])

    def test_source_inventory_counts_mock_and_past_without_zero_claims(self):
        walks = [
            {"source_type": "other"},
            {"source_type": "mock_exam"},
            {"source_type": "lecture"},
            {"source_type": "past_exam"},
            {"source_type": "mock_exam"},
            {"source_type": "quiz"},
        ]
        inventory = sgd._source_inventory(walks, "en")
        expected_order = (
            "Lecture / handout: 1", "Quiz: 1", "Mock exam: 2",
            "Past exam: 1", "Other material: 1",
        )
        positions = [inventory.index(text) for text in expected_order]
        self.assertEqual(sorted(positions), positions)
        self.assertNotIn("Mock exam: 0", inventory)
        self.assertNotIn("Past exam: 0", inventory)
        self.assertNotIn("Homework", inventory)
        self.assertNotIn("Textbook", inventory)

    def test_source_inventory_escapes_labels_and_scoped_absence_text(self):
        with mock.patch.dict(
                sgd.SOURCE_TYPE_LABELS,
                {"lecture": ("讲义<测试>&", 'Lecture <script>&"')}
        ), mock.patch.object(
                sgd, "SOURCE_ABSENCE",
                ("当前<范围>&", 'Not <provided> & "scoped".')
        ):
            inventory = sgd._source_inventory([{"source_type": "lecture"}], "bilingual")
        self.assertIn("讲义&lt;测试&gt;&amp;：1", inventory)
        self.assertIn("Lecture &lt;script&gt;&amp;&quot;: 1", inventory)
        self.assertIn("当前&lt;范围&gt;&amp;", inventory)
        self.assertIn("Not &lt;provided&gt; &amp; &quot;scoped&quot;.", inventory)
        self.assertNotIn("<script>", inventory)
        self.assertNotIn("<provided>", inventory)

    def test_source_inventory_uses_only_source_type_not_course_specific_fields(self):
        inventory = sgd._source_inventory([{
            "source_type": "quiz",
            "item_id": "EEC-160-course-specific-marker",
            "title": both("不应进入来源清单", "Must not enter the source inventory"),
        }], "en")
        self.assertIn("Quiz: 1", inventory)
        self.assertNotIn("EEC", inventory)
        self.assertNotIn("160", inventory)
        self.assertNotIn("course-specific-marker", inventory)

    def test_figure_only_keeps_original_prompt_and_image(self):
        manifest = self.manifest()
        walk = manifest["walkthroughs"][0]
        walk["prompt_asset_mode"] = "figure_only"
        walk["prompt_text"] = "A car travels 100 km in 2 h. Find its speed."
        document, _ = self._render(manifest)
        self.assertIn("Original prompt", document)
        self.assertIn("A car travels 100 km", document)
        self.assertIn("data:image/png;base64", document)

    def test_non_formula_example_says_so_instead_of_silently_skipping_step(self):
        manifest = self.manifest()
        manifest["knowledge_points"][0]["formulas"] = []
        manifest["walkthroughs"][0]["formula_uses"] = []
        manifest["walkthroughs"][0]["solution_kind"] = "concept"
        manifest["walkthroughs"][0]["no_formula_reason"] = both(
            "这题直接比较定义，不需要代数公式。",
            "This problem compares the definition directly; no algebraic formula is needed.")
        manifest["walkthroughs"][0]["no_formula_reason_provenance"] = both(
            "ai_supplement", "ai_supplement")
        document, _ = self._render(manifest)
        self.assertIn("这题直接比较定义", document)
        self.assertIn("compares the definition directly", document)

    def test_multi_knowledge_point_cross_reference_is_clickable_and_explains_use(self):
        manifest = self.manifest()
        manifest["knowledge_points"].append({
            "id": "kp-units", "title": both("单位一致", "Compatible units"),
            "explanation": both("运算前统一单位。", "Make units compatible before calculating."),
            "explanation_provenance": both(
                "ai_supplement", "ai_supplement"),
            "formulas": [], "source_refs": [self._source("concept")],
            "example_ids": ["ex-lecture-1"],
        })
        walk = manifest["walkthroughs"][0]
        walk["knowledge_point_ids"].append("kp-units")
        walk["knowledge_point_uses"]["kp-units"] = both(
            "确认千米和小时可直接组成千米/小时。",
            "Check that kilometres and hours form km/h directly.")
        walk["knowledge_point_uses_provenance"]["kp-units"] = both(
            "ai_supplement", "ai_supplement")
        document, _ = self._render(manifest)
        self.assertIn('href="#example-ex-lecture-1"', document)
        self.assertIn("确认千米和小时", document)
        self.assertIn("form km/h directly", document)

    def test_knowledge_point_source_assets_render_once_after_safe_validation(self):
        manifest = self.manifest()
        ref = manifest["knowledge_points"][0]["source_refs"][0]
        ref["asset_path"] = "references/assets/prompt.png"
        manifest["knowledge_points"][0]["source_refs"].append(dict(ref))

        document, _ = self._render(manifest)

        match = re.search(r'<div class="knowledge-assets">(.*?)</div>', document, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(1, match.group(1).count("<img "))
        self.assertIn("Knowledge-point source asset", match.group(1))

    def test_markdown_body_rejects_foreign_chapter_student_attempt_asset(self):
        attempt = self._declare_foreign_student_attempt()
        manifest = self.manifest()
        manifest["knowledge_points"][0]["explanation"]["en"] += (
            "\n\n![student submission](%s)" % attempt
        )

        with self.assertRaisesRegex(GuideError, "student_attempt"):
            self._render(manifest)

    def test_direct_concept_asset_rejects_foreign_chapter_student_attempt(self):
        attempt = self._declare_foreign_student_attempt()
        manifest = self.manifest()
        manifest["knowledge_points"][0]["source_refs"][0]["asset_path"] = attempt

        with self.assertRaisesRegex(GuideError, "student_attempt"):
            self._render(manifest)

    def test_asset_policy_drift_during_typed_render_fails_closed(self):
        initial = sgd.workspace_asset_policy_snapshot(self.ws)
        changed = dict(initial)
        changed["tainted_keys"] = set(initial["tainted_keys"]) | {"late-taint"}

        with mock.patch.object(
                sgd, "workspace_asset_policy_snapshot",
                side_effect=[initial, changed]):
            with self.assertRaisesRegex(sgd.ArtifactDriftError, "asset policy changed"):
                self._render()

    def test_typed_source_pages_fail_if_material_file_is_missing_or_misbound(self):
        source = os.path.join(self.materials, "lecture", "ch01.pdf")
        os.remove(source)
        with self.assertRaisesRegex(GuideError, "material source file is missing"):
            self._render()

        with open(source, "wb") as stream:
            stream.write(b"%PDF-1.4\nfixture")
        manifest = self.manifest()
        manifest["knowledge_points"][0]["source_refs"][0][
            "source_file"] = "lecture/not-the-source.pdf"
        with self.assertRaisesRegex(GuideError, "material source file is missing"):
            self._render(manifest)

    def test_direct_document_render_rejects_crop_replaced_after_receipt_validation(self):
        manifest = self.manifest()
        relative = "references/assets/prompt.png"
        target = os.path.join(self.ws, *relative.split("/"))
        with open(target, "rb") as stream:
            approved = stream.read()
        self.assertNotEqual(approved, ALT_VALID_PNG)

        report = sgd.validate_manifest(
            self.ws, 1, manifest, _enforce_v2_crop_receipts=True)
        report = dict(report)
        report["crop_receipt_verification"] = {
            "required": True,
            "status": "verified",
            "verified_asset_count": 1,
            "crop_receipt_ids": ["crop_" + "a" * 64],
            "verified_asset_bindings": [{
                "path": relative,
                "crop_receipt_id": "crop_" + "a" * 64,
                "sha256": hashlib.sha256(approved).hexdigest(),
                "width": 1,
                "height": 1,
            }],
        }
        real_resolve = sgd._resolve_asset
        replaced = {"done": False}
        embedded = {"data_uri": None}

        def replace_before_embed(*args, **kwargs):
            if len(args) > 1 and args[1] == relative and not replaced["done"]:
                replacement = target + ".replacement"
                with open(replacement, "wb") as stream:
                    stream.write(ALT_VALID_PNG)
                os.replace(replacement, target)
                replaced["done"] = True
            resolved = real_resolve(*args, **kwargs)
            if len(args) > 1 and args[1] == relative:
                embedded["data_uri"] = resolved
            return resolved

        with mock.patch.object(sgd, "validate_manifest", return_value=report), \
                mock.patch.object(sgd, "_resolve_asset", side_effect=replace_before_embed), \
                self.assertRaisesRegex(
                    sgr.ArtifactDriftError, "receipt-bound Study Guide crop.*path was replaced"
                ):
            self._render(manifest)
        self.assertTrue(replaced["done"])
        self.assertIn(
            base64.b64encode(approved).decode("ascii"), embedded["data_uri"])
        self.assertNotIn(
            base64.b64encode(ALT_VALID_PNG).decode("ascii"), embedded["data_uri"])

    def test_material_source_parent_symlink_or_reparse_is_rejected_when_supported(self):
        outside = tempfile.mkdtemp(prefix="outside-materials-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with open(os.path.join(outside, "ch01.pdf"), "wb") as stream:
            stream.write(b"%PDF-1.4\noutside")
        shutil.rmtree(os.path.join(self.materials, "lecture"))
        try:
            os.symlink(outside, os.path.join(self.materials, "lecture"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation is unavailable")
        with self.assertRaisesRegex(GuideError, "symlink/junction/reparse"):
            self._render()

    def test_document_lint_rejects_controls_raw_tex_and_duplicate_cards(self):
        document, report = self._render()
        with self.assertRaises(GuideError):
            sgd.validate_guide_document(
                document.replace("</main>", "<details></details></main>"),
                report["walkthrough_item_ids"], self.ws, self.materials)
        with self.assertRaises(GuideError):
            sgd.validate_guide_document(
                document.replace("</main>", r"\frac{x}{y}</main>"),
                report["walkthrough_item_ids"], self.ws, self.materials)
        card = '<article data-item-id="ex-lecture-1"></article>'
        with self.assertRaises(GuideError):
            sgd.validate_guide_document(
                document.replace("</main>", card + "</main>"),
                report["walkthrough_item_ids"], self.ws, self.materials)

    def test_formula_source_quote_is_rendered_as_mathml_not_visible_tex(self):
        manifest = self.manifest()
        manifest["knowledge_points"][0]["formulas"][0]["source_refs"][0][
            "quote_span"] = r"v=\frac{d}{t}"
        document, report = self._render(manifest)
        self.assertNotIn(r"\frac", document)
        self.assertGreaterEqual(document.count("<math"), 2)
        self.assertTrue(sgd.validate_guide_document(
            document, report["walkthrough_item_ids"], self.ws, self.materials)["ok"])

    def test_print_css_paginates_long_blocks_without_blank_colored_shells(self):
        document, _report = self._render()
        self.assertIn(
            ".formula-card,.formula-use,.final-answer,.answer-explanation,table,.prompt-zone,.walkthrough-zone"
            "{break-inside:auto;box-decoration-break:clone;-webkit-box-decoration-break:clone}",
            document,
        )
        self.assertNotIn(".mapped-examples-heading{break-before:page}", document)
        self.assertNotIn(".knowledge-section{break-before:page}", document)
        self.assertIn("tr{break-inside:avoid}", document)
        self.assertIn(
            ".localized-pair,.answer-language,.solution-steps>li"
            "{break-inside:avoid;page-break-inside:avoid}",
            document,
        )
        self.assertIn(".answer-language+.answer-language{break-before:avoid}", document)
        self.assertNotIn(".answer-pair,.answer-language", document)
        self.assertNotIn(".solution-steps>li,.closing-pair", document)
        self.assertIn(
            "figure,.example-header,.kp-uses,.prompt-intro,.formula-intro,.translation,.provenance-legend,.cross-reference li,.source-box li"
            "{break-inside:avoid;page-break-inside:avoid}",
            document,
        )
        self.assertIn(".example-header{break-after:avoid}", document)
        self.assertIn(
            ".source-box>strong{break-after:avoid;page-break-after:avoid}",
            document,
        )
        self.assertNotIn(".example-header,.kp-uses{break-after:avoid}", document)
        self.assertIn(".prompt-intro{display:inline-block;width:100%;vertical-align:top}", document)
        self.assertIn(".source-asset img{max-height:160mm}", document)
        self.assertIn(".example-card .source-asset img{max-height:155mm}", document)

    def test_ai_substitution_provenance_is_rendered_below_not_inside_math(self):
        value = r"\boxed{v=50}\quad\text{AI补充 / AI-supplemented}"
        self.assertEqual(
            (r"\boxed{v=50}", "ai_supplement"),
            sgd._split_substitution_provenance(value),
        )
        manifest = self.manifest()
        manifest["walkthroughs"][0]["formula_uses"][0]["substitution"] = value
        document, _report = self._render(manifest)
        self.assertIn('<div class="formula-display substitution-math">', document)
        self.assertIn('class="provenance-marker"', document)
        self.assertNotIn("AI补充 / AI-supplemented</mtext>", document)

    def test_document_lint_uses_shared_raw_tex_vocabulary(self):
        document, report = self._render()
        for command, message in (
                (r"\alpha", "visible raw TeX"),
                (r"\pmod", "visible raw TeX"),
                (r"$\alpha$", "visible raw TeX"),
                (r"$x$", "unrendered dollar-delimited TeX")):
            with self.subTest(command=command):
                with self.assertRaisesRegex(GuideError, message):
                    sgd.validate_guide_document(
                        document.replace("</main>", command + "</main>"),
                        report["walkthrough_item_ids"], self.ws, self.materials)
        with_paths = document.replace(
            "</main>", r"D:\min\notes C:\beta\file \\server\share\quad</main>")
        self.assertTrue(sgd.validate_guide_document(
            with_paths, report["walkthrough_item_ids"], self.ws, self.materials)["ok"])

    def test_document_lint_rejects_remote_script_and_traversal_links(self):
        document, report = self._render()
        expected = report["walkthrough_item_ids"]
        source_uri = Path(os.path.join(
            self.materials, "lecture", "ch01.pdf")).resolve().as_uri() + "#page=4"
        for unsafe in (
                "https://example.invalid/ch01.pdf#page=4",
                "javascript:alert(1)",
                Path(os.path.join(self.materials, "..", "outside.pdf")).resolve().as_uri()
                + "#page=4"):
            with self.subTest(unsafe=unsafe):
                poisoned = document.replace(source_uri, unsafe, 1)
                with self.assertRaises(GuideError):
                    sgd.validate_guide_document(
                        poisoned, expected, self.ws, self.materials)

    def test_document_lint_rejects_nul_and_replacement_character(self):
        document, report = self._render()
        for bad in ("\x00", "\ufffd"):
            with self.subTest(bad=ord(bad)):
                with self.assertRaises(GuideError):
                    sgd.validate_guide_document(
                        document + bad, report["walkthrough_item_ids"],
                        self.ws, self.materials)

    def test_default_cli_requires_typed_manifest_and_writes_branded_receipt(self):
        os.makedirs(os.path.join(self.ws, "notebook"), exist_ok=True)
        self._json("notebook/ch01.guide.json", self.manifest())
        os.makedirs(os.path.join(self.ws, "study_guide"))
        stale_pdf = os.path.join(self.ws, "study_guide", "ch01.pdf")
        with open(stale_pdf, "wb") as stream:
            stream.write(b"%PDF-1.4\nstale")
        self.assertEqual(0, self._run_synthetic_publication_fixture([
            "--workspace", self.ws, "--chapter", "1", "--profile", "full",
            "--pdf-backend", "html",
        ]))
        html_path = os.path.join(self.ws, "study_guide", "ch01.html")
        receipt_path = os.path.join(self.ws, "study_guide", "ch01.receipt.json")
        self.assertTrue(os.path.isfile(html_path))
        with open(receipt_path, "r", encoding="utf-8") as stream:
            receipt = json.load(stream)
        self.assertEqual(3, receipt["schema_version"])
        self.assertEqual("study_guide", receipt["artifact_type"])
        self.assertEqual("full", receipt["profile"])
        self.assertEqual("bilingual", receipt["language"])
        self.assertEqual("html_ready", receipt["status"])
        self.assertEqual("not_requested", receipt["visual_qa"]["status"])
        self.assertEqual(["ex-lecture-1"], receipt["rendered_item_ids"])
        self.assertEqual([], receipt["omitted_item_ids"])
        self.assertIsNone(receipt["pdf_file"])
        self.assertIsNone(receipt["pdf_sha256"])
        self.assertIsNone(receipt["conversion_input_html_sha256"])
        self.assertFalse(os.path.exists(stale_pdf))
        self.assertTrue(receipt["start_gate"]["ready_to_use"])
        self.assertEqual("a" * 64, receipt["start_gate"]["runtime_digest"])

    def test_true_study_guide_requires_explicit_profile(self):
        with self.assertRaisesRegex(GuideError, "requires explicit --profile"):
            sgr.run(["--workspace", self.ws, "--chapter", "1"], fake_math)

    def test_true_study_guide_start_gate_fails_closed(self):
        import exam_start

        with mock.patch.object(
                exam_start, "require_full_processing",
                side_effect=self.real_require_full_processing), \
                mock.patch.object(
                    exam_start, "check_registered_workspace_gate",
                    return_value={
                        "ready_to_use": False,
                        "reason": "workspace_not_registered",
                    }):
            with self.assertRaisesRegex(
                    GuideError, "exact confirmed workspace/materials pair"):
                self.real_start_gate(self.ws)

    def test_browser_pdf_receipt_is_hash_bound_and_waits_for_page_qa(self):
        os.makedirs(os.path.join(self.ws, "notebook"), exist_ok=True)
        self._json("notebook/ch01.guide.json", self.manifest())

        def fake_print(browser, html_path, pdf_path, timeout=120, **unused_roots):
            del timeout
            with open(pdf_path, "wb") as stream:
                stream.write(b"%PDF-1.4\n" + b"x" * 200)
            with open(html_path, "r", encoding="utf-8") as stream:
                document = stream.read()
            return {
                "converter": os.path.abspath(browser),
                "started_at": "2026-07-14T10:00:00Z",
                "completed_at": "2026-07-14T10:00:01Z",
                "input_html_sha256": sgr._sha256_bytes(
                    sgr._print_ready_html(
                        document,
                        workspace=unused_roots.get("workspace"),
                        materials_root=unused_roots.get("materials_root"),
                    ).encode("utf-8")),
                "source_html_sha256": sgr._sha256_file(html_path),
            }

        with mock.patch.object(sgr, "find_browser", return_value="C:/browser/msedge.exe"), \
                mock.patch.object(sgr, "print_pdf", side_effect=fake_print):
            self.assertEqual(0, self._run_synthetic_publication_fixture([
                "--workspace", self.ws, "--chapter", "1", "--profile", "full",
                "--pdf-backend", "browser", "--pdf",
            ]))
        with open(os.path.join(self.ws, "study_guide", "ch01.receipt.json"),
                  encoding="utf-8") as stream:
            receipt = json.load(stream)
        self.assertEqual("qa_pending", receipt["status"])
        self.assertEqual("pending", receipt["visual_qa"]["status"])
        self.assertRegex(receipt["pdf_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("browser", receipt["pdf_backend"])
        self.assertTrue(receipt["converter"].replace("\\", "/").endswith("browser/msedge.exe"))
        self.assertEqual(receipt["html_sha256"], sgr._sha256_file(
            os.path.join(self.ws, "study_guide", "ch01.html")))
        self.assertRegex(receipt["conversion_input_html_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["conversion_run_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("2026-07-14T10:00:00Z", receipt["conversion_started_at"])
        self.assertEqual("2026-07-14T10:00:01Z", receipt["conversion_completed_at"])

    def test_real_browser_pdf_exposes_page_numbers_to_qa_when_available(self):
        browser = sgr.find_browser()
        if not browser or sgqa.detect_backend() is None:
            self.skipTest("real browser/PDF raster backend unavailable")
        os.makedirs(os.path.join(self.ws, "notebook"), exist_ok=True)
        self._json("notebook/ch01.guide.json", self.manifest())
        with mock.patch.object(sgr, "find_browser", return_value=browser):
            self.assertEqual(0, self._run_synthetic_publication_fixture([
                "--workspace", self.ws, "--chapter", "1", "--profile", "full",
                "--pdf-backend", "browser", "--pdf",
            ]))
        code, report = sgqa.render(self.ws, 1)
        with open(os.path.join(self.ws, "study_guide", "ch01.receipt.json"),
                  encoding="utf-8") as stream:
            qa = json.load(stream).get("visual_qa")
        if code != 0:
            pdf_path = os.path.join(self.ws, "study_guide", "ch01.pdf")
            extracted = [page["text"] for page in sgqa.detect_backend().render_pages(pdf_path)]
        else:
            extracted = []
        self.assertEqual(0, code, {"qa": qa, "extracted": extracted})
        self.assertEqual("rendered", report["status"])


if __name__ == "__main__":
    unittest.main()
