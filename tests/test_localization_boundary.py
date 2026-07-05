# -*- coding: utf-8 -*-
"""PR G — lightweight localization boundary. Stdlib only.

Locks the policy that we keep student-facing Chinese templates next to control logic for now and
defer a full `locales/` split until a second real bundled locale exists — without adding premature
file-loading complexity. Also keeps the current required Chinese labels present in the skill files.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_LABELS = [
    "当前阶段", "易错点", "3分钟速记",
    # A5 七步讲解模板块标题（exam-tutor）+ 每题来源块
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

    def test_full_split_is_deferred(self):
        d = read("docs", "localization.md")
        self.assertIn("locales", d, "未提及将来的 locales/ 拆分")
        self.assertIn("暂不", d, "未说明当前有意暂不拆分")
        self.assertIn("第二", d, "未说明拆分要等到加入第二种语言时")

    def test_default_student_language_is_simplified_chinese(self):
        d = read("docs", "localization.md")
        self.assertIn("简体中文", d)
        self.assertIn("Simplified Chinese", d, "未声明学生侧默认简体中文")

    def test_english_control_plane_stays_stable(self):
        d = read("docs", "localization.md").lower()
        self.assertIn("control plane unchanged", d, "未声明英文控制层保持不变")

    def test_future_locale_must_not_duplicate_control(self):
        d = read("docs", "localization.md").lower()
        self.assertIn("must not duplicate control behavior", d,
                      "未声明 locale 文件不得复制 / 重写控制行为")

    def test_lists_required_chinese_labels(self):
        d = read("docs", "localization.md")
        for label in REQUIRED_LABELS:
            self.assertIn(label, d, f"localization.md 未列出必保留中文标签: {label}")

    def test_existing_student_labels_remain_in_skill_files(self):
        tutor = read("skills", "exam-tutor", "SKILL.md")
        for label in ("当前阶段", "这题在问什么", "逐步演算", "知识点溯源", "题目来源"):
            self.assertIn(label, tutor, f"exam-tutor 丢失学生侧标签: {label}")
        quiz = read("skills", "exam-quiz", "SKILL.md")
        for label in ("已记录到错题本", "这题考什么", "标准答题步骤", "题目来源"):
            self.assertIn(label, quiz, f"exam-quiz 丢失学生侧标签: {label}")
        cheat = read("skills", "exam-cheatsheet", "SKILL.md")
        for label in ("必背", "老师强调"):
            self.assertIn(label, cheat, f"exam-cheatsheet 丢失小抄栏目: {label}")

    def test_locales_dir_not_created_yet(self):
        # this PR defers the split: no locales/zh-CN/ skill content should exist yet
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "locales", "zh-CN")),
                         "本 PR 不应创建 locales/zh-CN/（拆分留待加入第二语言时）")


if __name__ == "__main__":
    unittest.main()
