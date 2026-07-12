# -*- coding: utf-8 -*-
"""Codex r1 回归钉：gen.run_claude 恒定二元组（判分等既有调用点的解包契约）+
run_claude_traced 三元组；i18n canon_mode/canon_tier 接受英文显示词（大小写不敏感）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmark"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen    # noqa: E402
import i18n   # noqa: E402


class RunClaudeShapes(unittest.TestCase):
    """不真调 claude：桩掉 _run_claude_impl，只钉返回形状。"""

    def setUp(self):
        self._orig = gen._run_claude_impl
        gen._run_claude_impl = lambda *a, **k: ("out", 0.1, ["references/wiki/ch02.md"])
        self.addCleanup(setattr, gen, "_run_claude_impl", self._orig)

    def test_run_claude_is_two_tuple(self):
        out, cost = gen.run_claude("q", "sonnet")           # ValueError here = 契约破坏
        self.assertEqual((out, cost), ("out", 0.1))

    def test_run_claude_traced_is_three_tuple(self):
        out, cost, files = gen.run_claude_traced("q", "sonnet")
        self.assertEqual(files, ["references/wiki/ch02.md"])

    def test_generate_one_untraced_pads_none_files(self):
        os.environ.pop("EXAMPREP_TRACE", None)
        gen._run_claude_impl = lambda *a, **k: ("out", 0.1, None)
        out, cost, files = gen.generate_one("psyc", "sonnet", "closedbook", "q1", "题", "")
        self.assertIsNone(files, "未开轨迹时第三元素必须补 None，而不是变二元组")

    def test_generate_one_env_trace_only_affects_generation(self):
        os.environ["EXAMPREP_TRACE"] = "1"
        try:
            out, cost, files = gen.generate_one("psyc", "sonnet", "closedbook", "q1", "题", "")
            self.assertEqual(files, ["references/wiki/ch02.md"])
            # 判分路径（run_claude）不受环境变量影响，仍是二元组
            self.assertEqual(len(gen.run_claude("q", "sonnet")), 2)
        finally:
            os.environ.pop("EXAMPREP_TRACE", None)


class EnModeAliases(unittest.TestCase):
    def test_en_display_labels_canonicalize(self):
        for raw, want in [("teach from scratch", "from_scratch"),
                          ("Teach From Scratch", "from_scratch"),
                          ("fill the gaps", "fill_gaps"),
                          ("start mid-course, shore up weak spots", "shore_up"),
                          ("shore up", "shore_up"), ("fill gaps", "fill_gaps")]:
            code, tier, warn = i18n.canon_mode(raw)
            self.assertEqual(code, want, raw)
            self.assertIsNone(warn, raw)

    def test_en_tier_labels_canonicalize(self):
        for raw, want in [("≤1 day", "le1d"), ("1-3 Days", "d1_3"), (">7 days", "gt7d")]:
            code, warn = i18n.canon_tier(raw)
            self.assertEqual(code, want, raw)
            self.assertIsNone(warn, raw)

    def test_unknown_mode_still_passes_through_with_warning(self):
        code, tier, warn = i18n.canon_mode("随便写的模式")
        self.assertEqual(code, "随便写的模式")
        self.assertIsNotNone(warn, "未知值透传铁律：保留原值但必须告警")


if __name__ == "__main__":
    unittest.main(verbosity=2)
