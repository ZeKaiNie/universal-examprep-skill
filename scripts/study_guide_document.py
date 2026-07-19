#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Render a validated typed chapter manifest as a real study guide. The stricter teaching
# document maps every example once, renders formulas as MathML, avoids duplicated full-prompt
# OCR, and keeps the complete worked-solution contract. The legacy source packet remains.

from collections import Counter
import html
import os
import re
import stat
from pathlib import Path

import exam_start
from study_guide_content import validate_manifest
from math_text_policy import search_visible_latex_command
from study_guide_provenance import (
    PROVENANCE_CATEGORIES as SHARED_PROVENANCE_CATEGORIES,
    PROVENANCE_ICONS as SHARED_PROVENANCE_ICONS,
    PROVENANCE_LABEL_VARIANTS,
    ProvenanceConflictError,
    clean_visible_provenance,
    provenance_values,
    strip_visible_provenance_prefix,
)
from study_guide_render import (
    ArtifactDriftError,
    GuideError,
    MarkdownRenderer,
    _capture_verified_crop_asset_snapshots,
    _resolve_asset,
    _verify_crop_asset_snapshots,
    _workspace_tainted_asset_keys,
    validate_generated_html,
)
from validate_workspace import workspace_asset_policy_snapshot


SOURCE_TYPE_LABELS = {
    "lecture": ("课件 / 讲义", "Lecture / handout"),
    "homework": ("作业", "Homework"),
    "quiz": ("Quiz", "Quiz"),
    "mock_exam": ("模拟考试", "Mock exam"),
    "past_exam": ("往年考试", "Past exam"),
    "textbook": ("教材", "Textbook"),
    "other": ("其他资料", "Other material"),
}
SOURCE_ABSENCE = (
    "当前工作区/资料集中未提供。",
    "Not provided in the current workspace/material set.",
)
SOURCE_ROLE_LABELS = {
    "concept": ("概念依据", "Concept evidence"),
    "formula": ("公式依据", "Formula evidence"),
    "question": ("题面", "Question"),
    "answer": ("答案", "Answer"),
    "solution": ("解答", "Solution"),
}

PROVENANCE_LABELS = {
    "material": ("🟢 来自资料", "🟢 From your materials"),
    "ai_supplemented": ("🟡 AI补充，可能与你老师讲的不完全一致",
                        "🟡 AI-supplemented — may differ from what your teacher taught"),
    "ai_generated": ("⚠️ AI生成答案，非老师/教材提供",
                     "⚠️ AI-generated answer — not from your teacher or textbook"),
    "ai_translation": ("🌐 AI翻译，原资料为另一种语言",
                       "🌐 AI translation — source material is in another language"),
    "ai_supplement": ("🟡 AI补充，可能与你老师讲的不完全一致",
                      "🟡 AI supplement — may differ from what your teacher taught"),
}

PROVENANCE_ICONS = dict(SHARED_PROVENANCE_ICONS)
PROVENANCE_UI_CATEGORY = dict(SHARED_PROVENANCE_CATEGORIES)
PROVENANCE_LEGEND_ORDER = (
    "material", "ai_translation", "ai_supplement", "ai_generated",
)
PROVENANCE_LEGEND_LABELS = {
    "material": PROVENANCE_LABELS["material"],
    "ai_translation": PROVENANCE_LABELS["ai_translation"],
    "ai_supplement": PROVENANCE_LABELS["ai_supplement"],
    "ai_generated": PROVENANCE_LABELS["ai_generated"],
}

LABELS = {
    "title": ("第 {chapter} 章 · 零基础完整教材", "Chapter {chapter} · Complete Beginner Study Guide"),
    "subtitle": ("知识点、公式与全部对应例题逐项精讲", "Knowledge points, formulas, and every mapped example explained step by step"),
    "coverage": ("覆盖证明", "Coverage proof"),
    "coverage_line": ("{kp} 个知识点；{done}/{expected} 道例题；模式：{profile}",
                      "{kp} knowledge points; {done}/{expected} examples; profile: {profile}"),
    "source_inventory": ("例题来源清单", "Example source inventory"),
    "contents": ("本章路线", "Chapter route"),
    "knowledge_point": ("知识点 {index}", "Knowledge point {index}"),
    "plain_explanation": ("先用白话讲懂", "Start with the plain-language idea"),
    "formulas": ("公式与适用条件", "Formulas and when to use them"),
    "formula_meaning": ("公式在说什么", "What the formula means"),
    "applicability": ("什么时候能用", "When it applies"),
    "variables": ("符号", "Symbols"),
    "symbol": ("符号", "Symbol"),
    "meaning": ("含义", "Meaning"),
    "mapped_examples": ("对应例题", "Mapped examples"),
    "example": ("例题 {index}", "Worked example {index}"),
    "source_type": ("资料类型", "Source type"),
    "original_prompt": ("原题", "Original prompt"),
    "prompt_asset": ("题面图", "Question-side asset"),
    "answer_asset": ("答案图", "Answer-side asset"),
    "concept_asset": ("知识点原资料图", "Knowledge-point source asset"),
    "translation": ("题面翻译", "Prompt translation"),
    "prompt_step": ("① 先看完整题面", "① Start with the complete prompt"),
    "what_asked": ("② 题目到底在问什么", "② What is the question asking?"),
    "quantities": ("③ 读出已知量和未知量", "③ Read the knowns and unknowns"),
    "known": ("已知量", "Known quantities"),
    "unknown": ("未知量", "Unknown quantities"),
    "formula_use": ("④ 选公式：为什么能用", "④ Choose the formula: why it applies"),
    "mapping": ("对号入座：符号对应题目里的什么", "Map each symbol to the prompt"),
    "substitution": ("代入数字 / 条件", "Substitute values / conditions"),
    "steps": ("⑤ 逐步计算", "⑤ Work through the calculation"),
    "answer": ("答案", "Answer"),
    "answer_explanation": ("⑥ 为什么这个答案成立", "⑥ Why this answer works"),
    "source_trace": ("⑦ 来源追踪", "⑦ Source trace"),
    "source_evidence": ("来源证据", "Source evidence"),
    "page": ("第", "Page"),
    "also_tests": ("这道题同时用到", "This example also uses"),
    "omissions": ("省略清单", "Omission ledger"),
    "omission_reason": ("省略原因", "Reason omitted"),
    "no_formula": ("这个知识点没有可直接套用的公式，重点是概念或步骤。",
                   "This knowledge point has no direct formula; focus on the concept or procedure."),
    "no_formula_example": ("这道题不需要套公式：按概念、定义或确定性步骤作答。",
                            "This example uses no formula: answer from the concept, definition, or deterministic procedure."),
    "why_no_formula": ("④ 为什么这题不用公式", "④ Why this problem needs no formula"),
    "solution_kind": ("解题类型", "Solution kind"),
    "kp_use": ("这个知识点在题里怎么用", "How this knowledge point is used"),
    "semantic_exclusions": ("未纳入教学的语义单元", "Semantic units excluded from teaching"),
    "semantic_exclusion_reason": ("排除原因", "Reason excluded"),
}


def _label(language, key, **values):
    zh, en = LABELS[key]
    if language == "zh":
        return zh.format(**values)
    if language == "en":
        return en.format(**values)
    return "%s / %s" % (zh.format(**values), en.format(**values))


def _strip_legacy_provenance(value, code):
    return strip_visible_provenance_prefix(value, code)


def _provenance_codes(value, code):
    """Flatten one provenance sidecar in display order for a language."""
    return provenance_values(value, code)


def _provenance_marker(value, code):
    categories = []
    for provenance in _provenance_codes(value, code):
        category = PROVENANCE_UI_CATEGORY[provenance]
        # Collapse only a consecutive run. A -> B -> A remains visible as
        # three runs, while A -> A -> A emits one marker.
        if not categories or categories[-1] != category:
            categories.append(category)
    if not categories:
        return ""
    icons = "".join(PROVENANCE_ICONS[category] for category in categories)
    return ('<span class="provenance-marker" data-provenance="%s" '
            'aria-label="provenance marker">%s</span>') % (
                html.escape(" ".join(categories), quote=True), html.escape(icons))


