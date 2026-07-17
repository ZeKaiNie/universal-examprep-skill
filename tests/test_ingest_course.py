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

from scripts import exam_start, ingest_course, validate_workspace
from scripts.ingestion import (
    ContentUnit, IngestionStore, ReviewPatch, atomic_write_json,
)
from scripts.ingestion.pipeline import (
    compile_review_outputs,
    refresh_build_manifest,
    verify_material_build_receipt,
)
from scripts.material_generation import (
    build_pending_generation,
    material_recovery_path,
    validate_runtime_recovery_log,
)

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

    def recover_material_build(self, materials, workspace, action):
        output = io.StringIO()
        home = workspace.parent / ".examprep-home"
        identity = exam_start._capture_runtime_identity()
        with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                mock.patch.object(
                    exam_start, "_capture_runtime_identity", return_value=identity
                ), contextlib.redirect_stdout(output):
            code = exam_start.run([
                "recover-material-build", "--materials", str(materials),
                "--workspace", str(workspace), "--action", action, "--json",
            ])
        return code, json.loads(output.getvalue())

    @staticmethod
    def publish_pending_from_current(workspace):
        ingest = workspace / ".ingest"
        raw_path = ingest / "source_raw_input.json"
        report_path = ingest / "parse_report.json"
        manifest_path = ingest / "build_manifest.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        pending = build_pending_generation(
            hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            hashlib.sha256(report_path.read_bytes()).hexdigest(),
            raw,
            report,
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )
        atomic_write_json(ingest / "material_build_pending.json", pending)
        return pending

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
            self.assertFalse(
                (workspace / ".ingest" / "material_build_pending.json").exists()
            )
            receipt_path = workspace / ".ingest" / "material_build_receipt.json"
            self.assertTrue(receipt_path.is_file())
            manifest = json.loads(
                (workspace / ".ingest" / "build_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(2, manifest["schema_version"])
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8"))[
                    "generation_id"
                ],
                manifest["material_build"]["generation_id"],
            )
            for name, relative in (
                    ("source_raw_input", ".ingest/source_raw_input.json"),
                    ("parse_report", ".ingest/parse_report.json"),
                    ("material_build_receipt",
                     ".ingest/material_build_receipt.json")):
                row = manifest["artifacts"][name]
                self.assertEqual(relative, row["path"])
                self.assertEqual(
                    hashlib.sha256(
                        workspace.joinpath(*relative.split("/")).read_bytes()
                    ).hexdigest(),
                    row["sha256"],
                )

    def test_runtime_recovery_resumes_exact_generation_without_builder_and_binds_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nCore concept\nA detailed source-backed explanation.",
                encoding="utf-8",
            )
            first_code, first_payload = self.run_course(materials, workspace)
            self.assertEqual(0, first_code, first_payload)

            ingest = workspace / ".ingest"
            raw_path = ingest / "source_raw_input.json"
            report_path = ingest / "parse_report.json"
            manifest_path = ingest / "build_manifest.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            pending = build_pending_generation(
                hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
                raw,
                report,
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            )
            atomic_write_json(ingest / "material_build_pending.json", pending)
            (workspace / "exam_runtime_receipt.json").unlink()

            home = root / ".examprep-home"
            identity = exam_start._capture_runtime_identity()
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                    mock.patch.object(
                        exam_start, "_capture_runtime_identity", return_value=identity
                    ), contextlib.redirect_stdout(output):
                recovered = exam_start.run([
                    "recover-material-build", "--materials", str(materials),
                    "--workspace", str(workspace), "--action", "resume", "--json",
                ])
            self.assertEqual(0, recovered, output.getvalue())

            with mock.patch.object(
                    material_builder, "run",
                    side_effect=AssertionError("resume must not invoke material builder")):
                code, payload = self.run_course(materials, workspace, confirm=False)
            self.assertEqual(0, code, payload)
            self.assertEqual("resumed", next(
                row["status"] for row in payload["steps"]
                if row["name"] == "material_build"
            ))
            recovery_relative = material_recovery_path(pending["generation_id"])
            recovery_path = workspace.joinpath(*recovery_relative.split("/"))
            recovery_log = json.loads(recovery_path.read_text(encoding="utf-8"))
            validate_runtime_recovery_log(recovery_log)
            outcome = recovery_log["records"][-1]["outcome"]
            self.assertEqual("completed", outcome["status"])
            receipt_path = ingest / "material_build_receipt.json"
            self.assertEqual(
                hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                outcome["material_build_receipt_sha256"],
            )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual([{
                "path": recovery_relative,
                "generation_id": pending["generation_id"],
                "outcome": "completed",
                "replacement_generation_id": None,
            }], receipt["completion"]["recovery_logs"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact = manifest["artifacts"][
                "material_build_recovery:%s" % pending["generation_id"]
            ]
            self.assertEqual(recovery_relative, artifact["path"])
            self.assertEqual(
                hashlib.sha256(recovery_path.read_bytes()).hexdigest(),
                artifact["sha256"],
            )

            original_manifest = manifest_path.read_bytes()
            manifest["artifacts"][
                "material_build_recovery:%s" % ("f" * 64)
            ] = dict(artifact)
            atomic_write_json(manifest_path, manifest)
            errors, _warnings, _stats = validate_workspace.validate(str(workspace))
            self.assertIn(
                "material build generation receipt is invalid",
                " | ".join(row["msg"] for row in errors),
            )
            manifest_path.write_bytes(original_manifest)

            recovery_path.unlink()
            errors, _warnings, _stats = validate_workspace.validate(str(workspace))
            self.assertIn(
                "material build generation receipt is invalid",
                " | ".join(row["msg"] for row in errors),
            )

    def test_incomplete_pending_resume_rebuilds_only_the_exact_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nCore concept\nA detailed source-backed explanation.",
                encoding="utf-8",
            )
            first_code, first_payload = self.run_course(materials, workspace)
            self.assertEqual(0, first_code, first_payload)
            pending = self.publish_pending_from_current(workspace)
            (workspace / ".ingest" / "source_raw_input.json").unlink()
            (workspace / "exam_runtime_receipt.json").unlink()

            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "resume"
            )
            self.assertEqual(0, recovered, recovery_payload)
            self.assertTrue(recovery_payload["interrupted_successor"])
            with mock.patch.object(
                    material_builder, "run", wraps=material_builder.run) as rebuilt:
                code, payload = self.run_course(
                    materials, workspace, confirm=False
                )
            self.assertEqual(0, code, payload)
            rebuilt.assert_called_once()
            receipt = json.loads((
                workspace / ".ingest" / "material_build_receipt.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(pending["generation_id"], receipt["generation_id"])
            recovery_log = json.loads(workspace.joinpath(
                *material_recovery_path(pending["generation_id"]).split("/")
            ).read_text(encoding="utf-8"))
            self.assertEqual(
                "completed", recovery_log["records"][-1]["outcome"]["status"]
            )

    def test_two_interrupted_supersedes_close_every_direct_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            lecture = materials / "ch01_lecture.txt"
            lecture.write_text(
                "Chapter 1\nGeneration zero source-backed explanation.",
                encoding="utf-8",
            )
            first_code, first_payload = self.run_course(materials, workspace)
            self.assertEqual(0, first_code, first_payload)
            first_pending = self.publish_pending_from_current(workspace)
            lecture.write_text(
                "Chapter 1\nGeneration one changed source-backed explanation.",
                encoding="utf-8",
            )
            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "supersede"
            )
            self.assertEqual(0, recovered, recovery_payload)

            real_run = ingest_course._run

            def stop_first_compiler(command):
                if os.path.basename(command[1]) == "check_deps.py":
                    return real_run(command)
                if os.path.basename(command[1]) == "ingest.py":
                    return SimpleNamespace(
                        returncode=23, stdout="", stderr="injected compiler stop"
                    )
                raise AssertionError("unexpected subprocess: %r" % (command,))

            output = io.StringIO()
            home = root / ".examprep-home"
            identity = exam_start._capture_runtime_identity()
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                    mock.patch.object(
                        exam_start, "_capture_runtime_identity",
                        return_value=identity,
                    ), mock.patch.object(
                        ingest_course, "_run", side_effect=stop_first_compiler
                    ), contextlib.redirect_stdout(output):
                stopped = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never", "--visual-index", "never",
                    "--json",
                ])
            self.assertEqual(23, stopped, output.getvalue())
            second_pending = json.loads((
                workspace / ".ingest" / "material_build_pending.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(
                first_pending["generation_id"],
                second_pending["supersedes_generation_id"],
            )
            first_log = json.loads(workspace.joinpath(
                *material_recovery_path(first_pending["generation_id"]).split("/")
            ).read_text(encoding="utf-8"))
            self.assertEqual(
                second_pending["generation_id"],
                first_log["records"][-1]["outcome"]["replacement_generation_id"],
            )

            lecture.write_text(
                "Chapter 1\nGeneration two changed source-backed explanation.",
                encoding="utf-8",
            )
            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "supersede"
            )
            self.assertEqual(0, recovered, recovery_payload)
            code, payload = self.run_course(materials, workspace, confirm=False)
            self.assertEqual(0, code, payload)
            receipt = json.loads((
                workspace / ".ingest" / "material_build_receipt.json"
            ).read_text(encoding="utf-8"))
            third_generation = receipt["generation_id"]
            self.assertEqual(
                second_pending["generation_id"],
                receipt["supersedes_generation_id"],
            )
            rows = {
                row["generation_id"]: row
                for row in receipt["completion"]["recovery_logs"]
            }
            self.assertEqual(
                second_pending["generation_id"],
                rows[first_pending["generation_id"]]["replacement_generation_id"],
            )
            self.assertEqual(
                third_generation,
                rows[second_pending["generation_id"]]["replacement_generation_id"],
            )
            self.assertEqual(
                {"abandoned"}, {row["outcome"] for row in rows.values()}
            )

    def test_blocker_first_incomplete_successor_requires_explicit_supersede(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            lecture = materials / "ch01_lecture.txt"
            lecture.write_text(
                "Chapter 1\nOriginal source-backed explanation.",
                encoding="utf-8",
            )
            first_code, first_payload = self.run_course(materials, workspace)
            self.assertEqual(0, first_code, first_payload)
            first_pending = self.publish_pending_from_current(workspace)
            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "supersede"
            )
            self.assertEqual(0, recovered, recovery_payload)

            ingest = workspace / ".ingest"
            manifest_path = ingest / "build_manifest.json"
            fake_raw = {
                "course_name": "Interrupted successor",
                "phases": [],
                "quiz_bank": [],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }
            fake_report = {
                "asset_role_promotions": [], "warnings": [], "ai_review": []
            }
            second_pending = build_pending_generation(
                hashlib.sha256(
                    material_builder._publication_json_bytes(fake_raw)
                ).hexdigest(),
                hashlib.sha256(
                    material_builder._publication_json_bytes(fake_report)
                ).hexdigest(),
                fake_raw,
                fake_report,
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                supersedes_generation_id=first_pending["generation_id"],
            )
            # Simulate a blocker-first crash: only the successor marker became
            # public.  Its sources are absent and the predecessor log is active.
            atomic_write_json(
                ingest / "material_build_pending.json", second_pending
            )
            first_recovery_path = workspace.joinpath(*material_recovery_path(
                first_pending["generation_id"]
            ).split("/"))
            first_log = json.loads(first_recovery_path.read_text(encoding="utf-8"))
            self.assertIsNone(first_log["records"][-1]["outcome"])

            # Publish a structurally valid receipt for a different runtime so
            # the ordinary start gate reports true runtime drift, not corruption.
            current_identity = exam_start._capture_runtime_identity()
            old_identity = json.loads(json.dumps(current_identity))
            old_identity["runtime_files"][0]["sha256"] = "0" * 64
            old_identity["runtime_digest"] = hashlib.sha256(
                exam_start.canonical_json(
                    old_identity["runtime_files"]
                ).encode("utf-8")
            ).hexdigest()
            atomic_write_json(
                workspace / exam_start.RUNTIME_RECEIPT_NAME,
                exam_start._build_runtime_receipt(old_identity),
            )
            blocked_code, blocked = self.run_course(
                materials, workspace, confirm=False
            )
            self.assertEqual(2, blocked_code, blocked)
            self.assertEqual(
                "runtime_drift",
                blocked["start_gate"]["runtime_provenance"]["reason"],
            )

            lecture.write_text(
                "Chapter 1\nBuilder now produces a different generation.",
                encoding="utf-8",
            )
            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "resume"
            )
            self.assertEqual(0, recovered, recovery_payload)
            second_recovery_path = workspace.joinpath(*material_recovery_path(
                second_pending["generation_id"]
            ).split("/"))
            protected_paths = {
                "pending": ingest / "material_build_pending.json",
                "raw": ingest / "source_raw_input.json",
                "report": ingest / "parse_report.json",
                "manifest": manifest_path,
                "first_recovery": first_recovery_path,
                "second_recovery": second_recovery_path,
            }
            before_mismatch = {
                name: path.read_bytes() for name, path in protected_paths.items()
            }
            mismatch_code, mismatch = self.run_course(
                materials, workspace, confirm=False
            )
            self.assertEqual(2, mismatch_code, mismatch)
            self.assertIn("explicit supersede", mismatch["error"])
            self.assertEqual(before_mismatch, {
                name: path.read_bytes() for name, path in protected_paths.items()
            })

            recovered, recovery_payload = self.recover_material_build(
                materials, workspace, "supersede"
            )
            self.assertEqual(0, recovered, recovery_payload)
            final_code, final_payload = self.run_course(
                materials, workspace, confirm=False
            )
            self.assertEqual(0, final_code, final_payload)
            self.assertEqual("ready", final_payload["readiness"])
            receipt_path = ingest / "material_build_receipt.json"
            receipt_bytes = receipt_path.read_bytes()
            receipt = json.loads(receipt_bytes.decode("utf-8"))
            third_generation = receipt["generation_id"]
            self.assertEqual(
                second_pending["generation_id"],
                receipt["supersedes_generation_id"],
            )
            rows = {
                row["generation_id"]: row
                for row in receipt["completion"]["recovery_logs"]
            }
            self.assertEqual(
                second_pending["generation_id"],
                rows[first_pending["generation_id"]]["replacement_generation_id"],
            )
            self.assertEqual(
                third_generation,
                rows[second_pending["generation_id"]]["replacement_generation_id"],
            )
            first_log = json.loads(first_recovery_path.read_text(encoding="utf-8"))
            second_log = json.loads(second_recovery_path.read_text(encoding="utf-8"))
            self.assertEqual(
                second_pending["generation_id"],
                first_log["records"][-1]["outcome"]["replacement_generation_id"],
            )
            self.assertEqual(
                third_generation,
                second_log["records"][-1]["outcome"]["replacement_generation_id"],
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for generation_id, path in (
                    (first_pending["generation_id"], first_recovery_path),
                    (second_pending["generation_id"], second_recovery_path)):
                artifact = manifest["artifacts"][
                    "material_build_recovery:%s" % generation_id
                ]
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    artifact["sha256"],
                )
            errors, _warnings, _stats = validate_workspace.validate(str(workspace))
            self.assertEqual([], errors)

            tampered = json.loads(receipt_bytes.decode("utf-8"))
            tampered_rows = {
                row["generation_id"]: row
                for row in tampered["completion"]["recovery_logs"]
            }
            tampered_rows[first_pending["generation_id"]][
                "replacement_generation_id"
            ] = third_generation
            atomic_write_json(receipt_path, tampered)
            with self.assertRaisesRegex(
                    ValueError, "supersede abandonment audit is stale"):
                verify_material_build_receipt(
                    workspace, require_manifest_binding=False, required=True
                )
            receipt_path.write_bytes(receipt_bytes)

    def test_pending_ingest_is_rolled_back_before_generation_routing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            ingest = workspace / ".ingest"
            materials.mkdir()
            ingest.mkdir(parents=True)
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            raw = {
                "course_name": "Crash recovery",
                "phases": [],
                "quiz_bank": [],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }
            report = {"warnings": [], "ai_review": []}
            raw_path = ingest / "source_raw_input.json"
            report_path = ingest / "parse_report.json"
            pending_path = ingest / "material_build_pending.json"
            atomic_write_json(raw_path, raw)
            atomic_write_json(report_path, report)
            pending = build_pending_generation(
                hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
                raw, report, None,
            )
            atomic_write_json(pending_path, pending)
            original_pending = pending_path.read_bytes()

            transaction_dir = ingest / "transactions" / "ingest-crash"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "000000.bak"
            backup.write_bytes(original_pending)
            atomic_write_json(ingest / "pending_ingest.json", {
                "schema_version": 1,
                "transaction_dir": ".ingest/transactions/ingest-crash",
                "targets": [{
                    "path": ".ingest/material_build_pending.json",
                    "backup": ".ingest/transactions/ingest-crash/000000.bak",
                }],
            })
            pending_path.write_text("{broken", encoding="utf-8")

            ready = self._start_gate(True)
            subprocess_results = (
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=23, stdout="", stderr="stop after route"),
            )
            output = io.StringIO()
            with mock.patch.object(
                    ingest_course.exam_start, "check_start_gate",
                    return_value=ready), mock.patch.object(
                    ingest_course, "_run", side_effect=subprocess_results
                    ), mock.patch.object(
                    material_builder, "run",
                    side_effect=AssertionError("restored exact resume skips builder")
                    ), contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never", "--visual-index", "never",
                    "--json",
                ])
            self.assertEqual(23, code, output.getvalue())
            self.assertEqual(original_pending, pending_path.read_bytes())
            self.assertFalse((ingest / "pending_ingest.json").exists())
            self.assertFalse(transaction_dir.exists())
            payload = json.loads(output.getvalue())
            self.assertEqual(
                pending["generation_id"],
                payload["material_recovery"]["generation_id"],
            )

    def test_material_receipt_tamper_cannot_be_blessed_by_manifest_refresh(self):
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
            self.assertEqual(0, code, payload)
            report_path = workspace / ".ingest" / "parse_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.setdefault("warnings", []).append("tampered after receipt")
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            refresh_build_manifest(workspace)
            errors, _warnings, _stats = validate_workspace.validate(str(workspace))

            self.assertEqual(2, validate_workspace._exit_code(errors))
            self.assertIn(
                "material build generation receipt is invalid",
                " | ".join(row["msg"] for row in errors),
            )

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
            workspace.mkdir()
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
            workspace.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)
            plan = {"sentinel": "asset-plan"}
            raw_input = {
                "phases": [],
                "quiz_bank": [],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }
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
                    str(workspace / ".ingest" / "material_build_pending.json"),
                    str(workspace / ".ingest" / "ai_review_manifest.json"),
                ],
                [path for path, _value in publications],
            )
            self.assertIs(report, publications[0][1])
            self.assertIs(raw_input, publications[1][1])
            self.assertEqual([plan], publish.call_args.kwargs["asset_plans"])
            self.assertEqual(
                (str(workspace / ".ingest" / "material_build_pending.json"),),
                publish.call_args.kwargs["blocker_paths"],
            )

    def test_material_build_no_publish_failure_preserves_last_good_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            ingest = workspace / ".ingest"
            materials.mkdir()
            ingest.mkdir(parents=True)
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            report_path = ingest / "parse_report.json"
            raw_path = ingest / "source_raw_input.json"
            manifest_path = ingest / "ai_review_manifest.json"
            report_path.write_bytes(material_builder._publication_json_bytes({
                "generation": "last-good",
                "warnings": [],
                "ai_review": [],
            }))
            raw_path.write_bytes(material_builder._publication_json_bytes({
                "generation": "last-good",
                "phases": [],
                "quiz_bank": [],
            }))
            manifest_path.write_bytes(material_builder._publication_json_bytes({
                "generation": "last-good",
                "entries": [],
            }))
            original_bytes = {
                path: path.read_bytes()
                for path in (report_path, raw_path, manifest_path)
            }
            ready = self._start_gate(True)
            failure_report = {
                "warnings": ["asset_publication_rejected: role drift"],
                "ai_review": [],
                "_no_publish_on_failure": True,
            }
            real_publish = material_builder._publish_builder_transaction

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
                    ), \
                    mock.patch.object(
                        material_builder,
                        "run",
                        return_value=(
                            5,
                            {"error": "asset publication rejected before workspace mutation"},
                            failure_report,
                        ),
                    ), \
                    mock.patch.object(
                        material_builder,
                        "_publish_builder_transaction",
                        wraps=real_publish,
                    ) as publish, \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(5, code)
            publish.assert_not_called()
            for path, expected in original_bytes.items():
                self.assertEqual(expected, path.read_bytes(), str(path))
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["process_success"])
            self.assertIn("before workspace mutation", payload["error"])

    def test_material_build_ordinary_failure_preserves_last_good_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            ingest = workspace / ".ingest"
            materials.mkdir()
            ingest.mkdir(parents=True)
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            report_path = ingest / "parse_report.json"
            report_path.write_bytes(material_builder._publication_json_bytes({
                "generation": "last-good",
                "warnings": [],
                "ai_review": [],
            }))
            ready = self._start_gate(True)
            failure_report = {
                "warnings": ["no_material_files"],
                "ai_review": [],
            }
            real_publish = material_builder._publish_builder_transaction

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
                    ), \
                    mock.patch.object(
                        material_builder,
                        "run",
                        return_value=(
                            4,
                            {"error": "ordinary material build failure"},
                            failure_report,
                        ),
                    ), \
                    mock.patch.object(
                        material_builder,
                        "_publish_builder_transaction",
                        wraps=real_publish,
                    ) as publish, \
                    contextlib.redirect_stdout(output):
                code = ingest_course.run([
                    "--materials", str(materials),
                    "--workspace", str(workspace),
                    "--render-pages", "never",
                    "--visual-index", "never",
                    "--json",
                ])

            self.assertEqual(4, code)
            publish.assert_not_called()
            self.assertEqual({
                "generation": "last-good",
                "warnings": [],
                "ai_review": [],
            }, json.loads(report_path.read_text(
                encoding="utf-8"
            )))
            self.assertFalse((ingest / "source_raw_input.json").exists())
            self.assertFalse((ingest / "ai_review_manifest.json").exists())
            payload = json.loads(output.getvalue())
            self.assertIn("ordinary material build failure", payload["error"])

    def test_material_build_rejects_non_boolean_no_publish_sentinel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            ingest = workspace / ".ingest"
            materials.mkdir()
            ingest.mkdir(parents=True)
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            report_path = ingest / "parse_report.json"
            original = material_builder._publication_json_bytes({
                "generation": "last-good",
                "warnings": [],
                "ai_review": [],
            })
            report_path.write_bytes(original)
            ready = self._start_gate(True)

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
                    ), \
                    mock.patch.object(
                        material_builder,
                        "run",
                        return_value=(
                            5,
                            {"error": "builder failure"},
                            {
                                "warnings": [],
                                "ai_review": [],
                                "_no_publish_on_failure": "true",
                            },
                        ),
                    ), \
                    mock.patch.object(
                        material_builder,
                        "_publish_builder_transaction",
                        wraps=material_builder._publish_builder_transaction,
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
            publish.assert_not_called()
            self.assertEqual(original, report_path.read_bytes())
            payload = json.loads(output.getvalue())
            self.assertEqual(
                "publication_failed", payload["steps"][-1]["operation_error"]
            )
            self.assertIn("non-boolean", payload["error"])

    def test_material_build_success_with_no_publish_sentinel_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            ingest = workspace / ".ingest"
            materials.mkdir()
            ingest.mkdir(parents=True)
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            report_path = ingest / "parse_report.json"
            original = material_builder._publication_json_bytes({
                "generation": "last-good",
                "warnings": [],
                "ai_review": [],
            })
            report_path.write_bytes(original)
            ready = self._start_gate(True)

            def contradictory_builder(_args, **kwargs):
                kwargs["_deferred_asset_plans"].append({"sentinel": "asset-plan"})
                return (
                    0,
                    {"phases": [], "quiz_bank": []},
                    {
                        "warnings": [],
                        "ai_review": [],
                        "_no_publish_on_failure": True,
                    },
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
                        material_builder,
                        "run",
                        side_effect=contradictory_builder,
                    ), \
                    mock.patch.object(
                        material_builder,
                        "_publish_builder_transaction",
                        wraps=material_builder._publish_builder_transaction,
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
            self.assertEqual(1, subprocess_run.call_count)
            publish.assert_not_called()
            self.assertEqual(original, report_path.read_bytes())
            self.assertFalse((ingest / "source_raw_input.json").exists())
            payload = json.loads(output.getvalue())
            self.assertIn("returned success", payload["error"])

    def test_interlock_raw_replacement_is_rejected_by_child_generation_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            workspace.mkdir()
            (materials / "ch01_lecture.txt").write_text(
                "Chapter 1\nA source-backed explanation.", encoding="utf-8"
            )
            ready = self._start_gate(True)
            raw_input = {
                "course_name": "Generation A",
                "phases": [],
                "quiz_bank": [],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }
            replacement_input = {
                "course_name": "Generation B",
                "phases": [],
                "quiz_bank": [],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
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
            workspace.mkdir()
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
                    {
                        "phases": [], "quiz_bank": [],
                        "teaching_examples": [],
                        "ingestion": {"content_units": []},
                    },
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
