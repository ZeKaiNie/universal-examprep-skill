#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render a validated typed chapter manifest as a real study guide.

The legacy renderer can still build a source packet.  This module owns the stricter teaching
document: knowledge points are followed by every mapped example exactly once, formulas are
rendered as MathML, full-prompt images are not followed by duplicated OCR, and every example
contains the complete worked-solution contract.
"""

from collections import Counter
import html
import os
import re
import stat
from pathlib import Path

from study_guide_content import validate_manifest
from study_guide_render import GuideError, MarkdownRenderer, _resolve_asset, validate_generated_html


SOURCE_TYPE_LABELS = {
    "lecture": ("课件 / 讲义", "Lecture / handout"),
    "homework": ("作业", "Homework"),
    "quiz": ("Quiz", "Quiz"),
    "mock_exam": ("模拟考试", "Mock exam"),
    "past_exam": ("往年考试", "Past exam"),
    "textbook": ("教材", "Textbook"),
    "other": ("其他资料", "Other material"),
}

PROVENANCE_LABELS = {
    "material": ("🟢 来自资料", "🟢 From your materials"),
    "ai_supplemented": ("🟡 AI补充，可能与你老师讲的不完全一致",
                        "🟡 AI-supplemented — may differ from what your teacher taught"),
    "ai_generated": ("⚠️ AI生成答案，非老师/教材提供",
                     "⚠️ AI-generated answer — not from your teacher or textbook"),
}

LABELS = {
    "title": ("第 {chapter} 章 · 零基础完整教材", "Chapter {chapter} · Complete Beginner Study Guide"),
    "subtitle": ("知识点、公式与全部对应例题逐项精讲", "Knowledge points, formulas, and every mapped example explained step by step"),
    "coverage": ("覆盖证明", "Coverage proof"),
    "coverage_line": ("{kp} 个知识点；{done}/{expected} 道例题；模式：{profile}",
                      "{kp} knowledge points; {done}/{expected} examples; profile: {profile}"),
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
    "self_check": ("⑥ 答案自检", "⑥ Check the answer"),
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


def _localized_text(renderer, value, language, css="localized"):
    parts = []
    if language in ("zh", "bilingual"):
        parts.append('<div class="%s lang-zh" lang="zh-CN">%s</div>' %
                     (css, renderer.render(value["zh"])))
    if language in ("en", "bilingual"):
        prefix = '<span class="en-prefix">EN · </span>' if language == "bilingual" else ""
        parts.append('<div class="%s lang-en" lang="en">%s%s</div>' %
                     (css, prefix, renderer.render(value["en"])))
    return "".join(parts)


def _localized_heading(value, language):
    if language == "zh":
        return html.escape(value["zh"])
    if language == "en":
        return html.escape(value["en"])
    return ('<span lang="zh-CN">%s</span><span class="heading-en" lang="en">'
            'EN · %s</span>') % (html.escape(value["zh"]), html.escape(value["en"]))


def _provenance(language, value):
    zh, en = PROVENANCE_LABELS[value]
    if language == "zh":
        return '<p class="provenance" lang="zh-CN">%s</p>' % html.escape(zh)
    if language == "en":
        return '<p class="provenance" lang="en">%s</p>' % html.escape(en)
    return ('<p class="provenance" lang="zh-CN">%s</p>'
            '<p class="provenance" lang="en">EN · %s</p>') % (
                html.escape(zh), html.escape(en))


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
        label = PROVENANCE_LABELS[provenance[code]][0 if code == "zh" else 1]
        prefix = '<span class="en-prefix">EN · </span>' if (
            code == "en" and language == "bilingual") else ""
        parts.append(
            '<section class="answer-language lang-%s" lang="%s">'
            '<p class="provenance">%s</p><div class="localized">%s%s</div></section>'
            % (code, "zh-CN" if code == "zh" else "en", html.escape(label),
               prefix, renderer.render(answer[code]))
        )
    return "".join(parts)


def _asset_figure(workspace, relative, label, language, index):
    data_uri = _resolve_asset(workspace, relative, "%s %d" % (label, index))
    caption = "%s %d · %s" % (_label(language, label), index, relative)
    return ('<figure class="source-asset"><img src="%s" alt="%s">'
            '<figcaption>%s</figcaption></figure>') % (
                data_uri, html.escape(caption, quote=True), html.escape(caption))


def _knowledge_assets(workspace, refs, language):
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
        _asset_figure(workspace, path, "concept_asset", language, index)
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


def _material_page_href(materials_root, source_file, page):
    """Return the canonical, validator-compatible URI for one confirmed source page.

    A source path in the manifest is relative to the separately confirmed materials root,
    not to ``study_guide/chNN.html``.  If that binding cannot be proved, rendering keeps the
    human-readable source label but deliberately omits a misleading link.
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
    return Path(target_real).resolve().as_uri() + "#page=%d" % page


