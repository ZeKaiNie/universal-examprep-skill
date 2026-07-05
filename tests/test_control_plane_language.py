# -*- coding: utf-8 -*-
"""PR D + A8a — modular skills use an English control plane + a Chinese student-facing layer. Stdlib only.

Enforces the split that the language policy (docs/language-policy.md) declared:
- every skills/*/SKILL.md exposes the required English control-section headings,
- student-facing examples/templates stay natural Simplified Chinese,
- no vague English control wording is introduced,
- the canonical provenance labels survive,
- (A8a) control-plane text is PURE ENGLISH, enforced zero-CJK over enumerated control
  sections — with three structural escapes for Chinese that legitimately lives inside
  control prose (see below). No comment-based waivers: every escape is structural.

A8a scope (what "control plane" means for the zero-CJK lint):
- skills/*/SKILL.md — every `## ` section EXCEPT: `## Student-facing Output` and any
  section whose heading itself contains CJK (a CJK heading is by definition a
  student-facing template block, e.g. confusion-tracker's 「## 💡 概念疑难点记录」).
  YAML frontmatter is exempt (description/argument-hint are the Chinese trigger surface).
  The H1 title line IS control (T2).
- AGENTS.md — the whole file is agent-directive (T3).
- scripts/*.py — the argparse CLI contract only (description= / epilog= / help=), T4.
OUT of scope (documented, deliberate): root SKILL.md + prompts/web_prompt.md (policy-
exempt Chinese entrypoints), templates/, docs/, script runtime fail-loud/status strings
(they feed student-visible surfaces — own design needed), persisted data vocabulary.

Escapes (stripped before the CJK scan, in this order):
  E1 「…」  — verbatim student-visible phrases / announcements / utterances
  E2 `…`   — code spans: CLI args, state values, placeholders, filenames, pref keys
  E3 ALLOWED_TOKENS — canonical labels & persisted vocabulary that must stay Chinese
     byte-exact (single source: docs/language-policy.md + persisted-state contracts)
"""
import ast
import glob
import os
import re
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
CANON_GREEN = "来自资料"

# E3 — canonical labels & persisted vocabulary allowed to appear bare inside control text.
# Everything here is either pinned by another test (language-policy / visual-asset /
# source-taxonomy / localization-boundary) or persisted into student workspaces
# (study_state.json / quiz_bank.json / generated md) — translating them = state migration.
ALLOWED_TOKENS = (
    # canonical provenance labels (docs/language-policy.md)
    "⚠️ AI生成答案，非老师/教材提供", "🟡 AI补充，可能与你老师讲的不完全一致", "🟢 来自资料",
    CANON_AMBER, CANON_YELLOW, CANON_GREEN,
    # bilingual asset labels (docs/file-format.md §4)
    "题面图 / question-side asset", "题面图", "答案图",
    # seven-step template labels (exam-tutor canon; behavior_smoke parses transcripts for these)
    "① 题面图", "② 这题在问什么", "③ 图里要读的量", "④ 核心公式",
    "⑤ 逐步演算", "⑥ 答案自检", "⑦ 知识点溯源",
    "这题在问什么", "图里要读的量", "核心公式", "逐步演算", "答案自检", "知识点溯源",
    "材料里要读的关键句/概念", "核心概念", "逐点展开",
    "七步精讲", "文科变体", "讲解模板",
    # closers (default-off blocks; names are canonical)
    "易错点", "3分钟速记", "现在轮到你",
    # learning modes × time tiers (persisted study_state values, A6)
    "零基础从头讲", "某章起步补弱", "查缺补漏",
    "≤1天", "1-3天", "3-7天", ">7天",
    # knowledge-window / record statuses (persisted)
    "在窗口", "窗口外", "已实测", "待回顾", "待复盘", "已订正", "已回顾", "已解决", "已复盘",
    # scope / pools (persisted + A2 contract)
    "混合题池", "混合",
    # receipts + source block shape (student-visible canon quoted in control text)
    "已记录到错题本", "已记录到疑难点", "题目来源", "答案来源", "来源未知", "来源页未知",
    # ordering names (select_hard_questions ORDER_LABEL data values)
    "先易后难", "先难挑战",
    # generated-md section names (persisted view blocks)
    "❌ 错题档案", "💡 概念疑难点记录", "错题档案", "概念疑难点记录",
    # abstention canon
    "资料里没有这道题的答案",
    # cheatsheet canonical columns
    "必背", "老师强调",
    # bare circled digits = seven-step block references in control prose (structural, no translation)
    "①", "②", "③", "④", "⑤", "⑥", "⑦",
)

# CJK detection: Han (base + Ext-A) + CJK punctuation + fullwidth punctuation +
# circled digits + 【】. Anything left after escape-stripping trips the lint —
# including stray fullwidth commas in "English" prose (catches half-translated lines).
CJK_RE = re.compile(
    u"[㐀-䶿一-鿿　-〿"
    u"！（），：；？①-⑳【】]")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def skill_files():
    return sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))


