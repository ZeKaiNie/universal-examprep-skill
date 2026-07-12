# -*- coding: utf-8 -*-
"""PR C — bilingual language policy (policy bridge). Stdlib only.

Locks: docs/language-policy.md defines an English control plane + a Simplified-Chinese
student-facing layer with ONE canonical provenance wording; every direct entrypoint uses
the canonical labels and NO old competing labels; exam-ingest defaults to Chinese; the
anti-hallucination protocol and web-portability are preserved; root stays Chinese-first.
"""
import glob as _glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUDENT_FACING = ["exam-tutor", "exam-quiz", "exam-review", "exam-cheatsheet", "exam-help"]

# the single canonical provenance wording (docs/language-policy.md is the source of truth)
CANON_YELLOW = "AI补充，可能与你老师讲的不完全一致"
CANON_AMBER = "AI生成答案，非老师/教材提供"
CANON_GREEN = "来自资料"

# every file that prescribes provenance labels must carry the canonical wording
# (v4-P2: the root zh manual moved to locales/zh/SKILL.md; the sub-skill zh
#  student copy moved to locales/zh/skills/ — the control files that still quote
#  the canonical labels stay pinned too)
CANONICAL_FILES = [
    ("docs", "language-policy.md"),
    ("locales", "zh", "SKILL.md"),
    ("AGENTS.md",),
    ("prompts", "web_prompt.md"),
    ("skills", "exam-help", "SKILL.md"),
    ("skills", "exam-ingest", "SKILL.md"),
    ("locales", "zh", "skills", "exam-help.md"),
    ("locales", "zh", "skills", "exam-cram.md"),
]

# direct student-facing / runtime surfaces must NOT contain any old competing label.
# NB: prose legitimately writes "AI 补充" (space around the Latin token), so we forbid only the
# unambiguous OLD LABEL strings (distinct suffix / wording), not the bare "AI 补充" prose form.
NO_OLD_ENTRYPOINTS = [
    ("SKILL.md",),
    ("locales", "zh", "SKILL.md"),
    ("locales", "en", "SKILL.md"),
    ("prompts", "web_prompt.en.md"),
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
] + sorted(
    # v4-P2: the per-skill student copy moved into the language packs — keep the
    # old-label ban covering every pack file so retired wording cannot resurface
    tuple(os.path.relpath(p, ROOT).split(os.sep))
    for loc in ("zh", "en")
    for p in _glob.glob(os.path.join(ROOT, "locales", loc, "skills", "*.md"))
)
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
        # v4-P2: the zh receipt example moved to the zh language pack
        pack = read("locales", "zh", "skills", "exam-ingest.md")
        self.assertIn("已初始化备考空间", pack, "exam-ingest zh 包缺少中文初始化回执示例")

    # ---- concrete student-facing labels ----
    def test_concrete_chinese_labels_in_tutor(self):
        tutor = read("skills", "exam-tutor", "SKILL.md")
        for label in ("当前阶段", "题面图", "这题在问什么", "图里要读的量", "核心公式", "逐步演算",
                      "答案自检", "知识点溯源", "题目来源", "答案来源", "易错点", "3分钟速记", "现在轮到你"):
            self.assertIn(label, tutor, f"exam-tutor 缺少具体标签: {label}")

    def test_quiz_feedback_labels(self):
        # v4-P2: the zh student wording lives in the zh language pack
        quiz = read("locales", "zh", "skills", "exam-quiz.md")
        self.assertIn("已记录到错题本", quiz, "exam-quiz zh 包缺少归档回执措辞")
        self.assertIn("连错两次", quiz)

    def test_review_replay_and_confusion_wording(self):
        r = read("locales", "zh", "skills", "exam-review.md")
        self.assertIn("错题重做", r, "exam-review zh 包缺少错题重做措辞")
        self.assertIn("疑难复述", r, "exam-review zh 包缺少疑难复述措辞")

    def test_cheatsheet_required_sections(self):
        c = read("skills", "exam-cheatsheet", "SKILL.md")
        for sec in ("必背", "例题", "例题解答", "要点解释"):
            self.assertIn(sec, c, f"小抄缺少栏目: {sec}")
        # v4-P2: the zh cheat-sheet layout example lives in the zh pack too
        pack = read("locales", "zh", "skills", "exam-cheatsheet.md")
        for sec in ("必背", "例题", "例题解答", "要点解释"):
            self.assertIn(sec, pack, f"小抄 zh 包缺少栏目: {sec}")

    # ---- root entry: source-labeling rules preserved + language policy mirrored ----
    # (v4-P2: the root SKILL.md is a language-neutral router; the zh manual that
    #  carries the zh label sentences lives at locales/zh/SKILL.md)
    def test_root_skill_exists_with_provenance_rules(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "SKILL.md")), "根 SKILL.md 不存在")
        zh = read("locales", "zh", "SKILL.md")
        self.assertIn("知识来源标注", zh, "zh 全量入口包缺少「知识来源标注」段")
        self.assertIn(CANON_AMBER, zh, "zh 全量入口包缺少 ⚠️ AI生成答案来源标注")

    def test_root_skill_mirrors_language_default(self):
        # the language-default rule now lives in BOTH layers: the root router
        # carries the English default-en dispatch line, the zh pack the zh wording
        router = read("SKILL.md")
        self.assertIn("default en unless the student opened in Chinese", router,
                      "根路由器缺少 default-en 派发行")
        self.assertIn("locales/zh/SKILL.md", router, "根路由器未指向 zh 全量入口包")
        self.assertIn("language-policy", router, "根路由器未指向 docs/language-policy.md")
        zh = read("locales", "zh", "SKILL.md")
        self.assertIn("简体中文", zh, "zh 全量入口包未镜像「默认简体中文」")
        self.assertIn("language-policy", zh, "zh 全量入口包未指向 docs/language-policy.md")
        self.assertIn(CANON_AMBER, zh, "zh 全量入口包未对齐 canonical 来源标注")

    def test_web_prompt_remains_chinese_first(self):
        web = read("prompts", "web_prompt.md")
        self.assertIn("网页端", web, "web_prompt 不再是中文优先")
        self.assertIn("备考", web)



