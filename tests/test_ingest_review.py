import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import ingest_review


class _Issue:
    def __init__(self, index, status="pending", reasons=None, severity="warning"):
        self.issue_id = "issue_%03d" % index
        self.status = status
        self.reason_codes = tuple(reasons or ("formula_hint",))
        self.severity = severity
        self.source_id = "src_" + "a" * 64
        self.source_sha256 = "b" * 64
        self.evidence = ()

    def to_dict(self):
        return {
            "issue_id": self.issue_id,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "severity": self.severity,
        }


class _Queue:
    def __init__(self, issues):
        self._issues = list(issues)

    def issues(self):
        return list(self._issues)

    def get(self, issue_id):
        return next((row for row in self._issues if row.issue_id == issue_id), None)


class _Store:
    def __init__(self, issues):
        self.review_queue = _Queue(issues)


class BoundedReviewList(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.material_temp = tempfile.TemporaryDirectory()
        self.home_temp = tempfile.TemporaryDirectory()
        self.workspace = self.temp.name
        self.environment = mock.patch.dict(
            os.environ, {"EXAMPREP_HOME": self.home_temp.name}
        )
        self.environment.start()
        output = io.StringIO()
        identity = ingest_review.exam_start._capture_runtime_identity()
        with mock.patch.object(
                ingest_review.exam_start, "_capture_runtime_identity",
                return_value=identity), contextlib.redirect_stdout(output):
            code = ingest_review.exam_start.run([
                "confirm", "--course", "review-fixture",
                "--materials", self.material_temp.name,
                "--workspace", self.workspace,
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en", "--processing-mode", "full", "--json",
            ])
        self.assertEqual(0, code, output.getvalue())

    def tearDown(self):
        self.environment.stop()
        self.home_temp.cleanup()
        self.material_temp.cleanup()
        self.temp.cleanup()

    def _run(self, issues, argv):
        out = io.StringIO()
        with mock.patch.object(ingest_review, "_store", return_value=(self.workspace, _Store(issues))):
            with contextlib.redirect_stdout(out):
                code = ingest_review.run(["--workspace", self.workspace, "--json"] + argv)
        return code, json.loads(out.getvalue())

    def test_list_is_bounded_and_resumable_with_summary(self):
        issues = [_Issue(index, status="pending" if index < 65 else "resolved")
                  for index in range(80)]
        code, payload = self._run(issues, ["list", "--status", "pending", "--limit", "20"])
        self.assertEqual(0, code)
        self.assertEqual(65, payload["count"])
        self.assertEqual(20, payload["returned"])
        self.assertEqual(20, payload["next_cursor"])
        self.assertTrue(payload["has_more"])
        self.assertEqual({"pending": 65}, payload["summary"]["by_status"])

        _, resumed = self._run(
            issues, ["list", "--status", "pending", "--cursor", "60", "--limit", "20"]
        )
        self.assertEqual(5, resumed["returned"])
        self.assertIsNone(resumed["next_cursor"])
        self.assertFalse(resumed["has_more"])

    def test_summary_only_and_complete_details_file(self):
        issues = [_Issue(index, reasons=("formula_hint", "garbled_text")) for index in range(3)]
        code, payload = self._run(
            issues,
            ["list", "--summary-only", "--details-file", ".ingest-review-details.json"],
        )
        self.assertEqual(0, code)
        self.assertEqual([], payload["issues"])
        self.assertEqual(0, payload["returned"])
        self.assertEqual(3, payload["summary"]["by_reason"]["formula_hint"])
        self.assertTrue(os.path.isfile(payload["details_file"]))
        with open(payload["details_file"], encoding="utf-8") as stream:
            details = json.load(stream)
        self.assertEqual(3, len(details["issues"]))

    def test_missing_answer_unrecoverable_requires_specific_evidence_note(self):
        issue = _Issue(1, reasons=("missing_answer",), severity="blocking")
        stderr = io.StringIO()
        with mock.patch.object(
                ingest_review, "_store", return_value=(self.workspace, _Store([issue]))):
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    ingest_review.run([
                        "--workspace", self.workspace,
                        "mark-unrecoverable", issue.issue_id,
                        "--reason", "No official answer found.",
                    ])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--evidence-note", stderr.getvalue())


class BatchReviewApply(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _patch(index):
        return SimpleNamespace(
            patch_id="patch_%03d" % index,
            issue_id="issue_%03d" % index,
        )

    @staticmethod
    def _result(applied=True, replayed=False):
        return SimpleNamespace(
            applied=applied,
            replayed=replayed,
            changed_operations=1 if applied else 0,
            issue_status="applied",
        )

    def test_batch_applies_each_ledger_patch_but_compiles_once(self):
        patches = [self._patch(1), self._patch(2), self._patch(3)]
        store = SimpleNamespace(
            apply_patch=mock.Mock(side_effect=[
                self._result(), self._result(), self._result(False, True),
            ])
        )
        with mock.patch.object(
                ingest_review, "_load_patch", side_effect=patches) as loader:
            with mock.patch.object(
                    ingest_review, "compile_review_outputs",
                    return_value={"readiness": "ready"}) as compile_outputs:
                payload = ingest_review._apply_patch_batch(
                    self.workspace, store, ["one.json", "two.json", "three.json"]
                )
        self.assertEqual(3, loader.call_count)
        self.assertEqual(patches, [call.args[0] for call in store.apply_patch.call_args_list])
        compile_outputs.assert_called_once_with(self.workspace)
        self.assertEqual(3, payload["patch_count"])
        self.assertEqual(2, payload["applied_count"])
        self.assertEqual(1, payload["replayed_count"])

    def test_batch_prefers_store_level_linear_apply(self):
        patches = [self._patch(1), self._patch(2), self._patch(3)]
        store = SimpleNamespace(
            apply_patches=mock.Mock(return_value=(
                self._result(), self._result(), self._result(False, True),
            )),
            apply_patch=mock.Mock(),
        )
        with mock.patch.object(
                ingest_review, "_load_patch", side_effect=patches):
            with mock.patch.object(
                    ingest_review, "compile_review_outputs",
                    return_value={"readiness": "ready"}) as compile_outputs:
                payload = ingest_review._apply_patch_batch(
                    self.workspace, store, ["one.json", "two.json", "three.json"]
                )
        store.apply_patches.assert_called_once_with(patches)
        store.apply_patch.assert_not_called()
        compile_outputs.assert_called_once_with(self.workspace)
        self.assertEqual(2, payload["applied_count"])
        self.assertEqual(1, payload["replayed_count"])

    def test_batch_failure_rebuilds_after_partial_ledger_progress(self):
        patches = [self._patch(1), self._patch(2)]
        store = SimpleNamespace(
            apply_patch=mock.Mock(side_effect=[self._result(), RuntimeError("boom")])
        )
        stderr = io.StringIO()
        with mock.patch.object(ingest_review, "_load_patch", side_effect=patches):
            with mock.patch.object(
                    ingest_review, "compile_review_outputs",
                    return_value={"readiness": "usable_with_gaps"}) as compile_outputs:
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        ingest_review._apply_patch_batch(
                            self.workspace, store, ["one.json", "two.json"]
                        )
        self.assertEqual(1, raised.exception.code)
        self.assertEqual(2, store.apply_patch.call_count)
        compile_outputs.assert_called_once_with(self.workspace)
        self.assertIn("failed after 1 ledger entries", stderr.getvalue())

    def test_batch_rejects_two_patches_for_the_same_issue(self):
        patches = [self._patch(1), self._patch(2)]
        patches[1].issue_id = patches[0].issue_id
        store = SimpleNamespace(apply_patch=mock.Mock())
        with mock.patch.object(ingest_review, "_load_patch", side_effect=patches):
            with self.assertRaises(SystemExit) as raised:
                ingest_review._apply_patch_batch(
                    self.workspace, store, ["one.json", "two.json"]
                )
        self.assertEqual(2, raised.exception.code)
        store.apply_patch.assert_not_called()

    def test_patch_list_is_nonempty_json_and_exclusive(self):
        patch_list = os.path.join(self.workspace, "patches.json")
        with open(patch_list, "w", encoding="utf-8") as stream:
            json.dump(["one.json", "two.json"], stream)
        args = SimpleNamespace(patch_files=[], patch_list=patch_list)
        self.assertEqual(
            ["one.json", "two.json"], ingest_review._batch_patch_paths(args)
        )
        args.patch_files = ["three.json"]
        with self.assertRaises(SystemExit):
            ingest_review._batch_patch_paths(args)


class ReviewerDiscoveredIssue(unittest.TestCase):
    def setUp(self):
        self.workspace = os.getcwd()
        self.source = SimpleNamespace(
            source_id="src_" + "a" * 64,
            sha256="b" * 64,
            path="materials/week01.pdf",
        )

        class Manifest:
            def __init__(inner, source):
                inner.source = source

            def get(inner, source_id):
                return inner.source if source_id == inner.source.source_id else None

            def verify_current(inner, source_id, sha256):
                if (source_id, sha256) != (inner.source.source_id, inner.source.sha256):
                    raise RuntimeError("source drift")

        class Queue:
            def __init__(inner):
                inner.rows = []

            def get(inner, issue_id):
                return next((row for row in inner.rows if row.issue_id == issue_id), None)

            def append(inner, issue):
                inner.rows.append(issue)

        self.store = SimpleNamespace(
            manifest=Manifest(self.source),
            review_queue=Queue(),
            mutation_lock=lambda: contextlib.nullcontext(),
            ingest_transaction=lambda unused: contextlib.nullcontext(),
            _expected_compiled_state=lambda: ({}, ()),
            refresh_source_statuses=mock.Mock(),
        )

    def test_report_is_evidence_bound_and_idempotent(self):
        args = SimpleNamespace(
            source_id=self.source.source_id,
            reason=["missing_prompt_asset"],
            page=[3, 3],
            target_unit=None,
            severity="blocking",
            description="The printed prompt image is missing.",
            suggested_action="Recover the original page image.",
        )
        receipt = {"receipts": [{
            "source_file": self.source.path,
            "source_sha256": self.source.sha256,
            "produced_pages": [1, 2, 3],
        }]}
        with mock.patch.object(ingest_review, "atomic_write_json") as write_evidence, \
                mock.patch.object(ingest_review, "read_json", return_value=receipt), \
                mock.patch.object(ingest_review, "refresh_build_manifest") as refresh:
            first = ingest_review._report_issue(self.workspace, self.store, args)
            second = ingest_review._report_issue(self.workspace, self.store, args)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["issue"]["issue_id"], second["issue"]["issue_id"])
        self.assertEqual([3], first["issue"]["pages"])
        self.assertEqual("blocking", first["issue"]["severity"])
        self.assertEqual(1, len(self.store.review_queue.rows))
        self.assertEqual(2, self.store.refresh_source_statuses.call_count)
        self.assertEqual(2, refresh.call_count)
        refresh.assert_called_with(
            self.workspace,
            rehash_artifacts=False,
            rehash_artifact_names=("review_queue", "source_manifest"),
        )
        self.assertEqual(2, write_evidence.call_count)
        evidence = first["issue"]["evidence"][0]
        self.assertTrue(evidence["path"].startswith(".ingest/evidence/"))

    def test_report_requires_a_location_or_target(self):
        args = SimpleNamespace(
            source_id=self.source.source_id,
            reason=["missing_prompt_asset"],
            page=None,
            target_unit=None,
            severity="warning",
            description="Missing evidence.",
            suggested_action="Inspect the source.",
        )
        with self.assertRaises(SystemExit) as raised:
            ingest_review._report_issue(self.workspace, self.store, args)
        self.assertEqual(2, raised.exception.code)

    def test_report_rejects_a_page_absent_from_revision_receipt(self):
        args = SimpleNamespace(
            source_id=self.source.source_id,
            reason=["missing_prompt_asset"],
            page=[999999],
            target_unit=None,
            severity="warning",
            description="Unbound location.",
            suggested_action="Inspect the source.",
        )
        receipt = {"receipts": [{
            "source_file": self.source.path,
            "source_sha256": self.source.sha256,
            "produced_pages": [1, 2, 3],
        }]}
        with mock.patch.object(ingest_review, "read_json", return_value=receipt):
            with self.assertRaises(SystemExit) as raised:
                ingest_review._report_issue(self.workspace, self.store, args)
        self.assertEqual(2, raised.exception.code)


class FullPromptConfirmation(unittest.TestCase):
    def test_confirmation_is_revision_bound_and_persisted_through_patch(self):
        source_id = "src_" + "d" * 64
        source_sha256 = "e" * 64
        asset_sha256 = "f" * 64
        unit = ingest_review.ContentUnit.create(
            source_id=source_id,
            source_sha256=source_sha256,
            source_file="materials/week01.pdf",
            kind="question",
            text="See the original printed prompt.",
            page=3,
            ordinal=1,
            external_id="hw1_q1",
            asset_path="references/assets/hw1-q1.png",
            asset_role="question_context",
            metadata={
                "question_text_status": "page_reference",
                "requires_assets": True,
                "source": "material",
                "source_type": "homework",
                "quiz_type": "subjective",
                "assets": [{
                    "path": "references/assets/hw1-q1.png",
                    "role": "question_context",
                    "sha256": asset_sha256,
                    "source_file": "materials/week01.pdf",
                    "source_sha256": source_sha256,
                }],
            },
        )
        issue = SimpleNamespace(
            issue_id="issue_" + "1" * 64,
            source_id=source_id,
            source_sha256=source_sha256,
            status="claimed",
            reason_codes=("full_prompt_asset_confirmed",),
            target_unit_ids=(unit.unit_id,),
            evidence=(ingest_review.EvidenceRef(
                ".ingest/evidence/%s/finding.json" % source_id,
                "2" * 64,
            ),),
        )
        store = SimpleNamespace(
            review_queue=SimpleNamespace(get=lambda unused: issue),
            _expected_compiled_state=lambda: ({unit.unit_id: unit}, ()),
            apply_patch=mock.Mock(return_value=SimpleNamespace(issue_status="applied")),
        )
        fake_asset = mock.Mock()
        fake_asset.is_file.return_value = True
        fake_asset.is_symlink.return_value = False
        args = SimpleNamespace(issue_id=issue.issue_id, reviewer="visual-auditor")

        with mock.patch.object(
                ingest_review, "safe_workspace_entry", return_value=fake_asset), \
                mock.patch.object(
                    ingest_review, "file_sha256", return_value=asset_sha256), \
                mock.patch.object(
                    ingest_review, "compile_review_outputs",
                    return_value={"readiness": "usable_with_gaps"}):
            payload = ingest_review._confirm_full_prompt(os.getcwd(), store, args)

        patch = store.apply_patch.call_args.args[0]
        proposed = patch.operations[0]["unit"]
        self.assertEqual(
            hashlib.sha256(
                ingest_review.canonical_json(unit.to_dict()).encode("utf-8")
            ).hexdigest(),
            patch.operations[0]["expected_unit_sha256"],
        )
        asset = proposed["metadata"]["assets"][0]
        self.assertIs(asset["contains_full_prompt"], True)
        self.assertEqual(asset_sha256, payload["confirmed"][0]["asset_sha256"])
        self.assertEqual("applied", payload["issue_status"])


if __name__ == "__main__":
    unittest.main()
