# -*- coding: utf-8 -*-
"""Pull questions (and answers when available) out of homework, quizzes and exams.

The bank only contains what the materials contain. When no answer is found the item
keeps `answer: null` so the tutor must label its own answer as AI-generated.

Numbering styles: `Problem 3` / `Q3` / `3.` / `第3题` / `(3)` in sequence, and textbook
labels such as `Problem 1.3.10` (matched to `Problem 1.3.10 Solution` in a solution file).
"""
import os
import re

from .index import BM25
from .text import chinese_numeral, tokenize

QUESTION_KINDS = ("homework", "exam", "solution")

_LABEL = r"(\d{1,3}(?:\.\d{1,3}){0,3})"
_ZH_NUM = r"([0-9一二三四五六七八九十]+)"
# Each pattern: (style, regex). Only line-start matches count.
_STARTS = [
    ("problem", re.compile(r"^\s*(?:problem|question|exercise|q)\s*[.#]?\s*%s\s*[.):：、\-]*\s*" % _LABEL, re.I)),
    ("zh_ti", re.compile(r"^\s*第\s*%s\s*[题问]\s*[.:：、]?\s*" % _ZH_NUM)),
    ("dot", re.compile(r"^\s*(\d{1,3})\s*[.、．)](?![.\d])\s*(?=\S)")),
    ("paren", re.compile(r"^\s*[（(](\d{1,3})[)）]\s*(?=\S)")),
]
_EXPLICIT = ("problem", "zh_ti")
_INNER_LABEL = re.compile(r"(?:problem|exercise|question|题)\s*%s(?![.\d])" % _LABEL, re.I)
_SOLUTION_TAIL = re.compile(r"\s*(?:solution|answer|解答|答案)s?\s*[:：]?\s*$", re.I)
_ANSWER_MARK = re.compile(
    r"^\s*(?:solution|answer|ans|解答|答案|参考答案|解析|解|答)\s*[:：.．]\s*", re.I | re.M)
_ANSWER_INLINE = re.compile(r"(?:^|\n)\s*(?:solution|answer|ans|解答|答案|参考答案|解析|解|答)\s*[:：.．]", re.I)
_OPTION = re.compile(r"^\s*[（(]?([A-H])[)）.．、]\s*(.+)$")
_POINTS = re.compile(r"\[\s*(\d+)\s*(?:points?|pts?|marks?)\s*\]|[（(]\s*(\d+)\s*分\s*[)）]", re.I)
_CHAPTER_HINT = re.compile(r"(?:chapter|lecture|ch\.?)\s*(\d{1,3})|第\s*([0-9一二三四五六七八九十]+)\s*[章讲]", re.I)
_SECTION_HEAD = re.compile(r"^\s*[一二三四五六七八九十]+\s*[、.．]\s*\S+题|^\s*(?:part|section)\s+[a-z0-9]+\b", re.I)
_SOL_TOKENS = re.compile(r"sol(?:ution)?s?|answers?|key|答案|解答|参考答案|题解", re.I)


def pair_key(rel):
    """`hw2 (4)(1).pdf`, `homework2solutions.pdf` and `hw2-solutions(1).pdf` all map to `hw2`."""
    name = os.path.splitext(os.path.basename(rel))[0].lower()
    name = re.sub(r"\(\d+\)", "", name)
    name = re.sub(r"homework|assignment|作业|problem\s*set|pset", "hw", name)
    name = _SOL_TOKENS.sub("", name)
    return re.sub(r"[^a-z0-9一-鿿]", "", name)


def _blocks(src):
    """Split a source into numbered question blocks: [{number, label, page, head, lines}].

    `number` is the ordinal used for the sequence rule; `label` is the textbook-style key
    (`1.3.10`) when present, else the ordinal as a string.  Explicit styles (`Problem N`,
    `第N题`) win over bare list numbering when a file has both.
    """
    explicit = _blocks_with(src, [s for s in _STARTS if s[0] in _EXPLICIT])
    if explicit:
        return explicit
    return _blocks_with(src, [s for s in _STARTS if s[0] not in _EXPLICIT])


def _blocks_with(src, starts):
    blocks, style, expect = [], None, 1
    cur = None
    for page in src.pages:
        if page.scan:
            # OCR text of a scanned sheet: keep only lines that follow a question label found on
            # this same page, so handwritten answer pages never continue a printed question
            cur = None
        for line in page.text.split("\n"):
            hit = None
            for st, rx in starts:
                if style is not None and st != style:
                    continue
                m = rx.match(line)
                if not m:
                    continue
                raw = m.group(1)
                if st == "problem" and "." in raw:
                    hit = (st, None, raw, line[m.end():])   # labelled: no sequence rule
                    break
                n = chinese_numeral(raw)
                if n is None:
                    continue
                if n == expect or n == 1 or (cur is not None and cur["number"] is not None and n == cur["number"] + 1):
                    hit = (st, n, None, line[m.end():])
                break
            if _SECTION_HEAD.match(line):
                cur, expect = None, 1  # "二、填空题" / "Part B": numbering restarts, header is not content
            elif hit:
                st, n, label, rest = hit
                style = style or st
                if label is None:
                    inner = _INNER_LABEL.search(rest)
                    label = inner.group(1) if inner and "." in inner.group(1) else str(n)
                rest = _SOLUTION_TAIL.sub("", rest) if _SOLUTION_TAIL.search(rest) else rest
                cur = {"number": n, "label": label, "page": page.number, "head": line.strip(), "lines": [rest]}
                blocks.append(cur)
                expect = (n + 1) if n is not None else expect
            elif cur is not None:
                cur["lines"].append(line)
    return blocks