def _source_ref(ref, materials_root=None):
    page_links = []
    if not ref.get("pages"):
        raise GuideError("typed Study Guide source refs require at least one source page", 1)
    for page in ref.get("pages", []):
        href = _material_page_href(materials_root, ref["source_file"], page)
        page_links.append('<a href="%s">p.%d</a>' %
                          (html.escape(href, quote=True), page))
    chunks = [html.escape(ref["source_file"]), ", ".join(page_links) or "—"]
    if ref.get("source_unit_id"):
        chunks.append("unit %s" % html.escape(ref["source_unit_id"]))
    if ref.get("role"):
        chunks.append(html.escape(ref["role"]))
    if ref.get("quote_span"):
        chunks.append("“%s”" % html.escape(ref["quote_span"]))
    return " · ".join(chunks)


def _source_trace(refs, materials_root=None):
    return "<ul class=\"source-list\">%s</ul>" % "".join(
        "<li>%s</li>" % _source_ref(ref, materials_root) for ref in refs
    )


def _quantity(renderer, quantity, language):
    suffix = []
    if quantity.get("symbol"):
        suffix.append("<code>%s</code>" % html.escape(quantity["symbol"]))
    if quantity.get("value"):
        suffix.append("<strong>%s</strong>" % html.escape(quantity["value"]))
    if quantity.get("unit"):
        suffix.append(html.escape(quantity["unit"]))
    return '<li>%s%s</li>' % (
        _localized_text(renderer, quantity["label"], language, "localized compact"),
        ('<div class="quantity-value">%s</div>' % " · ".join(suffix)) if suffix else "",
    )


def _formula(renderer, formula, language, materials_root=None):
    variables = "".join(
        "<tr><td><code>%s</code></td><td>%s</td></tr>" % (
            html.escape(variable["symbol"]),
            _localized_text(renderer, variable["meaning"], language, "localized compact"),
        ) for variable in formula["variables"]
    )
    return "".join([
        '<article class="formula-card" data-formula-id="%s">' % html.escape(formula["id"], quote=True),
        '<div class="formula-display">%s</div>' % renderer.render("$$%s$$" % formula["latex"]),
        '<h5>%s</h5>' % html.escape(_label(language, "formula_meaning")),
        _localized_text(renderer, formula["explanation"], language),
        '<h5>%s</h5>' % html.escape(_label(language, "applicability")),
        _localized_text(renderer, formula["applicability"], language),
        ('<table class="variables"><thead><tr><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>' %
         (html.escape(_label(language, "symbol")), html.escape(_label(language, "meaning")), variables))
        if variables else "",
        '<div class="source-box"><strong>%s</strong>%s</div>' %
        (html.escape(_label(language, "source_evidence")),
         _source_trace(formula["source_refs"], materials_root)),
        '</article>',
    ])