def _resolved_provenance(explicit, inferred, code):
    """Compatibility wrapper for callers that already separated the prefix."""

    explicit_codes = _provenance_codes(explicit, code)
    inferred_codes = _provenance_codes(inferred, code)
    if explicit_codes and inferred_codes:
        explicit_categories = tuple(
            PROVENANCE_UI_CATEGORY[value] for value in explicit_codes)
        inferred_categories = tuple(
            PROVENANCE_UI_CATEGORY[value] for value in inferred_codes)
        if explicit_categories != inferred_categories:
            raise GuideError(
                "typed provenance sidecar contradicts the visible provenance prefix",
                2,
            )
    return explicit_codes or inferred_codes


def _provenance_signature(value, code):
    categories = []
    for provenance in _provenance_codes(value, code):
        category = PROVENANCE_UI_CATEGORY[provenance]
        if not categories or categories[-1] != category:
            categories.append(category)
    return tuple(categories)


def _run_terminal_provenance(values, index):
    """Return language-scoped provenance only at the end of a same-label run."""

    current = values[index]
    following = values[index + 1] if index + 1 < len(values) else None
    terminal = {}
    for code in ("zh", "en"):
        signature = _provenance_signature(current, code)
        if signature and signature != _provenance_signature(following, code):
            terminal[code] = list(_provenance_codes(current, code))
    return terminal or None


def _append_terminal_marker(rendered, marker):
    if not marker:
        return rendered
    marker = "&#8239;" + marker
    candidates = []
    for closing in ("</p>", "</li>", "</td>"):
        position = rendered.rfind(closing)
        if position >= 0:
            candidates.append((position, closing))
    if not candidates:
        return rendered + marker
    position, closing = max(candidates, key=lambda row: row[0])
    return rendered[:position] + marker + rendered[position:]


def _collect_used_provenance(value):
    used = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if "provenance" in key:
                for code in ("zh", "en"):
                    used.update(_provenance_codes(item, code))
            else:
                used.update(_collect_used_provenance(item))
    elif isinstance(value, list):
        for item in value:
            used.update(_collect_used_provenance(item))
    return used


def _collect_legacy_provenance(value):
    used = set()
    if isinstance(value, str):
        for code in ("zh", "en"):
            unused_text, inferred = _strip_legacy_provenance(value, code)
            if inferred:
                used.add(inferred)
    elif isinstance(value, dict):
        for item in value.values():
            used.update(_collect_legacy_provenance(item))
    elif isinstance(value, list):
        for item in value:
            used.update(_collect_legacy_provenance(item))
    return used


def _render_provenance_legend(manifest, language):
    categories = {"material"}
    categories.update({
        PROVENANCE_UI_CATEGORY[value]
        for value in _collect_used_provenance(manifest)
        if value in PROVENANCE_UI_CATEGORY
    })
    categories.update(
        PROVENANCE_UI_CATEGORY[value]
        for value in _collect_legacy_provenance(manifest)
        if value in PROVENANCE_UI_CATEGORY
    )
    if not categories:
        return ""
    rows = []
    for category in PROVENANCE_LEGEND_ORDER:
        if category not in categories:
            continue
        zh, en = PROVENANCE_LEGEND_LABELS[category]
        if language == "zh":
            content = html.escape(zh)
        elif language == "en":
            content = html.escape(en)
        else:
            content = '%s <span class="legend-en" lang="en">EN · %s</span>' % (
                html.escape(zh), html.escape(en))
        rows.append('<li data-provenance="%s">%s</li>' % (
            html.escape(category, quote=True), content))
    title = ("来源标识说明" if language == "zh" else "Provenance legend"
             if language == "en" else "来源标识说明 / Provenance legend")
    note = (
        "连续、同来源的段落只在这一段连续内容的末尾标一次；来源类别变化时才开始新的标识段。"
        if language == "zh" else
        "Consecutive paragraphs with the same provenance are marked once at the end of the run; a provenance change starts a new run."
        if language == "en" else
        "连续、同来源的段落只在这一段连续内容的末尾标一次；来源类别变化时才开始新的标识段。 "
        "EN · Consecutive paragraphs with the same provenance are marked once at the end of the run; a provenance change starts a new run."
    )
    return ('<aside id="provenance-legend" class="provenance-legend">'
            '<strong>%s</strong><ul>%s</ul><p>%s</p></aside>') % (
                html.escape(title), "".join(rows), html.escape(note))


def _render_localized_markdown(renderer, value, *, english_prefix=False):
    """Render a localized block without leaving the bilingual prefix orphaned.

    Markdown paragraphs are block elements.  Placing ``EN ·`` before the first
    ``<p>`` lets a print engine split the prefix away from its sentence.  Put
    the prefix inside the first paragraph when one exists; non-paragraph
    Markdown (for example a display equation) keeps the conservative fallback.
    """

    rendered = renderer.render(value)
    if not english_prefix:
        return rendered
    prefix = '<span class="en-prefix">EN · </span>'
    match = re.search(r"<p(?:\s[^>]*)?>", rendered)
    if match:
        return rendered[:match.end()] + prefix + rendered[match.end():]
    return prefix + rendered


def _source_inventory(walkthroughs, language):
    counts = Counter(row["source_type"] for row in walkthroughs)
    lines = []
    for code in (("zh", "en") if language == "bilingual" else (language,)):
        en = code == "en"
        items = []
        for source_type, labels in SOURCE_TYPE_LABELS.items():
            count = counts.get(source_type, 0)
            if not count and source_type not in ("mock_exam", "past_exam"):
                continue
            value = count or "0 — %s" % SOURCE_ABSENCE[en]
            items.append(html.escape("%s%s%s" % (
                labels[en], ": " if en else "：", value)))
        lines.append('<span lang="%s">%s<strong>%s</strong> %s</span>' % (
            code,
            '<span class="en-prefix">EN · </span>' if en and language == "bilingual" else "",
            html.escape(LABELS["source_inventory"][en]), " · ".join(items),
        ))
    return '<p class="source-inventory">%s</p>' % "<br>".join(lines)


_PROVENANCE_UNSET = object()


def _clean_document_visible(value, code, sidecar=None):
    try:
        return clean_visible_provenance(value, code, sidecar)
    except ProvenanceConflictError as exc:
        raise GuideError(str(exc), 2) from exc


def _document_visible_provenance(value, sidecar=None):
    """Resolve sidecar/inline compatibility labels before run collapsing."""

    localized = value if isinstance(value, dict) else {"zh": value, "en": value}
    resolved_by_language = {}
    for code in ("zh", "en"):
        if code not in localized:
            continue
        unused_cleaned, resolved = _clean_document_visible(
            localized[code], code, sidecar)
        if resolved:
            resolved_by_language[code] = resolved
    return resolved_by_language or sidecar


def _localized_text(renderer, value, language, css="localized", provenance=None,
                    source_provenance=_PROVENANCE_UNSET):
    parts = []
    has_source_provenance = (
        source_provenance is not _PROVENANCE_UNSET
        and source_provenance is not None)
    if language in ("zh", "bilingual"):
        authoritative = (
            provenance if not has_source_provenance
            else source_provenance)
        cleaned, resolved = _clean_document_visible(
            value["zh"], "zh", authoritative)
        display = (
            resolved if not has_source_provenance
            and provenance is None else provenance)
        rendered_zh = _render_localized_markdown(renderer, cleaned)
        rendered_zh = _append_terminal_marker(
            rendered_zh,
            _provenance_marker(display, "zh"),
        )
        parts.append('<div class="%s lang-zh" lang="zh-CN">%s</div>' %
                     (css, rendered_zh))
    if language in ("en", "bilingual"):
        authoritative = (
            provenance if not has_source_provenance
            else source_provenance)
        cleaned, resolved = _clean_document_visible(
            value["en"], "en", authoritative)
        display = (
            resolved if not has_source_provenance
            and provenance is None else provenance)
        rendered_en = _render_localized_markdown(
            renderer, cleaned, english_prefix=language == "bilingual")
        rendered_en = _append_terminal_marker(
            rendered_en,
            _provenance_marker(display, "en"),
        )
        parts.append('<div class="%s lang-en" lang="en">%s</div>' %
                     (css, rendered_en))
    rendered = "".join(parts)
    if language == "bilingual":
        return '<div class="localized-pair">%s</div>' % rendered
    return rendered


