# -*- coding: utf-8 -*-
"""Localization ownership and dispatch contracts. Stdlib only.

Behavior remains single-copy in the control layer; locale packs own student wording only.
Persisted state uses language-neutral canonical codes, while display strings and command
aliases are normalized before storage. Directory parity is covered by test_language_packs.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_LABELS = [
    "当前阶段", "易错点", "3分钟速记",
    # 七步讲解模板块标题（exam-tutor）+ 每题来源块
    "题面图", "这题在问什么", "图里要读的量", "核心公式", "逐步演算", "答案自检", "知识点溯源",
    "题目来源", "答案来源",
    # exam-quiz 判分反馈仍用旧措辞
    "这题考什么", "标准答题步骤",
    "现在轮到你", "已记录到错题本", "资料里没有这道题的答案",
    # AI-supplement reminder uses the fixed student-facing marker
    "AI补充，可能与你老师讲的不完全一致",
]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class LocalizationBoundaryTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "localization.md")),
                        "缺少 docs/localization.md")

    def test_current_directory_ownership_is_explicit(self):
        d = read("docs", "localization.md")
        for path in ("SKILL.md", "skills/*/SKILL.md", "locales/zh/SKILL.md",
                     "locales/en/SKILL.md", "locales/<lang>/messages.json", "scripts/"):
            self.assertIn(path, d, path)
        self.assertIn("语言中性路由器", d)
        self.assertIn("不是完整流程副本", d)

    def test_neutral_canonical_codes_and_display_aliases_are_distinct(self):
        d = read("docs", "localization.md")
        for code in ("`zh`", "`en`", "`bilingual`", "`from_scratch`",
                     "`shore_up`", "`fill_gaps`"):
            self.assertIn(code, d)
        for display_alias in ("`中文`", "`English`", "`双语`"):
            self.assertIn(display_alias, d)
        self.assertIn("语言中性 canonical 代号", d)
        self.assertIn("归一化为对应代号", d)
        self.assertIn("不是新状态的 canonical 值", d)

    def test_default_student_language_is_english_with_chinese_opener(self):
        # v3+ default: English unless the student opens in Chinese (this doc was stale before)
        d = read("docs", "localization.md")
        self.assertIn("新对话默认英文", d, "未声明学生侧默认英文")
        self.assertIn("中文开场", d, "未声明中文开场切简体中文的例外")
        self.assertIn("双语只能显式选择", d)

    def test_english_control_plane_stays_stable(self):
        d = read("docs", "localization.md")
        self.assertIn("skills/exam-cram/SKILL.md", d)
        self.assertIn("behavioral source of truth", d)

    def test_locale_must_not_duplicate_control(self):
        d = read("docs", "localization.md")
        self.assertIn("locale 文件不拥有业务规则", d)
        self.assertIn("行为变化先且只改 `skills/*/SKILL.md`", d)

    def test_script_logic_stays_single_copy(self):
        d = read("docs", "localization.md")
        self.assertIn("脚本逻辑只有一份", d, "未声明脚本逻辑单份、语言中性")
        self.assertIn("旧中文显示值仅作为迁移输入和生成视图文案", d,
                      "未声明旧中文显示值只属于迁移/视图兼容边界")

    def test_missing_selected_pack_fails_loud(self):
        d = read("docs", "localization.md")
        self.assertIn("所选文案包缺失是打包错误", d)
        self.assertIn("不能静默换成另一种回复语言", d)

    def test_required_chinese_labels_live_in_policy_or_packs(self):
        pack_dir = os.path.join(ROOT, "locales", "zh", "skills")
        d = read("docs", "language-policy.md") + "\n" + read("locales", "zh", "SKILL.md")
        for name in os.listdir(pack_dir):
            if name.endswith(".md"):
                d += "\n" + read("locales", "zh", "skills", name)
        for label in REQUIRED_LABELS:
            self.assertIn(label, d, f"中文学生可见标签不可达: {label}")

    def test_source_quote_exception_does_not_exempt_agent_prose(self):
        d = read("docs", "localization.md")
        self.assertIn("逐字引文可以保留原语言", d)
        self.assertIn("智能体生成的标题、衔接、解释、解答和总结仍按当前语言输出", d)

    def test_existing_student_labels_remain_reachable(self):
        # Until the pack split lands (same PR), the fixed zh display labels live in the skill
        # files; after it they live in locales/zh/skills/. Accept either home so this policy
        # test doesn't fight the split commit-by-commit — but they must exist SOMEWHERE.
        def label_home(label, *fallback_files):
            for parts in fallback_files:
                p = os.path.join(ROOT, *parts)
                if os.path.isfile(p) and label in read(*parts):
                    return True
            return False

        for label in ("当前阶段", "这题在问什么", "逐步演算", "知识点溯源", "题目来源"):
            self.assertTrue(
                label_home(label, ("skills", "exam-tutor", "SKILL.md"),
                           ("locales", "zh", "skills", "exam-tutor.md")),
                f"exam-tutor 的学生侧标签在技能文件与 zh 语言包中都找不到: {label}")
        for label in ("已记录到错题本", "这题考什么", "标准答题步骤", "题目来源"):
            self.assertTrue(
                label_home(label, ("skills", "exam-quiz", "SKILL.md"),
                           ("locales", "zh", "skills", "exam-quiz.md")),
                f"exam-quiz 的学生侧标签在技能文件与 zh 语言包中都找不到: {label}")
        for label in ("必背", "例题解答"):
            self.assertTrue(
                label_home(label, ("skills", "exam-cheatsheet", "SKILL.md"),
                           ("locales", "zh", "skills", "exam-cheatsheet.md")),
                f"exam-cheatsheet 的小抄栏目在技能文件与 zh 语言包中都找不到: {label}")


if __name__ == "__main__":
    unittest.main()
