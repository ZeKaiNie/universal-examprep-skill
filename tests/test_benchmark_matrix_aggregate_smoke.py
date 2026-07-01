# -*- coding: utf-8 -*-
"""Root-level CI-reachable smoke for the T3 / T3.1 benchmark matrix pipeline.

CI runs only `python -m unittest discover -s tests` (the repo root tests/), so the full T3 suite under
`benchmark/tests/test_aggregate_matrix.py` is NOT discovered by CI. This thin proxy runs BOTH halves of
the committed pipeline on the fixture so their core behavior (and honesty invariants) are covered by CI
without any GitHub Actions / CI config:

  * aggregator   : aggregate_matrix.py  → a fresh summary.json-compatible matrix
  * renderer     : report_matrix.py --summary <that summary> --out-dir <tmp> → report.html

It also pins the two anti-footgun guarantees: rendering a custom summary must NOT clobber the published
`benchmark/results/matrix/` report, and a custom `--summary` with NO `--out-dir` must be refused. Pure
stdlib; no network / LLM / paid run."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmark")
FIX = os.path.join(BENCH, "tests", "fixtures", "matrix_pipeline")
REPORT_PY = os.path.join(BENCH, "report_matrix.py")
PUBLISHED_REPORT = os.path.join(BENCH, "results", "matrix", "report.html")


def _aggregate_fixture_to(out):
    sys.path.insert(0, BENCH)
    import aggregate_matrix as A
    A.main(["--answers", os.path.join(FIX, "answers.jsonl"), "--scores", os.path.join(FIX, "scores.jsonl"),
            "--primary-course", "courseA", "--secondary-course", "courseB",
            "--judge-model", "fixture-judge", "--out", out])


def _mtime(path):
    return os.path.getmtime(path) if os.path.isfile(path) else None


@unittest.skipUnless(os.path.isdir(FIX), "benchmark matrix fixture not present")
class BenchmarkAggregateSmoke(unittest.TestCase):
    def test_fixture_aggregates_to_expected_with_honesty_invariants(self):
        out = os.path.join(tempfile.mkdtemp(), "s.json")
        _aggregate_fixture_to(out)
        with open(out, encoding="utf-8") as f:
            s = json.load(f)
        with open(os.path.join(FIX, "expected_summary.json"), encoding="utf-8") as f:
            self.assertEqual(s, json.load(f))                                  # deterministic, matches expected
        # honesty invariants — failures never inflate metrics:
        self.assertIsNone(s["matrix"]["opus|material"]["correct"])            # all-infra cell → null, not correct
        self.assertEqual(s["matrix"]["opus|material"]["n_infra_error"], 2)
        self.assertEqual(s["matrix"]["opus|closedbook"]["correct"], 0.5)      # present judge_error → not-correct
        self.assertEqual(s["matrix"]["sonnet|rawfiles"]["correct"], 0.0)      # missing score → not-correct


@unittest.skipUnless(os.path.isdir(FIX) and os.path.isfile(REPORT_PY), "benchmark renderer/fixture not present")
class BenchmarkRendererSmoke(unittest.TestCase):
    """The renderer half of the pipeline is now in CI too (T3.1 fix 2) — not just the aggregator."""

    def test_summary_renders_to_report_html_without_touching_published(self):
        tmp = tempfile.mkdtemp()
        summary = os.path.join(tmp, "s.json")
        _aggregate_fixture_to(summary)                                        # 1) aggregate fixture → summary
        out_dir = os.path.join(tmp, "report")
        before = _mtime(PUBLISHED_REPORT)
        r = subprocess.run([sys.executable, REPORT_PY, "--summary", summary, "--out-dir", out_dir],
                           capture_output=True, text=True, encoding="utf-8")   # 2) render summary → out_dir
        self.assertEqual(r.returncode, 0, r.stderr)
        report = os.path.join(out_dir, "report.html")
        self.assertTrue(os.path.isfile(report))                               # 3) report.html exists
        with open(report, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("并非已发布", html)                                     # 5) custom-summary banner present
        self.assertEqual(before, _mtime(PUBLISHED_REPORT))                    # 6) published report untouched

    def test_custom_summary_without_outdir_is_refused_and_keeps_published(self):
        tmp = tempfile.mkdtemp()
        summary = os.path.join(tmp, "s.json")
        _aggregate_fixture_to(summary)
        before = _mtime(PUBLISHED_REPORT)
        r = subprocess.run([sys.executable, REPORT_PY, "--summary", summary],  # 7) NO --out-dir → must refuse
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)                                     # refused, not a silent clobber
        self.assertIn("覆盖已发布报告", r.stderr)
        self.assertEqual(before, _mtime(PUBLISHED_REPORT))                    # published results/matrix/ untouched


if __name__ == "__main__":
    unittest.main(verbosity=2)
