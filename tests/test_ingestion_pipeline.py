import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.asset_policy import physical_asset_key
from scripts import retrieve, validate_workspace
from scripts.ingestion import (
    ContentUnit,
    IngestionStore,
    PatchApplicationError,
    ReviewPatch,
    render_answer_value,
)
from scripts.ingestion.dedup import (
    DedupConfig,
    build_dedup_facts,
    load_canonical_groups,
    load_source_conflicts,
    load_source_priorities,
)
from scripts.ingestion.identifiers import file_sha256
from scripts.ingestion.pipeline import (
    _assert_publishable_qa,
    _deduplicate_bound_candidates,
    _metadata_assets,
    _new_quiz_item,
    _update_quiz_item_from_units,
    build_payload,
    compile_review_outputs,
    compile_structured_visuals,
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

    @staticmethod
    def asset_unit(unit_id, assets=(), asset_path=None, asset_role=None):
        return SimpleNamespace(
            unit_id=unit_id,
            metadata={"assets": list(assets)},
            asset_path=asset_path,
            asset_role=asset_role,
        )

    def test_metadata_assets_is_attempt_dominant_order_invariant_and_canonical(self):
        official = self.asset_unit("official", assets=({
            "path": "references/assets/X.png",
            "role": "question_context",
            "type": "crop_image",
        },))
        attempt = self.asset_unit(
            "attempt",
            asset_path="references\\assets\\X.png",
            asset_role="student_attempt",
        )
        first = _metadata_assets(official, attempt)
        second = _metadata_assets(attempt, official)
        self.assertEqual(first, second)
        self.assertEqual("references/assets/X.png", first[0]["path"])
        self.assertEqual("student_attempt", first[0]["role"])

    def test_metadata_assets_rejects_missing_role_and_conflicting_metadata(self):
        missing = self.asset_unit("missing", assets=({
            "path": "references/assets/x.png",
            "type": "crop_image",
        },))
        with self.assertRaisesRegex(ValueError, "role"):
            _metadata_assets(missing)

        first = self.asset_unit("first", assets=({
            "path": "references/assets/x.png",
            "role": "question_context",
            "caption": "first",
        },))
        second = self.asset_unit("second", assets=({
            "path": "references/assets/x.png",
            "role": "figure",
            "caption": "second",
        },))
        with self.assertRaisesRegex(ValueError, "conflicting caption"):
            _metadata_assets(first, second)

    def test_publishable_qa_rejects_direct_leak_but_allows_distinct_attempt_path(self):
        question = self.asset_unit("question", assets=({
            "path": "references/assets/prompt.png", "role": "question_context",
        }, {
            "path": "references/assets/attempt.png", "role": "student_attempt",
        }))
        answer = self.asset_unit("answer", assets=({
            "path": "references/assets/answer.png", "role": "worked_solution",
        },))
        tainted = {
            physical_asset_key("references/assets/attempt.png")
        }
        _assert_publishable_qa(question, answer, tainted)
        assets = _metadata_assets(question, answer, tainted_keys=tainted)
        self.assertEqual(
            {"question_context", "worked_solution", "student_attempt"},
            {asset["role"] for asset in assets},
        )

        leaking_answer = self.asset_unit("leak", assets=({
            "path": "references/assets/prompt.png", "role": "worked_solution",
        },))
        with self.assertRaisesRegex(ValueError, "both prompt and answer"):
            _assert_publishable_qa(question, leaking_answer, tainted)

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

    def test_validator_rejects_corrupt_content_unit_jpeg_with_matching_hash(self):
        payload = self.payload(missing_answer=False)
        seed = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "text"
        ))
        relative = "references/assets/corrupt-source.jpg"
        corrupt = b"\xff\xd8\xff\xd9"
        asset = self.workspace.joinpath(*relative.split("/"))
        asset.parent.mkdir(parents=True)
        asset.write_bytes(corrupt)
        figure = ContentUnit.create(
            seed.source_id,
            seed.source_sha256,
            seed.source_file,
            "figure",
            "Official source figure",
            1,
            ordinal=901,
            chapter_id="ch01",
            phase_id="phase01",
            asset_path=relative,
            asset_role="figure",
            metadata={"asset_sha256": hashlib.sha256(corrupt).hexdigest()},
        )
        payload["content_units"].append(figure.to_dict())
        persist_payload(self.workspace, payload)

        errors, _warnings, _stats = validate_workspace.validate(
            str(self.workspace)
        )
        self.assertTrue(any(
            "ContentUnit 光栅资产损坏" in entry["msg"]
            and figure.unit_id in entry["msg"]
            for entry in errors
        ), errors)

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

    def test_supplied_fast_quality_cannot_hide_local_formula_evidence(self):
        samples = [
            "This page introduces the next lecture topic in ordinary English prose.",
            "Use the probability notation P(A) for event A.",
            "Set identities include A ∪ B, A ∩ B, and x ∈ A.",
            "Conditional probability is P(A|B) = P(A ∩ B)/P(B).",
            "The measured probability is 3/4.",
            "P(A)=12/16=3/4; P(B)=6/16; P(A∩B)=3/16.",
        ]
        pages = [{
            "file": "ch01.txt",
            "page": index,
            "text": sample,
            "source_language": "en",
            "quality_signals": {
                "score": 1.0,
                "route": "fast",
                "reason_codes": [],
            },
        } for index, sample in enumerate(samples, 1)]
        payload = build_payload(
            str(self.materials), [str(self.source)], pages,
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", page) for page in range(1, len(pages) + 1)],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )

        quality_by_page = {row["page"]: row for row in payload["page_quality"]}
        self.assertEqual("fast", quality_by_page[1]["route"])
        self.assertNotIn("formula_hint", quality_by_page[1]["reason_codes"])
        unit_index = {row["unit_id"]: row for row in payload["content_units"]}
        for page in range(2, len(pages) + 1):
            self.assertEqual("recover", quality_by_page[page]["route"])
            self.assertIn("formula_hint", quality_by_page[page]["reason_codes"])
            candidate = next(
                row for row in payload["review_candidates"]
                if row["pages"] == [page] and "formula_hint" in row["reason_codes"]
            )
            self.assertEqual([], candidate["target_unit_ids"])
            self.assertFalse(any(
                row["kind"] == "formula" and row["page"] == page
                for row in payload["content_units"]
            ), "formula hints must request source-backed review, not invent formula units")

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            issue for issue in store.review_queue.issues()
            if issue.pages == (2,) and "formula_hint" in issue.reason_codes
        )
        generic_resolution = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [{"op": "mark_resolved", "reason": "generic acknowledgement"}],
            list(issue.evidence),
            reviewer="test-vision-reviewer",
            created_at="2026-07-15T11:59:59Z",
            status="validated",
        )
        with self.assertRaisesRegex(
                PatchApplicationError, "evidence-backed formula unit"):
            store.apply_patch(generic_resolution)
        recovered = ContentUnit.create(
            issue.source_id, issue.source_sha256, "ch01.txt", "formula",
            "P(A)", 2, ordinal=50, latex=r"P(A)", chapter_id="ch01",
            phase_id="phase01", metadata={"source_language": "en"},
            method="ai_recovered", confidence=1.0, provenance="ai_recovered",
        )
        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [
                {"op": "add_unit", "unit": recovered.to_dict()},
            ],
            list(issue.evidence),
            reviewer="test-vision-reviewer",
            created_at="2026-07-15T12:00:00Z",
            status="validated",
        )
        store.apply_patch(patch)
        self.assertEqual("formula", store.units()[recovered.unit_id].kind)
        self.assertEqual("ch01", store.units()[recovered.unit_id].chapter_id)
        self.assertEqual("applied", store.review_queue.get(issue.issue_id).status)

    def test_supplied_review_route_and_adapter_reason_survive_local_merge(self):
        payload = build_payload(
            str(self.materials), [str(self.source)], [{
                "file": "ch01.txt", "page": 1,
                "text": "Ordinary source prose still requires adapter-declared review.",
                "source_language": "en",
                "quality_signals": {
                    "score": 0.2,
                    "route": "review",
                    "reason_codes": ["adapter_ocr_failed"],
                },
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        quality = payload["page_quality"][0]
        self.assertEqual("review", quality["route"])
        self.assertEqual(0.2, quality["score"])
        self.assertIn("adapter_ocr_failed", quality["reason_codes"])
        candidate = next(
            row for row in payload["review_candidates"]
            if "adapter_ocr_failed" in row["reason_codes"]
        )
        self.assertTrue(candidate["target_unit_ids"])

    def test_local_table_layout_and_corruption_upgrade_supplied_fast_quality(self):
        supplied_fast = {"score": 0.99, "route": "fast", "reason_codes": []}
        pages = [
            {
                "file": "ch01.txt", "page": 1, "text": "Name\tValue",
                "source_language": "en", "quality_signals": supplied_fast,
                "elements": [{
                    "kind": "table", "text": "Name\tValue", "ordinal": 0,
                    "source_language": "en",
                }],
            },
            {
                "file": "ch01.txt", "page": 2, "text": "Left and right columns",
                "source_language": "en", "quality_signals": supplied_fast,
                "elements": [
                    {"kind": "text", "text": "L1", "bbox": [0, 0, 40, 20],
                     "source_language": "en"},
                    {"kind": "text", "text": "L2", "bbox": [0, 25, 40, 45],
                     "source_language": "en"},
                    {"kind": "text", "text": "R1", "bbox": [60, 0, 100, 20],
                     "source_language": "en"},
                    {"kind": "text", "text": "R2", "bbox": [60, 25, 100, 45],
                     "source_language": "en"},
                ],
            },
            {
                "file": "ch01.txt", "page": 3, "text": "damaged\x00extraction",
                "source_language": "en", "quality_signals": supplied_fast,
            },
        ]
        payload = build_payload(
            str(self.materials), [str(self.source)], pages,
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", page) for page in (1, 2, 3)],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        quality = {row["page"]: row for row in payload["page_quality"]}
        self.assertEqual("recover", quality[1]["route"])
        self.assertIn("table_hint", quality[1]["reason_codes"])
        self.assertEqual("recover", quality[2]["route"])
        self.assertIn("multi_column_hint", quality[2]["reason_codes"])
        self.assertEqual("recover", quality[3]["route"])
        self.assertIn("nul_or_replacement_char", quality[3]["reason_codes"])

        units = {row["unit_id"]: row for row in payload["content_units"]}
        for page, reason in ((1, "table_hint"), (2, "multi_column_hint")):
            candidate = next(
                row for row in payload["review_candidates"]
                if row["pages"] == [page] and reason in row["reason_codes"]
            )
            self.assertTrue(candidate["target_unit_ids"])
            self.assertTrue(all(
                unit_id in units and units[unit_id]["page"] == page
                for unit_id in candidate["target_unit_ids"]
            ))
        damaged = next(
            row for row in payload["content_units"]
            if row["kind"] == "text" and "\x00" in row["text"]
        )
        candidate = next(
            row for row in payload["review_candidates"]
            if row["pages"] == [3]
            and "nul_or_replacement_char" in row["reason_codes"]
        )
        self.assertEqual([damaged["unit_id"]], candidate["target_unit_ids"])

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

    def test_ai_review_external_ids_bind_the_exact_question_units(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1,
              "text": "Chapter 1\nPrinted prompt above a handwritten answer."}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "hw_ch01_1_1", "chapter": 1, "type": "subjective",
                "question": "Printed prompt crop", "answer": "Handwritten response",
                "source": "material", "source_type": "homework",
                "source_file": "ch01.txt", "source_pages": [1],
                "answer_source_pages": [1],
            }],
            report={
                "warnings": [], "skipped": [],
                "ai_review": [{
                    "kind": "homework_roster_visual_mapping_unverified",
                    "file": "ch01.txt", "pages": [1],
                    "external_ids": ["hw_ch01_1_1"],
                    "action": "Visually verify the roster-to-prompt crop mapping.",
                }],
            },
        )
        question = next(
            unit for unit in payload["content_units"]
            if unit["kind"] == "question" and unit["external_id"] == "hw_ch01_1_1"
        )
        review = next(
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["homework_roster_visual_mapping_unverified"]
        )
        self.assertEqual(review["target_unit_ids"], [question["unit_id"]])
        self.assertEqual(review["source_file"], "ch01.txt")
        self.assertEqual(review["pages"], [1])

    def test_scoped_type_review_does_not_expand_to_unrelated_questions(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Question one"},
                {"file": "ch01.txt", "page": 2, "text": "Question two"},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("ch01.txt", 2)],
            }],
            quiz_items=[
                {
                    "id": "type-q1", "chapter": 1, "type": "subjective",
                    "question": "Explain one.", "answer": "One.",
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
                {
                    "id": "type-q2", "chapter": 2, "type": "subjective",
                    "question": "Explain two.", "answer": "Two.",
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [2], "answer_source_pages": [2],
                    "source_language": "en", "answer_source_language": "en",
                },
            ],
            report={
                "warnings": [], "skipped": [],
                "ai_review": [{
                    "kind": "type_defaulted", "file": "ch01.txt", "pages": [1],
                    "external_ids": ["type-q1"],
                    "action": "Confirm this question type.",
                }],
            },
        )
        questions = {
            row["external_id"]: row for row in payload["content_units"]
            if row["kind"] == "question"
        }
        reviews = [
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["type_defaulted"]
        ]
        self.assertEqual(1, len(reviews))
        self.assertEqual([questions["type-q1"]["unit_id"]], reviews[0]["target_unit_ids"])
        self.assertNotIn(questions["type-q2"]["unit_id"], reviews[0]["target_unit_ids"])

        legacy = build_payload(
            str(self.materials),
            [str(self.source)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Question one"},
                {"file": "ch01.txt", "page": 2, "text": "Question two"},
            ],
            sections=[{
                "chapter": 1,
                "page_keys": [("ch01.txt", 1), ("ch01.txt", 2)],
            }],
            quiz_items=[
                {
                    "id": "type-q1", "chapter": 1, "type": "subjective",
                    "question": "Explain one.", "answer": "One.",
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [1], "answer_source_pages": [1],
                    "source_language": "en", "answer_source_language": "en",
                },
                {
                    "id": "type-q2", "chapter": 2, "type": "subjective",
                    "question": "Explain two.", "answer": "Two.",
                    "source": "material", "source_file": "ch01.txt",
                    "source_pages": [2], "answer_source_pages": [2],
                    "source_language": "en", "answer_source_language": "en",
                },
            ],
            report={
                "warnings": [], "skipped": [],
                "ai_review": [{
                    "kind": "type_defaulted", "file": "references/quiz_bank.json",
                    "action": "Legacy whole-bank type review.",
                }],
            },
        )
        legacy_review = next(
            row for row in legacy["review_candidates"]
            if row["reason_codes"] == ["type_defaulted"]
        )
        self.assertEqual(
            {row["unit_id"] for row in legacy["content_units"] if row["kind"] == "question"},
            set(legacy_review["target_unit_ids"]),
        )

    def test_missing_subjective_keywords_bind_official_answer_and_compile(self):
        solutions = self.materials / "solutions.txt"
        solutions.write_text("Official solution on page three", encoding="utf-8")
        payload = build_payload(
            str(self.materials),
            [str(self.source), str(solutions)],
            [
                {"file": "ch01.txt", "page": 1, "text": "Explain the result."},
                {"file": "solutions.txt", "page": 3,
                 "text": "Official solution on page three"},
            ],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "keyword-q1", "chapter": 1, "type": "subjective",
                "question": "Explain the result.",
                "answer": "Use the invariant and report 42.",
                "source": "material", "source_type": "homework",
                "source_file": "ch01.txt", "source_pages": [1],
                "answer_source_file": "solutions.txt", "answer_source_pages": [3],
                "source_language": "en", "answer_source_language": "en",
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "question"
        ))
        answer = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "answer"
        ))
        candidate = next(
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["subjective_keywords_missing"]
        )
        self.assertEqual("solutions.txt", candidate["source_file"])
        self.assertEqual([3], candidate["pages"])
        self.assertEqual([answer.unit_id], candidate["target_unit_ids"])
        self.assertNotEqual(question.source_id, answer.source_id)

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            issue for issue in store.review_queue.issues()
            if issue.reason_codes == ("subjective_keywords_missing",)
        )
        self.assertEqual(answer.source_id, issue.source_id)
        self.assertEqual(answer.source_sha256, issue.source_sha256)

        invalid = answer.to_dict()
        invalid["method"] = "manual"
        invalid["confidence"] = 1.0
        invalid_patch = ReviewPatch.create(
            issue.issue_id, issue.source_id, issue.source_sha256,
            [{"op": "replace_unit", "unit_id": answer.unit_id, "unit": invalid}],
            list(issue.evidence), reviewer="test",
            created_at="2026-07-15T12:00:00Z", status="validated",
        )
        with self.assertRaisesRegex(
                PatchApplicationError, "subjective_keywords_missing postcondition"):
            store.validate_patch(invalid_patch)

        curated = answer.to_dict()
        curated["method"] = "manual"
        curated["confidence"] = 1.0
        curated["metadata"] = dict(curated["metadata"])
        curated["metadata"]["keywords"] = ["invariant", "final value 42"]
        patch = ReviewPatch.create(
            issue.issue_id, issue.source_id, issue.source_sha256,
            [{"op": "replace_unit", "unit_id": answer.unit_id, "unit": curated}],
            list(issue.evidence), reviewer="test",
            created_at="2026-07-15T12:01:00Z", status="validated",
        )
        store.apply_patch(patch)
        compiled_units = store.units()
        curated_answer = compiled_units[answer.unit_id]
        self.assertEqual(["invariant", "final value 42"], curated_answer.metadata["keywords"])

        new_item = _new_quiz_item(compiled_units[question.unit_id], curated_answer)
        self.assertEqual(["invariant", "final value 42"], new_item["keywords"])
        existing = {
            "id": "keyword-q1", "chapter": 1, "type": "subjective",
            "question": question.text, "answer": answer.text,
            "source": "material", "keywords": ["stale"],
        }
        updates = _update_quiz_item_from_units(
            existing, compiled_units[question.unit_id], curated_answer,
            {curated_answer.unit_id},
        )
        self.assertGreater(updates, 0)
        self.assertEqual(["invariant", "final value 42"], existing["keywords"])

    def test_missing_quiz_type_defaults_to_subjective_for_keyword_review(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1,
              "text": "Explain the result. Official answer: use the invariant."}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "legacy-no-type", "chapter": 1,
                "question": "Explain the result.",
                "answer": "Use the invariant.",
                "source": "material", "source_type": "homework",
                "source_file": "ch01.txt", "source_pages": [1],
                "source_language": "en", "answer_source_language": "en",
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        answer = next(
            row for row in payload["content_units"] if row["kind"] == "answer"
        )
        candidate = next(
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["subjective_keywords_missing"]
        )
        self.assertEqual([answer["unit_id"]], candidate["target_unit_ids"])

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

    def test_extended_quiz_metadata_survives_ir_and_compile_mappings(self):
        source_item = {
            "id": "code-contract-q1",
            "chapter": 1,
            "type": "code",
            "question": "Implement solve(values).",
            "answer": "def solve(values): return sorted(values)",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
            "answer_source_pages": [1],
            "source_language": "en",
            "answer_source_language": "en",
            "gradable": True,
            "question_text_status": "full",
            "diagram_type": "control_flow",
            "language": "python",
            "expected_behavior": "Return values in ascending order.",
            "tests": ["assert solve([2, 1]) == [1, 2]"],
        }
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "Chapter 1\nImplement solve(values).",
                "source_language": "en",
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[source_item],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "question"
        ))
        answer = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "answer"
        ))
        question_fields = (
            "gradable", "question_text_status", "diagram_type", "language",
            "expected_behavior", "tests",
        )
        for field in question_fields:
            self.assertEqual(source_item[field], question.metadata[field])

        new_item = _new_quiz_item(question, answer)
        for field in question_fields + ("source_language",):
            self.assertEqual(source_item[field], new_item[field])
        self.assertEqual("en", new_item["answer_source_language"])

        existing = {
            "id": source_item["id"],
            "chapter": 99,
            "type": "subjective",
            "question": "stale",
            "answer": "stale",
            "source": "material",
        }
        updates = _update_quiz_item_from_units(
            existing, question, answer, {question.unit_id, answer.unit_id}
        )
        self.assertGreater(updates, 0)
        for field in question_fields + ("source_language",):
            self.assertEqual(source_item[field], existing[field])
        self.assertEqual("en", existing["answer_source_language"])

    def test_touched_units_remove_stale_optional_quiz_fields(self):
        source_item = {
            "id": "metadata-delete-q1",
            "chapter": 1,
            "type": "code",
            "question": "Implement solve(values).",
            "answer": "return sorted(values)",
            "source": "material",
            "source_file": "ch01.txt",
            "source_pages": [1],
            "answer_source_pages": [1],
            "source_language": "en",
            "answer_source_language": "en",
            "gradable": True,
            "question_text_status": "full",
            "language": "python",
            "tests": ["assert solve([2, 1]) == [1, 2]"],
        }
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "Chapter 1\nImplement solve(values).",
                "source_language": "en",
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[source_item],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "question"
        ))
        answer = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "answer"
        ))
        question_dict = question.to_dict()
        question_dict["metadata"] = {"quiz_type": "code"}
        clean_question = ContentUnit.from_dict(question_dict)
        answer_dict = answer.to_dict()
        answer_dict["metadata"] = {
            "answer_value": answer.metadata["answer_value"],
        }
        clean_answer = ContentUnit.from_dict(answer_dict)

        existing = _new_quiz_item(question, answer)
        existing.update({
            "source_file": "stale.pdf",
            "source_pages": [99],
            "answer_source_file": "stale-solutions.pdf",
            "answer_source_pages": [98],
            "options": ["stale"],
            "keywords": ["stale"],
            "knowledge_point": "stale",
            "knowledge_points": ["stale"],
            "source_type": "stale",
            "requires_assets": True,
            "maybe_requires_assets": True,
            "diagram_type": "stale",
            "expected_behavior": "stale",
            "assets": [{"path": "stale.png", "role": "question_context"}],
        })
        updates = _update_quiz_item_from_units(
            existing,
            clean_question,
            clean_answer,
            {clean_question.unit_id, clean_answer.unit_id},
        )

        self.assertGreater(updates, 0)
        self.assertEqual("metadata-delete-q1", existing["id"])
        self.assertEqual("code", existing["type"])
        self.assertEqual(clean_question.source_file, existing["source_file"])
        self.assertEqual([clean_question.page], existing["source_pages"])
        self.assertEqual(clean_answer.source_file, existing["answer_source_file"])
        self.assertEqual([clean_answer.page], existing["answer_source_pages"])
        self.assertIn("question_provenance", existing)
        self.assertIn("answer_provenance", existing)
        for field in (
                "options", "keywords", "knowledge_point", "knowledge_points",
                "source_type", "requires_assets", "maybe_requires_assets",
                "gradable", "question_text_status", "diagram_type", "language",
                "expected_behavior", "tests", "source_language",
                "answer_source_language", "assets"):
            self.assertNotIn(field, existing)

    def test_control_character_review_binds_replaceable_bad_unit(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "Chapter 1\x00damaged extraction",
                "source_language": "en",
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        damaged = next(
            row for row in payload["content_units"]
            if row["kind"] == "text" and "\x00" in row["text"]
        )
        candidate = next(
            row for row in payload["review_candidates"]
            if "nul_or_replacement_char" in row["reason_codes"]
        )
        self.assertEqual([damaged["unit_id"]], candidate["target_unit_ids"])

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            issue for issue in store.review_queue.issues()
            if "nul_or_replacement_char" in issue.reason_codes
        )
        self.assertEqual((damaged["unit_id"],), issue.target_unit_ids)
        still_damaged = dict(damaged)
        still_damaged["method"] = "manual"
        ineffective = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [{
                "op": "replace_unit",
                "unit_id": damaged["unit_id"],
                "unit": still_damaged,
            }],
            list(issue.evidence),
            reviewer="test",
            created_at="2026-07-14T12:00:00Z",
            status="validated",
        )
        with self.assertRaises(PatchApplicationError):
            store.apply_patch(ineffective)

        replacement = dict(damaged)
        replacement["text"] = "Chapter 1 recovered extraction"
        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [{
                "op": "replace_unit",
                "unit_id": damaged["unit_id"],
                "unit": replacement,
            }],
            list(issue.evidence),
            reviewer="test",
            created_at="2026-07-14T12:00:00Z",
            status="validated",
        )
        store.apply_patch(patch)
        self.assertNotIn("\x00", store.units()[damaged["unit_id"]].text)

    def test_mixed_formula_and_control_page_keeps_two_obligations(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "P(A)=12/16=3/4\x00damaged extraction",
                "source_language": "en",
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        formula_candidate = next(
            row for row in payload["review_candidates"]
            if row["reason_codes"] == ["formula_hint"]
        )
        control_candidate = next(
            row for row in payload["review_candidates"]
            if "nul_or_replacement_char" in row["reason_codes"]
        )
        self.assertEqual([], formula_candidate["target_unit_ids"])
        self.assertTrue(control_candidate["target_unit_ids"])
        self.assertNotIn("formula_hint", control_candidate["reason_codes"])

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        formula_issue = next(
            issue for issue in store.review_queue.issues()
            if issue.reason_codes == ("formula_hint",)
        )
        control_issue = next(
            issue for issue in store.review_queue.issues()
            if "nul_or_replacement_char" in issue.reason_codes
        )
        repair_operations = []
        for unit_id in control_issue.target_unit_ids:
            unit = store.units()[unit_id]
            repaired = unit.to_dict()
            repaired["text"] = repaired["text"].replace("\x00", " ")
            repair_operations.append({
                "op": "replace_unit", "unit_id": unit_id, "unit": repaired,
            })
        store.apply_patch(ReviewPatch.create(
            control_issue.issue_id,
            control_issue.source_id,
            control_issue.source_sha256,
            repair_operations,
            list(control_issue.evidence),
            reviewer="test",
            created_at="2026-07-15T12:00:00Z",
            status="validated",
        ))
        self.assertEqual(
            "pending", store.review_queue.get(formula_issue.issue_id).status
        )
        with self.assertRaisesRegex(
                PatchApplicationError, "evidence-backed formula unit"):
            store.apply_patch(ReviewPatch.create(
                formula_issue.issue_id,
                formula_issue.source_id,
                formula_issue.source_sha256,
                [{"op": "mark_resolved", "reason": "control bytes are fixed"}],
                list(formula_issue.evidence),
                reviewer="test",
                created_at="2026-07-15T12:00:01Z",
                status="validated",
            ))

        formula = ContentUnit.create(
            formula_issue.source_id,
            formula_issue.source_sha256,
            "ch01.txt",
            "formula",
            "P(A)=12/16=3/4",
            1,
            ordinal=99,
            latex=r"P(A)=\frac{12}{16}=\frac{3}{4}",
            chapter_id="ch01",
            phase_id="phase01",
            metadata={"source_language": "en"},
            method="ai_recovered",
            confidence=1.0,
            provenance="ai_recovered",
        )
        store.apply_patch(ReviewPatch.create(
            formula_issue.issue_id,
            formula_issue.source_id,
            formula_issue.source_sha256,
            [{"op": "add_unit", "unit": formula.to_dict()}],
            list(formula_issue.evidence),
            reviewer="test-vision-reviewer",
            created_at="2026-07-15T12:00:02Z",
            status="validated",
        ))
        self.assertEqual(
            "applied", store.review_queue.get(formula_issue.issue_id).status
        )

    def test_control_postcondition_covers_inherited_section_path(self):
        bad_heading = "Chapter 1\x00Damaged"
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": bad_heading + "\nBody",
                "source_language": "en",
                "elements": [
                    {"kind": "heading", "text": bad_heading, "ordinal": 0,
                     "level": 1, "source_language": "en"},
                    {"kind": "text", "text": "Body", "ordinal": 1,
                     "source_language": "en"},
                ],
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        units = {row["unit_id"]: row for row in payload["content_units"]}
        candidate = next(
            row for row in payload["review_candidates"]
            if "nul_or_replacement_char" in row["reason_codes"]
        )
        self.assertEqual(
            {"heading", "text"},
            {units[unit_id]["kind"] for unit_id in candidate["target_unit_ids"]},
        )
        body_id = next(
            unit_id for unit_id in candidate["target_unit_ids"]
            if units[unit_id]["kind"] == "text"
        )
        self.assertIn("\x00", units[body_id]["section_path"][0])

        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            issue for issue in store.review_queue.issues()
            if "nul_or_replacement_char" in issue.reason_codes
        )
        current = store.units()
        heading_id = next(
            unit_id for unit_id in issue.target_unit_ids
            if current[unit_id].kind == "heading"
        )
        repaired_heading = current[heading_id].to_dict()
        repaired_heading["text"] = "Chapter 1 Recovered"
        with self.assertRaisesRegex(
                PatchApplicationError, "unsafe control-text postcondition"):
            store.apply_patch(ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                [{"op": "replace_unit", "unit_id": heading_id,
                  "unit": repaired_heading}],
                list(issue.evidence),
                reviewer="test",
                created_at="2026-07-15T12:01:00Z",
                status="validated",
            ))
        self.assertIn("\x00", store.units()[heading_id].text)

        repaired_body = current[body_id].to_dict()
        repaired_body["section_path"] = ["Chapter 1 Recovered"]
        store.apply_patch(ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            [
                {"op": "replace_unit", "unit_id": heading_id,
                 "unit": repaired_heading},
                {"op": "replace_unit", "unit_id": body_id,
                 "unit": repaired_body},
            ],
            list(issue.evidence),
            reviewer="test",
            created_at="2026-07-15T12:01:01Z",
            status="validated",
        ))
        self.assertNotIn("\x00", store.units()[body_id].section_path[0])

    def test_source_language_is_persisted_or_routed_to_typed_review(self):
        explicit = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1, "text": "Chapter 1\nCore concept",
                "source_language": "en", "elements": [{
                    "kind": "text", "text": "Chapter 1\nCore concept", "ordinal": 0,
                    "bbox": None, "source_language": "en",
                }],
            }, {
                "file": "ch01.txt", "page": 2, "text": "V=IR",
                "source_language": "en",
            }],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "language-q1", "chapter": 1, "type": "subjective",
                "question": "Explain the core concept.", "answer": "Official answer",
                "source": "material", "source_file": "ch01.txt", "source_pages": [1],
                "source_language": "en", "answer_source_language": "en",
            }, {
                "id": "language-neutral-q2", "chapter": 1, "type": "subjective",
                "question": "P(A)=1/2", "answer": "1/2",
                "source": "material", "source_file": "ch01.txt", "source_pages": [1],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = next(row for row in explicit["content_units"]
                        if row["kind"] == "question"
                        and row["external_id"] == "language-q1")
        answer = next(row for row in explicit["content_units"]
                      if row["kind"] == "answer"
                      and row["external_id"] == "language-q1")
        self.assertEqual("en", question["metadata"]["source_language"])
        self.assertEqual("en", answer["metadata"]["source_language"])
        neutral_question = next(row for row in explicit["content_units"]
                                if row["kind"] == "question"
                                and row["external_id"] == "language-neutral-q2")
        neutral_answer = next(row for row in explicit["content_units"]
                              if row["kind"] == "answer"
                              and row["external_id"] == "language-neutral-q2")
        self.assertEqual("zxx", neutral_question["metadata"]["source_language"])
        self.assertEqual("zxx", neutral_answer["metadata"]["source_language"])
        fallback_formula = next(
            row for row in explicit["content_units"]
            if row["kind"] == "text" and row["page"] == 2
        )
        self.assertEqual("zxx", fallback_formula["metadata"]["source_language"])
        self.assertTrue(all(
            "source_language" not in row["metadata"]
            for row in explicit["content_units"] if row["kind"] == "page_anchor"
        ))
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
        self.assertEqual(2, len(language_issues))
        self.assertEqual({"blocking", "warning"}, {
            row["severity"] for row in language_issues
        })

    def test_mixed_semantic_unit_is_reviewed_but_formula_only_is_zxx(self):
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{
                "file": "ch01.txt", "page": 1,
                "text": "Explain the result，并说明中文条件。",
            }, {
                "file": "ch01.txt", "page": 2, "text": "V=IR",
                "source_language": "en",
                "elements": [{
                    "kind": "formula", "text": "V=IR", "latex": "V=IR",
                    "ordinal": 0, "bbox": None,
                }],
            }, {
                "file": "ch01.txt", "page": 3,
                "text": r"B_1=\{ttth,ttht,thtt,httt\}",
                "elements": [{
                    "kind": "formula", "text": r"B_1=\{ttth,ttht,thtt,httt\}",
                    "ordinal": 0, "bbox": None,
                }],
            }],
            sections=[{
                "chapter": 1,
                "page_keys": [
                    ("ch01.txt", 1), ("ch01.txt", 2), ("ch01.txt", 3),
                ],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        semantic = [row for row in payload["content_units"]
                    if row["kind"] in ("text", "formula")]
        self.assertEqual(3, len(semantic))
        mixed = next(row for row in semantic if row["kind"] == "text")
        formulas = [row for row in semantic if row["kind"] == "formula"]
        self.assertNotIn("source_language", mixed["metadata"])
        self.assertTrue(all(
            row["metadata"].get("source_language") == "zxx" for row in formulas
        ))
        reviewed = {
            target
            for row in payload["review_candidates"]
            if "source_language_unknown" in row["reason_codes"]
            for target in row["target_unit_ids"]
        }
        self.assertEqual({mixed["unit_id"]}, reviewed)
        self.assertFalse({row["unit_id"] for row in formulas} & reviewed)
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

    def test_asset_source_file_survives_payload_normalization(self):
        source_sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        payload = build_payload(
            str(self.materials),
            [str(self.source)],
            [{"file": "ch01.txt", "page": 1,
              "text": "Chapter 1\nExplain the diagram."}],
            sections=[{"chapter": 1, "page_keys": [("ch01.txt", 1)]}],
            quiz_items=[{
                "id": "asset-q1", "chapter": 1, "type": "subjective",
                "question": "Explain the diagram.", "answer": "Explanation.",
                "source": "material", "source_file": "ch01.txt",
                "source_pages": [1], "answer_source_pages": [1],
                "assets": [{
                    "path": "references/assets/ch01-p1.png",
                    "role": "answer_context",
                    "source_file": "ch01.txt",
                    "source_sha256": source_sha256,
                }],
            }],
            report={"warnings": [], "skipped": [], "ai_review": []},
        )
        question = next(
            row for row in payload["content_units"]
            if row["kind"] == "question" and row["external_id"] == "asset-q1"
        )
        self.assertEqual(
            "ch01.txt", question["metadata"]["assets"][0]["source_file"]
        )

    def test_persist_payload_rejects_globally_tainted_official_unit_before_write(self):
        payload = self.payload(missing_answer=False)
        seed = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "text"
        ))
        shared = "references/assets/shared.png"
        attempt = ContentUnit.create(
            seed.source_id, seed.source_sha256, seed.source_file,
            "figure", "Student work", 1, ordinal=900,
            chapter_id="ch02", phase_id="phase02",
            asset_path=shared, asset_role="student_attempt",
        )
        official_visual = ContentUnit.create(
            seed.source_id, seed.source_sha256, seed.source_file,
            "figure", "Official visual", 1, ordinal=901,
            chapter_id="ch01", phase_id="phase01",
            asset_path=shared, asset_role="figure",
        )
        payload["content_units"].extend([
            attempt.to_dict(), official_visual.to_dict(),
        ])
        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            persist_payload(self.workspace, payload)
        self.assertFalse((self.workspace / ".ingest").exists())

    def test_persist_payload_audits_seeded_bank_with_payload_before_any_write(self):
        payload = self.payload(missing_answer=False)
        seed = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "text"
        ))
        shared = "references/assets/shared.png"
        attempt = ContentUnit.create(
            seed.source_id, seed.source_sha256, seed.source_file,
            "figure", "Student work", 1, ordinal=920,
            chapter_id="ch01", phase_id="phase01",
            asset_path=shared, asset_role="student_attempt",
        )
        payload["content_units"].append(attempt.to_dict())

        references = self.workspace / "references"
        references.mkdir()
        (references / "quiz_bank.json").write_text(json.dumps([{
            "id": "seeded-q", "chapter": 1, "type": "subjective",
            "question": "Explain the official prompt.", "answer": "Answer",
            "source": "material", "source_file": "ch01.txt",
            "source_pages": [1],
            "assets": [{"path": shared, "role": "question_context"}],
        }]), encoding="utf-8")
        sentinel = self.workspace / "must-not-change.txt"
        sentinel.write_bytes(b"NO WRITE")
        before = {
            path.relative_to(self.workspace).as_posix(): path.read_bytes()
            for path in self.workspace.rglob("*") if path.is_file()
        }

        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            persist_payload(self.workspace, payload)

        after = {
            path.relative_to(self.workspace).as_posix(): path.read_bytes()
            for path in self.workspace.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse((self.workspace / ".ingest").exists())

    def test_review_compile_rejects_cross_unit_attempt_laundering_without_bank_overwrite(self):
        payload = self.payload(missing_answer=False)
        shared = "references/assets/shared.png"
        seed = ContentUnit.from_dict(next(
            row for row in payload["content_units"] if row["kind"] == "text"
        ))
        attempt = ContentUnit.create(
            seed.source_id, seed.source_sha256, seed.source_file,
            "figure", "Foreign student work", 1, ordinal=990,
            chapter_id="ch02", phase_id="phase02",
            asset_path=shared, asset_role="student_attempt",
        )
        payload["content_units"].append(attempt.to_dict())
        asset_dir = self.workspace / "references" / "assets"
        asset_dir.mkdir(parents=True)
        (asset_dir / "shared.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        persist_payload(self.workspace, payload)

        # Introduce the laundering only through a real validated review patch,
        # after the benign base generation has already been persisted.
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            row for row in store.review_queue.issues()
            if "pages_no_text" in row.reason_codes
        )
        recovered = ContentUnit.create(
            seed.source_id, seed.source_sha256, seed.source_file,
            "text", "Recovered text must stay hidden", 2, ordinal=991,
            chapter_id="ch01", phase_id="phase01",
            asset_path=shared, asset_role="figure",
            method="ai_recovered", provenance="ai_recovered",
        )
        patch = ReviewPatch.create(
            issue.issue_id, issue.source_id, issue.source_sha256,
            [{"op": "add_unit", "unit": recovered.to_dict()}],
            list(issue.evidence), reviewer="test",
            created_at="2026-07-16T10:00:00Z", status="validated",
        )
        store.apply_patch(patch)

        wiki = self.workspace / "references" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "ch01.md").write_text("# Chapter 1\n", encoding="utf-8")
        quiz_path = self.workspace / "references" / "quiz_bank.json"
        bank = [{
            "id": "q1", "chapter": 1, "type": "subjective",
            "question": "Explain the core concept.", "answer": "Official answer",
            "source": "material", "source_file": "ch01.txt", "source_pages": [1],
        }]
        quiz_path.write_text(json.dumps(bank), encoding="utf-8")
        retrieval_path = self.workspace / "references" / "retrieval_index.json"
        retrieval_path.write_bytes(b"OLD INDEX")
        watched = [
            self.workspace / ".ingest" / "content_units.jsonl",
            self.workspace / ".ingest" / "chapter_phase_mappings.jsonl",
            self.workspace / ".ingest" / "canonical_groups.jsonl",
            self.workspace / ".ingest" / "source_conflicts.jsonl",
            self.workspace / "references" / "wiki" / "ch01.md",
            quiz_path,
            retrieval_path,
        ]
        before = {path: path.read_bytes() for path in watched}

        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            compile_structured_visuals(self.workspace)
        self.assertEqual(before, {path: path.read_bytes() for path in watched})

        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            compile_review_outputs(self.workspace)
        self.assertEqual(before, {path: path.read_bytes() for path in watched})

    def test_review_compile_preflights_direct_attempt_question_before_all_writes(self):
        payload = self.payload(missing_answer=True)
        persist_payload(self.workspace, payload)
        store = IngestionStore(self.workspace, source_root=self.materials)
        issue = next(
            row for row in store.review_queue.issues()
            if "missing_answer" in row.reason_codes
        )
        attempt_path = "references/assets/student-attempt.png"
        asset = self.workspace / "references" / "assets" / "student-attempt.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"\x89PNG\r\n\x1a\n")
        existing_question = next(
            unit for unit in store.units().values()
            if unit.kind == "question" and unit.external_id == "q1"
        )
        question_payload = existing_question.to_dict()
        question_payload["asset_path"] = attempt_path
        question_payload["asset_role"] = "student_attempt"
        question = ContentUnit.from_dict(question_payload)
        answer = ContentUnit.create(
            question.source_id, question.source_sha256, question.source_file,
            "answer", "Official answer", question.page,
            ordinal=question.ordinal + 100, external_id="q1",
            chapter_id=question.chapter_id, phase_id=question.phase_id,
            metadata={"answer_source_pages": [question.page]},
            method="ai_recovered", provenance="ai_recovered",
        )
        patch = ReviewPatch.create(
            issue.issue_id, issue.source_id, issue.source_sha256,
            [
                {
                    "op": "replace_unit",
                    "unit_id": existing_question.unit_id,
                    "unit": question.to_dict(),
                },
                {"op": "add_unit", "unit": answer.to_dict()},
                {
                    "op": "pair_qa",
                    "question_unit_id": question.unit_id,
                    "answer_unit_id": answer.unit_id,
                },
            ],
            list(issue.evidence), reviewer="test",
            created_at="2026-07-16T10:05:00Z", status="validated",
        )
        store.apply_patch(patch)

        wiki = self.workspace / "references" / "wiki"
        wiki.mkdir(parents=True)
        wiki_path = wiki / "ch01.md"
        wiki_path.write_bytes(b"# Chapter 1\n\nUNCHANGED WIKI\n")
        quiz_path = self.workspace / "references" / "quiz_bank.json"
        quiz_path.write_text(json.dumps([{
            "id": "q1", "chapter": 1, "type": "subjective",
            "question": "Explain the core concept.", "answer": "Official answer",
            "source": "material", "source_file": "ch01.txt", "source_pages": [1],
        }]), encoding="utf-8")
        retrieval_path = self.workspace / "references" / "retrieval_index.json"
        retrieval_path.write_bytes(b"UNCHANGED INDEX")
        watched = [
            self.workspace / ".ingest" / "content_units.jsonl",
            self.workspace / ".ingest" / "chapter_phase_mappings.jsonl",
            self.workspace / ".ingest" / "duplicate_candidates.jsonl",
            self.workspace / ".ingest" / "canonical_groups.jsonl",
            self.workspace / ".ingest" / "source_conflicts.jsonl",
            self.workspace / ".ingest" / "source_priorities.jsonl",
            wiki_path,
            quiz_path,
            retrieval_path,
        ]
        self.assertTrue(all(path.is_file() for path in watched))
        before = {path: path.read_bytes() for path in watched}

        with self.assertRaisesRegex(ValueError, "is student_attempt evidence"):
            compile_review_outputs(self.workspace)

        self.assertEqual(before, {path: path.read_bytes() for path in watched})

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

    def test_teaching_attempt_mutation_stales_and_blocks_asset_index_rebuild(self):
        payload = self.payload(missing_answer=False)
        shared = "references/assets/official-prompt.png"
        rows = payload["content_units"]
        question_index = next(
            index for index, row in enumerate(rows) if row["kind"] == "question"
        )
        question = dict(rows[question_index])
        question["metadata"] = dict(question.get("metadata") or {})
        question["metadata"]["assets"] = [{
            "path": shared, "role": "question_context",
        }]
        rows[question_index] = ContentUnit.from_dict(question).to_dict()

        references = self.workspace / "references"
        references.mkdir()
        teaching_path = references / "teaching_examples.json"
        teaching_path.write_text("[]\n", encoding="utf-8")
        asset = references / "assets" / "official-prompt.png"
        asset.parent.mkdir()
        asset.write_bytes(b"\x89PNG\r\n\x1a\n")
        persist_payload(self.workspace, payload)

        wiki = references / "wiki"
        wiki.mkdir()
        (wiki / "ch01.md").write_text("# Chapter 1\n\nCore concept\n", encoding="utf-8")
        quiz_path = references / "quiz_bank.json"
        quiz_path.write_text(json.dumps([{
            "id": "q1", "chapter": 1, "type": "subjective",
            "question": "Explain the core concept.", "answer": "Official answer",
            "source": "material", "source_file": "ch01.txt", "source_pages": [1],
            "assets": [{"path": shared, "role": "question_context"}],
        }]), encoding="utf-8")

        compile_review_outputs(self.workspace)
        retrieval_path = references / "retrieval_index.json"
        clean_index = retrieve.load_index(str(self.workspace))
        self.assertEqual(
            "references/teaching_examples.json",
            clean_index["integrity"]["teaching_examples"]["file"],
        )
        self.assertEqual(
            file_sha256(teaching_path),
            clean_index["integrity"]["teaching_examples"]["sha256"],
        )

        hostile = [{
            "id": "foreign-student-attempt", "chapter": 2,
            "type": "subjective", "question": "Student work",
            "source_file": "foreign.pdf", "source_pages": [1],
            "teaching_role": "worked_example",
            "assets": [{"path": shared, "role": "student_attempt"}],
        }]
        teaching_path.write_text(json.dumps(hostile), encoding="utf-8")
        with self.assertRaises(SystemExit):
            retrieve.load_index(str(self.workspace))
        errors, _warnings, _stats = validate_workspace.validate(str(self.workspace))
        self.assertTrue(any(
            "retrieval_index.json" in entry["msg"] for entry in errors
        ), errors)

        stale_bytes = retrieval_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "student_attempt-tainted"):
            compile_review_outputs(self.workspace)
        self.assertEqual(stale_bytes, retrieval_path.read_bytes())

        # Removing the hostile declaration permits a fresh exact binding; no
        # attempt role survives in the rebuilt retrieval corpus.
        teaching_path.write_text("[]\n", encoding="utf-8")
        compile_review_outputs(self.workspace)
        rebuilt = retrieve.load_index(str(self.workspace))
        self.assertFalse(any(
            "student_attempt" in doc["asset_roles"] for doc in rebuilt["docs"]
        ))


if __name__ == "__main__":
    unittest.main()
