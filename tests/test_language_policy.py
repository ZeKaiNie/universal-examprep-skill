# -*- coding: utf-8 -*-
"""PR C — bilingual language policy (policy bridge). Stdlib only.

Locks: docs/language-policy.md defines an English control plane + a Simplified-Chinese
student-facing layer with ONE canonical provenance wording; every direct entrypoint uses
the canonical labels and NO old competing labels; exam-ingest defaults to Chinese; the
anti-hallucination protocol and web-portability are preserved; root stays Chinese-first.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUDENT_FACING = ["exam-tutor", "exam-quiz", "exam-review", "exam-cheatsheet", "exam-help"]

# the single canonical provenance wording (docs/language-policy.md is the source of truth)
CANON_YELLOW = "AI补充，可能与你老师讲的不完全一致"
CANON_AMBER = "AI生成答案，非老师/教材提供"
CANON_GREEN = "来自资料"

# every file that prescribes provenance labels must carry the canonical wording
CANONICAL_FILES = [
    ("docs", "language-policy.md"),
    ("SKILL.md",),
    ("AGENTS.md",),
    ("prompts", "web_prompt.md"),
    ("skills", "exam-help", "SKILL.md"),
    ("skills", "exam-ingest", "SKILL.md"),
]

# direct student-facing / runtime surfaces must NOT contain any old competing label.
# NB: prose legitimately writes "AI 补充" (space around the Latin token), so we forbid only the
# unambiguous OLD LABEL strings (distinct suffix / wording), not the bare "AI 补充" prose form.
NO_OLD_ENTRYPOINTS = [
    ("SKILL.md",),
    ("AGENTS.md",),
    ("prompts", "web_prompt.md"),
    ("README.md",),
    ("scripts", "ingest.py"),
    ("docs", "skill-architecture.md"),
    ("skills", "exam-help", "SKILL.md"),
    ("skills", "exam-tutor", "SKILL.md"),
    ("skills", "exam-cram", "SKILL.md"),
    ("skills", "exam-quiz", "SKILL.md"),
    ("skills", "exam-review", "SKILL.md"),
    ("skills", "exam-cheatsheet", "SKILL.md"),
    ("skills", "exam-ingest", "SKILL.md"),
]
OLD_LABELS = [
    "此答案由 AI 生成",
    "答案由 AI 生成",
    "来自学生上传的资料",
    "可能与老师讲的不一致",
    "可能与老师不一致",
]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class LanguagePolicyTest(unittest.TestCase):
    # ---- policy doc ----
    def test_policy_doc_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "language-policy.md")),
                        "缺少 docs/language-policy.md")

    def test_policy_defines_both_planes(self):
        p = read("docs", "language-policy.md")
        self.assertIn("control plane", p.lower(), "未定义英文控制层")
        self.assertIn("Simplified Chinese", p, "未定义简体中文学生层")

    def test_policy_documents_bilingual_split(self):
        # the policy documents the English-control / Chinese-student split + root stays Chinese-first
        p = read("docs", "language-policy.md").lower()
        self.assertIn("control-plane", p, "缺少控制层转换说明")
        self.assertIn("student-facing", p, "缺少学生侧说明")
        self.assertIn("chinese-first", p, "缺少根 SKILL.md 中文优先说明")

    # ---- canonical provenance wording everywhere ----
    def test_canonical_labels_present_in_all_target_files(self):
        for parts in CANONICAL_FILES:
            txt = read(*parts)
            where = "/".join(parts)
            self.assertIn(CANON_YELLOW, txt, f"{where} 缺少 canonical 🟡 标注")
            self.assertIn(CANON_AMBER, txt, f"{where} 缺少 canonical ⚠️ 标注")
            self.assertIn(CANON_GREEN, txt, f"{where} 缺少 🟢 来自资料")

    def test_no_old_competing_labels_in_entrypoints(self):
        for parts in NO_OLD_ENTRYPOINTS:
            txt = read(*parts)
            where = "/".join(parts)
            for old in OLD_LABELS:
                self.assertNotIn(old, txt, f"{where} 仍残留旧来源标注「{old}」")

    # ---- student-facing default Chinese ----
    def test_student_facing_subskills_default_simplified_chinese(self):
        for s in STUDENT_FACING:
            txt = read("skills", s, "SKILL.md")
            self.assertIn("Simplified Chinese", txt, f"{s} 未声明 student-facing 默认简体中文")

    def test_ingest_defaults_chinese_with_receipt_example(self):
        ing = read("skills", "exam-ingest", "SKILL.md")
        self.assertIn("Simplified Chinese", ing, "exam-ingest 未声明默认简体中文")
        self.assertIn("已初始化备考空间", ing, "exam-ingest 缺少中文初始化回执示例")

    # ---- concrete student-facing labels ----
    def test_concrete_chinese_labels_in_tutor(self):
        tutor = read("skills", "exam-tutor", "SKILL.md")
        for label in ("当前阶段", "这题考什么", "标准答题步骤", "易错点", "3分钟速记", "现在轮到你"):
            self.assertIn(label, tutor, f"exam-tutor 缺少具体标签: {label}")

    def test_quiz_feedback_labels(self):
        quiz = read("skills", "exam-quiz", "SKILL.md")
        self.assertIn("已记录到错题本", quiz, "exam-quiz 缺少归档回执措辞")
        self.assertIn("连错两次", quiz)

    def test_review_replay_and_confusion_wording(self):
        r = read("skills", "exam-review", "SKILL.md")
        self.assertIn("错题重做", r, "exam-review 缺少错题重做措辞")
        self.assertIn("疑难复述", r, "exam-review 缺少疑难复述措辞")

    def test_cheatsheet_required_sections(self):
        c = read("skills", "exam-cheatsheet", "SKILL.md")
        for sec in ("必背", "老师强调", "易错", "3分钟速记"):
            self.assertIn(sec, c, f"小抄缺少栏目: {sec}")

    # ---- root SKILL.md: anti-hallucination protocol preserved + language policy mirrored ----
    def test_root_skill_exists_with_provenance_protocol(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "SKILL.md")), "根 SKILL.md 不存在")
        root = read("SKILL.md")
        self.assertIn("知识来源透明化", root, "根 SKILL.md 缺少 知识来源透明化协议")

    def test_root_skill_mirrors_language_default(self):
        root = read("SKILL.md")
        self.assertIn("简体中文", root, "根 SKILL.md 未镜像「默认简体中文」")
        self.assertIn("language-policy", root, "根 SKILL.md 未指向 docs/language-policy.md")
        self.assertIn(CANON_AMBER, root, "根 SKILL.md 未对齐 canonical 来源标注")

    def test_web_prompt_remains_chinese_first(self):
        web = read("prompts", "web_prompt.md")
        self.assertIn("网页端", web, "web_prompt 不再是中文优先")
        self.assertIn("备考", web)


if __name__ == "__main__":
    unittest.main()
