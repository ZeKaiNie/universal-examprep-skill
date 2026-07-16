import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import import_formula_audit
from scripts.ingestion import (
    ContentUnit,
    EvidenceRef,
    IngestionStore,
    ReviewIssue,
    ReviewPatch,
    SourceRecord,
    atomic_write_json,
    atomic_write_jsonl,
    read_json,
    read_jsonl,
)


class FormulaAuditImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.materials = self.root / "materials"
        self.workspace.mkdir()
        self.materials.mkdir()
        self.source_path = self.materials / "ch01.pdf"
        self.source_path.write_bytes(b"stable fake PDF revision")
        self.source = SourceRecord.from_file(
            self.materials, "ch01.pdf", "application/pdf", status="review_required"
        )
        self.store = IngestionStore(self.workspace, source_root=self.materials)
        self.store.manifest.upsert(self.source)
        anchors = [
            ContentUnit.create(
                self.source.source_id,
                self.source.sha256,
                self.source.path,
                "page_anchor",
                "",
                page,
                ordinal=0,
                chapter_id="ch01",
                phase_id="phase01",
                method="native",
                provenance="material",
            )
            for page in range(1, 7)
        ]
        self.store.sync_base(anchors)
        atomic_write_json(
            self.workspace / ".ingest" / "build_manifest.json",
            {"source_root": str(self.materials)},
        )

    def tearDown(self):
        self.temp.cleanup()

    def issue(self, page=1, severity="warning"):
        evidence_path = (
            self.workspace / ".ingest" / "evidence" / self.source.source_id
            / ("page-%03d.json" % page)
        )
        atomic_write_json(evidence_path, {
            "schema_version": 1,
            "source_file": self.source.path,
            "source_id": self.source.source_id,
            "source_sha256": self.source.sha256,
            "candidate": {
                "pages": [page],
                "reason_codes": ["formula_hint"],
                "source_file": self.source.path,
                "target_unit_ids": [],
            },
        })
        evidence = EvidenceRef.from_file(
            self.workspace,
            str(evidence_path.relative_to(self.workspace)).replace("\\", "/"),
        )
        issue = ReviewIssue.create(
            self.source.source_id,
            self.source.sha256,
            ["formula_hint"],
            [evidence],
            "Visually inspect formula page %d." % page,
            pages=[page],
            severity=severity,
        )
        self.store.review_queue.append(issue)
        return issue

    def write_audit(self, name, rows):
        path = self.root / name
        atomic_write_json(path, rows)
        return path

    def row(
            self, issue, page, formulas=None, false_positive=False,
            aliases=False, object_evidence=False):
        evidence = issue.evidence[0].path
        if object_evidence:
            evidence = {
                "pdf_path": str(self.source_path),
                "source_sha256": self.source.sha256,
                "render_path": str(self.root / "untrusted-render-does-not-exist.png"),
                "method": "visual_review_of_original_pdf_180dpi",
            }
        result = {
            "issue_id": issue.issue_id,
            "source_file": self.source.path,
            "page": page,
            "evidence": evidence,
            "false_positive": false_positive,
        }
        if aliases:
            result["formulas_latex"] = list(formulas or ())
            result["semantic"] = (
                "The visually inspected source page contains these mathematical relations."
            )
        else:
            result["latex_formulas"] = list(formulas or ())
            result["semantic_en"] = (
                "The visually inspected source page contains these mathematical relations."
            )
        return result

    def test_drafts_both_alias_shapes_without_claiming_applying_or_trusting_render(self):
        first = self.issue(page=1)
        second = self.issue(page=2)
        audit = self.write_audit("audit.json", [
            self.row(
                first, 1, formulas=[r"P(A)=1", r"P(A^c)=1-P(A)"],
                object_evidence=True,
            ),
            self.row(second, 2, formulas=[r"P(A\mid B)=\frac{P(AB)}{P(B)}"], aliases=True),
        ])
        output = self.root / "drafts"

        summary = import_formula_audit.draft_formula_audits(
            self.workspace,
            [audit],
            output,
            reviewer="test-visual-reviewer",
            created_at="2026-07-15T12:00:00Z",
        )

        self.assertEqual(2, summary["issue_count"])
        self.assertEqual(3, summary["formula_unit_count"])
        self.assertEqual(1, summary["ignored_render_path_count"])
        self.assertEqual(
            {"claimed": False, "applied": False, "rebuilt": False},
            summary["ingestion_state_mutation"],
        )
        patch_paths = read_json(output / "patch-list.json")
        self.assertEqual(2, len(patch_paths))
        patches = [ReviewPatch.from_dict(read_json(path)) for path in patch_paths]
        units = []
        for patch in patches:
            self.assertEqual("validated", patch.status)
            issue = self.store.review_queue.get(patch.issue_id)
            self.assertEqual("pending", issue.status)
            self.assertEqual(
                [ref.to_dict() for ref in issue.evidence],
                [ref.to_dict() for ref in patch.evidence],
            )
            units.extend(
                ContentUnit.from_dict(operation["unit"])
                for operation in patch.operations
            )
        self.assertEqual(3, len(units))
        self.assertTrue(all(unit.kind == "formula" for unit in units))
        self.assertTrue(all(unit.metadata == {"source_language": "zxx"} for unit in units))
        self.assertTrue(all(unit.method == "vision" for unit in units))
        self.assertTrue(all(unit.provenance == "ai_recovered" for unit in units))
        self.assertTrue(all(unit.chapter_id == "ch01" for unit in units))
        self.assertNotIn("render_path", json.dumps(
            [patch.to_dict() for patch in patches], ensure_ascii=False
        ))
        self.assertEqual([], read_jsonl(
            self.workspace / ".ingest" / "review_patches.jsonl", default=[]
        ))

        for patch in patches:
            self.store.apply_patch(patch)
        self.assertTrue(all(
            self.store.review_queue.get(patch.issue_id).status == "applied"
            for patch in patches
        ))

    def test_contextual_batch_validation_runs_before_any_output(self):
        issue = self.issue(page=1)
        audit = self.write_audit(
            "audit.json", [self.row(issue, 1, formulas=[r"V=IR"])]
        )
        output = self.root / "drafts"

        with mock.patch.object(
                import_formula_audit.IngestionStore,
                "validate_patches",
                side_effect=RuntimeError("synthetic contextual rejection"),
        ) as validate:
            with self.assertRaisesRegex(
                    import_formula_audit.FormulaAuditImportError,
                    "failed contextual validation"):
                import_formula_audit.draft_formula_audits(
                    self.workspace, [audit], output, reviewer="reviewer"
                )

        validate.assert_called_once()
        self.assertFalse(output.exists())
        self.assertEqual("pending", self.store.review_queue.get(issue.issue_id).status)
        self.assertEqual([], read_jsonl(
            self.workspace / ".ingest" / "review_patches.jsonl", default=[]
        ))

    def test_real_compiled_drift_rejects_without_touching_existing_output(self):
        issue = self.issue(page=1)
        audit = self.write_audit(
            "audit.json", [self.row(issue, 1, formulas=[r"V=IR"])]
        )
        output = self.root / "drafts"
        output.mkdir()
        sentinel = output / "reviewer-note.txt"
        sentinel.write_text("keep this reviewer note", encoding="utf-8")
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        drift = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Out-of-ledger compiled drift",
            1,
            ordinal=999,
        )
        atomic_write_jsonl(
            self.store.units_path,
            [unit.to_dict() for unit in self.store.units().values()] + [drift.to_dict()],
        )

        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError,
                "compiled state was modified outside the ledger"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [audit], output, reviewer="reviewer"
            )

        self.assertEqual(before, {
            path.name: path.read_bytes() for path in output.iterdir()
        })
        self.assertEqual("pending", self.store.review_queue.get(issue.issue_id).status)
        self.assertEqual([], read_jsonl(
            self.workspace / ".ingest" / "review_patches.jsonl", default=[]
        ))

    def test_drafting_is_idempotent_in_the_same_output_directory(self):
        issue = self.issue(page=1)
        audit = self.write_audit(
            "audit.json", [self.row(issue, 1, formulas=[r"V=IR"])]
        )
        output = self.root / "drafts"

        first = import_formula_audit.draft_formula_audits(
            self.workspace, [audit], output, reviewer="stable-reviewer"
        )
        before = {
            path.name: path.read_bytes()
            for path in output.iterdir() if path.is_file()
        }
        second = import_formula_audit.draft_formula_audits(
            self.workspace, [audit], output, reviewer="stable-reviewer"
        )
        after = {
            path.name: path.read_bytes()
            for path in output.iterdir() if path.is_file()
        }

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual("pending", self.store.review_queue.get(issue.issue_id).status)

    def test_source_drift_and_evidence_mismatch_fail_before_output(self):
        issue = self.issue(page=1)
        wrong_evidence = self.row(issue, 1, formulas=[r"V=IR"])
        wrong_evidence["evidence"] = ".ingest/evidence/not-the-issue.json"
        audit = self.write_audit("bad-evidence.json", [wrong_evidence])
        output = self.root / "drafts"
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError,
                "exact issue evidence path"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [audit], output, reviewer="reviewer"
            )
        self.assertFalse(output.exists())

        valid = self.write_audit(
            "drift.json", [self.row(issue, 1, formulas=[r"V=IR"])]
        )
        self.source_path.write_bytes(b"drifted revision")
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError,
                "current source verification failed"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [valid], output, reviewer="reviewer"
            )
        self.assertFalse(output.exists())

    def test_audit_pdf_path_uses_file_identity_not_path_spelling(self):
        with mock.patch.object(
                import_formula_audit.os.path,
                "samefile",
                return_value=True,
        ) as samefile:
            self.assertTrue(import_formula_audit._same_absolute_path(
                self.source_path,
                Path(str(self.source_path).upper()),
            ))
        samefile.assert_called_once()

        with mock.patch.object(
                import_formula_audit.os.path,
                "samefile",
                side_effect=OSError("missing alias"),
        ):
            self.assertFalse(import_formula_audit._same_absolute_path(
                self.source_path,
                self.root / "missing.pdf",
            ))

    def test_duplicate_issue_empty_formula_and_ordinal_conflict_fail_closed(self):
        issue = self.issue(page=1)
        first = self.write_audit(
            "one.json", [self.row(issue, 1, formulas=[r"P(A)=1"])]
        )
        second = self.write_audit(
            "two.json", [self.row(issue, 1, formulas=[r"P(A)=1"])]
        )
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError, "duplicate issue"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [first, second], self.root / "duplicate", reviewer="reviewer"
            )

        empty = self.row(issue, 1, formulas=[])
        empty_audit = self.write_audit("empty.json", [empty])
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError,
                "at least one formula"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [empty_audit], self.root / "empty", reviewer="reviewer"
            )

        ordinal = import_formula_audit._formula_ordinal(issue.issue_id, 0)
        collision = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "formula",
            r"Q(B)=0",
            1,
            ordinal=ordinal,
            latex=r"Q(B)=0",
            chapter_id="ch01",
            phase_id="phase01",
            metadata={"source_language": "zxx"},
            method="vision",
            provenance="ai_recovered",
        )
        self.store.sync_base(list(self.store.base_units().values()) + [collision])
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError, "collides"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [first], self.root / "collision", reviewer="reviewer"
            )

    def test_warning_false_positive_drafts_and_applies_mark_resolved(self):
        issue = self.issue(page=3, severity="warning")
        row = self.row(issue, 3, formulas=[], false_positive=True, aliases=True)
        row["semantic"] = (
            "This page only shows reproducible MATLAB state-management commands and output; "
            "there is no mathematical formula to recover."
        )
        audit = self.write_audit("false-positive.json", [row])
        output = self.root / "false-positive"

        summary = import_formula_audit.draft_formula_audits(
            self.workspace,
            [audit],
            output,
            reviewer="test-visual-reviewer",
            created_at="2026-07-15T12:00:00Z",
        )

        self.assertEqual(1, summary["false_positive_issue_count"])
        self.assertEqual(0, summary["formula_unit_count"])
        patch = ReviewPatch.from_dict(read_json(read_json(output / "patch-list.json")[0]))
        self.assertEqual("mark_resolved", patch.operations[0]["op"])
        self.assertIn("formula_hint_false_positive_v1", patch.operations[0]["reason"])
        result = self.store.apply_patch(patch)
        self.assertEqual("resolved", result.issue_status)

    def test_multi_chapter_solution_page_is_left_unassigned_and_reported(self):
        issue = self.issue(page=5)
        second_chapter = ContentUnit.create(
            self.source.source_id,
            self.source.sha256,
            self.source.path,
            "text",
            "Chapter 2 material shares this solution page.",
            5,
            ordinal=1,
            chapter_id="ch02",
            phase_id="phase02",
            metadata={"source_language": "en"},
            method="native",
            provenance="material",
        )
        self.store.sync_base(list(self.store.base_units().values()) + [second_chapter])
        audit = self.write_audit(
            "multi-chapter.json", [self.row(issue, 5, formulas=[r"P(A)=1"])]
        )
        output = self.root / "multi-chapter"

        summary = import_formula_audit.draft_formula_audits(
            self.workspace,
            [audit],
            output,
            reviewer="test-visual-reviewer",
            created_at="2026-07-15T12:00:00Z",
        )

        self.assertEqual(
            [issue.issue_id], summary["unassigned_ambiguous_chapter_issue_ids"]
        )
        patch = ReviewPatch.from_dict(read_json(read_json(output / "patch-list.json")[0]))
        unit = ContentUnit.from_dict(patch.operations[0]["unit"])
        self.assertIsNone(unit.chapter_id)
        self.assertIsNone(unit.phase_id)

    def test_blocking_false_positive_is_rejected(self):
        issue = self.issue(page=4, severity="blocking")
        row = self.row(issue, 4, formulas=[], false_positive=True)
        row["semantic_en"] = (
            "Visual inspection found no mathematical formula on the cited source page."
        )
        audit = self.write_audit("blocking-false-positive.json", [row])
        with self.assertRaisesRegex(
                import_formula_audit.FormulaAuditImportError,
                "permitted only for warning"):
            import_formula_audit.draft_formula_audits(
                self.workspace, [audit], self.root / "blocked", reviewer="reviewer"
            )


if __name__ == "__main__":
    unittest.main()
