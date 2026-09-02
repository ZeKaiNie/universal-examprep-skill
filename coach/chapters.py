# -*- coding: utf-8 -*-
"""Group extracted sources into ordered chapters.

Rules (deliberately few):
1. One teaching file with a number in its name or first lines is one chapter
   (lec3.pdf, 第3章.pptx, 03-sorting.docx, "Lecture 3: ..." on the first page).
2. A teaching file without such a number is split on internal `Chapter N` /
   `第N章` / `Lecture N` headings when there are at least two in rising order.
3. Anything still unnumbered becomes its own chapter after the numbered ones,
   in file order.  Files with the same number are merged into one chapter.
Homework / exam / solution files are never chapters; they feed the quiz bank.
"""
import os
import re

from .text import chinese_numeral

TEACHING_KINDS = ("lecture", "notes", "other")

_EN_WORDS = r"(?:chapter|chap|lecture|lec|unit|week|module|topic|lesson|section|part)"
_HEAD_EN = re.compile(
    r"^\s*#{0,6}\s*%s\s*[.#]?\s*(\d{1,3})\b[\s:.\-–—]*(.*?)\s*$" % _EN_WORDS, re.I)
_HEAD_ZH = re.compile(
    r"^\s*#{0,6}\s*第\s*([0-9一二三四五六七八九十百]+)\s*(?:章|讲|单元|课|节|部分)\s*[:：.\-\s]*(.*?)\s*$")
_ANY_EN = re.compile(r"%s\s*[.#]?\s*(\d{1,3})(?!\d)[\s:.\-–—]*(.*?)\s*$" % _EN_WORDS, re.I)
_ANY_ZH = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*(?:章|讲|单元|课|节|部分)\s*[:：.\-\s]*(.*?)\s*$")
_FILE_NUM = re.compile(
    r"(?:^|[^a-z])(?:lec|lecture|ch|chap|chapter|unit|week|module|topic|lesson|l|w|c)[ _\-]?0*(\d{1,3})(?!\d)", re.I)
_FILE_ZH = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*(?:章|讲|单元|课|节|部分)")
_FILE_LEAD = re.compile(r"^\s*0*(\d{1,3})(?!\d)\s*[ _\-\.、．]")
_MD_HEAD = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