def _localized_heading(value, language):
    if language == "zh":
        return html.escape(value["zh"])
    if language == "en":
        return html.escape(value["en"])
    return ('<span class="localized-heading"><span lang="zh-CN">%s</span>'
            '<span class="heading-en" lang="en">EN · %s</span></span>') % (
                html.escape(value["zh"]), html.escape(value["en"]))


def _provenance(language, value):
    marker_zh = _provenance_marker(value, "zh")
    marker_en = _provenance_marker(value, "en")
    if language == "zh":
        return marker_zh
    if language == "en":
        return marker_en
    return ('<span class="provenance-pair"><span lang="zh-CN">%s</span>'
            '<span lang="en">%s</span></span>') % (marker_zh, marker_en)


def _provenance_run_block(value, language, css="provenance-run"):
    if language == "zh":
        marker = _provenance_marker(value, "zh")
        return ('<p class="%s" lang="zh-CN">%s</p>' % (css, marker)) if marker else ""
    if language == "en":
        marker = _provenance_marker(value, "en")
        return ('<p class="%s" lang="en">%s</p>' % (css, marker)) if marker else ""
    zh = _provenance_marker(value, "zh")
    en = _provenance_marker(value, "en")
    if not zh and not en:
        return ""
    return ('<p class="%s localized-pair"><span lang="zh-CN">%s</span>'
            '<span class="lang-en" lang="en">%s</span></p>') % (css, zh, en)


def _answer_provenance_map(value, language):
    if isinstance(value, dict):
        return value
    codes = ("zh", "en") if language == "bilingual" else (language,)
    return {code: value for code in codes}


def _answer_blocks(renderer, answer, provenance, language):
    provenance = _answer_provenance_map(provenance, language)
    parts = []
    codes = ("zh", "en") if language == "bilingual" else (language,)
    for code in codes:
        cleaned, resolved = _clean_document_visible(
            answer[code], code, provenance.get(code))
        rendered_answer = _render_localized_markdown(
            renderer, cleaned,
            english_prefix=code == "en" and language == "bilingual")
        rendered_answer = _append_terminal_marker(
            rendered_answer,
            _provenance_marker(resolved, code),
        )
        parts.append(
            '<section class="answer-language lang-%s" lang="%s">'
            '<div class="localized">%s</div></section>'
            % (code, "zh-CN" if code == "zh" else "en", rendered_answer)
        )
    rendered = "".join(parts)
    if language == "bilingual":
        return '<div class="answer-pair">%s</div>' % rendered
    return rendered


def _explanation_blocks(renderer, knowledge_point, language):
    explanation = knowledge_point["explanation"]
    provenance = knowledge_point.get("explanation_provenance") or {
        code: "material" for code in explanation
    }
    parts = []
    for code in (("zh", "en") if language == "bilingual" else (language,)):
        cleaned, resolved = _clean_document_visible(
            explanation[code], code, provenance.get(code))
        rendered_explanation = _render_localized_markdown(
            renderer, _collapse_soft_line_breaks(cleaned),
            english_prefix=code == "en" and language == "bilingual")
        rendered_explanation = _append_terminal_marker(
            rendered_explanation,
            _provenance_marker(resolved, code),
        )
        parts.append(
            '<section class="explanation-language lang-%s" lang="%s">'
            '<div class="localized">%s</div></section>'
            % (code, "zh-CN" if code == "zh" else "en", rendered_explanation)
        )
    rendered = "".join(parts)
    if language == "bilingual":
        return '<div class="localized-pair">%s</div>' % rendered
    return rendered


