# -*- coding: utf-8 -*-
"""End-to-end contracts for the official ingestion orchestrator."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from scripts import exam_start, ingest_course
from scripts.ingestion import ContentUnit, IngestionStore, ReviewPatch
from scripts.ingestion.pipeline import compile_review_outputs


class IngestCourseTest(unittest.TestCase):
    def test_validator_protocol_rejects_crash_or_inconsistent_json(self):
        bad_results = [
            SimpleNamespace(returncode=1, stdout="", stderr="traceback"),
            SimpleNamespace(returncode=0, stdout="{}", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout=json.dumps({"readiness": "blocked", "exit_code": 0,
                                   "error_count": 1}), stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"readiness": "blocked", "exit_code": 0,
                                   "error_count": 1}), stderr=""),
        ]
        for result in bad_results:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    ingest_course._validated_workspace_payload(result)

    def test_validator_protocol_accepts_content_block(self):
        result = SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"readiness": "blocked", "exit_code": 1,
                               "error_count": 3}), stderr="")
        self.assertEqual(
            "blocked", ingest_course._validated_workspace_payload(result)["readiness"])

    def run_course(self, materials, workspace, confirm=True):
        output = io.StringIO()
        home = workspace.parent / ".examprep-home"
        identity = exam_start._capture_runtime_identity()
        with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                mock.patch.object(
                    exam_start, "_capture_runtime_identity", return_value=identity
                ):
            if confirm and materials.is_dir():
                confirmation_output = io.StringIO()
                with contextlib.redirect_stdout(confirmation_output):
                    confirmation_code = exam_start.run([
                        "confirm", "--course", "test-course",
                        "--materials", str(materials),
                        "--workspace", str(workspace),
                        "--mode", "from_scratch",
                        "--time-budget", "le1d",
                        "--language", "en",
                        "--json",
                    ])
                self.assertEqual(0, confirmation_code, confirmation_output.getvalue())
            with contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])
        return code, json.loads(output.getvalue())

    def test_clean_text_course_is_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nCore concept\nA detailed source-backed explanation.",
                encoding="utf-8",
            )

            code, payload = self.run_course(materials, workspace)
            self.assertEqual(0, code)
            self.assertTrue(payload["process_success"])
            self.assertEqual("ready", payload["readiness"])
            self.assertTrue((workspace / ".ingest" / "build_manifest.json").is_file())
            self.assertTrue((workspace / "references" / "retrieval_index.json").is_file())

    def test_same_source_rerun_recompiles_applied_answer_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nCore concept with enough lecture prose.\n\n"
                "Quiz 1.1 Problem\nExplain the core concept in one sentence.",
                encoding="utf-8",
            )

            first_code, first = self.run_course(materials, workspace)
            self.assertEqual(10, first_code)
            self.assertEqual("blocked", first["readiness"])

            store = IngestionStore(workspace, source_root=materials)
            question = next(
                unit for unit in store.units().values()
                if unit.kind == "question" and unit.external_id
            )
            issue = next(
                issue for issue in store.review_queue.issues()
                if "missing_answer" in issue.reason_codes
            )
            answer = ContentUnit.create(
                question.source_id,
                question.source_sha256,
                question.source_file,
                "answer",
                "Recovered answer",
                question.page,
                ordinal=question.ordinal + 1,
                external_id=question.external_id,
                chapter_id=question.chapter_id,
                phase_id=question.phase_id,
                method="ai_recovered",
                confidence=0.9,
                provenance="ai_recovered",
            )
            patch = ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                [
                    {"op": "add_unit", "unit": answer.to_dict()},
                    {
                        "op": "pair_qa",
                        "question_unit_id": question.unit_id,
                        "answer_unit_id": answer.unit_id,
                    },
                ],
                list(issue.evidence),
                reviewer="test",
                created_at="2026-07-14T12:00:00Z",
                status="validated",
            )
            store.apply_patch(patch)
            compile_review_outputs(workspace)

            second_code, second = self.run_course(materials, workspace)
            self.assertEqual(0, second_code)
            self.assertIn(second["readiness"], ("ready", "usable_with_gaps"))
            bank = json.loads(
                (workspace / "references" / "quiz_bank.json").read_text(encoding="utf-8")
            )
            item = next(row for row in bank if row["id"] == question.external_id)
            self.assertEqual("Recovered answer", item["answer"])
            report = json.loads(
                (workspace / "ingest_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual([], report["missing_answer_ids"])
            self.assertEqual("applied", store.review_queue.get(issue.issue_id).status)

    def test_missing_materials_directory_fails_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing"
            workspace = root / "workspace"
            code, payload = self.run_course(missing, workspace, confirm=False)
            self.assertEqual(2, code)
            self.assertFalse(payload["process_success"])
            self.assertFalse(workspace.exists())

    def test_unconfirmed_ingestion_fails_closed_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )

            code, payload = self.run_course(materials, workspace, confirm=False)

            self.assertEqual(2, code)
            self.assertFalse(payload["process_success"])
            self.assertFalse(payload["start_gate"]["ready_to_ingest"])
            self.assertEqual("blocked", payload["steps"][0]["status"])
            self.assertFalse(workspace.exists())

    def test_runtime_receipt_drift_blocks_before_ingestion_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            home = root / ".examprep-home"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            confirmation_output = io.StringIO()
            identity = exam_start._capture_runtime_identity()
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                    mock.patch.object(
                        exam_start, "_capture_runtime_identity", return_value=identity
                    ), \
                    contextlib.redirect_stdout(confirmation_output):
                confirmation_code = exam_start.run([
                    "confirm", "--course", "test-course",
                    "--materials", str(materials), "--workspace", str(workspace),
                    "--mode", "from_scratch", "--time-budget", "le1d",
                    "--language", "en", "--json",
                ])
            self.assertEqual(0, confirmation_code, confirmation_output.getvalue())

            receipt_path = workspace / "exam_runtime_receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["python"]["executable"] = str(root / "different-python")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            code, payload = self.run_course(materials, workspace, confirm=False)
            self.assertEqual(2, code)
            self.assertFalse(payload["process_success"])
            self.assertEqual("blocked", payload["steps"][0]["status"])
            self.assertIn(
                "runtime_provenance", payload["steps"][0]["blockers"]
            )
            self.assertEqual(
                "runtime_drift", payload["start_gate"]["runtime_provenance"]["reason"]
            )
            self.assertFalse((workspace / ".ingest").exists())


if __name__ == "__main__":
    unittest.main()