class Chapter(object):
    def __init__(self, number, title):
        self.number = number
        self.title = title
        self.blocks = []       # [(source_rel, page_number, text)]
        self.sources = []      # source rel paths in order
        self.figures = []      # image files that belong here (by number in filename)

    @property
    def text(self):
        return "\n\n".join(b[2] for b in self.blocks if b[2])

    def to_dict(self):
        return {
            "n": self.number,
            "title": self.title,
            "sources": self.sources,
            "figures": self.figures,
            "blocks": [{"file": b[0], "page": b[1], "text": b[2]} for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls(d["n"], d["title"])
        c.sources = list(d.get("sources", []))
        c.figures = list(d.get("figures", []))
        c.blocks = [(b["file"], b["page"], b["text"]) for b in d.get("blocks", [])]
        return c


_ANY_NUM = re.compile(r"(?:^|[^0-9])0*(\d{1,3})(?!\d)")


def number_from_name(rel, loose=False):
    """Chapter number implied by a file name; `loose` also accepts a bare number (figure files)."""
    name = os.path.splitext(os.path.basename(rel))[0]
    m = _FILE_ZH.search(name)
    if m:
        return chinese_numeral(m.group(1))
    m = _FILE_NUM.search(name)
    if m:
        return int(m.group(1))
    m = _FILE_LEAD.match(name)
    if m:
        return int(m.group(1))
    if loose:
        m = _ANY_NUM.search(name)
        if m:
            return int(m.group(1))
    return None


def _heading_in_line(line):
    """Return (number, title) if the line looks like a chapter heading, else None."""
    if len(line) > 120:
        return None
    for rx, is_zh in ((_HEAD_ZH, True), (_HEAD_EN, False)):
        m = rx.match(line)
        if m:
            n = chinese_numeral(m.group(1)) if is_zh else int(m.group(1))
            if n is not None:
                return n, m.group(2).strip(" -:：.")
    return None


def _title_after(lines, idx):
    """Title printed on the line(s) after a bare 'Chapter N' heading (slide title pages)."""
    out = []
    for line in lines[idx + 1: idx + 4]:
        s = line.strip()
        if not s or len(s) > 80 or re.match(r"^\s*(section|§|\d+\.\d+|by\b|author|instructor|lecturer|prof)", s, re.I):
            break
        out.append(s)
    return " ".join(out)


def _number_and_title_from_head(lines):
    """Look at the first lines of a file for 'Lecture N: Title' anywhere in a short line."""
    for idx, line in enumerate(lines[:15]):
        if len(line) > 140:
            continue
        for rx, is_zh in ((_ANY_ZH, True), (_ANY_EN, False)):
            m = rx.search(line)
            if m:
                n = chinese_numeral(m.group(1)) if is_zh else int(m.group(1))
                if n is not None:
                    return n, m.group(2).strip(" -:：.#") or _title_after(lines, idx)
    return None, None


def guess_title(lines):
    for line in lines[:20]:
        m = _MD_HEAD.match(line)
        if m:
            return m.group(1)
    for line in lines[:10]:
        s = line.strip()
        if 2 <= len(s) <= 60 and not s.endswith((".", "。", ",", "，", ";", "；")) and not s[:1].isdigit():
            return s
    return ""


def _split_internal(src):
    """Split a numberless file on rising internal chapter headings. Returns list of segments."""
    heads = []
    seen_lines = []
    for page in src.pages:
        for idx, line in enumerate(page.text.split("\n")):
            hit = _heading_in_line(line)
            seen_lines.append((page.number, idx, line))
            if hit:
                heads.append((len(seen_lines) - 1, hit[0], hit[1]))
    if len(heads) < 2:
        return None
    numbers = [h[1] for h in heads]
    rising = sum(1 for a, b in zip(numbers, numbers[1:]) if b > a)
    if len(set(numbers)) < 2 or rising < (len(numbers) - 1) * 0.6:
        return None
    segments = []
    bounds = [h[0] for h in heads] + [len(seen_lines)]
    if bounds[0] > 0:  # text before the first heading joins the first chapter
        bounds[0] = 0
    for k, (start, number, title) in enumerate(heads):
        lo, hi = bounds[k], bounds[k + 1]
        blocks = {}
        for pnum, _, line in seen_lines[lo:hi]:
            blocks.setdefault(pnum, []).append(line)
        seg_blocks = [(src.rel, p, "\n".join(ls).strip()) for p, ls in sorted(blocks.items())]
        segments.append((number, title or guess_title([l for _, _, l in seen_lines[lo:hi]]), seg_blocks))
    return segments


def build_chapters(sources):
    numbered, unnumbered = {}, []
    figures = [s for s in sources if s.kind == "figure"]

    def add(number, title, rel, blocks):
        if number is None:
            unnumbered.append((title, rel, blocks))
            return
        ch = numbered.get(number)
        if ch is None:
            ch = numbered[number] = Chapter(number, title)
        elif not ch.title and title:
            ch.title = title
        ch.blocks.extend(blocks)
        if rel not in ch.sources:
            ch.sources.append(rel)

    for src in sources:
        if src.kind not in TEACHING_KINDS or src.error or not src.pages:
            continue
        lines = src.text.split("\n")
        n = number_from_name(src.rel)
        if n is None:
            # a numberless file is either one chapter ("Lecture 1: Intro" at the top)
            # or a whole book to split on its internal chapter headings
            segments = _split_internal(src)
            if segments:
                for seg_n, seg_title, seg_blocks in segments:
                    add(seg_n, seg_title, src.rel, seg_blocks)
                continue
        first_page = src.pages[0].text.split("\n")
        head_n, head_title = _number_and_title_from_head(first_page)
        if n is None:
            n = head_n
        title = head_title or guess_title(lines)
        add(n, title, src.rel, [(src.rel, p.number, p.text) for p in src.pages])

    chapters = [numbered[k] for k in sorted(numbered)]
    next_n = (max(numbered) + 1) if numbered else 1
    for title, rel, blocks in unnumbered:
        ch = Chapter(next_n, title or os.path.splitext(os.path.basename(rel))[0])
        ch.blocks = blocks
        ch.sources = [rel]
        chapters.append(ch)
        next_n += 1

    by_number = {c.number: c for c in chapters}
    for fig in figures:
        n = number_from_name(fig.rel, loose=True)
        target = by_number.get(n)
        if target is not None:
            target.figures.append(fig.rel)
    return chapters