def strip_escapes(text):
    text = re.sub(u"「[^」]*」", "", text)               # E1 first (may contain backticks)
    text = re.sub(r"`[^`\n]*`", "", text)                # E2 code spans
    for tok in sorted(ALLOWED_TOKENS, key=len, reverse=True):   # E3 — longest first,
        text = text.replace(tok, "")                     # 防短 token 吃掉长 token 的一部分
    return text


def split_frontmatter(text):
    """Return (frontmatter, rest). Frontmatter = leading --- fence pair, if present."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl == -1:
                return text, ""
            return text[:nl + 1], text[nl + 1:]
    return "", text


def control_sections(text):
    """Yield (heading, body) for every non-exempt `## ` section of a SKILL.md body."""
    _, body = split_frontmatter(text)
    parts = re.split(r"(?m)^(## .+)$", body)
    # parts = [pre-h2 chunk, heading, body, heading, body, ...]
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()
        section = parts[i + 1]
        if heading == "## Student-facing Output":
            continue                                     # the established Chinese zone
        if CJK_RE.search(heading):
            continue                                     # CJK heading = student-facing template block
        yield heading, section


def cjk_offenses(text):
    """Line numbers + snippets of CJK remaining after escape-stripping (for messages)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        rest = strip_escapes(line)
        m = CJK_RE.search(rest)
        if m:
            out.append("  line %d: %r" % (n, rest.strip()[:80]))
    return out


def argparse_strings(path):
    """(kwarg, string) pairs for description=/epilog=/help= literals in a script (ast-based)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords or []:
            if kw.arg in ("description", "epilog", "help"):
                v = kw.value
                if isinstance(v, ast.BinOp):             # "..." % (...) → lint the template
                    v = v.left
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    found.append((kw.arg, v.value))
    return found


class ControlPlaneLanguageTest(unittest.TestCase):
    def test_required_english_headings(self):
        for s in SKILLS:
            t = read("skills", s, "SKILL.md")
            for h in REQUIRED_HEADINGS:
                self.assertIn(h, t, f"{s}/SKILL.md 缺少英文控制章节 {h}")

    def test_tutor_chinese_template_preserved(self):
        t = read("skills", "exam-tutor", "SKILL.md")
        for label in ("当前阶段", "这题在问什么", "核心公式", "逐步演算", "答案自检",
                      "知识点溯源", "易错点", "3分钟速记"):
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

    # ---------------- A8a: zero-CJK over enumerated control sections ----------------

    def test_control_sections_are_pure_english(self):
        # T1 — default-deny: every `## ` section of every subskill is control unless
        # exempt (Student-facing Output / CJK heading). New subskills & new sections
        # are auto-covered (glob + default-deny), so this can't be whack-a-moled.
        bad = []
        for path in skill_files():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            rel = os.path.relpath(path, ROOT)
            for heading, body in control_sections(text):
                off = cjk_offenses(body)
                if off:
                    bad.append("%s %s:\n%s" % (rel, heading, "\n".join(off)))
        self.assertFalse(bad, "控制层出现未转义中文（转义方式：「」/反引号/ALLOWED_TOKENS）：\n"
                         + "\n".join(bad))

    def test_h1_title_is_english(self):
        # T2 — the H1 line of each subskill is control (its Chinese tail must be escaped)
        bad = []
        for path in skill_files():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            _, body = split_frontmatter(text)
            for line in body.splitlines():
                if line.startswith("# ") and not line.startswith("## "):
                    if CJK_RE.search(strip_escapes(line)):
                        bad.append("%s: %r" % (os.path.relpath(path, ROOT), line))
                    break
        self.assertFalse(bad, "子技能 H1 标题含未转义中文：\n" + "\n".join(bad))

    def test_agents_md_control_is_english(self):
        # T3 — AGENTS.md is entirely agent-directive; zero CJK after escapes
        off = cjk_offenses(read("AGENTS.md"))
        self.assertFalse(off, "AGENTS.md 控制文本含未转义中文：\n" + "\n".join(off))

    def test_script_cli_contract_is_english(self):
        # T4 — the argparse contract (description/epilog/help) the agent reads via --help
        bad = []
        for path in sorted(glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
            for kwarg, s in argparse_strings(path):
                rest = strip_escapes(s)
                if CJK_RE.search(rest):
                    bad.append("%s %s=%r" % (os.path.relpath(path, ROOT), kwarg, s[:70]))
        self.assertFalse(bad, "脚本 argparse 契约（agent 经 --help 读）含未转义中文：\n"
                         + "\n".join(bad))

    def test_allowed_tokens_all_alive(self):
        # dead-token hygiene: every E3 escape must actually occur somewhere in the
        # linted surfaces (skills/*/SKILL.md + AGENTS.md + scripts argparse strings)
        hay = "".join(read("skills", s, "SKILL.md") for s in SKILLS) + read("AGENTS.md")
        for path in sorted(glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
            hay += "".join(s for _, s in argparse_strings(path))
        dead = [t for t in ALLOWED_TOKENS if t not in hay]
        self.assertFalse(dead, "ALLOWED_TOKENS 里有已不存在于任何被扫描面的死 token（清理它们）: %r" % dead)


if __name__ == "__main__":
    unittest.main()