def _collapse_soft_line_breaks(value):
    """Collapse OCR/PDF soft wraps while preserving intentional paragraphs."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n\s*\n", normalized)
    return "\n\n".join(
        " ".join(line.strip() for line in paragraph.split("\n") if line.strip())
        for paragraph in paragraphs
        if paragraph.strip()
    )


def _teaching_explanation_blocks(renderer, knowledge_point, language):
    explanation = knowledge_point["teaching_explanation"]
    provenance = knowledge_point["teaching_explanation_provenance"]
    parts = []
    for code in (("zh", "en") if language == "bilingual" else (language,)):
        cleaned, resolved = _clean_document_visible(
            explanation[code], code, provenance.get(code))
        rendered_explanation = _render_localized_markdown(
            renderer, cleaned,
            english_prefix=code == "en" and language == "bilingual")
        rendered_explanation = _append_terminal_marker(
            rendered_explanation,
            _provenance_marker(resolved, code),
        )
        parts.append(
            '<section class="explanation-language teaching-explanation lang-%s" lang="%s">'
            '<div class="localized">%s</div></section>'
            % (code, "zh-CN" if code == "zh" else "en", rendered_explanation)
        )
    rendered = "".join(parts)
    if language == "bilingual":
        return '<div class="localized-pair">%s</div>' % rendered
    return rendered


def _asset_figure(workspace, relative, label, language, index,
                  student_attempt_tainted_keys=None,
                  verified_crop_snapshots=None):
    verified_snapshot = (
        verified_crop_snapshots.get(relative)
        if isinstance(verified_crop_snapshots, dict) else None
    )
    data_uri = _resolve_asset(
        workspace, relative, "%s %d" % (label, index),
        student_attempt_tainted_keys=student_attempt_tainted_keys,
        taint_message=(
            "%s asset is bound to student_attempt evidence and cannot be rendered: %%s"
            % label
        ),
        verified_asset_snapshot=verified_snapshot,
    )
    caption = "%s %d · %s" % (_label(language, label), index, relative)
    return ('<figure class="source-asset"><img src="%s" alt="%s">'
            '<figcaption>%s</figcaption></figure>') % (
                data_uri, html.escape(caption, quote=True), html.escape(caption))


def _knowledge_assets(workspace, refs, language, student_attempt_tainted_keys=None,
                      verified_crop_snapshots=None):
    paths = []
    seen = set()
    for ref in refs:
        path = ref.get("asset_path")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    if not paths:
        return ""
    return '<div class="knowledge-assets">%s</div>' % "".join(
        _asset_figure(
            workspace, path, "concept_asset", language, index,
            student_attempt_tainted_keys=student_attempt_tainted_keys,
            verified_crop_snapshots=verified_crop_snapshots,
        )
        for index, path in enumerate(paths, 1)
    )


def _is_link_or_reparse(path):
    if os.path.islink(path):
        return True
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _material_location_href(materials_root, source_file, location):
    """Return the canonical URI for one confirmed source location.

    A source path in the manifest is relative to the separately confirmed materials root,
    not to ``study_guide/chNN.html``.  If that binding cannot be proved, rendering fails
    closed.  Only PDF has a portable location fragment in this framework; PPTX/XLSX/DOCX
    anchors remain honest slide/worksheet/logical-segment labels linked to the source file.
    """
    if not isinstance(materials_root, str) or not materials_root:
        raise GuideError("typed Study Guide source links require the confirmed materials root", 1)
    lexical_root = os.path.abspath(materials_root)
    if not os.path.isdir(lexical_root) or _is_link_or_reparse(lexical_root):
        raise GuideError("confirmed materials root is missing or is a symlink/reparse point", 1)
    target = os.path.abspath(os.path.join(lexical_root, *source_file.split("/")))
    # Classify a hostile component before resolving it.  A link that escapes the materials root
    # is still primarily a symlink/junction/reparse violation; checking containment first hides
    # that evidence behind a generic "escapes" result and makes platform behavior inconsistent.
    cursor = lexical_root
    for part in source_file.split("/"):
        cursor = os.path.join(cursor, part)
        if os.path.lexists(cursor) and _is_link_or_reparse(cursor):
            raise GuideError("material source path crosses a symlink/junction/reparse point: %s"
                             % source_file, 1)
    root = os.path.realpath(lexical_root)
    target_real = os.path.realpath(target)
    try:
        contained = (os.path.commonpath((lexical_root, target)) == lexical_root
                     and os.path.commonpath((root, target_real)) == root)
    except ValueError:
        contained = False
    if not contained:
        raise GuideError("material source path escapes the confirmed materials root: %s"
                         % source_file, 1)
    if not os.path.isfile(target) or not os.path.isfile(target_real):
        raise GuideError("material source file is missing or not a regular file: %s"
                         % source_file, 1)
    href = Path(target_real).resolve().as_uri()
    if Path(source_file).suffix.lower() == ".pdf":
        href += "#page=%d" % location
    return href


def _source_location_label(source_file, location, language):
    suffix = Path(source_file).suffix.lower()
    labels = {
        ".pdf": ("第 %d 页", "page %d"),
        ".pptx": ("第 %d 张幻灯片", "slide %d"),
        ".ppt": ("第 %d 张幻灯片", "slide %d"),
        ".xlsx": ("第 %d 个工作表", "worksheet %d"),
        ".xls": ("第 %d 个工作表", "worksheet %d"),
        ".docx": ("第 %d 个逻辑段", "logical segment %d"),
        ".doc": ("第 %d 个逻辑段", "logical segment %d"),
    }
    zh, en = labels.get(suffix, ("位置 %d", "location %d"))
    if language == "zh":
        return zh % location
    if language == "en":
        return en % location
    return "%s / %s" % (zh % location, en % location)


def _source_ref(renderer, ref, language, materials_root=None):
    location_links = []
    if not ref.get("pages"):
        raise GuideError("typed Study Guide source refs require at least one source location", 1)
    for location in ref.get("pages", []):
        href = _material_location_href(
            materials_root, ref["source_file"], location)
        label = _source_location_label(
            ref["source_file"], location, language)
        location_links.append('<a href="%s">%s</a>' % (
            html.escape(href, quote=True), html.escape(label)))
    chunks = [html.escape(ref["source_file"]), ", ".join(location_links) or "—"]
    role = ref.get("role")
    if role:
        labels = SOURCE_ROLE_LABELS.get(role)
        chunks.append(html.escape(
            labels[0] if language == "zh" else labels[1] if language == "en"
            else "%s / %s" % labels) if labels else html.escape(role))
    # Exact quote spans and source-unit hashes remain in the typed manifest and
    # claim receipt.  Reprinting long OCR question/answer excerpts in a student
    # handout duplicates the prompt and can turn flattened fractions into false
    # mathematics.  Formula quotes are short, structured TeX and remain useful.
    if ref.get("quote_span") and role == "formula":
        quote = renderer.render_inline_math(ref["quote_span"])
        chunks.append("“%s”" % quote)
    return " · ".join(chunks)


def _source_trace(renderer, refs, language, materials_root=None):
    rows = []
    seen = set()
    for ref in refs:
        key = (
            ref.get("source_file"), tuple(ref.get("pages") or ()), ref.get("role"),
            ref.get("quote_span") if ref.get("role") == "formula" else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unit_id = html.escape(ref.get("source_unit_id", ""), quote=True)
        rows.append('<li data-source-unit-id="%s">%s</li>' % (
            unit_id, _source_ref(renderer, ref, language, materials_root)))
    return "<ul class=\"source-list\">%s</ul>" % "".join(rows)


def _quantity(renderer, quantity, language, provenance=None,
              source_provenance=_PROVENANCE_UNSET):
    suffix = []
    if quantity.get("symbol"):
        suffix.append(renderer.render_inline_math(quantity["symbol"]))
    if quantity.get("value"):
        suffix.append("<strong>%s</strong>" % html.escape(quantity["value"]))
    if quantity.get("unit"):
        suffix.append(html.escape(quantity["unit"]))
    parts = []
    has_source_provenance = (
        source_provenance is not _PROVENANCE_UNSET
        and source_provenance is not None)
    codes = ("zh", "en") if language == "bilingual" else (language,)
    for code in codes:
        authoritative = (
            provenance if not has_source_provenance
            else source_provenance)
        cleaned, resolved = _clean_document_visible(
            quantity["label"][code], code, authoritative)
        display = (
            resolved if not has_source_provenance
            and provenance is None else provenance)
        marker = _provenance_marker(display, code)
        label_html = _render_localized_markdown(
            renderer, cleaned,
            english_prefix=code == "en" and language == "bilingual")
        if suffix:
            detail = '<p class="quantity-value">%s%s</p>' % (
                " · ".join(suffix), ("&#8239;" + marker) if marker else "")
            body = label_html + detail
        else:
            body = _append_terminal_marker(label_html, marker)
        parts.append('<div class="localized compact lang-%s" lang="%s">%s</div>' % (
            code, "zh-CN" if code == "zh" else "en", body))
    rendered = "".join(parts)
    if language == "bilingual":
        rendered = '<div class="localized-pair">%s</div>' % rendered
    return '<li>%s</li>' % rendered


def _formula(renderer, formula, language, materials_root=None):
    formula_provenance = [
        _document_visible_provenance(
            formula["explanation"], formula.get("explanation_provenance")),
        _document_visible_provenance(
            formula["applicability"], formula.get("applicability_provenance")),
    ]
    formula_provenance.extend(
        _document_visible_provenance(
            row["meaning"], row.get("meaning_provenance"))
        for row in formula["variables"])
    variables = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (
            renderer.render_inline_math(variable["symbol"]),
            _localized_text(
                renderer, variable["meaning"], language, "localized compact",
                provenance=_run_terminal_provenance(
                    formula_provenance, index + 2),
                source_provenance=variable.get("meaning_provenance"),
            ),
        ) for index, variable in enumerate(formula["variables"])
    )
    return "".join([
        '<article class="formula-card" data-formula-id="%s">' % html.escape(formula["id"], quote=True),
        '<div class="formula-intro"><div class="formula-display">%s</div>'
        '<h5>%s</h5>%s</div>' % (
            renderer.render("$$%s$$" % formula["latex"]),
            html.escape(_label(language, "formula_meaning")),
            _localized_text(
                renderer, formula["explanation"], language,
                provenance=_run_terminal_provenance(formula_provenance, 0),
                source_provenance=formula.get("explanation_provenance"),
            ),
        ),
        '<h5>%s</h5>' % html.escape(_label(language, "applicability")),
        _localized_text(
            renderer, formula["applicability"], language,
            provenance=_run_terminal_provenance(formula_provenance, 1),
            source_provenance=formula.get("applicability_provenance"),
        ),
        ('<table class="variables"><thead><tr><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>' %
         (html.escape(_label(language, "symbol")), html.escape(_label(language, "meaning")), variables))
        if variables else "",
        '<div class="source-box"><strong>%s</strong>%s</div>' %
        (html.escape(_label(language, "source_evidence")),
         _source_trace(renderer, formula["source_refs"], language, materials_root)),
        '</article>',
    ])


def _split_substitution_provenance(value):
    suffixes = (
        r"\quad\text{AI补充 / AI-supplemented}",
        r"\quad\text{AI补充}",
        r"\quad\text{AI-supplemented}",
    )
    for suffix in suffixes:
        if value.endswith(suffix):
            return value[:-len(suffix)], "ai_supplement"
    return value, None


def _formula_use(renderer, use, language, formula_lookup):
    formula = formula_lookup[use["formula_id"]]
    substitution, substitution_provenance = _split_substitution_provenance(
        use["substitution"])
    substitution_code = language if language in ("zh", "en") else "zh"
    substitution, unused_visible_substitution = _clean_document_visible(
        substitution, substitution_code, use.get("substitution_provenance"))
    use_provenance = [
        _document_visible_provenance(
            use["why_applicable"], use.get("why_applicable_provenance"))]
    use_provenance.extend(
        _document_visible_provenance(
            row["maps_to"], row.get("maps_to_provenance"))
        for row in use["variable_mapping"])
    use_provenance.append(
        use.get("substitution_provenance") or substitution_provenance
        or unused_visible_substitution)
    mappings = "".join(
        '<tr><td>%s</td><td>%s</td></tr>' % (
            renderer.render_inline_math(row["symbol"]),
            _localized_text(
                renderer, row["maps_to"], language, "localized compact",
                provenance=_run_terminal_provenance(
                    use_provenance, index + 1),
                source_provenance=row.get("maps_to_provenance"),
            ),
        ) for index, row in enumerate(use["variable_mapping"])
    )
    explicit_substitution = use_provenance[-1]
    authoritative_substitution = use.get("substitution_provenance")
    for code in ("zh", "en"):
        _resolved_provenance(
            authoritative_substitution, substitution_provenance, code)
        _resolved_provenance(
            authoritative_substitution or substitution_provenance,
            unused_visible_substitution,
            code,
        )
    substitution_marker = _provenance(
        language,
        _run_terminal_provenance(use_provenance, len(use_provenance) - 1)
        if explicit_substitution else {
            code: [] for code in ("zh", "en")
        },
    )
    return "".join([
        '<section class="formula-use">',
        '<h4>%s</h4>' % html.escape(_label(language, "formula_use")),
        '<div class="formula-display small">%s</div>' % renderer.render("$$%s$$" % formula["latex"]),
        _localized_text(
            renderer, use["why_applicable"], language,
            provenance=_run_terminal_provenance(use_provenance, 0),
            source_provenance=use.get("why_applicable_provenance"),
        ),
        '<h5>%s</h5>' % html.escape(_label(language, "mapping")),
        ('<table><tbody>%s</tbody></table>' % mappings) if mappings else '<p>—</p>',
        '<h5>%s</h5>' % html.escape(_label(language, "substitution")),
        '<div class="substitution"><div class="formula-display substitution-math">%s%s</div></div>' % (
            renderer.render("$$%s$$" % substitution),
            ("&#8239;" + substitution_marker) if substitution_marker else "",
        ),
        '</section>',
    ])


def _walkthrough(renderer, workspace, walk, index, language, formula_lookup, kp_lookup,
                 materials_root=None, student_attempt_tainted_keys=None,
                 verified_crop_snapshots=None):
    prompt_asset_figures = [
        _asset_figure(
            workspace, path, "prompt_asset", language, asset_index,
            student_attempt_tainted_keys=student_attempt_tainted_keys,
            verified_crop_snapshots=verified_crop_snapshots,
        )
        for asset_index, path in enumerate(walk["prompt_asset_paths"], 1)
    ]
    prompt_heading = '<h4>%s</h4>' % html.escape(_label(language, "prompt_step"))
    if prompt_asset_figures:
        prompt_assets = (
            '<div class="prompt-intro">%s%s</div>%s' % (
                prompt_heading, prompt_asset_figures[0],
                "".join(prompt_asset_figures[1:]),
            )
        )
    else:
        prompt_assets = prompt_heading
    answer_assets = "".join(
        _asset_figure(
            workspace, path, "answer_asset", language, asset_index,
            student_attempt_tainted_keys=student_attempt_tainted_keys,
            verified_crop_snapshots=verified_crop_snapshots,
        )
        for asset_index, path in enumerate(walk["answer_asset_paths"], 1)
    )
    prompt = ""
    if walk["prompt_asset_mode"] != "full_prompt":
        prompt_code = (
            walk["original_language"]
            if walk["original_language"] in ("zh", "en")
            else (language if language in ("zh", "en") else "zh")
        )
        cleaned_prompt, prompt_provenance = _clean_document_visible(
            walk["prompt_text"], prompt_code, "material")
        if (cleaned_prompt == walk["prompt_text"]
                and walk["original_language"] in ("mixed", "unknown")):
            alternate = "en" if prompt_code == "zh" else "zh"
            alternate_cleaned, alternate_provenance = _clean_document_visible(
                walk["prompt_text"], alternate, "material")
            if alternate_cleaned != walk["prompt_text"]:
                cleaned_prompt = alternate_cleaned
                prompt_provenance = alternate_provenance
                prompt_code = alternate
        rendered_prompt = _append_terminal_marker(
            renderer.render(cleaned_prompt),
            _provenance_marker(prompt_provenance, prompt_code),
        )
        prompt = '<div class="original-prompt"><h4>%s</h4>%s</div>' % (
            html.escape(_label(language, "original_prompt")), rendered_prompt)
    translations = []
    source_languages = {
        "zh": {"zh"}, "en": {"en"}, "mixed": {"zh", "en"}, "unknown": set(),
    }[walk["original_language"]]
    target_languages = ({"zh", "en"} if language == "bilingual" else {language})
    for code in ("zh", "en"):
        if code not in target_languages - source_languages:
            continue
        if code in walk["translation"]:
            tag = "zh-CN" if code == "zh" else "en"
            prefix = "中文" if code == "zh" else "EN"
            cleaned, resolved = _clean_document_visible(
                walk["translation"][code], code,
                walk.get("translation_provenance", {}).get(code),
            )
            translated = _append_terminal_marker(
                renderer.render(cleaned),
                _provenance_marker(resolved, code),
            )
            translations.append('<div class="translation" lang="%s"><strong>%s · %s</strong>%s</div>' % (
                tag, prefix, html.escape(_label(language, "translation")),
                translated))
    known_provenance = [
        _document_visible_provenance(row["label"], row.get("provenance"))
        for row in walk["known_quantities"]]
    unknown_provenance = [
        _document_visible_provenance(row["label"], row.get("provenance"))
        for row in walk["unknown_quantities"]]
    known = "".join(
        _quantity(
            renderer, row, language,
            _run_terminal_provenance(known_provenance, index),
            source_provenance=row.get("provenance"),
        )
        for index, row in enumerate(walk["known_quantities"])
    )
    unknown = "".join(
        _quantity(
            renderer, row, language,
            _run_terminal_provenance(unknown_provenance, index),
            source_provenance=row.get("provenance"),
        )
        for index, row in enumerate(walk["unknown_quantities"])
    )
    formula_uses = "".join(
        _formula_use(renderer, row, language, formula_lookup) for row in walk["formula_uses"]
    )
    if not formula_uses:
        reason = walk.get("no_formula_reason")
        formula_uses = '<section class="notice no-formula-use"><h4>%s</h4>%s</section>' % (
            html.escape(_label(language, "why_no_formula")),
            _localized_text(
                renderer, reason, language,
                provenance=walk.get("no_formula_reason_provenance"),
            ) if reason else html.escape(
                _label(language, "no_formula_example")),
        )
    raw_step_provenance = list(walk.get("steps_provenance") or ())
    step_provenance = [
        _document_visible_provenance(
            step, raw_step_provenance[index]
            if index < len(raw_step_provenance) else None)
        for index, step in enumerate(walk["steps"])
    ]
    steps = "".join(
        '<li>%s</li>' % _localized_text(
            renderer, step, language, "localized compact",
            provenance=_run_terminal_provenance(
                step_provenance, step_index),
            source_provenance=step_provenance[step_index],
        )
        for step_index, step in enumerate(walk["steps"])
    )
    source_zh, source_en = SOURCE_TYPE_LABELS[walk["source_type"]]
    source_label = source_zh if language == "zh" else source_en if language == "en" else "%s / %s" % (source_zh, source_en)
    linked = " · ".join(
        kp_lookup[kp_id]["title"].get(language if language != "bilingual" else "zh", kp_id)
        for kp_id in walk["knowledge_point_ids"]
    )
    kp_uses = []
    kp_use_provenance = walk.get("knowledge_point_uses_provenance") or {}
    aggregate_kp_provenance = [
        _document_visible_provenance(
            (walk.get("knowledge_point_uses") or {}).get(kp_id)
            or kp_lookup[kp_id]["explanation"],
            kp_use_provenance.get(kp_id),
        ) for kp_id in walk["knowledge_point_ids"]
    ]
    for kp_index, kp_id in enumerate(walk["knowledge_point_ids"]):
        usage = (walk.get("knowledge_point_uses") or {}).get(kp_id)
        if usage is None:
            usage = kp_lookup[kp_id]["explanation"]
        kp_uses.append('<li><strong>%s</strong>%s</li>' % (
            _localized_heading(kp_lookup[kp_id]["title"], language),
            _localized_text(
                renderer, usage, language, "localized compact",
                provenance=_run_terminal_provenance(
                    aggregate_kp_provenance, kp_index),
                source_provenance=kp_use_provenance.get(kp_id),
            ),
        ))
    solution_kind = walk.get("solution_kind") or (
        "formula" if walk.get("formula_uses") else "concept")
    display_answer = walk.get("teaching_answer", walk["answer"])
    display_answer_provenance = walk.get(
        "teaching_answer_provenance", walk["answer_provenance"])
    answer_explanation = walk.get("answer_explanation")
    explanation_panel = ""
    if answer_explanation:
        explanation_panel = '<div class="answer-explanation"><h4>%s</h4>%s</div>' % (
            html.escape(_label(language, "answer_explanation")),
            _localized_text(
                renderer, answer_explanation, language,
                provenance=walk.get("answer_explanation_provenance", "ai_supplement"),
            ),
        )
    return "".join([
        '<article class="example-card" id="example-%s" data-item-id="%s" data-source-type="%s">' %
        (html.escape(walk["item_id"], quote=True), html.escape(walk["item_id"], quote=True),
         html.escape(walk["source_type"], quote=True)),
        '<header class="example-header"><p class="eyebrow">%s · %s</p><h3>%s</h3>' % (
            html.escape(_label(language, "example", index=index)), html.escape(source_label),
            _localized_heading(walk["title"], language)),
        '<p class="kp-links"><strong>%s：</strong>%s</p></header>' %
        (html.escape(_label(language, "also_tests")), html.escape(linked)),
        '<section class="prompt-zone">%s%s%s</section>' %
        (prompt_assets, prompt, "".join(translations)),
        '<section class="kp-uses"><h4>%s</h4><ul>%s</ul></section>' % (
            html.escape(_label(language, "kp_use")), "".join(kp_uses)),
        '<section class="walkthrough-zone">',
        '<h4>%s</h4>%s' % (html.escape(_label(language, "what_asked")),
                            _localized_text(
                                renderer, walk["what_asked"], language,
                                provenance=walk.get("what_asked_provenance"),
                            )),
        '<h4>%s</h4><div class="quantity-grid"><div><h5>%s</h5><ul>%s</ul></div>'
        '<div><h5>%s</h5><ul>%s</ul></div></div>' % (
            html.escape(_label(language, "quantities")), html.escape(_label(language, "known")),
            known or "<li>—</li>", html.escape(_label(language, "unknown")), unknown or "<li>—</li>"),
        formula_uses,
        '<h4>%s</h4><ol class="solution-steps">%s</ol>' %
        (html.escape(_label(language, "steps")), steps),
        '<div class="final-answer"><h4>%s</h4>%s%s</div>' %
        (html.escape(_label(language, "answer")),
         _answer_blocks(renderer, display_answer, display_answer_provenance, language), ""),
        answer_assets,
        explanation_panel,
        '<div class="closing-pair">',
        '<div class="source-box"><strong>%s</strong>%s</div>' %
        (html.escape(_label(language, "source_trace")),
         _source_trace(renderer, walk["source_trace"], language, materials_root)),
        '</div>',
        '</section></article>',
    ])


def render_manifest(workspace, manifest, math_converter=None, materials_root=None):
    """Validate and render one typed chapter manifest."""
    exam_start.require_full_processing(
        workspace, purpose="Study Guide document rendering")
    chapter = manifest.get("chapter") if isinstance(manifest, dict) else None
    report = validate_manifest(
        workspace, chapter, manifest, _enforce_v2_crop_receipts=True)
    pipeline_version = report.get("ingestion_pipeline_version")
    if pipeline_version != "ingestion-v2":
        raise GuideError(
            "%s Study Guide compatibility is read-only; document rendering requires "
            "a verified ingestion-v2 manifest and live target-item crop receipts"
            % (pipeline_version or "non-structured"),
            2,
        )
    if manifest.get("authoring_protocol_version") != 2:
        raise GuideError(
            "a new visual Study Guide requires authoring_protocol_version=2 with "
            "target-item crop receipts and detailed per-item answer explanations; "
            "the explicit answer_explanation_mode separately determines whether "
            "isolated receipts are required",
            2,
        )
    verified_crop_snapshots = _capture_verified_crop_asset_snapshots(
        workspace, report
    )
    try:
        asset_policy = workspace_asset_policy_snapshot(workspace)
    except (OSError, UnicodeError, ValueError) as exc:
        raise GuideError(
            "typed Study Guide cannot build the complete workspace asset policy: %s" % exc,
            2,
        )
    if asset_policy["unsafe_paths"]:
        raise GuideError(
            "typed Study Guide workspace contains an unsafe asset declaration: %s"
            % asset_policy["unsafe_paths"][0],
            2,
        )
    if asset_policy["conflicts"]:
        raise GuideError(
            "typed Study Guide workspace contains a student-attempt/asset-role conflict: %s"
            % asset_policy["conflicts"][0],
            2,
        )
    tainted_asset_keys = _workspace_tainted_asset_keys(
        workspace, "typed Study Guide renderer")
    language = report["language"]
    renderer_language = {"zh": "中文", "en": "English", "bilingual": "双语"}[language]
    renderer = MarkdownRenderer(
        workspace, math_converter, language=renderer_language,
        student_attempt_tainted_keys=tainted_asset_keys,
    )
    kp_lookup = {row["id"]: row for row in manifest["knowledge_points"]}
    formula_lookup = {
        formula["id"]: formula
        for kp in manifest["knowledge_points"] for formula in kp["formulas"]
    }
    walkthrough_lookup = {row["item_id"]: row for row in manifest["walkthroughs"]}
    primary = {row["item_id"]: row["knowledge_point_ids"][0] for row in manifest["walkthroughs"]}
    index_by_id = {row["item_id"]: index for index, row in enumerate(manifest["walkthroughs"], 1)}
    sections = []
    for kp_index, kp in enumerate(manifest["knowledge_points"], 1):
        formulas = "".join(
            _formula(renderer, formula, language, materials_root)
            for formula in kp["formulas"]
        )
        if not formulas:
            formulas = '<p class="notice">%s</p>' % html.escape(_label(language, "no_formula"))
        cards = "".join(
            _walkthrough(renderer, workspace, walkthrough_lookup[item_id], index_by_id[item_id],
                         language, formula_lookup, kp_lookup, materials_root,
                         student_attempt_tainted_keys=tainted_asset_keys,
                         verified_crop_snapshots=verified_crop_snapshots)
            for item_id in kp["example_ids"]
            if item_id in walkthrough_lookup and primary[item_id] == kp["id"]
        )
        cross_refs = [walkthrough_lookup[item_id] for item_id in kp["example_ids"]
                      if item_id in walkthrough_lookup and primary[item_id] != kp["id"]]
        cross_rows = []
        cross_provenance = [
            _document_visible_provenance(
                (walk.get("knowledge_point_uses") or {}).get(
                    kp["id"], kp["explanation"]),
                (walk.get("knowledge_point_uses_provenance") or {}).get(
                    kp["id"]),
            )
            for walk in cross_refs
        ]
        for cross_index, walk in enumerate(cross_refs):
            usage = (walk.get("knowledge_point_uses") or {}).get(kp["id"], kp["explanation"])
            cross_rows.append(
                    '<li><a href="#example-%s">%s · %s</a>%s</li>' % (
                    html.escape(walk["item_id"], quote=True),
                    html.escape(_label(language, "example", index=index_by_id[walk["item_id"]])),
                    _localized_heading(walk["title"], language),
                    _localized_text(
                        renderer, usage, language, "localized compact",
                        provenance=_run_terminal_provenance(
                            cross_provenance, cross_index),
                        source_provenance=(
                            walk.get("knowledge_point_uses_provenance")
                            or {}).get(kp["id"]),
                    ),
                )
            )
        cross = ('<div class="cross-reference"><strong>%s：</strong><ul>%s</ul></div>' %
                 (html.escape(_label(language, "mapped_examples")),
                  "".join(cross_rows))) if cross_rows else ""
        example_notice = ""
        if not kp["example_ids"] and kp.get("example_note"):
            example_notice = '<div class="notice no-mapped-examples">%s</div>' % (
                _localized_text(
                    renderer, kp["example_note"], language, "localized compact"
                )
            )
        explanation_blocks = (
            _teaching_explanation_blocks(renderer, kp, language)
            if "teaching_explanation" in kp
            else _explanation_blocks(renderer, kp, language)
        )
        sections.append("".join([
            '<section class="knowledge-section" id="kp-%s">' % html.escape(kp["id"], quote=True),
            '<p class="chapter-marker">%s</p><h2>%s</h2>' % (
                html.escape(_label(language, "knowledge_point", index=kp_index)),
                _localized_heading(kp["title"], language)),
            '<h3>%s</h3>%s' % (html.escape(_label(language, "plain_explanation")),
                                explanation_blocks),
            _knowledge_assets(
                workspace, kp["source_refs"], language,
                student_attempt_tainted_keys=tainted_asset_keys,
                verified_crop_snapshots=verified_crop_snapshots,
            ),
            '<h3>%s</h3>%s' % (html.escape(_label(language, "formulas")), formulas),
            '<div class="source-box"><strong>%s</strong>%s</div>' %
            (html.escape(_label(language, "source_evidence")),
             _source_trace(renderer, kp["source_refs"], language, materials_root)),
            '<h3 class="mapped-examples-heading">%s · %d</h3>%s%s%s</section>' % (
                html.escape(_label(language, "mapped_examples")),
                len(kp["example_ids"]), cards, cross, example_notice),
        ]))
    semantic_exclusions = ""
    if manifest.get("semantic_exclusions"):
        rows = []
        for exclusion in manifest["semantic_exclusions"]:
            rows.append('<li><code>%s</code><strong>%s：</strong>%s</li>' % (
                html.escape(exclusion["source_unit_id"]),
                html.escape(_label(language, "semantic_exclusion_reason")),
                _localized_text(renderer, exclusion["reason"], language, "localized compact"),
            ))
        semantic_exclusions = '<section class="semantic-exclusions"><h2>%s</h2><ul>%s</ul></section>' % (
            html.escape(_label(language, "semantic_exclusions")), "".join(rows))
    omissions = ""
    if manifest["omissions"]:
        rows = []
        for omission in manifest["omissions"]:
            rows.append('<li><strong>%s</strong>%s%s</li>' % (
                html.escape(omission["item_id"]),
                _localized_text(renderer, omission["reason"], language, "localized compact"),
                _source_trace(renderer, omission["source_refs"], language, materials_root)))
        omissions = '<section class="omissions"><h2>%s</h2><ul>%s</ul></section>' % (
            html.escape(_label(language, "omissions")), "".join(rows))
    source_inventory = _source_inventory(manifest["walkthroughs"], language)
    provenance_legend = _render_provenance_legend(manifest, language)
    route = "".join('<li>%s</li>' %
                    _localized_heading(kp["title"], language)
                    for kp in manifest["knowledge_points"])
    lang_attr = {"zh": "zh-CN", "en": "en", "bilingual": "mul"}[language]
    title = _label(language, "title", chapter=chapter)
    document = """<!doctype html>