def heads(src):
    """[(page, head_line, label)] for every question block of a source, in document order."""
    return [(b["page"], b["head"], b["label"]) for b in _blocks(src)]


def _split_answer(text):
    m = _ANSWER_INLINE.search(text)
    if not m:
        return text.strip(), None
    q = text[: m.start()].strip()
    a = _ANSWER_MARK.sub("", text[m.start():], count=1).strip()
    return q, (a or None)


def _strip_shared_lines(sol_text, q_text):
    qset = {" ".join(l.split()) for l in q_text.split("\n") if l.strip()}
    keep = [l for l in sol_text.split("\n") if l.strip() and " ".join(l.split()) not in qset]
    return "\n".join(keep).strip() or None


def _options(text):
    opts, rest = [], []
    for line in text.split("\n"):
        m = _OPTION.match(line)
        if m and (not opts or ord(m.group(1)) == ord(opts[-1][0]) + 1):
            opts.append("%s. %s" % (m.group(1), m.group(2).strip()))
        else:
            rest.append(line)
    if len(opts) >= 2:
        return opts, "\n".join(rest).strip()
    return [], text


def _qtype(text, options):
    if options:
        return "choice"
    low = text.lower()
    if re.search(r"true\s+or\s+false|判断(?:题|正误|对错)|正确还是错误|对还是错", low):
        return "true_false"
    if "____" in text or "___" in text or re.search(r"[（(]\s{2,}[)）]", text):
        return "fill_blank"
    return "subjective"


def _chapter_hint(text):
    m = _CHAPTER_HINT.search(text[:300])
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else chinese_numeral(m.group(2))


def _match_solution(sol_blocks, blk):
    for sb in sol_blocks:
        if sb["label"] == blk["label"]:
            return sb
    if blk["number"] is not None and "." not in blk["label"]:
        for sb in sol_blocks:
            if sb["number"] == blk["number"] and "." not in sb["label"]:
                return sb
    return None


def extract_questions(sources, chapters):
    q_sources = [s for s in sources if s.kind in QUESTION_KINDS and not s.error and s.pages]
    solutions = {}
    for s in q_sources:
        if s.kind == "solution":
            solutions.setdefault(pair_key(s.rel), s)
    paired = set()
    items = []

    for src in q_sources:
        key = pair_key(src.rel)
        sol = solutions.get(key) if src.kind != "solution" else None
        sol_blocks = _blocks(sol) if sol is not None else []
        if sol is not None:
            paired.add(sol.rel)
        for blk in _blocks(src):
            text = "\n".join(blk["lines"]).strip()
            q, a = _split_answer(text)
            sol_ref = None
            sb = _match_solution(sol_blocks, blk) if sol is not None and a is None else None
            if sb is not None:
                stext = "\n".join(sb["lines"]).strip()
                a = _strip_shared_lines(stext, q)
                if a is None:
                    _, a = _split_answer(stext)
                sol_ref = {"file": sol.rel, "page": sb["page"], "head": sb["head"]}
            if len(q) < 8 and not a:
                continue
            options, q_body = _options(q)
            m = _POINTS.search(q_body)
            items.append({
                "number": blk["number"],
                "label": blk["label"],
                "question": q_body,
                "options": options,
                "answer": a,
                "type": _qtype(q_body, options),
                "points": int(m.group(1) or m.group(2)) if m else None,
                "source": {"file": src.rel, "page": blk["page"], "kind": src.kind, "head": blk["head"]},
                "answer_source": sol_ref or ({"file": src.rel, "page": blk["page"], "head": blk["head"]} if a else None),
                "chapter": _chapter_hint(q_body),
            })

    # solution files WITH a partner must not produce duplicate items
    items = [it for it in items if not (it["source"]["kind"] == "solution" and it["source"]["file"] in paired)]

    assign_chapters(items, chapters)
    for i, it in enumerate(items, 1):
        it["id"] = "q%03d" % i
    return items


def assign_chapters(items, chapters):
    if not chapters:
        return
    numbers = {c.number for c in chapters}
    bm = BM25([tokenize(ch.text) for ch in chapters])
    for it in items:
        if it.get("chapter") in numbers:
            it["chapter_guessed"] = False
            continue
        first = it["label"].split(".")[0] if "." in it["label"] else None
        if first and first.isdigit() and int(first) in numbers:
            it["chapter"] = int(first)      # textbook label 7.3.6 → chapter 7
            it["chapter_guessed"] = False
            continue
        toks = tokenize(" ".join([it["question"], " ".join(it.get("options") or []), it.get("answer") or ""]))
        best = bm.best(toks, 1)
        it["chapter"] = chapters[best[0][0]].number if best else None
        it["chapter_guessed"] = True