class A8bLanguageDispatch(unittest.TestCase):
    """A8b：合并首问（模式×时间×语言）、派发规则、en 平行块与锚点存活。"""

    def _read(self, *parts):
        import os
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_cram_combined_ask_and_dispatch(self):
        t = self._read("skills", "exam-cram", "SKILL.md")
        self.assertIn("ask ONE combined question", t)             # 语言并入 A6 首问，不新增阻塞问题
        self.assertIn("语言 / Language：中文 / English / 双语", t)  # 三语语言行
        self.assertIn("--language <语言>", t)                      # 一次 set 立三样
        self.assertIn("NEVER infer `双语`", t)                     # 双语只显式选择
        self.assertIn("SINGLE-LANGUAGE PURITY", t)                 # 派发规则（阶段6 单语言纯净）
        self.assertIn("Simplified Chinese", t)                     # 既有钉字存活

    def test_tutor_en_block_keeps_anchors(self):
        # v4-P2: the former `### English rendering` block is now the en language pack
        t = self._read("locales", "en", "skills", "exam-tutor.md")
        self.assertIn("① Question figure", t)                      # 纯英文块标（阶段6）
        self.assertIn("⑦ Source trace", t)                         # 七步块尾锚
        self.assertIn("Question source:", t)                       # 英文来源块行
        ctl = self._read("skills", "exam-tutor", "SKILL.md")
        self.assertIn("> EN:", ctl)                                # 双语镜像行形态（Language packs 段引用）
        self.assertIn("locales/en/skills/exam-tutor.md", ctl)      # 控制层派发到 en 包

    def test_quiz_en_block_keeps_receipt(self):
        t = self._read("locales", "en", "skills", "exam-quiz.md")
        self.assertIn("Recorded to the mistake archive", t)        # 英文回执（阶段6）
        ctl = self._read("skills", "exam-quiz", "SKILL.md")
        self.assertIn("locales/en/skills/exam-quiz.md", ctl)       # 控制层派发到 en 包

    def test_policy_carries_single_language_purity(self):
        # 阶段 6：锚点不变性已废除——政策必须携带新原则与词汇表（旧名只允许出现在废除说明里）
        p = read("docs", "language-policy.md")
        self.assertIn("SINGLE-LANGUAGE PURITY", p, "缺单语言纯净原则")
        self.assertIn("PERSISTED / JUDGING-LAYER VOCABULARY", p, "缺持久化/判分层词汇表")
        self.assertIn("EN CANONICAL VOCABULARY", p, "缺 EN canonical 词表")
        self.assertNotIn("### ANCHOR-INVARIANCE PRINCIPLE", p, "旧锚点不变性小节不应复活")
