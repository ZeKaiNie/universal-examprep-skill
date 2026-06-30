# -*- coding: utf-8 -*-
"""PR T1 — benchmark / test-flow documentation consistency. Stdlib only.

This guard keeps the benchmark + testing story internally consistent and drift-free,
WITHOUT running any paid benchmark:

1. User-facing docs must not hard-code a TOTAL unittest count (it drifts every PR).
   The live count comes from `python -m unittest discover -s tests -v`.
   (Benchmark item counts like `65 题` / `50 题` are NOT banned — only 测试/tests totals.)
2. Benchmark docs must consistently name the primary matrix arms: closedbook / rawfiles / skill.
3. Benchmark docs must describe material / dump-all as a legacy/stress/footnote arm,
   not the primary fair control.
4. The audit doc must honestly state: Tier 2 behavioral smoke is not implemented yet;
   summary.json is a precomputed artifact; the committed aggregator is future work.

It does NOT add Tier 2, an aggregator, or any LLM/paid run.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# docs that must not hard-code a total unittest count
COUNT_FILES = [
    "README.md",
    os.path.join("benchmark", "README.md"),
    os.path.join("benchmark", "docs", "testing-audit.md"),
    os.path.join("benchmark", "docs", "coverage-matrix.md"),
]

# benchmark docs that describe the matrix arms
ARM_FILES = [
    os.path.join("benchmark", "README.md"),
    os.path.join("benchmark", "docs", "测试流程详解.md"),
    os.path.join("benchmark", "docs", "testing-audit.md"),
]

AUDIT = os.path.join("benchmark", "docs", "testing-audit.md")

# total-unittest-count phrasings that drift. NOT benchmark item counts (题 / 道) or tier ordinals.
# A real COUNT carries a quantity connective (个/项/条/道); requiring it avoids matching tier
# references like "Tier 0 单测" / "Tier 1 测试" while still catching 个单测 / 项自动化测试 / 条测试.
BANNED_COUNT = [
    re.compile(r"\d+\s*个\s*单测"),                                 # 109 个单测
    re.compile(r"\d+\s*(?:个|项|条|道)\s*(?:单元|自动化)?测试"),      # 109 个/项/条/道 (单元/自动化) 测试
    re.compile(r"\d+\s+(?:unit\s+|automated\s+)?tests\b", re.I),    # 109 (unit/automated) tests
    re.compile(r"\d+\s+test\s+cases?\b", re.I),                     # 109 test case(s)
]

PRIMARY_ARMS = ["closedbook", "rawfiles", "skill"]
MATERIAL_TOKENS = ["material", "dump-all", "一股脑全塞", "给全材料"]
# the material/dump-all arm must be bound to a legacy/stress framing, not just have the words scattered:
LEGACY_BIND = re.compile(r"(遗留|压力|legacy|stress)[^。\n]{0,16}(臂|脚注|footnote)")

# the three tier docs must agree on the Tier 2 concept = behavioral smoke (not the retired
# "3–5 item benchmark-pipeline smoke"). 行为冒烟 is the shared canonical token.
TIER_FILES = [
    os.path.join("benchmark", "docs", "test_tiers.md"),
    os.path.join("benchmark", "docs", "testing-audit.md"),
    os.path.join("benchmark", "docs", "coverage-matrix.md"),
]
TIERS_DOC = os.path.join("benchmark", "docs", "test_tiers.md")
BEHAVIORAL_SMOKE = "行为冒烟"
NOT_IMPLEMENTED = ["未实现", "尚未实现", "not implemented"]
# the retired Tier-2 definition ("3–5 题" benchmark-pipeline smoke) must not come back:
OLD_TIER2_ITEMS = re.compile(r"3\s*[-–—]\s*5\s*题")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class BenchmarkDocsConsistencyTest(unittest.TestCase):
    def test_no_hardcoded_unittest_counts(self):
        offenders = []
        for rel in COUNT_FILES:
            txt = read(rel)
            for pat in BANNED_COUNT:
                for m in pat.finditer(txt):
                    offenders.append(f"{rel} -> {m.group(0)!r}")
        self.assertEqual(
            offenders, [],
            "用户可见文档里仍硬编了单元测试总数（会随每次加测试而漂移，应改为 stdlib 测试套件 + "
            "`python -m unittest discover -s tests -v` 取实时值）: " + "; ".join(offenders),
        )

    def test_benchmark_item_counts_are_not_banned(self):
        # guardrail on the guard: data counts (题 / 道) must stay legal — they are NOT the drift target
        for sample in ("共 65 题", "50 题", "16 题", "共 55 题有标准答案", "10 道越界探针", "Python 3.8/3.12"):
            for pat in BANNED_COUNT:
                self.assertIsNone(pat.search(sample), f"误伤数据计数: {sample!r} 命中 {pat.pattern}")

    def test_banned_count_catches_real_drift(self):
        # guardrail on the guard: the phrasings people actually type MUST be caught
        for sample in ("109 个测试", "109 个单元测试", "98 个自动化测试", "109 项自动化测试",
                       "共 109 个单测", "109 条测试", "109 tests", "109 unit tests", "88 test cases"):
            self.assertTrue(any(p.search(sample) for p in BANNED_COUNT),
                            f"漏网的硬编测试总数措辞: {sample!r}")

    def test_benchmark_docs_name_primary_arms(self):
        # require each arm as a backticked code identifier — a bare substring is trivially satisfied
        # ("skill" is a substring of the project name and `skill_workspace/`).
        for rel in ARM_FILES:
            txt = read(rel)
            for arm in PRIMARY_ARMS:
                self.assertRegex(
                    txt, r"`" + re.escape(arm) + r"`",
                    f"{rel} 未把主对照臂「{arm}」写成反引号代号（应为 `closedbook`/`rawfiles`/`skill`）",
                )

    def test_material_is_legacy_stress_footnote(self):
        for rel in ARM_FILES:
            txt = read(rel)
            has_material = any(tok in txt for tok in MATERIAL_TOKENS)
            self.assertTrue(has_material, f"{rel} 未提到 material/dump-all（应作为遗留/压力脚注存在）")
            self.assertRegex(
                txt, LEGACY_BIND,
                f"{rel} 未把 material/dump-all 绑定为「遗留/压力臂」或「压力脚注/footnote」"
                "（避免被当成主对照臂）",
            )

    def test_audit_states_tier2_not_implemented(self):
        a = read(AUDIT)
        self.assertIn("Tier 2", a, "审计文档未提 Tier 2")
        # tie the not-implemented claim to the Tier 2 + 行为冒烟 line, so a Tier-4-only「未实现」别处出现不能蒙混
        t2_lines = [ln for ln in a.splitlines() if "Tier 2" in ln and BEHAVIORAL_SMOKE in ln]
        self.assertTrue(t2_lines, "审计文档无「Tier 2 … 行为冒烟」行")
        self.assertTrue(
            any(any(m in ln for m in NOT_IMPLEMENTED) for ln in t2_lines),
            "审计文档未在 Tier 2 行为冒烟处声明尚未实现（不能靠文档别处的『未实现』蒙混）",
        )

    def test_audit_states_summary_is_precomputed(self):
        a = read(AUDIT)
        self.assertIn("summary.json", a, "审计文档未提 summary.json")
        self.assertTrue(
            ("precomputed" in a) or ("预先计算" in a) or ("预计算" in a),
            "审计文档未说明 summary.json 是预先计算（precomputed）的产物",
        )

    def test_audit_states_aggregator_is_future(self):
        a = read(AUDIT)
        self.assertTrue(
            ("aggregator" in a) or ("聚合器" in a),
            "审计文档未提聚合器（aggregator）",
        )
        self.assertTrue(
            ("未来" in a) or ("future" in a) or ("T3" in a),
            "审计文档未把聚合器标注为未来工作（future / PR T3）",
        )

    # ---- Tier 2 definition must be consistent across the three tier docs ----
    def test_tier2_is_behavioral_smoke_in_all_tier_docs(self):
        # test_tiers.md / testing-audit.md / coverage-matrix.md must share the SAME Tier 2 concept
        for rel in TIER_FILES:
            txt = read(rel)
            self.assertIn(BEHAVIORAL_SMOKE, txt,
                          f"{rel} 未用统一的 Tier 2 概念「{BEHAVIORAL_SMOKE}」（三份分层文档须一致）")

    def test_test_tiers_defines_tier2_as_unimplemented_behavioral_smoke(self):
        t = read(TIERS_DOC)
        # the Tier 2 row itself must name 行为冒烟 AND its not-implemented status (not a whole-doc search)
        t2_lines = [ln for ln in t.splitlines() if "Tier 2" in ln and BEHAVIORAL_SMOKE in ln]
        self.assertTrue(t2_lines, "test_tiers.md 无「Tier 2 … 行为冒烟」行（Tier 2 应定义为行为冒烟）")
        self.assertTrue(
            any(any(m in ln for m in NOT_IMPLEMENTED) for ln in t2_lines),
            "test_tiers.md 未在 Tier 2 行为冒烟处声明尚未实现",
        )

    def test_test_tiers_tier2_is_not_the_old_pipeline_item_smoke(self):
        # the retired definition ("3–5 题" benchmark-pipeline smoke) must not be Tier 2 anymore
        t = read(TIERS_DOC)
        self.assertIsNone(
            OLD_TIER2_ITEMS.search(t),
            "test_tiers.md 仍把 Tier 2 定义成「3–5 题」的 benchmark 管线冒烟——应改为行为冒烟，"
            "管线 mock 自检请另命名（benchmark pipeline mock check）",
        )


if __name__ == "__main__":
    unittest.main()
