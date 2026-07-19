#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tier 2 behavioral smoke — DETERMINISTIC by default, real-LLM smoke OPT-IN only.

This harness tests the skill as a *tutoring workflow*, not just as static files.

  python benchmark/behavior_smoke/run_behavior_smoke.py --check-fixture   # validate the mini-course
  python benchmark/behavior_smoke/run_behavior_smoke.py --mock            # run detectors on mock outputs

The --mock / --check-fixture paths are stdlib-only, no network, no LLM, no API key — safe for CI.
Real-agent smoke (single-turn per scenario, reusing the SAME detector FUNCTIONS as --mock — each scenarios primary positive check, applied to the one live reply) is WIRED but gated behind BOTH a flag and an env opt-in and never runs by default (its wiring is tested deterministically against a stub agent in tests/test_behavior_smoke_live.py):

  RUN_SKILL_BEHAVIOR_LLM=1 python benchmark/behavior_smoke/run_behavior_smoke.py --llm

It will NOT call any model in CI, never reads API keys, and never runs a paid benchmark.
"""
import os
import re
import sys
import json
import shutil
import tempfile
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))           # repo root
FIXTURE = os.path.join(HERE, "fixtures", "mini_course")
SCENARIOS = os.path.join(HERE, "scenarios.json")

# canonical provenance labels — single source of truth is docs/language-policy.md
CANON_LABELS = [
    "🟢 来自资料",
    "🟡 AI补充，可能与你老师讲的不完全一致",
    "⚠️ AI生成答案，非老师/教材提供",
]


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# ---------------- detectors (deterministic, stdlib-only) ----------------

def load_quiz_bank_ids(workspace):
    data = json.loads(_read(os.path.join(workspace, "references", "quiz_bank.json")))
    return {str(q.get("id")) for q in data if isinstance(q, dict) and q.get("id") is not None}


def _q_is_visual(q):
    """A lecture/figure item: it carries (or may carry) a question-side visual asset."""
    if q.get("requires_assets") or q.get("maybe_requires_assets"):
        return True
    if q.get("question_text_status") in ("stub", "page_reference"):
        return True
    for a in (q.get("assets") or []):
        if isinstance(a, dict) and a.get("role") in ("question_context", "figure", "diagram", "table"):
            return True
    return False


def load_quiz_bank_map(workspace):
    """{id: {'question','answer','source','ai_generated','visual','chapter','phase'}} — used for
    content-match, answer-leak guard, bank-derived provenance/visual checks, and chapter/phase scoping."""
    data = json.loads(_read(os.path.join(workspace, "references", "quiz_bank.json")))
    return {str(q["id"]): {"question": q.get("question", ""), "answer": q.get("answer"),
                           "source": q.get("source"), "ai_generated": q.get("ai_generated"),
                           "visual": _q_is_visual(q),
                           "chapter": q.get("chapter"), "phase": q.get("phase")}
            for q in data if isinstance(q, dict) and q.get("id") is not None}


def extract_question_ids(text):
    """Quiz outputs mark each drawn item as [#<id>]; pull them all out."""
    return re.findall(r"\[#([^\]\s]+)\]", text or "")


# numbered / Q. / Qn: / 第n题 — always a question item
_NUM_ITEM_RE = re.compile(r"^\s*(?:\d+\s*[.、)）]|[Qq]\d*\s*[.、:：)）]|第\s*[一二三四五六七八九十百零\d]+\s*题\s*[:：]?)")
# an OPTION line — labels are A–D (NOT all of A–Z, so "Q." is a question, not an option)
_OPTION_RE = re.compile(r"^\s*[-*•]?\s*(?:[A-Da-d]|[一二三四甲乙丙丁]|[①②③④⑤⑥])\s*[.、)）.]")


def _is_question_item(ln):
    if _OPTION_RE.match(ln):
        return False
    if _NUM_ITEM_RE.match(ln):
        return True
    # a BULLET counts as a question only if it actually reads like one (ends with ？/?),
    # so a harmless instruction bullet ("- 请直接回复答案") isn't required to carry a tag.
    return bool(re.match(r"^\s*[-*•]\s", ln) and re.search(r"[？?]\s*$", ln))


def assert_quiz_ids_in_bank(text, bank):
    """bank = set of ids (ID/scope check) OR dict {id: question} (also content-match each item).
    Pass a CHAPTER-SCOPED bank to enforce that the quiz draws only from the requested chapter."""
    t = text or ""
    allowed = set(bank)
    qmap = bank if isinstance(bank, dict) else None
    # (a) EVERY [#id] tag anywhere must be in the (scoped) bank — catches invented OR out-of-scope tags.
    ids = extract_question_ids(t)
    if not ids or any(i not in allowed for i in ids):
        return False
    # (b) no malformed multi-tag lines, no UNTAGGED question-item line, and (c) each tag's bank question
    #     must appear in ITS OWN segment (this line + following lines up to the next tag) — so swapping
    #     tag↔content across items (mc_q1's tag on mc_q2's text) is caught, and an invented body fails.
    lines = t.splitlines()
    for idx, ln in enumerate(lines):
        lids = extract_question_ids(ln)
        if len(lids) > 1:
            return False
        if not lids and _is_question_item(ln):
            return False
        if lids and qmap is not None:
            seg = re.sub(r"\[#[^\]]+\]", "", ln)
            for nxt in lines[idx + 1:]:
                if extract_question_ids(nxt):
                    break
                seg += nxt
            seg = re.sub(r"\s+", "", seg)
            b = re.sub(r"\s+", "", qmap.get(lids[0], ""))
            if b:
                k = min(8, len(b))
                # require BOTH ends of the bank question to appear: a prefix-collision or an appended
                # invented body changes the END, so it fails; a middle paraphrase (dropping e.g.
                # "（queue）") keeps both ends, so it passes.
                if b[:k] not in seg or b[-k:] not in seg:
                    return False
    return True


def has_canonical_provenance_labels(text):
    # each canonical label must ANNOTATE content — prefix `label：内容` OR suffix `内容（label）` —
    # not sit in a legend (single- or multi-line) of bare labels with no labelled answer.
    t = text or ""
    for lbl in CANON_LABELS:
        used = False
        for m in re.finditer(re.escape(lbl), t):
            # content must be on the SAME line as the label ([ \t] only — no newline crossing), so a
            # multi-line legend "🟢 …：\n🟡 …：\n答案：…" with an unlabelled answer does NOT count.
            prefix_use = re.match(r"[ \t]*[:：][ \t]*\S", t[m.end():m.end() + 24])       # label：内容
            # 内容（label — the char before （ must be real content, NOT another label's closing ）
            # (so a paren-legend "标签说明（🟢…）（🟡…）（⚠️…）" doesn't read as labelled content)
            suffix_use = re.search(r"[^）)\s][ \t]*[（(][ \t]*$", t[max(0, m.start() - 16):m.start()])
            if prefix_use or suffix_use:
                used = True
                break
        if not used:
            return False
    return True


def scope_override_declared(text):
    """A2 scope contract: serving items OUTSIDE the active scope must carry the verbatim override
    marker BEFORE the first question item — a declaration after the item does not count."""
    t = text or ""
    m = re.search(r"⚠️\s*临时覆盖你的\s*\S+\s*范围偏好", t)
    if not m:
        return False
    # the FIRST question item may be an UNTAGGED question line, not only a [#id] tag — a quiz asked
    # before the declaration must fail either way
    pos = len(t)
    tag = re.search(r"\[#[^\]]+\]", t)
    if tag:
        pos = tag.start()
    off = 0
    for ln in t.splitlines(True):
        stripped = ln.rstrip("\n")
        # 「题目：…」无编号提问行也是第一道题——声明必须先于它（不只认编号/bullet/[#id] 行）
        if _is_question_item(stripped) or re.match(r"^\s*题目?\s*[一二三四五六七八九十\d]*\s*[:：]", stripped):
            pos = min(pos, off)
            break
        off += len(ln)
    return m.start() < pos


# A5 七步模板的块名（含文科变体）——正文切割时，圆圈数字行只有后接这些块名才算新节，
# 这样 ⑤ 正文里的 ①② 子步骤（如「① 先算地址增量」）不会把所在节的正文误切成空。
# 长名在前，保证正则交替优先匹配更长的文科变体（如「材料里要读的关键句」先于「材料里要读的关键」）。
_TT_SECTION_NAMES = ("材料里要读的关键句/概念", "材料里要读的关键概念", "材料里要读的关键句",
                     "材料里要读的关键", "这题在问什么", "这题考什么", "知识点溯源", "图里要读的量",
                     "逐步演算", "逐步代入", "逐点展开", "核心概念", "核心公式",
                     "为什么这个答案成立", "题面图")
# 0 容差边界：块名后必须紧跟分隔符（冒号/空白/行尾）才算「新节标题」；否则是正文里的子步骤
# （如「① 核心公式代入得地址。」——名字后是「代」不是分隔符，不切）。
_CIRCLED_SECTION = (r"[①②③④⑤⑥⑦⑧⑨⑩]\s*(?:" + "|".join(_TT_SECTION_NAMES) + r")(?=[：:\s]|$)")


def _heading_present(text, name):
    """True if `name` is a section HEADING — markdown (## / **bold**), an ordered-list heading
    (`1. 考点拆解`), OR the skill's documented bracket block (`【考点拆解】`) — not an inline mention."""
    n = re.escape(name)
    # 名字外可选包一层【】：这样 `## 【易错点】` / `**【3分钟速记】**` 这类"markdown 前缀 + 方括号名"
    # 也算标题（QR_5）——否则 no_unsolicited_closing_blocks 会漏掉这些收尾块形态。
    md = (rf"(?m)^\s{{0,3}}"
          rf"(?:#{{1,4}}\s*|\*\*\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*|[0-9一二三四五六七八九十]+\s*[、.．)）]\s*){{1,3}}"
          rf"[【〖]?\s*{n}\s*[】〗]?")
    # the bracket block must START a line (a heading), not be inline in a checklist like "请包含：【…】【…】"
    bracket = rf"(?m)^\s{{0,3}}[【〖]\s*{n}\s*[】〗]"
    # the documented student-facing closer format is a bare line-start `名字：`（如 易错点：/3分钟速记：）—
    # a heading too; the colon must follow the name directly, so inline mentions don't count.
    bare = rf"(?m)^\s{{0,3}}{n}\s*[:：]"
    return (bool(re.search(md, text or "")) or bool(re.search(bracket, text or ""))
            or bool(re.search(bare, text or "")))


def _heading_has_body(text, name):
    """The section under `name`'s heading must have actual body content (not an empty heading)."""
    n = re.escape(name)
    # line-anchored with OPTIONAL prefixes so the bare `名字：` heading form gets a body check too
    m = re.search(rf"(?m)^\s{{0,3}}(?:#{{1,4}}\s*|\*\*\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*|[0-9一二三四五六七八九十]+\s*[、.．)）]\s*|[【〖]\s*)*{n}\s*[】〗]?",
                  text or "")
    if not m:
        return False
    rest = (text or "")[m.end():]
    # cut the body at the next markdown/bracket heading only — NOT at numbered lines, since a section
    # body legitimately contains ordered lists (e.g. "1. 判断结构类型 …").
    nxt = re.search(r"(?m)^\s{0,3}(?:#{1,4}\s|\*\*|[【〖]|" + _CIRCLED_SECTION + r")", rest)
    body = rest[:nxt.start()] if nxt else rest
    return len(re.sub(r"\s+", "", body)) >= 2


def _zb(text, *variants):
    return any(_heading_present(text, v) and _heading_has_body(text, v) for v in variants)


def has_zero_basic_sections(text):
    # each required part must be a real heading AND have actual body content under it.
    # A5 起精讲走七步模板：② 这题在问什么 吸收了 考点拆解，④⑤ 核心公式/逐步演算 吸收了
    # 标准答题步骤——新旧两种输出格式都满足零基础精讲的结构要求。
    # 易错点/3分钟速记/现在轮到你 是可选收尾块（仅学生要求才输出），不再是结构要求。
    return (_zb(text, "考点拆解", "这题在问什么", "这题考什么")
            and _zb(text, "标准答题步骤", "标准答题模板", "逐步演算", "逐步代入", "逐点展开"))



# ---- A5：七步讲解模板 + 每题固定来源块 --------------------------------------------------------
# 七步逐一绑定「期望的圆圈序号」（① 第1步 … ⑦ 第7步）+ 块名变体（含文科变体）。绑定序号 → 抓
# 「全用①/编号错乱」（Codex R2-HKR）；块名后必须紧跟标题终止符 → 子步骤「① 核心公式代入…」不再
# 冒充 ④ 标题（HKH）；只认这七个圆圈步骤标题作正文边界 → 纯编号标题骨架也不算有正文（HKJ）。
_SEVEN_STEPS = (
    ("①", ("题面图",)),
    ("②", ("这题在问什么", "这题考什么")),
    ("③", ("图里要读的量", "材料里要读的关键句/概念", "材料里要读的关键概念",
            "材料里要读的关键句", "材料里要读的关键")),
    ("④", ("核心公式", "核心概念")),
    ("⑤", ("逐步演算", "逐步代入", "逐点展开论证", "逐点展开")),
    ("⑥", ("为什么这个答案成立",)),
    ("⑦", ("知识点溯源",)),
)
# 块名后的合法标题终止符：冒号 / 空白 / 行尾 / 括注起始（AI 答案 ⑤ 标题「逐步演算（⚠️…）」）/ 加粗星号 / 方括号收尾。
_STEP_DELIM = r"(?=[：:\s（(*】〗]|$)"
# 答案/解析块标题匹配（用于 AI 答案 ⚠️ 检查）——沿用较宽的行首前缀集。
_TT_PREFIX = (r"(?m)^\s{0,3}(?:#{1,4}\s*|\*\*\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*"
              r"|[0-9一二三四五六七八九十]+\s*[、.．)）]\s*|[【〖]\s*){1,3}")


# 每道题都以 ① 题面图 起头——这才是真正的逐题边界（比 [#id] 标签更可靠，未标号的额外题也切得开）。
_STEP1_HEADING_RE = r"(?m)^\s{0,3}①\s*题面图" + _STEP_DELIM


def _split_questions(text):
    """按 ① 题面图 步骤标题切逐题片段（QR_v）；没有 ① 时退回 [#id]；都没有则整体一段。"""
    text = text or ""
    bounds = [m.start() for m in re.finditer(_STEP1_HEADING_RE, text)]
    if not bounds:
        bounds = [m.start() for m in re.finditer(r"\[#[^\]\n]+\]", text)]
    if len(bounds) <= 1:
        return [text]
    return [text[bounds[i]:(bounds[i + 1] if i + 1 < len(bounds) else len(text))]
            for i in range(len(bounds))]


def _question_tag_step1_consistent(text):
    """每个带 [#id] 标签的题都必须有自己的 ① 题面图块；标签数 > ① 块数 = 有题整块缺失（QR_v）。"""
    text = text or ""
    tags = len(re.findall(r"\[#[^\]\n]+\]", text))
    step1s = len(re.findall(_STEP1_HEADING_RE, text))
    return tags <= step1s


def _seven_step_heads(text):
    """按「期望圆圈序号 + 块名 + 终止符」定位七步标题，返回每步 (起, 止)；任一步缺失返回 None。"""
    heads = []
    for marker, variants in _SEVEN_STEPS:
        found = None
        for v in variants:
            m = re.search(r"(?m)^\s{0,3}" + marker + r"\s*" + re.escape(v) + _STEP_DELIM, text)
            if m:
                found = (m.start(), m.end())
                break
        if not found:
            return None
        heads.append(found)
    return heads


def _one_question_template_ok(seg):
    heads = _seven_step_heads(seg)
    if heads is None:                       # 缺步（如跳过 ②）
        return False
    starts = [h[0] for h in heads]
    if starts != sorted(starts):            # 必须 ①→⑦ 物理顺序出现（抓公式先行）
        return False
    for s, e in heads:                      # 每步标题下必须有真实正文（到下一步标题/文末为止）
        nxt = min([p for p in starts if p > s] + [len(seg)])
        if len(re.sub(r"\s+", "", seg[e:nxt])) < 2:
            return False
    # ⑦ 溯源检查限定在 ⑦ 自己的正文（切到其后的来源块行为止）——不能靠必然相邻的来源块行或
    # opt-in 收尾块里的 wiki/链接蒙混（Codex R1-F3）。
    tail = seg[heads[-1][1]:]
    sb = SOURCE_BLOCK_RE.search(tail)
    seven_body = tail[:sb.start()] if sb else tail
    # QR_2：来源块必须紧跟 ⑦（opt-in 收尾块只能在来源块之后）——⑦ 与来源块之间不得夹收尾块标题。
    if sb and any(_heading_present(seven_body, nm) for nm in OPTIONAL_CLOSER_NAMES):
        return False
    # QR_0：来源信息确实不明时，如实写「来源未知/来源页未知」即合规——诚实弃答不因缺 wiki 被判失败。
    if "来源未知" in seven_body or "来源页未知" in seven_body:
        return True
    # 否则（声称有出处）必须给出 wiki 路径 + 可点击原文页链接；裸「第 N 章」不算。
    if "references/wiki/" not in seven_body:
        return False
    return bool(re.search(r"\[[^\]]+\]\([^)]+\)", seven_body))


def teaching_template_ok(text):
    """A5 七步讲解模板：①-⑦ 按期望圆圈序号做真实标题、严格顺序、各带正文；⑦ 落到 wiki 路径 + 页链接
    （或如实来源未知）。多题响应（零基础/panic 逐题精讲）里**每一道题**（按 ① 题面图 切段，且带
    标签的题都要有自己的 ① 块）都必须各自满足——抓「首题齐全、后续题省略 ②/④/⑦」与未标号的额外
    坏题（Codex R2-HKO / R3-QR_v）。"""
    if not _question_tag_step1_consistent(text or ""):
        return False
    return all(_one_question_template_ok(s) for s in _split_questions(text or ""))


# 来源块单行：题目来源：…｜答案来源：…（全角｜或 ASCII | 都接受），末尾还需 canonical 标签。
SOURCE_BLOCK_RE = re.compile(r"(?m)^[^\n]*题目来源\s*[:：][^\n]*[｜|][^\n]*答案来源\s*[:：][^\n]*$")
_ANSWER_TITLE_RE = re.compile(_TT_PREFIX + r"(?:逐步演算|逐步代入|逐点展开|标准答案|参考答案|解析)[^\n]*")


# canonical 溯源标签（docs/language-policy.md 单一来源）；⚠ 归一化掉变体选择符 FE0F 再比对。
_CANONICAL_SOURCE_LABELS = ("🟢 来自资料",
                            "🟡 AI补充，可能与你老师讲的不完全一致",
                            "⚠️ AI生成答案，非老师/教材提供")


def _norm_warn(s):
    return (s or "").replace("\ufe0f", "")


def _one_source_block_ok(seg, ai_answer):
    m = SOURCE_BLOCK_RE.search(seg or "")
    if not m:
        return False
    label = _norm_warn(re.split(r"[｜|]", m.group(0))[-1].strip())
    canon = tuple(_norm_warn(c) for c in _CANONICAL_SOURCE_LABELS)
    # 允许 canonical 标签后带一个括号补充（🟢 来自资料（讲义 ch2）），但不允许任意自由文本尾巴
    # （🟢 来自资料 但这句我瞎编的）——去掉尾部括注后必须逐字等于某个 canonical 标签。
    core = re.sub(r"\s*[（(【\[].*$", "", label).strip()
    if core not in canon:
        return False
    if ai_answer:
        if core != canon[2]:
            return False
        # 答案/解析块标题必须含**完整** ⚠️ AI生成答案，非老师/教材提供 文本，不能只有 ⚠️ 图标（HKM）。
        title = _ANSWER_TITLE_RE.search(seg or "")
        warn = _norm_warn(_CANONICAL_SOURCE_LABELS[2])
        if not title or warn not in _norm_warn(title.group(0)):
            return False
    return True


def question_source_block_ok(text, ai_answer=False):
    """A5 每题固定来源块，**逐题**校验（HKO）：每个 [#id] 片段都要有一行 `题目来源：…｜答案来源：…｜
    <canonical 标签>`，尾标签去括注后逐字等于 canonical（抓「来源块缺失」「无/伪标签」，R1-F1）；
    ai_answer=True（无教材答案）时尾标签须为 ⚠️ canonical，且答案/解析块标题须含完整 ⚠️ 警告文本（HKM）。"""
    return all(_one_source_block_ok(s, ai_answer) for s in _split_questions(text or ""))


# 可选收尾块（易错点/3分钟速记/现在轮到你）——默认不许出现，仅学生要求/已存偏好时才输出。
OPTIONAL_CLOSER_NAMES = ("易错点", "3分钟速记", "三分钟速记", "现在轮到你")


def no_unsolicited_closing_blocks(text):
    """A5：默认输出到来源块为止——任何收尾块标题的出现都算「未经要求擅自附加」。
    本探测器不看上下文；学生真的要求了的场景在 dispatch 层豁免（不跑本检查）。"""
    return not any(_heading_present(text, name) for name in OPTIONAL_CLOSER_NAMES)


# ---- A6：时间宽裕度行为（≤1天紧迫节奏 / 窗口外知识点回问或实测）--------------------------
# ≤1天默认禁止开场澄清、偏好确认与反思式追问，但不禁止标准题库 drill/checkpoint。
# 只有学生明确设置 no_questions 时才是「一个互动问题都不许问」。底层问句探测仍识别两类信号：
# (1) 整段收尾即停下等学生（最后一句是问句）；(2) 中途出现明确要学生拿主意/许可/报状态/选择的问句。
# 自答式反问（你可能会问…？/问完立刻自答）与纯陈述句不算。DRIFT 侧 run_drift.py 有一份问句
# 识别副本，二者由 tests parity 锁一致。
_STUDENT_ASK_CUE = re.compile(
    r"你想|你要|你希望|你打算|你更|你觉得|你认为|你选|你决定|你来定|由你|你自己|你先|"
    r"你(?:还)?记得|你会(?:不会)?|你能(?:不能)?|你有(?:没有)?|你是(?:不是|否)|你(?:比较)?熟|"
    r"你复习到|你学到|要不要|想不想|请问|需要我|哪一?章|从哪|"
    r"(?:先讲|先复习|先看|先做|先学|讲|复习|看|做|来)[^，。？?！!\n]{0,8}还是|"
    r"需(?:不需)?要(?:我|先)|要(?:不要)?先|用不用(?:先|我)|该(?:先|不该)|哪个先|先哪|先(?:讲|复习|看|做|学|过)(?:什么|哪|谁)|"
    # 通用许可/回检/下一步问句（还有问题吗 / 可以吗 / 接下来怎么安排）——这些是元问句信号
    r"还有(?:什么|没有)?问题|有没有(?:什么)?问题|有问题吗|还有(?:不懂|不会|疑问)|哪里不(?:懂|会|清楚)|"
    r"可以吗|可不可以|行吗|行不行|好吗|好不好|方便吗|需要吗|要吗|"
    r"接下来(?:怎么|怎样|如何|想|要|需要)|下一步(?:怎么|想|要)|怎么安排|如何安排|怎么(?:样)?进行|"
    r"下列(?:哪|何)|哪一(?:项|种)|请(?:选择|判断|计算|回答|作答)|which of the following|choose|calculate|answer(?: this)?\b|"
    r"do you\b|would you\b|are you\b|have you\b|can you\b|which chapter\b|what.*\byou\b|"
    r"should i\b|shall i\b|want me to\b|which.*first\b|any questions\b",
    re.I)
# 自答式反问前缀（你/您可能会问…？——自问自答，不停下等学生）
_RHETORICAL_PREFACE = re.compile(
    r"[你您]可能(?:会)?问|[你您]也许(?:会)?问|[你您](?:有没有|是不是|会不会)想过|[你您]是不是(?:觉得|以为)|"
    r"[你您]也许(?:会)?好奇|[你您]可能(?:会)?好奇|试想|想象一下|不妨想")
# 紧接着的自答句（问完自己立刻给答案）
_SELF_ANSWER = re.compile(
    r"^(?:因为|答案|其实|这是因为|这(?:正)?是|正是|原因|答[:：]|because|the answer|it'?s because|"
    r"here'?s why)", re.I)


def asks_student_question(text):
    """是否向学生抛出了需其回答的**非反问**问句（会停下来等学生）。
    这是通用问句信号，不独立决定≤1天是否违规：经题库核验的 drill/checkpoint 由
    urgent_question_cadence_ok 豁免；明确 no_questions 时则任何互动问句都禁止。自答式反问
    （你可能会问…？/问完立刻自答）与纯陈述句不算。跨软换行、行内非行尾问号、中英皆识别。"""
    t = re.sub(r"[ \t]*\n[ \t]*", " ", text or "").strip()  # 软换行并为空格：跨行问句也能识别
    sents = [s.strip() for s in re.split(r"(?<=[？?。！!；;])\s*", t) if s.strip()]
    if not sents:
        return False
    # (1) 收尾即停下等学生：最后一句是问句且非反问前缀 → 违约（不需要白名单 cue）
    last = sents[-1]
    if (last.endswith("？") or last.endswith("?")) and not _RHETORICAL_PREFACE.search(last):
        return True
    # (2) 中途的明确元问句（让学生拿主意/许可/报状态/选择），排除反问与紧接自答
    for i, s in enumerate(sents):
        if not (s.endswith("？") or s.endswith("?")):
            continue
        if _RHETORICAL_PREFACE.search(s):
            continue
        nxt = sents[i + 1] if i + 1 < len(sents) else ""
        if _SELF_ANSWER.match(nxt):
            continue
        if _STUDENT_ASK_CUE.search(s):
            return True
    return False


# ---- A8b：语言并入合并首问（模式 × 时间 × 语言，一次问、一条 set 立三样） ----
_LANG_ASK_RE = re.compile(r"语言\s*/\s*Language：中文\s*/\s*English\s*/\s*双语")
_SET_LINE_RE = re.compile(r"update_progress\.py[^\n]*\bset\b[^\n]*")

# v4：set 命令旗标是机器面——中文显示词与 canonical 代号两代词汇都合法（词表同源 scripts/i18n.py，
# 缺文件时用等价兜底映射，不让冒烟崩）。学生可见文本的探测（首问三模式等）仍是 zh-only 口径不变。
try:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from i18n import canon_mode as _i18n_mode, canon_tier as _i18n_tier, canon_language as _i18n_lang

    def _flag_mode(v):
        return _i18n_mode(v or "")[0]

    def _flag_tier(v):
        return _i18n_tier(v or "")[0]

    def _flag_lang(v):
        return _i18n_lang(v or "")[0]
except Exception:
    _M_ZH = {"零基础从头讲": "from_scratch", "某章起步补弱": "shore_up", "查缺补漏": "fill_gaps"}
    _T_ZH = {"≤1天": "le1d", "1-3天": "d1_3", "3-7天": "d3_7", ">7天": "gt7d"}
    _L_ZH = {"中文": "zh", "English": "en", "双语": "bilingual"}

    def _flag_mode(v):
        v = (v or "").strip()
        return _M_ZH.get(v, v)

    def _flag_tier(v):
        v = (v or "").strip()
        return _T_ZH.get(v, v)

    def _flag_lang(v):
        v = (v or "").strip()
        return _L_ZH.get(v, v)


def _set_flags(text):
    """最后一条 set 行的 --mode/--time-budget/--language 旗标值（**顺序无关**）。"""
    flags = {}
    for m in _SET_LINE_RE.finditer(text or ""):
        cur = {}
        for fm in re.finditer(r"--(mode|time-budget|language)[=\s]\s*\"?([^\s\"]+)", m.group(0)):
            cur[fm.group(1)] = fm.group(2)
        if cur:
            flags = cur
    return flags


def language_first_ask_ok(text):
    """首问回合契约：**完整**合并首问——三语语言行 + 三个模式选项 + 四个时间档选项都在场
    （set 发生在学生答复之后——由 language_persist_ok 另测）。"""
    t = text or ""
    if not _LANG_ASK_RE.search(t):
        return False
    # 每个模式选项须带英文 gloss（同行括注、以拉丁字母开头）——语言未定前英文学生也能读懂
    for m in ("零基础从头讲", "某章起步补弱", "查缺补漏"):
        if not re.search(re.escape(m) + r"[^\n]{0,8}[（(]\s*[A-Za-z]", t):
            return False
    # 四个时间档同样各须 gloss（只 gloss 一个档骗不过）
    for x in ("≤1天", "1-3天", "3-7天", ">7天"):
        if not re.search(re.escape(x) + r"[^\n]{0,6}[（(]\s*[<≤>A-Za-z0-9]", t):
            return False
    return True


def language_persist_ok(text, urgent=False, expected_lang=None):
    """持久化契约：一条 set 同时带全三旗标（顺序无关）且 --language ∈ canonical。
    urgent=紧迫开场变体：另须零开场澄清/偏好问句 + 默认 零基础从头讲/≤1天 + 语言 ∈ {中文, English}
    （双语只能显式选择，**绝不静默推断**）；expected_lang=按开场语言的推断期望——
    中文开场静默持久化 English 也算违约（契约：推断学生开场所用语言）。"""
    f = _set_flags(text)
    if not all(k in f for k in ("mode", "time-budget", "language")):
        return False
    if _flag_mode(f["mode"]) not in ("from_scratch", "shore_up", "fill_gaps"):
        return False                                    # mode 也须 canonical（随便讲讲 骗不过；代号/中文皆可）
    if _flag_tier(f["time-budget"]) not in ("le1d", "d1_3", "d3_7", "gt7d"):
        return False                                    # tier 同理（someday 骗不过）
    if urgent:
        if not urgent_no_student_questions_ok(text):
            return False
        if not (_flag_mode(f["mode"]) == "from_scratch" and _flag_tier(f["time-budget"]) == "le1d"):
            return False
        # 显式双语开场 = 显式选择而非推断 → 放行；但必须是**肯定式**请求——
        # 「不要双语，直接中文讲」里的 双语 是否定式提及，不算同意（否定词贴邻前缀即拒）
        first_set = _SET_LINE_RE.search(text or "")
        pre = (text or "")[:first_set.start()] if first_set else ""
        explicit_bi = False
        for bm in re.finditer("双语", pre):
            ctx = pre[max(0, bm.start() - 4):bm.start()]
            if not any(neg in ctx for neg in ("不要", "别", "不用", "无需", "非")):
                explicit_bi = True
                break
        if _flag_lang(f["language"]) == "bilingual":
            return explicit_bi
        if _flag_lang(f["language"]) not in ("zh", "en"):
            return False
        return expected_lang is None or _flag_lang(f["language"]) == _flag_lang(expected_lang)
    return _flag_lang(f["language"]) in ("zh", "en", "bilingual")


def urgent_question_cadence_ok(text, fixture_path=None, no_questions=False):
    """≤1天节奏门禁：禁止澄清/偏好/反思式追问，但允许真实标准题库 checkpoint。

    题库豁免必须同时满足 [#id] 真实存在且题面与 fixture 内 quiz_bank.json 匹配；仅贴伪 ID，
    或在题库题后夹带「还有问题吗」，都不会被豁免。学生显式要求 no_questions 时，
    no_questions=True 直接禁止所有互动问句。
    """
    if not asks_student_question(text):
        return True
    if no_questions or not fixture_path:
        return False
    try:
        bank = load_quiz_bank_map(fixture_path)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    ids = extract_question_ids(text)
    question_map = {i: v["question"] for i, v in bank.items()}
    if not ids or not assert_quiz_ids_in_bank(text, question_map):
        return False

    # 只移除经核验的题库题面及 ID，再检查剩余文本；有真题不能掩盖后续偏好追问。
    # 题面中间的软换行允许不同，避免格式化换行误伤合法 checkpoint。
    remainder = text or ""
    for qid in dict.fromkeys(ids):
        question = str(bank[qid].get("question") or "")
        if question:
            flexible = r"\s*".join(re.escape(ch) for ch in question)
            remainder = re.sub(flexible, "", remainder, count=1)
        remainder = re.sub(r"\[#" + re.escape(qid) + r"\]", "", remainder)
    return not asks_student_question(remainder)


def urgent_no_student_questions_ok(text):
    """显式 no_questions 的向后兼容检查：不允许任何互动问句。"""
    return urgent_question_cadence_ok(text, no_questions=True)


# 回想类复核（问学生是否记得）与出题实测类复核分开——>7天 档契约要求用难题实测，不能只问「还记得吗」。
_RECALL_CUE = re.compile(
    r"还记得|是否(?:还)?记得|记不记得|复述一?[遍下]|能不能(?:说|讲|复述|做出|想起)|说说看|想一想.*是什么|"
    r"考考你|考考自己")
_TEST_CUE = re.compile(
    r"先做一道题|来一道题|做(?:一)?道题|做题(?:验证|检验|实测)?|实测一?下?|测一?测|测一?下|"
    r"用.{0,8}(?:难?题|例题).{0,6}(?:实测|检验|验证|考|试)|出(?:一)?道(?:难?题|题)")
_WINDOW_RECHECK_CUE = re.compile(_RECALL_CUE.pattern + "|" + _TEST_CUE.pattern)
# 复核所在**分句**里若含拒绝/否定/反事实词，视为没真的复核。「会不会/行不行」里的「不会」不算拒绝
# （那是复核问句本身），故 不会 用后视排除 会/还/行。此外把「否定式的发问/实测」（不问/不实测/不测…）
# 也算拒绝——「我不问你还记得吗，直接用」不是复核。
_WINDOW_REFUSAL = re.compile(
    r"(?<![会还行])不(?:会|做|想|去|再|打算|出)|不(?:问|实测|测|考|检验|核对?)|"
    r"不用|无需|无须|不需要|没(?:有)?(?:先|去|怎么)|"
    r"别(?:再|又|去)|懒得|就当你|默认你|不再|本来该|本应|原本(?:该|想)")
# 整份回答最终"默认还会/直接当会了"的收口——无论前面问没问都算没真复核（先确认…但我就当你会了）。
_WINDOW_DEFAULT_OUT = re.compile(r"(?:就当|默认|直接当|当)你(?:已经|都|应该)?(?:会|懂|记得|掌握|没问题|行|OK)")


def _has_unconditional_default(t):
    """整份回答里是否有**未被否定的**「默认还会」收口——「不会默认你会」这类否定式安全声明不算。"""
    for m in _WINDOW_DEFAULT_OUT.finditer(t or ""):
        pre = (t or "")[max(0, m.start() - 5):m.start()]
        if not re.search(r"[不别没绝]|无需|无须", pre):
            return True
    return False


def window_out_rechecked(text, require_test=False):
    """遇到「窗口外」的知识点必须真的复核，不能默认还会。检测：既点明窗口外语境，又在某个**未被否定
    的分句**里做了真实复核。`require_test=True`（>7天 档）时**只认出题实测**——只问「还记得吗」不算，
    契约要求用对应难题实测；`require_test=False`（3-7天 档）时回想发问或出题实测都算。
    只说「先确认一下」不发问、或末尾「我就当你会了」默认收口，一律不算真复核；但「不会默认你会」这类
    否定式安全声明不误伤。"""
    t = text or ""
    if not re.search(r"窗口外|上次(?:讲过|学过)了?有?一?阵|好一?阵子没", t):
        return False
    if _has_unconditional_default(t):               # 最终（未被否定地）默认还会 → 前面问了也白问
        return False
    cue = _TEST_CUE if require_test else _WINDOW_RECHECK_CUE
    for c in re.split(r"[。！？!?；;，,、\n]", t):
        if cue.search(c) and not _WINDOW_REFUSAL.search(c):
            return True
    return False


# ---- v4 红线：§2.4 笔记本落盘回执 / §2.5 建区必确认 --------------------------------------------
# §2.4 教学回合缺省 Output Contract =「先落盘、再在聊天里给摘要+链接」。回执两件套缺一不可：
# (1) notebook.py add-entry 落盘命令证据（code-span 里也认——只看行内容、不看包裹符，
#     镜像 _SET_LINE_RE 对 set 命令行的锚定方式）；
# (2) 学生可见的 notebook/chNN.md#锚点 链接（zh canonical 回执形如 完整解答：notebook/ch02.md#q13）。
# 若命令带 --chapter，则至少一条回执链接的章号须与某条命令的零填充章号一致——防「命令写 --chapter 3、
# 回执却指向没写过的 ch02」的假回执。
_NOTEBOOK_CMD_RE = re.compile(r"notebook\.py[^\n]*\badd-entry\b[^\n]*")
_NOTEBOOK_LINK_RE = re.compile(r"notebook/ch(\d{2})\.md#[^\s`)）\]｜|」』>]+")


def notebook_persist_receipt_ok(text):
    """教学回合是否带完整落盘回执：add-entry 命令 + notebook/chNN.md# 学生可见锚点链接（章号一致）。
    全程只在聊天里讲（无命令，或有命令却没给学生可点的锚点链接）→ False（v4 §2.4 红线：
    chat-only 教学 = 违约，学生一关窗口内容就蒸发）。"""
    t = text or ""
    cmds = _NOTEBOOK_CMD_RE.findall(t)
    if not cmds:
        return False
    links = _NOTEBOOK_LINK_RE.findall(t)
    if not links:
        return False
    chapters = set()
    for c in cmds:
        m = re.search(r"--chapter[=\s]+\"?(\d+)", c)
        if m:
            chapters.add("%02d" % int(m.group(1)))
    if chapters and not (set(links) & chapters):
        return False
    return True


# §2.5 建区必确认：任何工作区创建（ingest.py --output-dir / update_progress.py workspace-register）
# 之前，必须先有辅导方的落点确认问句（工作区/复习库 × 建在/路径/位置 × ？）、再有学生的肯定答复；
# 静默创建 = 违约（进红线场景）。transcript 以行首「学生：/辅导：」等说话人标记切回合。
_WS_CREATE_RE = re.compile(r"ingest\.py[^\n]*--output-dir|workspace-register")
_WS_SPEAKER_RE = re.compile(
    r"(?m)^\s*(学生|用户|同学|Student|User|辅导|助教|教练|老师|Assistant|Coach|Tutor)\s*[:：]")
_WS_USER_SPEAKERS = ("学生", "用户", "同学", "Student", "User")
_WS_ASK_CUE = re.compile(r"工作区|复习库|资料库|知识库|workspace", re.I)
_WS_PLACE_CUE = re.compile(r"建在|放在|落在|存到|路径|目录|位置|落点|哪")
_WS_ASSENT_RE = re.compile(r"可以|好|就(?:建|放|存|这)|没问题|同意|确认|OK|嗯|行|对", re.I)
_WS_DISSENT_RE = re.compile(
    r"不(?:行|要|可以|同意|好|对)|别(?:建|放|在)|换(?:个|一个|到|位置|地方)|先不|等等|再想想")


def _split_speaker_turns(text):
    """按行首「说话人：」标记把 transcript 切成 (speaker, body) 回合序列。"""
    t = text or ""
    marks = list(_WS_SPEAKER_RE.finditer(t))
    return [(m.group(1), t[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(t))])
            for i, m in enumerate(marks)]


