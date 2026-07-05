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
    ("SKILL.en.md",),
    ("prompts", "web_prompt.en.md"),
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
    ("SKILL.en.md",),
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
        for label in ("当前阶段", "题面图", "这题在问什么", "图里要读的量", "核心公式", "逐步演算",
                      "答案自检", "知识点溯源", "题目来源", "答案来源", "易错点", "3分钟速记", "现在轮到你"):
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

    # ---- root SKILL.md: source-labeling rules preserved + language policy mirrored ----
    def test_root_skill_exists_with_provenance_rules(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "SKILL.md")), "根 SKILL.md 不存在")
        root = read("SKILL.md")
        self.assertIn("知识来源标注", root, "根 SKILL.md 缺少「知识来源标注」段")
        self.assertIn(CANON_AMBER, root, "根 SKILL.md 缺少 ⚠️ AI生成答案来源标注")

    def test_root_skill_mirrors_language_default(self):
        root = read("SKILL.md")
        self.assertIn("简体中文", root, "根 SKILL.md 未镜像「默认简体中文」")
        self.assertIn("language-policy", root, "根 SKILL.md 未指向 docs/language-policy.md")
        self.assertIn(CANON_AMBER, root, "根 SKILL.md 未对齐 canonical 来源标注")

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
        self.assertIn("LANGUAGE-INVARIANT", t)                     # 派发规则 + 锚点不变性
        self.assertIn("Simplified Chinese", t)                     # 既有钉字存活

    def test_tutor_en_block_keeps_anchors(self):
        t = self._read("skills", "exam-tutor", "SKILL.md")
        self.assertIn("### English rendering", t)
        self.assertIn("① 题面图 (Question figure)", t)             # token+gloss 形态
        self.assertIn("题目来源：lec", t.replace("lecture03", "lec"))  # 来源块行原样（示例）
        self.assertIn("> EN:", t)                                  # 双语镜像行形态

    def test_quiz_en_block_keeps_receipt(self):
        t = self._read("skills", "exam-quiz", "SKILL.md")
        self.assertIn("已记录到错题本 (recorded to the mistake archive)", t)
        self.assertIn("### English rendering", t)

    def test_policy_carries_anchor_invariance(self):
        t = self._read("docs", "language-policy.md")
        self.assertIn("ANCHOR-INVARIANCE PRINCIPLE", t)
        self.assertIn("Language state & dispatch", t)



