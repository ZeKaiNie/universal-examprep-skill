import json
import tempfile
import unittest
from pathlib import Path

from scripts.ingestion import ContentUnit, IngestionStore, ReviewPatch, render_answer_value
from scripts.ingestion.pipeline import (
    build_payload,
    compile_review_outputs,
    persist_payload,
)


class IngestionPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.materials = self.root / "materials"
        self.workspace = self.root / "workspace"
        self.materials.mkdir()
        self.workspace.mkdir()
        self.source = self.materials / "ch01.txt"
        self.source.write_text("Chapter 1\nCore concept", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, missing_answer=True):
        quiz = {
            "id": "q1",
            "chapter": 1,
            "type": "subjective",
            "question": "Explain the core concept.",
            "answer": "" if missing_answer else "Official answer",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
            "answer_source_pages": [1],
        }
        return build_payload(
            str(self.materials),
            [str(self.source)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Chapter 1\nCore concept"},
                {"file": "ch01.txt", "page": 2, "text": ""},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("ch01.txt", 2)],
            }],
            quiz_items=[quiz],
            report={
                "warnings": [],
                "skipped": [],
                "ai_review": [{
                    "kind": "pages_no_text",
                    "file": "ch01.txt",
                    "pages": [2],
                    "action": "Visually inspect page 2.",
                }],
            },
        )

    def test_payload_accounts_for_blank_pages_and_external_source_root(self):
        payload = self.payload()
        self.assertEqual(str(self.materials.resolve()), payload["source_root"])
        anchors = [
            row for row in payload["content_units"] if row["kind"] == "page_anchor"
        ]
        self.assertEqual([1, 2], sorted(row["page"] for row in anchors))
        self.assertEqual("review_required", payload["sources"][0]["status"])
        page_two = next(row for row in payload["page_quality"] if row["page"] == 2)
        self.assertEqual("review", page_two["route"])

        first = persist_payload(self.workspace, payload)
        second = persist_payload(self.workspace, payload)
        self.assertEqual(first["source_count"], second["source_count"])
        store = IngestionStore(self.workspace, source_root=self.materials)
        self.assertEqual(2, len([u for u in store.units().values() if u.kind == "page_anchor"]))
        self.assertGreaterEqual(len(store.review_queue.issues()), 2)
        store.manifest.verify_current(payload["sources"][0]["source_id"], payload["sources"][0]["sha256"])

    def test_source_drift_is_rejected_at_persist_boundary(self):
        payload = self.payload()
        self.source.write_text("changed after extraction", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source changed"):
            persist_payload(self.workspace, payload)

    def test_late_invalid_candidate_cannot_partially_replace_authoritative_state(self):
        payload = self.payload()
        persist_payload(self.workspace, payload)
        ingest_root = self.workspace / ".ingest"
        before = {
            str(path.relative_to(ingest_root)): path.read_bytes()
            for path in ingest_root.rglob("*") if path.is_file()
        }

        changed = json.loads(json.dumps(payload))
        text_unit = next(row for row in changed["content_units"] if row["kind"] == "text")
        text_unit["text"] = "MUTATED BEFORE A LATE VALIDATION FAILURE"
        changed["review_candidates"].append({
            "source_file": "unknown-source.txt",
            "reason_codes": ["review_required"],
            "pages": [1],
            "target_unit_ids": [],
        })
        with self.assertRaisesRegex(ValueError, "unknown source"):
            persist_payload(self.workspace, changed)

        after = {
            str(path.relative_to(ingest_root)): path.read_bytes()
            for path in ingest_root.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_ingest_transaction_rolls_back_all_registered_files(self):
        payload = self.payload()
        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        original = store.base_units_path.read_bytes()
        with store.mutation_lock():
            with self.assertRaisesRegex(RuntimeError, "simulated commit failure"):
                with store.ingest_transaction([store.BASE_UNITS_PATH]):
                    store.base_units_path.write_text("partial\n", encoding="utf-8")
                    raise RuntimeError("simulated commit failure")
        self.assertEqual(original, store.base_units_path.read_bytes())
        self.assertFalse(store.pending_ingest_path.exists())

    def test_global_alerts_enter_the_typed_queue_and_are_deduplicated(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1, "text": "Chapter 1\nCore concept"}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={
                "warnings": ["wiki_empty: no lecture content entered the wiki"],
                "skipped": [],
                "ai_review": [{
                    "kind": "wiki_empty",
                    "file": "(all)",
                    "action": "Inspect every source and recover the lecture content.",
                }],
            },
        )
        self.assertEqual([], payload["unbound_review_candidates"])
        matching = [
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["wiki_empty"]
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("ch01.txt", matching[0]["source_file"])
        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        self.assertEqual(
            1,
            len([
                issue for issue in store.review_queue.issues()
                if issue.reason_codes == ("wiki_empty",)
            ]),
        )

    def test_non_gradable_legacy_item_creates_no_missing_answer_review(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1,
              "text": "Chapter 1\nCompleted demonstration"}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "worked-only", "chapter": 1, "type": "subjective",
                "question": "Completed demonstration", "answer": "",
                "gradable": False, "source_file": "ch01.txt", "source_pages": [1],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        self.assertFalse(any(
            "missing_answer" in candidate["reason_codes"]
            for candidate in payload["review_candidates"]
        ))

    def test_typed_answers_keep_value_and_deterministic_display_text(self):
        for index, value in enumerate((False, 0, ["A", 2], {"z": 1, "a": 2})):
            with self.subTest(value=value):
                payload = build_payload(
                    str(self.materials),
                    [str(self.source)],
                    [{"file": "ch01.txt", "page": 1, "text": "Chapter 1\nCore concept"}],
                    sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
                    quiz_items=[{
                        "id": "typed-%d" % index,
                        "chapter": 1,
                        "type": "subjective",
                        "question": "Return the typed value.",
                        "answer": value,
                        "source": "material",
                        "source_file": "ch01.txt",
                        "source_pages": [1],
                    }],
                    report={"warnings": [], "skipped": [], "ai_review": []},
                )
                answer = next(
                    row for row in payload["content_units"] if row["kind"] == "answer"
                )
                self.assertEqual(value, answer["metadata"]["answer_value"])
                self.assertEqual(render_answer_value(value), answer["text"])
                target = self.root / ("typed-workspace-%d" % index)
                target.mkdir()
                persist_payload(target, payload)

    def test_split_question_and_answer_sources_keep_page_metadata_owned(self):
        solutions = self.materials / "solutions.txt"
        solutions.write_text("Official solutions\nAnswer on page three", encoding="utf-8")
        payload = build_payload(
            str(self.materials),
            [str(self.source), str(solutions)],
            [
                {"file": "ch01.txt", "page": 1,
                 "text": "Chapter 1\nExplain the core concept."},
                {"file": "solutions.txt", "page": 3,
                 "text": "Official solutions\nThe core concept is ..."},
            ],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "split-q1", "chapter": 1, "type": "subjective",
                "question": "Explain the core concept.",
                "answer": "The core concept is ...", "source": "material",
                "source_file": "ch01.txt", "source_pages": [1],
                "answer_source_file": "solutions.txt", "answer_source_pages": [3],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = next(row for row in payload["content_units"]
                        if row["kind"] == "question")
        answer = next(row for row in payload["content_units"]
                      if row["kind"] == "answer")
        self.assertEqual([1], question["metadata"]["source_pages"])
        self.assertNotIn("source_pages", answer["metadata"])
        self.assertEqual([3], answer["metadata"]["answer_source_pages"])
        self.assertEqual("solutions.txt", answer["source_file"])

        # This is the strict boundary that previously rejected the answer unit
        # because question page 1 did not belong to solutions.txt.
        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        stored_answer = next(unit for unit in store.units().values()
                             if unit.kind == "answer" and unit.external_id == "split-q1")
        self.assertEqual("solutions.txt", stored_answer.source_file)
        self.assertNotIn("source_pages", stored_answer.metadata)
        self.assertEqual([3], stored_answer.metadata["answer_source_pages"])

    def test_validated_answer_patch_compiles_and_survives_base_resync(self):
        payload = self.payload()
        persist_payload(self.workspace, payload)
        wiki = self.workspace / "references" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "ch01.md").write_text("# Chapter 1\n\nCore concept\n", encoding="utf-8")
        quiz_path = self.workspace / "references" / "quiz_bank.json"
        quiz_path.write_text(json.dumps([{
            "id": "q1",
            "chapter": 1,
            "type": "subjective",
            "question": "Explain the core concept.",
            "answer": "",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
        }]), encoding="utf-8")
        (self.workspace / "ingest_report.json").write_text(
            json.dumps({"missing_answer_ids": ["q1"]}), encoding="utf-8"
        )

        store = IngestionStore(self.workspace, source_root=self.materials)
        question = next(
            unit for unit in store.units().values()
            if unit.kind == "question" and unit.external_id == "q1"
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
            "AI recovered answer",
            question.page,
            ordinal=question.ordinal + 1,
            external_id="q1",
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
        wiki_before = (wiki / "ch01.md").read_bytes()
        compiled = compile_review_outputs(self.workspace)
        self.assertEqual(wiki_before, (wiki / "ch01.md").read_bytes())
        self.assertGreater(compiled["retrieval_chunks"], 0)
        bank = json.loads(quiz_path.read_text(encoding="utf-8"))
        self.assertEqual("AI recovered answer", bank[0]["answer"])
        self.assertEqual("material", bank[0]["source"])
        self.assertEqual("ai_recovered", bank[0]["answer_provenance"])
        report = json.loads((self.workspace / "ingest_report.json").read_text(encoding="utf-8"))
        self.assertEqual([], report["missing_answer_ids"])

        # A fresh parser snapshot with the same source revision retains ledger-touched
        # compiled units instead of throwing the expensive review away.
        store.sync_base(payload["content_units"], payload["mappings"])
        preserved = store.units()
        self.assertIn(answer.unit_id, preserved)
        self.assertEqual(answer.unit_id, preserved[question.unit_id].paired_unit_id)
        # Simulate a same-source ingest rerun overwriting derived artifacts from raw input.
        quiz_path.write_text(json.dumps([{
            "id": "q1",
            "chapter": 1,
            "type": "subjective",
            "question": "Explain the core concept.",
            "answer": "",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
        }]), encoding="utf-8")
        (self.workspace / "ingest_report.json").write_text(
            json.dumps({"missing_answer_ids": ["q1"]}), encoding="utf-8"
        )
        compile_review_outputs(self.workspace)
        rebuilt_bank = json.loads(quiz_path.read_text(encoding="utf-8"))
        self.assertEqual("AI recovered answer", rebuilt_bank[0]["answer"])
        self.assertEqual([], json.loads(
            (self.workspace / "ingest_report.json").read_text(encoding="utf-8")
        )["missing_answer_ids"])
        index = json.loads(
            (self.workspace / "references" / "retrieval_index.json").read_text(encoding="utf-8")
        )
        self.assertEqual(2, index["version"])
        self.assertIn("content_units", index["integrity"])


if __name__ == "__main__":
    unittest.main()