def workspace_target_confirmed_ok(text):
    """工作区创建是否「先问后建」：第一个创建调用之前，先有辅导方落点确认问句、再有学生肯定答复
    （答复分句里含拒绝/换址词不算同意）。没有创建调用也返回 False——本场景断言的是「确认过的创建」，
    防止空转 transcript 混绿。问了不等答复就建、先建后事后追认、学生拒绝后仍建，一律 False
    （v4 §2.5 静默建区红线）。"""
    t = text or ""
    m = _WS_CREATE_RE.search(t)
    if not m:
        return False
    ask_seen = False
    for spk, body in _split_speaker_turns(t[:m.start()]):
        if spk in _WS_USER_SPEAKERS:
            if ask_seen and _WS_ASSENT_RE.search(body) and not _WS_DISSENT_RE.search(body):
                return True
        elif not ask_seen and re.search(r"[？?]", body) \
                and _WS_ASK_CUE.search(body) and _WS_PLACE_CUE.search(body):
            ask_seen = True
    return False


def visual_first_asset_display_ok(text, fixture_path=FIXTURE):
    """Smoke-check a visual-required output contract.

    This is structural, not a UI renderer: it requires a labelled question-side Markdown image
    before any prose/prompt/explanation/hint/answer text, rejects non-question images anywhere in
    the prompt block, and rejects path-only or unsafe image targets.
    """
    t = text or ""
    if re.search(r"[/\\][A-Za-z]:", t):
        return False

    question_asset_roles = {"question_context", "figure", "diagram", "table"}
    answer_asset_roles = {"answer_context", "worked_solution"}

    def normalized_safe_asset_target(target):
        p = (target or "").strip()
        if not p or p.startswith(("/", "\\")):
            return None
        if "://" in p or re.match(r"(?i)^[a-z][a-z0-9+.-]*:", p):
            return None
        if any(part == ".." for part in re.split(r"[\\/]+", p)):
            return None
        if not p.startswith("references/assets/"):
            return None
        abs_fixture = os.path.abspath(fixture_path)
        abs_target = os.path.abspath(os.path.join(abs_fixture, *re.split(r"[\\/]+", p)))
        if not (abs_target == abs_fixture or abs_target.startswith(abs_fixture + os.sep)):
            return None
        if not (os.path.isfile(abs_target) and os.access(abs_target, os.R_OK)):
            return None
        return "/".join(re.split(r"[\\/]+", p))

    def expected_assets_for_output():
        ids = extract_question_ids(t)
        if not ids:
            return None, None
        try:
            bank = json.loads(_read(os.path.join(fixture_path, "references", "quiz_bank.json")))
        except Exception:
            return None, None

        by_id = {str(q.get("id")): q for q in bank if isinstance(q, dict) and q.get("id") is not None}
        question_paths, answer_paths = set(), set()
        for qid in dict.fromkeys(ids):
            item = by_id.get(str(qid))
            if item is None:
                return None, None
            for asset in item.get("assets", []) or []:
                if not isinstance(asset, dict):
                    continue
                path = asset.get("path")
                if not isinstance(path, str):
                    continue
                normalized = "/".join(re.split(r"[\\/]+", path.strip()))
                role = asset.get("role")
                if role in question_asset_roles:
                    question_paths.add(normalized)
                elif role in answer_asset_roles:
                    answer_paths.add(normalized)
        return question_paths, answer_paths

    image_re = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    question_side_label = "\u9898\u9762\u56fe / question-side asset"
    answer_side_label = "\u7b54\u6848\u56fe / answer-side asset"

    def prompt_alt_ok(alt):
        # C2b：zh 纯形「题面图」与旧双语复合形都算合格题面侧 ALT（单语言纯净后 zh 输出用纯形）
        alt = alt or ""
        lower_alt = alt.lower()
        return (
            ("题面图" in alt or "question-side asset" in lower_alt)
            and answer_side_label not in alt
            and "answer-side asset" not in lower_alt
            and "worked solution" not in lower_alt
            and "\u7b54\u6848\u56fe" not in alt
        )

    def answer_alt_ok(alt):
        alt = alt or ""
        lower_alt = alt.lower()
        return (
            "题面图" not in alt
            and "question-side asset" not in lower_alt
            and (
                "答案图" in alt
                or "answer-side asset" in lower_alt
                or "worked solution" in lower_alt
            )
        )

    expected_question_assets, expected_answer_assets = expected_assets_for_output()
    if not expected_question_assets:
        return False

    marker_patterns = (
        "(^|\\n)\\s*" + "\u9898\u76ee",
        "(^|\\n)\\s*" + "\u95ee\u9898[:\uff1a]",
        "(^|\\n)\\s*" + "\u8bf7\u4f5c\u7b54",
        "(^|\\n)\\s*" + "\u8bf7\u56de\u7b54",
        "(^|\\n)\\s*" + "\u8bf7\u5148\u56de\u7b54",
        "(^|\\n)\\s*" + "\u4f5c\u7b54",
        "(^|\\n)\\s*" + "\u63d0\u793a",
        "(^|\\n)\\s*" + "\u89e3\u6790",
        "(^|\\n)\\s*" + "\u7b54\u6848[:\uff1a]",
        r"(^|\n)\s*Question:",
        r"(^|\n)\s*Hint:",
        r"(^|\n)\s*Explanation:",
        r"(^|\n)\s*Answer:",
    )
    positions = [m.start() for pat in marker_patterns for m in [re.search(pat, t, flags=re.MULTILINE)] if m]
    if not positions:
        return False
    first_action = min(positions)

    solution_marker_patterns = (
        "(^|\\n)\\s*" + "\u89e3\u6790",
        "(^|\\n)\\s*" + "\u7b54\u6848[:\uff1a]",
        r"(^|\n)\s*Explanation:",
        r"(^|\n)\s*Answer:",
    )
    solution_positions = [
        m.start()
        for pat in solution_marker_patterns
        for m in [re.search(pat, t, flags=re.MULTILINE)]
        if m and m.start() >= first_action
    ]
    solution_start = min(solution_positions, default=None)

    images = list(image_re.finditer(t))
    displayed_question_assets = set()

    for img in images:
        target = normalized_safe_asset_target(img.group(2))
        if target is None:
            return False
        if img.start() < first_action:
            if not prompt_alt_ok(img.group(1)) or target not in expected_question_assets:
                return False
            displayed_question_assets.add(target)
            continue
        if solution_start is None or img.start() < solution_start:
            return False
        if not answer_alt_ok(img.group(1)) or target not in expected_answer_assets:
            return False

    if not expected_question_assets.issubset(displayed_question_assets):
        return False
    qpos = min(img.start() for img in images if img.start() < first_action)
    if t[:qpos].strip():
        return False

    pre_action_without_images = image_re.sub("", t[:first_action]).strip()
    if pre_action_without_images:
        return False

    return qpos < first_action