<html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>%s</title><style>
:root{--ink:#172033;--muted:#59677f;--line:#d9e2ef;--accent:#2457c5;--blue:#eef6ff;--violet:#f6f2ff;--green:#eaf8ef;}
*{box-sizing:border-box}html{background:#edf1f7}body{max-width:1040px;margin:0 auto;padding:36px 46px 90px;background:#fff;color:var(--ink);font:17px/1.7 "Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
h1{font-size:2.2rem;line-height:1.18;margin:.2em 0}h2{font-size:1.65rem;line-height:1.3;border-bottom:3px solid var(--accent);padding-bottom:.25em;margin:2em 0 .8em}h3{font-size:1.25rem;margin:1.45em 0 .55em}h4{margin:1.1em 0 .4em}h5{margin:.75em 0 .3em}p{margin:.5em 0}ul,ol{padding-left:1.6em}li{margin:.28em 0}.localized-heading{display:block}
.hero{border-bottom:1px solid var(--line);padding-bottom:1.1em}.subtitle,.eyebrow,.chapter-marker,.source-box{color:var(--muted)}.coverage{background:#f4f7fb;border-left:5px solid var(--accent);padding:.8em 1em;margin:1.2em 0}.provenance-legend{background:#fff9e8;border:1px solid #ead9a4;border-radius:10px;padding:.75em 1em;margin:1em 0}.provenance-legend ul{display:flex;flex-wrap:wrap;gap:.35em 1.4em;margin:.35em 0;padding-left:1.35em}.provenance-legend p{color:var(--muted);font-size:.88rem}.legend-en{display:block;color:#315689;font-size:.9em}.provenance-marker{display:inline-block;white-space:nowrap;font-size:.88em;letter-spacing:.06em}.route{columns:2}.heading-en,.lang-en{display:block;margin-top:.2em}.heading-en,.en-prefix{color:#315689;font-size:.92em}.localized>p:first-child{margin-top:.2em}.compact p{display:inline}.formula-card{border:1px solid var(--line);border-radius:12px;padding:1em 1.15em;margin:.8em 0}.formula-display{text-align:center;overflow-x:auto;background:#f8fafc;border-radius:8px;padding:.55em;margin:.6em 0}.formula-display p{margin:0}.variables,table{width:100%%;border-collapse:collapse;margin:.7em 0}th,td{border:1px solid #bdc9d9;padding:.5em .65em;vertical-align:top}th{background:#edf3fb}.source-box{border-left:3px solid #a9bad2;padding:.25em .7em;margin:.8em 0;font-size:.88rem}.source-box>strong{display:block}.source-list{margin:.2em 0}
 .example-card{border:1px solid var(--line);border-radius:16px;margin:1.3em 0;overflow:hidden;box-shadow:0 3px 14px rgba(30,55,90,.07)}.example-card:target{outline:4px solid #dcae29}.example-header{padding:1em 1.2em}.example-header h3{margin:.2em 0}.kp-uses{padding:.2em 1.2em .9em;background:#f8fafc}.cross-reference{font-size:.92em;line-height:1.5}.cross-reference a,.source-list a{color:var(--accent);text-decoration:underline}.prompt-zone{background:var(--blue);padding:1em 1.2em}.walkthrough-zone{background:var(--violet);padding:1em 1.2em}.formula-use{background:#fff;border:1px solid #dcd7eb;border-radius:10px;padding:.8em 1em;margin:.9em 0}.notice{background:#fff8df;border-left:4px solid #dcae29;padding:.55em .8em}.quantity-grid{display:grid;grid-template-columns:1fr 1fr;gap:1em}.quantity-grid>div{background:#fff8;padding:.4em .8em;border-radius:8px}.quantity-value{margin:.15em 0 .4em}.substitution{font-size:1.05em}.substitution-math{font-size:.94em}.substitution-math>.provenance-marker,.substitution-math>.provenance-pair{display:block;text-align:right;font-size:.86em;margin-top:.25em}.final-answer{background:var(--green);border-left:5px solid #3f9b60;padding:.65em .9em;margin:1em 0}.answer-language+.answer-language{border-top:1px solid #b9d8c3;margin-top:.7em;padding-top:.6em}.answer-explanation{background:#fff;border:1px solid #b8c7dc;border-left:5px solid #5175a8;border-radius:8px;padding:.65em .9em;margin:1em 0}.translation{background:#fff9dd;border-left:4px solid #dcae29;padding:.5em .8em;margin:.7em 0}.source-asset{text-align:center;margin:1em auto}.source-asset img{display:block;max-width:100%%;max-height:76vh;margin:auto;border:1px solid var(--line);border-radius:8px}.source-asset figcaption{font-size:.82rem;color:var(--muted);margin-top:.3em}.mapped-examples-heading,.source-list,code{overflow-wrap:anywhere;word-break:break-word}code{font-family:Consolas,monospace;background:#edf1f5;padding:.08em .25em;border-radius:4px}.omissions,.semantic-exclusions{background:#fff8df;padding:1em}
@page{size:A4;margin:16mm 14mm 18mm;@bottom-center{content:"%s " counter(page) " / " counter(pages);font-size:9pt;color:#667085}}
@media print{html,body{background:#fff}body{max-width:none;padding:0;font-size:10.5pt}.hero{break-after:page}h1,h2,h3,h4,h5,.localized-heading{break-after:avoid;break-inside:avoid;page-break-inside:avoid}p,li{orphans:3;widows:3}.formula-card,.formula-use,.final-answer,.answer-explanation,table,.prompt-zone,.walkthrough-zone{break-inside:auto;box-decoration-break:clone;-webkit-box-decoration-break:clone}.prompt-intro{display:inline-block;width:100%%;vertical-align:top}.localized-pair,.answer-language,.solution-steps>li{break-inside:avoid;page-break-inside:avoid}.answer-language+.answer-language{break-before:avoid}figure,.example-header,.kp-uses,.prompt-intro,.formula-intro,.translation,.provenance-legend,.cross-reference li,.source-box li{break-inside:avoid;page-break-inside:avoid}.source-box>strong{break-after:avoid;page-break-after:avoid}.example-header{break-after:avoid}.example-card{box-shadow:none;overflow:visible}thead{display:table-header-group}tr{break-inside:avoid}.source-asset img{max-height:160mm}.example-card .source-asset img{max-height:155mm}.substitution-math{overflow:visible;font-size:9.5pt}.route{columns:2}}
</style></head><body><header class="hero"><p class="subtitle">%s</p><h1>%s</h1>
<div class="coverage"><strong>%s</strong><p>%s</p>%s</div>
%s
  <h2>%s</h2><ol class="route">%s</ol></header><main>%s%s%s</main></body></html>""" % (
        html.escape(lang_attr, quote=True), html.escape(title), html.escape(_label(language, "page")),
        html.escape(_label(language, "subtitle")), html.escape(title), html.escape(_label(language, "coverage")),
        html.escape(_label(language, "coverage_line", kp=len(manifest["knowledge_points"]),
                           done=len(manifest["walkthroughs"]), expected=len(report["expected_item_ids"]),
                           profile=manifest["profile"])), source_inventory, provenance_legend,
        html.escape(_label(language, "contents")), route, "".join(sections),
        semantic_exclusions, omissions)
    validate_guide_document(
        document, report["walkthrough_item_ids"], workspace=workspace,
        materials_root=materials_root)
    try:
        final_asset_policy = workspace_asset_policy_snapshot(workspace)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ArtifactDriftError(
            "workspace asset policy became unreadable during typed Guide rendering: %s" % exc
        )
    if final_asset_policy != asset_policy:
        raise ArtifactDriftError(
            "workspace asset policy changed during typed Guide rendering"
        )
    _verify_crop_asset_snapshots(workspace, verified_crop_snapshots)
    return document, report


class _GuideAuditParser(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.item_ids = []
        self.controls = []
        self.element_ids = set()
        self.example_fragment_links = []
        self.provenance_legend_count = 0
        self.provenance_legend_depth = 0
        self.provenance_legend_categories = []
        self.provenance_marker_count = 0
        self.answer_explanation_count = 0
        self.deprecated_self_check_nodes = []
        self.outside_legend_text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if self.provenance_legend_depth:
            self.provenance_legend_depth += 1
        elif values.get("id") == "provenance-legend":
            self.provenance_legend_count += 1
            self.provenance_legend_depth = 1
        if (self.provenance_legend_depth
                and tag.lower() == "li" and values.get("data-provenance")):
            self.provenance_legend_categories.append(values["data-provenance"])
        if "provenance-marker" in classes:
            self.provenance_marker_count += 1
        if "answer-explanation" in classes:
            self.answer_explanation_count += 1
        if any("self-check" in value or "self_check" in value for value in classes):
            self.deprecated_self_check_nodes.append(tag.lower())
        if "data-item-id" in values:
            self.item_ids.append(values["data-item-id"])
        if "id" in values:
            self.element_ids.add(values["id"])
        if values.get("href", "").startswith("#example-"):
            self.example_fragment_links.append(values["href"][1:])
        if tag.lower() in {"details", "summary", "button", "input", "select", "textarea"}:
            self.controls.append(tag.lower())

    def handle_endtag(self, tag):
        del tag
        if self.provenance_legend_depth:
            self.provenance_legend_depth -= 1

    def handle_data(self, data):
        if not self.provenance_legend_depth:
            self.outside_legend_text.append(data)


def validate_guide_document(document, expected_item_ids, workspace=None, materials_root=None):
    validate_generated_html(
        document, workspace=workspace, materials_root=materials_root)
    for index, char in enumerate(document):
        code = ord(char)
        if (code < 0x20 and char not in "\t\n\r") or code in (0x7F, 0xFFFD):
            raise GuideError("generated guide contains forbidden character U+%04X at %d" %
                             (code, index), 1)
    bare_command = search_visible_latex_command(document)
    if bare_command:
        raise GuideError(
            "generated guide contains visible raw TeX command %s" % bare_command.group(0), 1)
    if re.search(
            r"(?<!\\)\$\$.*?(?<!\\)\$\$|"
            r"(?<![\\$])\$(?!\$)[^$\r\n]+(?<!\\)\$(?!\$)",
            document, re.S):
        raise GuideError("generated guide contains unrendered dollar-delimited TeX", 1)
    parser = _GuideAuditParser()
    parser.feed(document)
    parser.close()
    if parser.controls:
        raise GuideError("generated guide contains hidden/interactive controls: %s" %
                         ", ".join(sorted(set(parser.controls))), 1)
    if parser.provenance_legend_count != 1:
        raise GuideError(
            "generated guide must contain exactly one provenance legend", 1)
    if (len(parser.provenance_legend_categories)
            != len(set(parser.provenance_legend_categories))):
        raise GuideError(
            "generated guide provenance legend repeats a label meaning", 1)
    allowed_legend_categories = set(PROVENANCE_LEGEND_ORDER)
    if (not parser.provenance_legend_categories
            or not set(parser.provenance_legend_categories)
            <= allowed_legend_categories):
        raise GuideError(
            "generated guide provenance legend categories are invalid", 1)
    outside_legend = "".join(parser.outside_legend_text)
    leaked_labels = sorted({
        label
        for by_category in PROVENANCE_LABEL_VARIANTS.values()
        for variants in by_category.values()
        for label in variants
        if label and label in outside_legend
    })
    if leaked_labels:
        raise GuideError(
            "generated guide repeats a full provenance label outside its legend: %s"
            % leaked_labels[0], 1)
    if parser.deprecated_self_check_nodes:
        raise GuideError(
            "generated guide contains a deprecated answer-self-check panel", 1)
    if parser.answer_explanation_count != len(expected_item_ids):
        raise GuideError(
            "generated guide must render exactly one beginner answer explanation "
            "for every example: expected=%d got=%d"
            % (len(expected_item_ids), parser.answer_explanation_count), 1)
    if len(set(parser.item_ids)) != len(parser.item_ids):
        raise GuideError("generated guide duplicates an example card", 1)
    if (len(parser.item_ids) != len(expected_item_ids)
            or set(parser.item_ids) != set(expected_item_ids)):
        raise GuideError("rendered example coverage differs from typed manifest: expected=%s got=%s" %
                         (list(expected_item_ids), parser.item_ids), 1)
    missing_fragments = sorted(set(parser.example_fragment_links) - parser.element_ids)
    if missing_fragments:
        raise GuideError("generated guide contains broken example cross-links: %s" %
                         missing_fragments, 1)
    return {"ok": True, "item_ids": parser.item_ids}
