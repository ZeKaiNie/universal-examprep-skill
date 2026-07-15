#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""i18n.py — the SINGLE vocabulary source for the whole engine (v4 P1). Stdlib only.

Why this module exists (language layering; see shipped docs/language-policy.md):
  * Persisted files (study_state.json etc.) store LANGUAGE-NEUTRAL canonical codes —
    Chinese display strings are no longer the schema. Before v4 the enum vocabulary was
    hardcoded independently in three scripts (update_progress / select_hard_questions /
    show_question_assets) and had already started to drift; this module is now the only
    definition point.
  * Display strings live in per-language catalogs (zh is the historical canonical wording,
    moved here VERBATIM so existing zh test assertions keep passing; en is its structural
    twin). `locales/<lang>/messages.json`, when present, OVERRIDES the embedded catalogs —
    the language-pack split lands in the same PR (P2) and extends these catalogs with the
    full script-message inventory.
  * Normalizers accept THREE generations of input and always converge on codes:
      v4 canonical codes  ("fill_gaps")            → unchanged
      zh display strings  ("查缺补漏", "在窗口")     → mapped to codes
      legacy four modes   ("panic", "sprint" …)    → migrated (mode + implied tier)
    Unknown values are KEPT AS-IS with a warning (never silently rewritten) — same
    fail-loud philosophy as the old _normalize_* trio this module replaces.