def has_hint_skip_offer(text):
    t = (text or "")
    tl = t.lower()
    has_hint = ("提示" in t) or ("hint" in tl)
    has_skip = ("跳过" in t) or ("skip" in tl)
    has_archive = ("错题本" in t) or ("错题档案" in t) or ("归档" in t)
    # reject explicit DENIAL of any escape-hatch option — negation BEFORE the verb/noun
    # ("不会…记录进错题档案") OR AFTER the noun ("错题本暂不记录此题").
    negated = (bool(re.search(
                   r"(没有|不能|不会|不可以|不可|无法|不给|不予|不许|不准|拒绝)[^。\n]{0,10}?(提示|跳过|归档|错题本|错题档案)", t))
               or bool(re.search(r"(错题本|错题档案)[^。\n]{0,6}?(暂不|不记|不写|不归|未记|不予记|不会记|不加入)", t))
               # bare 不 + verb ("不归档" / "不写入" / "不让跳过") and 不把/不将 + verb ("不把…记录")
               or bool(re.search(r"不\s*(归档|写入|记入|记录|存入|加入|放入)|不\s*(给|让|许|准)\s*(提示|跳过)", t))
               or bool(re.search(r"不\s*(把|将)[^。\n]{0,8}?(归档|记录|记入|写入|存入|加入)", t)))
    return has_hint and has_skip and has_archive and not negated


