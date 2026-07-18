import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ingestion.storage as storage_module
from scripts.ingestion import (
    ChapterPhaseMapping,
    ConflictError,
    ContentUnit,
    EvidenceRef,
    IngestionStore,
    PatchApplicationError,
    ReviewIssue,
    ReviewPatch,
    SchemaValidationError,
    SourceDriftError,
    SourceRecord,
    UnsafePathError,
    atomic_write_json,
    atomic_write_jsonl,
    make_source_id,
    normalize_workspace_path,
    read_json,
    read_jsonl,
    workspace_publication_lock,
)


ZERO_SHA = "0" * 64


class IngestionCoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.source_path = self.workspace / "materials" / "week01.pdf"
        self.source_path.parent.mkdir(parents=True)
        self.source_path.write_bytes(b"course material revision one")
        self.evidence_path = self.workspace / "scratch" / "pages" / "week01-p1.png"
        self.evidence_path.parent.mkdir(parents=True)
        self.evidence_path.write_bytes(b"fake but content-addressed png evidence")

        self.source = SourceRecord.from_file(
            self.workspace, "materials/week01.pdf", "application/pdf", status="parsed"
        )
        self.evidence = EvidenceRef.from_file(
            self.workspace, "scratch/pages/week01-p1.png"
        )
        self.store = IngestionStore(self.workspace)
        self.store.manifest.upsert(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def unit(
        self, kind, text, page, ordinal, asset=False, provenance="material",
        external_id=None,
    ):
        asset_path = "references/assets/week01-p1.png" if asset else None
        if asset:
            destination = self.workspace / asset_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"question asset")
        return ContentUnit.create(
            source_id=self.source.source_id,
            source_sha256=self.source.sha256,
            source_file=self.source.path,
            kind=kind,
            text=text,
            page=page,
            ordinal=ordinal,
            external_id=external_id,
            bbox=(10, 20 + ordinal, 500, 90 + ordinal),
            asset_path=asset_path,
            provenance=provenance,
        )

    def issue(self, reason, targets=(), status="pending"):
        issue = ReviewIssue.create(
            source_id=self.source.source_id,
            source_sha256=self.source.sha256,
            reason_codes=[reason],
            pages=[1],
            evidence=[self.evidence],
            target_unit_ids=targets,
            description="Review page one for %s" % reason,
            status=status,
        )
        self.store.review_queue.append(issue)
        return issue

    @staticmethod
    def replace_operation(current, replacement):
        return {
            "op": "replace_unit",
            "unit_id": current.unit_id,
            "expected_unit_sha256": hashlib.sha256(
                storage_module.canonical_json(current.to_dict()).encode("utf-8")
            ).hexdigest(),
            "unit": replacement.to_dict(),
        }

    def test_stable_ids_and_strict_model_round_trips(self):
        self.assertEqual(
            make_source_id("materials\\week01.pdf"),
            make_source_id("materials/week01.pdf"),
        )
        source_again = SourceRecord.from_dict(self.source.to_dict())
        self.assertEqual(self.source, source_again)

        unit_a = self.unit("page_anchor", "Page 1", 1, 0)
        unit_b = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "page_anchor",
            "Different presentation text does not alter locator identity",
            1,
            ordinal=0,
            bbox=(10.0, 20.0, 500.0, 90.0),
        )
        self.assertEqual(unit_a.unit_id, unit_b.unit_id)
        self.assertEqual(unit_a, ContentUnit.from_dict(unit_a.to_dict()))

        mapping = ChapterPhaseMapping.create(
            unit_a.unit_id, self.source.source_id, self.source.sha256,
            "Chapter 1", "Phase 1", "ch01", "phase01"
        )
        self.assertEqual(mapping, ChapterPhaseMapping.from_dict(mapping.to_dict()))

        issue_a = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["visual_question", "no_text"],
            [self.evidence],
            "Needs visual recovery",
            pages=[3, 1, 3],
            target_unit_ids=[unit_a.unit_id],
        )
        issue_b = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["no_text", "visual_question"],
            [self.evidence],
            "Localized wording may differ",
            pages=[1, 3],
            target_unit_ids=[unit_a.unit_id],
        )
        self.assertEqual(issue_a.issue_id, issue_b.issue_id)
        self.assertEqual(issue_a, ReviewIssue.from_dict(issue_a.to_dict()))

        operation_a = {"op": "add_unit", "unit": unit_a.to_dict()}
        operation_b = {"unit": unit_a.to_dict(), "op": "add_unit"}
        patch_a = ReviewPatch.create(
            issue_a.issue_id,
            self.source.source_id,
            self.source.sha256,
            [operation_a],
            [self.evidence],
        )
        patch_b = ReviewPatch.create(
            issue_a.issue_id,
            self.source.source_id,
            self.source.sha256,
            [operation_b],
            [self.evidence],
        )
        self.assertEqual(patch_a.patch_id, patch_b.patch_id)
        self.assertEqual(patch_a, ReviewPatch.from_dict(patch_a.to_dict()))

    def test_content_unit_asset_prompt_controls_are_typed_and_revision_bound(self):
        asset_sha = hashlib.sha256(b"complete prompt crop").hexdigest()
        unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "See the complete prompt image.",
            1,
            ordinal=19,
            external_id="prompt-q1",
            metadata={
                "quiz_type": "subjective",
                "assets": [{
                    "path": "references/assets/prompt-q1.png",
                    "role": "question_context",
                    "type": "crop_image",
                    "contains_full_prompt": True,
                    "sha256": asset_sha,
                    "source_sha256": self.source.sha256,
                    "source_file": self.source.path,
                }],
            },
        )
        restored = ContentUnit.from_dict(unit.to_dict())
        self.assertEqual("crop_image", restored.metadata["assets"][0]["type"])
        self.assertIs(True, restored.metadata["assets"][0]["contains_full_prompt"])
        self.assertEqual(asset_sha, restored.metadata["assets"][0]["sha256"])

        invalid_cases = (
            ("contains_full_prompt", "true", "must be a boolean"),
            ("type", "screenshot", "must be one of"),
            ("role", "figure", "requires role=question_context"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field):
                malformed = unit.to_dict()
                malformed["metadata"]["assets"][0][field] = value
                with self.assertRaisesRegex(SchemaValidationError, message):
                    ContentUnit.from_dict(malformed)

        unbound = unit.to_dict()
        unbound["metadata"]["assets"][0].pop("sha256")
        with self.assertRaisesRegex(SchemaValidationError, "exact asset sha256 revision"):
            ContentUnit.from_dict(unbound)

        noncanonical = unit.to_dict()
        noncanonical["metadata"]["assets"][0]["path"] = (
            "references\\assets\\prompt-q1.png"
        )
        with self.assertRaisesRegex(SchemaValidationError, "canonical POSIX separators"):
            ContentUnit.from_dict(noncanonical)

    def test_content_unit_preserves_receipted_crop_location_contract(self):
        asset_sha = hashlib.sha256(b"target-only crop").hexdigest()
        spec_sha = "b" * 64
        asset = {
            "path": "references/assets/q1_crop_%s.png" % spec_sha[:12],
            "role": "question_context",
            "type": "crop_image",
            "sha256": asset_sha,
            "source_file": self.source.path,
            "source_sha256": self.source.sha256,
            "source_page": 3,
            "source_bbox_pdf_points": [40.0, 120.0, 560.0, 410.0],
            "crop_receipt_id": "crop_" + "a" * 64,
            "crop_spec_sha256": spec_sha,
            "content_scope": "full_prompt",
            "isolation": "target_item_only",
            "contains_full_prompt": True,
        }
        unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "See the target-only crop.",
            3,
            ordinal=27,
            external_id="crop-q1",
            metadata={"quiz_type": "subjective", "assets": [asset]},
        )
        restored = ContentUnit.from_dict(unit.to_dict())
        self.assertEqual(asset, restored.metadata["assets"][0])

        for field, value, message in (
            ("source_page", 0, "integer >= 1"),
            ("isolation", "whole_page", "must be one of"),
            ("content_scope", "full_answer", "side=prompt"),
        ):
            with self.subTest(field=field):
                malformed = unit.to_dict()
                malformed["metadata"]["assets"][0][field] = value
                with self.assertRaisesRegex(SchemaValidationError, message):
                    ContentUnit.from_dict(malformed)

    def test_zxx_is_valid_only_for_formula_symbol_units(self):
        formula = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "formula", "", 1, ordinal=20, latex="V=IR",
            metadata={"source_language": "zxx"},
        )
        compact_text = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "text", "V=IR", 1, ordinal=21,
            metadata={"source_language": "zxx"},
        )
        numeric_answer = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "answer", "4", 1, ordinal=22, external_id="q1",
            metadata={"source_language": "zxx"},
        )
        result_set = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "formula", r"S=\{bbb,bbn,bnb,bnn\}", 1, ordinal=25,
            metadata={"source_language": "zxx"},
        )
        state_pair = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "formula", "(ma,ea)", 1, ordinal=26,
            metadata={"source_language": "zxx"},
        )
        four_symbol_outcomes = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "formula", r"B_1=\{ttth,ttht,thtt,httt\}", 1, ordinal=28,
            metadata={"source_language": "zxx"},
        )
        for unit in (
                formula, compact_text, numeric_answer, result_set, state_pair,
                four_symbol_outcomes):
            self.assertEqual("zxx", ContentUnit.from_dict(
                unit.to_dict()).metadata["source_language"])

        with self.assertRaisesRegex(SchemaValidationError, "formula/symbol-only"):
            ContentUnit.create(
                self.source.source_id, self.source.sha256, self.source.path,
                "question", "Use V=IR to calculate current.", 1, ordinal=23,
                external_id="q2", metadata={"source_language": "zxx"},
            )
        with self.assertRaisesRegex(SchemaValidationError, "formula/symbol-only"):
            ContentUnit.create(
                self.source.source_id, self.source.sha256, self.source.path,
                "code", "x=1", 1, ordinal=24,
                metadata={"source_language": "zxx"},
            )
        for text, latex in (
            ("P=1 otherwise 0", None),
            ("", r"P=1\;\text{for a valid result}"),
            ("use x=1", None),
        ):
            with self.subTest(text=text, latex=latex):
                with self.assertRaisesRegex(
                        SchemaValidationError, "formula/symbol-only"):
                    ContentUnit.create(
                        self.source.source_id, self.source.sha256, self.source.path,
                        "formula", text, 1, ordinal=27, latex=latex,
                        metadata={"source_language": "zxx"},
                    )

    def test_workspace_paths_reject_traversal_absolute_drive_and_unc(self):
        invalid = (
            "../secret.pdf",
            "materials/../../secret.pdf",
            "/etc/passwd",
            "C:\\course\\notes.pdf",
            "C:notes.pdf",
            "\\\\server\\share\\notes.pdf",
            "//server/share/notes.pdf",
            "materials//notes.pdf",
            "materials/./notes.pdf",
            " materials/notes.pdf",
            "materials./notes.pdf",
            "materials/notes.pdf.",
            "materials/dir /notes.pdf",
            "materials/NUL.txt",
            "materials/com1",
            "materials/LPT\u00b9.log",
            "materials/bad?.pdf",
            "materials/line\nbreak.pdf",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(UnsafePathError):
                    normalize_workspace_path(value)

        with self.assertRaises(UnsafePathError):
            SourceRecord.create("../x.pdf", ZERO_SHA, 0, "application/pdf")
        with self.assertRaises(SchemaValidationError):
            EvidenceRef("../evidence.png", ZERO_SHA)
        with self.assertRaises(UnsafePathError):
            ContentUnit.create(
                self.source.source_id,
                self.source.sha256,
                self.source.path,
                "figure",
                "",
                1,
                asset_path="C:\\outside.png",
            )

    def test_schema_and_status_validation_are_fail_closed(self):
        raw = self.source.to_dict()
        raw["unknown"] = True
        with self.assertRaises(SchemaValidationError):
            SourceRecord.from_dict(raw)

        raw = self.source.to_dict()
        raw["status"] = "done-ish"
        with self.assertRaises(SchemaValidationError):
            SourceRecord.from_dict(raw)

        raw = self.source.to_dict()
        raw["source_id"] = "src_" + "f" * 64
        with self.assertRaises(SchemaValidationError):
            SourceRecord.from_dict(raw)

        issue = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["no_text"],
            [self.evidence],
            "No extractable text",
        )
        bad_issue = issue.to_dict()
        bad_issue["status"] = "complete"
        with self.assertRaises(SchemaValidationError):
            ReviewIssue.from_dict(bad_issue)

        with self.assertRaises(SchemaValidationError):
            ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [{"op": "delete_unit", "unit_id": "unit_" + "0" * 64}],
                [self.evidence],
            )

    def test_quiz_metadata_extension_is_strict_and_round_trips(self):
        metadata = {
            "quiz_type": "code",
            "gradable": True,
            "question_text_status": "full",
            "diagram_type": "control_flow",
            "language": "python",
            "expected_behavior": "Return the sorted values.",
            "tests": ["assert solve([2, 1]) == [1, 2]"],
            "assets": [{
                "path": "references/assets/week01-p1.png",
                "role": "question_context",
                "source_file": "materials/week01.pdf",
                "source_sha256": self.source.sha256,
            }],
        }
        unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "Implement solve.",
            1,
            ordinal=7,
            external_id="code-q1",
            metadata=metadata,
        )
        self.assertEqual(metadata, ContentUnit.from_dict(unit.to_dict()).metadata)

        invalid = (
            ("gradable", 1),
            ("question_text_status", "partial"),
            ("diagram_type", " "),
            ("language", ["python"]),
            ("expected_behavior", " trailing "),
            ("tests", []),
            ("tests", ["  "]),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                broken = dict(metadata)
                broken[field] = value
                with self.assertRaises(SchemaValidationError):
                    ContentUnit.create(
                        self.source.source_id,
                        self.source.sha256,
                        self.source.path,
                        "question",
                        "Implement solve.",
                        1,
                        ordinal=8,
                        external_id="code-q2",
                        metadata=broken,
                    )

    def test_atomic_json_and_jsonl_round_trip(self):
        json_path = self.workspace / "scratch" / "atomic.json"
        jsonl_path = self.workspace / "scratch" / "atomic.jsonl"
        atomic_write_json(json_path, {"z": 1, "中文": [2, 3]})
        atomic_write_jsonl(jsonl_path, [{"id": 1}, {"id": 2}])
        self.assertEqual({"z": 1, "中文": [2, 3]}, read_json(json_path))
        self.assertEqual([{"id": 1}, {"id": 2}], read_jsonl(jsonl_path))
        self.assertEqual([], list(json_path.parent.glob(".*.tmp")))

    def test_manifest_and_review_queue_append_are_idempotent(self):
        self.assertFalse(self.store.manifest.upsert(self.source))
        parsed_again = SourceRecord.create(
            self.source.path,
            self.source.sha256,
            self.source.size_bytes,
            self.source.media_type,
            status="review_required",
        )
        self.assertTrue(self.store.manifest.upsert(parsed_again))
        self.assertFalse(self.store.manifest.upsert(parsed_again))

        issue = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["no_text"],
            [self.evidence],
            "First description",
        )
        self.assertTrue(self.store.review_queue.append(issue))
        self.assertFalse(self.store.review_queue.append(issue))
        conflict = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["no_text"],
            [self.evidence],
            "Different description, same immutable issue identity",
        )
        self.assertEqual(issue.issue_id, conflict.issue_id)
        with self.assertRaises(ConflictError):
            self.store.review_queue.append(conflict)

    def test_all_allow_list_operations_apply_and_replay_idempotently(self):
        question = self.unit(
            "question", "Old prompt", 1, 1, asset=True, external_id="q1"
        )
        answer = self.unit("answer", "42", 1, 2, external_id="q1")
        self.store.append_unit(question)
        self.store.append_unit(answer)

        added = self.unit("text", "AI recovered explanation", 1, 3, provenance="ai_recovered")
        replaced_question = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "Recovered full prompt",
            1,
            ordinal=1,
            external_id="q1",
            bbox=question.bbox,
            asset_path=question.asset_path,
            provenance="ai_recovered",
        )
        self.assertEqual(question.unit_id, replaced_question.unit_id)

        issue = self.issue("visual_question", [question.unit_id, answer.unit_id])
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [
                self.replace_operation(question, replaced_question),
                {"op": "add_unit", "unit": added.to_dict()},
                {"op": "assign_chapter", "unit_id": question.unit_id, "chapter": "Chapter 1",
                 "phase": "Phase 1", "chapter_id": "ch01", "phase_id": "phase01"},
                {"op": "pair_qa", "question_unit_id": question.unit_id, "answer_unit_id": answer.unit_id},
                {"op": "classify_asset", "unit_id": question.unit_id, "asset_role": "question_context"},
            ],
            [self.evidence],
            status="validated",
        )

        result = self.store.apply_patch(patch)
        self.assertTrue(result.applied)
        self.assertFalse(result.replayed)
        self.assertEqual("applied", result.issue_status)

        units = self.store.units()
        self.assertEqual("Recovered full prompt", units[question.unit_id].text)
        self.assertEqual("question_context", units[question.unit_id].asset_role)
        self.assertEqual(answer.unit_id, units[question.unit_id].paired_unit_id)
        self.assertEqual(question.unit_id, units[answer.unit_id].paired_unit_id)
        self.assertEqual("ch01", units[answer.unit_id].chapter_id)
        self.assertEqual("phase01", units[answer.unit_id].phase_id)
        self.assertIn(added.unit_id, units)
        self.assertEqual("Chapter 1", self.store.mappings()[question.unit_id].chapter)
        self.assertEqual("Chapter 1", self.store.mappings()[answer.unit_id].chapter)
        self.assertEqual("applied", self.store.review_queue.get(issue.issue_id).status)

        replay = self.store.apply_patch(patch)
        self.assertFalse(replay.applied)
        self.assertTrue(replay.replayed)
        self.assertEqual(1, len(read_jsonl(self.store.ledger_path)))

    def test_replace_unit_expected_digest_rejects_a_stale_authored_patch(self):
        original = self.unit("text", "Original value", 1, 21)
        self.store.append_unit(original)
        first_issue = self.issue("garbled_text", [original.unit_id])
        stale_issue = self.issue("no_text", [original.unit_id])
        expected_original = hashlib.sha256(
            storage_module.canonical_json(original.to_dict()).encode("utf-8")
        ).hexdigest()

        current = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "First reviewer value",
            1,
            ordinal=original.ordinal,
            bbox=original.bbox,
        )
        first_patch = ReviewPatch.create(
            first_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "replace_unit", "unit_id": original.unit_id,
              "expected_unit_sha256": expected_original, "unit": current.to_dict()}],
            [self.evidence],
            status="validated",
        )
        self.store.apply_patch(first_patch)

        stale_value = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Stale reviewer value",
            1,
            ordinal=original.ordinal,
            bbox=original.bbox,
        )
        stale_patch = ReviewPatch.create(
            stale_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "replace_unit", "unit_id": original.unit_id,
              "expected_unit_sha256": expected_original,
              "unit": stale_value.to_dict()}],
            [self.evidence],
            status="validated",
        )
        with self.assertRaisesRegex(ConflictError, "changed after the patch was authored"):
            self.store.apply_patch(stale_patch)
        self.assertEqual("First reviewer value", self.store.units()[original.unit_id].text)
        self.assertEqual("pending", self.store.review_queue.get(stale_issue.issue_id).status)

    def test_new_replace_unit_patch_requires_expected_digest_on_every_entry_path(self):
        original = self.unit("text", "Original value", 1, 22)
        self.store.append_unit(original)
        issue = self.issue("garbled_text", [original.unit_id])
        replacement = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Recovered value",
            1,
            ordinal=original.ordinal,
            bbox=original.bbox,
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "replace_unit",
                "unit_id": original.unit_id,
                "unit": replacement.to_dict(),
            }],
            [self.evidence],
            status="validated",
        )

        for operation in (
                lambda: self.store.validate_patch(patch),
                lambda: self.store.validate_patches([patch]),
                lambda: self.store.apply_patch(patch),
                lambda: self.store.apply_patches([patch])):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                        PatchApplicationError,
                        "must bind expected_unit_sha256"):
                    operation()
                self.assertEqual(
                    "Original value", self.store.units()[original.unit_id].text
                )
                self.assertEqual(
                    "pending", self.store.review_queue.get(issue.issue_id).status
                )
                self.assertFalse(self.store.ledger_path.exists())
                self.assertFalse(self.store.pending_patch_path.exists())

    def test_legacy_ledger_replace_without_expected_digest_remains_replayable(self):
        original = self.unit("text", "Legacy original", 1, 23)
        self.store.append_unit(original)
        issue = self.issue("garbled_text", [original.unit_id], status="applied")
        replacement = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Legacy recovered",
            1,
            ordinal=original.ordinal,
            bbox=original.bbox,
        )
        legacy_patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "replace_unit",
                "unit_id": original.unit_id,
                "unit": replacement.to_dict(),
            }],
            [self.evidence],
            status="validated",
        )
        atomic_write_jsonl(self.store.ledger_path, [{
            "patch_id": legacy_patch.patch_id,
            "fingerprint": self.store._patch_fingerprint(legacy_patch),
            "issue_id": legacy_patch.issue_id,
            "source_id": legacy_patch.source_id,
            "source_sha256": legacy_patch.source_sha256,
            "source_revisions": self.store._declared_patch_source_revisions(
                legacy_patch
            ),
            "patch": legacy_patch.to_dict(),
        }])

        expected_units, _mappings = self.store._expected_compiled_state()
        self.assertEqual("Legacy recovered", expected_units[original.unit_id].text)
        self.store.rebuild_compiled_from_ledger()
        self.assertEqual(
            "Legacy recovered", self.store.units()[original.unit_id].text
        )
        replay = self.store.apply_patch(legacy_patch)
        self.assertFalse(replay.applied)
        self.assertTrue(replay.replayed)
        self.assertEqual(1, len(read_jsonl(self.store.ledger_path)))

    def test_batch_apply_reconstructs_ledger_once_and_replays_idempotently(self):
        originals = [
            self.unit("text", "Old text one", 1, 31),
            self.unit("text", "Old text two", 1, 32),
        ]
        for unit in originals:
            self.store.append_unit(unit)

        patches = []
        for index, unit in enumerate(originals, 1):
            issue = self.issue("garbled_text", [unit.unit_id])
            replacement = ContentUnit.create(
                self.source.source_id,
                self.source.sha256,
                self.source.path,
                "text",
                "Recovered text %d" % index,
                1,
                ordinal=unit.ordinal,
                bbox=unit.bbox,
            )
            self.assertEqual(unit.unit_id, replacement.unit_id)
            patches.append(ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [self.replace_operation(unit, replacement)],
                [self.evidence],
                status="validated",
            ))

        truth_paths = (
            self.store.units_path,
            self.store.mappings_path,
            self.store.ledger_path,
            self.store.review_queue.path,
            self.store.manifest.path,
        )
        before = {
            path: path.read_bytes() if path.exists() else None
            for path in truth_paths
        }
        with mock.patch.object(
                self.store, "_expected_compiled_state",
                wraps=self.store._expected_compiled_state) as reconstruct:
            validated = self.store.validate_patches(patches)
        self.assertEqual(tuple(patches), validated)
        self.assertEqual(1, reconstruct.call_count)
        self.assertEqual(before, {
            path: path.read_bytes() if path.exists() else None
            for path in truth_paths
        })

        with mock.patch.object(
                self.store, "_expected_compiled_state",
                wraps=self.store._expected_compiled_state) as reconstruct:
            results = self.store.apply_patches(patches)
        self.assertEqual(1, reconstruct.call_count)
        self.assertTrue(all(result.applied for result in results))
        self.assertEqual(
            ["Recovered text 1", "Recovered text 2"],
            [self.store.units()[unit.unit_id].text for unit in originals],
        )
        self.assertEqual(2, len(read_jsonl(self.store.ledger_path)))

        with mock.patch.object(
                self.store, "_expected_compiled_state",
                wraps=self.store._expected_compiled_state) as reconstruct:
            replayed = self.store.apply_patches(patches)
        self.assertEqual(1, reconstruct.call_count)
        self.assertTrue(all(result.replayed for result in replayed))
        self.assertEqual(2, len(read_jsonl(self.store.ledger_path)))

    def test_batch_apply_keeps_prior_commits_when_a_later_patch_is_invalid(self):
        first = self.unit("text", "First old value", 1, 41)
        second = self.unit("text", "Second unchanged value", 1, 42)
        self.store.append_unit(first)
        self.store.append_unit(second)
        first_issue = self.issue("garbled_text", [first.unit_id])
        second_issue = self.issue("garbled_text", [second.unit_id])
        recovered = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "First recovered value",
            1,
            ordinal=first.ordinal,
            bbox=first.bbox,
        )
        first_patch = ReviewPatch.create(
            first_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [self.replace_operation(first, recovered)],
            [self.evidence],
            status="validated",
        )
        ineffective = ReviewPatch.create(
            second_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [self.replace_operation(second, second)],
            [self.evidence],
            status="validated",
        )

        before = {
            "units": self.store.units_path.read_bytes(),
            "queue": self.store.review_queue.path.read_bytes(),
            "ledger": (
                self.store.ledger_path.read_bytes()
                if self.store.ledger_path.exists() else None
            ),
        }
        with self.assertRaises(PatchApplicationError):
            self.store.validate_patches([first_patch, ineffective])
        self.assertEqual(before, {
            "units": self.store.units_path.read_bytes(),
            "queue": self.store.review_queue.path.read_bytes(),
            "ledger": (
                self.store.ledger_path.read_bytes()
                if self.store.ledger_path.exists() else None
            ),
        })
        self.assertEqual("First old value", self.store.units()[first.unit_id].text)
        self.assertEqual("pending", self.store.review_queue.get(first_issue.issue_id).status)

        with self.assertRaises(PatchApplicationError):
            self.store.apply_patches([first_patch, ineffective])

        self.assertEqual("First recovered value", self.store.units()[first.unit_id].text)
        self.assertEqual("applied", self.store.review_queue.get(first_issue.issue_id).status)
        self.assertEqual("pending", self.store.review_queue.get(second_issue.issue_id).status)
        self.assertEqual(1, len(read_jsonl(self.store.ledger_path)))
        self.assertFalse(self.store.pending_patch_path.exists())

    def test_batch_snapshot_bounds_queue_parsing_and_asset_hashes(self):
        asset_path = self.workspace / "references" / "assets" / "shared.png"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"shared immutable asset")
        asset_sha = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        asset_unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "Prompt with a shared asset",
            1,
            ordinal=50,
            external_id="asset-q",
            metadata={
                "assets": [{
                    "path": "references/assets/shared.png",
                    "role": "question_context",
                    "sha256": asset_sha,
                    "source_sha256": self.source.sha256,
                }],
            },
        )
        self.store.append_unit(asset_unit)

        originals = [
            self.unit("text", "Batch old one", 1, 51),
            self.unit("text", "Batch old two", 1, 52),
        ]
        patches = []
        for index, unit in enumerate(originals, 1):
            self.store.append_unit(unit)
            issue = self.issue("garbled_text", [unit.unit_id])
            replacement = ContentUnit.create(
                self.source.source_id,
                self.source.sha256,
                self.source.path,
                "text",
                "Batch recovered %d" % index,
                1,
                ordinal=unit.ordinal,
                bbox=unit.bbox,
            )
            patches.append(ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [self.replace_operation(unit, replacement)],
                [self.evidence],
                status="validated",
            ))

        def assert_bounded(operation):
            with mock.patch.object(
                    storage_module, "stable_file_sha256",
                    wraps=storage_module.stable_file_sha256) as digest, mock.patch.object(
                    storage_module.ReviewQueue, "_issues_from_rows",
                    wraps=storage_module.ReviewQueue._issues_from_rows) as parse_queue:
                operation(patches)
            asset_calls = [
                call for call in digest.call_args_list
                if Path(call.args[0]).resolve() == asset_path.resolve()
            ]
            self.assertEqual(2, len(asset_calls))
            self.assertEqual(1, parse_queue.call_count)

        assert_bounded(self.store.validate_patches)
        assert_bounded(self.store.apply_patches)

    def test_batch_apply_revalidation_catches_source_evidence_and_asset_drift_without_writes(self):
        asset_path = self.workspace / "references" / "assets" / "drift.png"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_bytes = b"stable asset bytes"
        asset_path.write_bytes(asset_bytes)
        asset_sha = hashlib.sha256(asset_bytes).hexdigest()
        original = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "Old prompt",
            1,
            ordinal=53,
            external_id="drift-q",
            metadata={
                "assets": [{
                    "path": "references/assets/drift.png",
                    "role": "question_context",
                    "sha256": asset_sha,
                    "source_sha256": self.source.sha256,
                }],
            },
        )
        self.store.append_unit(original)
        issue = self.issue("garbled_text", [original.unit_id])
        replacement = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            "Recovered prompt",
            1,
            ordinal=original.ordinal,
            external_id=original.external_id,
            metadata=original.metadata,
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [self.replace_operation(original, replacement)],
            [self.evidence],
            status="validated",
        )
        original_bytes = {
            self.source_path: self.source_path.read_bytes(),
            self.evidence_path: self.evidence_path.read_bytes(),
            asset_path: asset_bytes,
        }
        truth_paths = (
            self.store.units_path,
            self.store.mappings_path,
            self.store.ledger_path,
            self.store.review_queue.path,
            self.store.manifest.path,
        )

        for drift_path in (self.source_path, self.evidence_path, asset_path):
            with self.subTest(path=str(drift_path)):
                for path, payload in original_bytes.items():
                    path.write_bytes(payload)
                before = {
                    path: path.read_bytes() if path.exists() else None
                    for path in truth_paths
                }
                original_apply = self.store._apply_operations_to_state
                mutated = [False]

                def mutate_after_validation(*args, **kwargs):
                    result = original_apply(*args, **kwargs)
                    if not mutated[0]:
                        drift_path.write_bytes(original_bytes[drift_path] + b" drift")
                        mutated[0] = True
                    return result

                with mock.patch.object(
                        self.store, "_apply_operations_to_state",
                        side_effect=mutate_after_validation):
                    with self.assertRaises(SourceDriftError):
                        self.store.apply_patches([patch])

                self.assertTrue(mutated[0])
                self.assertEqual(before, {
                    path: path.read_bytes() if path.exists() else None
                    for path in truth_paths
                })
                self.assertFalse(self.store.pending_patch_path.exists())
                self.assertEqual("pending", self.store.review_queue.get(issue.issue_id).status)

        for path, payload in original_bytes.items():
            path.write_bytes(payload)

    def test_batch_validation_detects_combined_identity_conflict_without_writes(self):
        questions = [
            self.unit("question", "First recovered prompt", 1, 61,
                      external_id="shared-question-id"),
            self.unit("question", "Second recovered prompt", 1, 62,
                      external_id="shared-question-id"),
        ]
        issues = [self.issue("visual_question"), self.issue("no_text")]
        patches = [
            ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [{"op": "add_unit", "unit": question.to_dict()}],
                [self.evidence],
                status="validated",
            )
            for issue, question in zip(issues, questions)
        ]

        for patch in patches:
            self.store.validate_patch(patch)
        before_units = dict(self.store.units())
        before_queue = self.store.review_queue.path.read_bytes()
        with self.assertRaisesRegex(ConflictError, "external_id is not unique"):
            self.store.validate_patches(patches)
        self.assertEqual(before_units, self.store.units())
        self.assertEqual(before_queue, self.store.review_queue.path.read_bytes())
        self.assertFalse(self.store.ledger_path.exists())

    def test_ledger_replay_rejects_an_invalid_prefix_even_if_a_later_patch_repairs_it(self):
        original = self.unit(
            "question", "Original prompt", 1, 63,
            external_id="shared-prefix-id",
        )
        duplicate = self.unit(
            "question", "Recovered duplicate prompt", 1, 64,
            external_id="shared-prefix-id",
        )
        repaired = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "question",
            duplicate.text,
            1,
            ordinal=duplicate.ordinal,
            external_id="repaired-prefix-id",
            bbox=duplicate.bbox,
        )
        self.assertEqual(duplicate.unit_id, repaired.unit_id)
        self.store.append_unit(original)
        add_issue = self.issue("visual_question", status="applied")
        repair_issue = self.issue(
            "garbled_text", [duplicate.unit_id], status="applied"
        )
        patches = [
            ReviewPatch.create(
                add_issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [{"op": "add_unit", "unit": duplicate.to_dict()}],
                [self.evidence],
                status="validated",
            ),
            ReviewPatch.create(
                repair_issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [{"op": "replace_unit", "unit_id": duplicate.unit_id,
                  "unit": repaired.to_dict()}],
                [self.evidence],
                status="validated",
            ),
        ]
        rows = []
        for patch in patches:
            rows.append({
                "patch_id": patch.patch_id,
                "fingerprint": self.store._patch_fingerprint(patch),
                "issue_id": patch.issue_id,
                "source_id": patch.source_id,
                "source_sha256": patch.source_sha256,
                "source_revisions": self.store._declared_patch_source_revisions(patch),
                "patch": patch.to_dict(),
            })
        atomic_write_jsonl(self.store.ledger_path, rows)

        with self.assertRaisesRegex(ConflictError, "external_id is not unique"):
            self.store._expected_compiled_state()

    def test_validation_never_recovers_pending_ingestion(self):
        original = self.unit("text", "Unreadable value", 1, 71)
        self.store.append_unit(original)
        issue = self.issue("garbled_text", [original.unit_id])
        recovered = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Recovered value",
            1,
            ordinal=original.ordinal,
            bbox=original.bbox,
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [self.replace_operation(original, recovered)],
            [self.evidence],
            status="validated",
        )
        sentinel = self.workspace / "interrupted-target.json"
        sentinel.write_text("must survive validation", encoding="utf-8")
        transaction = self.workspace / ".ingest" / "transactions" / "pending-test"
        transaction.mkdir(parents=True)
        atomic_write_json(self.store.pending_ingest_path, {
            "schema_version": 1,
            "transaction_dir": ".ingest/transactions/pending-test",
            "targets": [{"path": "interrupted-target.json", "backup": None}],
        })

        with self.assertRaisesRegex(ConflictError, "requires recovery before validation"):
            self.store.validate_patch(patch)
        with self.assertRaisesRegex(ConflictError, "requires recovery before validation"):
            self.store.validate_patches([patch])
        self.assertTrue(sentinel.exists())
        self.assertTrue(self.store.pending_ingest_path.exists())
        self.assertTrue(transaction.exists())

    def test_hard_crash_restores_material_blocker_and_all_targets(self):
        old_target = self.workspace / "old-target.txt"
        new_target = self.workspace / "new-target.txt"
        old_target.write_bytes(b"old generation")
        self.store.material_build_pending_path.parent.mkdir(
            parents=True, exist_ok=True
        )
        self.store.material_build_pending_path.write_bytes(b"pending generation")
        script = (
            "import os; from pathlib import Path; "
            "from scripts.ingestion import IngestionStore; "
            "root=Path(%r); store=IngestionStore(root); "
            "old=root/'old-target.txt'; new=root/'new-target.txt'; "
            "lock=store.mutation_lock(allow_material_generation=True); "
            "lock.__enter__(); tx=store.ingest_transaction(["
            "'old-target.txt','new-target.txt',"
            "'.ingest/material_build_pending.json']); tx.__enter__(); "
            "old.write_bytes(b'partial generation'); "
            "new.write_bytes(b'new partial file'); "
            "store.material_build_pending_path.unlink(); os._exit(73)"
        ) % str(self.workspace)
        crashed = subprocess.run([sys.executable, "-B", "-c", script])
        self.assertEqual(73, crashed.returncode)
        self.assertEqual(b"partial generation", old_target.read_bytes())
        self.assertEqual(b"new partial file", new_target.read_bytes())
        self.assertFalse(self.store.material_build_pending_path.exists())
        self.assertTrue(self.store.pending_ingest_path.exists())

        with self.assertRaisesRegex(
                ConflictError, "requires recovery before validation"):
            with self.store.validation_lock():
                pass
        self.assertEqual(b"partial generation", old_target.read_bytes())

        # Mutation recovery runs first, then the restored material blocker
        # prevents this ordinary mutation from proceeding.
        with self.assertRaisesRegex(
                ConflictError, "material_build_pending.json"):
            with self.store.mutation_lock():
                pass
        self.assertEqual(b"old generation", old_target.read_bytes())
        self.assertFalse(new_target.exists())
        self.assertEqual(
            b"pending generation",
            self.store.material_build_pending_path.read_bytes(),
        )
        self.assertFalse(self.store.pending_ingest_path.exists())
        self.assertEqual([], list(self.store.transactions_path.iterdir()))

    def test_material_pending_blocks_ordinary_publication_lock(self):
        self.store.material_build_pending_path.parent.mkdir(
            parents=True, exist_ok=True
        )
        self.store.material_build_pending_path.write_bytes(b"pending")

        with self.assertRaisesRegex(
                ConflictError, "material_build_pending.json"):
            with workspace_publication_lock(self.workspace):
                pass
        with workspace_publication_lock(
                self.workspace, allow_material_generation=True):
            self.assertTrue(self.store.material_build_pending_path.exists())

    def test_assign_chapter_inherits_to_cross_source_paired_answer(self):
        solution_path = self.workspace / "materials" / "solutions.pdf"
        solution_path.write_bytes(b"official solution revision")
        solution = SourceRecord.from_file(
            self.workspace, "materials/solutions.pdf", "application/pdf", status="parsed"
        )
        self.store.manifest.upsert(solution)

        question = self.unit("question", "Prompt", 1, 21, external_id="split-q1")
        answer = ContentUnit.create(
            solution.source_id,
            solution.sha256,
            solution.path,
            "answer",
            "Official answer",
            3,
            ordinal=22,
            external_id="split-q1",
        )
        self.store.append_unit(question)
        self.store.append_unit(answer)
        issue = self.issue("chapter_ambiguous", [question.unit_id])
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [
                {
                    "op": "assign_chapter",
                    "unit_id": question.unit_id,
                    "chapter": "Chapter 1",
                    "phase": "Phase 1",
                    "chapter_id": "ch01",
                    "phase_id": "phase01",
                },
                {
                    "op": "pair_qa",
                    "question_unit_id": question.unit_id,
                    "answer_unit_id": answer.unit_id,
                    "source_revisions": sorted([
                        {
                            "source_id": question.source_id,
                            "source_sha256": question.source_sha256,
                        },
                        {
                            "source_id": answer.source_id,
                            "source_sha256": answer.source_sha256,
                        },
                    ], key=lambda row: row["source_id"]),
                },
            ],
            [self.evidence],
            status="validated",
        )

        self.store.apply_patch(patch)
        stored_answer = self.store.units()[answer.unit_id]
        answer_mapping = self.store.mappings()[answer.unit_id]
        self.assertEqual(("ch01", "phase01"), (
            stored_answer.chapter_id, stored_answer.phase_id,
        ))
        self.assertEqual(solution.source_id, answer_mapping.source_id)
        self.assertEqual(solution.sha256, answer_mapping.source_sha256)

    def test_cross_source_answer_drift_reopens_and_does_not_replay_pair(self):
        solution_path = self.workspace / "materials" / "solutions-drift.pdf"
        solution_path.write_bytes(b"official solution revision one")
        solution_v1 = SourceRecord.from_file(
            self.workspace, "materials/solutions-drift.pdf", "application/pdf",
            status="parsed",
        )
        self.store.manifest.upsert(solution_v1)
        question = self.unit(
            "question", "Prompt", 1, 121, external_id="split-drift-q1"
        )
        answer_v1 = ContentUnit.create(
            solution_v1.source_id,
            solution_v1.sha256,
            solution_v1.path,
            "answer",
            "Official answer v1",
            3,
            ordinal=122,
            external_id="split-drift-q1",
        )
        self.store.append_unit(question)
        self.store.append_unit(answer_v1)
        issue = self.issue("chapter_ambiguous", [question.unit_id])

        def pair_patch(answer, created_at):
            return ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [
                    {
                        "op": "assign_chapter",
                        "unit_id": question.unit_id,
                        "chapter": "Chapter 1",
                        "phase": "Phase 1",
                        "chapter_id": "ch01",
                        "phase_id": "phase01",
                    },
                    {
                        "op": "pair_qa",
                        "question_unit_id": question.unit_id,
                        "answer_unit_id": answer.unit_id,
                        "source_revisions": sorted([
                            {"source_id": question.source_id,
                             "source_sha256": question.source_sha256},
                            {"source_id": answer.source_id,
                             "source_sha256": answer.source_sha256},
                        ], key=lambda row: row["source_id"]),
                    },
                ],
                [self.evidence],
                reviewer="test",
                created_at=created_at,
                status="validated",
            )

        unbound = pair_patch(answer_v1, "2026-07-15T12:09:59Z").to_dict()
        unbound["operations"][1].pop("source_revisions")
        unbound = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            unbound["operations"],
            [self.evidence],
            reviewer="test",
            created_at="2026-07-15T12:09:59Z",
            status="validated",
        )
        with self.assertRaisesRegex(
                PatchApplicationError, "bind every touched source revision"):
            self.store.apply_patch(unbound)

        first_patch = pair_patch(answer_v1, "2026-07-15T12:10:00Z")
        self.store.apply_patch(first_patch)
        first_ledger = read_jsonl(self.store.ledger_path)
        self.assertEqual(2, len(first_ledger[0]["source_revisions"]))

        solution_path.write_bytes(b"official solution revision two")
        solution_v2 = SourceRecord.from_file(
            self.workspace, "materials/solutions-drift.pdf", "application/pdf",
            status="parsed",
        )
        self.store.manifest.upsert(solution_v2)
        answer_v2 = ContentUnit.create(
            solution_v2.source_id,
            solution_v2.sha256,
            solution_v2.path,
            "answer",
            "Official answer v2",
            3,
            ordinal=122,
            external_id="split-drift-q1",
        )
        self.assertEqual(answer_v1.unit_id, answer_v2.unit_id)
        base_units = self.store.base_units()
        base_units[answer_v2.unit_id] = answer_v2
        self.store.sync_base(base_units.values(), self.store.base_mappings().values())

        reopened = self.store.review_queue.get(issue.issue_id)
        current = self.store.units()
        self.assertEqual("pending", reopened.status)
        self.assertIsNone(current[question.unit_id].paired_unit_id)
        self.assertIsNone(current[answer_v2.unit_id].paired_unit_id)
        self.assertEqual("Official answer v2", current[answer_v2.unit_id].text)
        self.assertNotIn(question.unit_id, self.store.ledger_touched_unit_ids())

        second_patch = pair_patch(answer_v2, "2026-07-15T12:10:01Z")
        self.store.apply_patch(second_patch)
        current = self.store.units()
        self.assertEqual(answer_v2.unit_id, current[question.unit_id].paired_unit_id)
        self.assertEqual(question.unit_id, current[answer_v2.unit_id].paired_unit_id)
        self.assertEqual(2, len(read_jsonl(self.store.ledger_path)))

    def test_standalone_pair_inherits_existing_question_mapping(self):
        question = self.unit("question", "Prompt", 1, 23, external_id="later-pair-q1")
        answer = self.unit("answer", "Official answer", 1, 24, external_id="later-pair-q1")
        self.store.append_unit(question)
        self.store.append_unit(answer)

        chapter_issue = self.issue("chapter_ambiguous", [question.unit_id])
        self.store.apply_patch(ReviewPatch.create(
            chapter_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "assign_chapter",
                "unit_id": question.unit_id,
                "chapter": "Chapter 1",
                "phase": "Phase 1",
                "chapter_id": "ch01",
                "phase_id": "phase01",
            }],
            [self.evidence],
            status="validated",
        ))

        pair_issue = self.issue(
            "visual_question", [question.unit_id, answer.unit_id]
        )
        self.store.apply_patch(ReviewPatch.create(
            pair_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "pair_qa",
                "question_unit_id": question.unit_id,
                "answer_unit_id": answer.unit_id,
            }],
            [self.evidence],
            status="validated",
        ))

        stored = self.store.units()
        mappings = self.store.mappings()
        self.assertEqual(("ch01", "phase01"), (
            stored[answer.unit_id].chapter_id, stored[answer.unit_id].phase_id,
        ))
        self.assertEqual("Chapter 1", mappings[answer.unit_id].chapter)
        self.assertEqual("Phase 1", mappings[answer.unit_id].phase)
        self.assertEqual(2, len(read_jsonl(self.store.ledger_path)))

    def test_standalone_pair_mapping_conflict_is_atomic(self):
        question = self.unit("question", "Prompt", 1, 25, external_id="later-conflict-q1")
        answer = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "answer",
            "Official answer",
            1,
            ordinal=26,
            external_id="later-conflict-q1",
            chapter_id="ch02",
            phase_id="phase02",
        )
        self.store.append_unit(question)
        self.store.append_unit(answer)

        chapter_issue = self.issue("chapter_ambiguous", [question.unit_id])
        self.store.apply_patch(ReviewPatch.create(
            chapter_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "assign_chapter",
                "unit_id": question.unit_id,
                "chapter": "Chapter 1",
                "phase": "Phase 1",
                "chapter_id": "ch01",
                "phase_id": "phase01",
            }],
            [self.evidence],
            status="validated",
        ))
        pair_issue = self.issue(
            "visual_question", [question.unit_id, answer.unit_id]
        )
        pair_patch = ReviewPatch.create(
            pair_issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{
                "op": "pair_qa",
                "question_unit_id": question.unit_id,
                "answer_unit_id": answer.unit_id,
            }],
            [self.evidence],
            status="validated",
        )

        with self.assertRaises(ConflictError):
            self.store.apply_patch(pair_patch)
        stored = self.store.units()
        self.assertIsNone(stored[question.unit_id].paired_unit_id)
        self.assertIsNone(stored[answer.unit_id].paired_unit_id)
        self.assertEqual("ch02", stored[answer.unit_id].chapter_id)
        self.assertNotIn(answer.unit_id, self.store.mappings())
        self.assertEqual("pending", self.store.review_queue.get(pair_issue.issue_id).status)
        self.assertEqual(1, len(read_jsonl(self.store.ledger_path)))

    def test_assign_chapter_fails_closed_on_paired_answer_conflict(self):
        question = self.unit("question", "Prompt", 1, 31, external_id="conflict-q1")
        answer = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "answer",
            "Official answer",
            1,
            ordinal=32,
            external_id="conflict-q1",
            chapter_id="ch02",
            phase_id="phase02",
        )
        self.store.append_unit(question)
        self.store.append_unit(answer)
        issue = self.issue("chapter_ambiguous", [question.unit_id])
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [
                {
                    "op": "assign_chapter",
                    "unit_id": question.unit_id,
                    "chapter": "Chapter 1",
                    "phase": "Phase 1",
                    "chapter_id": "ch01",
                    "phase_id": "phase01",
                },
                {
                    "op": "pair_qa",
                    "question_unit_id": question.unit_id,
                    "answer_unit_id": answer.unit_id,
                },
            ],
            [self.evidence],
            status="validated",
        )

        with self.assertRaises(ConflictError):
            self.store.apply_patch(patch)
        self.assertIsNone(self.store.units()[question.unit_id].chapter_id)

    def test_patch_status_and_target_scope_are_checked(self):
        question = self.unit("question", "Prompt", 1, 1)
        other = self.unit("text", "Other", 1, 2)
        self.store.append_unit(question)
        self.store.append_unit(other)
        issue = self.issue("chapter_ambiguous", [question.unit_id])

        proposed = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "assign_chapter", "unit_id": question.unit_id, "chapter": "1", "phase": "1",
              "chapter_id": "ch01", "phase_id": "phase01"}],
            [self.evidence],
            status="proposed",
        )
        with self.assertRaises(PatchApplicationError):
            self.store.apply_patch(proposed)

        out_of_scope = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "assign_chapter", "unit_id": other.unit_id, "chapter": "1", "phase": "1",
              "chapter_id": "ch01", "phase_id": "phase01"}],
            [self.evidence],
            status="validated",
        )
        with self.assertRaises(PatchApplicationError):
            self.store.apply_patch(out_of_scope)
        self.assertEqual({}, self.store.mappings())

    def test_source_drift_rejects_patch_before_mutation(self):
        issue = self.issue("no_text")
        recovered = self.unit("text", "Recovered", 1, 9, provenance="ai_recovered")
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "add_unit", "unit": recovered.to_dict()}],
            [self.evidence],
            status="validated",
        )
        self.source_path.write_bytes(b"course material changed after review")
        with self.assertRaises(SourceDriftError):
            self.store.apply_patch(patch)
        self.assertEqual({}, self.store.units())
        self.assertEqual([], read_jsonl(self.store.ledger_path, default=[]))

    def test_evidence_drift_rejects_patch(self):
        issue = self.issue("no_text")
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "mark_unrecoverable", "reason": "Image is illegible"}],
            [self.evidence],
            status="validated",
        )
        self.evidence_path.write_bytes(b"evidence changed after review")
        with self.assertRaises(SourceDriftError):
            self.store.apply_patch(patch)
        self.assertEqual("pending", self.store.review_queue.get(issue.issue_id).status)

    def test_mark_unrecoverable_is_allow_list_terminal_operation(self):
        issue = self.issue("encrypted_source")
        patch = ReviewPatch.create(
            issue.issue_id,
            self.source.source_id,
            self.source.sha256,
            [{"op": "mark_unrecoverable", "reason": "Password was not provided"}],
            [self.evidence],
            status="validated",
        )
        result = self.store.apply_patch(patch)
        self.assertEqual("unrecoverable", result.issue_status)
        self.assertEqual("unrecoverable", self.store.review_queue.get(issue.issue_id).status)

        unit = self.unit("text", "Not permitted alongside terminal op", 1, 10)
        with self.assertRaises(SchemaValidationError):
            ReviewPatch.create(
                issue.issue_id,
                self.source.source_id,
                self.source.sha256,
                [
                    {"op": "mark_unrecoverable", "reason": "No"},
                    {"op": "add_unit", "unit": unit.to_dict()},
                ],
                [self.evidence],
            )


if __name__ == "__main__":
    unittest.main()
