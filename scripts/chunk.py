#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-slice (v4-P3) — deterministic cleaning + chunking of course material, pure stdlib.

Why this exists: real ingested wikis are often DEGENERATE — the PSYC 110 chapters are single
20K–56K-char lines with scraper CSS residue at the head and zero headings, so "split on ##"
never fires. This module owns the full slicing path:

  clean_text()        strip CSS/HTML scraper residue + collapse whitespace (conservative:
                      only patterns that cannot be course prose)
  chunk_text()        headings when they exist; otherwise sentence-group rebuilding with
                      discourse-marker-preferred boundaries. Every chunk carries its
                      [start, end) offsets INTO THE CLEANED TEXT plus the cleaned text is
                      returned, so any chunk can be located back verbatim (acceptance:
                      a verbatim gold span lands inside exactly one chunk).

Ingest imports chunk_text(); retrieve.build_index() consumes the output shape directly.
No LLM, no network, no third-party deps.
"""
import re

TARGET = 1200          # aim chars per chunk
HARD_MAX = 2000        # plan acceptance: no chunk exceeds this
MIN_TAIL = 300         # a trailing chunk smaller than this merges backward

# --- cleaning: ONLY structures that cannot be legitimate course prose ---
# a CSS rule = selector + {prop: value; ...} — REQUIRE the css-declaration shape inside the
# braces (letters/hyphens, colon, value). Math braces ({x | x > 0}, {a, b, c}) have no
# `prop: value` pair and never match; python dicts ({'a': 1}) have quoted keys and never match.
_CSS_RULE_RE = re.compile(
    r"(?:^|\s)[\w.#][\w.#\-, >]{0,60}\{\s*(?:[a-zA-Z-]+\s*:\s*[^{};:]{1,80};?\s*){1,8}\}")
_HTML_TAG_RE = re.compile(r"</?(?:div|span|p|br|img|table|tr|td|th|ul|ol|li|a|b|i|em|strong)\b[^>]*>",
                          re.I)
_WS_RE = re.compile(r"[ \t　]+")

# sentence enders (en + zh) — offsets preserved because we split, never rewrite
_SENT_RE = re.compile(r"[^.!?。！？]*[.!?。！？]+[\"'”』」)]*\s*|[^.!?。！？]+$")
# lecture-transcript discourse markers: boundaries PREFER to start a new chunk here
_MARKER_RE = re.compile(
    r"^(?:okay|ok|so|now|all right|alright|next|today|let's|lets|first|second|finally|"
    r"好|那么|接下来|下面|现在|首先|其次|最后|我们来|让我们)\b", re.I)

_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+\S.*$")


def clean_text(text):
    """Deterministic conservative cleanup. Returns cleaned text; chunk offsets refer to THIS."""
    t = text or ""
    t = _HTML_TAG_RE.sub(" ", t)
    # CSS rules only in the first 2KB (scraper residue lives at the head; course text later may
    # legitimately contain braces — e.g. set notation {x | x > 0} — so never global-strip)
    head, rest = t[:2048], t[2048:]
    head = _CSS_RULE_RE.sub(" ", head)
    t = head + rest
    t = _WS_RE.sub(" ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _sentences(text):
    """[(start, end)] sentence spans covering the text (verbatim slices, nothing rewritten)."""
    out = []
    pos = 0
    for m in _SENT_RE.finditer(text):
        s = m.group(0)
        if s.strip():
            out.append((m.start(), m.end()))
        pos = m.end()
    if not out and text.strip():
        out = [(0, len(text))]
    return out


def _slice_by_headings(text):
    """[(title, start, end)] using markdown headings as boundaries; None if no usable headings."""
    marks = [(m.start(), m.group(0).strip()) for m in _HEADING_RE.finditer(text)]
    if len(marks) < 2:
        return None
    spans = []
    for i, (pos, raw) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        title = re.sub(r"^#{1,4}\s+", "", raw.splitlines()[0])[:80]
        spans.append((title, pos, end))
    # leading preamble before the first heading stays a chunk of its own when non-trivial
    if marks[0][0] > MIN_TAIL:
        spans.insert(0, ("", 0, marks[0][0]))
    return spans


def _pack_sentences(text, start, end, target, hard_max):
    """Greedy sentence packing inside [start, end): close a chunk near `target`, never exceed
    `hard_max`; prefer closing right BEFORE a discourse marker (new topic starts a new chunk)."""
    sents = [(s + start, e + start) for s, e in _sentences(text[start:end])]
    chunks = []
    cur_s = None
    cur_e = None
    for s, e in sents:
        if cur_s is None:
            cur_s, cur_e = s, e
            continue
        cand = e - cur_s
        marker = _MARKER_RE.match(text[s:min(e, s + 24)].lstrip())
        if cand > hard_max or (cand > target and marker) or (cand > target * 1.5):
            chunks.append((cur_s, cur_e))
            cur_s, cur_e = s, e
        else:
            cur_e = e
    if cur_s is not None:
        if chunks and (cur_e - cur_s) < MIN_TAIL and (cur_e - chunks[-1][0]) <= hard_max:
            chunks[-1] = (chunks[-1][0], cur_e)          # merge tiny tail backward
        else:
            chunks.append((cur_s, cur_e))
    # oversize single sentences (degenerate no-punctuation runs): hard-split at hard_max
    final = []
    for s, e in chunks:
        while e - s > hard_max:
            final.append((s, s + hard_max))
            s += hard_max
        final.append((s, e))
    return final


def chunk_text(raw_text, target=TARGET, hard_max=HARD_MAX):
    """→ (cleaned_text, [{"title","start","end","text"}]). Offsets index cleaned_text verbatim."""
    text = clean_text(raw_text)
    if not text:
        return text, []
    out = []
    spans = _slice_by_headings(text)
    if spans:
        for title, s, e in spans:
            if e - s <= hard_max:
                out.append({"title": title, "start": s, "end": e})
            else:
                for cs, ce in _pack_sentences(text, s, e, target, hard_max):
                    out.append({"title": title, "start": cs, "end": ce})
    else:
        for cs, ce in _pack_sentences(text, 0, len(text), target, hard_max):
            out.append({"title": "", "start": cs, "end": ce})
    for c in out:
        c["text"] = text[c["start"]:c["end"]]
    out = [c for c in out if c["text"].strip()]
    return text, out