Public surface:
    MODES, TIERS, LANGS, ARTIFACT_MODES, WINDOW_STATUSES, ROW_STATUSES
    canon_mode(v)      -> (code_or_original, implied_tier_or_None, warning_or_None)
    canon_tier(v)      -> (code_or_original, warning_or_None)
    canon_language(v)  -> (code_or_original, warning_or_None)
    canon_artifact_mode(v) -> (code_or_original, warning_or_None)
    canon_window_status(v) -> code_or_original   (strict check is the caller's job)
    canon_row_status(v)    -> code_or_original   (unknown free strings pass through)
    display(kind, code, lang="zh") -> student-visible string (passthrough when unknown)
    workspace_language(state_or_None) -> "zh" | "en" | "bilingual"
    workspace_artifact_mode(state_or_None) -> "chat" | "visual"
    catalog(lang) -> dict (embedded catalog merged with locales/<lang>/messages.json)
    msg(msgid, lang, **fmt) -> formatted message string (P2 fills the inventory)
"""
import json
import os

# ---------------------------------------------------------------- canonical codes
MODES = ("from_scratch", "shore_up", "fill_gaps")
TIERS = ("le1d", "d1_3", "d3_7", "gt7d")
LANGS = ("zh", "en", "bilingual")
ARTIFACT_MODES = ("chat", "visual")
WINDOW_STATUSES = ("in_window", "out_window", "verified")
# Row statuses: the KNOWN lifecycle set. `set-*-status` stays free-string tolerant —
# unknown statuses pass through untouched; only known vocabulary is normalized.
ROW_STATUSES = ("to_review", "to_revisit", "corrected", "reviewed", "revisited", "resolved")
MISTAKE_RESOLVED = frozenset(("corrected", "reviewed", "resolved"))
CONFUSION_RESOLVED = frozenset(("revisited", "resolved"))

# ---------------------------------------------------------------- zh display (verbatim v3 wording)
_ZH = {
    "mode.from_scratch": "零基础从头讲",
    "mode.shore_up": "某章起步补弱",
    "mode.fill_gaps": "查缺补漏",
    "tier.le1d": "≤1天",
    "tier.d1_3": "1-3天",
    "tier.d3_7": "3-7天",
    "tier.gt7d": ">7天",
    "lang.zh": "中文",
    "lang.en": "English",
    "lang.bilingual": "双语",
    "artifact.chat": "对话省额",
    "artifact.visual": "视觉教材",
    "window.in_window": "在窗口",
    "window.out_window": "窗口外",
    "window.verified": "已实测",
    "row.to_review": "待复盘",
    "row.to_revisit": "待回顾",
    "row.corrected": "已订正",
    "row.reviewed": "已复盘",
    "row.revisited": "已回顾",
    "row.resolved": "已解决",
    # v4-P4 notebook engine (scripts/notebook.py) — entry-type labels + index headings
    "notebook_type.walkthrough": "精讲",
    "notebook_type.feedback": "判分",
    "notebook_type.confusion": "疑难",
    "notebook_type.review": "复盘",
    "notebook.index_title": "# 📒 学习笔记目录",
    "notebook.chapter_heading": "第 %(num)s 章",
    "mistakes.index_title": "# ❌ 错题本目录",
    "mistakes.status_suffix": "｜ 状态：%(status)s",
}
_EN = {
    "mode.from_scratch": "teach from scratch",
    "mode.shore_up": "start mid-course, shore up weak spots",
    "mode.fill_gaps": "fill the gaps",
    "tier.le1d": "≤1 day",
    "tier.d1_3": "1-3 days",
    "tier.d3_7": "3-7 days",
    "tier.gt7d": ">7 days",
    "lang.zh": "Chinese",
    "lang.en": "English",
    "lang.bilingual": "Bilingual",
    "artifact.chat": "chat-only",
    "artifact.visual": "visual study guide",
    "window.in_window": "in window",
    "window.out_window": "out of window",
    "window.verified": "verified by quiz",
    "row.to_review": "to review",
    "row.to_revisit": "to revisit",
    "row.corrected": "corrected",
    "row.reviewed": "reviewed",
    "row.revisited": "revisited",
    "row.resolved": "resolved",
    # v4-P4 notebook engine (scripts/notebook.py) — entry-type labels + index headings
    "notebook_type.walkthrough": "Walkthrough",
    "notebook_type.feedback": "Feedback",
    "notebook_type.confusion": "Confusion",
    "notebook_type.review": "Review",
    "notebook.index_title": "# 📒 Notebook index",
    "notebook.chapter_heading": "Chapter %(num)s",
    "mistakes.index_title": "# ❌ Mistake-notebook index",
    "mistakes.status_suffix": "| Status: %(status)s",
}
_EMBEDDED = {"zh": _ZH, "en": _EN}

# ---------------------------------------------------------------- input → code maps
# Every zh display maps back to its code (round-trip through the generated md view),
# plus the historical loose aliases carried over VERBATIM from update_progress.py.
_MODE_IN = {c: c for c in MODES}
_MODE_IN.update({_ZH["mode." + c]: c for c in MODES})
# en 显示词与常见英文松散别名同样归代号（Codex r1：en 首问流把 "teach from scratch" 存成
# 未知自由串，下游选择器命不中 MODES 便静默退回 fill_gaps——tier/language 早已收 en，模式补齐）。
# ASCII 输入在 canon_mode 里统一 lower() 后查表，故此处键全小写。
_MODE_IN.update({_EN["mode." + c].lower(): c for c in MODES})
_MODE_IN.update({
    "from scratch": "from_scratch", "teach-from-scratch": "from_scratch",
    "shore up": "shore_up", "shore up weak spots": "shore_up",
    "start mid-course": "shore_up", "mid-course": "shore_up",
    "fill gaps": "fill_gaps", "fill-the-gaps": "fill_gaps", "gap filling": "fill_gaps",
})

# Legacy four modes (normal/sprint/panic/mock) → (new mode code, implied tier code or None).
MODE_MIGRATION = {
    "panic": ("from_scratch", "le1d"),
    "sprint": ("fill_gaps", "d1_3"),
    "normal": ("fill_gaps", None),
    "mock": ("fill_gaps", None),
}

_TIER_IN = {c: c for c in TIERS}
_TIER_IN.update({_ZH["tier." + c]: c for c in TIERS})
_TIER_IN.update({
    "<=1天": "le1d", "1天": "le1d", "当天": "le1d", "今天": "le1d",
    "一天": "le1d", "考前一天": "le1d", "明天考": "le1d",
    "1—3天": "d1_3", "1~3天": "d1_3", "2-3天": "d1_3", "几天": "d1_3",
    "3—7天": "d3_7", "3~7天": "d3_7", "一周": "d3_7", "一周内": "d3_7",
    "＞7天": "gt7d", "7天以上": "gt7d", "一周以上": "gt7d", "还早": "gt7d",
    "时间充裕": "gt7d",
    # en-side loose aliases (the en pack's students type these)
    "1 day": "le1d", "1-3 days": "d1_3", "3-7 days": "d3_7", ">7 days": "gt7d",
})
_TIER_IN.update({_EN["tier." + c].lower(): c for c in TIERS})   # en 显示词整词也收（与模式同理）

_LANG_IN = {c: c for c in LANGS}
_LANG_IN.update({_ZH["lang." + c]: c for c in LANGS})
_LANG_IN.update({
    # ASCII aliases are matched case-insensitively (see canon_language)
    "zh-cn": "zh", "chinese": "zh", "简体中文": "zh", "汉语": "zh", "中": "zh",
    "english": "en", "英文": "en", "英语": "en",
    "bi": "bilingual", "zh+en": "bilingual", "中英": "bilingual", "中英双语": "bilingual",
})

_ARTIFACT_IN = {c: c for c in ARTIFACT_MODES}
_ARTIFACT_IN.update({_ZH["artifact." + c]: c for c in ARTIFACT_MODES})
_ARTIFACT_IN.update({_EN["artifact." + c].lower(): c for c in ARTIFACT_MODES})
_ARTIFACT_IN.update({
    # Explicit output-resource choices only.  These aliases do not inspect or infer a
    # subscription tier; callers must persist a choice made by the user/host.
    "对话模式": "chat", "只在对话教学": "chat", "仅对话": "chat", "聊天教学": "chat",
    "省额度": "chat", "省token": "chat", "低token": "chat", "v3": "chat",
    "chat only": "chat", "conversation only": "chat", "low-token": "chat",
    "save tokens": "chat",
    "打印pdf": "visual", "生成pdf": "visual", "pdf": "visual", "可打印教材": "visual",
    "完整教材": "visual", "不在乎token": "visual", "不在乎 token": "visual",
    "token不敏感": "visual", "token 不敏感": "visual",
    "study guide": "visual", "printable": "visual", "token-insensitive": "visual",
    "print pdf": "visual", "visual": "visual",
})

_WINDOW_IN = {c: c for c in WINDOW_STATUSES}
_WINDOW_IN.update({_ZH["window." + c]: c for c in WINDOW_STATUSES})
_WINDOW_IN.update({_EN["window." + c]: c for c in WINDOW_STATUSES})

_ROW_IN = {c: c for c in ROW_STATUSES}
_ROW_IN.update({_ZH["row." + c]: c for c in ROW_STATUSES})
_ROW_IN.update({_EN["row." + c]: c for c in ROW_STATUSES})


# ---------------------------------------------------------------- normalizers
def canon_mode(v):
    """→ (code 或原值, 迁移带出的 tier code 或 None, warning 或 None)。
    v4 代号/zh 显示词原样归代号；旧四模式迁移 + 警告；未知值保留但警告（绝不静默改写）。"""
    v = (v or "").strip()
    if v in _MODE_IN:
        return _MODE_IN[v], None, None
    if v.lower() in _MODE_IN:          # en 显示词/别名大小写不敏感（zh 键不受 lower 影响）
        return _MODE_IN[v.lower()], None, None
    if v in MODE_MIGRATION:
        code, tier = MODE_MIGRATION[v]
        return code, tier, ("旧模式「%s」已废弃，迁移为「%s」%s（新模式仅 %s）"
                            % (v, display("mode", code),
                               ("＋时间宽裕度「%s」" % display("tier", tier)) if tier else "",
                               "/".join(display("mode", c) for c in MODES)))
    return v, None, ("非标准学习模式「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
                     % (v, "/".join(display("mode", c) for c in MODES)))


def canon_tier(v):
    """→ (code 或原值, warning 或 None)。"""
    v = (v or "").strip()
    if v in _TIER_IN:
        return _TIER_IN[v], None
    if v.lower() in _TIER_IN:          # en 显示词大小写不敏感（与 canon_mode 同理）
        return _TIER_IN[v.lower()], None
    return v, ("非标准时间宽裕度「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
               % (v, "/".join(display("tier", c) for c in TIERS)))


def canon_language(v):
    """→ (code 或原值, warning 或 None)。ASCII 别名不区分大小写；未知值原样保留并告警。"""
    v = (v or "").strip()
    if v in _LANG_IN:
        return _LANG_IN[v], None
    key = v.lower()
    if key in _LANG_IN:
        return _LANG_IN[key], None
    return v, ("非标准语言偏好「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
               % (v, "/".join(LANGS)))


def canon_artifact_mode(v):
    """→ (code 或原值, warning 或 None)。

    This normalizes an explicit resource-output preference only.  Unknown values are retained
    for auditability; :func:`workspace_artifact_mode` separately fails safe to ``chat``.
    """
    v = (v or "").strip()
    if v in _ARTIFACT_IN:
        return _ARTIFACT_IN[v], None
    key = v.lower()
    if key in _ARTIFACT_IN:
        return _ARTIFACT_IN[key], None
    return v, ("非标准输出资源模式「%s」——canonical 仅 %s；已按原值保留，运行时回退为 chat"
               % (v, "/".join(ARTIFACT_MODES)))


def canon_window_status(v):
    """已知窗口状态词（代号/中/英显示）→ 代号；未知原样返回（是否拒绝由调用方决定）。"""
    v = (v or "").strip()
    return _WINDOW_IN.get(v, v)


def canon_row_status(v):
    """已知行状态词 → 代号；未知自由字符串原样通过（set-*-status 保持自由词容忍）。"""
    v = (v or "").strip()
    return _ROW_IN.get(v, v)


# ---------------------------------------------------------------- display / catalogs
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_catalog_cache = {}


def catalog(lang):
    """Embedded catalog merged with locales/<lang>/messages.json when it exists (P2 fills it).
    Unknown lang falls back to zh (the historical canonical wording, fullest coverage)."""
    lang = lang if lang in _EMBEDDED else "zh"
    if lang in _catalog_cache:
        return _catalog_cache[lang]
    cat = dict(_EMBEDDED[lang])
    path = os.path.join(_REPO_ROOT, "locales", lang, "messages.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                overlay = json.load(f)
            if isinstance(overlay, dict):
                cat.update({k: v for k, v in overlay.items() if isinstance(v, str)})
        except (OSError, ValueError):
            pass    # a broken pack must never take the engine down — embedded wording wins
    _catalog_cache[lang] = cat
    return cat


def display(kind, code, lang="zh"):
    """code → student-visible string; non-canonical values pass through untouched
    (free-string statuses / user-kept unknown values render as themselves)."""
    if code is None:
        return code
    return catalog(lang).get("%s.%s" % (kind, code), code)


def msg(msgid, lang="zh", **fmt):
    """Script-message lookup by msgid (inventory lands with the pack split; until a msgid is
    catalogued, callers keep their literal strings — this function is the forward seam)."""
    s = catalog(lang).get(msgid)
    if s is None:
        return None
    return s % fmt if fmt else s


def workspace_language(state):
    """study_state 的 language 字段（新代号/旧显示词均可）→ 'zh' | 'en' | 'bilingual'。
    None/未知 → 'zh'（历史 canonical，兼容旧工作区）。"""
    v = (state or {}).get("language") if isinstance(state, dict) else state
    code, _w = canon_language(v or "")
    return code if code in LANGS else "zh"


def workspace_artifact_mode(state):
    """Return the effective output-resource mode, failing safe for old or unknown state.

    Missing fields keep v3-compatible chat teaching.  A PDF/visual workflow is therefore
    enabled only by a recognized, explicitly persisted ``visual`` choice.
    """
    v = (state or {}).get("artifact_mode") if isinstance(state, dict) else state
    if not isinstance(v, str):
        return "chat"
    code, _w = canon_artifact_mode(v)
    return code if code in ARTIFACT_MODES else "chat"
