# -*- coding: utf-8 -*-
"""PR D — modular skills use an English control plane + a Chinese student-facing layer. Stdlib only.

Enforces the split that PR #6's language policy declared:
- every skills/exam-*/SKILL.md exposes the required English control-section headings,
- student-facing examples/templates stay natural Simplified Chinese,
- no vague English control wording is introduced,
- the canonical provenance labels survive.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKILLS = ["exam-cram", "exam-ingest", "exam-tutor", "exam-quiz",
          "exam-review", "exam-cheatsheet", "exam-audit", "exam-help",
          "confusion-tracker"]

REQUIRED_HEADINGS = ["## Purpose", "## Activation", "## Inputs",
                     "## Workflow", "## Output Contract", "## Boundaries"]

# control prose must be concrete; these vague tokens are banned (case-insensitive)
VAGUE = ["properly", "comprehensively", "as needed", "appropriately",
         "optimize the learning experience"]

CANON_AMBER = "AI生成答案，非老师/教材提供"
CANON_YELLOW = "AI补充，可能与你老师讲的不完全一致"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class ControlPlaneLanguageTest(unittest.TestCase):
    def test_required_english_headings(self):
        for s in SKILLS:
            t = read("skills", s, "SKILL.md")
            for h in REQUIRED_HEADINGS:
                self.assertIn(h, t, f"{s}/SKILL.md 缺少英文控制章节 {h}")

    def test_tutor_chinese_template_preserved(self):
        t = read("skills", "exam-tutor", "SKILL.md")
        for label in ("当前阶段", "这题考什么", "标准答题步骤", "易错点", "3分钟速记"):
            self.assertIn(label, t, f"exam-tutor 丢失中文教学模板标签: {label}")

    def test_quiz_chinese_feedback_preserved(self):
        self.assertIn("已记录到错题本", read("skills", "exam-quiz", "SKILL.md"),
                      "exam-quiz 丢失中文判分反馈措辞")

    def test_cheatsheet_chinese_sections_preserved(self):
        c = read("skills", "exam-cheatsheet", "SKILL.md")
        for sec in ("必背", "老师强调", "易错点"):
            self.assertIn(sec, c, f"exam-cheatsheet 丢失中文小抄栏目: {sec}")

    def test_no_vague_english_control_wording(self):
        for s in SKILLS:
            low = read("skills", s, "SKILL.md").lower()
            for v in VAGUE:
                self.assertNotIn(v, low, f"{s}/SKILL.md 引入了空泛英文控制措辞: {v!r}")

    def test_canonical_provenance_labels_survive(self):
        for s in ("exam-cram", "exam-ingest", "exam-quiz"):
            t = read("skills", s, "SKILL.md")
            self.assertIn(CANON_AMBER, t, f"{s} 丢失 canonical ⚠️ 标注")
        # the yellow canonical label must still exist somewhere in the collection
        anywhere = "".join(read("skills", s, "SKILL.md") for s in SKILLS)
        self.assertIn(CANON_YELLOW, anywhere, "canonical 🟡 标注在技能集合里消失了")


if __name__ == "__main__":
    unittest.main()
