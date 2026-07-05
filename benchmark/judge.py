#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Scoring for the hallucination benchmark.

Two kinds of scoring, per the methodology:
  1. DETERMINISTIC numeric check  — for answer_type == "numeric": parse the number out
     of the answer and compare to the gold value within a tolerance. No LLM judge, so
     no judge noise where the ground truth is exact.
  2. LLM-as-judge (claim-level entailment) — for factual/definition answers: decompose
     the answer into atomic claims and check each against the grounding context
     (the gold supporting span). faithfulness = supported_claims / total_claims.
     The judge is BLINDED to which arm produced the answer, and should be a DIFFERENT
     model family than the generator when possible (configurable).

`ask_judge` is injected so the same code path works in --mock (a deterministic
overlap heuristic, no Claude needed) and in real mode (a `claude -p` call).
"""

import re
import json
import math

ABSTAIN_MARKERS = [
    "材料中未涵盖", "材料未涵盖", "无法确定", "不确定", "未提及", "没有提到",
    "not covered", "cannot determine", "not in the material", "i don't know", "not sure",
]

# 数字抽取（B5 加固）——**单遍、按出现顺序**扫，取最后一个数值单元的值（末位无效就 None，绝不回退到前面的数）。
# 一个数字单元 = 数字/逗号/小数点的连串（末位须是数字，不吞尾随标点）+ 可选科学计数。逗号+小数点混排交给
# _to_number 判：合法美式千分位（1,000,000 / 1,000.50）才转数，欧式（1.234,56）或乱逗号（3,14 / 1,2,3）当歧义拒。
# 乘方：数值^数值算成值（10^6 / 1e6^2 / 2^2.5）；含符号的乘方（n^2 / 2^n，复杂度记号）不是数值答案 → 跳过。
# 整数部分可省（.05 / p=.32 这类无前导零小数——APA/统计惯例，PSYC 110 常见），交给 _to_number 判逗号歧义。
# 符号仅在词/数边界起效：3-5 / 2020-01-01 / 555-1234 里的 - 是连字符不是负号（CJK 后仍算负号：答案是-5）。
# 空格千分位（ISO 31-0：1 000 000，含 NBSP/thin space）整段捕获后去空格转数。分数 3/4 算成 0.75（分母 0 拒）。
# 章节/页码引用（chapter 7 / p.12 / 第7章）不是数值答案 → 跳过——防「答案是 42，见第 7 章」把 7 当答案。
# 负号边界：字母/数字/下划线后的 - 是连字符（3-5 / 2020-01-01 / Q-5）；逗号/括号/CJK 后仍是负号
# （(3,-2) 坐标、roots 5,-3、答案是-5）。
_SIGN = r"(?:(?<![0-9A-Za-z_])[-+])?"
_SP_GROUP = ('(?<![0-9A-Za-z_])\\d{1,3}(?:[ \xa0\u2009\u202f]\\d{3})+'
             + '(?:[.,]\\d+)?(?!\\d)')              # 空格系千分位：左界防 Q1 粘连，尾界防 1 2020 前缀吞并
_NUM_CORE = r"(?:\d+(?:[.,]\d+)*|[.,]\d+)(?:[eE][+-]?\d+)?"
_UNIT = _SIGN + r"(?:" + _SP_GROUP + r"|" + _NUM_CORE + r")"
_DEC_EXP = r"[-+]?\d+(?:\.\d+)?"                                   # 指数允许小数（2^2.5）
_US_GROUP = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")     # 合法美式千分位（可带小数）
_POW_TAIL = r"(?:\s*\^\s*" + _DEC_EXP + r")?"                     # 分数操作数允许自带乘方（1/2^10）
_PAREN_UNIT = r"\(?\s*" + _UNIT + r"\s*\)?"                       # 括号负底数 (-2)^2 的标准写法
_COEF = _UNIT + r"\s*[×✕⨯*xX]\s*10\s*\^\s*" + _DEC_EXP   # 系数科学计数（x 仅在紧跟 10^ 时算乘号）
_ANY_NUM = re.compile(
    r"(?P<coef>" + _COEF + r")"                              # 系数科学计数 2×10^6 / 1.5 x 10^-3 = 系数·10^指数
    r"|(?P<frac>" + _UNIT + _POW_TAIL + r"\s*/\s*" + _UNIT + _POW_TAIL + r")"   # 分数（^ 先于 / 结合）
    r"|(?P<pow>" + _PAREN_UNIT + r"\s*\^\s*" + _DEC_EXP + r")"   # 数值底数（可带括号）^ 数值指数 → 算成值
    r"|(?P<sym>[A-Za-z_]\w*\s*\^\s*" + _DEC_EXP + r")"       # 符号底数乘方 n^2 / n^2.5 → 跳过
    r"|(?P<sym2>" + _UNIT + r"\s*\^\s*[A-Za-z_]\w*)"         # 数值底数 ^ 符号指数 2^n（复杂度记号）→ 跳过
    r"|(?P<num>" + _UNIT + r")")                             # 普通数字单元
_STRIP_SP = re.compile(r"[ \u00a0\u2009\u202f]")
# 引用语境：英文关键词在数字前（chapter 7 / p. 12 / equation (7)——括号可选），中文要求「第」在前**且**
# 章/页/讲等类别字紧随其后（第7章）。「7 章」「共 7 章」这类**数量**（无「第」前缀）不算引用，照常当数。
_CITE_EN = re.compile(
    r"(?<![a-z])(?:(?:chapters?|chap|ch|pages?|sections?|sec|figures?|figs?|tables?|tab|"
    r"equations?|eqs?|lectures?|lec|slides?|questions?|problems?|exercises?|ex|refs?)\.?"
    r"|p{1,2}\.)\s*\(?\s*$", re.I)   # 复数形（pages 12-13 / slides 3/20）同样是引用
# p/pp **必须带点**（p. 45 是页码引用；p .32 / p = .32 是 p 值，不是引用）。
_CITE_CN_AFTER = re.compile(r"^\s*(?:[-–—~到至]\s*\d+(?:\.\d+)?\s*)?[章节節页頁讲講题題课課问問条]")


def _to_number(s):
    """字符串数字 → float；空/非数/非有限(inf/nan)/带逗号但非合法千分位 都返回 None（宁可标记也不猜）。"""
    s = str(s).strip()
    if "," in s:
        if not _US_GROUP.match(s):                     # 有逗号但不是合法千分位 → 歧义，拒绝
            return None
        s = s.replace(",", "")
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None             # 1e400 之类 → inf → 拒绝（否则 abs(inf-inf)=nan 判错）


def _unit_number(tok):
    """一个数字单元 token → float（先剥空格千分位再走 _to_number 的逗号歧义判定）。
    带空格千分位 + 单个逗号小数（ISO 欧式 1 000,5）时逗号**无歧义**是小数点——空格分组已定界。"""
    s = _STRIP_SP.sub("", tok)
    if s != tok and re.fullmatch(r"[-+]?\d+,\d+", s):
        s = s.replace(",", ".")
    return _to_number(s)


def _pow_value(tok):
    # 一元负号按数学约定**后结合**：-2^2 = -(2^2) = -4；只有带括号的 (-2)^2 才是负底数 = 4。
    tok = tok.strip()
    sign = 1.0
    if tok[:1] in "+-" and not tok[1:].lstrip().startswith("("):
        if tok[0] == "-":
            sign = -1.0
        tok = tok[1:].lstrip()
    base_str, _, exp_str = tok.partition("^")
    base = _unit_number(base_str.strip().lstrip("(").rstrip(")"))   # (-2)^2 的括号剥掉；底数歧义(1,00^2) → None
    if base is None:
        return None
    try:
        v = sign * (base ** float(exp_str.strip()))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    if not isinstance(v, float) or not math.isfinite(v):
        return None                                    # 负底数小数指数 → complex → 拒（isfinite 会 TypeError）
    return v


def _side_value(s):
    """分数的一侧：可自带乘方（1/2^10 的 2^10）。"""
    return _pow_value(s) if "^" in s else _unit_number(s)


def _coef_value(tok):
    """系数科学计数 2×10^6 → 系数 · 10^指数。任一侧解析失败 → None。"""
    coeff_str, rest = re.split(r"[×✕⨯*xX]", tok, 1)
    coeff = _unit_number(coeff_str.strip())
    power = _pow_value(rest.strip())                    # rest = '10 ^ exp'
    if coeff is None or power is None:
        return None
    v = coeff * power
    return v if math.isfinite(v) else None


def _frac_value(tok):
    num_str, _, den_str = tok.partition("/")
    n, d = _side_value(num_str), _side_value(den_str)
    if n is None or d is None or d == 0:               # 分母 0 / 任一侧歧义 → 整个分数作废
        return None
    v = n / d
    return v if math.isfinite(v) else None


def _is_citation(text, start, end):
    """数字是否处在章节/页码**引用**语境（chapter 7 / p. 12 / 第7章）——引用不是数值答案。"""
    pre = text[max(0, start - 12):start]
    if _CITE_EN.search(pre):
        return True
    return bool(re.search(r"第\s*$", pre) and _CITE_CN_AFTER.match(text[end:]))


# 引用**续接**连接符：区间（-/~/到）或列表（,/、/and/&）——page 12-13 / pages 12, 13 / pages 12 and 13
# 的后续页码同样跳过，不当答案。仅在已确立引用（cite_end 非空）后生效，不会误吞普通数字。
_CITE_RANGE_GAP = re.compile(
    r"\s*(?:[-–—~,、&]|(?<![a-z])(?:to|and|or)(?![a-z])|[到至和])\s*$", re.I)


def _extract_units(text):
    """按出现顺序返回所有**算数的**数值单元 [(值或 None, 起始, 结束), ...]——
    符号乘方 / 章节页码引用（含区间/列表后续、引用分数）都已跳过，无效单元值为 None（位置保留）。"""
    units = []
    if not text:
        return units
    cite_end = None                                    # 上一个被跳过的引用数字的结尾（用于吞掉区间/列表后续）
    for m in _ANY_NUM.finditer(text):
        if m.group("sym") is not None or m.group("sym2") is not None:
            continue                                   # 含符号的乘方(n^2 / 2^n) 不是数值答案 → 不计
        if m.group("num") is not None or m.group("frac") is not None:
            if _is_citation(text, m.start(), m.end()):
                cite_end = m.end()                     # page 3/4 这类引用分数也跳过
                continue
            if cite_end is not None and _CITE_RANGE_GAP.fullmatch(text[cite_end:m.start()]):
                cite_end = m.end()                     # 引用区间/列表后续（page 12-13 / 12, 13 的 13）一并跳过
                continue
        cite_end = None
        if m.group("coef") is not None:
            units.append((_coef_value(m.group("coef")), m.start(), m.end()))  # 系数科学计数 2×10^6
        elif m.group("pow") is not None:
            units.append((_pow_value(m.group("pow")), m.start(), m.end()))   # 坏乘方 → 值 None
        elif m.group("frac") is not None:
            units.append((_frac_value(m.group("frac")), m.start(), m.end()))  # 坏分数 → 值 None
        else:
            units.append((_unit_number(m.group("num")), m.start(), m.end()))  # 歧义/inf → 值 None
    return units


def _extract_final_unit(text):
    """返回 (最后一个数值单元的值或 None, 起始偏移, 结束偏移)；全文无数值单元则返回 None。
    末位无效**不回退**到前面的数——最终答案通常在末尾，回退会拿错。"""
    units = _extract_units(text)
    return units[-1] if units else None


def _extract_final_number(text):
    """返回文本里**最后一个数值单元**的值（float），无/末位无效则 None。最终答案通常在末尾。"""
    u = _extract_final_unit(text)
    return None if u is None else u[0]

_GOLD_MIN_LEN = 2
_NEG_TOKENS = ("不是", "并非", "不叫", "不属于", "not", "no", "never", "未", "非")
# 小句级否定（词法快路的否定升级）：强否定在 gold 所在小句里**前后任一侧**出现都不走快路，交给 LLM 裁判。
# 单字否定（未/非）只看紧邻窗口——防「未来」「非常」误伤。英文 not/no/never 用词边界——防 note/know 误伤。
_CLAUSE_SPLIT = re.compile(r"[。．.!?！？;；,，\n]")
_NEG_STRONG_CN = ("不是", "并非", "不叫", "不属于", "没有", "不算", "不对", "并不", "错误", "未提及")
_NEG_EN = re.compile(r"(?<![a-z])(?:not|no|never|nor|wrong|incorrect|false)(?![a-z])|n't(?![a-z])", re.I)


def looks_abstained(answer):
    a = (answer or "").lower()
    return any(m.lower() in a for m in ABSTAIN_MARKERS)


def _norm(s):
    """Lowercase + drop all whitespace, so 'Word-RAM' / 'word-ram' / 'word - ram' compare equal."""
    return re.sub(r"\s+", "", (s or "").lower())


def _lex_norm(s):
    """小写 + 空白折叠成单空格，再剥掉紧邻标点的空格（'word - ram'→'word-ram'）——
    但保留**词间**空格，让 ASCII 词边界检查有意义（microRAM ≠ ' RAM'）。"""
    s = re.sub(r"\s+", " ", (s or "").lower()).strip()
    return re.sub(r" (?=[^0-9a-zÀ-ɏ一-鿿])"
                  r"|(?<=[^0-9a-zÀ-ɏ一-鿿]) ", "", s)


def _ascii_alnum(c):
    return c.isascii() and c.isalnum()


def contains_gold(answer, gold):
    """Deterministic: True if the canonical gold answer appears verbatim inside the answer
    (whitespace/case-insensitive), at an ASCII word boundary, and isn't negated in its clause.

    A cheap, noise-free CORRECTNESS signal for canonical short answers — the same spirit as the
    numeric path. It exists because the LLM judge was demonstrably marking exact-match answers
    (e.g. answer 'Word-RAM。' vs gold 'Word-RAM') as unsupported. Short golds (< 2 chars) defer to
    the numeric/LLM paths to avoid spurious substring hits. False here is SAFE — it just falls
    through to the LLM judge — so every guard errs toward rejecting the fast path."""
    g = _lex_norm(gold)
    if len(g) < _GOLD_MIN_LEN:
        return False
    a = _lex_norm(answer)
    # 检查**每一处**出现：「有人说不是 Word-RAM，其实正确模型就是 Word-RAM」——第一处被否定、第二处干净 → True
    idx = a.find(g)
    while idx >= 0:
        if _occurrence_ok(a, g, idx):
            return True
        idx = a.find(g, idx + 1)
    return False


def _occurrence_ok(a, g, idx):
    """gold 在 a[idx:] 的这一处出现是否算数：ASCII 词边界 + 所在小句无强否定 + 贴邻无单字否定。"""
    # ASCII 词边界：gold 边缘是 ASCII 字母/数字时，答案里贴邻的字符不能也是（microRAM 不算含 RAM）
    if _ascii_alnum(g[0]) and idx > 0 and _ascii_alnum(a[idx - 1]):
        return False
    end = idx + len(g)
    if _ascii_alnum(g[-1]) and end < len(a) and _ascii_alnum(a[end]):
        return False
    # 小句级否定：gold 所在小句里前后任一侧出现强否定（'Word-RAM is not the answer'）→ 不走快路
    cs = 0
    for mm in _CLAUSE_SPLIT.finditer(a, 0, idx):
        cs = mm.end()
    me = _CLAUSE_SPLIT.search(a, end)
    clause = a[cs:me.start() if me else len(a)]
    if any(t in clause for t in _NEG_STRONG_CN) or _NEG_EN.search(clause):
        return False
    pre6 = a[max(0, idx - 6):idx]                      # 近窗：多字否定词
    if any(t in pre6 for t in ("不是", "并非", "不叫", "不属于", "not", "no", "never")):
        return False
    pre2 = a[max(0, idx - 2):idx]                      # 贴邻：单字否定（未/非）——防「非常明显是 X」被 6 字窗误伤
    return not any(t in pre2 for t in ("未", "非"))


# 弃答里的**顺带计数**（「我查了全部 20 讲都没提」「材料只讨论了 1960 年代」）：豁免需**两个条件同时**成立——
# ① 数字后紧跟量词/单位类别字（讲/章/页/年/lectures…），② 数字前有**材料范围提示词**（材料/全部/共/查了/
# only/checked/all…）。只有①不够：「not sure, maybe 5 years」「可能是 1960 年」是带单位的**猜测**，
# 仍算已作答（不确定+报数=对冲，不豁免）。
_COUNT_AFTER = re.compile(
    r"^\s*(?:[讲章页题课节遍次门册条年]"
    r"|(?:lectures?|chapters?|pages?|slides?|sections?|questions?|problems?|lessons?|times|years?)(?![a-z]))",
    re.I)
_SCOPE_CUE = re.compile(
    r"材料|讲义|课件|全部|所有|共|只讲|只讨论|只涉及|只到|只有|讲到|查了|翻了|看了|覆盖|截至"
    r"|(?<![a-z])(?:all|only|checked|covers?|entire|through)(?![a-z])", re.I)


def _incidental_count(answer, u):
    """答案里最后一个数字单元 u 是否只是「材料规模」类顺带计数（前有范围提示词 + 后跟量词类别字）。"""
    if u is None:
        return False
    return bool(_COUNT_AFTER.match(answer[u[2]:])
                and _SCOPE_CUE.search(answer[max(0, u[1] - 12):u[1]]))


def _hedged_numeric_commit(answer):
    """弃答文本里是否仍**给出了数字**（对冲）——检查**每一个**数值单元，任一非豁免即算对冲：
    「not covered; maybe 999. I checked all 20 lectures」末位 20 讲是顺带计数、但中间的 999 是猜测。
    含解析失败的数字尝试（「可能是 3,14」「maybe 5/0」：报了数≠没作答）。引用（第20讲）被抽取器跳过、
    材料规模顺带计数被豁免，都不算对冲。"""
    return any(not _incidental_count(answer, u) for u in _extract_units(answer))


def check_numeric(answer, gold, tolerance):
    """Deterministic numeric correctness within absolute tolerance.

    Compares the LAST number in the answer (final answers usually come last) to gold.
    Returns (correct: bool, parsed: float|None).
    """
    parsed = _extract_final_number(answer)
    gold_val = _to_number(gold)
    if parsed is None or gold_val is None:
        return False, None
    tol = 1e-6 if tolerance in (None, "") else abs(float(tolerance))
    return abs(parsed - gold_val) <= tol, parsed


def _faithfulness_prompt(question, answer, context):
    """Pointwise, span-anchored rubric (less position-biased than pairwise A/B)."""
    return (
        "你是严格的事实核查员。下面给你一段【参考依据】、一个【问题】和一个【待评答案】。\n"
        "请把【待评答案】拆成若干条原子事实陈述，对每条判断它是否能由【参考依据】直接推出"
        "（1=能支持，0=不能/与依据矛盾）。只依据【参考依据】判断，不要用你自己的知识。\n"
        "只输出 JSON：{\"claims\":[{\"claim\":\"...\",\"supported\":0或1}],"
        "\"correct\":0或1,\"abstained\":0或1}。correct=答案是否与依据一致且回答了问题；"
        "abstained=答案是否表示\"材料未涵盖/不确定\"。\n\n"
        f"【参考依据】\n{context}\n\n【问题】\n{question}\n\n【待评答案】\n{answer}\n"
    )


def _parse_judge_json(text):
    """Pull the JSON object out of a judge reply (tolerant of surrounding prose / ``` fences).

    Tries a greedy match (outermost braces) then a lazy one, so both a clean object and one wrapped
    in prose parse. Returns None ONLY when nothing parses — and the caller treats that as a judge
    ERROR (flagged), never as a silent 'wrong/hallucinated' verdict."""
    if not text:
        return None
    for m in (re.search(r"\{.*\}", text, re.DOTALL), re.search(r"\{.*?\}", text, re.DOTALL)):
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    return None


def mock_judge(prompt):
    """Deterministic stand-in judge for --mock runs (no Claude call).

    Crude lexical-overlap heuristic over the answer vs the reference context that the
    prompt embeds. Good enough to exercise the whole pipeline and produce a sample
    report; NOT a real measurement.
    """
    # rsplit so we grab the REAL context/answer blocks (the marker words also appear
    # in the instruction text above them).
    ctx = prompt.rsplit("【参考依据】", 1)[-1].split("【问题】", 1)[0]
    ans = prompt.rsplit("【待评答案】", 1)[-1]
    ctx_tokens = set(re.findall(r"\w+", ctx.lower()))
    ans_tokens = [t for t in re.findall(r"\w+", ans.lower()) if len(t) > 1]
    if not ans_tokens:
        return json.dumps({"claims": [], "correct": 0, "abstained": 1})
    supported = sum(1 for t in ans_tokens if t in ctx_tokens)
    ratio = supported / len(ans_tokens)
    claims = [{"claim": "mock", "supported": 1 if ratio >= 0.5 else 0}]
    return json.dumps({"claims": claims, "correct": 1 if ratio >= 0.5 else 0,
                       "abstained": 1 if looks_abstained(ans) else 0})


def judge_answer(item, answer, ask_judge, judge_repeats=1):
    """Score one answer. Returns a dict of metrics for this (item, answer).

    item: dict with id/question/gold_answer/supporting_span/answer_type/answerable/tolerance
    ask_judge: callable(prompt:str) -> str (the judge model reply); use mock_judge for --mock.
    """
    answer = answer or ""
    out = {"id": item["id"], "answer_type": item.get("answer_type", "factual"),
           "answerable": bool(item.get("answerable", True)), "abstained": looks_abstained(answer)}

    # Unanswerable probes: the right behaviour is to ABSTAIN. Answering = a hallucination.
    if not out["answerable"]:
        out["faithfulness"] = 1.0 if out["abstained"] else 0.0
        out["correct"] = out["abstained"]
        out["hallucinated"] = 0 if out["abstained"] else 1
        return out

    # Numeric: deterministic, no LLM judge.
    if out["answer_type"] == "numeric":
        correct, parsed = check_numeric(answer, item["gold_answer"], item.get("tolerance"))
        out["parsed_number"] = parsed
        if out["abstained"] and _hedged_numeric_commit(answer):
            out["abstained"] = False       # 挂着「不确定」却仍报出数字 = 已作答（含判不出值的数字尝试
            #                                「可能是 3,14」——报了数≠没作答）；对冲不能把瞎编洗成弃答
        out["faithfulness"] = 1.0 if correct else 0.0
        out["correct"] = correct
        out["hallucinated"] = 0 if (correct or out["abstained"]) else 1
        return out

    # Factual / definition.
    gold = item.get("gold_answer") or ""
    # (a) DETERMINISTIC lexical fast-path: the canonical gold answer is present verbatim -> correct,
    #     no LLM call. This both removes judge cost/noise AND fixes the observed failure where the
    #     LLM judge scored exact-match answers (answer 'Word-RAM。' vs gold 'Word-RAM') as wrong.
    if not out["abstained"] and contains_gold(answer, gold):
        out["faithfulness"] = 1.0
        out["correct"] = True
        out["hallucinated"] = 0
        out["scored_by"] = "lexical"
        return out

    # (b) Otherwise claim-level entailment via the (blinded) LLM judge, run judge_repeats times.
    context = (item.get("supporting_span") or "") + "\n" + gold
    prompt = _faithfulness_prompt(item["question"], answer, context)
    faiths, corrects, absts, parsed_any = [], [], [], False
    for _ in range(max(1, judge_repeats)):
        verdict = _parse_judge_json(ask_judge(prompt)) if ask_judge else None
        if verdict is None:
            continue                       # this reply didn't parse — don't count it as a verdict
        parsed_any = True
        claims = verdict.get("claims") or []
        supported = sum(1 for c in claims if c.get("supported") == 1)
        faiths.append(supported / len(claims) if claims else (1.0 if verdict.get("correct") else 0.0))
        corrects.append(1 if verdict.get("correct") else 0)
        absts.append(1 if verdict.get("abstained") else 0)
    if not parsed_any:
        # The judge produced NO parseable verdict (or wasn't supplied). A judge failure is NOT
        # evidence of a hallucination — flag it, don't silently score the answer as a fabrication
        # (that exact bug is what suppressed the skill arm to a fake 38%).
        out["faithfulness"] = None
        out["correct"] = False
        out["hallucinated"] = 0
        out["judge_error"] = True
        out["scored_by"] = "judge_error"
        return out
    out["faithfulness"] = sum(faiths) / len(faiths)
    out["correct"] = (sum(corrects) / len(corrects)) >= 0.5
    out["abstained"] = (sum(absts) / len(absts)) >= 0.5
    out["judge_self_consistency"] = _agreement(corrects)
    out["hallucinated"] = 0 if (out["faithfulness"] >= 0.999 or out["abstained"]) else 1
    out["scored_by"] = "llm"
    return out


def _agreement(votes):
    """Fraction of judge reruns that agree with the majority verdict."""
    if not votes:
        return 1.0
    ones = sum(votes)
    return max(ones, len(votes) - ones) / len(votes)
