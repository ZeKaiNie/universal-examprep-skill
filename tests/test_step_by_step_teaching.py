"""Focused regression coverage for the auditable step-by-step teaching mode."""

import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "list_teaching_examples.py")
NOTEBOOK = os.path.join(ROOT, "scripts", "notebook.py")
PROGRESS = os.path.join(ROOT, "scripts", "update_progress.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import notebook as notebook_engine  # noqa: E402
import i18n  # noqa: E402
import stable_ids  # noqa: E402
import update_progress  # noqa: E402


class StepByStepTeachingContract(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.join(ROOT, "step-teaching-" + uuid.uuid4().hex)
        os.makedirs(self.ws)
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, "references"))
        self.items = [
            {
                "id": "e1",
                "chapter": 1,
                "teaching_role": "worked_example",
                "gradable": False,
                "question": "Worked example one.",
                "answer": "Answer one.",
                "source": "material",
                "source_file": "slides.pdf",
                "source_pages": [1],
            },
            {
                "id": "e2",
                "chapter": 1,
                "teaching_role": "paired_problem",
                "gradable": False,
                "question": "Worked example two.",
                "answer": "Answer two.",
                "source": "material",
                "source_file": "slides.pdf",
                "source_pages": [2],
            },
        ]
        self._write_json("references/quiz_bank.json", [])
        self._write_json("references/teaching_examples.json", self.items)
        self._write_json(
            "references/teaching_baseline.json",
            {
                "schema_version": 1,
                "policy": "append_only",
                "teaching_example_ids": ["e1", "e2"],
                "teaching_example_ids_by_chapter": {"1": ["e1", "e2"]},
            },
        )
        self._write_json(
            "references/figure_page_index.json", {"wiki_visual_coverage": {}}
        )
        self._write_json(
            "references/image_question_index.json",
            {"prompt_suspects": [], "answer_suspects": []},
        )
        self._write_state()

    def _write_json(self, relative, value):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    def _read_json(self, relative):
        with open(
            os.path.join(self.ws, *relative.split("/")), encoding="utf-8"
        ) as stream:
            return json.load(stream)

    def _write_state(
        self,
        *,
        processing_mode="full",
        no_questions=False,
        phase_evidence=None,
        checklist_done=False,
    ):
        preferences = {"interaction_style": "step_by_step"}
        if no_questions:
            preferences["no_questions"] = True
        state = {
            "version": 1,
            "current_phase": 1,
            "scope": None,
            "mode": "from_scratch",
            "time_budget": "le1d",
            "language": "en",
            "artifact_mode": "chat",
            "processing_mode": processing_mode,
            "preferences": preferences,
            "mistake_archive": [],
            "confusion_log": [],
            "knowledge_window": [],
            "phase_checklist": [{"text": "Phase 1", "done": checklist_done}],
            "phase_evidence": phase_evidence or {},
            "last_updated": None,
        }
        self._write_json("study_state.json", state)

    def _run_list(self):
        return subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--workspace",
                self.ws,
                "--chapter",
                "1",
                "--next-pending",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _run_progress(self, *args):
        return subprocess.run(
            [sys.executable, PROGRESS, "--workspace", self.ws, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _add_walkthrough(self, ident, body, *, marked=True):
        title = "Example " + ident
        args = [
            sys.executable,
            NOTEBOOK,
            "--workspace",
            self.ws,
            "add-entry",
            "--chapter",
            "1",
            "--type",
            "walkthrough",
            "--id",
            ident,
            "--title",
            title,
        ]
        if marked:
            args.append("--teaching-example")
        result = subprocess.run(
            args,
            input=body,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return "notebook/ch01.md#" + notebook_engine.entry_anchor(ident, title)

    def _record(self, ident, notebook_ref):
        return self._run_progress(
            "record-taught-example", "--id", ident, "--notebook-ref", notebook_ref
        )

    def test_next_pending_uses_manifest_order_not_notebook_presence(self):
        self._add_walkthrough("e2", "Explanation for e2.")
        result = self._run_list()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["pending_ids"], ["e1", "e2"])
        self.assertEqual(payload["next"]["id"], "e1")
        self.assertFalse(payload["teaching_example_roster_exhausted"])

    def test_interaction_style_is_canonical_and_dormant_outside_full_route(self):
        state = self._read_json("study_state.json")
        self.assertEqual(
            i18n.workspace_effective_interaction_style(state), "step_by_step"
        )

        state.pop("processing_mode")
        self.assertEqual(i18n.workspace_effective_interaction_style(state), "batch")
        self.assertTrue(i18n.workspace_interaction_style_dormant(state))
        state["processing_mode"] = "lightweight"
        self.assertEqual(i18n.workspace_effective_interaction_style(state), "batch")
        state["processing_mode"] = "full"
        state["preferences"]["no_questions"] = True
        self.assertEqual(i18n.workspace_effective_interaction_style(state), "batch")

        self._write_state(processing_mode="lightweight")
        dormant = self._run_list()
        self.assertEqual(dormant.returncode, 2)
        self.assertIn(
            "requires effective interaction_style=step_by_step", dormant.stderr
        )
        self.assertIn("saved preference dormant", dormant.stderr)

        self._write_state()
        set_batch = self._run_progress("set", "--interaction-style", "batch")
        self.assertEqual(set_batch.returncode, 0, set_batch.stdout + set_batch.stderr)
        self.assertEqual(
            self._read_json("study_state.json")["preferences"]["interaction_style"],
            "batch",
        )
        bad_alias = self._run_progress(
            "set", "--pref", "interaction_style=step-by-step"
        )
        self.assertEqual(bad_alias.returncode, 2)
        self.assertEqual(
            self._read_json("study_state.json")["preferences"]["interaction_style"],
            "batch",
        )

    def test_record_binds_marker_hashes_and_stale_block_reenters_pending(self):
        plain_ref = self._add_walkthrough(
            "e1", "Explanation one without the evidence marker.", marked=False
        )
        self.assertEqual(self._record("e1", plain_ref).returncode, 2)

        e1_ref = self._add_walkthrough("e1", "Explanation one.")
        out_of_order = self._record(
            "e2", self._add_walkthrough("e2", "Explanation two.")
        )
        self.assertEqual(out_of_order.returncode, 2)

        recorded = self._record("e1", e1_ref)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        record = self._read_json("study_state.json")["phase_evidence"]["1"]
        self.assertEqual(record["teaching_examples"], ["e1"])
        self.assertEqual(record["notebook"], [e1_ref])
        binding = record["teaching_example_bindings"][0]
        self.assertEqual(
            set(binding),
            {
                "id",
                "notebook_ref",
                "notebook_block_sha256",
                "manifest_item_sha256",
            },
        )
        for field in ("notebook_block_sha256", "manifest_item_sha256"):
            self.assertRegex(binding[field], r"^[0-9a-f]{64}$")

        self.assertEqual(json.loads(self._run_list().stdout)["next"]["id"], "e2")
        notebook_path = os.path.join(self.ws, "notebook", "ch01.md")
        with open(notebook_path, encoding="utf-8") as stream:
            notebook_text = stream.read()
        with open(notebook_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                notebook_text.replace(
                    "Explanation one.", "Explanation one, revised."
                )
            )

        stale = self._run_list()
        self.assertEqual(stale.returncode, 0, stale.stdout + stale.stderr)
        stale_payload = json.loads(stale.stdout)
        self.assertEqual(stale_payload["next"]["id"], "e1")
        self.assertEqual(stale_payload["stale_binding_ids"], ["e1"])
        self.assertIn(
            "notebook_block_revision_changed",
            stale_payload["stale_binding_problems"][0]["problems"],
        )
        repaired = self._record("e1", e1_ref)
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)

    def test_append_only_new_item_reopens_completion_and_corruption_blocks(self):
        for ident in ("e1", "e2"):
            ref = self._add_walkthrough(ident, "Explanation for %s." % ident)
            recorded = self._record(ident, ref)
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

        state = self._read_json("study_state.json")
        state["phase_evidence"]["1"]["status"] = "covered_unverified"
        state["phase_evidence"]["1"]["completed_at"] = "2026-07-18 00:00"
        state["phase_checklist"][0]["done"] = True
        self._write_json("study_state.json", state)

        self.items.append(
            {
                "id": "e3",
                "chapter": 1,
                "teaching_role": "worked_example",
                "gradable": False,
                "question": "New append-only example.",
                "answer": "Answer three.",
                "source": "material",
                "source_file": "slides.pdf",
                "source_pages": [3],
            }
        )
        self._write_json("references/teaching_examples.json", self.items)
        self.assertEqual(json.loads(self._run_list().stdout)["pending_ids"], ["e3"])

        e3_ref = self._add_walkthrough("e3", "Explanation for e3.")
        recorded = self._record("e3", e3_ref)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        reopened = self._read_json("study_state.json")
        self.assertNotIn("status", reopened["phase_evidence"]["1"])
        self.assertNotIn("completed_at", reopened["phase_evidence"]["1"])
        self.assertFalse(reopened["phase_checklist"][0]["done"])
        self.assertTrue(
            json.loads(self._run_list().stdout)[
                "teaching_example_roster_exhausted"
            ]
        )

        notebook_path = os.path.join(self.ws, "notebook", "ch01.md")
        with open(notebook_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n```text\nunterminated\n")
        corrupt = self._run_list()
        self.assertEqual(corrupt.returncode, 2)
        self.assertIn("structurally invalid", corrupt.stderr)


class StableItemIdContract(unittest.TestCase):
    def test_safe_unicode_and_forbidden_boundaries(self):
        self.assertIsNone(stable_ids.stable_item_id_problem("例题-α_01"))
        invalid = (
            "bad id",
            "bad#id",
            "bad/id",
            "bad\\id",
            "bad|id",
            "bad`id",
            "bad[id]",
            "bad\u200bid",
            "x" * 201,
            "bad\ufdd0id",
            "bad\ufffdid",
        )
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.assertIsNotNone(stable_ids.stable_item_id_problem(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