class A8cEnEntrypoints(unittest.TestCase):
    """A8c/C2b/v4-P2：两个派生英文入口（locales/en/SKILL.md / prompts/web_prompt.en.md）。
    阶段 6 反转：en 面**零 CJK**（钉在 tests/test_language_purity.py T1），本类钉
    EN canonical 词表在场 + 结构契约；zh 为行为事实源（locales/zh/SKILL.md）。"""

    EN_FILES = (("locales", "en", "SKILL.md"), ("prompts", "web_prompt.en.md"))

    EN_LABELS = ("🟢 From your materials",
                 "🟡 AI-supplemented — may differ from what your teacher taught",
                 "⚠️ AI-generated answer — not from your teacher or textbook")

    def _read(self, parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_files_exist_no_frontmatter(self):
        for parts in self.EN_FILES:
            t = self._read(parts)
            self.assertFalse(t.startswith("---"), parts)          # 非可触发入口
            self.assertIn("source of truth", t, parts)            # zh 为事实源声明

    def test_en_canonical_vocabulary_present(self):
        need = self.EN_LABELS + (
            "⚠️ Temporarily overriding your",
            "scope preference",
            "The materials do not contain an answer to this question.",
            "Question-side asset", "Answer-side asset",
            "① Question figure", "⑦ Source trace",
            "Question source:", "Answer source:")
        for parts in self.EN_FILES:
            t = self._read(parts)
            for tok in need:
                self.assertIn(tok, t, (parts, tok))

    def test_label_lines_carry_en_canonical(self):
        # 同行守卫（反转版）：🟢/🟡 作标签的行必须同行带完整 EN canonical 句；⚠️ 行须带
        # AI-generated / Temporarily overriding / 视觉门禁等已知用途之一。集合引用先剥。
        import re as _re
        setref = _re.compile(r"🟢[/\s]*🟡[/\s]*(?:⚠️)?")
        codespan = _re.compile(r"`[^`\n]*`")   # 代码 span 里提及的 zh canonical 形不算标签用法
        req = (("🟢", ("From your materials",)),
               ("🟡", ("AI-supplemented",)),
               ("⚠️", ("AI-generated answer", "Temporarily overriding")))
        for parts in self.EN_FILES:
            for ln in self._read(parts).splitlines():
                s = setref.sub("", codespan.sub("", ln))
                for emoji, needles in req:
                    if emoji in s:
                        self.assertTrue(any(x in s for x in needles), (parts, ln[:100]))

    def test_web_prompt_en_specific_pins(self):
        t = self._read(("prompts", "web_prompt.en.md"))
        self.assertIn("NEVER claim you have written or updated `study_state.json`", t)
        self.assertIn("read-only fact source", t)
        self.assertIn('question_text_status="stub"', t)
        self.assertIn('"page_reference"', t)
        self.assertIn("3-minute mnemonic", t)                     # 收尾块 EN 名（默认不输出规则随文）
        self.assertIn("default reply language", t.lower())        # web 无 state 的英文默认自声明

    def test_root_en_specific_pins(self):
        t = self._read(("locales", "en", "SKILL.md"))
        self.assertIn("set-check", t)
        self.assertIn("mistake_archive", t)
        self.assertIn("Before asking, explaining, hinting, or solving", t)
        self.assertIn("locales/zh/SKILL.md", t)                   # 指回 zh 事实源

    def test_machine_token_parity_with_zh_root(self):
        zh = self._read(("locales", "zh", "SKILL.md"))
        en = self._read(("locales", "en", "SKILL.md"))
        for tok in ("study_state.json", "update_progress.py", "quiz_bank.json",
                    "requires_assets=true", "maybe_requires_assets=true",
                    "select_questions.py", "select_hard_questions.py"):
            self.assertIn(tok, zh, tok)
            self.assertIn(tok, en, tok)

    def test_en_surfaces_are_discoverable(self):
        # A8c-2：英文用户必须能从 README/兼容矩阵/portability/AGENTS 找到 en 入口面
        #（v4-P2 改址后发现路径不能丢：SKILL.en.md → locales/en/SKILL.md）
        readme = self._read(("README.md",))
        self.assertIn("locales/en/SKILL.md", readme)
        self.assertIn("prompts/web_prompt.en.md", readme)
        self.assertIn("web_prompt.en.md", self._read(("docs", "agent-portability.md")))
        self.assertIn("locales/en/SKILL.md", self._read(("docs", "agent-portability.md")))
        self.assertIn("web_prompt.en.md", self._read(("AGENTS.md",)))
        self.assertIn("locales/en/SKILL.md", self._read(("AGENTS.md",)))

    def test_retired_root_en_entry_absent(self):
        # v4-P2：根级 SKILL.en.md 已退役——绝不允许复活成第二个英文事实源
        self.assertFalse(os.path.exists(os.path.join(ROOT, "SKILL.en.md")),
                         "根级 SKILL.en.md 不应存在（en 全量入口 = locales/en/SKILL.md）")


if __name__ == "__main__":
    unittest.main()
