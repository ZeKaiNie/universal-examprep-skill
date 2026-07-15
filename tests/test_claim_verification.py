import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import study_guide_content as study_guide_content
from scripts import verify_claims
from scripts.ingestion import dedup as ingestion_dedup
from scripts.ingestion import IngestionStore, ReviewPatch
from scripts.ingestion.claims import (
    ClaimRecord,
    ClaimSource,
    ClaimSubject,
    ClaimValidationError,
    QuoteSpan,
    canonical_fact_snapshot_sha256,
    canonical_manifest_sha256,
    compile_claim_proposals,
    import_claim_records,
    load_claim_records,
    payload_sha256,
    read_claim_jsonl,
    validate_claim_subject_bindings,
    validate_guide_claim_coverage,
    verify_claim,
    verify_claim_records,
)
from scripts.ingestion.facts import FactEvidenceRef, SourcePriority, UnitRevisionRef
from scripts.ingestion.dedup import (
    load_source_conflicts,
    validate_workspace_fact_integrity,
)
from scripts.ingestion.identifiers import canonical_json, file_sha256
from scripts.ingestion.models import ContentUnit, SourceRecord
from scripts.ingestion.pipeline import build_payload, persist_payload
from scripts.ingestion.storage import atomic_write_json, atomic_write_jsonl, read_json


class ClaimVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        source_path = self.workspace / "materials" / "a.txt"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("authoritative source", encoding="utf-8")
        self.source = SourceRecord.from_file(
            self.workspace, "materials/a.txt", "text/plain", status="complete"
        )
        self.unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "前缀😀树结构保持有序，suffix",
            1,
            ordinal=1,
            chapter_id="ch01",
        )

    def tearDown(self):
        self.temp.cleanup()

    def claim(
        self,
        unit=None,
        field="explanation",
        role="concept_evidence",
        claim_text="树结构的作者解释。",
        entity_id="kp1",
        claim_index=0,
    ):
        unit = unit or self.unit
        quote_text = "😀树结构保持有序"
        start = unit.text.index(quote_text)
        return ClaimRecord.create(
            ClaimSubject("ch01", "knowledge_point", entity_id, field, "zh", claim_index),
            claim_text,
            ClaimSource(
                UnitRevisionRef.from_unit(unit),
                "text",
                payload_sha256(unit.text),
                role,
            ),
            QuoteSpan.create(start, start + len(quote_text), quote_text),
        )

    def manifest(self, record, reference=True, extra_knowledge_points=()):
        refs = [{"claim_id": record.claim_id}] if reference else []
        return {
            "schema_version": 1,
            "chapter": 1,
            "language": "zh",
            "profile": "full",
            "knowledge_points": [
                {
                    "id": "kp1",
                    "title": {"zh": "树结构"},
                    "explanation": {"zh": record.claim_text},
                    "formulas": [],
                    "source_refs": refs,
                },
                *extra_knowledge_points,
            ],
            "walkthroughs": [],
            "omissions": [],
        }

    def test_exact_unicode_codepoint_location_does_not_claim_entailment(self):
        record = self.claim(claim_text="This assertion is intentionally not compared semantically.")
        self.assertEqual(record.claim_id, verify_claim(record, (self.unit,), (self.source,)))
        self.assertEqual("location_only", record.verification_scope)
        self.assertEqual(record, ClaimRecord.from_dict(record.to_dict()))

    def test_utf16_style_offset_and_stale_revision_fail(self):
        record = self.claim()
        bad = record.to_dict()
        bad["quote"]["start"] += 1
        bad["quote"]["end"] += 1
        shifted = ClaimRecord.create(
            bad["subject"], bad["claim_text"], bad["source"], bad["quote"]
        )
        with self.assertRaises(ClaimValidationError):
            verify_claim(shifted, (self.unit,), (self.source,))

        changed = ContentUnit.create(
            self.unit.source_id,
            self.unit.source_sha256,
            self.unit.source_file,
            self.unit.kind,
            self.unit.text + " changed",
            self.unit.page,
            ordinal=self.unit.ordinal,
            chapter_id=self.unit.chapter_id,
        )
        self.assertEqual(self.unit.unit_id, changed.unit_id)
        with self.assertRaises(ClaimValidationError):
            verify_claim(record, (changed,), (self.source,))

    def test_answer_side_content_cannot_support_a_prompt_claim(self):
        answer = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "answer",
            "😀树结构保持有序",
            2,
            ordinal=2,
            chapter_id="ch01",
        )
        record = ClaimRecord.create(
            ClaimSubject("ch01", "quiz_item", "q1", "prompt_text", "zh", 0),
            "Prompt assertion",
            ClaimSource(
                UnitRevisionRef.from_unit(answer), "text", payload_sha256(answer.text), "answer_evidence"
            ),
            QuoteSpan.create(0, len(answer.text), answer.text),
        )
        with self.assertRaises(ClaimValidationError):
            verify_claim(record, (answer,), (self.source,))

    def test_receipt_is_deterministic_and_binds_every_artifact_hash(self):
        record = self.claim()
        manifest = self.manifest(record)
        kwargs = {
            "guide_content_sha256": "1" * 64,
            "source_manifest_sha256": "2" * 64,
            "content_units_sha256": "3" * 64,
            "canonical_groups_sha256": "4" * 64,
            "source_conflicts_sha256": "5" * 64,
            "claim_records_sha256": "6" * 64,
            "fact_snapshot_sha256": "7" * 64,
        }
        first = verify_claim_records(
            (record,), (self.unit,), (self.source,), "ch01", manifest=manifest, **kwargs
        )
        second = verify_claim_records(
            (record,), (self.unit,), (self.source,), "ch01", manifest=manifest, **kwargs
        )
        self.assertEqual(first, second)
        self.assertEqual("location_only", first.verification_scope)
        changed = dict(kwargs)
        changed["guide_content_sha256"] = "7" * 64
        third = verify_claim_records(
            (record,), (self.unit,), (self.source,), "ch01", manifest=manifest, **changed
        )
        self.assertNotEqual(first.receipt_id, third.receipt_id)
        changed_fact = dict(kwargs)
        changed_fact["fact_snapshot_sha256"] = "8" * 64
        fourth = verify_claim_records(
            (record,), (self.unit,), (self.source,), "ch01",
            manifest=manifest, **changed_fact
        )
        self.assertNotEqual(first.receipt_id, fourth.receipt_id)

    def test_manifest_hash_is_canonical_not_file_format_dependent(self):
        record = self.claim()
        manifest = self.manifest(record)
        reordered = {key: manifest[key] for key in reversed(list(manifest))}
        self.assertEqual(
            canonical_manifest_sha256(manifest),
            canonical_manifest_sha256(reordered),
        )

    def test_subject_binding_requires_exact_authored_text_and_known_field(self):
        record = self.claim()
        manifest = self.manifest(record)
        self.assertEqual((record,), validate_claim_subject_bindings((record,), manifest, "ch01"))
        mismatched = self.manifest(record)
        mismatched["knowledge_points"][0]["explanation"]["zh"] = "不同的作者文本"
        with self.assertRaises(ClaimValidationError):
            validate_claim_subject_bindings((record,), mismatched, "ch01")

        unknown = self.claim(field="unsupported_field")
        unknown_manifest = self.manifest(unknown)
        with self.assertRaises(ClaimValidationError):
            validate_claim_subject_bindings((unknown,), unknown_manifest, "ch01")

    def test_receipt_only_includes_claim_ids_explicitly_referenced_by_guide(self):
        bound = self.claim()
        unreferenced = self.claim(
            entity_id="kp2",
            claim_text="This valid record is not referenced by this guide.",
        )
        manifest = self.manifest(
            bound,
            extra_knowledge_points=(
                {
                    "id": "kp2",
                    "title": {"zh": "未引用"},
                    "explanation": {"zh": unreferenced.claim_text},
                    "formulas": [],
                    "source_refs": [],
                },
            ),
        )
        receipt = verify_claim_records(
            (bound, unreferenced),
            (self.unit,),
            (self.source,),
            "ch01",
            manifest=manifest,
            guide_content_sha256="1" * 64,
            source_manifest_sha256="2" * 64,
            content_units_sha256="3" * 64,
            canonical_groups_sha256="4" * 64,
            source_conflicts_sha256="5" * 64,
            claim_records_sha256="6" * 64,
            fact_snapshot_sha256="7" * 64,
        )
        self.assertEqual((bound.claim_id,), receipt.location_verified_claim_ids)

    def test_claim_sidecar_import_is_strict_and_atomic(self):
        record = self.claim()
        import_claim_records(self.workspace, (record,))
        self.assertEqual((record,), load_claim_records(self.workspace))
        malformed = self.workspace / "malformed.jsonl"
        malformed.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
        with self.assertRaises(ClaimValidationError):
            read_claim_jsonl(malformed)
        self.assertEqual((record,), load_claim_records(self.workspace))

    def test_create_proposal_requires_offset_for_repeated_quote(self):
        repeated = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "重复片段 / 重复片段",
            3,
            ordinal=3,
            chapter_id="ch01",
        )
        subject = ClaimSubject("ch01", "knowledge_point", "kp1", "explanation", "zh", 0)
        proposal = {
            "subject": subject.to_dict(),
            "source_unit_id": repeated.unit_id,
            "payload_field": "text",
            "role": "concept_evidence",
            "claim_text": "树结构的作者解释。",
            "quote": {"text": "重复片段"},
        }
        with self.assertRaises(ClaimValidationError):
            compile_claim_proposals((proposal,), (repeated,), (self.source,))
        explicit = dict(proposal)
        explicit["quote"] = {"text": "重复片段", "start": repeated.text.rindex("重复片段")}
        records = compile_claim_proposals((explicit,), (repeated,), (self.source,))
        self.assertEqual(explicit["quote"]["start"], records[0].quote.start)
        self.assertEqual(UnitRevisionRef.from_unit(repeated), records[0].source.unit_ref)

    def test_cli_import_and_verify_paths(self):
        ingest = self.workspace / ".ingest"
        ingest.mkdir()
        atomic_write_json(
            ingest / "source_manifest.json",
            {"schema_version": 1, "sources": [self.source.to_dict()]},
        )
        atomic_write_jsonl(ingest / "content_units.jsonl", [self.unit.to_dict()])
        atomic_write_jsonl(ingest / "canonical_groups.jsonl", [])
        atomic_write_jsonl(ingest / "source_conflicts.jsonl", [])
        atomic_write_json(
            ingest / "build_manifest.json",
            {"schema_version": 1, "pipeline_version": "ingestion-v1"},
        )
        guide = self.workspace / "study_guide" / "ch01.guide.json"
        record = self.claim()
        atomic_write_json(guide, self.manifest(record))
        incoming = self.workspace / "incoming_claims.jsonl"
        atomic_write_jsonl(incoming, [record.to_dict()])
        proposals = self.workspace / "claim_proposals.json"
        atomic_write_json(
            proposals,
            {
                "schema_version": 1,
                "proposals": [
                    {
                        "subject": record.subject.to_dict(),
                        "source_unit_id": self.unit.unit_id,
                        "payload_field": "text",
                        "role": "concept_evidence",
                        "claim_text": record.claim_text,
                        "quote": {"text": record.quote.text},
                    }
                ],
            },
        )

        real_lock = verify_claims.workspace_publication_lock
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                verify_claims, "workspace_publication_lock",
                side_effect=real_lock) as lock_call:
            self.assertEqual(
                0,
                verify_claims.run(
                    [
                        "create", "--workspace", str(self.workspace),
                        "--input-proposals", str(proposals), "--json",
                    ]
                ),
            )
            self.assertEqual(
                0,
                verify_claims.run(
                    [
                        "import", "--workspace", str(self.workspace),
                        "--input-claims", str(incoming), "--json",
                    ]
                ),
            )
            self.assertEqual(
                0,
                verify_claims.run(
                    [
                        "verify", "--workspace", str(self.workspace),
                        "--manifest", "study_guide/ch01.guide.json",
                        "--chapter", "1", "--json",
                    ]
                ),
            )
        self.assertEqual(3, lock_call.call_count)
        receipt_path = ingest / "claim_verification_receipts" / "ch01.json"
        receipt = read_json(receipt_path)
        self.assertEqual("location_only", receipt["verification_scope"])
        self.assertEqual(1, receipt["verified_claim_count"])
        self.assertEqual(self.claim().claim_id, receipt["location_verified_claim_ids"][0])
        self.assertEqual(
            canonical_manifest_sha256(self.manifest(record)),
            receipt["guide_content_sha256"],
        )

    def test_cli_claim_and_receipt_paths_cannot_target_control_files(self):
        (self.workspace / ".ingest").mkdir()
        control = self.workspace / "study_state.json"
        control.write_text('{"sentinel":true}\n', encoding="utf-8")

        attempts = (
            [
                "import", "--workspace", str(self.workspace),
                "--input-claims", str(self.workspace / "missing.jsonl"),
                "--claims", "study_state.json",
            ],
            [
                "create", "--workspace", str(self.workspace),
                "--input-proposals", str(self.workspace / "missing.json"),
                "--claims", "study_state.json",
            ],
            [
                "verify", "--workspace", str(self.workspace),
                "--manifest", "study_state.json", "--chapter", "1",
                "--receipt", "study_state.json",
            ],
        )
        for argv in attempts:
            with self.subTest(command=argv[0]), self.assertRaisesRegex(
                    ClaimValidationError, "fixed to"):
                verify_claims.run(argv)
        self.assertEqual('{"sentinel":true}\n', control.read_text(encoding="utf-8"))

    def test_study_guide_v2_gate_recomputes_receipt_and_material_coverage(self):
        unit = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Conditional probability restricts the sample space.",
            1,
            ordinal=7,
            chapter_id="ch01",
            metadata={"source_language": "en"},
        )
        claim_text = "Restrict the sample space to the known event."
        quote = "Conditional probability restricts the sample space."
        record = ClaimRecord.create(
            ClaimSubject("ch01", "knowledge_point", "kp1", "explanation", "en", 0),
            claim_text,
            ClaimSource(
                UnitRevisionRef.from_unit(unit),
                "text",
                payload_sha256(unit.text),
                "concept_evidence",
            ),
            QuoteSpan.create(0, len(quote), quote),
        )
        manifest = {
            "schema_version": 1,
            "chapter": 1,
            "language": "en",
            "profile": "full",
            "knowledge_points": [{
                "id": "kp1",
                "title": {"en": "Conditional probability"},
                "explanation": {"en": claim_text},
                "formulas": [],
                "source_refs": [{
                    "source_file": unit.source_file,
                    "pages": [unit.page],
                    "source_unit_id": unit.unit_id,
                    "role": "concept",
                    "claim_id": record.claim_id,
                }],
            }],
            "walkthroughs": [],
            "omissions": [],
            "semantic_exclusions": [],
        }
        ingest = self.workspace / ".ingest"
        ingest.mkdir()
        source_manifest_path = ingest / "source_manifest.json"
        content_units_path = ingest / "content_units.jsonl"
        canonical_groups_path = ingest / "canonical_groups.jsonl"
        source_conflicts_path = ingest / "source_conflicts.jsonl"
        atomic_write_json(
            source_manifest_path,
            {"schema_version": 1, "sources": [self.source.to_dict()]},
        )
        atomic_write_jsonl(content_units_path, [unit.to_dict()])
        atomic_write_jsonl(canonical_groups_path, [])
        atomic_write_jsonl(source_conflicts_path, [])
        atomic_write_json(
            ingest / "build_manifest.json",
            {"schema_version": 1, "pipeline_version": "ingestion-v2"},
        )
        import_claim_records(self.workspace, (record,))
        guide_path = self.workspace / "study_guide" / "ch01.guide.json"
        atomic_write_json(guide_path, manifest)
        empty_fact_snapshot = {
            "conflicts": (),
            "snapshot": {"schema_version": 1, "test_fixture": True},
        }
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                verify_claims,
                "validate_workspace_fact_integrity",
                return_value=empty_fact_snapshot,
        ):
            self.assertEqual(
                0,
                verify_claims.run([
                    "verify", "--workspace", str(self.workspace),
                    "--manifest", "study_guide/ch01.guide.json",
                    "--chapter", "1", "--json",
                ]),
            )
        receipt_path = ingest / "claim_verification_receipts" / "ch01.json"

        with mock.patch.object(
                verify_claims,
                "validate_workspace_fact_integrity",
                return_value={
                    "conflicts": (
                        mock.Mock(conflict_id="conflict_test", status="unresolved"),
                    ),
                },
        ):
            with self.assertRaisesRegex(ClaimValidationError, "unresolved source conflicts"):
                verify_claims.run([
                    "verify", "--workspace", str(self.workspace),
                    "--manifest", "study_guide/ch01.guide.json",
                    "--chapter", "1", "--json",
                ])

        mismatched_ref = copy.deepcopy(manifest)
        mismatched_ref["knowledge_points"][0]["source_refs"][0][
            "source_unit_id"
        ] = "unit_not_the_claim_unit"
        atomic_write_json(guide_path, mismatched_ref)
        with mock.patch.object(
                verify_claims,
                "validate_workspace_fact_integrity",
                return_value=empty_fact_snapshot,
        ), self.assertRaisesRegex(ClaimValidationError, "source unit disagrees"):
            verify_claims.run([
                "verify", "--workspace", str(self.workspace),
                "--manifest", "study_guide/ch01.guide.json",
                "--chapter", "1", "--json",
            ])
        atomic_write_json(guide_path, manifest)
        inventory = {
            "units": [unit.to_dict()],
            "unit_index": {unit.unit_id: unit.to_dict()},
        }

        with mock.patch.object(
                study_guide_content,
                "validate_workspace_fact_integrity",
                return_value=empty_fact_snapshot,
        ):
            report = study_guide_content._validate_v2_claim_gate(
                str(self.workspace), 1, manifest, inventory
            )
        self.assertEqual("location_only", report["verification_scope"])
        self.assertEqual(1, report["required_material_assertion_count"])

        with mock.patch.object(
                study_guide_content,
                "validate_workspace_fact_integrity",
                return_value={
                    "conflicts": (
                        mock.Mock(conflict_id="conflict_test", status="unresolved"),
                    ),
                },
        ):
            with self.assertRaisesRegex(
                    study_guide_content.ContentError, "unresolved source conflicts"):
                study_guide_content._validate_v2_claim_gate(
                    str(self.workspace), 1, manifest, inventory
                )

        changed = dict(manifest)
        changed["knowledge_points"] = [dict(manifest["knowledge_points"][0])]
        changed["knowledge_points"][0]["explanation"] = {"en": "A stale changed assertion."}
        with mock.patch.object(
                study_guide_content,
                "validate_workspace_fact_integrity",
                return_value=empty_fact_snapshot,
        ), self.assertRaises(study_guide_content.ContentError):
            study_guide_content._validate_v2_claim_gate(
                str(self.workspace), 1, changed, inventory
            )

    def test_v2_claim_and_guide_gates_rederive_facts_and_accept_ledger_terminal_state(self):
        materials = self.workspace / "materials"
        source_a = materials / "a.txt"
        source_b = materials / "b.txt"
        source_a.write_text("authoritative A", encoding="utf-8")
        source_b.write_text("authoritative B", encoding="utf-8")
        prompt = "What is 2+2?"
        payload = build_payload(
            str(materials),
            [str(source_a), str(source_b)],
            [
                {
                    "file": "a.txt",
                    "page": 1,
                    "text": "",
                    "elements": [{
                        "kind": "text",
                        "text": "Addition combines two quantities.",
                        "source_language": "en",
                    }],
                },
                {"file": "b.txt", "page": 1, "text": ""},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("a.txt", 1), ("b.txt", 1)],
            }],
            quiz_items=[
                {
                    "id": "key-a", "chapter": 1, "type": "subjective",
                    "question": prompt, "answer": "4", "source": "material",
                    "source_file": "a.txt", "source_pages": [1],
                    "answer_source_pages": [1], "source_language": "en",
                    "answer_source_language": "en",
                },
                {
                    "id": "key-b", "chapter": 1, "type": "subjective",
                    "question": prompt, "answer": "5", "source": "material",
                    "source_file": "b.txt", "source_pages": [1],
                    "answer_source_pages": [1], "source_language": "en",
                    "answer_source_language": "en",
                },
            ],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        persist_payload(self.workspace, payload)
        initial_snapshot = validate_workspace_fact_integrity(self.workspace)
        self.assertGreater(initial_snapshot["conflict_count"], 0)
        self.assertTrue(all(
            conflict.status == "unresolved"
            for conflict in initial_snapshot["conflicts"]
        ))

        store = IngestionStore(self.workspace, source_root=materials)
        concept = next(
            unit for unit in store.units().values()
            if unit.kind == "text" and unit.metadata.get("source_language") == "en"
        )
        record = ClaimRecord.create(
            ClaimSubject("ch01", "knowledge_point", "kp1", "explanation", "en", 0),
            concept.text,
            ClaimSource(
                UnitRevisionRef.from_unit(concept),
                "text",
                payload_sha256(concept.text),
                "concept_evidence",
            ),
            QuoteSpan.create(0, len(concept.text), concept.text),
        )
        manifest = {
            "schema_version": 1,
            "chapter": 1,
            "language": "en",
            "profile": "full",
            "knowledge_points": [{
                "id": "kp1",
                "title": {"en": "Addition"},
                "explanation": {"en": concept.text},
                "formulas": [],
                "source_refs": [{
                    "source_file": concept.source_file,
                    "pages": [concept.page],
                    "source_unit_id": concept.unit_id,
                    "role": "concept",
                    "claim_id": record.claim_id,
                }],
            }],
            "walkthroughs": [],
            "omissions": [],
            "semantic_exclusions": [],
        }
        import_claim_records(self.workspace, (record,))
        guide_path = self.workspace / "study_guide" / "ch01.guide.json"
        atomic_write_json(guide_path, manifest)
        verify_args = [
            "verify", "--workspace", str(self.workspace),
            "--manifest", "study_guide/ch01.guide.json",
            "--chapter", "1", "--json",
        ]
        with self.assertRaisesRegex(ClaimValidationError, "unresolved source conflicts"):
            verify_claims.run(verify_args)

        for index, conflict in enumerate(load_source_conflicts(self.workspace)):
            issue = store.review_queue.get(conflict.review_issue_id)
            patch = ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                [{
                    "op": "mark_unrecoverable",
                    "reason": "The current official answer keys genuinely disagree.",
                }],
                list(issue.evidence),
                reviewer="test",
                created_at="2026-07-14T12:%02d:00Z" % index,
                status="validated",
            )
            store.apply_patch(patch)
        persist_payload(self.workspace, payload)
        terminal_snapshot = validate_workspace_fact_integrity(self.workspace)
        self.assertTrue(terminal_snapshot["conflicts"])
        self.assertIn("content_units", terminal_snapshot["snapshot"]["inputs"])
        self.assertIn("review_patches", terminal_snapshot["snapshot"]["inputs"])
        self.assertTrue(all(
            set(row) == {"source_id", "path", "sha256", "size_bytes"}
            for row in terminal_snapshot["snapshot"]["source_revisions"]
        ))
        self.assertTrue(all(
            conflict.status == "unrecoverable"
            for conflict in terminal_snapshot["conflicts"]
        ))
        self.assertGreater(terminal_snapshot["review_patch_count"], 0)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, verify_claims.run(verify_args))
        current_store = IngestionStore(self.workspace, source_root=materials)
        current_units = tuple(current_store.units().values())
        inventory = {
            "units": [unit.to_dict() for unit in current_units],
            "unit_index": {unit.unit_id: unit.to_dict() for unit in current_units},
        }
        guide_report = study_guide_content._validate_v2_claim_gate(
            str(self.workspace), 1, manifest, inventory
        )
        self.assertEqual("location_only", guide_report["verification_scope"])
        self.assertEqual(
            terminal_snapshot["snapshot"],
            guide_report["fact_integrity"],
        )
        self.assertIn("parser_receipts", terminal_snapshot["snapshot"]["inputs"])
        self.assertEqual(
            {"source", "sha256", "count"},
            set(terminal_snapshot["snapshot"]["page_quality"]),
        )
        self.assertGreater(
            terminal_snapshot["snapshot"]["page_quality"]["count"], 0
        )
        self.assertEqual(
            len(terminal_snapshot["snapshot"]["page_quality"]["sha256"]), 64
        )

        parser_path = self.workspace / ".ingest" / "parser_receipts.json"
        parser_bytes = parser_path.read_bytes()
        receipt_path = (
            self.workspace / ".ingest" / "claim_verification_receipts" / "ch01.json"
        )
        parser_path.unlink()
        with self.assertRaisesRegex(study_guide_content.ContentError, "parser_receipts"):
            study_guide_content._validate_v2_claim_gate(
                str(self.workspace), 1, manifest, inventory
            )
        with self.assertRaisesRegex(ValueError, "parser_receipts"):
            verify_claims.run(verify_args)
        # A stale receipt may remain on disk, but every publication consumer
        # re-derives the fact chain and therefore rejects it above.
        self.assertTrue(receipt_path.exists())
        receipt_path.unlink()
        parser_path.write_bytes(parser_bytes)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, verify_claims.run(verify_args))

        # A valid parser-identity update, paired with an updated build-manifest
        # binding, is still a provenance revision and must stale the old claim
        # receipt even when source/content units are byte-for-byte unchanged.
        old_receipt = read_json(receipt_path)
        parser_document = read_json(parser_path)
        parser_document["receipts"][0]["adapter"] = "core-revalidated"
        parser_document["receipts"][0]["config_sha256"] = "e" * 64
        atomic_write_json(parser_path, parser_document)
        manifest_path = self.workspace / ".ingest" / "build_manifest.json"
        parser_manifest = read_json(manifest_path)
        parser_manifest["artifacts"]["parser_receipts"]["sha256"] = file_sha256(
            parser_path
        )
        atomic_write_json(manifest_path, parser_manifest)
        updated_facts = validate_workspace_fact_integrity(self.workspace)
        updated_fact_sha256 = canonical_fact_snapshot_sha256(
            updated_facts["snapshot"]
        )
        self.assertNotEqual(
            old_receipt["fact_snapshot_sha256"], updated_fact_sha256
        )
        with self.assertRaisesRegex(study_guide_content.ContentError, "stale"):
            study_guide_content._validate_v2_claim_gate(
                str(self.workspace), 1, manifest, inventory
            )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, verify_claims.run(verify_args))
        updated_receipt = read_json(receipt_path)
        self.assertEqual(updated_fact_sha256, updated_receipt["fact_snapshot_sha256"])
        self.assertNotEqual(old_receipt["receipt_id"], updated_receipt["receipt_id"])

        # Regression for the old parse(A) -> path-hash(B) split.  The helper
        # must compare the manifest to the hash of the exact captured A bytes,
        # never return A rows while binding B on disk.
        priorities_path = self.workspace / ".ingest" / "source_priorities.jsonl"
        priorities_a = priorities_path.read_bytes()
        manifest_a = manifest_path.read_bytes()
        priority_b = SourcePriority.create(
            terminal_snapshot["priorities"][0].source_id,
            terminal_snapshot["priorities"][0].source_sha256,
            rank=10,
            tier="teacher_official",
            basis="review",
            evidence=(FactEvidenceRef("evidence.txt", "3" * 64),),
        )
        priorities_b = (canonical_json(priority_b.to_dict()) + "\n").encode("utf-8")
        priorities_path.write_bytes(priorities_b)
        manifest_b = read_json(manifest_path)
        manifest_b["artifacts"]["source_priorities"]["sha256"] = file_sha256(
            priorities_path
        )
        atomic_write_json(manifest_path, manifest_b)
        priorities_path.write_bytes(priorities_a)
        real_stable_jsonl = ingestion_dedup.stable_read_jsonl

        def swap_priority_after_snapshot(path):
            rows, capture = real_stable_jsonl(path)
            if Path(path) == priorities_path:
                priorities_path.write_bytes(priorities_b)
            return rows, capture

        try:
            with mock.patch.object(
                    ingestion_dedup, "stable_read_jsonl",
                    side_effect=swap_priority_after_snapshot,
            ), self.assertRaisesRegex(ValueError, "source_priorities"):
                validate_workspace_fact_integrity(self.workspace)
        finally:
            priorities_path.write_bytes(priorities_a)
            manifest_path.write_bytes(manifest_a)

        real_derivation = ingestion_dedup.validate_persisted_fact_derivation
        for input_label, relative in (
            ("content_units", ".ingest/content_units.jsonl"),
            ("review_patches", ".ingest/review_patches.jsonl"),
            ("parser_receipts", ".ingest/parser_receipts.json"),
        ):
            with self.subTest(mid_validation_drift=input_label):
                input_path = self.workspace.joinpath(*relative.split("/"))
                original_bytes = input_path.read_bytes()

                def drift_after_derivation(*args, **kwargs):
                    result = real_derivation(*args, **kwargs)
                    input_path.write_bytes(original_bytes + b"\n")
                    return result

                try:
                    with mock.patch.object(
                            ingestion_dedup,
                            "validate_persisted_fact_derivation",
                            side_effect=drift_after_derivation,
                    ), self.assertRaisesRegex(ValueError, input_label):
                        validate_workspace_fact_integrity(self.workspace)
                finally:
                    input_path.write_bytes(original_bytes)

        source_bytes = source_a.read_bytes()
        try:
            source_a.write_bytes(b"drifted source bytes")
            with self.assertRaisesRegex(ValueError, "source"):
                validate_workspace_fact_integrity(self.workspace)
        finally:
            source_a.write_bytes(source_bytes)

        receipt_path.unlink()
        real_atomic_write_json = verify_claims.atomic_write_json

        def drift_before_atomic_callback(path, value, before_publish=None):
            source_a.write_bytes(b"source changed before receipt publication")
            try:
                return real_atomic_write_json(
                    path, value, before_publish=before_publish
                )
            finally:
                source_a.write_bytes(source_bytes)

        with mock.patch.object(
                verify_claims,
                "atomic_write_json",
                side_effect=drift_before_atomic_callback,
        ), self.assertRaisesRegex(ValueError, "source"):
            verify_claims.run(verify_args)
        self.assertFalse(receipt_path.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, verify_claims.run(verify_args))

        receipt_path.unlink()
        conflict_path = self.workspace / ".ingest" / "source_conflicts.jsonl"
        conflict_path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "source_conflicts"):
            verify_claims.run(verify_args)
        self.assertFalse(receipt_path.exists())

        # A forged receipt that binds the now-empty sidecar must not let the
        # independent Study Guide gate skip deterministic fact re-derivation.
        source_manifest_path = self.workspace / ".ingest" / "source_manifest.json"
        content_units_path = self.workspace / ".ingest" / "content_units.jsonl"
        canonical_groups_path = self.workspace / ".ingest" / "canonical_groups.jsonl"
        claim_records_path = self.workspace / ".ingest" / "claim_records.jsonl"
        forged = verify_claim_records(
            (record,),
            current_units,
            tuple(current_store.manifest.records()),
            "ch01",
            manifest=manifest,
            guide_content_sha256=canonical_manifest_sha256(manifest),
            source_manifest_sha256=file_sha256(source_manifest_path),
            content_units_sha256=file_sha256(content_units_path),
            canonical_groups_sha256=file_sha256(canonical_groups_path),
            source_conflicts_sha256=file_sha256(conflict_path),
            claim_records_sha256=file_sha256(claim_records_path),
            fact_snapshot_sha256="f" * 64,
        )
        atomic_write_json(receipt_path, forged.to_dict())
        with self.assertRaisesRegex(
                study_guide_content.ContentError, "source_conflicts"):
            study_guide_content._validate_v2_claim_gate(
                str(self.workspace), 1, manifest, inventory
            )

    def test_v2_material_coverage_includes_formula_prompt_and_official_answer(self):
        def make_unit(kind, text, ordinal, *, latex=None, external_id=None):
            return ContentUnit.create(
                self.source.source_id,
                self.source.sha256,
                self.source.path,
                kind,
                text,
                ordinal,
                ordinal=ordinal,
                latex=latex,
                external_id=external_id,
                chapter_id="ch01",
                metadata={"source_language": "en"},
            )

        concept = make_unit("text", "A conditional probability uses known evidence.", 10)
        formula = make_unit("formula", "Conditional probability formula", 11,
                            latex=r"P(A\mid B)=P(A\cap B)/P(B)")
        question = make_unit("question", "Compute P(A|B).", 12, external_id="q1")
        answer = make_unit("answer", "0.5", 13, external_id="q1")

        def record(subject, claim_text, unit, payload_field, role, quote):
            payload = getattr(unit, payload_field)
            start = payload.index(quote)
            return ClaimRecord.create(
                subject,
                claim_text,
                ClaimSource(
                    UnitRevisionRef.from_unit(unit), payload_field,
                    payload_sha256(payload), role,
                ),
                QuoteSpan.create(start, start + len(quote), quote),
            )

        concept_claim = record(
            ClaimSubject("ch01", "knowledge_point", "kp1", "explanation", "en", 0),
            "Use the event that is already known.", concept, "text", "concept_evidence",
            "known evidence",
        )
        formula_claim = record(
            ClaimSubject("ch01", "formula", "f1", "latex", "source", 0),
            formula.latex, formula, "latex", "formula_evidence", formula.latex,
        )
        prompt_claim = record(
            ClaimSubject("ch01", "walkthrough", "q1", "prompt_text", "source", 0),
            question.text, question, "text", "question_evidence", question.text,
        )
        answer_claim = record(
            ClaimSubject("ch01", "walkthrough", "q1", "answer", "en", 0),
            answer.text, answer, "text", "answer_evidence", answer.text,
        )

        def ref(unit, role, claim):
            return {
                "source_file": unit.source_file,
                "pages": [unit.page],
                "source_unit_id": unit.unit_id,
                "role": role,
                "claim_id": claim.claim_id,
            }

        manifest = {
            "schema_version": 1,
            "chapter": 1,
            "language": "en",
            "profile": "full",
            "knowledge_points": [{
                "id": "kp1",
                "explanation": {"en": concept_claim.claim_text},
                "source_refs": [ref(concept, "concept", concept_claim)],
                "formulas": [{
                    "id": "f1",
                    "latex": formula.latex,
                    "source_refs": [ref(formula, "formula", formula_claim)],
                }],
            }],
            "walkthroughs": [{
                "item_id": "q1",
                "original_language": "en",
                "prompt_text": question.text,
                "answer": {"en": answer.text},
                "answer_provenance": {"en": "material"},
                "source_trace": [
                    ref(question, "question", prompt_claim),
                    ref(answer, "answer", answer_claim),
                ],
            }],
            "omissions": [],
            "semantic_exclusions": [],
        }
        records = (concept_claim, formula_claim, prompt_claim, answer_claim)
        unit_index = {
            unit.unit_id: unit.to_dict() for unit in (concept, formula, question, answer)
        }
        covered = validate_guide_claim_coverage(
            records, manifest, "ch01", tuple(unit_index.values())
        )
        self.assertEqual(4, len(covered))

        wrong_unit = copy.deepcopy(manifest)
        wrong_unit["walkthroughs"][0]["source_trace"][1]["source_unit_id"] = question.unit_id
        with self.assertRaisesRegex(ClaimValidationError, "source unit disagrees"):
            validate_guide_claim_coverage(
                records, wrong_unit, "ch01", tuple(unit_index.values())
            )

        wrong_role = copy.deepcopy(manifest)
        wrong_role["walkthroughs"][0]["source_trace"][1]["role"] = "question"
        with self.assertRaisesRegex(ClaimValidationError, "role=.*incompatible"):
            validate_guide_claim_coverage(
                records, wrong_role, "ch01", tuple(unit_index.values())
            )

        duplicate = copy.deepcopy(manifest)
        duplicate["knowledge_points"][0]["source_refs"].append(
            copy.deepcopy(duplicate["knowledge_points"][0]["source_refs"][0])
        )
        with self.assertRaisesRegex(ClaimValidationError, "repeats claim_id"):
            validate_guide_claim_coverage(
                records, duplicate, "ch01", tuple(unit_index.values())
            )

        wrong_quote = copy.deepcopy(manifest)
        wrong_quote["knowledge_points"][0]["source_refs"][0]["quote_span"] = "wrong"
        with self.assertRaisesRegex(ClaimValidationError, "quote_span disagrees"):
            validate_guide_claim_coverage(
                records, wrong_quote, "ch01", tuple(unit_index.values())
            )

        manifest["walkthroughs"][0]["source_trace"][1].pop("claim_id")
        with self.assertRaisesRegex(
                ClaimValidationError, "field=answer language=en"):
            validate_guide_claim_coverage(
                records, manifest, "ch01", tuple(unit_index.values())
            )

    def test_kp_explanations_require_per_language_material_claim_or_visible_ai_label(self):
        concept = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "text", "Authoritative English concept.", 1, ordinal=30,
            chapter_id="ch01", metadata={"source_language": "en"},
        )
        figure = ContentUnit.create(
            self.source.source_id, self.source.sha256, self.source.path,
            "figure", "", 1, ordinal=31, chapter_id="ch01",
            asset_path="references/assets/figure.png",
        )
        claim = ClaimRecord.create(
            ClaimSubject("ch01", "knowledge_point", "kp_text", "explanation", "en", 0),
            concept.text,
            ClaimSource(
                UnitRevisionRef.from_unit(concept), "text",
                payload_sha256(concept.text), "concept_evidence",
            ),
            QuoteSpan.create(0, len(concept.text), concept.text),
        )
        ref = {
            "source_unit_id": concept.unit_id, "role": "concept",
            "claim_id": claim.claim_id,
        }
        manifest = {
            "schema_version": 1, "chapter": 1, "language": "en", "profile": "full",
            "knowledge_points": [
                {
                    "id": "kp_visual", "explanation": {"en": "Unsupported prose."},
                    "formulas": [],
                    "source_refs": [{"source_unit_id": figure.unit_id, "role": "concept"}],
                },
                {
                    "id": "kp_text", "explanation": {"en": concept.text},
                    "formulas": [], "source_refs": [ref],
                },
            ],
            "walkthroughs": [], "omissions": [], "semantic_exclusions": [],
        }
        units = (figure.to_dict(), concept.to_dict())
        with self.assertRaisesRegex(ClaimValidationError, "kp_visual.*language=en"):
            validate_guide_claim_coverage((claim,), manifest, "ch01", units)
        manifest["knowledge_points"][0]["explanation_provenance"] = {
            "en": "ai_supplement"
        }
        self.assertEqual(
            ("knowledge_point=kp_text field=explanation language=en",),
            validate_guide_claim_coverage((claim,), manifest, "ch01", units),
        )

        bilingual = copy.deepcopy(manifest)
        bilingual["language"] = "bilingual"
        bilingual["knowledge_points"] = [copy.deepcopy(manifest["knowledge_points"][1])]
        bilingual["knowledge_points"][0]["explanation"] = {
            "en": concept.text, "zh": "由 AI 翻译的中文解释。",
        }
        with self.assertRaisesRegex(ClaimValidationError, "language=zh"):
            validate_guide_claim_coverage((claim,), bilingual, "ch01", units)
        bilingual["knowledge_points"][0]["explanation_provenance"] = {
            "en": "material", "zh": "ai_translation",
        }
        self.assertEqual(1, len(validate_guide_claim_coverage(
            (claim,), bilingual, "ch01", units
        )))
        bilingual["knowledge_points"][0]["explanation_provenance"]["en"] = "ai_supplement"
        with self.assertRaisesRegex(ClaimValidationError, "needs a claimed material explanation"):
            validate_guide_claim_coverage((claim,), bilingual, "ch01", units)

    def test_v2_zero_claimable_surface_is_an_explicit_blocker(self):
        manifest = {
            "schema_version": 1,
            "chapter": 1,
            "language": "en",
            "profile": "full",
            "knowledge_points": [],
            "walkthroughs": [],
            "omissions": [],
            "semantic_exclusions": [],
        }
        with self.assertRaisesRegex(
                ClaimValidationError, "zero claimable material assertions"):
            validate_guide_claim_coverage((), manifest, "ch01", ())

    def test_create_merges_chapters_and_replaces_only_matching_subject(self):
        second = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Second chapter authoritative statement.",
            2,
            ordinal=2,
            chapter_id="ch02",
        )
        ingest = self.workspace / ".ingest"
        ingest.mkdir()
        atomic_write_json(
            ingest / "source_manifest.json",
            {"schema_version": 1, "sources": [self.source.to_dict()]},
        )
        atomic_write_jsonl(
            ingest / "content_units.jsonl", [self.unit.to_dict(), second.to_dict()]
        )

        def proposal(path, chapter_id, entity_id, unit, claim_text):
            document = {
                "schema_version": 1,
                "proposals": [{
                    "subject": ClaimSubject(
                        chapter_id, "knowledge_point", entity_id,
                        "explanation", "en", 0,
                    ).to_dict(),
                    "source_unit_id": unit.unit_id,
                    "payload_field": "text",
                    "role": "concept_evidence",
                    "claim_text": claim_text,
                    "quote": {"text": unit.text},
                }],
            }
            atomic_write_json(path, document)

        first_path = self.workspace / "ch1-proposals.json"
        second_path = self.workspace / "ch2-proposals.json"
        proposal(first_path, "ch01", "kp1", self.unit, "Chapter one explanation.")
        proposal(second_path, "ch02", "kp2", second, "Chapter two explanation.")
        with contextlib.redirect_stdout(io.StringIO()):
            verify_claims.run([
                "create", "--workspace", str(self.workspace),
                "--input-proposals", str(first_path), "--json",
            ])
            verify_claims.run([
                "create", "--workspace", str(self.workspace),
                "--input-proposals", str(second_path), "--json",
            ])
        merged = load_claim_records(self.workspace)
        self.assertEqual({"ch01", "ch02"}, {row.subject.chapter_id for row in merged})

        proposal(second_path, "ch02", "kp2", second, "Updated chapter two explanation.")
        with contextlib.redirect_stdout(io.StringIO()):
            verify_claims.run([
                "create", "--workspace", str(self.workspace),
                "--input-proposals", str(second_path), "--json",
            ])
        updated = load_claim_records(self.workspace)
        self.assertEqual(2, len(updated))
        self.assertIn("Chapter one explanation.", {row.claim_text for row in updated})
        self.assertIn("Updated chapter two explanation.", {row.claim_text for row in updated})


if __name__ == "__main__":
    unittest.main()