class A8cEnEntrypoints(unittest.TestCase):
    """A8c：英文发布形态的两个派生入口（SKILL.en.md / prompts/web_prompt.en.md）。
    zh 为行为事实源，en 为派生渲染；十类锚点逐字节中文 + gloss；en 面须真英文（另有纯度测试）。"""

    EN_FILES = (("SKILL.en.md",), ("prompts", "web_prompt.en.md"))

    # en 面纯度测试的转义（**独立于** test_control_plane_language.ALLOWED_TOKENS——那边的
    # 死 token 卫生只扫 skills/*+AGENTS+argparse，这里的 token 在那边会被误判为死）
    EN_SURFACE_TOKENS = (
        "🟢 来自资料", "🟡 AI补充，可能与你老师讲的不完全一致", "⚠️ AI生成答案，非老师/教材提供",
        "来自资料", "AI补充，可能与你老师讲的不完全一致", "AI生成答案，非老师/教材提供",
        "临时覆盖你的", "范围偏好",
        "① 题面图", "② 这题在问什么", "③ 图里要读的量", "④ 核心公式",
        "⑤ 逐步演算", "⑥ 答案自检", "⑦ 知识点溯源",
        "题目来源", "答案来源", "来源未知", "来源页未知",
        "易错点", "3分钟速记", "现在轮到你",
        "错题本", "错题档案", "已记录到错题本", "已记录到疑难点", "概念疑难点记录",
        "阶段", "还记得", "复述", "做题实测",
        "题面图 / question-side asset", "答案图 / answer-side asset", "题面图", "答案图",
        "资料里没有这道题的答案",
        "零基础从头讲", "某章起步补弱", "查缺补漏", "≤1天", "1-3天", "3-7天", ">7天",
        "在窗口", "窗口外", "已实测", "待复盘", "已订正", "已回顾", "已解决", "已复盘",
        "备战计划", "实时进度", "进度打卡面板", "备考科目", "当前复习", "进度打卡",
        "错题累积", "第 X/N 阶段已通关", "第 X 阶段",
        "中文", "双语", "讲解模板", "混合题池", "语言",
    )

    def _read(self, parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_files_exist_no_frontmatter(self):
        for parts in self.EN_FILES:
            t = self._read(parts)
            self.assertFalse(t.startswith("---"), parts)          # 无 YAML 前言（不是可触发 skill 文件）
            self.assertIn("source of truth", t, parts)            # 派生渲染声明（zh 为事实源）

    def test_ten_anchor_classes_present(self):
        # 只要求 zh 源文件里确实存在的锚点（en 是派生渲染，不得要求 zh 没有的内容）
        need = ("🟢 来自资料", "🟡 AI补充，可能与你老师讲的不完全一致", "⚠️ AI生成答案，非老师/教材提供",
                "临时覆盖你的", "范围偏好", "① 题面图", "⑦ 知识点溯源", "题目来源", "答案来源",
                "易错点", "阶段", "现在轮到你", "题面图 / question-side asset",
                "答案图 / answer-side asset", "资料里没有这道题的答案")
        for parts in self.EN_FILES:
            t = self._read(parts)
            for tok in need:
                self.assertIn(tok, t, (parts, tok))

    def test_en_surface_is_actually_english(self):
        # 纯度：剥 「…」/反引号/EN_SURFACE_TOKENS 后零 CJK——旗舰英文形态不许烂成中英混杂
        import re as _re
        cjk = _re.compile(u"[㐀-䶿一-鿿]")
        for parts in self.EN_FILES:
            t = self._read(parts)
            t = _re.sub(u"「[^」]*」", "", t)
            t = _re.sub(r"`[^`\n]*`", "", t)
            for tok in sorted(self.EN_SURFACE_TOKENS, key=len, reverse=True):
                t = t.replace(tok, "")
            bad = [ln.strip()[:80] for ln in t.splitlines() if cjk.search(ln)]
            self.assertFalse(bad, (parts, bad[:6]))

    def test_label_lines_carry_chinese_canonical(self):
        # 同行守卫：🟢/🟡 只作来源标注用、⚠️ 另用于范围覆盖——**任何**含它们的行必须同行带
        # 对应中文 canonical（防英文竞争标注）。「🟢/🟡/⚠️」「🟢🟡」这类对标签集合的紧凑
        # 引用先剥掉（是集合引用、不是标注本身）。mutation 验证：把任一标签行换成
        # 「🟢 From the materials」必须失败。
        import re as _re
        setref = _re.compile(r"🟢[/／\s]*🟡[/／\s]*(?:⚠️)?")
        req = (("🟢", ("来自资料",)), ("🟡", ("AI补充",)),
               ("⚠️", ("AI生成答案", "临时覆盖你的")))
        for parts in self.EN_FILES:
            for ln in self._read(parts).splitlines():
                s = setref.sub("", ln)
                for emoji, zhs in req:
                    if emoji in s:
                        self.assertTrue(any(z in s for z in zhs), (parts, ln[:100]))

    def test_web_prompt_en_specific_pins(self):
        t = self._read(("prompts", "web_prompt.en.md"))
        self.assertIn("NEVER claim you have written or updated `study_state.json`", t)
        self.assertIn("read-only fact source", t)                 # 粘贴 state = 只读恢复（对齐 zh 只读事实源）
        self.assertIn('question_text_status="stub"', t)           # stub 门禁与 zh 同钉
        self.assertIn('"page_reference"', t)
        self.assertIn("3分钟速记", t)                              # zh web 专有锚（root 无此节）
        self.assertIn("default reply language", t.lower())        # web 无 state 的英文默认自声明

    def test_root_en_specific_pins(self):
        t = self._read(("SKILL.en.md",))
        self.assertIn("set-check", t)
        self.assertIn("mistake_archive", t)
        self.assertIn("Before asking, explaining, hinting, or solving", t)
        self.assertIn("SKILL.md", t)                              # 指回 zh 事实源

    def test_en_surfaces_are_discoverable(self):
        # A8c-2：英文用户必须能从 README/兼容矩阵/portability/AGENTS 找到 en 入口面
        readme = self._read(("README.md",))
        self.assertIn("SKILL.en.md", readme)
        self.assertIn("prompts/web_prompt.en.md", readme)
        self.assertIn("web_prompt.en.md", self._read(("docs", "agent-portability.md")))
        self.assertIn("web_prompt.en.md", self._read(("AGENTS.md",)))

    def test_machine_token_parity_with_zh_root(self):
        zh = self._read(("SKILL.md",))
        en = self._read(("SKILL.en.md",))
        for tok in ("study_state.json", "update_progress.py", "quiz_bank.json",
                    "requires_assets=true", "maybe_requires_assets=true",
                    "select_questions.py", "select_hard_questions.py"):
            self.assertIn(tok, zh, tok)
            self.assertIn(tok, en, tok)


if __name__ == "__main__":
    unittest.main()
