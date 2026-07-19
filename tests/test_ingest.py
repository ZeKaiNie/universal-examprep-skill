#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/ingest.py 的端到端测试，覆盖正常流程与多种极端输入。

仅用 Python 标准库（unittest + subprocess），无需安装任何依赖。
运行方式：
    python -m unittest discover -s tests
  或
    python tests/test_ingest.py
"""

import hashlib
import os
import sys
import io
import json
import shutil
import tempfile
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from scripts import exam_start
from scripts import ingest as ingest_module
from scripts import validate_workspace
from scripts.ingestion import ContentUnit
from scripts.ingestion.pipeline import build_payload, verify_material_build_receipt
from scripts.ingestion.storage import atomic_write_json, workspace_publication_lock
from scripts.material_generation import (
    append_runtime_recovery,
    build_pending_generation,
    build_runtime_recovery,
    json_sha256,
    material_recovery_path,
)
UnsafePathError = ingest_module.UnsafePathError

INGEST = os.path.join(SCRIPTS, "ingest.py")

VALID = {
    "course_name": "数据结构",
    "phases": [
        {"phase_num": 1, "phase_name": "基础概念篇", "wiki_filename": "ch1_concepts.md", "wiki_content": "# 第一章\n概念"},
        {"phase_num": 2, "phase_name": "线性表", "wiki_filename": "ch2_list.md", "wiki_content": "# 第二章\n线性表"},
    ],
    "quiz_bank": [
        {"id": "q1", "chapter": 1, "type": "choice", "question": "栈的特点?", "options": ["A.FIFO", "B.LIFO", "C.随机", "D.无序"], "answer": "B", "explanation": "后进先出"},
        {"id": "q2", "chapter": 2, "type": "subjective", "question": "解释链表", "answer": "由节点和指针构成", "keywords": ["指针", "节点"]},
    ],
}


_RUNTIME_IDENTITY = None
_CONFIRMED_FULL_PAIRS = set()


def _confirm_full_workspace(workspace, materials):
    """Register the exact full-mode pair used by a compiler fixture."""
    global _RUNTIME_IDENTITY
    workspace = os.path.abspath(workspace)
    materials = os.path.abspath(materials)
    key = (os.path.normcase(workspace), os.path.normcase(materials))
    if key in _CONFIRMED_FULL_PAIRS:
        return
    os.makedirs(materials, exist_ok=True)
    if _RUNTIME_IDENTITY is None:
        _RUNTIME_IDENTITY = exam_start._capture_runtime_identity()
    output = io.StringIO()
    with mock.patch.object(
            exam_start, "_capture_runtime_identity",
            return_value=_RUNTIME_IDENTITY), redirect_stdout(output):
        code = exam_start.run([
            "confirm", "--course", "compiler-fixture",
            "--materials", materials, "--workspace", workspace,
            "--mode", "from_scratch", "--time-budget", "le1d",
            "--language", "en", "--processing-mode", "full", "--json",
        ])
    if code != 0:
        raise AssertionError(output.getvalue())
    _CONFIRMED_FULL_PAIRS.add(key)


def run_ingest(input_obj, out_dir, *extra):
    """把 input_obj 写成临时 JSON 并调用 ingest.py，返回 CompletedProcess。"""
    ingestion = input_obj.get("ingestion") if isinstance(input_obj, dict) else None
    materials = (
        ingestion.get("source_root")
        if isinstance(ingestion, dict) else None
    ) or (os.path.abspath(out_dir) + "-materials")
    _confirm_full_workspace(out_dir, materials)
    fd, in_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(in_path, "w", encoding="utf-8") as f:
        json.dump(input_obj, f, ensure_ascii=False)
    try:
        return subprocess.run(
            [sys.executable, INGEST, "-i", in_path, "-o", out_dir, *extra],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.remove(in_path)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def read_bytes(*parts):
    with open(os.path.join(*parts), "rb") as stream:
        return stream.read()


class IngestEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp + "-registry"
        self.materials = self.tmp + "-materials"
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.materials, ignore_errors=True)
        environment = mock.patch.dict(
            os.environ, {"EXAMPREP_HOME": self.home}
        )
        environment.start()
        self.addCleanup(environment.stop)
        _confirm_full_workspace(self.tmp, self.materials)

    def structured_input(self):
        materials = os.path.join(self.tmp, "materials")
        workspace = os.path.join(self.tmp, "workspace")
        os.makedirs(materials, exist_ok=True)
        os.makedirs(workspace, exist_ok=True)
        source = os.path.join(materials, "ch01.txt")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write("Chapter 1\nCore concept\n")
        question = {
            "id": "q1",
            "chapter": 1,
            "type": "subjective",
            "question": "Explain the core concept.",
            "answer": "Official answer.",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
            "answer_source_pages": [1],
        }
        payload = build_payload(
            materials,
            [source],
            [{"file": "ch01.txt", "page": 1,
              "text": "Chapter 1\nCore concept"}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[question],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        _confirm_full_workspace(workspace, materials)
        return workspace, {
            "course_name": "Concurrency 101",
            "phases": [{
                "phase_num": 1,
                "phase_name": "Core",
                "wiki_filename": "ch1.md",
                "wiki_content": "# Chapter 1\nCore concept",
            }],
            "quiz_bank": [question],
            "teaching_examples": [],
            "ingestion": payload,
        }

    @staticmethod
    def workspace_snapshot(workspace):
        snapshot = {}
        for root, _dirs, files in os.walk(workspace):
            for name in files:
                path = os.path.join(root, name)
                relative = os.path.relpath(path, workspace).replace(os.sep, "/")
                if relative in (".study_state.lock", ".ingest/mutation.lock"):
                    continue
                with open(path, "rb") as stream:
                    snapshot[relative] = stream.read()
        return snapshot

    # ---------- 正常流程 ----------
    def test_valid_input_generates_all_files(self):
        r = run_ingest(VALID, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in ("references/wiki/ch1_concepts.md", "references/wiki/ch2_list.md",
                    "references/quiz_bank.json", "study_plan.md", "study_progress.md"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), f"缺少 {rel}")
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(len(bank), 2)  # 题库是合法 JSON 数组

    def test_legacy_ingest_blocks_attempt_tainted_knowledge_point_concept_in_both_orders(self):
        shared = "references/assets/shared.png"
        official = {
            "id": "official", "chapter": 1, "type": "subjective",
            "question": "Official prompt", "answer": "Official answer",
            "knowledge_points": ["Sensitive concept"],
            "assets": [{"path": shared, "role": "question_context"}],
        }
        attempt = {
            "id": "attempt", "chapter": 1, "type": "subjective",
            "question": "Student submission", "answer": "",
            "assets": [{"path": shared, "role": "student_attempt"}],
        }
        for index, bank in enumerate(([attempt, official], [official, attempt])):
            with self.subTest(order=index):
                workspace = os.path.join(self.tmp, "legacy-taint-%d" % index)
                payload = {
                    "course_name": "Asset policy",
                    "phases": [{
                        "phase_num": 1, "phase_name": "Core",
                        "wiki_filename": "ch1.md", "wiki_content": "# Core",
                    }],
                    "quiz_bank": bank,
                }
                result = run_ingest(payload, workspace)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("student_attempt", result.stdout + result.stderr)
                self.assertFalse(os.path.exists(os.path.join(
                    workspace, "references", "retrieval_index.json"
                )))

    def test_structured_initial_ingest_blocks_foreign_attempt_tainted_concept(self):
        workspace, input_obj = self.structured_input()
        shared = "references/assets/shared.png"
        input_obj["quiz_bank"][0]["knowledge_points"] = ["Sensitive concept"]
        input_obj["quiz_bank"][0]["assets"] = [{
            "path": shared, "role": "question_context",
        }]
        rows = input_obj["ingestion"]["content_units"]
        q_index = next(i for i, row in enumerate(rows) if row["kind"] == "question")
        question = dict(rows[q_index])
        question["metadata"] = dict(question["metadata"])
        question["metadata"]["assets"] = [{
            "path": shared, "role": "question_context",
        }]
        official = ContentUnit.from_dict(question)
        rows[q_index] = official.to_dict()
        rows.append(ContentUnit.create(
            official.source_id, official.source_sha256, official.source_file,
            "figure", "Foreign student work", 1, ordinal=990,
            chapter_id="ch02", phase_id="phase02",
            asset_path=shared, asset_role="student_attempt",
        ).to_dict())
        asset = os.path.join(workspace, "references", "assets", "shared.png")
        os.makedirs(os.path.dirname(asset), exist_ok=True)
        with open(asset, "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")

        result = run_ingest(input_obj, workspace, "--force")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("student_attempt", result.stdout + result.stderr)
        self.assertFalse(os.path.exists(os.path.join(
            workspace, "references", "retrieval_index.json"
        )))

    def test_structured_initial_pair_alias_fails_before_any_derivative_write(self):
        workspace, input_obj = self.structured_input()
        shared = "references/assets/shared.png"
        rows = input_obj["ingestion"]["content_units"]
        question_index = next(i for i, row in enumerate(rows) if row["kind"] == "question")
        answer_index = next(i for i, row in enumerate(rows) if row["kind"] == "answer")
        question = dict(rows[question_index])
        answer = dict(rows[answer_index])
        self.assertEqual(answer["unit_id"], question["paired_unit_id"])
        self.assertEqual(question["unit_id"], answer["paired_unit_id"])
        question.update(asset_path=shared, asset_role="figure")
        answer.update(
            external_id=None,
            asset_path=shared,
            asset_role="worked_solution",
        )
        rows[question_index] = ContentUnit.from_dict(question).to_dict()
        rows[answer_index] = ContentUnit.from_dict(answer).to_dict()
        # Hostile legacy spelling is injected only after typed model creation;
        # the shared preflight must still compare it as the same physical file.
        rows[answer_index]["asset_path"] = "references\\assets\\shared.png"

        sentinels = {
            "references/wiki/ch1.md": b"OLD WIKI",
            "references/quiz_bank.json": b"OLD BANK",
            "references/retrieval_index.json": b"OLD INDEX",
            ".ingest/content_units.jsonl": b"OLD UNITS",
            ".ingest/canonical_groups.jsonl": b"OLD FACTS",
        }
        for relative, payload in sentinels.items():
            path = os.path.join(workspace, *relative.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as stream:
                stream.write(payload)
        before = self.workspace_snapshot(workspace)

        result = run_ingest(input_obj, workspace, "--force")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("both prompt and official answer", result.stdout + result.stderr)
        self.assertEqual(before, self.workspace_snapshot(workspace))

    def test_structured_cli_reingest_does_not_reenter_mutation_lock(self):
        workspace, input_obj = self.structured_input()
        first = run_ingest(input_obj, workspace)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(os.path.isfile(os.path.join(
            workspace, ".ingest", "build_manifest.json")))

        second = run_ingest(input_obj, workspace)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn("another ingestion mutation", second.stdout + second.stderr)

    def test_cli_publication_lock_conflict_is_no_write(self):
        workspace, input_obj = self.structured_input()
        first = run_ingest(input_obj, workspace)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        changed = json.loads(json.dumps(input_obj, ensure_ascii=False))
        changed["phases"][0]["wiki_content"] = "# Chapter 1\nMUTATED"
        changed["quiz_bank"][0]["answer"] = "MUTATED"

        with workspace_publication_lock(workspace):
            before = self.workspace_snapshot(workspace)
            blocked = run_ingest(changed, workspace, "--force")
            after = self.workspace_snapshot(workspace)

        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertIn("工作区发布冲突", blocked.stdout + blocked.stderr)
        self.assertNotIn("Traceback", blocked.stdout + blocked.stderr)
        self.assertEqual(before, after)

    def test_cli_reads_replaced_raw_input_inside_publication_lock(self):
        workspace, input_a = self.structured_input()
        ingest_dir = os.path.join(workspace, ".ingest")
        os.makedirs(ingest_dir, exist_ok=True)
        input_path = os.path.join(ingest_dir, "source_raw_input.json")
        with open(input_path, "w", encoding="utf-8") as stream:
            json.dump(input_a, stream, ensure_ascii=False)

        input_b = json.loads(json.dumps(input_a, ensure_ascii=False))
        input_b["course_name"] = "Replacement generation"
        input_b["phases"][0]["wiki_content"] = "# Chapter 1\nGENERATION B"

        def replace_before_lock(_args, _output_dir):
            replacement = input_path + ".next"
            with open(replacement, "w", encoding="utf-8") as stream:
                json.dump(input_b, stream, ensure_ascii=False)
            os.replace(replacement, input_path)

        argv = [INGEST, "-i", input_path, "-o", workspace]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            ingest_module, "_before_publication_lock", replace_before_lock
        ), redirect_stdout(output):
            ingest_module.main()

        wiki = read(workspace, "references", "wiki", "ch1.md")
        self.assertIn("GENERATION B", wiki)
        self.assertNotIn("Core concept", wiki)
        persisted_input = json.loads(read(input_path))
        self.assertEqual("Replacement generation", persisted_input["course_name"])

    def test_expected_input_sha_rejects_replaced_generation_inside_lock(self):
        workspace, input_a = self.structured_input()
        ingest_dir = os.path.join(workspace, ".ingest")
        os.makedirs(ingest_dir, exist_ok=True)
        input_path = os.path.join(ingest_dir, "source_raw_input.json")
        with open(input_path, "w", encoding="utf-8") as stream:
            json.dump(input_a, stream, ensure_ascii=False)
        with open(input_path, "rb") as stream:
            expected_sha256 = hashlib.sha256(stream.read()).hexdigest()

        input_b = json.loads(json.dumps(input_a, ensure_ascii=False))
        input_b["course_name"] = "Replacement generation"
        input_b["phases"][0]["wiki_content"] = "# Chapter 1\nGENERATION B"

        def replace_before_lock(_args, _output_dir):
            replacement = input_path + ".next"
            with open(replacement, "w", encoding="utf-8") as stream:
                json.dump(input_b, stream, ensure_ascii=False)
            os.replace(replacement, input_path)

        argv = [
            INGEST, "-i", input_path, "-o", workspace,
            "--expected-input-sha256", expected_sha256,
        ]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            ingest_module, "_before_publication_lock", replace_before_lock
        ), redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            ingest_module.main()

        self.assertEqual(1, stopped.exception.code)
        self.assertIn("input generation drifted before compilation", output.getvalue())
        self.assertFalse(os.path.exists(
            os.path.join(workspace, "references", "wiki", "ch1.md")
        ))

    def test_pending_generation_rolls_back_late_compile_failure_and_retries(self):
        workspace, input_obj = self.structured_input()
        input_obj["terms"] = {"legacy glossary": ["stale expansion"]}
        first = run_ingest(input_obj, workspace)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)

        critical = [
            ".ingest/build_manifest.json",
            ".ingest/base_content_units.jsonl",
            ".ingest/content_units.jsonl",
            "references/wiki/ch1.md",
            "references/quiz_bank.json",
            "references/teaching_examples.json",
            "references/teaching_baseline.json",
            "references/retrieval_index.json",
            "references/terms.json",
            "ingest_report.json",
            "study_plan.md",
        ]
        baseline = {
            relative: read_bytes(workspace, *relative.split("/"))
            for relative in critical
        }
        ingest_dir = os.path.join(workspace, ".ingest")
        raw_path = os.path.join(ingest_dir, "source_raw_input.json")
        report_path = os.path.join(ingest_dir, "parse_report.json")
        pending_path = os.path.join(ingest_dir, "material_build_pending.json")
        raw_obj = json.loads(json.dumps(input_obj, ensure_ascii=False))
        raw_obj.pop("terms")
        ingest_module.validate(raw_obj)
        atomic_write_json(raw_path, raw_obj)
        report = {"asset_role_promotions": [], "warnings": [], "ai_review": []}
        atomic_write_json(report_path, report)
        manifest_sha = hashlib.sha256(
            read_bytes(ingest_dir, "build_manifest.json")
        ).hexdigest()
        pending = build_pending_generation(
            hashlib.sha256(read_bytes(raw_path)).hexdigest(),
            hashlib.sha256(read_bytes(report_path)).hexdigest(),
            raw_obj,
            report,
            manifest_sha,
        )
        atomic_write_json(pending_path, pending)
        expected_sha = hashlib.sha256(read_bytes(raw_path)).hexdigest()
        argv = [
            INGEST, "-i", raw_path, "-o", workspace,
            "--expected-input-sha256", expected_sha,
        ]

        with mock.patch.object(sys, "argv", argv), mock.patch.object(
                ingest_module, "_compile_visuals_unlocked",
                side_effect=RuntimeError("injected late compiler failure")), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            ingest_module.main()

        self.assertTrue(os.path.isfile(pending_path))
        self.assertFalse(os.path.exists(
            os.path.join(ingest_dir, "pending_ingest.json")
        ))
        for relative, expected in baseline.items():
            with open(os.path.join(workspace, *relative.split("/")), "rb") as stream:
                self.assertEqual(expected, stream.read(), relative)
        self.assertFalse(os.path.exists(
            os.path.join(ingest_dir, "material_build_receipt.json")
        ))

        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            ingest_module.main()
        self.assertFalse(os.path.exists(pending_path))
        self.assertFalse(os.path.exists(
            os.path.join(workspace, "references", "terms.json")
        ))
        receipt_path = os.path.join(ingest_dir, "material_build_receipt.json")
        self.assertTrue(os.path.isfile(receipt_path))
        manifest_path = os.path.join(ingest_dir, "build_manifest.json")
        manifest = json.loads(read(manifest_path))
        self.assertEqual(2, manifest["schema_version"])
        self.assertEqual(
            json.loads(read(receipt_path))["generation_id"],
            manifest["material_build"]["generation_id"],
        )

        # JSON booleans compare equal to integers in Python.  Neither the
        # generation schema nor the manifest protocol may therefore rely on
        # ordinary ``== 1`` equality.
        valid_receipt = json.loads(read(receipt_path))
        boolean_receipt = json.loads(json.dumps(valid_receipt))
        boolean_receipt["schema_version"] = True
        unsigned = dict(boolean_receipt)
        unsigned.pop("status")
        unsigned.pop("generation_id")
        boolean_receipt["generation_id"] = json_sha256(unsigned)
        atomic_write_json(receipt_path, boolean_receipt)
        with self.assertRaisesRegex(ValueError, "generation schema"):
            verify_material_build_receipt(workspace, required=True)
        atomic_write_json(receipt_path, valid_receipt)

        boolean_manifest = json.loads(json.dumps(manifest))
        boolean_manifest["material_build"]["protocol_version"] = True
        atomic_write_json(manifest_path, boolean_manifest)
        with self.assertRaisesRegex(ValueError, "protocol_version"):
            verify_material_build_receipt(workspace, required=True)
        atomic_write_json(manifest_path, manifest)

        # A current-protocol workspace cannot be downgraded by deleting both
        # the receipt and its generic artifact row.
        os.unlink(receipt_path)
        manifest["artifacts"].pop("material_build_receipt")
        atomic_write_json(manifest_path, manifest)
        errors, _warnings, _stats = validate_workspace.validate(workspace)
        self.assertTrue(errors)
        self.assertIn(
            "required material build receipt is missing",
            " | ".join(row["msg"] for row in errors),
        )

    def test_completed_recovery_log_rolls_back_if_final_manifest_write_crashes(self):
        workspace, input_obj = self.structured_input()
        first = run_ingest(input_obj, workspace)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)

        ingest_dir = os.path.join(workspace, ".ingest")
        raw_path = os.path.join(ingest_dir, "source_raw_input.json")
        report_path = os.path.join(ingest_dir, "parse_report.json")
        pending_path = os.path.join(ingest_dir, "material_build_pending.json")
        manifest_path = os.path.join(ingest_dir, "build_manifest.json")
        receipt_path = os.path.join(ingest_dir, "material_build_receipt.json")
        raw_obj = json.loads(json.dumps(input_obj, ensure_ascii=False))
        ingest_module.validate(raw_obj)
        atomic_write_json(raw_path, raw_obj)
        report = {"asset_role_promotions": [], "warnings": [], "ai_review": []}
        atomic_write_json(report_path, report)
        pending = build_pending_generation(
            hashlib.sha256(read_bytes(raw_path)).hexdigest(),
            hashlib.sha256(read_bytes(report_path)).hexdigest(),
            raw_obj,
            report,
            hashlib.sha256(read_bytes(manifest_path)).hexdigest(),
        )
        atomic_write_json(pending_path, pending)
        recovery = build_runtime_recovery(
            pending,
            hashlib.sha256(read_bytes(pending_path)).hexdigest(),
            "resume",
            {
                "path": "exam_runtime_receipt.json", "state": "missing",
                "sha256": None, "runtime_digest": None,
            },
            {
                "path": "exam_runtime_receipt.json", "state": "valid",
                "sha256": "4" * 64, "runtime_digest": "5" * 64,
            },
            "2026-07-16T00:00:00Z",
        )
        recovery_relative = material_recovery_path(pending["generation_id"])
        recovery_path = os.path.join(workspace, *recovery_relative.split("/"))
        atomic_write_json(recovery_path, append_runtime_recovery(None, recovery))
        baseline = {
            "pending": read_bytes(pending_path),
            "recovery": read_bytes(recovery_path),
            "manifest": read_bytes(manifest_path),
        }
        argv = [
            INGEST, "-i", raw_path, "-o", workspace,
            "--expected-input-sha256",
            hashlib.sha256(read_bytes(raw_path)).hexdigest(),
        ]
        finalizer_globals = ingest_module.finalize_material_build_generation.__globals__
        original_atomic_write = finalizer_globals["atomic_write_json"]
        observed_recovery_status = []

        def crash_after_completed_log(path, value):
            result = original_atomic_write(path, value)
            # GitHub Windows TEMP may expose one physical file through an
            # 8.3/reparse alias while the finalizer uses its resolved path.
            # Compare file identity so the injected crash cannot miss there.
            is_recovery_path = os.path.samefile(path, recovery_path)
            if (is_recovery_path
                    and isinstance(value, dict)
                    and value.get("records")
                    and (value["records"][-1].get("outcome") or {}).get(
                        "status"
                    ) == "completed"):
                live = json.loads(read(recovery_path))
                observed_recovery_status.append(
                    live["records"][-1]["outcome"]["status"]
                )
                raise RuntimeError("injected crash after completed recovery log")
            return result

        with mock.patch.object(sys, "argv", argv), mock.patch.dict(
                finalizer_globals, {"atomic_write_json": crash_after_completed_log}
                ), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            ingest_module.main()

        self.assertEqual(["completed"], observed_recovery_status)
        self.assertEqual(baseline["pending"], read_bytes(pending_path))
        self.assertEqual(baseline["recovery"], read_bytes(recovery_path))
        self.assertEqual(baseline["manifest"], read_bytes(manifest_path))
        self.assertFalse(os.path.exists(receipt_path))
        self.assertFalse(os.path.exists(
            os.path.join(ingest_dir, "pending_ingest.json")
        ))

        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            ingest_module.main()
        self.assertFalse(os.path.exists(pending_path))
        completed = json.loads(read(recovery_path))
        self.assertEqual(
            "completed", completed["records"][-1]["outcome"]["status"]
        )

    def test_validator_rejects_invalid_legacy_build_manifest_schema(self):
        workspace, input_obj = self.structured_input()
        compiled = run_ingest(input_obj, workspace)
        self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)
        errors, _warnings, _stats = validate_workspace.validate(workspace)
        self.assertNotIn(
            "schema_version must be integer 1 or 2",
            " | ".join(row["msg"] for row in errors),
        )

        manifest_path = os.path.join(
            workspace, ".ingest", "build_manifest.json"
        )
        manifest = json.loads(read(manifest_path))
        manifest["schema_version"] = True
        atomic_write_json(manifest_path, manifest)

        errors, _warnings, _stats = validate_workspace.validate(workspace)
        self.assertEqual(2, validate_workspace._exit_code(errors))
        self.assertIn(
            "schema_version must be integer 1 or 2",
            " | ".join(row["msg"] for row in errors),
        )

    def test_malformed_terms_fail_before_glossary_publication(self):
        malformed = (
            ["not", "an", "object"],
            {"probability": "概率"},
            {"probability": []},
            {" probability": ["概率"]},
            {"probability": ["概率", "概率"]},
        )
        for index, terms in enumerate(malformed):
            with self.subTest(terms=terms):
                workspace = os.path.join(self.tmp, "bad-terms-%d" % index)
                raw = json.loads(json.dumps(VALID, ensure_ascii=False))
                raw["terms"] = terms
                result = run_ingest(raw, workspace)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(os.path.exists(
                    os.path.join(workspace, "references", "terms.json")
                ))

    def test_path_guard_error_is_reported_without_traceback(self):
        output = io.StringIO()
        with mock.patch.object(
            ingest_module,
            "safe_workspace_entry",
            side_effect=UnsafePathError("path contains a symlink entry"),
        ):
            with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                ingest_module._safe_output_tree(self.tmp)
        self.assertEqual(1, stopped.exception.code)
        self.assertIn("符号链接", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_anchors_replaced_with_generated_content(self):
        progress_before = read(self.tmp, "study_progress.md")
        run_ingest(VALID, self.tmp)
        plan = read(self.tmp, "study_plan.md")
        prog = read(self.tmp, "study_progress.md")
        # Confirmation now creates the learner-state view before the compiler.
        # The compiler replaces plan markers but must preserve that generated view.
        self.assertNotIn("<!-- PHASE_TABLE -->", plan)
        self.assertIn("**", plan)
        self.assertEqual(progress_before, prog)

    # ---------- 极端输入：结构性错误应中止（退出码 1） ----------
    def test_missing_phase_key_fails_loudly(self):
        bad = {"course_name": "X", "phases": [{"phase_num": 1, "wiki_filename": "a.md", "wiki_content": "x"}], "quiz_bank": []}
        r = run_ingest(bad, self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("phase_name", r.stdout)  # 明确指出缺哪个字段
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "references", "wiki", "chNone_notes.md")))

    def test_empty_phases_fails(self):
        self.assertEqual(run_ingest({"course_name": "X", "phases": [], "quiz_bank": []}, self.tmp).returncode, 1)

    def test_phases_not_a_list_fails(self):
        self.assertEqual(run_ingest({"course_name": "X", "phases": "oops", "quiz_bank": []}, self.tmp).returncode, 1)

    def test_path_traversal_filename_rejected(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "../../evil.md", "wiki_content": "x"}],
               "quiz_bank": []}
        r = run_ingest(bad, self.tmp)
        self.assertEqual(r.returncode, 1)
        # 确认没有写到 wiki 目录之外
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.md")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.md")))

    def test_duplicate_wiki_filename_rejected(self):
        bad = {"course_name": "X", "phases": [
            {"phase_num": 1, "phase_name": "A", "wiki_filename": "dup.md", "wiki_content": "x"},
            {"phase_num": 2, "phase_name": "B", "wiki_filename": "dup.md", "wiki_content": "y"},
        ], "quiz_bank": []}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    def test_duplicate_quiz_id_rejected_before_any_workspace_write(self):
        bad = json.loads(json.dumps(VALID, ensure_ascii=False))
        bad["quiz_bank"][1]["id"] = "q1"
        result = run_ingest(bad, self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("重复", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "references")))

    def test_choice_question_missing_options_fails(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
               "quiz_bank": [{"id": "q1", "type": "choice", "question": "?", "answer": "A"}]}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    def test_invalid_json_fails(self):
        in_path = os.path.join(self.tmp, "bad.json")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        r = subprocess.run([sys.executable, INGEST, "-i", in_path, "-o", self.tmp],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)

    # ---------- 扩展题型：diagram / fill_blank 等被接受 ----------
    def test_extended_quiz_types_accepted(self):
        data = {"course_name": "数据结构",
                "phases": [{"phase_num": 1, "phase_name": "树", "wiki_filename": "ch1.md", "wiki_content": "x"}],
                "quiz_bank": [
                    {"id": "d1", "type": "diagram", "question": "画出 AVL 插入 [3,2,1]", "answer": "右旋", "diagram_type": "avl_tree"},
                    {"id": "f1", "type": "fill_blank", "question": "栈是____", "answer": "LIFO"},
                ]}
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual({q["type"] for q in bank}, {"diagram", "fill_blank"})

    def test_unknown_quiz_type_fails(self):
        bad = {"course_name": "X",
               "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
               "quiz_bank": [{"id": "q1", "type": "essay", "question": "?", "answer": "a"}]}
        self.assertEqual(run_ingest(bad, self.tmp).returncode, 1)

    # ---------- 缺标准答案：警告但不中止 ----------
    def test_missing_answer_warns_but_succeeds(self):
        data = {"course_name": "X",
                "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
                "quiz_bank": [{"id": "q9", "type": "subjective", "question": "?"}]}
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("q9", r.stdout)  # 点名缺答案的题

    def test_legacy_non_gradable_bank_item_does_not_add_missing_answer(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [{
                "id": "legacy-worked", "chapter": 1, "type": "subjective",
                "question": "Completed demonstration", "gradable": False,
                "answer_status": "unknown",
            }],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        report = json.loads(read(self.tmp, "ingest_report.json"))
        self.assertEqual([], report["missing_answer_ids"])
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertIs(bank[0]["gradable"], False)

    def test_gradable_must_be_a_real_boolean(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [{
                "id": "bad", "type": "subjective", "question": "q",
                "gradable": "false",
            }],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("gradable", r.stdout + r.stderr)

    def test_teaching_examples_persist_independently_and_do_not_add_missing_answers(self):
        example = {
            "id": "lecture_example_1_2", "chapter": 1,
            "type": "subjective", "question": "A completed worked demonstration.",
            "answer_status": "unknown", "source": "material",
            "source_file": "ch01.pdf", "source_pages": [3],
            "teaching_role": "worked_example", "assets": [],
        }
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "# Chapter 1\nExample 1.2"}],
            "quiz_bank": [{"id": "q1", "chapter": 1, "type": "subjective",
                           "question": "Assessable question", "answer": "answer"}],
            "teaching_examples": [dict(example)],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = os.path.join(self.tmp, "references", "teaching_examples.json")
        self.assertTrue(os.path.isfile(path))
        saved = json.loads(read(path))
        self.assertEqual(saved, [example])
        index = json.loads(read(
            self.tmp, "references", "retrieval_index.json"))
        teaching_integrity = index["integrity"]["teaching_examples"]
        self.assertEqual(
            "references/teaching_examples.json", teaching_integrity["file"]
        )
        with open(path, "rb") as stream:
            self.assertEqual(
                hashlib.sha256(stream.read()).hexdigest(),
                teaching_integrity["sha256"],
            )
        report = json.loads(read(self.tmp, "ingest_report.json"))
        self.assertEqual(report["teaching_examples"], 1)
        index = json.loads(read(
            self.tmp, "references", "retrieval_index.json"))
        with open(os.path.join(
                self.tmp, "references", "teaching_examples.json"), "rb") as stream:
            self.assertEqual(
                hashlib.sha256(stream.read()).hexdigest(),
                index["integrity"]["teaching_examples"]["sha256"],
            )
        self.assertEqual(report["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertEqual(report["teaching_examples_by_chapter"], {"1": 1})
        self.assertEqual(report["teaching_example_ids_by_chapter"],
                         {"1": ["lecture_example_1_2"]})
        self.assertEqual(report["missing_answer_ids"], [])
        baseline = json.loads(read(
            self.tmp, "references", "teaching_baseline.json"))
        self.assertEqual(baseline["policy"], "append_only")
        self.assertEqual(baseline["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertFalse(any(name.startswith(".teaching_examples.json.")
                             for name in os.listdir(os.path.join(self.tmp, "references"))))

    def test_teaching_manifest_destination_symlink_is_rejected_without_touching_target(self):
        os.makedirs(os.path.join(self.tmp, "references"))
        outside = os.path.join(os.path.dirname(self.tmp), "outside-teaching.json")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("OUTSIDE-SENTINEL")
        link = os.path.join(self.tmp, "references", "teaching_examples.json")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")
        data = dict(VALID, teaching_examples=[])
        result = run_ingest(data, self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-SENTINEL")

    def test_references_parent_symlink_escape_is_rejected_before_writes(self):
        outside = tempfile.mkdtemp(prefix="outside-references-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        link = os.path.join(self.tmp, "references")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")
        result = run_ingest(dict(VALID, teaching_examples=[]), self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(os.listdir(outside), [])

    def test_quiz_bank_hardlink_is_replaced_without_mutating_outside_inode(self):
        self.assertEqual(run_ingest(VALID, self.tmp).returncode, 0)
        outside = os.path.join(os.path.dirname(self.tmp), "outside-quiz-hardlink.json")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        with open(outside, "w", encoding="utf-8") as stream:
            stream.write("OUTSIDE-HARDLINK-SENTINEL")
        quiz_path = os.path.join(self.tmp, "references", "quiz_bank.json")
        os.remove(quiz_path)
        try:
            os.link(outside, quiz_path)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("hard links unavailable")

        result = run_ingest(VALID, self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-HARDLINK-SENTINEL")
        self.assertFalse(os.path.samefile(outside, quiz_path))
        self.assertEqual(len(json.loads(read(quiz_path))), len(VALID["quiz_bank"]))

    def test_wiki_destination_symlink_is_rejected_without_touching_target(self):
        self.assertEqual(run_ingest(VALID, self.tmp).returncode, 0)
        outside = os.path.join(os.path.dirname(self.tmp), "outside-wiki.md")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        with open(outside, "w", encoding="utf-8") as stream:
            stream.write("OUTSIDE-WIKI-SENTINEL")
        wiki_path = os.path.join(self.tmp, "references", "wiki", "ch1_concepts.md")
        os.remove(wiki_path)
        try:
            os.symlink(outside, wiki_path)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlink privilege")

        result = run_ingest(VALID, self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stdout + result.stderr)
        self.assertEqual(read(outside), "OUTSIDE-WIKI-SENTINEL")

    def test_legacy_raw_input_without_teaching_examples_does_not_require_manifest(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(
            self.tmp, "references", "teaching_examples.json")))

    def test_legacy_rerun_preserves_existing_teaching_manifest_and_report_baseline(self):
        example = {
            "id": "lecture_example_1_2", "chapter": 1,
            "type": "subjective", "question": "A completed worked demonstration.",
            "answer_status": "unknown", "source": "material",
            "source_file": "ch01.pdf", "source_pages": [3],
            "teaching_role": "worked_example", "assets": [],
        }
        base = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
        }
        first = dict(base, teaching_examples=[example])
        self.assertEqual(run_ingest(first, self.tmp).returncode, 0)
        # A legacy producer omits the new field.  It must not erase either the manifest or the
        # retention baseline used by validate_workspace.py.
        self.assertEqual(run_ingest(base, self.tmp).returncode, 0)
        saved = json.loads(read(self.tmp, "references", "teaching_examples.json"))
        report = json.loads(read(self.tmp, "ingest_report.json"))
        self.assertEqual(saved, [example])
        self.assertEqual(report["teaching_example_ids"], ["lecture_example_1_2"])
        self.assertEqual(report["teaching_example_ids_by_chapter"],
                         {"1": ["lecture_example_1_2"]})
        self.assertEqual(report["teaching_examples"], 1)

    def test_explicit_smaller_snapshot_cannot_shrink_independent_teaching_baseline(self):
        examples = [
            {"id": "ex1", "chapter": 1, "type": "subjective",
             "question": "Chapter 1 demonstration", "source_file": "ch01.pdf",
             "source_pages": [1], "teaching_role": "worked_example", "assets": []},
            {"id": "ex2", "chapter": 2, "type": "subjective",
             "question": "Chapter 2 demonstration", "source_file": "ch02.pdf",
             "source_pages": [1], "teaching_role": "worked_example", "assets": []},
        ]
        data = dict(VALID, teaching_examples=examples)
        self.assertEqual(run_ingest(data, self.tmp).returncode, 0)

        smaller = dict(VALID, teaching_examples=[examples[1]])
        result = run_ingest(smaller, self.tmp)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "teaching baseline IDs missing from the current teaching_examples snapshot",
            result.stdout + result.stderr,
        )
        baseline = json.loads(read(
            self.tmp, "references", "teaching_baseline.json"))
        report = json.loads(read(self.tmp, "ingest_report.json"))
        current = json.loads(read(
            self.tmp, "references", "teaching_examples.json"))
        self.assertEqual(baseline["teaching_example_ids"], ["ex1", "ex2"])
        self.assertEqual(baseline["teaching_example_ids_by_chapter"],
                         {"1": ["ex1"], "2": ["ex2"]})
        self.assertEqual(report["teaching_example_ids"], ["ex1", "ex2"])
        self.assertEqual(report["current_teaching_example_ids"], ["ex1", "ex2"])
        self.assertEqual([item["id"] for item in current], ["ex1", "ex2"])

    def test_invalid_teaching_example_role_fails_loudly(self):
        data = {
            "course_name": "X",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md",
                        "wiki_content": "x"}],
            "quiz_bank": [],
            "teaching_examples": [{
                "id": "e1", "chapter": 1, "teaching_role": "quiz_me",
                "source_file": "ch01.pdf", "source_pages": [1],
            }],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("teaching_role", r.stdout)

    # ---------- 幂等：保护 study_progress.md ----------
    def test_rerun_does_not_clobber_progress(self):
        run_ingest(VALID, self.tmp)
        prog_path = os.path.join(self.tmp, "study_progress.md")
        with open(prog_path, "a", encoding="utf-8") as f:
            f.write("\n学生的错题记录-请勿删除\n")
        r = run_ingest(VALID, self.tmp)  # 再跑一次，不加 --force
        self.assertEqual(r.returncode, 0)
        self.assertIn("学生的错题记录-请勿删除", read(prog_path))  # 进度保留

    def test_force_backs_up_then_regenerates(self):
        run_ingest(VALID, self.tmp)
        prog_path = os.path.join(self.tmp, "study_progress.md")
        with open(prog_path, "a", encoding="utf-8") as f:
            f.write("\n旧内容标记\n")
        r = run_ingest(VALID, self.tmp, "--force")
        self.assertEqual(r.returncode, 0)
        backups = [n for n in os.listdir(self.tmp) if n.startswith("study_progress.md.bak-")]
        self.assertTrue(backups, "未创建备份文件")
        self.assertIn("旧内容标记", read(self.tmp, backups[0]))   # 旧内容进了备份
        self.assertNotIn("旧内容标记", read(prog_path))            # 新文件已重置


    # ---------- 回归：缺 id 自动补全 ----------
    def test_missing_ids_auto_filled_with_unique_ids(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"type": "subjective", "question": "Q1", "answer": "A1"},
                {"type": "subjective", "question": "Q2", "answer": "A2"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        ids = [q["id"] for q in bank]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, f"ID 重复: {ids}")

    # ---------- 回归：已有 id 不撞号 ----------
    def test_auto_ids_skip_existing_ids(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q2", "type": "subjective", "question": "已有q2", "answer": "A2"},
                {"type": "subjective", "question": "缺id", "answer": "A"},
                {"type": "subjective", "question": "缺id2", "answer": "B"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        ids = [q["id"] for q in bank]
        self.assertIn("q2", ids)
        # 自动补的 id 不能是 q2
        auto_ids = [i for i in ids if i != "q2"]
        self.assertNotIn("q2", auto_ids, f"撞号: {ids}")
        self.assertEqual(len(set(ids)), 3, f"ID 重复: {ids}")

    def test_auto_ids_preserve_zero_id_as_stable_string(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": 0, "type": "subjective", "question": "已有数字 id", "answer": "A"},
                {"type": "subjective", "question": "缺 id", "answer": "B"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(bank[0]["id"], "0")
        self.assertEqual(len({q["id"] for q in bank}), 2, f"ID 重复: {[q['id'] for q in bank]}")

    # ---------- 回归：true_false 中英文规范化 ----------
    def test_true_false_normalize_cn_en(self):
        cases = [
            ("正确", True), ("错误", False),
            ("对", True), ("错", False),
            ("是", True), ("否", False),
            ("真", True), ("假", False),
            ("true", True), ("false", False),
            ("yes", True), ("no", False),
            ("√", True), ("×", False),
        ]
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": f"q{i}", "type": "true_false", "question": raw, "answer": raw}
                for i, (raw, _) in enumerate(cases, 1)
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        for i, (raw, expected) in enumerate(cases):
            q = bank[i]
            self.assertIsInstance(q["answer"], bool,
                                  f"q{i+1} 答案 {raw!r} 应为 bool，实际 {type(q['answer']).__name__}")
            self.assertEqual(q["answer"], expected,
                             f"q{i+1} 答案 {raw!r} 期望 {expected}，实际 {q['answer']}")

    def test_true_false_boolean_false_not_reported_missing(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q1", "type": "true_false", "question": "判断题", "answer": False},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("缺少标准答案", r.stdout)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertIs(bank[0]["answer"], False)

    # ---------- 回归：无法识别的 true_false 答案不被静默强转 ----------
    def test_true_false_unknown_answer_preserved(self):
        data = {
            "course_name": "测试",
            "phases": [{"phase_num": 1, "phase_name": "P", "wiki_filename": "a.md", "wiki_content": "x"}],
            "quiz_bank": [
                {"id": "q1", "type": "true_false", "question": "未知答案", "answer": "maybe"},
                {"id": "q2", "type": "true_false", "question": "数字", "answer": "1"},
                {"id": "q3", "type": "choice", "question": "无关题型", "options": ["A.1","B.2","C.3","D.4"], "answer": "A"},
            ],
        }
        r = run_ingest(data, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bank = json.loads(read(self.tmp, "references", "quiz_bank.json"))
        self.assertEqual(bank[0]["answer"], "maybe",
                         f"无法识别的 true_false 答案应保留原值，实际 {bank[0]['answer']!r}")
        self.assertEqual(bank[1]["answer"], "1",
                         f"数字答案应保留原值，实际 {bank[1]['answer']!r}")
        self.assertEqual(bank[2]["answer"], "A",
                         "choice 题型不应受影响")



class IngestReportPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp + "-registry"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        environment = mock.patch.dict(
            os.environ, {"EXAMPREP_HOME": self.home}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_missing_answers_persisted_to_workspace(self):
        tmp = self.tmp
        data = {"course_name": "测试课", "phases": [
            {"phase_num": 1, "phase_name": "第一章", "wiki_filename": "ch01.md",
             "wiki_content": "# 第一章"}],
            "quiz_bank": [{"id": "q1", "type": "subjective", "question": "无答案的题?",
                           "source": "material", "ai_generated": False}]}
        in_path = os.path.join(tmp, "raw.json")
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r = run_ingest(data, tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.load(open(os.path.join(tmp, "ingest_report.json"), encoding="utf-8"))
        self.assertIn("q1", rep["missing_answer_ids"])            # 缺答案清单持久化，后续会话可接手

    def test_missing_answer_ids_match_assigned_ids(self):
        tmp = self.tmp
        data = {"course_name": "测试课", "phases": [
            {"phase_num": 1, "phase_name": "第一章", "wiki_filename": "ch01.md",
             "wiki_content": "# 第一章"}],
            "quiz_bank": [{"type": "subjective", "question": "没 id 也没答案的题?",
                           "source": "material", "ai_generated": False}]}
        in_path = os.path.join(tmp, "raw.json")
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        r = run_ingest(data, tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.load(open(os.path.join(tmp, "ingest_report.json"), encoding="utf-8"))
        bank = json.load(open(os.path.join(tmp, "references", "quiz_bank.json"), encoding="utf-8"))
        bank_ids = {q["id"] for q in bank}
        self.assertTrue(rep["missing_answer_ids"])                # 补号后清单指向真实题库 id
        self.assertTrue(set(rep["missing_answer_ids"]) <= bank_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
