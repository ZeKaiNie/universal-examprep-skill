#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared visible-provenance compatibility rules for Study Guide outputs.

Typed provenance sidecars are authoritative.  Older authoring paths embedded a
full provenance label at the start of student-visible text.  Renderers may
remove that compatibility prefix, but only after proving that it agrees with
the sidecar.  A disagreement is never silently hidden.
"""

import re


class ProvenanceConflictError(ValueError):
    """Visible provenance text contradicts its typed provenance sidecar."""


PROVENANCE_ICONS = {
    "material": "🟢",
    "ai_translation": "🌐",
    "ai_supplement": "🟡",
    "ai_supplemented": "🟡",
    "ai_generated": "⚠️",
}

PROVENANCE_CATEGORIES = {
    "material": "material",
    "ai_translation": "ai_translation",
    "ai_supplement": "ai_supplement",
    "ai_supplemented": "ai_supplement",
    "ai_generated": "ai_generated",
}

# Include both canonical labels and exact historical spellings.  Some old
# translation labels used the supplement-coloured icon; they still denote the
# typed ``ai_translation`` category.
PROVENANCE_LABEL_VARIANTS = {
    "zh": {
        "material": (
            "🟢 来自资料",
        ),
        "ai_translation": (
            "🌐 AI 翻译（原文来自资料）",
            "🌐 AI翻译，原资料为另一种语言",
            "🟡 AI翻译，原文来自资料",
            "🟡 AI翻译，原资料为另一种语言",
        ),
        "ai_supplement": (
            "🟡 AI补充，可能与你老师讲的不完全一致",
        ),
        "ai_generated": (
            "⚠️ AI生成答案，非老师/教材提供",
        ),
    },
    "en": {
        "material": (
            "🟢 From your materials",
        ),
        "ai_translation": (
            "🌐 AI translation of material evidence",
            "🌐 AI translation — source material is in another language",
            "🟡 AI translation of material evidence",
            "🟡 AI translation — source material is in another language",
        ),
        "ai_supplement": (
            "🟡 AI-supplemented — may differ from what your teacher taught",
            "🟡 AI supplement — may differ from what your teacher taught",
        ),
        "ai_generated": (
            "⚠️ AI-generated answer — not from your teacher or textbook",
        ),
    },
}

NOTEBOOK_LEGEND_MARKER = "<!-- EXAMPREP-PROVENANCE-LEGEND:v1 -->"
_LEGACY_NOTEBOOK_LEGEND_PATTERNS = (
    re.compile(r"来源标识（本章仅说明一次）"),
    re.compile(r"Provenance legend \(shown once for this chapter\)", re.I),
)


def provenance_values(value, code):
    """Flatten one language-scoped sidecar without changing its order."""

    if isinstance(value, str):
        return [value] if value in PROVENANCE_CATEGORIES else []
    if isinstance(value, (list, tuple)):
        output = []
        for item in value:
            output.extend(provenance_values(item, code))
        return output
    if isinstance(value, dict):
        language_keys = set(value) & {"zh", "en"}
        if language_keys and set(value) <= {"zh", "en"}:
            return provenance_values(value.get(code), code)
        output = []
        for item in value.values():
            output.extend(provenance_values(item, code))
        return output
    return []


def provenance_categories(value, code):
    output = []
    for provenance in provenance_values(value, code):
        category = PROVENANCE_CATEGORIES[provenance]
        if not output or output[-1] != category:
            output.append(category)
    return tuple(output)


def _prefix_candidates(code):
    candidates = []
    for provenance, labels in PROVENANCE_LABEL_VARIANTS[code].items():
        for label in labels:
            for prefix in (
                "[%s] " % label,
                "[%s]\n" % label,
                "[%s]：" % label,
                "[%s]: " % label,
                "[%s] — " % label,
                "%s：" % label,
                "%s: " % label,
                "%s — " % label,
                "%s " % label,
            ):
                candidates.append((prefix, provenance))
            candidates.append(("[%s]" % label, provenance))
            candidates.append((label, provenance))
    # Prefer the most explicit/longest spelling when variants share a prefix.
    return sorted(candidates, key=lambda row: len(row[0]), reverse=True)


_PREFIXES = {code: _prefix_candidates(code) for code in ("zh", "en")}


def strip_visible_provenance_prefix(value, code):
    """Return ``(clean_text, inferred_provenance)`` for one visible string.

    A bare canonical label is recognized only when it is the entire value or is
    followed by whitespace/punctuation.  This prevents a coincidental longer
    word from being truncated.
    """

    if not isinstance(value, str):
        return value, None
    for prefix, provenance in _PREFIXES[code]:
        if not value.startswith(prefix):
            continue
        if prefix in PROVENANCE_LABEL_VARIANTS[code].get(provenance, ()):
            if len(value) > len(prefix) and value[len(prefix)] not in " \t\r\n：:—":
                continue
        cleaned = value[len(prefix):]
        if prefix.endswith("：") or prefix.endswith(": ") or prefix.endswith(" — "):
            cleaned = cleaned.lstrip()
        return cleaned, provenance
    return value, None


def clean_visible_provenance(value, code, sidecar=None):
    """Strip a visible prefix and resolve it against the authoritative sidecar.

    The returned provenance list is the sidecar when present, otherwise the
    inferred legacy label.  Any category disagreement fails closed.
    """

    cleaned, inferred = strip_visible_provenance_prefix(value, code)
    if inferred is None:
        alternate = "en" if code == "zh" else "zh"
        alternate_cleaned, alternate_inferred = strip_visible_provenance_prefix(
            value, alternate)
        if alternate_inferred is not None:
            cleaned, inferred = alternate_cleaned, alternate_inferred
    explicit = provenance_values(sidecar, code)
    if inferred is not None and explicit:
        inferred_category = PROVENANCE_CATEGORIES[inferred]
        explicit_categories = provenance_categories(explicit, code)
        if explicit_categories != (inferred_category,):
            raise ProvenanceConflictError(
                "typed provenance sidecar contradicts the visible provenance prefix"
            )
    return cleaned, explicit or ([inferred] if inferred is not None else [])


def notebook_has_provenance_legend(value):
    if not isinstance(value, str):
        return False
    if NOTEBOOK_LEGEND_MARKER in value:
        return True
    return any(pattern.search(value) for pattern in _LEGACY_NOTEBOOK_LEGEND_PATTERNS)


def notebook_legend_lines(language):
    zh = (
        "> **来源标识（本章仅说明一次）：** 🟢 来自资料；"
        "🌐 AI 翻译（原文来自资料）；🟡 AI 补充，可能与你老师讲的不完全一致；"
        "⚠️ AI 生成答案、并非老师或教材答案。"
    )
    en = (
        "**Provenance legend (shown once for this chapter):** 🟢 From your "
        "materials; 🌐 AI translation of material evidence; 🟡 AI supplement — "
        "may differ from what your teacher taught; "
        "⚠️ AI-generated answer, not a teacher/textbook answer."
    )
    if language == "zh":
        return [NOTEBOOK_LEGEND_MARKER, zh]
    if language == "en":
        return [NOTEBOOK_LEGEND_MARKER, "> " + en]
    return [NOTEBOOK_LEGEND_MARKER, zh, "> EN: " + en]


def forbidden_explanation_fragment(value):
    """Return a reason when model-only explanation text leaks UI scaffolding."""

    if not isinstance(value, str):
        return None
    for labels in PROVENANCE_LABEL_VARIANTS.values():
        for variants in labels.values():
            for label in variants:
                if label in value:
                    return "a full provenance label"
    if any(icon in value for icon in set(PROVENANCE_ICONS.values()) | {"⚠"}):
        return "a provenance terminal emoji"
    self_check_patterns = (
        re.compile(r"答案\s*自检"),
        re.compile(r"自检\s*答案"),
        re.compile(r"答案\s*(?:检查|核对|验证|核验)"),
        re.compile(r"(?:检查|核对|验证|核验)\s*答案"),
        re.compile(r"结果\s*自检"),
        re.compile(r"自我检查"),
        re.compile(r"检查\s*答案"),
        re.compile(r"核对\s*答案"),
        re.compile(r"\banswer[ -]?self[ -]?check\b", re.I),
        re.compile(r"\banswer (?:check|verification)\b", re.I),
        re.compile(r"\bself[ -]?check\b", re.I),
        re.compile(r"\b(?:check|verify) (?:the |your |this )?answer\b", re.I),
        re.compile(r"\bsanity check\b", re.I),
    )
    if any(pattern.search(value) for pattern in self_check_patterns):
        return "deprecated answer-self-check content"
    return None
