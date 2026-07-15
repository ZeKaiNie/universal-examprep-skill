import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import validate_workspace
from scripts.ingestion import ContentUnit, IngestionStore, ReviewPatch, render_answer_value
from scripts.ingestion.dedup import (
    DedupConfig,
    build_dedup_facts,
    load_canonical_groups,
    load_source_conflicts,
    load_source_priorities,
)
from scripts.ingestion.identifiers import file_sha256
from scripts.ingestion.pipeline import (
    _deduplicate_bound_candidates,
    build_payload,
    compile_review_outputs,
    persist_payload,
)
from scripts.ingestion.storage import atomic_write_json, atomic_write_jsonl


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

    def near_answer_conflict_payload(self):
        alternate = self.materials / "alternate.txt"
        alternate.write_text("Chapter 1\nAlternate official key", encoding="utf-8")
        prompt = "Apply the recurrence relation and report the final checked value."
        return build_payload(
            str(self.materials),
            [str(self.source), str(alternate)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Chapter 1\n" + prompt},
                {"file": "alternate.txt", "page": 1, "text": "Chapter 1\n" + prompt},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("alternate.txt", 1)],
            }],
            quiz_items=[
                {
                    "id": "key-10", "chapter": 1, "type": "subjective",
                    "question": prompt,
                    "answer": (
                        "The final value is 10 after applying the recurrence relation "
                        "and checking the boundary condition."
                    ),
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
                {
                    "id": "key-11", "chapter": 1, "type": "subjective",
                    "question": prompt,
                    "answer": (
                        "The final value is 11 after applying the recurrence relation "
                        "and checking the boundary condition."
                    ),
                    "source": "material", "source_file": "alternate.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
            ],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )

    def rewrite_bound_artifact(self, manifest, label, payload):
        artifact = manifest["artifacts"][label]
        path = self.workspace.joinpath(*artifact["path"].split("/"))
        path.write_bytes(payload)
        artifact["sha256"] = hashlib.sha256(payload).hexdigest()
        return path

    def write_build_manifest(self, manifest):
        path = self.workspace / ".ingest" / "build_manifest.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
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
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual(1, len(payload["parser_receipts"]))
        self.assertEqual([1, 2], payload["parser_receipts"][0]["produced_pages"])
        self.assertEqual(2, payload["parser_receipts"][0]["discovered_page_count"])

        first = persist_payload(self.workspace, payload)
        second = persist_payload(self.workspace, payload)
        self.assertEqual(first["source_count"], second["source_count"])
        store = IngestionStore(self.workspace, source_root=self.materials)
        self.assertEqual(2, len([u for u in store.units().values() if u.kind == "page_anchor"]))
        self.assertGreaterEqual(len(store.review_queue.issues()), 2)
        store.manifest.verify_current(payload["sources"][0]["source_id"], payload["sources"][0]["sha256"])
        self.assertEqual("ingestion-v2", first["pipeline_version"])
        receipts = json.loads(
            (self.workspace / ".ingest" / "parser_receipts.json").read_text(encoding="utf-8")
        )
        self.assertEqual("core", receipts["receipts"][0]["adapter"])

    def test_raster_sidecar_requires_exact_first_class_source_revision(self):
        image = self.materials / "diagram.png"
        sidecar = self.materials / "diagram.ocr.txt"
        image.write_bytes(b"synthetic-image-source")
        sidecar.write_text("OCR transcript", encoding="utf-8")
        sidecar_bytes = sidecar.read_bytes()
        page = {
            "file": "diagram.png",
            "page": 1,
            "text": "",
            "elements": [],
            "metadata": {
                "format": "standalone_raster",
                "page_equivalent": "image",
                "raster": {},
                "sidecar": {
                    "source_file": "diagram.ocr.txt",
                    "sha256": hashlib.sha256(sidecar_bytes).hexdigest(),
                    "byte_size": len(sidecar_bytes),
                    "discovery": "explicit",
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "unknown first-class source"):
            build_payload(str(self.materials), [str(image)], [page])

        masquerade = json.loads(json.dumps(page))
        masquerade["elements"] = [{
            "kind": "text",
            "text": "Sidecar text copied under the image source",
            "ordinal": 0,
            "bbox": None,
            "method": "manual",
            "confidence": 1.0,
            "metadata": {"sidecar": page["metadata"]["sidecar"]},
        }]
        with self.assertRaisesRegex(ValueError, "only on its image page anchor"):
            build_payload(
                str(self.materials), [str(image), str(sidecar)], [masquerade]
            )

        payload = build_payload(
            str(self.materials), [str(image), str(sidecar)], [page]
        )
        anchor = next(
            row for row in payload["content_units"]
            if row["source_file"] == "diagram.png" and row["kind"] == "page_anchor"
        )
        self.assertEqual(
            "diagram.ocr.txt",
            anchor["metadata"]["parser_metadata"]["sidecar"]["source_file"],
        )
        anchor["metadata"]["parser_metadata"]["sidecar"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match its source revision"):
            persist_payload(self.workspace, payload)

    def test_post_binding_review_dedup_preserves_blocking_severity(self):
        common = {
            "reason_codes": ["chapter_unassigned"],
            "source_file": "scan.png",
            "pages": [],
            "target_unit_ids": [],
            "description": "first",
            "suggested_action": "inspect",
        }
        warning = dict(common, severity="warning")
        blocker = dict(common, severity="blocking", description="second")
        folded = _deduplicate_bound_candidates([warning, blocker])
        self.assertEqual(1, len(folded))
        self.assertEqual("blocking", folded[0]["severity"])
        self.assertEqual("first | second", folded[0]["description"])

    def test_parser_receipt_source_and_page_drift_fail_closed(self):
        payload = self.payload()
        wrong_hash = json.loads(json.dumps(payload))
        wrong_hash["parser_receipts"][0]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match source revision"):
            persist_payload(self.workspace, wrong_hash)

        wrong_pages = json.loads(json.dumps(payload))
        wrong_pages["parser_receipts"][0]["produced_pages"] = [1]
        with self.assertRaisesRegex(ValueError, "disagree with page inventory"):
            persist_payload(self.workspace, wrong_pages)

        wrong_discovery = json.loads(json.dumps(payload))
        wrong_discovery["parser_receipts"][0]["discovered_page_count"] = 3
        with self.assertRaisesRegex(ValueError, "contiguous discovered pages"):
            persist_payload(self.workspace, wrong_discovery)

    def test_parser_failure_inventory_requires_zero_anchors_status_and_typed_issue(self):
        failed = build_payload(
            str(self.materials),
            [str(self.source)],
            [],
            report={
                "warnings": [],
                "ai_review": [],
                "skipped": [{"file": "ch01.txt", "why": "parser failed"}],
            },
        )
        self.assertEqual("failed", failed["sources"][0]["status"])
        self.assertEqual("failed", failed["parser_receipts"][0]["status"])
        self.assertEqual([], failed["parser_receipts"][0]["produced_pages"])
        persist_payload(self.workspace, failed)

        produced = json.loads(json.dumps(failed))
        produced["page_quality"] = [{
            "source_file": "ch01.txt", "page": 1, "score": 1.0,
            "route": "fast", "reason_codes": [],
        }]
        produced["parser_receipts"][0]["produced_pages"] = [1]
        produced["parser_receipts"][0]["discovered_page_count"] = 1
        with self.assertRaisesRegex(ValueError, "must produce zero page anchors"):
            persist_payload(self.workspace, produced)

        complete = json.loads(json.dumps(failed))
        complete["sources"][0]["status"] = "complete"
        with self.assertRaisesRegex(ValueError, "contradicts SourceRecord status"):
            persist_payload(self.workspace, complete)

        no_issue = json.loads(json.dumps(failed))
        no_issue["review_candidates"] = []
        with self.assertRaisesRegex(ValueError, "blocking typed issue"):
            persist_payload(self.workspace, no_issue)

        unsupported = build_payload(
            str(self.materials),
            [str(self.source)],
            [],
            report={
                "warnings": [],
                "ai_review": [],
                "skipped": [{"file": "ch01.txt", "why": "unsupported format"}],
            },
        )
        self.assertEqual("unsupported", unsupported["sources"][0]["status"])
        self.assertEqual("unsupported", unsupported["parser_receipts"][0]["status"])
        persist_payload(self.workspace, unsupported)

    def test_failed_parser_terminal_issue_requires_authoritative_ledger_patch(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [],
            report={
                "warnings": [],
                "ai_review": [],
                "skipped": [{"file": "ch01.txt", "why": "parser failed"}],
            },
        )
        manifest = persist_payload(self.workspace, payload)
        queue_path = self.workspace / ".ingest" / "review_queue.jsonl"
        original_rows = [
            json.loads(line)
            for line in queue_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        target_id = next(
            row["issue_id"] for row in original_rows if row["severity"] == "blocking"
        )
        for terminal_status in (
            "applied", "resolved", "unrecoverable", "superseded",
        ):
            with self.subTest(status=terminal_status):
                rows = json.loads(json.dumps(original_rows))
                next(
                    row for row in rows if row["issue_id"] == target_id
                )["status"] = terminal_status
                atomic_write_jsonl(queue_path, rows)
                current_manifest = json.loads(json.dumps(manifest))
                current_manifest["artifacts"]["review_queue"]["sha256"] = (
                    file_sha256(queue_path)
                )
                atomic_write_json(
                    self.workspace / ".ingest" / "build_manifest.json",
                    current_manifest,
                )

                errors, _warnings, _stats = validate_workspace.validate(
                    str(self.workspace)
                )
                self.assertTrue(
                    any(
                        "authoritative review ledger patch" in entry["msg"]
                        for entry in errors
                    ),
                    errors,
                )

    def test_failed_parser_accepts_ledger_backed_unrecoverable_terminal_issue(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [],
            report={
                "warnings": [],
                "ai_review": [],
                "skipped": [{"file": "ch01.txt", "why": "parser failed"}],
            },
        )
        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            issue for issue in store.review_queue.issues()
            if issue.severity == "blocking"
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [{
                "op": "mark_unrecoverable",
                "reason": "The configured parser cannot recover this source.",
            }],
            list(issue.evidence),
            reviewer="test",
            created_at="2026-07-14T12:00:00Z",
            status="validated",
        )
        result = store.apply_patch(patch)
        self.assertEqual("unrecoverable", result.issue_status)

        # Re-ingestion binds the queue and authoritative ledger into a fresh
        # manifest; the terminal issue remains valid parser evidence.
        persist_payload(self.workspace, payload)
        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertFalse(
            any(
                "parser receipt lacks an exact blocking typed issue" in entry["msg"]
                for entry in errors
            ),
            errors,
        )

    def test_review_route_requires_exact_active_source_location_and_reason_issue(self):
        payload = self.payload()
        review_row = next(
            row for row in payload["page_quality"] if row["route"] == "review"
        )
        exact_index = next(
            index for index, row in enumerate(payload["review_candidates"])
            if row["source_file"] == review_row["source_file"]
            and row["pages"] == [review_row["page"]]
            and row["reason_codes"] == review_row["reason_codes"]
        )

        wrong_reason = json.loads(json.dumps(payload))
        wrong_reason["review_candidates"][exact_index]["reason_codes"] = ["other_reason"]
        with self.assertRaisesRegex(ValueError, "route=review lacks an exact active"):
            persist_payload(self.workspace, wrong_reason)

        wrong_location = json.loads(json.dumps(payload))
        wrong_location["review_candidates"][exact_index]["pages"] = [1]
        with self.assertRaisesRegex(ValueError, "route=review lacks an exact active"):
            persist_payload(self.workspace, wrong_location)

        wrong_severity = json.loads(json.dumps(payload))
        wrong_severity["review_candidates"][exact_index]["severity"] = "warning"
        with self.assertRaisesRegex(ValueError, "route=review lacks an exact active"):
            persist_payload(self.workspace, wrong_severity)

        review_receipt = json.loads(json.dumps(payload))
        review_receipt["parser_receipts"][0]["status"] = "review_required"
        persist_payload(self.workspace, review_receipt)

        unrelated = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1, "text": "clean parser output",
                "source_language": "en",
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        unrelated["parser_receipts"][0]["status"] = "review_required"
        unrelated["review_candidates"].append({
            "reason_codes": ["unrelated_page_problem"],
            "source_file": "ch01.txt",
            "pages": [2],
            "severity": "blocking",
            "description": "Issue points at a different location.",
            "suggested_action": "Inspect page 2.",
            "target_unit_ids": [],
        })
        with self.assertRaisesRegex(ValueError, "exact active blocking issue"):
            persist_payload(self.workspace, unrelated)

    def test_review_route_allows_issue_to_carry_additional_reason_codes(self):
        payload = self.payload()
        review_row = next(
            row for row in payload["page_quality"] if row["route"] == "review"
        )
        candidate = next(
            row for row in payload["review_candidates"]
            if row["source_file"] == review_row["source_file"]
            and row["pages"] == [review_row["page"]]
            and row["reason_codes"] == review_row["reason_codes"]
        )
        candidate["reason_codes"] = sorted(set(
            candidate["reason_codes"] + ["manual_attention"]
        ))

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        matching = [
            issue for issue in store.review_queue.issues()
            if issue.status in ("pending", "claimed", "validated", "blocked")
            and issue.severity == "blocking"
            and issue.pages == (review_row["page"],)
            and set(review_row["reason_codes"]).issubset(issue.reason_codes)
        ]
        self.assertEqual(1, len(matching))
        self.assertIn("manual_attention", matching[0].reason_codes)

    def test_validator_rejects_terminalized_issue_while_page_still_routes_to_review(self):
        payload = self.payload()
        manifest = persist_payload(self.workspace, payload)
        review_row = next(
            row for row in payload["page_quality"] if row["route"] == "review"
        )
        queue_path = self.workspace / ".ingest" / "review_queue.jsonl"
        rows = [
            json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        changed = False
        for row in rows:
            if (row["pages"] == [review_row["page"]]
                    and row["reason_codes"] == review_row["reason_codes"]):
                row["status"] = "resolved"
                changed = True
        self.assertTrue(changed)
        atomic_write_jsonl(queue_path, rows)
        manifest["artifacts"]["review_queue"]["sha256"] = file_sha256(queue_path)
        atomic_write_json(
            self.workspace / ".ingest" / "build_manifest.json", manifest
        )

        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertTrue(any(
            "route=review lacks an exact active" in entry["msg"]
            for entry in errors
        ), errors)

    def test_recover_route_warning_remains_non_blocking_parser_inventory(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1, "text": "recoverable layout",
                "source_language": "en",
                "quality_signals": {
                    "score": 0.7,
                    "route": "recover",
                    "reason_codes": ["layout_uncertain"],
                },
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        self.assertEqual("success", payload["parser_receipts"][0]["status"])
        manifest = persist_payload(self.workspace, payload)
        self.assertEqual("ingestion-v2", manifest["pipeline_version"])
        store = IngestionStore(self.workspace, source_root=self.materials)
        warning_issue = next(
            issue for issue in store.review_queue.issues()
            if issue.reason_codes == ("layout_uncertain",)
        )
        self.assertEqual("warning", warning_issue.severity)

        errors, warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertFalse(any(
            "parser receipt" in entry["msg"] and "涓嶄竴鑷?" in entry["msg"]
            for entry in errors
        ), errors)
        self.assertTrue(any(
            warning_issue.issue_id in entry["msg"] for entry in warnings
        ))

    def test_answer_conflict_enters_typed_queue_and_terminal_decision_replays(self):
        second = self.materials / "alternate.txt"
        second.write_text("Chapter 1\nAlternate key", encoding="utf-8")
        payload = build_payload(
            str(self.materials),
            [str(self.source), str(second)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Chapter 1\nWhat is 2+2?"},
                {"file": "alternate.txt", "page": 1,
                 "text": "Chapter 1\nWhat is 2+2?"},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("alternate.txt", 1)],
            }],
            quiz_items=[
                {
                    "id": "key-a", "chapter": 1, "type": "subjective",
                    "question": "What is 2+2?", "answer": "4",
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
                {
                    "id": "key-b", "chapter": 1, "type": "subjective",
                    "question": "What is 2+2?", "answer": "5",
                    "source": "material", "source_file": "alternate.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
            ],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        manifest = persist_payload(self.workspace, payload)
        conflicts = load_source_conflicts(self.workspace)
        self.assertEqual(1, len(conflicts))
        self.assertEqual("answer_mismatch", conflicts[0].conflict_kind)
        self.assertEqual("unresolved", conflicts[0].status)
        self.assertIsNotNone(conflicts[0].review_issue_id)
        self.assertFalse(any(
            set(ref.unit_id for ref in group.member_refs)
            == set(member.unit_ref.unit_id for member in conflicts[0].members)
            for group in load_canonical_groups(self.workspace)
        ))

        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = store.review_queue.get(conflicts[0].review_issue_id)
        self.assertIsNotNone(issue)
        self.assertEqual("blocking", issue.severity)
        self.assertIn("source_conflict", issue.reason_codes)
        validation_errors, validation_warnings, _stats = validate_workspace.validate(
            str(self.workspace)
        )
        self.assertTrue(any(
            conflicts[0].conflict_id in entry["msg"] for entry in validation_errors
        ))
        self.assertFalse(any(
            conflicts[0].conflict_id in entry["msg"] for entry in validation_warnings
        ))
        build_manifest_path = self.workspace / ".ingest" / "build_manifest.json"
        stale_manifest = json.loads(json.dumps(manifest))
        stale_manifest["fact_summary"]["source_conflict_count"] = 0
        build_manifest_path.write_text(
            json.dumps(stale_manifest, ensure_ascii=False), encoding="utf-8"
        )
        stale_errors, _stale_warnings, _stale_stats = validate_workspace.validate(
            str(self.workspace)
        )
        self.assertTrue(any(
            "fact_summary.source_conflict_count" in entry["msg"]
            for entry in stale_errors
        ))

        capped_manifest = json.loads(json.dumps(manifest))
        capped_manifest["fact_summary"]["stats"]["near_truncated"] = True
        capped_manifest["fact_summary"]["warnings"] = [
            "near_candidate_comparison_cap_reached"
        ]
        build_manifest_path.write_text(
            json.dumps(capped_manifest, ensure_ascii=False), encoding="utf-8"
        )
        capped_errors, capped_warnings, _capped_stats = validate_workspace.validate(
            str(self.workspace)
        )
        self.assertTrue(any(
            "fact_summary.stats" in entry["msg"] for entry in capped_errors
        ))
        self.assertFalse(any(
            "near-duplicate recall" in entry["msg"] for entry in capped_warnings
        ))
        build_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        evidence_path = self.workspace.joinpath(*issue.evidence[0].path.split("/"))
        self.assertTrue(evidence_path.is_file())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(2, len(evidence["locations"]))

        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [{"op": "mark_unrecoverable",
              "reason": "The two current official keys genuinely disagree."}],
            list(issue.evidence),
            reviewer="test",
            created_at="2026-07-14T12:00:00Z",
            status="validated",
        )
        applied = store.apply_patch(patch)
        self.assertEqual("unrecoverable", applied.issue_status)

        # Re-ingesting the same revisions must rebuild conflict state from the
        # append-only ledger rather than reverting the sidecar to unresolved.
        replayed_manifest = persist_payload(self.workspace, payload)
        replayed = load_source_conflicts(self.workspace)
        self.assertEqual("unrecoverable", replayed[0].status)
        self.assertEqual(patch.patch_id, replayed[0].resolution.patch_id)
        self.assertEqual("unrecoverable", replayed[0].resolution.action)
        self.assertEqual(1, replayed_manifest["fact_summary"]["source_conflict_count"])
        self.assertEqual(manifest["fact_summary"]["source_conflict_count"], 1)
        terminal_errors, _terminal_warnings, terminal_stats = (
            validate_workspace.validate(str(self.workspace))
        )
        self.assertFalse(any(
            "确定性重算或 ledger replay" in entry["msg"]
            for entry in terminal_errors
        ), terminal_errors)
        self.assertEqual(1, terminal_stats["ingestion_source_conflicts"])

    def test_validator_rejects_empty_fact_graph_for_live_near_answer_conflict(self):
        manifest = persist_payload(self.workspace, self.near_answer_conflict_payload())
        self.assertGreater(manifest["fact_summary"]["source_conflict_count"], 0)

        for label, count_field in (
            ("duplicate_candidates", "duplicate_candidate_count"),
            ("canonical_groups", "canonical_group_count"),
            ("source_conflicts", "source_conflict_count"),
        ):
            self.rewrite_bound_artifact(manifest, label, b"")
            manifest["fact_summary"][count_field] = 0
        self.write_build_manifest(manifest)

        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertTrue(
            any("duplicate candidate base rows" in entry["msg"] for entry in errors),
            " | ".join(entry["msg"] for entry in errors),
        )

    def test_validator_rejects_forged_permissive_fact_config_and_empty_graph(self):
        alternate = self.materials / "alternate.txt"
        alternate.write_text("alternate", encoding="utf-8")
        answers = (
            "The final value is 10 after applying the recurrence relation and "
            "checking the boundary condition.",
            "The final value is 11 after applying the recurrence relation and "
            "checking the boundary condition.",
        )
        payload = build_payload(
            str(self.materials),
            [str(self.source), str(alternate)],
            [
                {
                    "file": "ch01.txt", "page": 1, "text": "",
                    "elements": [{
                        "kind": "answer", "text": answers[0],
                        "source_language": "en",
                    }],
                },
                {
                    "file": "alternate.txt", "page": 1, "text": "",
                    "elements": [{
                        "kind": "answer", "text": answers[1],
                        "source_language": "en",
                    }],
                },
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("alternate.txt", 1)],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        manifest = persist_payload(self.workspace, payload)
        self.assertGreater(manifest["fact_summary"]["source_conflict_count"], 0)

        store = IngestionStore(self.workspace)
        forged_config = DedupConfig(min_near_chars=10_000)
        forged = build_dedup_facts(
            store.units().values(),
            store.manifest.records(),
            config=forged_config,
            priorities=load_source_priorities(self.workspace),
        )
        self.assertEqual(0, len(forged["candidates"]))
        self.assertEqual(0, len(forged["canonical_groups"]))
        self.assertEqual(0, len(forged["conflicts"]))

        for label, fact_key, count_field in (
            ("duplicate_candidates", "candidates", "duplicate_candidate_count"),
            ("canonical_groups", "canonical_groups", "canonical_group_count"),
            ("source_conflicts", "conflicts", "source_conflict_count"),
        ):
            encoded = "".join(
                json.dumps(
                    row.to_dict(), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
                for row in forged[fact_key]
            ).encode("utf-8")
            self.rewrite_bound_artifact(manifest, label, encoded)
            manifest["fact_summary"][count_field] = len(forged[fact_key])
        manifest["fact_summary"]["config"] = forged_config.to_dict()
        manifest["fact_summary"]["config_sha256"] = forged_config.config_sha256
        manifest["fact_summary"]["stats"] = forged["stats"]
        manifest["fact_summary"]["warnings"] = list(forged["warnings"])
        self.write_build_manifest(manifest)

        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        messages = " | ".join(entry["msg"] for entry in errors)
        self.assertIn("canonical default DedupConfig", messages)

    def test_validator_rejects_superseded_conflict_without_ledger_replay_basis(self):
        manifest = persist_payload(self.workspace, self.near_answer_conflict_payload())
        conflict_path = self.workspace.joinpath(
            *manifest["artifacts"]["source_conflicts"]["path"].split("/")
        )
        rows = [
            json.loads(line) for line in conflict_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertGreater(len(rows), 0)
        rows[0]["status"] = "superseded"
        rows[0]["resolution"] = None
        encoded = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ).encode("utf-8")
        self.rewrite_bound_artifact(manifest, "source_conflicts", encoded)
        self.write_build_manifest(manifest)

        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertTrue(
            any("source conflict final rows" in entry["msg"] for entry in errors),
            " | ".join(entry["msg"] for entry in errors),
        )

    def test_adapter_quality_method_and_structured_metadata_survive_normalization(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt",
                "page": 1,
                "text": "x = 4",
                "quality_signals": {
                    "score": 0.91, "route": "fast", "reason_codes": ["adapter_layout"],
                },
                "metadata": {"sheet_name": "Signals", "page_equivalent": "worksheet"},
                "elements": [{
                    "kind": "formula", "text": "x = 4", "latex": "x=4",
                    "ordinal": 0, "bbox": [1, 2, 3, 4],
                    "method": "ocr", "confidence": 0.88,
                    "metadata": {"cell": "B2", "formula": "=2+2"},
                }],
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        anchor = next(row for row in payload["content_units"]
                      if row["kind"] == "page_anchor")
        formula = next(row for row in payload["content_units"]
                       if row["kind"] == "formula")
        self.assertEqual(0.91, anchor["confidence"])
        self.assertEqual("worksheet", anchor["metadata"]["parser_metadata"]["page_equivalent"])
        self.assertEqual("ocr", formula["method"])
        self.assertEqual(0.88, formula["confidence"])
        self.assertEqual("B2", formula["metadata"]["parser_metadata"]["cell"])
        persist_payload(self.workspace, payload)

    def test_legacy_v1_payload_remains_readable_without_claiming_v2_receipts(self):
        payload = self.payload()
        payload["schema_version"] = 1
        payload.pop("parser_receipts")
        manifest = persist_payload(self.workspace, payload)
        self.assertEqual("ingestion-v1", manifest["pipeline_version"])
        self.assertFalse((self.workspace / ".ingest" / "parser_receipts.json").exists())

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

    def test_source_language_is_persisted_or_routed_to_typed_review(self):
        explicit = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1, "text": "Chapter 1\nCore concept",
              "source_language": "en"}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "language-q1", "chapter": 1, "type": "subjective",
                "question": "Explain the core concept.", "answer": "Official answer",
                "source": "material", "source_file": "ch01.txt", "source_pages": [1],
                "source_language": "en", "answer_source_language": "en",
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = next(row for row in explicit["content_units"]
                        if row["kind"] == "question")
        answer = next(row for row in explicit["content_units"]
                      if row["kind"] == "answer")
        self.assertEqual("en", question["metadata"]["source_language"])
        self.assertEqual("en", answer["metadata"]["source_language"])
        self.assertFalse(any(
            "source_language_unknown" in row["reason_codes"]
            for row in explicit["review_candidates"]
        ))

        unknown = self.payload(missing_answer=False)
        language_issues = [
            row for row in unknown["review_candidates"]
            if set(row["reason_codes"]) & {
                "source_language_unknown", "answer_source_language_unknown"
            }
        ]
        self.assertEqual(3, len(language_issues))
        self.assertEqual({"blocking", "warning"}, {
            row["severity"] for row in language_issues
        })

    def test_mixed_and_formula_only_semantic_units_enter_typed_language_review(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "Explain the result，并说明中文条件。",
            }, {
                "file": "ch01.txt", "page": 2, "text": "V=IR",
                "elements": [{
                    "kind": "formula", "text": "V=IR", "latex": "V=IR",
                    "ordinal": 0, "bbox": None,
                }],
            }],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("ch01.txt", 2)],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        semantic = [row for row in payload["content_units"]
                    if row["kind"] in ("text", "formula")]
        self.assertEqual(2, len(semantic))
        self.assertTrue(all(
            "source_language" not in row["metadata"] for row in semantic
        ))
        reviewed = {
            target
            for row in payload["review_candidates"]
            if "source_language_unknown" in row["reason_codes"]
            for target in row["target_unit_ids"]
        }
        self.assertEqual({row["unit_id"] for row in semantic}, reviewed)
        self.assertTrue(all(
            row["severity"] == "blocking"
            for row in payload["review_candidates"]
            if "source_language_unknown" in row["reason_codes"]
        ))

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
