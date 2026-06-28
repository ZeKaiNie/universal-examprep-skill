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

ABSTAIN_MARKERS = [
    "材料中未涵盖", "材料未涵盖", "无法确定", "不确定", "未提及", "没有提到",
    "not covered", "cannot determine", "not in the material", "i don't know", "not sure",
]

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

_GOLD_MIN_LEN = 2
_NEG_TOKENS = ("不是", "并非", "不叫", "不属于", "not", "no", "never", "未", "非")


def looks_abstained(answer):
    a = (answer or "").lower()
    return any(m.lower() in a for m in ABSTAIN_MARKERS)


def _norm(s):
    """Lowercase + drop all whitespace, so 'Word-RAM' / 'word-ram' / 'word - ram' compare equal."""
    return re.sub(r"\s+", "", (s or "").lower())


def contains_gold(answer, gold):
    """Deterministic: True if the canonical gold answer appears verbatim inside the answer
    (whitespace/case-insensitive) and isn't immediately negated.

    A cheap, noise-free CORRECTNESS signal for canonical short answers — the same spirit as the
    numeric path. It exists because the LLM judge was demonstrably marking exact-match answers
    (e.g. answer 'Word-RAM。' vs gold 'Word-RAM') as unsupported. Short golds (< 2 chars) defer to
    the numeric/LLM paths to avoid spurious substring hits."""
    g = _norm(gold)
    if len(g) < _GOLD_MIN_LEN:
        return False
    a = _norm(answer)
    idx = a.find(g)
    if idx < 0:
        return False
    pre = a[max(0, idx - 6):idx]                       # light guard: reject '不是 Word-RAM' / 'not X'
    return not any(_norm(n) in pre for n in _NEG_TOKENS)


def check_numeric(answer, gold, tolerance):
    """Deterministic numeric correctness within absolute tolerance.

    Compares the LAST number in the answer (final answers usually come last) to gold.
    Returns (correct: bool, parsed: float|None).
    """
    nums = _NUM_RE.findall(answer or "")
    if not nums:
        return False, None
    try:
        parsed = float(nums[-1])
        gold_val = float(gold)
    except (TypeError, ValueError):
        return False, None
    tol = 1e-6 if tolerance in (None, "") else float(tolerance)
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
