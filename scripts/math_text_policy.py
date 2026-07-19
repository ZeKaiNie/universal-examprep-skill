#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared policy for TeX commands embedded in human-facing prose.

Typed TeX fields store TeX without Markdown delimiters.  Human-facing prose is a
different trust domain: a TeX command is allowed there only inside the durable
``$...$`` or ``$$...$$`` source form.  This module only detects that boundary; it
never guesses at or rewrites authored text.
"""

import re


# Keep this vocabulary intentionally narrower than a generic ``\\[A-Za-z]+``
# matcher.  Course prose can legitimately contain Windows paths such as
# ``D:\\EEC``; those must not be mistaken for mathematics.
LATEX_COMMAND_RE = re.compile(
    r"\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|oint|lim|log|ln|sin|cos|tan|exp|"
    r"min|max|sup|inf|begin|end|left|right|middle|cup|cap|bigcup|bigcap|"
    r"setminus|mid|vert|land|lor|"
    r"leq|geq|le|ge|neq|approx|equiv|in|notin|subset|subseteq|supset|supseteq|"
    r"mathbb|mathcal|mathbf|mathrm|text|operatorname|overline|underline|vec|hat|"
    r"bar|dot|ddot|cdot|cdots|ldots|times|div|pm|mp|to|rightarrow|leftarrow|"
    r"Rightarrow|Leftrightarrow|implies|iff|infty|partial|nabla|forall|exists|"
    r"Pr|pmod|quad|boxed|varnothing|ne|"
    r"alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|rho|sigma|tau|phi|psi|omega)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

_WINDOWS_DRIVE_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\[^\s<>\"']*")
_WINDOWS_UNC_PATH_RE = re.compile(
    r"(?<!\\)\\\\[^\\\s<>\"']+\\[^\\\s<>\"']+(?:\\[^\s<>\"']*)?"
)
_MARKDOWN_CODE_RE = re.compile(r"```[\s\S]*?```|`[^`\r\n]*`")
_RAW_SUPERSCRIPT_RE = re.compile(
    r"(?:[A-Za-z0-9\)\]\}\u0370-\u03ff])\s*\^\s*"
    r"(?:[+-]?\d+|\{[^{}\r\n]{1,32}\}|[A-Za-z\u0370-\u03ff])"
)
_RAW_SUBSCRIPT_RE = re.compile(
    r"(?:\b[A-Za-z\u0370-\u03ff]|[\)\]])\s*_\s*"
    r"(?:\d+|\{[^{}\r\n]{1,32}\}|[A-Za-z\u0370-\u03ff])"
)
_STACKED_FRACTION_RE = re.compile(
    r"(?:=|≠|≤|≥)\s*([+-]?(?:\d+(?:\.\d+)?|[A-Za-z]))\s*\n"
    r"\s*([+-]?(?:\d+(?:\.\d+)?|[A-Za-z]))(?=\s*(?:$|[,.;:)\]+\-*/=≠≤≥]))",
    re.MULTILINE,
)
_VERTICAL_OPERATOR_RE = re.compile(
    r"(?:=|:)\s*\n\s*[^\n]{1,12}\s*\n\s*(?:X|Σ|∑)\s*(?:\n|$)",
    re.MULTILINE,
)


def _mask_matches(text, regex):
    chars = list(text)
    for match in regex.finditer(text):
        for position in range(match.start(), match.end()):
            if chars[position] not in "\r\n":
                chars[position] = " "
    return "".join(chars)


def mask_windows_paths(text):
    """Blank absolute drive/UNC paths without hiding relative prose commands."""

    masked = _mask_matches(text or "", _WINDOWS_DRIVE_PATH_RE)
    return _mask_matches(masked, _WINDOWS_UNC_PATH_RE)


def mask_standard_math(text):
    """Blank complete dollar-delimited math spans while preserving positions.

    Inline math may not consume a newline; display math may span lines.  An
    unmatched delimiter is deliberately left visible so a command after it still
    fails closed.
    """

    chars = list(text or "")
    for start, end in iter_standard_math_spans(text):
        for position in range(start, end):
            if chars[position] not in "\r\n":
                chars[position] = " "
    return "".join(chars)


def iter_standard_math_spans(text):
    """Yield complete dollar-delimited math spans as ``(start, end)`` pairs.

    This is the single tokenizer used both by the output-safety mask and by
    dependency/readiness preflight.  Keeping those consumers on the same span
    definition prevents a guide from rendering math that preflight failed to
    notice.  Incomplete delimiters are deliberately ignored here so the normal
    raw-math hazard checks can reject them separately.
    """

    value = text or ""
    length = len(value)
    index = 0
    while index < length:
        if value[index] != "$" or (index and value[index - 1] == "\\"):
            index += 1
            continue
        width = 2 if index + 1 < length and value[index + 1] == "$" else 1
        cursor = index + width
        close = None
        while cursor < length:
            if value[cursor] == "$" and (cursor == 0 or value[cursor - 1] != "\\"):
                if width == 1:
                    close = cursor
                    break
                if cursor + 1 < length and value[cursor + 1] == "$":
                    close = cursor
                    break
            if width == 1 and value[cursor] in "\r\n":
                break
            cursor += 1
        if close is None:
            index += width
            continue
        end = close + width
        yield index, end
        index = end


def count_standard_math_spans(text):
    """Count complete student-visible ``$...$``/``$$...$$`` math spans."""

    return sum(1 for _span in iter_standard_math_spans(text))


def first_bare_latex_command(text):
    """Return the first TeX command outside standard math, or ``None``."""

    match = search_visible_latex_command(mask_standard_math(text))
    return match.group(0) if match else None


def find_unrendered_math_hazard(text):
    """Return raw superscript/subscript notation outside durable math spans.

    A bare ``x^2`` or ``p_0`` may look acceptable in JSON or a terminal but is
    emitted literally by ordinary Markdown prose. Mask standard math, code,
    and Windows paths first so callers can fail closed without rewriting the
    author's intended expression.
    """

    masked = mask_standard_math(text or "")
    masked = _mask_matches(masked, _MARKDOWN_CODE_RE)
    masked = mask_windows_paths(masked)
    for code, regex in (
            ("raw_superscript", _RAW_SUPERSCRIPT_RE),
            ("raw_subscript", _RAW_SUBSCRIPT_RE)):
        match = regex.search(masked)
        if match:
            return {"code": code, "snippet": match.group(0)}
    return None


def search_visible_latex_command(text):
    """Return a TeX-command match without masking dollar-delimited final output."""

    return LATEX_COMMAND_RE.search(mask_windows_paths(text))


def find_math_layout_hazard(text):
    r"""Return an OCR math-layout hazard or ``None`` without rewriting evidence.

    PDF text extraction can flatten a stacked fraction into ``= 4\n9`` or a
    summation into ``=\nm\nX\ni=1``.  Such text may be exact source evidence but
    is unsafe as student-facing mathematics.  Callers must request a reviewed,
    typeset teaching field or a source image instead of guessing a repair.
    """

    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for code, regex in (
            ("stacked_fraction_flattened", _STACKED_FRACTION_RE),
            ("vertical_operator_flattened", _VERTICAL_OPERATOR_RE)):
        match = regex.search(value)
        if match:
            snippet = value[max(0, match.start() - 40):match.end() + 40]
            return {"code": code, "snippet": snippet}
    return None