def _section(text, header_keywords):
    """Lines under the first '## ' header containing ANY of header_keywords, until the next '## '."""
    if isinstance(header_keywords, str):
        header_keywords = [header_keywords]
    out, grab = [], False
    for ln in (text or "").splitlines():
        if ln.startswith("## "):
            grab = any(k in ln for k in header_keywords)
            continue
        if grab:
            out.append(ln)
    return "\n".join(out)


def _table_data_rows(section_text):
    """Markdown table data rows in a section (excludes header, separator, and non-table prose)."""
    rows = []
    seen_header = False
    for ln in section_text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):   # separator row ( | --- | --- | )
            continue
        if not seen_header:
            seen_header = True                          # first table row is the header
            continue
        # skip empty-state placeholder rows like `| 暂无错题 | - | - |` or all-blank/dash rows
        joined = "".join(cells)
        if (not joined
                or all(c in ("", "-", "—", "/", "无", "暂无", "空", "N/A", "n/a") for c in cells)
                or re.search(r"暂无|尚无|无记录|无错题|无疑难", joined)):
            continue
        rows.append(cells)
    return rows


def _row_matches(rows, expect):
    # token-boundary match (join cells with a separator) so `mc_q2` doesn't match `mc_q20`
    pat = re.compile(rf"(?<![0-9A-Za-z_]){re.escape(expect)}(?![0-9A-Za-z_])")
    return any(pat.search(" | ".join(r)) for r in rows)


_ARCHIVE_NEG = re.compile(r"未\s*归档|未\s*记录|未\s*存|未\s*加入|待\s*归档|待\s*记录|暂不|尚未")


def progress_has_mistake_archive(progress_text, expect=None):
    # standard template section is "## ❌ 错题档案记录"; mini/legacy wording uses "错题本" — accept both.
    # a row must match `expect` (if given) AND not be marked 未归档/待归档 (i.e. actually archived).
    rows = _table_data_rows(_section(progress_text, ["错题档案", "错题本"]))

    def archived(r):
        joined = " | ".join(r)
        if expect and not re.search(rf"(?<![0-9A-Za-z_]){re.escape(expect)}(?![0-9A-Za-z_])", joined):
            return False
        return not _ARCHIVE_NEG.search(joined)

    return any(archived(r) for r in rows)


