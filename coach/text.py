# -*- coding: utf-8 -*-
"""Small shared text helpers: tokenizing (ASCII words + CJK bigrams), cleanup, slicing."""
import re
import unicodedata

CJK = "぀-ヿ㐀-䶿一-鿿"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'+#\-]*|[%s]+" % CJK)
_CJK_RE = re.compile("[%s]" % CJK)
_STOP = frozenset(
    "a an the is are was were be been being am do does did have has had will would shall should "
    "can could may might must of in on at by for with about between into through to from up down "
    "out off over under and or but not no so than too very this that these those it its we you they "
    "he she i me my our your their what which who whom how when where why if then else".split()
)

# Ligatures and typography that PDF extraction commonly produces.
_REPLACE = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "’": "'", "‘": "'", "“": '"', "”": '"', " ": " ",
    "−": "-", "–": "-", "—": "-",
}


def clean(text):
    """Normalize whitespace and typography without destroying paragraph breaks."""
    if not text:
        return ""
    for k, v in _REPLACE.items():
        text = text.replace(k, v)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_mostly_cjk(text, sample=4000):
    """True when CJK characters dominate the alphabetic content."""
    if not text:
        return False
    sample_text = text[:sample]
    cjk = len(_CJK_RE.findall(sample_text))
    latin = sum(1 for ch in sample_text if ch.isascii() and ch.isalpha())
    return cjk * 2 > latin  # one CJK character carries about as much as two Latin letters


def tokenize(text):
    """ASCII words lowercased plus CJK character bigrams (unigram for a lone CJK char)."""
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if _CJK_RE.match(tok):
            if len(tok) == 1:
                out.append(tok)
            else:
                out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            if tok in _STOP:
                continue
            if tok.endswith("'s"):
                tok = tok[:-2]
            out.append(tok)
    return out


def split_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def pack(text, target, hard_max=None):
    """Split text into pieces of about `target` chars at paragraph, then line, boundaries."""
    hard_max = hard_max or int(target * 1.4)
    pieces, cur = [], ""
    for para in split_paragraphs(text):
        if len(para) > hard_max:
            # very long paragraph: cut on lines / sentences
            for line in re.split(r"(?<=[.!?。！？])\s+|\n", para):
                if not line:
                    continue
                if cur and len(cur) + len(line) + 1 > target:
                    pieces.append(cur)
                    cur = ""
                cur = (cur + "\n" + line) if cur else line
            continue
        if cur and len(cur) + len(para) + 2 > target:
            pieces.append(cur)
            cur = ""
        cur = (cur + "\n\n" + para) if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def shorten(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def chinese_numeral(s):
    """Convert 一二三…十/百 numerals to int; returns None if not a numeral."""
    if s.isdigit():
        return int(s)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if not s or any(ch not in digits and ch not in "十百" for ch in s):
        return None
    total, num = 0, 0
    for ch in s:
        if ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        else:
            num = digits[ch]
    return total + num


def display_width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
