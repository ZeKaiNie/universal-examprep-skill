import contextlib
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from scripts import readiness
from scripts import validate_workspace


class CapabilityMatrix(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = self.temp.name
        os.makedirs(os.path.join(self.ws, "references", "wiki"))
        os.makedirs(os.path.join(self.ws, "notebook"))
        with open(os.path.join(self.ws, "references", "wiki", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("# Chapter 1\n\nA source-backed concept.\n")
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as f:
            json.dump({"current_phase": 1, "language": "bilingual"}, f)

    def tearDown(self):
        self.temp.cleanup()

    def _json(self, relative, value):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)

    def _jsonl(self, relative, rows):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _validated_manifest(self, formulas=None, substitutions=None):
        formulas = formulas or []
        substitutions = substitutions or []
        manifest = {
            "knowledge_points": [{"formulas": [{"latex": value} for value in formulas]}],
            "walkthroughs": [{
                "formula_uses": [{"substitution": value} for value in substitutions]
            }],
        }
        report = {
            "ok": True,
            "chapter": 1,
            "profile": "full",
            "language": "bilingual",
            "expected_item_ids": ["item-1"],
            "walkthrough_item_ids": ["item-1"],
            "omitted_item_ids": [],
            "input_path": os.path.join(self.ws, "notebook", "ch01.guide.json"),
        }
        return mock.patch.object(
            readiness.study_guide_content, "load_and_validate_manifest",
            return_value=(manifest, report),
        )

    def test_structural_success_does_not_imply_quiz_or_artifact_ready(self):
        self._json("references/quiz_bank.json", [{
            "id": "q1", "chapter": 1, "type": "subjective",
            "question": "Explain it", "answer": "An answer",
        }])
        matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        self.assertEqual("ready", matrix["workspace_structural"]["status"])
        self.assertEqual("blocked", matrix["quiz_ready"]["status"])
        self.assertIn("no_gradable_chapter_items", matrix["quiz_ready"]["reason_codes"])
        self.assertEqual("blocked", matrix["artifact_ready"]["status"])
        self.assertIn("chapter_teaching_manifest_missing",
                      matrix["artifact_ready"]["reason_codes"])

    def test_valid_quiz_and_manifest_are_reported_separately(self):
        self._json("references/quiz_bank.json", [{
            "id": "q1", "chapter": 1, "type": "subjective", "gradable": True,
            "question": "Explain it", "answer": "An answer", "keywords": ["answer"],
        }, {
            "id": "legacy-demo", "chapter": 1, "type": "subjective", "gradable": False,
            "question": "Worked demonstration", "answer": "",
        }])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        with self._validated_manifest() as loader:
            matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        loader.assert_called_once_with(self.ws, 1)
        self.assertEqual("ready", matrix["quiz_ready"]["status"])
        self.assertEqual(1, matrix["quiz_ready"]["counts"]["candidate_items"])
        self.assertEqual("ready", matrix["artifact_ready"]["status"])
        self.assertEqual("none", matrix["artifact_ready"]["counts"]["math_status"])

    def test_control_text_and_current_chapter_formula_review_block_artifact(self):
        with open(os.path.join(self.ws, "references", "wiki", "ch01.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Broken\x00 text\n")
        self._json("references/quiz_bank.json", [])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        os.makedirs(os.path.join(self.ws, ".ingest"), exist_ok=True)
        with open(os.path.join(self.ws, ".ingest", "content_units.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({"unit_id": "unit_1", "chapter_id": "ch01"}) + "\n")
        with open(os.path.join(self.ws, ".ingest", "review_queue.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "status": "pending", "severity": "warning",
                "reason_codes": ["formula_hint"], "target_unit_ids": ["unit_1"],
            }) + "\n")
        matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        self.assertEqual("blocked", matrix["teaching_ready"]["status"])
        self.assertIn("unsafe_control_text", matrix["teaching_ready"]["reason_codes"])
        self.assertEqual("blocked", matrix["artifact_ready"]["status"])
        self.assertIn("chapter_source_recovery_pending",
                      matrix["artifact_ready"]["reason_codes"])

    def test_math_status_uses_typed_formulas_and_substitutions(self):
        self._json("references/quiz_bank.json", [])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        with self._validated_manifest(formulas=["E=mc^2"], substitutions=["E=2c^2"]):
            result = readiness.chapter_math_readiness(self.ws, 1)
        self.assertEqual("standard", result["status"])
        self.assertEqual(1, result["counts"]["formulas"])
        self.assertEqual(1, result["counts"]["substitutions"])

    def test_replacement_character_and_active_formula_issue_need_recovery(self):
        with open(os.path.join(self.ws, "references", "wiki", "ch01.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Broken \ufffd chapter\n")
        self._json("references/quiz_bank.json", [])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        os.makedirs(os.path.join(self.ws, ".ingest"), exist_ok=True)
        with open(os.path.join(self.ws, ".ingest", "content_units.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({"unit_id": "unit_1", "chapter_id": "ch01"}) + "\n")
        with open(os.path.join(self.ws, ".ingest", "review_queue.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "status": "pending", "reason_codes": ["formula_hint"],
                "target_unit_ids": ["unit_1"],
            }) + "\n")
        with self._validated_manifest(formulas=["x=y"]):
            result = readiness.chapter_math_readiness(self.ws, 1)
        self.assertEqual("needs_recovery", result["status"])
        self.assertIn("unicode_replacement_text", result["reason_codes"])
        self.assertIn("chapter_source_recovery_pending", result["reason_codes"])
        self.assertEqual(1, result["counts"]["replacement_characters"])

    def test_targetless_high_risk_issue_maps_by_source_page_and_blocks_chapter(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [{
            "unit_id": "unit_ch1", "source_id": "src_ch1", "page": 4,
            "chapter_id": "ch01",
        }])
        self._jsonl(".ingest/review_queue.jsonl", [{
            "status": "pending", "severity": "warning",
            "source_id": "src_ch1", "pages": [4],
            "reason_codes": ["formula_hint"], "target_unit_ids": [],
        }])
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("blocked", artifact["status"])
        self.assertIn("chapter_source_recovery_pending", artifact["reason_codes"])
        self.assertNotIn("unbound_high_risk_review_pending", artifact["reason_codes"])
        self.assertEqual(1, artifact["counts"]["chapter_high_risk_review_issues"])
        self.assertEqual(0, artifact["counts"]["global_unbound_high_risk_review_issues"])
        self.assertEqual({"source_page": 1}, artifact["counts"]["review_attribution"])

    def test_targetless_other_chapter_high_risk_does_not_block_selected_chapter(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [{
            "unit_id": "unit_ch2", "source_id": "src_ch2", "page": 8,
            "chapter_id": "ch02",
        }])
        self._jsonl(".ingest/review_queue.jsonl", [{
            "status": "pending", "severity": "warning",
            "source_id": "src_ch2", "pages": [8],
            "reason_codes": ["formula_hint"], "target_unit_ids": [],
        }])
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("ready", artifact["status"])
        self.assertEqual(0, artifact["counts"]["chapter_high_risk_review_issues"])
        self.assertEqual(0, artifact["counts"]["global_unbound_high_risk_review_issues"])
        self.assertEqual(1, artifact["counts"]["other_chapter_active_review_issues"])

    def test_high_risk_issue_without_any_mapping_blocks_artifacts_globally(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [{
            "unit_id": "unit_ch1", "source_id": "src_ch1", "page": 1,
            "chapter_id": "ch01",
        }])
        self._jsonl(".ingest/review_queue.jsonl", [{
            "status": "claimed", "severity": "warning",
            "source_id": "src_unknown", "pages": [99],
            "reason_codes": ["garbled_text", "formula_hint"],
            "target_unit_ids": ["unit_missing"],
        }])
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("blocked", artifact["status"])
        self.assertIn("unbound_high_risk_review_pending", artifact["reason_codes"])
        self.assertNotIn("chapter_source_recovery_pending", artifact["reason_codes"])
        self.assertEqual(1, artifact["counts"]["global_unbound_high_risk_review_issues"])
        self.assertEqual(
            {"formula_hint": 1, "garbled_text": 1},
            artifact["counts"]["global_unbound_high_risk_review_reasons"],
        )
        self.assertEqual({"unbound": 1}, artifact["counts"]["review_attribution"])
        self.assertIn("unbound_high_risk_review_pending",
                      artifact["counts"]["math_reasons"])

    def test_unbound_non_high_risk_warning_is_counted_but_does_not_block_artifact(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [])
        self._jsonl(".ingest/review_queue.jsonl", [{
            "status": "pending", "severity": "warning",
            "source_id": "src_unknown", "pages": [3],
            "reason_codes": ["ambiguous_pairing"], "target_unit_ids": [],
        }])
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("ready", artifact["status"])
        self.assertEqual(1, artifact["counts"]["unbound_active_review_issues"])
        self.assertEqual(0, artifact["counts"]["global_unbound_high_risk_review_issues"])
        self.assertEqual("none", artifact["counts"]["math_status"])

    def test_terminal_unbound_high_risk_issues_do_not_block_artifact(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [])
        for status in sorted(readiness.TERMINAL_REVIEW_STATUSES):
            with self.subTest(status=status):
                self._jsonl(".ingest/review_queue.jsonl", [{
                    "status": status, "severity": "blocking",
                    "source_id": "src_unknown", "pages": [3],
                    "reason_codes": ["formula_hint"], "target_unit_ids": [],
                }])
                with self._validated_manifest():
                    matrix = readiness.capability_readiness(self.ws, chapter=1)
                artifact = matrix["artifact_ready"]
                self.assertEqual("ready", artifact["status"])
                self.assertEqual(
                    0, artifact["counts"]["global_unbound_high_risk_review_issues"])
                self.assertEqual(0, artifact["counts"]["unbound_active_review_issues"])

    def test_source_only_fallback_requires_one_unique_chapter(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [{
            "unit_id": "unit_a", "source_id": "src_one", "page": 1,
            "chapter_id": "ch01",
        }, {
            "unit_id": "unit_b", "source_id": "src_one", "page": 2,
            "chapter_id": "ch01",
        }])
        self._jsonl(".ingest/review_queue.jsonl", [{
            "status": "pending", "severity": "warning",
            "source_id": "src_one", "pages": [],
            "reason_codes": ["formula_hint"], "target_unit_ids": [],
        }])
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("blocked", artifact["status"])
        self.assertEqual(
            {"source_unique_chapter": 1},
            artifact["counts"]["review_attribution"],
        )

    def test_truncated_review_queue_is_a_global_artifact_and_math_blocker(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._jsonl(".ingest/content_units.jsonl", [{
            "unit_id": "unit_ch1", "source_id": "src_ch1", "page": 4,
            "chapter_id": "ch01",
        }])
        queue = os.path.join(self.ws, ".ingest", "review_queue.jsonl")
        with open(queue, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "status": "pending", "severity": "blocking",
                "source_id": "src_ch1", "pages": [4],
                "reason_codes": ["formula_hint"], "target_unit_ids": [],
            }) + "\n")
            f.write("{truncated\n")

        scope = readiness._review_scope(self.ws, 1)
        self.assertEqual(["review_queue_unreadable_or_invalid"], scope["load_errors"])
        self.assertEqual(1, len(scope["local_high_risk_rows"]))
        self.assertEqual(
            {"review_queue_unreadable_or_invalid": 1},
            scope["global_unbound_high_risk_reasons"],
        )

        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("blocked", artifact["status"])
        self.assertIn("review_queue_unreadable_or_invalid", artifact["reason_codes"])
        self.assertIn("unbound_high_risk_review_pending", artifact["reason_codes"])
        self.assertIn(
            "review_queue_unreadable_or_invalid",
            artifact["counts"]["math_reasons"],
        )

    def test_malformed_content_units_cannot_make_review_attribution_safe(self):
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        units = os.path.join(self.ws, ".ingest", "content_units.jsonl")
        os.makedirs(os.path.dirname(units), exist_ok=True)
        with open(units, "w", encoding="utf-8") as f:
            f.write("{broken\n")
        self._jsonl(".ingest/review_queue.jsonl", [])

        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, chapter=1)
        artifact = matrix["artifact_ready"]
        self.assertEqual("blocked", artifact["status"])
        self.assertIn("content_units_unreadable_or_invalid", artifact["reason_codes"])
        self.assertEqual(
            {"content_units_unreadable_or_invalid": 1},
            artifact["counts"]["global_unbound_high_risk_review_reasons"],
        )

    def test_visual_artifact_requires_matching_hashes_and_ready_all_page_qa(self):
        self._json("references/quiz_bank.json", [])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._json("study_state.json", {
            "current_phase": 1, "language": "bilingual", "artifact_mode": "visual",
        })
        guide = os.path.join(self.ws, "study_guide")
        os.makedirs(guide)
        files = {
            "ch01.html": b'<!doctype html><html><body>guide</body></html>',
            "ch01.pdf": b"%PDF-1.4\nexample",
        }
        for relative, payload in files.items():
            path = os.path.join(guide, *relative.split("/"))
            with open(path, "wb") as stream:
                stream.write(payload)
        materials = os.path.join(self.ws, "materials")
        os.makedirs(materials)
        gate = {
            "ready_to_use": True,
            "workspace": self.ws,
            "materials": materials,
            "registered_course": "fixture-course",
            "runtime_provenance": {"receipt": {
                "runtime_digest": "a" * 64,
                "runtime_file_count": 10,
                "skill_version": "5.0.0-test",
                "git_commit": "b" * 40,
                "git_branch": "codex/test",
                "git_dirty": False,
                "python_executable": "python-test",
            }},
        }
        artifact_qa = readiness.study_guide_qa
        html_path = os.path.join(guide, "ch01.html")
        pdf_path = os.path.join(guide, "ch01.pdf")
        manifest_path = os.path.join(self.ws, "notebook", "ch01.guide.json")
        html_hash = artifact_qa._sha256_file(html_path)
        pdf_hash = artifact_qa._sha256_file(pdf_path)
        input_hash = artifact_qa._conversion_input_hash(html_path)
        start_gate = artifact_qa._start_gate_snapshot(gate)
        converter = os.path.abspath("C:/browser/msedge.exe")
        started = "2026-07-14T10:00:00Z"
        completed = "2026-07-14T10:00:01Z"
        self._json("study_guide/ch01.receipt.json", {
            "schema_version": 2,
            "artifact_type": "study_guide",
            "chapter": 1,
            "profile": "full",
            "language": "bilingual",
            "content_manifest": "notebook/ch01.guide.json",
            "content_manifest_sha256": artifact_qa._sha256_file(manifest_path),
            "expected_item_ids": ["item-1"],
            "rendered_item_ids": ["item-1"],
            "omitted_item_ids": [],
            "html_file": "study_guide/ch01.html",
            "html_sha256": html_hash,
            "pdf_file": "study_guide/ch01.pdf",
            "pdf_sha256": pdf_hash,
            "pdf_backend": "browser",
            "converter": converter,
            "conversion_input_html_sha256": input_hash,
            "conversion_started_at": started,
            "conversion_completed_at": completed,
            "conversion_run_sha256": artifact_qa._conversion_run_hash(
                1, "full", "bilingual", html_hash, pdf_hash, input_hash,
                converter, started, completed, start_gate,
            ),
            "preflight": {"status": "passed", "pdf_backend": "browser"},
            "start_gate": start_gate,
            "generated_at": "2026-07-14T10:00:02Z",
            "status": "qa_pending",
            "visual_qa": {"schema_version": 1, "status": "pending"},
        })

        class Backend(object):
            name = "fixture-renderer"

            def render_pages(self, unused_path):
                png = (artifact_qa.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR"
                       + struct.pack(">II", 1200, 1800) + b"page-one")
                return [{
                    "png": png,
                    "text": "Readable study guide content.\nPage 1 of 1",
                    "width": 1200,
                    "height": 1800,
                    "white_ratio": 0.93,
                }]

        with self._validated_manifest(), mock.patch.object(
                artifact_qa.exam_start, "check_registered_workspace_gate",
                return_value=gate):
            code, unused_summary = artifact_qa.render(
                self.ws, 1, backend=Backend(), now="2026-07-14T10:01:00Z")
            self.assertEqual(0, code)
            code, unused_summary = artifact_qa.accept(
                self.ws, 1, "all", "codex", page_verdicts=["1=pass"],
                now="2026-07-14T10:02:00Z")
            self.assertEqual(0, code)
            matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        self.assertEqual("ready", matrix["artifact_ready"]["status"])
        receipt = matrix["artifact_ready"]["counts"]["receipt"]
        self.assertTrue(receipt["html_hash_match"])
        self.assertTrue(receipt["pdf_hash_match"])
        self.assertEqual("ready", receipt["qa_status"])

        with open(os.path.join(guide, "ch01.pdf"), "ab") as stream:
            stream.write(b"drift")
        with self._validated_manifest(), mock.patch.object(
                artifact_qa.exam_start, "check_registered_workspace_gate",
                return_value=gate):
            matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        self.assertEqual("blocked", matrix["artifact_ready"]["status"])
        self.assertIn("chapter_artifact_hash_mismatch",
                      matrix["artifact_ready"]["reason_codes"])

    def test_visual_artifact_rejects_minimal_fake_receipt(self):
        self._json("references/quiz_bank.json", [])
        self._json("notebook/ch01.guide.json", {"schema_version": 1, "chapter": 1})
        self._json("study_state.json", {
            "current_phase": 1, "language": "bilingual", "artifact_mode": "visual",
        })
        self._json("study_guide/ch01.receipt.json", {
            "chapter": 1,
            "html_sha256": "a" * 64,
            "pdf_sha256": "b" * 64,
            "visual_qa": {"status": "ready", "inspected_pages": "all"},
        })
        with self._validated_manifest():
            matrix = readiness.capability_readiness(self.ws, [], [], {}, chapter=1)
        self.assertEqual("blocked", matrix["artifact_ready"]["status"])
        self.assertIn("chapter_artifact_receipt_invalid",
                      matrix["artifact_ready"]["reason_codes"])


class BoundedValidatorOutput(unittest.TestCase):
    def test_json_defaults_to_bounded_details_and_keeps_counts(self):
        with tempfile.TemporaryDirectory() as ws:
            errors = []
            warnings = [{"level": "warning", "msg": "formula warning %d" % i}
                        for i in range(100)]
            out = io.StringIO()
            with mock.patch.object(validate_workspace, "validate",
                                   return_value=(errors, warnings, {"sentinel": 1})):
                with mock.patch.object(
                        validate_workspace._readiness_matrix, "capability_readiness",
                        return_value={
                            "chapter": 1,
                            "workspace_structural": {"status": "ready"},
                            "teaching_ready": {"status": "usable_with_gaps"},
                            "quiz_ready": {"status": "blocked"},
                            "artifact_ready": {
                                "status": "blocked",
                                "reason_codes": ["chapter_artifact_hash_mismatch"],
                                "counts": {"math_status": "needs_recovery"},
                            },
                        }):
                    with contextlib.redirect_stdout(out):
                        code = validate_workspace.main([ws, "--json", "--max-items", "5"])
            self.assertEqual(0, code)
            payload = json.loads(out.getvalue())
            self.assertEqual(100, payload["warning_count"])
            self.assertEqual(5, len(payload["warnings"]))
            self.assertEqual(95, payload["truncated"]["warnings"])
            self.assertEqual({"formula_or_math": 100}, payload["warning_summary"])
            artifact = payload["capabilities"]["artifact_ready"]
            self.assertIn("chapter_artifact_hash_mismatch", artifact["reason_codes"])
            self.assertEqual("needs_recovery", artifact["counts"]["math_status"])


if __name__ == "__main__":
    unittest.main()