def _formula_use(renderer, use, language, formula_lookup):
    formula = formula_lookup[use["formula_id"]]
    mappings = "".join(
        '<tr><td><code>%s</code></td><td>%s</td></tr>' % (
            html.escape(row["symbol"]),
            _localized_text(renderer, row["maps_to"], language, "localized compact"),
        ) for row in use["variable_mapping"]
    )
    return "".join([
        '<section class="formula-use">',
        '<h4>%s · <code>%s</code></h4>' %
        (html.escape(_label(language, "formula_use")), html.escape(use["formula_id"])),
        '<div class="formula-display small">%s</div>' % renderer.render("$$%s$$" % formula["latex"]),
        _localized_text(renderer, use["why_applicable"], language),
        '<h5>%s</h5>' % html.escape(_label(language, "mapping")),
        ('<table><tbody>%s</tbody></table>' % mappings) if mappings else '<p>—</p>',
        '<h5>%s</h5>' % html.escape(_label(language, "substitution")),
        '<div class="substitution">%s</div>' % renderer.render("$$%s$$" % use["substitution"]),
        '</section>',
    ])


def _walkthrough(renderer, workspace, walk, index, language, formula_lookup, kp_lookup,
                 materials_root=None):
    prompt_assets = "".join(
        _asset_figure(workspace, path, "prompt_asset", language, asset_index)
        for asset_index, path in enumerate(walk["prompt_asset_paths"], 1)
    )
    answer_assets = "".join(
        _asset_figure(workspace, path, "answer_asset", language, asset_index)
        for asset_index, path in enumerate(walk["answer_asset_paths"], 1)
    )
    prompt = ""
    if walk["prompt_asset_mode"] != "full_prompt":
        prompt = '<div class="original-prompt"><h4>%s</h4>%s</div>' % (
            html.escape(_label(language, "original_prompt")), renderer.render(walk["prompt_text"]))
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
            translations.append('<div class="translation" lang="%s"><strong>%s · %s</strong>%s</div>' % (
                tag, prefix, html.escape(_label(language, "translation")),
                renderer.render(walk["translation"][code])))
    known = "".join(_quantity(renderer, row, language) for row in walk["known_quantities"])
    unknown = "".join(_quantity(renderer, row, language) for row in walk["unknown_quantities"])
    formula_uses = "".join(
        _formula_use(renderer, row, language, formula_lookup) for row in walk["formula_uses"]
    )
    if not formula_uses:
        reason = walk.get("no_formula_reason")
        formula_uses = '<section class="notice no-formula-use"><h4>%s</h4>%s</section>' % (
            html.escape(_label(language, "why_no_formula")),
            _localized_text(renderer, reason, language) if reason else html.escape(
                _label(language, "no_formula_example")),
        )
    steps = "".join(
        '<li>%s</li>' % _localized_text(renderer, step, language, "localized compact")
        for step in walk["steps"]
    )
    source_zh, source_en = SOURCE_TYPE_LABELS[walk["source_type"]]
    source_label = source_zh if language == "zh" else source_en if language == "en" else "%s / %s" % (source_zh, source_en)
    linked = " · ".join(
        kp_lookup[kp_id]["title"].get(language if language != "bilingual" else "zh", kp_id)
        for kp_id in walk["knowledge_point_ids"]
    )
    kp_uses = []
    for kp_id in walk["knowledge_point_ids"]:
        usage = (walk.get("knowledge_point_uses") or {}).get(kp_id)
        if usage is None:
            usage = kp_lookup[kp_id]["explanation"]
        kp_uses.append('<li><strong>%s</strong>%s</li>' % (
            _localized_heading(kp_lookup[kp_id]["title"], language),
            _localized_text(renderer, usage, language, "localized compact"),
        ))
    solution_kind = walk.get("solution_kind") or (
        "formula" if walk.get("formula_uses") else "concept")
    return "".join([
        '<article class="example-card" id="example-%s" data-item-id="%s" data-source-type="%s">' %
        (html.escape(walk["item_id"], quote=True), html.escape(walk["item_id"], quote=True),
         html.escape(walk["source_type"], quote=True)),
        '<header class="example-header"><p class="eyebrow">%s · %s</p><h3>%s · %s</h3>' % (
            html.escape(_label(language, "example", index=index)), html.escape(source_label),
            html.escape(walk["item_id"]), _localized_heading(walk["title"], language)),
        '<p class="kp-links"><strong>%s：</strong>%s</p></header>' %
        (html.escape(_label(language, "also_tests")), html.escape(linked)),
        '<section class="kp-uses"><h4>%s</h4><ul>%s</ul><p><strong>%s：</strong><code>%s</code></p></section>' % (
            html.escape(_label(language, "kp_use")), "".join(kp_uses),
            html.escape(_label(language, "solution_kind")), html.escape(solution_kind)),
        '<section class="prompt-zone"><h4>%s</h4>%s%s%s</section>' %
        (html.escape(_label(language, "prompt_step")), prompt_assets, prompt,
         "".join(translations)),
        '<section class="walkthrough-zone">',
        '<h4>%s</h4>%s' % (html.escape(_label(language, "what_asked")),
                            _localized_text(renderer, walk["what_asked"], language)),
        '<h4>%s</h4><div class="quantity-grid"><div><h5>%s</h5><ul>%s</ul></div>'
        '<div><h5>%s</h5><ul>%s</ul></div></div>' % (
            html.escape(_label(language, "quantities")), html.escape(_label(language, "known")),
            known or "<li>—</li>", html.escape(_label(language, "unknown")), unknown or "<li>—</li>"),
        formula_uses,
        '<h4>%s</h4><ol class="solution-steps">%s</ol>' %
        (html.escape(_label(language, "steps")), steps),
        '<div class="final-answer"><h4>%s</h4>%s%s</div>' %
        (html.escape(_label(language, "answer")),
         _answer_blocks(renderer, walk["answer"], walk["answer_provenance"], language), ""),
        answer_assets,
        '<div class="self-check"><h4>%s</h4>%s</div>' %
        (html.escape(_label(language, "self_check")), _localized_text(renderer, walk["self_check"], language)),
        '<div class="source-box"><strong>%s</strong>%s</div>' %
        (html.escape(_label(language, "source_trace")),
         _source_trace(walk["source_trace"], materials_root)),
        '</section></article>',
    ])