def progress_has_confusion_row(progress_text, expect=None):
    rows = _table_data_rows(_section(progress_text, ["疑难", "confusion"]))
    if not rows:
        return False
    return _row_matches(rows, expect) if expect else True


def progress_current_phase(progress_text):
    # anchor to the CURRENT-phase marker (当前阶段 / 当前进行阶段) so a "已完成：阶段 1" line listed
    # BEFORE the active checkpoint can't be misread as the current phase.
    m = re.search(r"当前(?:进行)?阶段[^#\n]{0,8}?(\d+)", progress_text or "")
    return int(m.group(1)) if m else None


# restart-at-1 / from-scratch language a resume message must NOT contain.
# NB: no \b after the digit — between a digit and a CJK char there is no word boundary, so
# "从阶段1开始" (no space) would otherwise slip; (?!\d) guards against matching 阶段 10/11.
_RESTART_RE = re.compile(
    r"从\s*头\s*开始|从\s*阶段\s*1(?!\d)|从\s*第\s*1\s*阶段|重新\s*开始|重头\s*开始|从头(重新)?来")


def resume_refers_to_phase(resume_text, phase):
    """Resume must point at the CURRENT phase AND not restart at phase 1 / from scratch.

    Accepts spacing/word-order variants: 阶段 2 / 阶段2 / 第2阶段 / 第 2 阶段.
    """
    t = resume_text or ""
    mentions = bool(re.search(rf"阶段\s*{phase}(?!\d)|第\s*{phase}\s*阶段", t))
    # reject negation of the current phase, both 阶段2 and 第2阶段 forms
    negated = bool(re.search(rf"(?:不是|不在)\s*(?:第\s*{phase}\s*阶段|第?\s*阶段\s*{phase})", t))
    # reject restarting from a NON-current phase, both word orders: 阶段2 / 第2阶段
    rm = re.search(r"从\s*(?:第\s*(\d+)\s*阶段|第?\s*阶段\s*(\d+))\s*(?:阶段)?\s*(?:开始|起|做起|学起|重新|重头)", t)
    if rm:
        rn = rm.group(1) or rm.group(2)
        if rn != str(phase):
            return False
    return mentions and not negated and not _RESTART_RE.search(t)


