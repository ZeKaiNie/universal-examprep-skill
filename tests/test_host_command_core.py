import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from scripts.host_adapters import command_core
from scripts import validate_workspace as workspace_validator
from scripts.ingestion import (
    ConflictError, workspace_publication_lock, workspace_state_lock,
)


class FakeRunner:
    def __init__(self, payload, returncode=0, raw_stdout=None, stderr=""):
        self.payload = payload
        self.returncode = returncode
        self.raw_stdout = raw_stdout
        self.stderr = stderr
        self.argv = None
        self.kwargs = None

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = dict(kwargs)
        stdout = self.raw_stdout
        if stdout is None:
            stdout = json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(
            returncode=self.returncode, stdout=stdout, stderr=self.stderr)


def status_payload(ready=False, success=True):
    return {"process_success": success, "ready_to_ingest": ready}


def validator_payload(workspace, chapter=1, readiness="ready"):
    snapshot = command_core.dependency_snapshot_receipt(
        command_core.collect_dependency_snapshot(workspace))
    return {
        "exit_code": 0,
        "readiness": readiness,
        "capabilities": {"chapter": chapter},
        "warning_count": 0,
        "warning_summary": {},
        "warnings": [],
        "error_count": 0,
        "error_summary": {},
        "errors": [],
        "truncated": {"errors": 0, "warnings": 0},
        "dependency_snapshot": snapshot,
    }


