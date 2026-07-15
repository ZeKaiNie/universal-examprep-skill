import contextlib
import io
import json
import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
