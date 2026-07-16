# -*- coding: utf-8 -*-
"""End-to-end contracts for the official ingestion orchestrator."""

import contextlib
import hashlib
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

try:
    import build_raw_input_from_workspace as material_builder
except ImportError:
    from scripts import build_raw_input_from_workspace as material_builder


class IngestCourseTest(unittest.TestCase):
    @staticmethod
    def _start_gate(ready=True):
        return {
            "ready_to_ingest": bool(ready),
            "runtime_provenance": {
                "reason": "verified" if ready else "runtime_drift",
            },
            "ingestion_permission": {
                "allowed": bool(ready),
                "blockers": [] if ready else ["runtime_provenance"],
            },
        }

    def test_optional_adapter_is_hidden_and_rejected_without_host_injection(self):
        help_output = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, \
                contextlib.redirect_stdout(help_output):
            ingest_course.run(["--help"])
        self.assertEqual(0, stopped.exception.code)
        self.assertNotIn("--ingest-adapter", help_output.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            materials = Path(temp) / "materials"
            workspace = Path(temp) / "workspace"
            materials.mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials), "--workspace", str(workspace),
                    "--ingest-adapter", "docling", "--json",
                ])
            self.assertEqual(2, code)
            self.assertIn("host-injected adapter_runner", json.loads(output.getvalue())["error"])

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

    def test_material_build_assets_stay_deferred_when_publish_gate_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)
            drifted = self._start_gate(False)
            captured_plans = []

            def fake_builder_run(_args, **kwargs):
                self.assertTrue(kwargs["_publication_locked"])
                plans = kwargs["_deferred_asset_plans"]
                plans.append({"sentinel": "asset-plan"})
                captured_plans.append(plans)
                return (
                    0,
                    {"phases": [], "quiz_bank": []},
                    {"warnings": [], "ai_review": []},
                )

            output = io.StringIO()
            with mock.patch.object(
                    ingest_course.exam_start,
                    "check_start_gate",
                    side_effect=(ready, ready, ready, drifted)), \
                    mock.patch.object(
                        ingest_course,
                        "_run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout="", stderr=""
                        ),
                    ), \
                    mock.patch.object(
                        material_builder, "run", side_effect=fake_builder_run
                    ), \
                    mock.patch.object(
                        material_builder, "_publish_builder_transaction"
                    ) as publish, \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(2, code)
            self.assertEqual([[{"sentinel": "asset-plan"}]], captured_plans)
            publish.assert_not_called()
            payload = json.loads(output.getvalue())
            self.assertIn("material_build_publish", payload["error"])
            self.assertFalse(
                (workspace / ".ingest" / "source_raw_input.json").exists()
            )
            self.assertFalse((workspace / "references" / "assets").exists())

    def test_material_build_publishes_json_and_assets_in_one_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)
            plan = {"sentinel": "asset-plan"}
            raw_input = {"phases": [], "quiz_bank": []}
            report = {"warnings": [], "ai_review": []}

            def fake_builder_run(_args, **kwargs):
                self.assertTrue(kwargs["_publication_locked"])
                self.assertEqual(str(workspace), kwargs["publication_workspace"])
                kwargs["_deferred_asset_plans"].append(plan)
                return 0, raw_input, report

            subprocess_results = (
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(
                    returncode=23, stdout="", stderr="stop after publication"
                ),
            )
            output = io.StringIO()
            with mock.patch.object(
                    ingest_course.exam_start,
                    "check_start_gate",
                    return_value=ready), \
                    mock.patch.object(
                        ingest_course, "_run", side_effect=subprocess_results
                    ), \
                    mock.patch.object(
                        material_builder, "run", side_effect=fake_builder_run
                    ), \
                    mock.patch.object(
                        material_builder, "_publish_builder_transaction"
                    ) as publish, \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(23, code)
            publish.assert_called_once()
            publications = publish.call_args.args[0]
            self.assertEqual(
                [
                    str(workspace / ".ingest" / "parse_report.json"),
                    str(workspace / ".ingest" / "source_raw_input.json"),
                    str(workspace / ".ingest" / "ai_review_manifest.json"),
                ],
                [path for path, _value in publications],
            )
            self.assertIs(report, publications[0][1])
            self.assertIs(raw_input, publications[1][1])
            self.assertEqual([plan], publish.call_args.kwargs["asset_plans"])

    def test_interlock_raw_replacement_is_rejected_by_child_generation_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)
            raw_input = {
                "course_name": "Generation A",
                "phases": [],
                "quiz_bank": [],
            }
            replacement_input = {
                "course_name": "Generation B",
                "phases": [],
                "quiz_bank": [],
            }
            report = {"warnings": [], "ai_review": []}
            expected_sha256 = hashlib.sha256(
                material_builder._publication_json_bytes(raw_input)
            ).hexdigest()
            real_run = ingest_course._run
            observed_ingest_commands = []

            def fake_builder_run(_args, **kwargs):
                self.assertTrue(kwargs["_publication_locked"])
                return 0, raw_input, report

            def run_with_interlock_replacement(command):
                if os.path.basename(command[1]) == "check_deps.py":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if os.path.basename(command[1]) != "ingest.py":
                    raise AssertionError("unexpected subprocess: %r" % (command,))
                observed_ingest_commands.append(command)
                expected_index = command.index("--expected-input-sha256") + 1
                self.assertEqual(expected_sha256, command[expected_index])
                raw_path = workspace / ".ingest" / "source_raw_input.json"
                replacement = Path(str(raw_path) + ".next")
                replacement.write_bytes(
                    material_builder._publication_json_bytes(replacement_input)
                )
                os.replace(str(replacement), str(raw_path))
                return real_run(command)

            output = io.StringIO()
            with mock.patch.object(
                    ingest_course.exam_start,
                    "check_start_gate",
                    return_value=ready), \
                    mock.patch.object(
                        ingest_course, "_run",
                        side_effect=run_with_interlock_replacement,
                    ), \
                    mock.patch.object(
                        material_builder, "run", side_effect=fake_builder_run
                    ), \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(1, code)
            self.assertEqual(1, len(observed_ingest_commands))
            payload = json.loads(output.getvalue())
            self.assertIn("input generation drifted before compilation", payload["error"])
            self.assertFalse((workspace / "references" / "wiki").exists())

    def test_material_build_transaction_failure_is_structured(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)

            def fake_builder_run(_args, **kwargs):
                kwargs["_deferred_asset_plans"].append(
                    {"sentinel": "asset-plan"}
                )
                return (
                    0,
                    {"phases": [], "quiz_bank": []},
                    {"warnings": [], "ai_review": []},
                )

            output = io.StringIO()
            with mock.patch.object(
                    ingest_course.exam_start,
                    "check_start_gate",
                    return_value=ready), \
                    mock.patch.object(
                        ingest_course,
                        "_run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout="", stderr=""
                        ),
                    ) as subprocess_run, \
                    mock.patch.object(
                        material_builder, "run", side_effect=fake_builder_run
                    ), \
                    mock.patch.object(
                        material_builder,
                        "_publish_builder_transaction",
                        side_effect=OSError("injected late publication failure"),
                    ), \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(2, code)
            self.assertEqual(1, subprocess_run.call_count)
            payload = json.loads(output.getvalue())
            self.assertEqual(
                "publication_failed",
                payload["steps"][-1]["operation_error"],
            )
            self.assertIn("injected late publication failure", payload["error"])

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

    def test_publication_conflict_stops_before_any_ingestion_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            home = root / ".examprep-home"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            identity = exam_start._capture_runtime_identity()
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                    mock.patch.object(
                        exam_start, "_capture_runtime_identity", return_value=identity
                    ):
                confirmation_output = io.StringIO()
                with contextlib.redirect_stdout(confirmation_output):
                    confirmation_code = exam_start.run([
                        "confirm", "--course", "test-course",
                        "--materials", str(materials),
                        "--workspace", str(workspace),
                        "--mode", "from_scratch", "--time-budget", "le1d",
                        "--language", "en", "--json",
                    ])
                self.assertEqual(0, confirmation_code, confirmation_output.getvalue())
                state_before = (workspace / "study_state.json").read_bytes()
                receipt_before = (
                    workspace / exam_start.RUNTIME_RECEIPT_NAME
                ).read_bytes()

                output = io.StringIO()
                with ingest_course.workspace_publication_lock(str(workspace)), \
                        contextlib.redirect_stdout(output):
                    code = ingest_course.run([
                        "--materials", str(materials),
                        "--workspace", str(workspace),
                        "--render-pages", "never",
                        "--visual-index", "never",
                        "--json",
                    ])

            payload = json.loads(output.getvalue())
            self.assertEqual(1, code)
            self.assertEqual("publication_conflict", payload["steps"][-1]["operation_error"])
            self.assertFalse((workspace / ".ingest").exists())
            self.assertFalse((workspace / "references").exists())
            self.assertEqual(state_before, (workspace / "study_state.json").read_bytes())
            self.assertEqual(
                receipt_before,
                (workspace / exam_start.RUNTIME_RECEIPT_NAME).read_bytes(),
            )

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
