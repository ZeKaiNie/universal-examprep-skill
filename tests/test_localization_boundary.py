# -*- coding: utf-8 -*-
"""Localization boundary — v4 doctrine. Stdlib only.

Locks the v4 policy: the `locales/` language-pack split is APPROVED AND ACTIVE (the old
"defer until a second bundled locale exists" trigger has fired — English is a full second
bundled language). Script logic stays single-copy and language-neutral; only student-visible
copy lives in language packs. Required canonical Chinese labels stay pinned for the zh pack.

Note: the pack DIRECTORY structure (locales/zh + locales/en trees, msgid parity) is asserted
by the language-pack structure tests that land with the split itself; this module pins the
POLICY document so doc and code cannot drift apart in between.
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
    "现在轮到你", "已记录到错题本", "资料里没有明确答案",
    # AI-supplement reminder uses the canonical marker (single source: docs/language-policy.md)
    "AI补充，可能与你老师讲的不完全一致",
]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class LocalizationBoundaryTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "localization.md")),
                        "缺少 docs/localization.md")

    def test_split_is_approved_and_active(self):
        # v4 inverts the old "defer the split" policy — the doc must say the split is ACTIVE
        # and must NOT still carry the old deferral wording.
        d = read("docs", "localization.md")
        self.assertIn("locales", d, "未提及 locales/ 语言包")
        self.assertIn("正式启用", d, "未声明 locales/ 拆分已启用（v4 政策）")
        self.assertIn("approved and active", d.lower(), "缺英文侧的启用声明")
        self.assertNotIn("暂不", d, "仍残留旧的「暂缓拆分」措辞——v4 政策已反转")

    def test_split_trigger_condition_is_recorded(self):
        # the doc must record WHY the old trigger fired: English is the second bundled language
        d = read("docs", "localization.md")
        self.assertIn("第二", d, "未记录「第二种打包语言」触发条件已满足")
        self.assertIn("second bundled language", d.lower(), "缺英文侧的触发条件记录")

    def test_default_student_language_is_english_with_chinese_opener(self):
        # v3+ default: English unless the student opens in Chinese (this doc was stale before)
        d = read("docs", "localization.md")
        self.assertIn("默认语言 = English", d, "未声明学生侧默认英文")
        self.assertIn("中文开场", d, "未声明中文开场切简体中文的例外")
        self.assertIn("Simplified Chinese", d)

    def test_english_control_plane_stays_stable(self):
        d = read("docs", "localization.md").lower()
        self.assertIn("control plane unchanged", d, "未声明英文控制层保持不变")

    def test_locale_must_not_duplicate_control(self):
        d = read("docs", "localization.md").lower()
        self.assertIn("must not duplicate control behavior", d,
                      "未声明 locale 文件不得复制 / 重写控制行为")

    def test_script_logic_stays_single_copy(self):
        # the核心 v4 anti-drift rule: logic is never duplicated per language
        d = read("docs", "localization.md")
        self.assertIn("脚本逻辑只有一份", d, "未声明脚本逻辑单份、语言中性")
        self.assertIn("canonical 代号", d, "未声明持久化只存语言中性 canonical 代号")

    def test_zh_is_the_fallback_locale(self):
        d = read("docs", "localization.md")
        self.assertIn("回退", d, "未声明语言包缺失时的回退策略")

    def test_lists_required_chinese_labels(self):
        d = read("docs", "localization.md")
        for label in REQUIRED_LABELS:
            self.assertIn(label, d, f"localization.md 未列出必保留中文标签: {label}")

    def test_existing_student_labels_remain_reachable(self):
        # Until the pack split lands (same PR), the canonical zh labels live in the skill
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
