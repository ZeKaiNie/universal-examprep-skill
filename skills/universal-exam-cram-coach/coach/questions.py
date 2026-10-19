# -*- coding: utf-8 -*-
"""Pull questions (and answers when available) out of homework, quizzes and exams.

The bank only contains what the materials contain. When no answer is found the item
keeps `answer: null` so the tutor must label its own answer as AI-generated.

Numbering styles: `Problem 3` / `Q3` / `3.` / `第3题` / `(3)` in sequence, and textbook
labels such as `Problem 1.3.10` (matched to `Problem 1.3.10 Solution` in a solution file).
"""
import os
import re
import unicodedata

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


# ------------------------------------------------------------------ grading short answers
#
# Only answers that can be compared mechanically are graded here: option letters, true/false,
# numbers and short fill-ins. Anything else is left to the tutor, who sees the reference answer.

_KEY_PREFIX = re.compile(r"^\s*(?:正确答案|参考答案|答案|答|解|answer|ans|key|solution)\s*(?:是|为|is)?\s*[:：.．]?\s*", re.I)
_LETTERS = re.compile(r"^[（(]?\s*([A-H](?:\s*[,，、/和与]?\s*[A-H])*)\s*[)）]?(?![A-Za-z])")
_LETTER_ANYWHERE = re.compile(r"(?:答案|选|answer\s+is|correct\s+(?:answer|option)\s+is)\s*[:：是为]?\s*[（(]?\s*([A-H])(?![A-Za-z])", re.I)
_TRUE = {"正确", "对", "是", "√", "✓", "✔", "t", "true", "yes", "y", "correct", "right"}
_FALSE = {"错误", "错", "否", "不对", "×", "✗", "✘", "x", "f", "false", "no", "n", "incorrect", "wrong"}
_STRONG_TRUTH = {"正确", "错误", "√", "×", "✓", "✗", "✔", "✘", "true", "false"}  # unambiguous even outside T/F items
_NEG = re.compile(r"错|不|否|×|✗|✘|false|incorrect|wrong|\bno\b", re.I)
_POS = re.compile(r"对|正确|是|√|✓|✔|true|correct|right|\byes\b", re.I)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*/\s*\d+(?:\.\d+)?)?\s*%?")
_PUNCT = re.compile(r"[\s,，.。;；:：、!！?？'\"“”‘’()（）\[\]【】<>《》]+")


def _norm(text):
    return unicodedata.normalize("NFKC", text or "").strip()


def _first_line(answer):
    for line in (answer or "").split("\n"):
        if line.strip():
            return _KEY_PREFIX.sub("", _norm(line)).strip()
    return ""


def _number(text, strict=True):
    """The single number in `text` (fractions and percentages allowed), else None.

    strict: nothing but the number may be there (reference keys); otherwise a unit or a word
    around it is fine (the student's reply), as long as there is exactly one number.
    """
    found = _NUMBER.findall(text)
    if len(found) != 1 or (strict and _NUMBER.sub("", text).strip(" =≈约是为:：")):
        return None
    tok = found[0].replace(" ", "")
    pct = tok.endswith("%")
    tok = tok.rstrip("%")
    try:
        if "/" in tok:
            a, b = tok.split("/")
            val = float(a) / float(b)
        else:
            val = float(tok)
    except (ValueError, ZeroDivisionError):
        return None
    return val / 100 if pct else val


def _decimals(text):
    m = re.search(r"\.(\d+)", text)
    return len(m.group(1)) if m else 0


def _truth(text, loose=False):
    t = _PUNCT.sub("", _norm(text)).lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    if loose and len(t) <= 12:  # the student's reply: "对的", "不正确", "it's false"
        if _NEG.search(t):
            return False
        if _POS.search(t):
            return True
    return None


def answer_key(q):
    """(kind, value) for answers that can be compared mechanically, else None.

    kind is "choice" (set of letters), "truth" (bool), "number" (float, decimals) or "text".
    """
    answer = _norm(q.get("answer"))
    if not answer:
        return None
    head = _first_line(answer)
    if q.get("options"):
        m = _LETTERS.match(head) or _LETTER_ANYWHERE.search(answer)
        if m:
            return "choice", frozenset(re.findall(r"[A-H]", m.group(1)))
        return None
    lead = re.match(r"^(\S+?)(?=$|[\s.,;:!，。；：！])", head)  # "False. The heap property ..." → "False"
    lead = lead.group(1) if lead else head
    if q.get("type") == "true_false" or _PUNCT.sub("", lead).lower() in _STRONG_TRUTH:
        truth = _truth(lead) if lead != head or q.get("type") == "true_false" else _truth(head)
        if truth is not None:
            return "truth", truth
    if len(head) > 40:
        return None
    num = _number(head)
    if num is not None:
        return "number", (num, _decimals(head))
    if q.get("type") == "fill_blank" and len(head) <= 30:
        return "text", _PUNCT.sub("", head).lower()
    return None


def grade_answer(q, given):
    """("right" | "wrong" | "unsure", shown_key) for the student's answer `given`."""
    key = answer_key(q)
    given = _norm(given)
    if key is None or not given:
        return "unsure", None
    kind, value = key
    if kind == "choice":
        # capital letters anywhere ("I'd pick B"); lower case only in a bare reply ("b", "b,d"),
        # so the article "a" in a sentence is not read as option A
        pattern = r"(?<![A-Za-z])([A-Ha-h])(?![A-Za-z])" if len(given) <= 5 else r"(?<![A-Za-z])([A-H])(?![A-Za-z])"
        letters = re.findall(pattern, given)
        if not letters:  # the student may have typed the option text instead of its letter
            g = _PUNCT.sub("", given).lower()
            letters = [o[0] for o in q.get("options") or [] if _PUNCT.sub("", o[2:]).lower() == g]
        if not letters:
            return "unsure", "".join(sorted(value))
        return ("right" if frozenset(l.upper() for l in letters) == value else "wrong"), "".join(sorted(value))
    if kind == "truth":
        t = _truth(given, loose=True)
        shown = "√" if value else "×"
        return ("unsure" if t is None else "right" if t == value else "wrong"), shown
    if kind == "number":
        num, dec = value
        g = _number(given, strict=False)
        if g is None:
            return "unsure", _first_line(q["answer"])
        tol = max(0.5 * 10 ** -dec, 0.01 * abs(num), 1e-9)
        return ("right" if abs(g - num) <= tol else "wrong"), _first_line(q["answer"])
    same = _PUNCT.sub("", given).lower() == value
    return ("right" if same else "unsure"), _first_line(q["answer"])


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
