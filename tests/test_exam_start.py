# -*- coding: utf-8 -*-
"""Contracts for the executable first-contact / ingestion gate."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import exam_start
from scripts.material_generation import (
    build_pending_generation,
    material_recovery_path,
    validate_runtime_recovery_log,
)


class ExamStartTest(unittest.TestCase):
    def _run(self, home, arguments):
        output = io.StringIO()
        if arguments and arguments[0] in ("confirm", "recover-material-build"):
            # Confirm intentionally captures twice (write, then fail-closed
            # verification). Freeze one real snapshot so unrelated parallel
            # repository edits cannot make this unit test nondeterministic.
            identity = exam_start._capture_runtime_identity()
            capture = mock.patch.object(
                exam_start, "_capture_runtime_identity", return_value=identity
            )
        else:
            capture = contextlib.nullcontext()
        with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), capture, \
                contextlib.redirect_stdout(output):
            code = exam_start.run(arguments + ["--json"])
        return code, json.loads(output.getvalue())

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2
        ) + "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        return exam_start.hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _fake_package(root):
        package = root / "runtime-package"
        for directory in ("skills", "locales", "scripts", "prompts", "docs"):
            (package / directory).mkdir(parents=True, exist_ok=True)
        (package / "SKILL.md").write_text(
            "---\nname: test-exam-skill\nmetadata:\n  version: \"9.9\"\n---\n",
            encoding="utf-8",
        )
        (package / "AGENTS.md").write_text("runtime contract\n", encoding="utf-8")
        (package / "LICENSE").write_text("test runtime license\n", encoding="utf-8")
        (package / "scripts" / "entry.py").write_text("VALUE = 1\n", encoding="utf-8")
        return package

    def test_state_status_reports_transition_interaction_style_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            state = {
                "current_phase": 1,
                "mode": "from_scratch",
                "time_budget": "le1d",
                "language": "en",
                "preferences": {"interaction_style": "step_by_step"},
            }
            self._write_json(workspace / "study_state.json", state)

            historical = exam_start._load_state_status(str(workspace))
            self.assertTrue(historical["ready"])
            self.assertEqual(
                historical["interaction_style_effective"], "step_by_step")
            self.assertFalse(historical["interaction_style_dormant"])

            state["processing_mode"] = "lightweight"
            self._write_json(workspace / "study_state.json", state)
            dormant = exam_start._load_state_status(str(workspace))
            self.assertEqual(dormant["interaction_style_preference"],
                             "step_by_step")
            self.assertEqual(dormant["interaction_style_effective"], "batch")
            self.assertTrue(dormant["interaction_style_dormant"])
            self.assertEqual(dormant["interaction_style_dormant_reason"],
                             "processing_mode_not_full")

    def test_status_is_read_only_and_blocked_without_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()

            code, payload = self._run(home, [
                "status", "--materials", str(materials), "--workspace", str(workspace),
            ])

            self.assertEqual(0, code)
            self.assertFalse(payload["ready_to_ingest"])
            self.assertEqual(
                ["workspace_confirmation", "learning_choices", "runtime_provenance"],
                payload["ingestion_permission"]["blockers"],
            )
            self.assertEqual(
                "runtime_receipt_missing", payload["runtime_provenance"]["reason"]
            )
            self.assertFalse(workspace.exists())
            self.assertFalse(home.exists())

    def test_confirm_writes_only_workspace_and_leaves_installed_package_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            package = self._fake_package(root)
            before = {
                path.relative_to(package).as_posix(): path.read_bytes()
                for path in package.rglob("*") if path.is_file()
            }
            with mock.patch.object(exam_start, "PACKAGE_ROOT", str(package)):
                code, payload = self._run(home, [
                    "confirm", "--course", "course",
                    "--materials", str(materials), "--workspace", str(workspace),
                    "--mode", "from_scratch", "--time-budget", "le1d",
                    "--language", "en",
                ])
            self.assertEqual(0, code)
            after = {
                path.relative_to(package).as_posix(): path.read_bytes()
                for path in package.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual("9.9", payload["runtime_receipt"]["skill_version"])
            receipt = json.loads(
                (workspace / "exam_runtime_receipt.json").read_text(encoding="utf-8")
            )
            self.assertFalse(receipt["git"]["available"])
            self.assertIsNotNone(receipt["git"]["reason"])

    def test_confirm_rejects_a_workspace_inside_the_installed_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            materials.mkdir()
            package = self._fake_package(root)
            workspace = package / "student-workspace"
            before = sorted(path.relative_to(package).as_posix() for path in package.rglob("*"))
            with mock.patch.object(exam_start, "PACKAGE_ROOT", str(package)):
                code, payload = self._run(home, [
                    "confirm", "--course", "course",
                    "--materials", str(materials), "--workspace", str(workspace),
                    "--mode", "from_scratch", "--time-budget", "le1d",
                    "--language", "en",
                ])
            self.assertEqual(2, code)
            self.assertIn("installed runtime package", payload["error"])
            self.assertFalse(workspace.exists())
            after = sorted(path.relative_to(package).as_posix() for path in package.rglob("*"))
            self.assertEqual(before, after)

    def test_ordinary_confirm_requires_all_three_choices_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()

            code, payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
            ])

            self.assertEqual(2, code)
            self.assertEqual(["language"], payload["missing_learning_choices"])
            self.assertFalse(workspace.exists())
            self.assertFalse(home.exists())

    def test_confirm_creates_exact_receipt_and_sets_choices_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()

            code, payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "bilingual", "--artifact-mode", "visual",
            ])

            self.assertEqual(0, code)
            self.assertTrue(payload["ready_to_ingest"])
            state = json.loads((workspace / "study_state.json").read_text(encoding="utf-8"))
            self.assertEqual("from_scratch", state["mode"])
            self.assertEqual("le1d", state["time_budget"])
            self.assertEqual("bilingual", state["language"])
            self.assertEqual("visual", state["artifact_mode"])
            registry = json.loads((home / "workspaces.json").read_text(encoding="utf-8"))
            receipt = registry["workspaces"][0]["confirmation"]
            self.assertTrue(receipt["confirmed"])
            self.assertEqual(2, receipt["version"])
            self.assertEqual(str(workspace.resolve()), receipt["workspace"])
            self.assertEqual(str(materials.resolve()), receipt["materials"])
            self.assertEqual(
                os.path.normcase(str(workspace.resolve())), receipt["workspace_canonical"])
            self.assertEqual(
                os.path.normcase(str(materials.resolve())), receipt["materials_canonical"])
            self.assertFalse(receipt["urgent"])
            runtime_path = workspace / "exam_runtime_receipt.json"
            self.assertTrue(runtime_path.is_file())
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            self.assertEqual(1, runtime["schema_version"])
            self.assertEqual("exam_runtime", runtime["receipt_type"])
            self.assertEqual("4.2", runtime["skill"]["version"])
            self.assertTrue(runtime["created_at"].endswith("Z"))
            self.assertEqual(str(Path(exam_start.PACKAGE_ROOT).resolve()), runtime["package_root"])
            self.assertEqual(str(Path(os.sys.executable).resolve()), runtime["python"]["executable"])
            self.assertGreater(len(runtime["runtime_files"]), 10)
            self.assertEqual(
                runtime["runtime_digest"],
                exam_start.hashlib.sha256(
                    exam_start.canonical_json(runtime["runtime_files"]).encode("utf-8")
                ).hexdigest(),
            )
            self.assertTrue(payload["runtime_provenance"]["verified"])

            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}):
                workspace_gate = exam_start.check_registered_workspace_gate(str(workspace))
            self.assertTrue(workspace_gate["ready_to_use"])
            self.assertEqual(str(materials.resolve()), workspace_gate["materials"])

            other_materials = root / "other-materials"
            other_materials.mkdir()
            _, mismatch = self._run(home, [
                "status", "--materials", str(other_materials),
                "--workspace", str(workspace),
            ])
            self.assertFalse(mismatch["ready_to_ingest"])
            self.assertEqual(
                "materials_mismatch",
                mismatch["workspace_confirmation"]["reason"],
            )

    def test_pending_runtime_loss_requires_explicit_generation_bound_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            common = [
                "--course", "EEC160", "--materials", str(materials),
                "--workspace", str(workspace), "--mode", "from_scratch",
                "--time-budget", "le1d", "--language", "en",
            ]
            code, _payload = self._run(home, ["confirm"] + common)
            self.assertEqual(0, code)

            raw = {
                "quiz_bank": [], "teaching_examples": [],
                "ingestion": {"content_units": []},
            }
            report = {"asset_role_promotions": []}
            raw_sha = self._write_json(
                workspace / ".ingest" / "source_raw_input.json", raw
            )
            report_sha = self._write_json(
                workspace / ".ingest" / "parse_report.json", report
            )
            pending = build_pending_generation(raw_sha, report_sha, raw, report)
            self._write_json(
                workspace / ".ingest" / "material_build_pending.json", pending
            )
            (workspace / "exam_runtime_receipt.json").unlink()

            blocked_code, blocked = self._run(home, ["confirm"] + common)
            self.assertEqual(2, blocked_code, blocked)
            self.assertEqual("material_build_recovery", blocked["failed_step"])
            self.assertIn("recover-material-build", blocked["next_action"])
            self.assertFalse((workspace / "exam_runtime_receipt.json").exists())

            recovered_code, recovered = self._run(home, [
                "recover-material-build", "--materials", str(materials),
                "--workspace", str(workspace), "--action", "resume",
            ])
            self.assertEqual(0, recovered_code, recovered)
            self.assertTrue(recovered["ready_to_ingest"])
            self.assertEqual(pending["generation_id"], recovered["generation_id"])
            recovery_path = workspace.joinpath(*material_recovery_path(
                pending["generation_id"]
            ).split("/"))
            recovery_log = json.loads(recovery_path.read_text(encoding="utf-8"))
            validate_runtime_recovery_log(recovery_log)
            authorization = recovery_log["records"][-1]["authorization"]
            self.assertEqual("resume", authorization["action"])
            self.assertEqual(pending["generation_id"],
                             authorization["pending"]["generation_id"])
            self.assertEqual("missing",
                             authorization["previous_runtime_receipt"]["state"])
            self.assertTrue((workspace / "exam_runtime_receipt.json").is_file())

    def test_reconfirm_transfers_one_workspace_and_legacy_duplicates_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            workspace = root / "workspace"
            materials_a = root / "materials-a"
            materials_b = root / "materials-b"
            materials_a.mkdir()
            materials_b.mkdir()

            common = [
                "--workspace", str(workspace), "--mode", "from_scratch",
                "--time-budget", "le1d", "--language", "en",
            ]
            code_a, first = self._run(home, [
                "confirm", "--course", "course-a", "--materials", str(materials_a),
            ] + common)
            self.assertEqual(0, code_a)
            self.assertTrue(first["ready_to_ingest"])

            code_b, second = self._run(home, [
                "confirm", "--course", "course-b", "--materials", str(materials_b),
            ] + common)
            self.assertEqual(0, code_b)
            self.assertTrue(second["ready_to_ingest"])
            registry_path = home / "workspaces.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(registry["workspaces"]))
            self.assertEqual("course-b", registry["workspaces"][0]["course"])
            self.assertEqual(str(materials_b.resolve()), registry["workspaces"][0]["materials"])
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}):
                gate = exam_start.check_registered_workspace_gate(str(workspace))
            self.assertTrue(gate["ready_to_use"])
            self.assertEqual("course-b", gate["registered_course"])
            self.assertEqual(str(materials_b.resolve()), gate["materials"])

            # A legacy/corrupted registry with two owners for one canonical
            # workspace must never be resolved by "first passing row" order.
            duplicate = dict(registry["workspaces"][0])
            duplicate["course"] = "stale-course"
            duplicate["materials"] = str(materials_a.resolve())
            registry["workspaces"].append(duplicate)
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}):
                ambiguous = exam_start.check_registered_workspace_gate(str(workspace))
                exact = exam_start.update_progress.workspace_confirmation_status(
                    str(workspace), str(materials_b))
            self.assertFalse(ambiguous["ready_to_use"])
            self.assertEqual("workspace_registration_ambiguous", ambiguous["reason"])
            self.assertEqual(2, ambiguous["candidate_count"])
            self.assertFalse(exact["confirmed"])
            self.assertEqual("workspace_registration_ambiguous", exact["reason"])

    def test_deleted_materials_root_invalidates_registered_artifact_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            code, payload = self._run(home, [
                "confirm", "--course", "course", "--materials", str(materials),
                "--workspace", str(workspace), "--mode", "from_scratch",
                "--time-budget", "le1d", "--language", "en",
            ])
            self.assertEqual(0, code)
            self.assertTrue(payload["ready_to_ingest"])

            materials.rmdir()
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}):
                direct = exam_start.check_start_gate(str(workspace), str(materials))
                registered = exam_start.check_registered_workspace_gate(str(workspace))
            self.assertFalse(direct["ready_to_ingest"])
            self.assertEqual(
                "confirmation_path_missing_or_not_directory",
                direct["workspace_confirmation"]["reason"],
            )
            self.assertFalse(registered["ready_to_use"])
            self.assertEqual("registered_workspace_gate_blocked", registered["reason"])
            self.assertIn(
                "workspace_confirmation",
                registered["attempts"][0]["blockers"],
            )

    def test_missing_or_tampered_runtime_receipt_blocks_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            code, _payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en",
            ])
            self.assertEqual(0, code)
            receipt_path = workspace / "exam_runtime_receipt.json"
            original = receipt_path.read_bytes()

            receipt_path.unlink()
            _, missing = self._run(home, [
                "status", "--materials", str(materials), "--workspace", str(workspace),
            ])
            self.assertFalse(missing["ready_to_ingest"])
            self.assertEqual("runtime_receipt_missing", missing["runtime_provenance"]["reason"])
            self.assertFalse(receipt_path.exists())

            receipt_path.write_bytes(original)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["runtime_files"][0]["sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            tampered_bytes = receipt_path.read_bytes()
            _, tampered = self._run(home, [
                "status", "--materials", str(materials), "--workspace", str(workspace),
            ])
            self.assertFalse(tampered["ready_to_ingest"])
            self.assertEqual(
                "runtime_receipt_unreadable_or_invalid",
                tampered["runtime_provenance"]["reason"],
            )
            self.assertEqual(tampered_bytes, receipt_path.read_bytes())

    def test_runtime_receipt_publication_conflict_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            identity = exam_start._capture_runtime_identity()
            receipt_path = workspace / exam_start.RUNTIME_RECEIPT_NAME

            with mock.patch.object(
                    exam_start, "_capture_runtime_identity", return_value=identity):
                with exam_start.workspace_publication_lock(str(workspace)):
                    with self.assertRaises(exam_start.ConflictError):
                        exam_start._write_runtime_receipt(str(workspace))

            self.assertFalse(receipt_path.exists())

    def test_reconfirm_conflict_does_not_deadlock_or_partially_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            arguments = [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en",
            ]
            code, first = self._run(home, arguments)
            self.assertEqual(0, code)
            self.assertTrue(first["ready_to_ingest"])
            state_path = workspace / "study_state.json"
            receipt_path = workspace / exam_start.RUNTIME_RECEIPT_NAME
            registry_path = home / "workspaces.json"
            before = {
                "state": state_path.read_bytes(),
                "receipt": receipt_path.read_bytes(),
                "registry": registry_path.read_bytes(),
            }

            # update_progress owns the same publication lock.  confirm must not
            # hold an outer lock across that subprocess; under a real conflict
            # the child fails promptly and no later publication step runs.
            with exam_start.workspace_publication_lock(str(workspace)):
                conflict_code, conflict = self._run(home, arguments)

            self.assertNotEqual(0, conflict_code)
            self.assertEqual("learning_choices", conflict["failed_step"])
            self.assertIn("mutation", conflict["error"])
            self.assertEqual(before["state"], state_path.read_bytes())
            self.assertEqual(before["receipt"], receipt_path.read_bytes())
            self.assertEqual(before["registry"], registry_path.read_bytes())

    def test_runtime_identity_drift_is_bounded_and_blocks_ingestion_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            code, _payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en",
            ])
            self.assertEqual(0, code)
            current = exam_start._capture_runtime_identity()
            current["runtime_files"] = [
                {"path": "changed/%03d.py" % index, "sha256": "%064x" % index}
                for index in range(25)
            ]
            current["runtime_digest"] = exam_start.hashlib.sha256(
                exam_start.canonical_json(current["runtime_files"]).encode("utf-8")
            ).hexdigest()
            with mock.patch.object(
                    exam_start, "_capture_runtime_identity", return_value=current):
                _, drift = self._run(home, [
                    "status", "--materials", str(materials),
                    "--workspace", str(workspace),
                ])
            provenance = drift["runtime_provenance"]
            self.assertFalse(drift["ready_to_ingest"])
            self.assertEqual("runtime_drift", provenance["reason"])
            self.assertIn("runtime_provenance", drift["ingestion_permission"]["blockers"])
            self.assertLessEqual(len(provenance["changed_files"]), 20)
            self.assertTrue(provenance["changed_files_truncated"])

    def test_git_branch_or_dirty_metadata_alone_does_not_block_runtime_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            code, _payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en",
            ])
            self.assertEqual(0, code)
            receipt = json.loads(
                (workspace / "exam_runtime_receipt.json").read_text(encoding="utf-8")
            )
            current = {key: value for key, value in receipt.items()
                       if key not in ("schema_version", "receipt_type", "created_at")}
            current["git"] = dict(current["git"])
            current["git"]["dirty"] = not bool(current["git"].get("dirty"))
            current["git"]["branch"] = "renamed-audit-branch"
            with mock.patch.dict(os.environ, {"EXAMPREP_HOME": str(home)}), \
                    mock.patch.object(
                        exam_start, "_capture_runtime_identity", return_value=current):
                status = exam_start.check_start_gate(str(workspace), str(materials))
            self.assertTrue(status["ready_to_ingest"])
            self.assertEqual("verified", status["runtime_provenance"]["reason"])
            self.assertCountEqual(
                ["branch", "dirty"], status["runtime_provenance"]["git_metadata_drift"])

    def test_status_reports_registry_or_snapshot_operation_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            home.mkdir()
            (home / "workspaces.json").write_text("{broken", encoding="utf-8")
            code, payload = self._run(home, [
                "status", "--materials", str(materials), "--workspace", str(workspace),
            ])
            self.assertEqual(1, code)
            self.assertFalse(payload["process_success"])
            self.assertEqual(["registry_unreadable"], payload["operation_errors"])

    def test_confirm_rejects_link_backed_materials_when_links_are_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            real_materials = root / "real-materials"
            linked_materials = root / "linked-materials"
            workspace = root / "workspace"
            real_materials.mkdir()
            try:
                linked_materials.symlink_to(real_materials, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlink creation is unavailable on this host")
            code, payload = self._run(home, [
                "confirm", "--course", "course",
                "--materials", str(linked_materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d", "--language", "en",
            ])
            self.assertEqual(2, code)
            self.assertIn("symbolic link", payload["error"])
            self.assertFalse(workspace.exists())

    def test_link_backed_runtime_receipt_fails_closed_when_links_are_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()
            code, _payload = self._run(home, [
                "confirm", "--course", "EEC160",
                "--materials", str(materials), "--workspace", str(workspace),
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en",
            ])
            self.assertEqual(0, code)
            receipt_path = workspace / "exam_runtime_receipt.json"
            outside = root / "outside-receipt.json"
            outside.write_bytes(receipt_path.read_bytes())
            receipt_path.unlink()
            try:
                receipt_path.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this host")
            before = outside.read_bytes()
            _, payload = self._run(home, [
                "status", "--materials", str(materials), "--workspace", str(workspace),
            ])
            self.assertFalse(payload["ready_to_ingest"])
            self.assertEqual(
                "unsafe_runtime_receipt", payload["runtime_provenance"]["reason"]
            )
            self.assertEqual(before, outside.read_bytes())

    def test_urgent_defaults_only_with_explicit_flag_and_never_infers_bilingual(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            ordinary_workspace = root / "ordinary-workspace"
            urgent_workspace = root / "urgent-workspace"
            materials.mkdir()

            ordinary_code, ordinary = self._run(home, [
                "confirm", "--course", "ordinary",
                "--materials", str(materials), "--workspace", str(ordinary_workspace),
                "--language", "zh",
            ])
            self.assertEqual(2, ordinary_code)
            self.assertCountEqual(
                ["mode", "time_budget"], ordinary["missing_learning_choices"]
            )
            self.assertFalse(ordinary_workspace.exists())

            urgent_code, urgent = self._run(home, [
                "confirm", "--course", "urgent",
                "--materials", str(materials), "--workspace", str(urgent_workspace),
                "--language", "zh", "--urgent",
            ])
            self.assertEqual(0, urgent_code)
            self.assertEqual(["mode", "time_budget"], urgent["inferred_learning_choices"])
            state = json.loads(
                (urgent_workspace / "study_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("from_scratch", state["mode"])
            self.assertEqual("le1d", state["time_budget"])
            self.assertEqual("zh", state["language"])
            self.assertNotEqual("bilingual", state["language"])

    def test_urgent_without_caller_supplied_language_fails_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "registry"
            materials = root / "materials"
            workspace = root / "workspace"
            materials.mkdir()

            code, payload = self._run(home, [
                "confirm", "--course", "urgent",
                "--materials", str(materials), "--workspace", str(workspace),
                "--urgent",
            ])
            self.assertEqual(2, code)
            self.assertEqual(["language"], payload["missing_learning_choices"])
            self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
