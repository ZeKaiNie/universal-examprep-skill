# -*- coding: utf-8 -*-
import base64
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
        self._json("study_state.json", {"language": "bilingual"})
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
        gate_patch = mock.patch.object(
            sgr, "_start_gate_or_raise", return_value=self.start_gate)
        gate_patch.start()
        self.addCleanup(gate_patch.stop)
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

    def _source(self, role):
        return {"source_file": "lecture/ch01.pdf", "pages": [4],
                "source_unit_id": "unit-p4", "role": role}

    def _render(self, manifest=None):
        return sgd.render_manifest(
            self.ws, manifest or self.manifest(), fake_math,
            materials_root=self.materials)

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
            "source_refs": [self._source("formula")],
        }
        walk = {
            "item_id": "ex-lecture-1", "source_type": "lecture",
            "answer_provenance": {"zh": "ai_supplemented", "en": "material"},
            "knowledge_point_ids": ["kp-speed"],
            "knowledge_point_uses": {
                "kp-speed": both("把题目中的路程和时间代入平均速度关系。",
                                 "Map the prompt's distance and time into average speed."),
            },
            "notebook_anchor": "ex-lecture-1-ex-lecture-1",
            "title": both("速度公式例题", "Speed-formula example"),
            "original_language": "en", "prompt_asset_mode": "full_prompt",
            "prompt_asset_paths": ["references/assets/prompt.png"], "answer_asset_paths": [],
            "translation": {"zh": "一辆车 2 小时行驶 100 千米，求速度。"},
            "what_asked": both("求平均速度。", "Find the average speed."),
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
            }],
            "steps": [
                both("先写出速度公式。", "Write the speed formula."),
                both("代入 100 和 2，得到 50。", "Substitute 100 and 2 to get 50."),
            ],
            "answer": both("平均速度是 50 千米/小时。", "The average speed is 50 km/h."),
            "self_check": both("50×2=100，能还原路程。", "50×2=100, which recovers the distance."),
            "source_trace": [self._source("question"), self._source("answer")],
        }
        return {
            "schema_version": 1, "chapter": 1, "language": "bilingual", "profile": "full",
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
        self.assertIn("🟡 AI翻译，原资料为另一种语言", document)
        self.assertNotIn("🟢 来自资料", document)
        self.assertIn("🟢 From your materials", document)
        expected_uri = Path(os.path.join(
            self.materials, "lecture", "ch01.pdf")).resolve().as_uri() + "#page=4"
        self.assertIn('href="%s"' % expected_uri, document)

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
        document, _ = self._render(manifest)
        self.assertIn("这题直接比较定义", document)
        self.assertIn("compares the definition directly", document)

    def test_multi_knowledge_point_cross_reference_is_clickable_and_explains_use(self):
        manifest = self.manifest()
        manifest["knowledge_points"].append({
            "id": "kp-units", "title": both("单位一致", "Compatible units"),
            "explanation": both("运算前统一单位。", "Make units compatible before calculating."),
            "formulas": [], "source_refs": [self._source("concept")],
            "example_ids": ["ex-lecture-1"],
        })
        walk = manifest["walkthroughs"][0]
        walk["knowledge_point_ids"].append("kp-units")
        walk["knowledge_point_uses"]["kp-units"] = both(
            "确认千米和小时可直接组成千米/小时。",
            "Check that kilometres and hours form km/h directly.")
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
        self.assertEqual(0, sgr.run([
            "--workspace", self.ws, "--chapter", "1", "--profile", "full",
            "--pdf-backend", "html",
        ], fake_math))
        html_path = os.path.join(self.ws, "study_guide", "ch01.html")
        receipt_path = os.path.join(self.ws, "study_guide", "ch01.receipt.json")
        self.assertTrue(os.path.isfile(html_path))
        with open(receipt_path, "r", encoding="utf-8") as stream:
            receipt = json.load(stream)
        self.assertEqual(2, receipt["schema_version"])
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
                exam_start, "check_registered_workspace_gate",
                return_value={"ready_to_use": False, "reason": "workspace_not_registered"}):
            with self.assertRaisesRegex(GuideError, "exam_start confirm"):
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
            self.assertEqual(0, sgr.run([
                "--workspace", self.ws, "--chapter", "1", "--profile", "full",
                "--pdf-backend", "browser", "--pdf",
            ], fake_math))
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
            self.assertEqual(0, sgr.run([
                "--workspace", self.ws, "--chapter", "1", "--profile", "full",
                "--pdf-backend", "browser", "--pdf",
            ], fake_math))
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