def count_wiki_reads(transcript_text):
    """best-effort: count read_file events that touch references/wiki/*.md in a JSONL transcript."""
    n = 0
    for line in (transcript_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("tool") == "read_file" and "references/wiki/" in str(ev.get("path", "")):
            n += 1
    return n



def _state_row_written(fixture_path, sc, key, field, expect):
    """A4：fixture 是 state-backed 时，错题/疑难写入还必须落在 study_state.json（经官方工具）——
    只改生成视图 md 的回归不能再混绿。fixture 无 state（md-only 降级）则不作此断言。"""
    if not os.path.isfile(os.path.join(fixture_path, "study_state.json")):
        return True
    rel = sc.get(key)
    if not rel:
        return False                                   # state-backed 场景缺 state_after 快照即失败
    try:
        st = json.loads(_read(_p(rel)))
    except (OSError, ValueError):
        return False
    try:
        st0 = json.loads(_read(os.path.join(fixture_path, "study_state.json")))
    except (OSError, ValueError):
        return False
    if st.get("current_phase") != st0.get("current_phase"):
        return False        # 归档写入不许顺带毁断点——checkpoint 退位即失败
    rows = st.get(field) or []
    # add-mistake --id <qid> 把 qid 存在 id 字段、note 只写错因——按 id 或 note 任一匹配
    return any(isinstance(r, dict) and ((expect or "") in (r.get("note") or "")
                                        or (expect or "") == (r.get("id") or ""))
               for r in rows)


def validate_fixture_workspace(path):
    """Run the Tier-1 validator on a workspace. Returns (ok, errors, warnings, stats)."""
    spath = os.path.join(ROOT, "scripts")
    if spath not in sys.path:
        sys.path.insert(0, spath)
    import validate_workspace as V
    errors, warnings, stats = V.validate(path)
    return V._exit_code(errors) == 0, errors, warnings, stats


def artifact_mode_routing_ok(text, expected):
    """Deterministic command-trace oracle for the chat/visual artifact gate.

    The trace format is intentionally host-neutral: ordinary commands are literal, while a native
    host adapter records ``PDF_ADAPTER[native] -> study_guide/chNN.pdf``.  This checks routing and
    persisted preference semantics, not whether a particular host happens to expose a PDF tool.
    """
    value = text or ""
    lower = value.lower()
    lines = value.splitlines()

    def _positions(predicate):
        return [i for i, line in enumerate(lines) if predicate(line, line.lower())]

    set_visual = _positions(
        lambda line, low: "update_progress.py" in low and " set " in (" " + low + " ")
        and re.search(r"--artifact-mode\s+(?:visual|视觉教材|完整教材)", line, re.I)
    )
    set_chat = _positions(
        lambda line, low: "update_progress.py" in low and " set " in (" " + low + " ")
        and re.search(r"--artifact-mode\s+(?:chat|对话省额|只在对话)", line, re.I)
    )
    render = _positions(lambda _line, low: "study_guide_render.py" in low)
    html_render = _positions(
        lambda _line, low: "study_guide_render.py" in low and "--pdf" not in low
    )
    browser_pdf = _positions(
        lambda _line, low: "study_guide_render.py" in low and "--pdf" in low
    )
    native_pdf = _positions(
        lambda _line, low: "pdf_adapter[native]" in low
        and re.search(r"study_guide[/\\]ch\d+\.pdf", low) is not None
    )
    preflight = _positions(
        lambda _line, low: "check_deps.py" in low
        and "--workspace" in low and "--chapter" in low
        and "--artifact-mode visual" in low
        and re.search(r"--pdf-backend\s+(?:native|browser|html)", low) is not None
    )
    explicit_visual = _positions(
        lambda line, _low: re.search(
            r"学生\s*[:：].*(?:不在乎\s*token|token\s*不敏感|视觉教材|以后.*(?:pdf|打印版)|每章.*(?:pdf|打印版))",
            line, re.I,
        ) is not None
    )
    explicit_pdf = _positions(
        lambda line, _low: re.search(r"学生\s*[:：].*(?:这|本|当前).*(?:pdf|打印版)", line, re.I)
        is not None
    )
    state_chat_ok = _positions(
        lambda _line, low: "state_write=ok" in low and "artifact_mode=chat" in low
    )
    state_visual_ok = _positions(
        lambda _line, low: "state_write=ok" in low and "artifact_mode=visual" in low
    )
    preflight_ok = _positions(lambda _line, low: "preflight_exit=0" in low)
    html_ok = _positions(lambda _line, low: "html_exit=0" in low)
    pdf_ok = _positions(lambda _line, low: "pdf_exit=0" in low)
    failed = _positions(
        lambda _line, low: (
            "probe_error" in low
            or re.search(r"(?:preflight|html|pdf)_exit\s*=\s*(?:[1-9]\d*|error|fail)", low)
            is not None
            or re.search(r"state_write\s*=\s*(?!ok\b)\S+", low) is not None
            or re.search(r"missing_needed\s*=\s*\[\s*[^\]\s]", low) is not None
        )
    )
    if failed:
        return False

    if expected == "chat":
        return (bool(set_chat and state_chat_ok) and set_chat[0] < state_chat_ok[0]
                and not set_visual and not render and not preflight
                and "--pdf-backend" not in lower and "pdf_adapter[" not in lower)

    if expected == "visual":
        if not (set_visual and state_visual_ok and explicit_visual and preflight and preflight_ok
                and html_render and html_ok and pdf_ok):
            return False
        # The user's explicit standing choice must precede persistence; selected-backend preflight
        # precedes rendering so a missing content dependency is discovered before generation.
        if not (explicit_visual[0] < set_visual[0] < state_visual_ok[0]
                < preflight[0] < preflight_ok[0] < html_render[0] < html_ok[0]):
            return False
        chosen_line = lines[preflight[0]].lower()
        if "--pdf-backend native" in chosen_line:
            return bool(native_pdf and html_ok[0] < native_pdf[0] < pdf_ok[0]
                        and not browser_pdf)
        if "--pdf-backend browser" in chosen_line:
            return bool(browser_pdf and html_ok[0] < browser_pdf[0] < pdf_ok[0])
        return False

    if expected == "one_shot":
        state_markers = _positions(lambda _line, low: "artifact_mode=chat" in low)
        if not (len(state_markers) >= 2 and explicit_pdf and preflight and preflight_ok
                and html_render and html_ok and pdf_ok):
            return False
        pdf_action = native_pdf or browser_pdf
        return (not set_visual and not set_chat and bool(pdf_action)
                and state_markers[0] < explicit_pdf[0] < preflight[0] < preflight_ok[0]
                < html_render[0] < html_ok[0] < pdf_action[0] < pdf_ok[0]
                < state_markers[-1])

    return False


# ---------------- scenario runner (mock = deterministic) ----------------

def load_scenarios():
    return json.loads(_read(SCENARIOS))


def _p(rel):
    return os.path.join(HERE, rel)


def check_scenario_mock(name, sc, fixture_path=FIXTURE):
    """Return (ok, detail) for one scenario using only mock artifacts — no LLM."""
    if name == "quiz_bank_only":
        qmap = load_quiz_bank_map(fixture_path)
        ch = sc.get("chapter")
        in_scope = {i: v for i, v in qmap.items()
                    if ch is None or str(v.get("chapter")) == str(ch) or str(v.get("phase")) == str(ch)}
        scoped = {i: v["question"] for i, v in in_scope.items()}
        min_q = sc.get("min_questions", 1)
        good_txt = _read(_p(sc["mock_output"]))
        n_good = len(set(extract_question_ids(good_txt)))
        # R5 T1 (parity): run the SAME answer-leak guard live uses — the authored golden must not leak.
        no_leak = not _reply_leaks_answer_key(good_txt, [v.get("answer") for v in in_scope.values()])
        good = assert_quiz_ids_in_bank(good_txt, scoped) and n_good >= min_q and no_leak
        bad = assert_quiz_ids_in_bank(_read(_p(sc["mock_negative"])), scoped)
        return (good and not bad), (f"good={good} n={n_good}>={min_q} no_leak={no_leak} "
                                    f"invented/oos_caught={not bad} ch/phase={ch}")
    if name == "scope_override":
        # R5 T2: the good golden must declare the override AND serve a REAL bank item (content-matched),
        # so a valid-ID sticker on invented text can't pass — the SAME contract live enforces.
        # R5 T1 (parity): also run the answer-leak guard the live path uses.
        # R6 U2 (parity): content-match against the VISUAL subset only (the scenario asked for figure items).
        bank = load_quiz_bank_map(fixture_path)
        vmap = {i: v["question"] for i, v in bank.items() if v.get("visual")}
        good_txt = _read(_p(sc["mock_output"]))
        no_leak = not _reply_leaks_answer_key(good_txt, [v.get("answer") for v in bank.values()])
        good = (scope_override_declared(good_txt) and assert_quiz_ids_in_bank(good_txt, vmap) and no_leak)
        bad = scope_override_declared(_read(_p(sc["mock_negative"])))
        return (good and not bad), f"declared+visualmatch+no_leak={good} undeclared_caught={not bad}"
    if name == "provenance_labels":
        ok = has_canonical_provenance_labels(_read(_p(sc["mock_output"])))
        return ok, f"all_canonical_labels={ok}"
    if name == "hint_skip_mistake_archive":
        offer = has_hint_skip_offer(_read(_p(sc["mock_output"])))
        arch = progress_has_mistake_archive(_read(_p(sc["progress_after"])), sc.get("expect_archive"))
        state_ok = _state_row_written(fixture_path, sc, "state_after", "mistake_archive",
                                      sc.get("expect_archive"))
        return (offer and arch and state_ok), (f"hint_skip_offer={offer} mistake_archived={arch} "
                                               f"state_row={state_ok}")
    if name == "confusion_tracking":
        ok = progress_has_confusion_row(_read(_p(sc["mock_output"])), sc.get("expect_confusion"))
        state_ok = _state_row_written(fixture_path, sc, "state_after", "confusion_log",
                                      sc.get("expect_confusion"))
        return (ok and state_ok), f"confusion_row_written={ok} state_row={state_ok}"
    if name == "checkpoint_recovery":
        state_p = os.path.join(fixture_path, "study_state.json")
        if os.path.isfile(state_p):                    # A4: 结构化状态是唯一事实源，断点从 JSON 读
            ph = json.loads(_read(state_p)).get("current_phase")
        else:
            ph = progress_current_phase(_read(os.path.join(fixture_path, "study_progress.md")))
        resume = _read(_p(sc["mock_output"]))
        refers = resume_refers_to_phase(resume, sc["expected_phase"])
        return (ph == sc["expected_phase"] and refers), f"current_phase={ph} resume_refers_current={refers}"
    if name == "no_python_fallback":
        ok = validate_fixture_workspace(_p(sc["fallback_workspace"]))[0]
        return ok, f"hand_authored_workspace_valid={ok}"
    if name == "zero_basic_key_question":
        # A5 起零基础/panic 模式对每道重点题都走七步模板——好例须过 seven-step + 来源块 + 结构小节；
        # 旧的「考点拆解 + 标准答题步骤」两段式（无 ①-⑦）现在必须被判不合格（QR_8）。
        bank = load_quiz_bank_map(fixture_path)
        good_txt = _read(_p(sc["mock_output"]))
        # R6 U3 (parity): the good golden must tag a REAL bank id AND teach its topic (same as live).
        grounded = _served_bank_items(good_txt, fixture_path)[1] and _reply_teaches_bank_topic(good_txt, bank)
        good = (grounded and teaching_template_ok(good_txt) and question_source_block_ok(good_txt)
                and has_zero_basic_sections(good_txt))
        legacy_bad = teaching_template_ok(_read(_p(sc["mock_negative_legacy"])))
        return (good and not legacy_bad), (f"grounded={grounded} seven_step_good={good} "
                                           f"legacy_two_section_caught={not legacy_bad}")
    if name == "language_first_ask":
        # 首问回合：好例=三语语言行；反例漏语言行 → 被抓（首问回合不要求 set——还没等到学生答复）。
        # 持久化回合：好例=一条 set 三旗标（**乱序**也认）；反例 --language 非 canonical → 被抓。
        # 紧迫变体：静默三旗标 + 默认 零基础从头讲/≤1天 + 语言 ∈ {中文,English}；
        # 反例①静默推断 双语 → 被抓；反例②收尾仍提问 → 被抓。
        ask = language_first_ask_ok(_read(_p(sc["mock_output"])))
        ask_bad = language_first_ask_ok(_read(_p(sc["mock_negative"])))
        ask_unglossed = language_first_ask_ok(_read(_p(sc["mock_negative_unglossed"])))
        persist = language_persist_ok(_read(_p(sc["mock_persist"])))
        persist_bad = language_persist_ok(_read(_p(sc["mock_persist_negative"])))
        exp = sc.get("urgent_expected_language")
        urgent = language_persist_ok(_read(_p(sc["mock_urgent"])), urgent=True, expected_lang=exp)
        urgent_bi = language_persist_ok(_read(_p(sc["mock_urgent_bilingual"])), urgent=True, expected_lang=exp)
        urgent_bi_explicit = language_persist_ok(_read(_p(sc["mock_urgent_bilingual_explicit"])),
                                                 urgent=True, expected_lang=exp)
        urgent_bi_negated = language_persist_ok(_read(_p(sc["mock_urgent_bilingual_negated"])),
                                                urgent=True, expected_lang=exp)
        urgent_mm = language_persist_ok(_read(_p(sc["mock_urgent_wrong_language"])), urgent=True,
                                        expected_lang=exp)
        urgent_q = language_persist_ok(_read(_p(sc["mock_urgent_negative"])), urgent=True, expected_lang=exp)
        return (ask and not ask_bad and not ask_unglossed and persist and not persist_bad
                and urgent and not urgent_bi and urgent_bi_explicit and not urgent_bi_negated
                and not urgent_mm and not urgent_q), (
            f"ask_good={ask} missing_language_line_caught={not ask_bad} "
            f"unglossed_options_caught={not ask_unglossed} "
            f"persist_orderfree={persist} noncanonical_caught={not persist_bad} "
            f"urgent_defaults_ok={urgent} inferred_bilingual_caught={not urgent_bi} "
            f"explicit_bilingual_allowed={urgent_bi_explicit} "
            f"negated_bilingual_caught={not urgent_bi_negated} "
            f"opening_language_mismatch_caught={not urgent_mm} urgent_question_caught={not urgent_q}")
    if name == "time_budget_no_questions":
        # 显式 no_questions：好例零互动问句、偏好问句被抓；普通≤1天仍允许真实题库 checkpoint。
        good = urgent_question_cadence_ok(_read(_p(sc["mock_output"])), fixture_path,
                                          no_questions=True)
        bad = urgent_question_cadence_ok(_read(_p(sc["mock_negative"])), fixture_path,
                                         no_questions=True)
        checkpoint = urgent_question_cadence_ok(_read(_p(sc["mock_bank_checkpoint"])), fixture_path)
        return (good and not bad and checkpoint), (
            f"explicit_no_questions_good={good} clarification_caught={not bad} "
            f"bank_checkpoint_allowed={checkpoint}")
    if name == "knowledge_window_recheck":
        # 3-7天：好例回问/实测都算；反例默认还会 → 被抓。
        good = window_out_rechecked(_read(_p(sc["mock_output"])))
        bad = window_out_rechecked(_read(_p(sc["mock_negative"])))
        # >7天：必须出题实测——好例出题过；只口头回问的反例在 require_test 下被抓（契约要求难题实测）。
        test_good = window_out_rechecked(_read(_p(sc["mock_test"])), require_test=True)
        recall_only = window_out_rechecked(_read(_p(sc["mock_negative_recall_only"])), require_test=True)
        return (good and not bad and test_good and not recall_only), (
            f"rechecked_good={good} assumed_known_caught={not bad} "
            f"test_good={test_good} recall_only_in_over7d_caught={not recall_only}")
    if name == "teaching_template":
        good_txt = _read(_p(sc["mock_output"]))
        liberal_txt = _read(_p(sc["mock_liberal"]))
        ai_txt = _read(_p(sc["mock_ai_answer"]))
        no_source_txt = _read(_p(sc["mock_negative_no_source"]))
        # R6 U3 (parity): each POSITIVE golden must tag a REAL bank id AND teach its topic (same as live).
        bank = load_quiz_bank_map(fixture_path)
        def _grounded(txt):
            return _served_bank_items(txt, fixture_path)[1] and _reply_teaches_bank_topic(txt, bank)
        good = _grounded(good_txt) and teaching_template_ok(good_txt) and question_source_block_ok(good_txt)
        liberal = (_grounded(liberal_txt) and teaching_template_ok(liberal_txt)
                   and question_source_block_ok(liberal_txt))
        ai_good = (_grounded(ai_txt) and teaching_template_ok(ai_txt)
                   and question_source_block_ok(ai_txt, ai_answer=True))
        skip_ask = teaching_template_ok(_read(_p(sc["mock_negative_skip_ask"])))
        formula_first = teaching_template_ok(_read(_p(sc["mock_negative_formula_first"])))
        # 来源块缺失的反例：七步模板本身合格（证明两只探测器职责正交），只有来源块探测器抓它
        no_source_tpl = teaching_template_ok(no_source_txt)
        no_source = question_source_block_ok(no_source_txt)
        unlabeled = question_source_block_ok(_read(_p(sc["mock_negative_unlabeled_source"])))
        warn_line = question_source_block_ok(_read(_p(sc["mock_negative_missing_warn"])), ai_answer=True)
        warn_title = question_source_block_ok(_read(_p(sc["mock_negative_warn_title"])), ai_answer=True)
        # 收尾块契约：好例默认不带收尾块；未经要求擅自附加的反例要被抓；
        # 学生明确要求的 opt-in 例外只查七步+来源块（收尾块检查豁免）。
        good_closer_free = (no_unsolicited_closing_blocks(good_txt)
                            and no_unsolicited_closing_blocks(liberal_txt)
                            and no_unsolicited_closing_blocks(ai_txt))
        unsolicited = no_unsolicited_closing_blocks(_read(_p(sc["mock_negative_unsolicited_closers"])))
        optin_txt = _read(_p(sc["mock_optin_closers"]))
        optin_ok = teaching_template_ok(optin_txt) and question_source_block_ok(optin_txt)
        return (good and liberal and ai_good and not skip_ask and not formula_first
                and no_source_tpl and not no_source and not unlabeled
                and not warn_line and not warn_title
                and good_closer_free and not unsolicited and optin_ok), (
            f"good={good} liberal={liberal} ai_good={ai_good} skip_ask_caught={not skip_ask} "
            f"formula_first_caught={not formula_first} no_source_caught={not no_source} "
            f"(tpl_still_ok={no_source_tpl}) unlabeled_caught={not unlabeled} "
            f"warn_line_caught={not warn_line} warn_title_caught={not warn_title} "
            f"good_closer_free={good_closer_free} unsolicited_closers_caught={not unsolicited} "
            f"optin_closers_ok={optin_ok}")
    if name == "visual_first_assets":
        good = visual_first_asset_display_ok(_read(_p(sc["mock_output"])), fixture_path)
        answer_first = visual_first_asset_display_ok(_read(_p(sc["mock_negative"])), fixture_path)
        answer_before_prompt = visual_first_asset_display_ok(_read(_p(sc["mock_negative_leak"])), fixture_path)
        unlabeled_answer = visual_first_asset_display_ok(_read(_p(sc["mock_negative_unlabeled"])), fixture_path)
        prose_before = visual_first_asset_display_ok(_read(_p(sc["mock_negative_prose"])), fixture_path)
        answer_after_prompt = visual_first_asset_display_ok(_read(_p(sc["mock_negative_after_prompt"])), fixture_path)
        unsafe_path = visual_first_asset_display_ok(_read(_p(sc["mock_negative_unsafe_path"])), fixture_path)
        question_label_late = visual_first_asset_display_ok(
            _read(_p(sc["mock_negative_question_label_late"])), fixture_path)
        missing_asset = visual_first_asset_display_ok(_read(_p(sc["mock_negative_missing_asset"])), fixture_path)
        answer_text = visual_first_asset_display_ok(_read(_p(sc["mock_negative_answer_text"])), fixture_path)
        path_only = visual_first_asset_display_ok(_read(_p(sc["mock_negative_path"])), fixture_path)
        return (
            good and not answer_first and not answer_before_prompt and not unlabeled_answer
            and not prose_before and not answer_after_prompt and not unsafe_path
            and not question_label_late and not missing_asset and not answer_text and not path_only
        ), (
            f"good={good} answer_side_first_caught={not answer_first} "
            f"answer_before_prompt_caught={not answer_before_prompt} "
            f"unlabeled_answer_caught={not unlabeled_answer} prose_before_caught={not prose_before} "
            f"answer_after_prompt_caught={not answer_after_prompt} unsafe_path_caught={not unsafe_path} "
            f"question_label_late_caught={not question_label_late} missing_asset_caught={not missing_asset} "
            f"answer_text_caught={not answer_text} "
            f"path_only_caught={not path_only}")
    if name == "notebook_persist_ok":
        # v4 §2.4 红线：教学回合必须「先落盘、再摘要」——好例同时带 notebook.py add-entry 落盘命令
        # 与学生可见的 notebook/chNN.md# 锚点回执；反例全程只在聊天里讲、零落盘 → 被抓。
        good = notebook_persist_receipt_ok(_read(_p(sc["mock_output"])))
        bad = notebook_persist_receipt_ok(_read(_p(sc["mock_negative"])))
        return (good and not bad), f"persist_receipt_good={good} chat_only_caught={not bad}"
    if name == "workspace_confirm_ok":
        # v4 §2.5 红线：建区必确认——第一个创建调用（ingest --output-dir / workspace-register）之前
        # 须有落点确认问句 + 学生肯定答复；开场直接 ingest --output-dir 静默建区的反例 → 被抓。
        good = workspace_target_confirmed_ok(_read(_p(sc["mock_output"])))
        bad = workspace_target_confirmed_ok(_read(_p(sc["mock_negative"])))
        return (good and not bad), f"confirm_before_create_good={good} silent_create_caught={not bad}"
    if name == "artifact_mode_routing":
        chat = artifact_mode_routing_ok(_read(_p(sc["mock_chat"])), "chat")
        chat_bad = artifact_mode_routing_ok(_read(_p(sc["mock_chat_negative"])), "chat")
        chat_unpersisted = artifact_mode_routing_ok(
            _read(_p(sc["mock_chat_not_persisted"])), "chat")
        visual = artifact_mode_routing_ok(_read(_p(sc["mock_visual"])), "visual")
        guessed = artifact_mode_routing_ok(_read(_p(sc["mock_visual_subscription_guess"])),
                                           "visual")
        preflight_failed = artifact_mode_routing_ok(
            _read(_p(sc["mock_visual_preflight_failed"])), "visual")
        one_shot = artifact_mode_routing_ok(_read(_p(sc["mock_one_shot"])), "one_shot")
        persisted = artifact_mode_routing_ok(_read(_p(sc["mock_one_shot_persisted"])),
                                             "one_shot")
        probe_error = artifact_mode_routing_ok(
            _read(_p(sc["mock_one_shot_probe_error"])), "one_shot")
        return (chat and not chat_bad and not chat_unpersisted and visual and not guessed
                and not preflight_failed and one_shot and not persisted and not probe_error), (
            f"chat_no_artifact={chat} chat_auto_pdf_caught={not chat_bad} "
            f"chat_unpersisted_caught={not chat_unpersisted} explicit_visual_routed={visual} "
            f"subscription_guess_caught={not guessed} "
            f"failed_preflight_caught={not preflight_failed} one_shot_keeps_chat={one_shot} "
            f"one_shot_persist_caught={not persisted} probe_error_caught={not probe_error}")
    return False, "unknown scenario"


def run_mock(verbose=True):
    spec = load_scenarios()
    fixture_path = os.path.join(HERE, spec.get("fixture", "fixtures/mini_course"))
    results = []   # (name, detail, status) where status ∈ {PASS, FAIL, SKIP}
    for sc in spec["scenarios"]:
        name = sc["name"]
        if sc.get("best_effort"):
            # best-effort scenarios are NOT asserted in deterministic mode — report as SKIP,
            # never as PASS, so the conclusion doesn't overstate what was verified.
            results.append((name, "best-effort（需 LLM/transcript，--mock 不断言）", "SKIP"))
            continue
        ok, detail = check_scenario_mock(name, sc, fixture_path)
        results.append((name, detail, "PASS" if ok else "FAIL"))
    n_pass = sum(1 for _, _, s in results if s == "PASS")
    n_skip = sum(1 for _, _, s in results if s == "SKIP")
    n_fail = sum(1 for _, _, s in results if s == "FAIL")
    if verbose:
        for name, detail, status in results:
            print(f"  [{status}] {name}: {detail}")
        print(f"  ({n_pass} passed, {n_skip} skipped[best-effort, 未断言], {n_fail} failed)")
    return n_fail == 0, results


def check_fixture(verbose=True):
    ok, errors, warnings, stats = validate_fixture_workspace(FIXTURE)
    # This committed mini-course is intentionally not a live learner session, so it has no
    # machine-bound lightweight receipt.  That one warning is expected; every content/schema
    # warning (keywords / diagram_type / code tests …) still fails the fixture loudly.
    unexpected_warnings = [
        row for row in warnings
        if "lightweight session has not been initialized" not in row.get("msg", "")
    ]
    clean = ok and not unexpected_warnings
    if verbose:
        print(f"fixture: {FIXTURE}")
        print(f"  valid={ok}  warnings={len(warnings)}  stats={stats}")
        for e in errors:
            print(f"  [error] {e['msg']}")
        for w in warnings:
            print(f"  [warning] {w['msg']}")
    return clean


DEFAULT_AGENT_CMD = "claude -p {prompt}"


# scenarios where the agent must PRODUCE a worked answer — only these get the standard answer in the
# prompt digest. Serve-only quiz scenarios (quiz_bank_only / scope_override) are deliberately EXCLUDED
# so the agent is never handed an answer key it could echo to the student (R5 T1: answer-leak source).
_ANSWER_DEPENDENT_SCENARIOS = {"zero_basic_key_question", "teaching_template"}


def _live_prompt(fixture_path, sc):
    """Build a single-turn prompt from the fixture (bank + plan + progress) plus the scenario's
    student turn. The reply is fed to each scenarios PRIMARY positive detector — the SAME functions --mock uses (mocks extra negative/variant assertions validate the detectors and are not re-run live)."""
    bank = json.loads(_read(os.path.join(fixture_path, "references", "quiz_bank.json")))
    answer_dependent = sc.get("name") in _ANSWER_DEPENDENT_SCENARIOS
    header = ("题库（只能从这里出题，出题必须带 [#题号]；判分以下面的标准答案为准）：" if answer_dependent
              else "题库（只能从这里出题，出题必须带 [#题号]；只发题面，不要泄露/展示标准答案给学生）：")
    lines = [header]
    for q in bank:
        if isinstance(q, dict) and q.get("id") is not None:
            e = "- [#%s] (阶段%s) %s" % (q["id"], q.get("phase", q.get("chapter", "?")),
                                        str(q.get("question", "")))
            if q.get("options"):
                e += " 选项: " + " / ".join(str(o) for o in q["options"])
            # R4 T2 / R5 T1: include the standard answer / keywords ONLY for answer-dependent scenarios,
            # so they exercise the bank-backed answer contract instead of solving from prior knowledge
            # (same as drift/run_live_smoke.bank_digest). Serve-only quiz scenarios get NO answer in the
            # prompt — the agent cannot leak a key it was never given (leak-guard below is defense-in-depth).
            key = q.get("answer") if q.get("answer") not in (None, "", []) else q.get("answer_keywords")
            if answer_dependent and key not in (None, "", []):
                e += "（标准答案: %s）" % key
            # visual-required items must expose their asset paths + roles so a real agent knows
            # WHICH image to render first (the detector expects the exact fixture path back).
            if q.get("requires_assets") or q.get("maybe_requires_assets") \
                    or q.get("question_text_status") in ("stub", "page_reference"):
                for a in (q.get("assets") or []):
                    # ONLY question-side assets go in the prompt — never answer_context/worked_solution
                    # (emitting those would leak the answer / tell the agent to show it before solving).
                    if (isinstance(a, dict) and a.get("path")
                            and a.get("role") in ("question_context", "figure", "diagram", "table")):
                        e += "\n    题面侧图（先真实展示、标「题面图」）: %s（role=%s）" % (
                            str(a["path"]).replace("\\", "/"), a.get("role"))
            lines.append(e)
    digest = "\n".join(lines)
    plan = ""
    plan_p = os.path.join(fixture_path, "study_plan.md")
    if os.path.isfile(plan_p):
        plan = "\n【复习计划】\n" + _read(plan_p)
    return ("你是「期末极速备考教练」skill。严格按其防幻觉/分章/关卡契约输出。" + plan
            + "\n【" + digest + "】\n\n学生：" + sc["prompt"] + "\n辅导：")


def _reply_has_ai_generated_label(reply):
    """True if the reply's provenance uses the AI-generated answer label (zh or en canonical) — then
    the source-block/⑤ title must carry the full warning (question_source_block_ok ai_answer mode)."""
    return ("AI生成答案，非老师/教材提供" in reply
            or "AI-generated answer — not from your teacher or textbook" in reply)


# phrases that INTRODUCE a revealed answer (NOT the bare word 答案, which also appears in benign
# 「回复答案」 instructions). Used to tell a leaked short key from the same token merely occurring as an
# option label / in the question body. Mirrors the contract of drift/run_live_smoke.py's quiz leak check.
_ANSWER_REVEAL_SRC = r"标准答案|参考答案|正确答案|正确选项|正确的?是|答案速查|答案[:：是为]|应选|应填|正解|参考解|解析"


def _leak_norm(text):
    # strip whitespace AND markdown emphasis (* _ ~ `) so an answer mangled with **bold** / `code`
    # can't dodge the verbatim check
    return re.sub(r"[\s*_~`]+", "", text or "")


def _reply_leaks_answer_key(reply, answers):
    """A student-facing quiz-SERVE reply must not reveal a served item's standard answer. Two leak forms
    are caught: (1) a DISTINCTIVE full answer (normalized len >= 8 — a worked solution / code block)
    dumped verbatim; (2) ANY answer, INCLUDING short keys ('A' / 'FIFO' / 'True'), sitting next to an
    answer-reveal marker (标准答案 / 正确答案 / 答案速查 / 正解 …). The marker requirement for short keys
    separates a LEAKED key from the same token appearing as an option label, inside the question body, or
    in a 「回复答案」 instruction. Paraphrased answers are inherently beyond a string check (same limit as
    the sibling drift/run_live_smoke.py leak oracle)."""
    r = _leak_norm(reply)
    for a in answers:
        s = _leak_norm(str(a)) if a not in (None, "", []) else ""
        if not s:
            continue
        if len(s) >= 8 and s in r:
            return True
        esc = re.escape(s)
        if re.search(r"(?:" + _ANSWER_REVEAL_SRC + r").{0,12}" + esc, r) \
                or re.search(esc + r".{0,12}(?:" + _ANSWER_REVEAL_SRC + r")", r):
            return True
    return False


def _served_bank_items(reply, fixture_path):
    """For a template reply that tags [#id]s: (ids, all_in_bank, any_ai_generated).
    all_in_bank is False for an empty or any-invented tag set (R6 U3 — a fabricated [#FAKE_999] must not
    pass). any_ai_generated flags a served item the bank marks source=ai_generated (R6 U6)."""
    bank = load_quiz_bank_map(fixture_path)
    ids = extract_question_ids(reply)
    in_bank = [i for i in ids if i in bank]
    all_in_bank = bool(ids) and len(in_bank) == len(ids)
    any_ai = any(bank[i].get("ai_generated") or bank[i].get("source") == "ai_generated" for i in in_bank)
    return ids, all_in_bank, any_ai


def _reply_teaches_bank_topic(reply, bank):
    """R6 U3: each served [#id]'s segment must actually TEACH that bank item's topic — require a
    distinctive >=4-char CJK n-gram of the bank question to appear in the id's segment (its tag line +
    following lines up to the next tag). This TOLERATES pedagogical retitling / paraphrase / dropped
    parentheticals (only ONE shared 4-gram is needed) but REJECTS a real id stuck on a wholly fabricated
    off-bank question (a real citation on invented content — the exact hallucination the skill forbids).
    An id whose question has no >=4-char CJK run imposes no anchor (can't be gamed by content there)."""
    lines = (reply or "").splitlines()
    for idx, ln in enumerate(lines):
        lids = extract_question_ids(ln)
        if not lids:
            continue
        item = bank.get(lids[0])
        q = item.get("question", "") if isinstance(item, dict) else ""
        grams = set()
        for run in re.findall(r"[一-鿿]{4,}", re.sub(r"\s+", "", q)):
            for i in range(len(run) - 3):
                grams.add(run[i:i + 4])
        if not grams:
            continue
        seg = re.sub(r"\[#[^\]]+\]", "", ln)
        for nxt in lines[idx + 1:]:
            if extract_question_ids(nxt):
                break
            seg += nxt
        seg = re.sub(r"\s+", "", seg)
        if not any(g in seg for g in grams):
            return False
    return True


def live_reply_check(name, sc, reply, fixture_path):
    """Apply the scenario's POSITIVE detector to a LIVE reply (the SAME functions --mock uses, so
    no logic drift). Returns (ok, detail) for reply-verifiable scenarios, or None for scenarios whose
    contract is about WRITTEN state/files — a one-shot `claude -p` can only TALK, so those are skipped."""
    if name == "quiz_bank_only":
        qmap = load_quiz_bank_map(fixture_path)
        ch = sc.get("chapter")
        in_scope = {i: v for i, v in qmap.items()
                    if ch is None or str(v.get("chapter")) == str(ch) or str(v.get("phase")) == str(ch)}
        scoped = {i: v["question"] for i, v in in_scope.items()}
        n = len(set(extract_question_ids(reply)))
        # R5 T1: a serve-only quiz reply must not dump the served items' answer key to the student.
        leaked = _reply_leaks_answer_key(reply, [v.get("answer") for v in in_scope.values()])
        ok = assert_quiz_ids_in_bank(reply, scoped) and n >= sc.get("min_questions", 1) and not leaked
        return ok, f"ids_in_bank={assert_quiz_ids_in_bank(reply, scoped)} n={n} answer_leak={leaked}"
    if name == "scope_override":
        # scope_override_declared treats "no item served" as vacuously OK, so a reply that prints only
        # the override warning and serves nothing would falsely PASS live — also require ≥1 served item.
        # R5 T2: content-match the served items against the bank (pass a {id: question} DICT, not a set)
        # so a valid-ID sticker on invented/mismatched text FAILS — mirrors the strengthened --mock check.
        # R6 U2: the scenario asked for lecture VISUAL/figure items — restrict the acceptable map to the
        # visual subset, so serving a non-visual bank item (e.g. the stack MCQ) after the override FAILS.
        # R5 T1: also forbid leaking the answer key of any served item.
        bank = load_quiz_bank_map(fixture_path)
        vmap = {i: v["question"] for i, v in bank.items() if v.get("visual")}
        ids = extract_question_ids(reply)
        matched = assert_quiz_ids_in_bank(reply, vmap)
        leaked = _reply_leaks_answer_key(reply, [v.get("answer") for v in bank.values()])
        ok = scope_override_declared(reply) and len(ids) >= 1 and matched and not leaked
        return ok, (f"override_before_first_item={scope_override_declared(reply)} "
                    f"items_served={len(ids)} visual_content_match={matched} answer_leak={leaked}")
    if name == "provenance_labels":
        ok = has_canonical_provenance_labels(reply)
        return ok, f"canonical_labels={ok}"
    if name == "zero_basic_key_question":
        # R6 U3: the served [#id] must be a REAL bank item (a fabricated [#FAKE_999] must not pass) AND
        # the taught content must match that item's topic (a real id on a fabricated off-bank question
        # must not pass). R6 U6: force AI-answer mode when the BANK marks the served item ai_generated —
        # so a bank ai_generated item can't be mislabeled 🟢; also honor the reply's own ⚠️ label.
        bank = load_quiz_bank_map(fixture_path)
        _ids, all_in_bank, bank_ai = _served_bank_items(reply, fixture_path)
        on_topic = _reply_teaches_bank_topic(reply, bank)
        ai = _reply_has_ai_generated_label(reply) or bank_ai
        ok = (all_in_bank and on_topic and teaching_template_ok(reply)
              and question_source_block_ok(reply, ai_answer=ai) and has_zero_basic_sections(reply))
        return ok, f"ids_in_bank={all_in_bank} on_topic={on_topic} seven_step+source(ai={ai})+sections={ok}"
    if name == "teaching_template":
        # T2: mirror --mock — the default output ENDS at the source block, so unsolicited closers
        # (易错点 / 3分钟速记 / 现在轮到你) after it are a contract violation.
        # R3: when the reply's source block carries the AI-generated label, the ⑤/解析 title must too.
        # R6 U3: served [#id] must be a REAL bank item AND teach its topic. R6 U6: bank ai_generated
        # forces ⚠️ mode.
        bank = load_quiz_bank_map(fixture_path)
        _ids, all_in_bank, bank_ai = _served_bank_items(reply, fixture_path)
        on_topic = _reply_teaches_bank_topic(reply, bank)
        ai = _reply_has_ai_generated_label(reply) or bank_ai
        ok = (all_in_bank and on_topic and teaching_template_ok(reply)
              and question_source_block_ok(reply, ai_answer=ai) and no_unsolicited_closing_blocks(reply))
        return ok, f"ids_in_bank={all_in_bank} on_topic={on_topic} seven_step+source(ai={ai})+no_closers={ok}"
    if name == "time_budget_no_questions":
        # 本 live 场景的 prompt 明确写了「别问我问题」，因此按 no_questions=true 检查；
        # 普通≤1天的题库 checkpoint 豁免由同一函数的默认分支与 deterministic mock 覆盖。
        ok = urgent_question_cadence_ok(reply, fixture_path, no_questions=True)
        return ok, f"explicit_no_questions_respected={ok}"
    if name == "knowledge_window_recheck":
        ok = window_out_rechecked(reply)
        return ok, f"out_of_window_rechecked={ok}"
    if name == "language_first_ask":
        ok = language_first_ask_ok(reply)
        return ok, f"trilingual_first_ask={ok}"
    if name == "visual_first_assets":
        ok = visual_first_asset_display_ok(reply, fixture_path)
        return ok, f"prompt_asset_first={ok}"
    if name == "notebook_persist_ok":
        # v4 §2.4：回执本身是 reply 可验的（命令 + 锚点链接都在学生可见回复里）；真实落盘文件是否
        # 存在属于 state/file 维度，留给 drift live——这里与 --mock 用同一只探测器，无逻辑漂移。
        ok = notebook_persist_receipt_ok(reply)
        return ok, f"notebook_persist_receipt={ok}"
    if name == "checkpoint_recovery":
        # R6 U4: reply-verifiable — the resume message must point at the CURRENT phase and not restart at
        # phase 1. The fixture's phase is a static precondition (checked in --mock); the drift-catching
        # signal (does the agent resume from phase N vs restart?) lives in the assistant text, so DON'T
        # group this with the file-mutation SKIP cases.
        ok = resume_refers_to_phase(reply, sc["expected_phase"])
        return ok, f"resume_refers_to_phase({sc['expected_phase']})={ok}"
    if name == "artifact_mode_routing":
        # The live prompt explicitly asks for the economical standing choice.  This one-turn check
        # can prove the agent did not auto-render or silently switch to visual; the visual/one-shot
        # positive branches are covered by deterministic command traces and long-session drift.
        ok = artifact_mode_routing_ok(reply, "chat")
        return ok, f"explicit_chat_did_not_render_or_switch={ok}"
    return None   # state/file-mutation or best-effort scenario → not reply-verifiable one-shot


# the repo's skill-contract surface — copied into every live sandbox so a paid `--llm` run exercises
# THIS skill, not a generic agent guessing from the prompt stub (R6 U1). Only paths that exist are copied.
# v4-P2: the full-entry packs live under locales/ (SKILL.en.md is retired) — without locales/ the root
# SKILL.md router dispatches into nothing and a live run stops exercising the actual manual.
_SKILL_CONTRACT_PATHS = ("SKILL.md", "locales", "AGENTS.md", "skills", "prompts", "scripts", "docs")


def _prepare_live_sandbox(fixture_path):
    """Throwaway sandbox = a COPY of the fixture workspace (the agent's cwd) PLUS the repo's skill
    contract. Returns (sandbox_root, agent_cwd). The detector ORACLE always reads the pristine original
    fixture_path, so a misbehaving agent can neither dirty the repo nor poison later checks (R6 U1)."""
    sandbox = tempfile.mkdtemp(prefix="bsmoke_")
    agent_cwd = os.path.join(sandbox, "ws")
    shutil.copytree(fixture_path, agent_cwd)
    for rel in _SKILL_CONTRACT_PATHS:
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(agent_cwd, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    return sandbox, agent_cwd


def run_llm(argv=None):
    """OPT-IN single-turn live smoke: drive a real agent per scenario, apply each scenarios PRIMARY positive detector — the SAME deterministic
    detectors as --mock to each reply, write transcripts, report metrics. Never in CI.

    Two ways to run:
      * real agent (subscription, no API key): set RUN_SKILL_BEHAVIOR_LLM=1 (default cmd `claude -p {prompt}`);
      * any agent / a stub (also how the wiring is tested deterministically): pass --agent-cmd "<cmd {prompt}>".
    """
    ap = argparse.ArgumentParser(description="behavior_smoke live (opt-in)")
    ap.add_argument("--agent-cmd", help="agent 命令模板，含 {prompt}（给了就跑，用于真跑或 stub 测试）")
    ap.add_argument("--out-dir", default=None,
                    help="transcript 输出目录（默认写到仓库外的系统临时目录，避免含答案键的 transcript 落进工作树；"
                         "R6 U5）")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-out", type=int, default=200000)
    # main-level flags reach here in the forwarded argv — declare them as harmless so ONLY genuine
    # typos land in the remainder, which we then reject (a paid run must not silently proceed on
    # `--timeot 5` with the default timeout).
    ap.add_argument("--llm", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--check-fixture", action="store_true", help=argparse.SUPPRESS)
    args, rest = ap.parse_known_args(argv)
    if rest:
        ap.error("unrecognized live-smoke arguments: " + " ".join(rest))
    # budget caps must be positive — a negative --max-out would turn call_agent's guarded stdout read
    # into an unbounded read, defeating the very budget guard this path exists to enforce.
    if args.timeout <= 0 or args.max_out <= 0:
        ap.error("--timeout 和 --max-out 必须为正整数（预算上限，不能 ≤0）")

    if not args.agent_cmd:
        if os.environ.get("RUN_SKILL_BEHAVIOR_LLM") != "1":
            print("LLM behavioral smoke is OPT-IN and disabled by default.")
            print("Enable a real run: set RUN_SKILL_BEHAVIOR_LLM=1 (uses `claude -p`), "
                  "or pass --agent-cmd \"<cmd containing {prompt}>\".")
            return 2
        args.agent_cmd = DEFAULT_AGENT_CMD

    sys.path.insert(0, os.path.join(ROOT, "benchmark", "drift"))
    import run_live_smoke as _LS   # reuse its hardened call_agent (timeout / output cap / kill-tree)

    spec = load_scenarios()
    fixture_path = os.path.join(HERE, spec.get("fixture", "fixtures/mini_course"))
    # R6 U5: default the transcript dir OUTSIDE the repo. A naive `--llm` run then never writes prompt/
    # reply transcripts (which embed answer keys for answer-dependent scenarios) into the worktree.
    out_dir = args.out_dir or tempfile.mkdtemp(prefix="bsmoke_out_")
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for sc in spec["scenarios"]:
        name = sc["name"]
        probe = live_reply_check(name, sc, "", fixture_path)
        if probe is None:      # not reply-verifiable via a one-shot call — honestly skip
            results.append((name, "state/file-mutation 或 best-effort，一次性 `-p` 不可验（如需请用 drift live）",
                            "SKIP"))
            continue
        prompt = _live_prompt(fixture_path, sc)
        # T1/U1: run the agent inside a THROWAWAY sandbox = fixture copy + the repo's skill contract
        # (SKILL.md / skills/ / …). The agent has filesystem access and could mutate quiz_bank.json /
        # progress, but the detector ORACLE always reads the pristine original fixture_path, so a
        # misbehaving agent can neither dirty the repo nor poison later checks.
        sandbox, agent_cwd = _prepare_live_sandbox(fixture_path)
        try:
            reply = _LS.call_agent(args.agent_cmd, prompt, args.timeout, args.max_out, cwd=agent_cwd)
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
        with open(os.path.join(out_dir, "live_%s.md" % name), "w", encoding="utf-8") as f:
            f.write("# scenario: %s\n\n## prompt\n%s\n\n## reply\n%s\n" % (name, prompt, reply))
        ok, detail = live_reply_check(name, sc, reply, fixture_path)
        results.append((name, detail, "PASS" if ok else "FAIL"))

    n_pass = sum(1 for _, _, s in results if s == "PASS")
    n_skip = sum(1 for _, _, s in results if s == "SKIP")
    n_fail = sum(1 for _, _, s in results if s == "FAIL")
    for nm, detail, status in results:
        print(f"  [{status}] {nm}: {detail}")
    print(f"  live smoke: {n_pass} passed, {n_skip} skipped, {n_fail} failed "
          f"(transcripts → {out_dir}/live_*.md)")
    return 1 if n_fail else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Tier 2 behavioral smoke — deterministic by default, LLM smoke opt-in")
    ap.add_argument("--mock", action="store_true",
                    help="run deterministic detectors on mock outputs (no LLM, no network)")
    ap.add_argument("--check-fixture", action="store_true",
                    help="validate the mini-course fixture workspace (Tier 1)")
    ap.add_argument("--llm", action="store_true",
                    help="real claude -p smoke; requires RUN_SKILL_BEHAVIOR_LLM=1 (off in CI)")
    args, _rest = ap.parse_known_args(argv)   # --llm sub-flags (--agent-cmd …) parsed inside run_llm
    # T3: leftover args are ONLY legitimate for the --llm runner (--agent-cmd/--out-dir/…). Outside it,
    # an unknown/misspelled flag must FAIL loudly, not silently make a --mock/--check-fixture run green.
    if _rest and not args.llm:
        ap.error("unrecognized arguments: " + " ".join(_rest))
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.llm:
        return run_llm(argv)
    if args.check_fixture:
        return 0 if check_fixture() else 1
    if args.mock:
        ok, _ = run_mock()
        print("结论:", "✓ 确定性行为冒烟全部通过（best-effort 项已跳过、未断言）" if ok else "✗ 有失败")
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