class CommandCoreTest(unittest.TestCase):
    def setUp(self):
        self.workspace = os.path.abspath(tempfile.mkdtemp(prefix="host_ws_"))
        self.materials = os.path.abspath(tempfile.mkdtemp(prefix="host_mat_"))

    def test_unknown_command_rejected_before_runner(self):
        with self.assertRaisesRegex(command_core.CommandCoreError, "not allowlisted"):
            command_core.run_json_command("shell", ["anything"])

    def test_status_exit_zero_can_still_be_blocked(self):
        runner = FakeRunner(status_payload(ready=False))
        receipt = command_core.exam_start_status(
            self.workspace, self.materials, runner=runner)
        self.assertEqual(0, receipt["exit_code"])
        self.assertFalse(receipt["payload"]["ready_to_ingest"])
        self.assertEqual(command_core.SCHEMA_VERSION, receipt["schema_version"])

    def test_ingest_exit_ten_is_successful_blocked_readiness(self):
        runner = FakeRunner(
            {"process_success": True, "readiness": "blocked"}, returncode=10)
        receipt = command_core.ingest_course(
            self.workspace, self.materials, runner=runner)
        self.assertEqual(10, receipt["exit_code"])
        self.assertTrue(receipt["payload"]["process_success"])

    def test_ingest_exit_ten_payload_mismatch_rejected(self):
        runner = FakeRunner(
            {"process_success": False, "readiness": "blocked"}, returncode=10)
        with self.assertRaisesRegex(command_core.CommandCoreError, "exit 10"):
            command_core.ingest_course(self.workspace, self.materials, runner=runner)

    def test_validator_process_and_payload_exit_must_match(self):
        runner = FakeRunner({
            "exit_code": 0, "readiness": "blocked", "capabilities": {},
        }, returncode=1)
        with self.assertRaisesRegex(command_core.CommandCoreError, "disagree"):
            command_core.validate_workspace(self.workspace, runner=runner)

    def test_validator_receipt_binds_chapter_content_and_warning_fingerprints(self):
        payload = validator_payload(self.workspace, chapter=2)
        first = command_core.validate_workspace(
            self.workspace, chapter=2, runner=FakeRunner(payload))
        self.assertEqual(2, first["binding"]["chapter"])
        os.makedirs(os.path.join(self.workspace, ".ingest"))
        with open(os.path.join(self.workspace, ".ingest", "build_manifest.json"),
                  "w", encoding="utf-8") as stream:
            stream.write("{}\n")
        second_payload = validator_payload(self.workspace, chapter=2)
        second = command_core.validate_workspace(
            self.workspace, chapter=2, runner=FakeRunner(second_payload))
        self.assertNotEqual(
            first["binding"]["content_sha256"],
            second["binding"]["content_sha256"])

    def test_validator_refuses_binding_when_workspace_changes_during_command(self):
        payload = validator_payload(self.workspace, chapter=1)
        target = os.path.join(
            self.workspace, ".ingest", "build_manifest.json")

        class MutatingRunner(FakeRunner):
            def __call__(self, argv, **kwargs):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8") as stream:
                    stream.write("{}\n")
                return super().__call__(argv, **kwargs)

        with self.assertRaisesRegex(
                command_core.CommandCoreError,
                "workspace dependencies changed"):
            command_core.validate_workspace(
                self.workspace, chapter=1, runner=MutatingRunner(payload))

    def test_validator_host_receipt_cannot_acknowledge_truncated_warnings(self):
        payload = validator_payload(self.workspace, chapter=1)
        payload["warning_count"] = 2
        payload["warnings"] = [{"msg": "shown"}]
        payload["truncated"] = {"errors": 0, "warnings": 1}
        with self.assertRaisesRegex(
                command_core.CommandCoreError, "truncated"):
            command_core.validate_workspace(
                self.workspace, chapter=1, runner=FakeRunner(payload))

    def test_source_manifest_path_swap_is_rejected_before_root_selection(self):
        ingest_dir = os.path.join(self.workspace, ".ingest")
        os.makedirs(ingest_dir)
        manifest = os.path.join(ingest_dir, "build_manifest.json")
        replacement = os.path.join(ingest_dir, "replacement.json")
        with open(manifest, "w", encoding="utf-8") as stream:
            json.dump({"source_root": self.materials}, stream)
        with open(replacement, "w", encoding="utf-8") as stream:
            json.dump({"source_root": self.workspace}, stream)

        real_open = command_core.os.open
        swapped = {"done": False}

        def swapping_open(path, flags):
            if (not swapped["done"]
                    and os.path.normcase(os.path.abspath(path))
                    == os.path.normcase(manifest)):
                os.replace(replacement, manifest)
                swapped["done"] = True
            return real_open(path, flags)

        with mock.patch.object(command_core.os, "open", side_effect=swapping_open):
            with self.assertRaisesRegex(
                    command_core.CommandCoreError, "changed while being hashed"):
                command_core.collect_dependency_snapshot(self.workspace)

    def test_strict_json_rejects_duplicate_keys(self):
        runner = FakeRunner(None, raw_stdout=(
            '{"process_success":true,"ready_to_ingest":false,'
            '"ready_to_ingest":true}'))
        with self.assertRaisesRegex(command_core.CommandCoreError, "strict JSON"):
            command_core.exam_start_status(
                self.workspace, self.materials, runner=runner)

    def test_shell_metacharacters_remain_one_literal_argv_value(self):
        runner = FakeRunner(status_payload())
        marker = "; Remove-Item -Recurse C:\\not-executed"
        command_core.run_json_command(
            "exam_start.status", ["status", "--workspace", marker, "--json"],
            runner=runner)
        self.assertIn(marker, runner.argv)
        self.assertFalse(runner.kwargs.get("shell", False))

    def test_undocumented_exit_code_fails_closed(self):
        runner = FakeRunner(status_payload(success=False), returncode=9)
        with self.assertRaisesRegex(command_core.CommandCoreError, "undocumented"):
            command_core.exam_start_status(
                self.workspace, self.materials, runner=runner)

    def test_convenience_functions_require_absolute_paths(self):
        with self.assertRaisesRegex(command_core.CommandCoreError, "absolute path"):
            command_core.exam_start_status("relative", self.materials,
                                           runner=FakeRunner(status_payload()))

    def test_review_list_is_read_only_bounded_command(self):
        runner = FakeRunner({
            "count": 3, "returned": 0, "cursor": 0,
            "summary": {"by_status": {"pending": 3}},
        })
        receipt = command_core.review_list(
            self.workspace, statuses=("pending", "blocked"), runner=runner)
        self.assertEqual("ingest_review.list", receipt["command"])
        self.assertIn("--summary-only", runner.argv)
        self.assertEqual(2, runner.argv.count("--status"))

    def test_progress_show_does_not_define_a_second_state_schema(self):
        state = {"current_phase": 1, "phase_evidence": {}}
        with open(os.path.join(self.workspace, "study_state.json"),
                  "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        runner = FakeRunner(state)
        receipt = command_core.progress_show(self.workspace, runner=runner)
        self.assertEqual(1, receipt["payload"]["current_phase"])
        self.assertEqual("study_state.json", receipt["state_binding"]["path"])
        self.assertEqual(64, len(receipt["state_binding"]["sha256"]))
        with self.assertRaisesRegex(command_core.CommandCoreError, "empty state"):
            command_core.progress_show(self.workspace, runner=FakeRunner({}))

    def test_dependency_snapshot_ignores_workspace_lock_files(self):
        os.makedirs(os.path.join(self.workspace, ".ingest"))
        with workspace_state_lock(self.workspace):
            snapshot = command_core.collect_dependency_snapshot(self.workspace)
        self.assertFalse(any(
            row["path"] in command_core.WORKSPACE_LOCK_DEPENDENCIES
            for row in snapshot["records"]
        ))

    def test_publication_lock_serializes_state_and_ingestion_writers(self):
        os.makedirs(os.path.join(self.workspace, ".ingest"))
        with workspace_publication_lock(self.workspace):
            with self.assertRaisesRegex(ConflictError, "mutation"):
                with workspace_publication_lock(self.workspace):
                    self.fail("nested publication lock unexpectedly acquired")

    def test_dependency_snapshot_covers_readiness_facts_guides_assets_and_parent_source(self):
        root = os.path.abspath(tempfile.mkdtemp(prefix="snapshot_parent_"))
        workspace = os.path.join(root, "workspace")
        os.makedirs(workspace)

        def write(relative, value=b"x"):
            target = os.path.join(root, *relative.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as stream:
                stream.write(value)

        build_manifest = json.dumps({"source_root": root}).encode("utf-8")
        write("workspace/.ingest/build_manifest.json", build_manifest)
        for relative in (
            "workspace/.ingest/parser_receipts.json",
            "workspace/.ingest/review_patches.jsonl",
            "workspace/.ingest/base_content_units.jsonl",
            "workspace/.ingest/chapter_phase_mappings.jsonl",
            "workspace/.ingest/duplicate_candidates.jsonl",
            "workspace/references/assets/question.png",
            "workspace/references/wiki/ch1_intro.md",
            "workspace/study_guide/ch01.visual_qa.json",
            "materials/lecture.pdf",
        ):
            write(relative)

        snapshot = command_core.collect_dependency_snapshot(workspace)
        resolved = []
        paths = set()
        for row in snapshot["records"]:
            base = workspace if row["root"] == "workspace" else root
            resolved.append(os.path.normcase(os.path.abspath(
                os.path.join(base, *row["path"].split("/")))))
            paths.add((row["root"], row["path"]))
        self.assertEqual(len(resolved), len(set(resolved)))
        self.assertIn(("source_root", "materials/lecture.pdf"), paths)
        self.assertIn(("workspace", ".ingest/parser_receipts.json"), paths)
        self.assertIn(("workspace", ".ingest/review_patches.jsonl"), paths)
        self.assertIn(("workspace", "references/assets/question.png"), paths)
        self.assertIn(("workspace", "study_guide/ch01.visual_qa.json"), paths)
        self.assertFalse(any(
            row["root"] == "source_root"
            and row["path"].startswith("workspace/")
            for row in snapshot["records"]))
        public = command_core.dependency_snapshot_receipt(snapshot)
        self.assertEqual({
            "schema_version", "algorithm", "snapshot_sha256", "content_sha256",
            "root_count",
            "directory_count", "file_count", "total_bytes",
        }, set(public))
        self.assertNotIn("lecture.pdf", json.dumps(public))
        os.makedirs(os.path.join(workspace, "study_guide", "empty_readiness_dir"))
        changed = command_core.dependency_snapshot_receipt(
            command_core.collect_dependency_snapshot(workspace))
        self.assertNotEqual(public["snapshot_sha256"], changed["snapshot_sha256"])

    def test_completion_snapshot_rejects_progress_time_mutation(self):
        os.makedirs(os.path.join(self.workspace, ".ingest"))
        with open(os.path.join(self.workspace, ".ingest", "build_manifest.json"),
                  "w", encoding="utf-8") as stream:
            stream.write("{}\n")
        target = os.path.join(self.workspace, "study_state.json")
        with open(target, "w", encoding="utf-8") as stream:
            stream.write('{"current_phase":1}\n')

        class DispatchRunner:
            def __call__(inner_self, argv, **kwargs):
                script = os.path.basename(argv[1])
                if script == "validate_workspace.py":
                    payload = validator_payload(self.workspace, chapter=1)
                elif script == "update_progress.py":
                    with open(target, "w", encoding="utf-8") as stream:
                        stream.write('{"current_phase":2}\n')
                    payload = {"current_phase": 1, "phase_evidence": {}}
                else:
                    raise AssertionError(script)
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps(payload), stderr="")

        with self.assertRaisesRegex(
                command_core.CommandCoreError,
                "progress payload disagrees|workspace dependencies changed"):
            command_core.completion_snapshot(
                self.workspace, chapter=1, runner=DispatchRunner())

    def test_completion_snapshot_rejects_progress_aba_even_when_tree_returns_to_a(self):
        os.makedirs(os.path.join(self.workspace, ".ingest"))
        with open(os.path.join(self.workspace, ".ingest", "build_manifest.json"),
                  "w", encoding="utf-8") as stream:
            stream.write("{}\n")
        target = os.path.join(self.workspace, "study_state.json")
        state_a = {"current_phase": 1, "phase_evidence": {}}
        state_b = {
            "current_phase": 1,
            "phase_evidence": {"1": {"status": "verified"}},
        }
        with open(target, "w", encoding="utf-8") as stream:
            json.dump(state_a, stream)

        class AbaRunner:
            def __call__(inner_self, argv, **kwargs):
                script = os.path.basename(argv[1])
                if script == "validate_workspace.py":
                    payload = validator_payload(self.workspace, chapter=1)
                elif script == "update_progress.py":
                    with open(target, "w", encoding="utf-8") as stream:
                        json.dump(state_b, stream)
                    payload = state_b
                    with open(target, "w", encoding="utf-8") as stream:
                        json.dump(state_a, stream)
                else:
                    raise AssertionError(script)
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps(payload), stderr="")

        with self.assertRaisesRegex(
                command_core.CommandCoreError, "progress payload disagrees"):
            command_core.completion_snapshot(
                self.workspace, chapter=1, runner=AbaRunner())

    def test_progress_only_files_change_full_digest_not_hint_content_digest(self):
        state_path = os.path.join(self.workspace, "study_state.json")
        progress_path = os.path.join(self.workspace, "study_progress.md")
        with open(state_path, "w", encoding="utf-8") as stream:
            stream.write('{"phase_evidence":{}}\n')
        with open(progress_path, "w", encoding="utf-8") as stream:
            stream.write("before\n")
        before = command_core.dependency_snapshot_receipt(
            command_core.collect_dependency_snapshot(self.workspace))
        with open(state_path, "w", encoding="utf-8") as stream:
            stream.write('{"phase_evidence":{"1":{"status":"verified"}}}\n')
        with open(progress_path, "w", encoding="utf-8") as stream:
            stream.write("after\n")
        after = command_core.dependency_snapshot_receipt(
            command_core.collect_dependency_snapshot(self.workspace))
        self.assertNotEqual(before["snapshot_sha256"], after["snapshot_sha256"])
        self.assertEqual(before["content_sha256"], after["content_sha256"])

    def test_real_validator_json_exposes_only_public_snapshot_receipt(self):
        receipt = command_core.validate_workspace(self.workspace, chapter=1)
        snapshot = receipt["payload"]["dependency_snapshot"]
        self.assertEqual({
            "schema_version", "algorithm", "snapshot_sha256", "content_sha256",
            "root_count",
            "directory_count", "file_count", "total_bytes",
        }, set(snapshot))
        self.assertEqual(snapshot["content_sha256"],
                         receipt["binding"]["content_sha256"])
        self.assertEqual(snapshot["snapshot_sha256"],
                         receipt["binding"]["dependency_snapshot_sha256"])

    def test_validator_post_snapshot_drift_forces_every_capability_blocked(self):
        first = command_core.collect_dependency_snapshot(self.workspace)
        second = dict(first, snapshot_sha256="f" * 64)
        optimistic = {
            "chapter": 1,
            "workspace_structural": {
                "status": "ready", "ready": True, "reason_codes": [], "counts": {},
            },
            "teaching_ready": {
                "status": "ready", "ready": True, "reason_codes": [],
                "counts": {"chapter": 1},
            },
            "quiz_ready": {
                "status": "ready", "ready": True, "reason_codes": [],
                "counts": {"chapter": 1},
            },
            "artifact_ready": {
                "status": "ready", "ready": True, "reason_codes": [],
                "counts": {"chapter": 1},
            },
        }
        output = io.StringIO()
        with mock.patch.object(
                workspace_validator, "_collect_dependency_snapshot",
                side_effect=(first, second)), mock.patch.object(
                workspace_validator, "validate", return_value=([], [], {})), \
                mock.patch.object(
                    workspace_validator._readiness_matrix, "capability_readiness",
                    return_value=optimistic), redirect_stdout(output):
            code = workspace_validator.main([
                self.workspace, "--json", "--dependency-snapshot", "--chapter", "1",
            ])
        document = json.loads(output.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("blocked", document["readiness"])
        for name, capability in document["capabilities"].items():
            if name == "chapter":
                continue
            self.assertEqual("blocked", capability["status"])
            self.assertEqual(
                ["dependency_snapshot_drift"], capability["reason_codes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
