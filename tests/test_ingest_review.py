import contextlib
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
        self.workspace = self.temp.name

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