def render_manifest(workspace, manifest, math_converter=None, materials_root=None):
    """Validate and render one typed chapter manifest."""
    chapter = manifest.get("chapter") if isinstance(manifest, dict) else None
    report = validate_manifest(workspace, chapter, manifest)
    language = report["language"]
    renderer_language = {"zh": "中文", "en": "English", "bilingual": "双语"}[language]
    renderer = MarkdownRenderer(workspace, math_converter, language=renderer_language)
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
                         language, formula_lookup, kp_lookup, materials_root)
            for item_id in kp["example_ids"]
            if item_id in walkthrough_lookup and primary[item_id] == kp["id"]
        )
        cross_refs = [walkthrough_lookup[item_id] for item_id in kp["example_ids"]
                      if item_id in walkthrough_lookup and primary[item_id] != kp["id"]]
        cross_rows = []
        for walk in cross_refs:
            usage = (walk.get("knowledge_point_uses") or {}).get(kp["id"], kp["explanation"])
            cross_rows.append(
                '<li><a href="#example-%s"><code>%s</code></a>%s</li>' % (
                    html.escape(walk["item_id"], quote=True), html.escape(walk["item_id"]),
                    _localized_text(renderer, usage, language, "localized compact"),
                )
            )
        cross = ('<div class="cross-reference"><strong>%s：</strong><ul>%s</ul></div>' %
                 (html.escape(_label(language, "mapped_examples")),
                  "".join(cross_rows))) if cross_rows else ""
        sections.append("".join([
            '<section class="knowledge-section" id="kp-%s">' % html.escape(kp["id"], quote=True),
            '<p class="chapter-marker">%s</p><h2>%s</h2>' % (
                html.escape(_label(language, "knowledge_point", index=kp_index)),
                _localized_heading(kp["title"], language)),
            '<h3>%s</h3>%s' % (html.escape(_label(language, "plain_explanation")),
                                _localized_text(renderer, kp["explanation"], language)),
            _knowledge_assets(workspace, kp["source_refs"], language),
            '<h3>%s</h3>%s' % (html.escape(_label(language, "formulas")), formulas),
            '<div class="source-box"><strong>%s</strong>%s</div>' %
            (html.escape(_label(language, "source_evidence")),
             _source_trace(kp["source_refs"], materials_root)),
            '<h3 class="mapped-examples-heading">%s · %s</h3>%s%s</section>' % (
                html.escape(_label(language, "mapped_examples")),
                html.escape(", ".join(kp["example_ids"])), cards, cross),
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
                _source_trace(omission["source_refs"], materials_root)))
        omissions = '<section class="omissions"><h2>%s</h2><ul>%s</ul></section>' % (
            html.escape(_label(language, "omissions")), "".join(rows))
    counts = Counter(row["source_type"] for row in manifest["walkthroughs"])
    buckets = " · ".join("%s: %d" % (key, counts[key]) for key in sorted(counts))
    route = "".join('<li><code>%s</code> · %s</li>' %
                    (html.escape(kp["id"]), _localized_heading(kp["title"], language))
                    for kp in manifest["knowledge_points"])
    lang_attr = {"zh": "zh-CN", "en": "en", "bilingual": "mul"}[language]
    title = _label(language, "title", chapter=chapter)
    document = """<!doctype html>
<html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>%s</title><style>
:root{--ink:#172033;--muted:#59677f;--line:#d9e2ef;--accent:#2457c5;--blue:#eef6ff;--violet:#f6f2ff;--green:#eaf8ef;}
*{box-sizing:border-box}html{background:#edf1f7}body{max-width:1040px;margin:0 auto;padding:36px 46px 90px;background:#fff;color:var(--ink);font:17px/1.7 "Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
h1{font-size:2.2rem;line-height:1.18;margin:.2em 0}h2{font-size:1.65rem;line-height:1.3;border-bottom:3px solid var(--accent);padding-bottom:.25em;margin:2em 0 .8em}h3{font-size:1.25rem;margin:1.45em 0 .55em}h4{margin:1.1em 0 .4em}h5{margin:.75em 0 .3em}p{margin:.5em 0}ul,ol{padding-left:1.6em}li{margin:.28em 0}
.hero{border-bottom:1px solid var(--line);padding-bottom:1.1em}.subtitle,.eyebrow,.chapter-marker,.source-box,.coverage-detail{color:var(--muted)}.coverage{background:#f4f7fb;border-left:5px solid var(--accent);padding:.8em 1em;margin:1.2em 0}.route{columns:2}.heading-en,.lang-en{display:block;margin-top:.2em}.heading-en,.en-prefix{color:#315689;font-size:.92em}.localized>p:first-child{margin-top:.2em}.compact p{display:inline}.formula-card{border:1px solid var(--line);border-radius:12px;padding:1em 1.15em;margin:.8em 0}.formula-display{text-align:center;overflow-x:auto;background:#f8fafc;border-radius:8px;padding:.55em;margin:.6em 0}.formula-display p{margin:0}.variables,table{width:100%%;border-collapse:collapse;margin:.7em 0}th,td{border:1px solid #bdc9d9;padding:.5em .65em;vertical-align:top}th{background:#edf3fb}.source-box{border-left:3px solid #a9bad2;padding:.25em .7em;margin:.8em 0;font-size:.88rem}.source-list{margin:.2em 0}
 .example-card{border:1px solid var(--line);border-radius:16px;margin:1.3em 0;overflow:hidden;box-shadow:0 3px 14px rgba(30,55,90,.07)}.example-card:target{outline:4px solid #dcae29}.example-header{padding:1em 1.2em}.example-header h3{margin:.2em 0}.kp-uses{padding:.2em 1.2em .9em;background:#f8fafc}.cross-reference a,.source-list a{color:var(--accent);text-decoration:underline}.prompt-zone{background:var(--blue);padding:1em 1.2em}.walkthrough-zone{background:var(--violet);padding:1em 1.2em}.formula-use{background:#fff;border:1px solid #dcd7eb;border-radius:10px;padding:.8em 1em;margin:.9em 0}.notice{background:#fff8df;border-left:4px solid #dcae29;padding:.55em .8em}.quantity-grid{display:grid;grid-template-columns:1fr 1fr;gap:1em}.quantity-grid>div{background:#fff8;padding:.4em .8em;border-radius:8px}.quantity-value{margin:.15em 0 .4em}.substitution{font-size:1.05em}.final-answer{background:var(--green);border-left:5px solid #3f9b60;padding:.65em .9em;margin:1em 0}.answer-language+.answer-language{border-top:1px solid #b9d8c3;margin-top:.7em;padding-top:.6em}.self-check{background:#fff;border:1px dashed #9aacbf;padding:.65em .9em}.translation{background:#fff9dd;border-left:4px solid #dcae29;padding:.5em .8em;margin:.7em 0}.source-asset{text-align:center;margin:1em auto}.source-asset img{display:block;max-width:100%%;max-height:76vh;margin:auto;border:1px solid var(--line);border-radius:8px}.source-asset figcaption{font-size:.82rem;color:var(--muted);margin-top:.3em}code{font-family:Consolas,monospace;background:#edf1f5;padding:.08em .25em;border-radius:4px}.omissions,.semantic-exclusions{background:#fff8df;padding:1em}
@page{size:A4;margin:16mm 14mm 18mm;@bottom-center{content:"%s " counter(page) " / " counter(pages);font-size:9pt;color:#667085}}
@media print{html,body{background:#fff}body{max-width:none;padding:0;font-size:10.5pt}.hero{break-after:page}.knowledge-section{break-before:page}.knowledge-section:first-of-type{break-before:auto}h2,h3,h4,h5{break-after:avoid}.mapped-examples-heading{break-before:page}.formula-card,.formula-use,.final-answer,.self-check,table,figure,.example-header,.kp-uses{break-inside:avoid}.example-header,.kp-uses{break-after:avoid}.example-card{box-shadow:none;overflow:visible}.prompt-zone,.walkthrough-zone{break-inside:auto}.source-asset img{max-height:95mm}.route{columns:2}}
</style></head><body><header class="hero"><p class="subtitle">%s</p><h1>%s</h1>
<div class="coverage"><strong>%s</strong><p>%s</p><p class="coverage-detail">%s</p></div>
  <h2>%s</h2><ol class="route">%s</ol></header><main>%s%s%s</main></body></html>""" % (
        html.escape(lang_attr, quote=True), html.escape(title), html.escape(_label(language, "page")),
        html.escape(_label(language, "subtitle")), html.escape(title), html.escape(_label(language, "coverage")),
        html.escape(_label(language, "coverage_line", kp=len(manifest["knowledge_points"]),
                           done=len(manifest["walkthroughs"]), expected=len(report["expected_item_ids"]),
                           profile=manifest["profile"])), html.escape(buckets),
        html.escape(_label(language, "contents")), route, "".join(sections),
        semantic_exclusions, omissions)
    validate_guide_document(
        document, report["walkthrough_item_ids"], workspace=workspace,
        materials_root=materials_root)
    return document, report


class _GuideAuditParser(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.item_ids = []
        self.controls = []
        self.element_ids = set()
        self.example_fragment_links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-item-id" in values:
            self.item_ids.append(values["data-item-id"])
        if "id" in values:
            self.element_ids.add(values["id"])
        if values.get("href", "").startswith("#example-"):
            self.example_fragment_links.append(values["href"][1:])
        if tag.lower() in {"details", "summary", "button", "input", "select", "textarea"}:
            self.controls.append(tag.lower())


def validate_guide_document(document, expected_item_ids, workspace=None, materials_root=None):
    validate_generated_html(
        document, workspace=workspace, materials_root=materials_root)
    for index, char in enumerate(document):
        code = ord(char)
        if (code < 0x20 and char not in "\t\n\r") or code in (0x7F, 0xFFFD):
            raise GuideError("generated guide contains forbidden character U+%04X at %d" %
                             (code, index), 1)
    if re.search(r"(?<![A-Za-z])\\(?:frac|dfrac|tfrac|sqrt|sum|int|begin|end|mathbf|mathrm)\b", document):
        raise GuideError("generated guide contains visible raw TeX", 1)
    parser = _GuideAuditParser()
    parser.feed(document)
    parser.close()
    if parser.controls:
        raise GuideError("generated guide contains hidden/interactive controls: %s" %
                         ", ".join(sorted(set(parser.controls))), 1)
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
