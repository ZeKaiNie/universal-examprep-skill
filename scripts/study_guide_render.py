#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render one chapter as a typed Study Guide or an explicitly branded source packet.

Markdown/JSON files remain the auditable sources.  This renderer builds a human-facing HTML
view with native MathML and data-URI images, then optionally asks a local Edge/Chrome to print
the same artifact to PDF.

Exit codes: 0 success; 1 render/print failure; 2 unsafe or invalid input; 3 missing optional
dependency (latex2mathml or a local browser).  A missing math dependency or malformed legacy
formula never leaves an old/partial HTML behind.  A missing browser preserves the already
validated HTML and removes any stale PDF.
"""
import argparse
import base64
import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

import i18n
from ingestion import workspace_publication_lock
from check_deps import (LATEX2MATHML_PIN, LATEX2MATHML_VERSION, build_report,
                        installed_distribution_version)
from validate_workspace import LATEX_COMMAND_RE, workspace_asset_policy_snapshot
from asset_policy import physical_asset_key, workspace_asset_is_student_attempt
from ingestion.identifiers import normalize_workspace_path
try:
    from .image_validation import (
        ImageValidationError, validate_image_blob as _shared_validate_image_blob,
    )
except ImportError:
    from image_validation import (
        ImageValidationError, validate_image_blob as _shared_validate_image_blob,
    )


for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass


QUESTION_ROLES = {"question_context", "figure", "diagram", "table"}
ANSWER_ROLES = {"answer_context", "worked_solution"}
ALL_ROLES = QUESTION_ROLES | ANSWER_ROLES | {"student_attempt"}
ARTIFACT_RECEIPT_SCHEMA_VERSION = 2
SOURCE_LABELS_ZH = {
    "teacher": "🟢 来自资料",
    "material": "🟢 来自资料",
    "mixed": "🟡 AI补充，可能与你老师讲的不完全一致",
    "ai_generated": "⚠️ AI生成答案，非老师/教材提供",
    "unknown": "来源未知",
}
SOURCE_LABELS_EN = {
    "teacher": "🟢 From your materials",
    "material": "🟢 From your materials",
    "mixed": "🟡 AI-supplemented — may differ from what your teacher taught",
    "ai_generated": "⚠️ AI-generated answer — not from your teacher or textbook",
    "unknown": "Source unknown",
}
CANON_LANGUAGES = {"中文", "English", "双语"}
LANGUAGE_CODE_TO_UI = {"zh": "中文", "en": "English", "bilingual": "双语"}
HTML_LANG = {"中文": "zh-CN", "English": "en", "双语": "mul"}

UI_ZH = {
    "true": "正确", "false": "错误", "source_anchor": "原页锚点",
    "material_image": "资料图片", "prompt_source": "题面来源", "answer_source": "答案来源",
    "source_unknown_file": "来源文件未知", "source_label": "来源标签",
    "prompt_asset": "题面图", "answer_asset": "答案图",
    "teaching_empty": "本章没有可用的教学例题记录；未虚构补题。",
    "example": "例题 {index}：{title}", "prompt": "题面", "walkthrough_answer": "讲解与答案",
    "worked_demonstration": "材料示范过程",
    "prompt_missing": "题面文字缺失；请核对题面图和原资料。",
    "teaching_answer_missing": "资料未提供可展示的标准答案。", "plain_explanation": "通俗解释",
    "quiz_empty": "本章题库没有可展示的题目；未现场编题。",
    "quiz": "Quiz {index} · {id}", "quiz_prompt_missing": "题干缺失；请返回题库修复，不能猜题。",
    "unknown_answer": "资料里没有这道题的答案。", "quiz_answer_missing": "本题没有可展示的答案。",
    "answer_asset_only": "未提供结构化文字答案；请查看下方答案图。",
    "details": "展开答案与解析", "answer": "答案", "analysis": "解析",
    "notebook_empty": "本章 notebook 尚无落盘讲解；此处不会伪造学习记录。",
    "manifest_missing": "旧工作区未提供 teaching_examples.json；教学例题层按空层展示。",
    "title": "第 {chapter} 章 · 人类可读复习教材",
    "subtitle": "Markdown / JSON 保持为事实源；本页是离线、可打印的阅读视图。",
    "guide_source": "知识源：{wiki} ｜ 教学例题：{teaching} ｜ Quiz：{quiz} ｜ Notebook：{notebook}",
    "notebook_yes": "有", "notebook_no": "无",
    "concepts_heading": "一、核心概念与课件内容", "examples_heading": "二、教学例题",
    "quiz_heading": "三、Quiz 与考试练习", "notebook_heading": "四、你的详细讲解与复盘 Notebook",
}
UI_EN = {
    "true": "True", "false": "False", "source_anchor": "Original-page anchor",
    "material_image": "Material image", "prompt_source": "Question source", "answer_source": "Answer source",
    "source_unknown_file": "Source file unknown", "source_label": "Provenance",
    "prompt_asset": "Question-side asset", "answer_asset": "Answer-side asset",
    "teaching_empty": "No teaching example is available for this chapter; none was invented.",
    "example": "Example {index}: {title}", "prompt": "Question", "walkthrough_answer": "Walkthrough and answer",
    "worked_demonstration": "Worked demonstration",
    "prompt_missing": "Question text is missing; check the question-side asset and original material.",
    "teaching_answer_missing": "The materials do not provide a displayable standard answer.",
    "plain_explanation": "Plain-language explanation",
    "quiz_empty": "The question bank has no displayable item for this chapter; no item was invented.",
    "quiz": "Quiz {index} · {id}",
    "quiz_prompt_missing": "The prompt is missing; repair the question bank instead of guessing it.",
    "unknown_answer": "The materials do not contain an answer to this question.",
    "quiz_answer_missing": "This item has no displayable answer.",
    "answer_asset_only": "No structured text answer is available; use the answer-side asset below.",
    "details": "Show answer and explanation", "answer": "Answer", "analysis": "Explanation",
    "notebook_empty": "This chapter has no persisted notebook walkthrough; no learning record was invented.",
    "manifest_missing": "This legacy workspace has no teaching_examples.json; the teaching layer is shown as empty.",
    "title": "Chapter {chapter} · Human-readable Study Guide",
    "subtitle": "Markdown and JSON remain the sources of truth; this is an offline, printable reading view.",
    "guide_source": "Knowledge source: {wiki} | Teaching examples: {teaching} | Quiz: {quiz} | Notebook: {notebook}",
    "notebook_yes": "present", "notebook_no": "absent",
    "concepts_heading": "1. Core Concepts and Course Materials", "examples_heading": "2. Teaching Examples",
    "quiz_heading": "3. Quiz and Exam Practice", "notebook_heading": "4. Detailed Walkthrough and Review Notebook",
}


def _ui_pair(key, **values):
    """Return the two fixed UI renderings without translating persisted course facts."""
    return UI_ZH[key].format(**values), UI_EN[key].format(**values)


def _ui(language, key, **values):
    """Return plain UI text for non-HTML contexts.

    Bilingual *HTML* must use :func:`_ui_html` so the two languages remain independent DOM
    blocks.  The slash form here is intentionally limited to attributes/debug values that cannot
    contain block markup.
    """
    zh, en = _ui_pair(key, **values)
    if language == "中文":
        return zh
    if language == "English":
        return en
    return "%s / %s" % (zh, en)


def _language_blocks_html(language, zh, en):
    """Render fixed UI as one block per language; dynamic facts are passed through unchanged."""
    if language == "中文":
        return html.escape(str(zh))
    if language == "English":
        return html.escape(str(en))
    return (
        '<span class="lang-block lang-zh" lang="zh-CN">%s</span>'
        '<span class="lang-block lang-en" lang="en">&gt; EN: %s</span>'
        % (html.escape(str(zh)), html.escape(str(en)))
    )


def _ui_html(language, key, **values):
    zh, en = _ui_pair(key, **values)
    return _language_blocks_html(language, zh, en)


def _ui_fact_html(language, key, fact, separator=None):
    """Attach the same persisted fact to localized labels without translating that fact."""
    zh_label, en_label = _ui_pair(key)
    fact = str(fact)
    if separator is None:
        zh = "%s：%s" % (zh_label, fact)
        en = "%s: %s" % (en_label, fact)
    else:
        zh = "%s%s%s" % (zh_label, separator, fact)
        en = "%s%s%s" % (en_label, separator, fact)
    return _language_blocks_html(language, zh, en)
MATHML_FORBIDDEN_TAGS = {"script", "style", "iframe", "object", "embed", "link", "img"}
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
ET.register_namespace("", MATHML_NS)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BLOCK_DOLLAR_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.S)
_INLINE_DOLLAR_RE = re.compile(r"(?<![\\$])\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)")
_LEGACY_PAREN_RE = re.compile(r"(?<!\\)\((?=[^()\n]*\\[A-Za-z]+)[^()\n]+\)")
_LEGACY_BRACKET_RE = re.compile(r"(?<!\\)\[(?=[^\[\]\n]*\\[A-Za-z]+)[^\[\]\n]+\]")
_SOURCE_COMMENT_RE = re.compile(r"^\s*<!--\s*(.*?)\s*-->\s*$")


class GuideError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


class ArtifactDriftError(GuideError):
    """A source/runtime changed while an artifact publication was in flight."""

    def __init__(self, message):
        super().__init__("artifact input drift: %s" % message, 1)


class MissingMathDependency(GuideError):
    def __init__(self, detected_version=None):
        command = '"%s" -m pip install %s' % (sys.executable, LATEX2MATHML_PIN)
        detail = ("检测到未经本技能审计的 latex2mathml==%s；" % detected_version
                  if detected_version else "缺少离线 MathML 转换依赖；")
        super().__init__(
            "检测到标准 LaTeX 公式，但%s必须使用固定版本。请运行：%s" % (detail, command),
            3,
        )


def _reject_constant(value):
    raise ValueError("non-standard JSON constant %s" % value)


def _contained(ws, path):
    ws_real = os.path.normcase(os.path.realpath(ws))
    real = os.path.normcase(os.path.realpath(path))
    return real == ws_real or real.startswith(ws_real + os.sep)


def _reject_symlink_components(ws, path, label):
    """Reject every path component below workspace, even when a link resolves back inside it."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(ws))
    except ValueError:
        raise GuideError("%s 与 workspace 不在同一文件系统" % label)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise GuideError("%s 逃出 workspace" % label)
    cur = ws
    for part in (() if rel == "." else rel.split(os.sep)):
        cur = os.path.join(cur, part)
        try:
            reparse = _is_reparse_stat(os.lstat(cur))
        except OSError:
            reparse = False
        if os.path.islink(cur) or reparse:
            raise GuideError("%s 含符号链接路径组件：%s" % (label, part))


def _guard_workspace(workspace):
    ws = os.path.abspath(workspace)
    if (os.path.islink(ws)
            or (os.path.lexists(ws) and _is_reparse_stat(os.lstat(ws)))):
        raise GuideError("--workspace 不得是符号链接：%s" % workspace)
    if not os.path.isdir(ws):
        raise GuideError("workspace 不存在或不是目录：%s" % workspace)
    return ws


def _guard_existing_file(ws, path, label):
    if not os.path.lexists(path):
        raise GuideError("缺少 %s" % label)
    _reject_symlink_components(ws, path, label)
    if not _contained(ws, path):
        raise GuideError("%s 经路径解析逃出 workspace，拒绝读取" % label)
    if not os.path.isfile(path):
        raise GuideError("%s 不是普通文件" % label)
    return path


def _guard_optional_file(ws, path, label):
    # lexists catches broken links; treating one as an absent optional layer would bypass safety.
    if not os.path.lexists(path):
        return None
    return _guard_existing_file(ws, path, label)


def _safe_relative_parts(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GuideError("%s 必须是非空的 workspace 相对路径" % label)
    raw = value.strip()
    # The renderer accepts one spelling for one physical file.  In particular, do not
    # normalize Markdown backslashes or discard empty/dot components: either operation would
    # let a tainted asset acquire a second spelling after the policy snapshot was built.
    if raw != value or "\\" in raw:
        raise GuideError("%s 必须使用规范的正斜杠相对路径：%s" % (label, raw))
    try:
        canonical = normalize_workspace_path(raw)
    except ValueError as exc:
        raise GuideError("%s 不是安全、规范的 workspace 相对路径（%s）：%s"
                         % (label, exc, raw))
    if canonical != raw:
        raise GuideError("%s 不是规范的 workspace 相对路径：%s" % (label, raw))
    return canonical.split("/")


def _validate_provenance_path(value, label):
    if value is None:
        return
    _safe_relative_parts(value, label)


_ASSET_POLICY_UNSET = object()


class _VerifiedTaintedKeys(frozenset):
    """Workspace-bound capability produced only after a complete live policy read."""

    def __new__(cls, values, workspace, policy):
        value = super(_VerifiedTaintedKeys, cls).__new__(cls, values)
        value.workspace_real = os.path.normcase(os.path.realpath(os.path.abspath(workspace)))
        value.policy = policy
        return value


def _workspace_tainted_asset_keys(ws, label):
    """Load the complete three-layer policy; omission must never mean "no taint"."""
    try:
        snapshot = workspace_asset_policy_snapshot(ws)
    except (OSError, UnicodeError, ValueError) as exc:
        raise GuideError("%s cannot build the complete workspace asset policy: %s" %
                         (label, exc))
    if snapshot["unsafe_paths"]:
        raise GuideError("%s found an unsafe workspace asset declaration: %s" %
                         (label, snapshot["unsafe_paths"][0]))
    if snapshot["conflicts"]:
        raise GuideError("%s found a student-attempt/asset-role conflict: %s" %
                         (label, snapshot["conflicts"][0]))
    return _VerifiedTaintedKeys(snapshot["tainted_keys"], ws, snapshot)


def _coerce_tainted_asset_keys(ws, supplied, label):
    """Never let an arbitrary caller-supplied empty set weaken the live policy."""

    workspace_real = os.path.normcase(os.path.realpath(os.path.abspath(ws)))
    if (isinstance(supplied, _VerifiedTaintedKeys)
            and supplied.workspace_real == workspace_real
            and isinstance(getattr(supplied, "policy", None), dict)):
        return supplied
    live = _workspace_tainted_asset_keys(ws, label)
    if supplied is _ASSET_POLICY_UNSET or supplied is None:
        return live
    supplied_policy = supplied if isinstance(supplied, dict) else None
    try:
        extra = (set(supplied_policy.get("tainted_keys", ()))
                 if supplied_policy is not None else set(supplied))
    except TypeError as exc:
        raise GuideError("%s received an invalid asset-policy key collection: %s" %
                         (label, exc))
    if any(not isinstance(key, str) for key in extra):
        raise GuideError("%s received a non-string asset-policy key" % label)
    merged_policy = dict(live.policy)
    merged_policy["tainted_keys"] = frozenset(set(live) | extra)
    if supplied_policy is not None:
        try:
            identities = set(supplied_policy.get("tainted_identity_keys", ()))
        except TypeError as exc:
            raise GuideError("%s received invalid asset identity capabilities: %s" %
                             (label, exc))
        if any(not isinstance(key, str) for key in identities):
            raise GuideError("%s received a non-string asset identity capability" % label)
        merged_policy["tainted_identity_keys"] = frozenset(
            set(live.policy.get("tainted_identity_keys", ())) | identities
        )
    return _VerifiedTaintedKeys(merged_policy["tainted_keys"], ws, merged_policy)


def _resolve_asset(ws, rel, label, allow_wiki_parent_asset=False,
                   student_attempt_tainted_keys=_ASSET_POLICY_UNSET, taint_message=None):
    """Resolve a workspace asset.

    Normal sources must use workspace-relative paths and may never contain ``..``.  The only
    compatibility exception is the exact ``../assets/<safe tail>`` shape emitted inside
    ``references/wiki/*.md`` by build_visual_index.  It is remapped to
    ``<ws>/references/assets/<tail>`` and receives the same component/symlink checks.
    """
    student_attempt_tainted_keys = _coerce_tainted_asset_keys(
        ws, student_attempt_tainted_keys, label)
    raw = rel.strip() if isinstance(rel, str) else rel
    wiki_compat = bool(
        allow_wiki_parent_asset and isinstance(raw, str) and raw.startswith("../assets/")
    )
    if wiki_compat:
        tail = raw[len("../assets/"):]
        tail_parts = _safe_relative_parts(tail, label)
        parts = ["references", "assets"] + tail_parts
    else:
        parts = _safe_relative_parts(rel, label)
    cur = ws
    for part in parts:
        cur = os.path.join(cur, part)
        try:
            reparse = os.path.lexists(cur) and _is_reparse_stat(os.lstat(cur))
        except OSError:
            reparse = False
        if os.path.islink(cur) or reparse:
            raise GuideError("%s 含符号链接路径组件，拒绝读取：%s" % (label, rel))
    if not _contained(ws, cur):
        raise GuideError("%s 逃出 workspace：%s" % (label, rel))
    if wiki_compat:
        assets_root = os.path.join(ws, "references", "assets")
        root_real = os.path.normcase(os.path.realpath(assets_root))
        cur_real = os.path.normcase(os.path.realpath(cur))
        if cur_real != root_real and not cur_real.startswith(root_real + os.sep):
            raise GuideError("wiki ../assets 兼容路径未落在 references/assets：%s" % rel)
    if not os.path.isfile(cur):
        raise GuideError("%s 图片不存在：%s" % (label, rel))
    # Compare taint only after resolving the validated path beneath the canonical workspace.
    # This keeps the wiki ../assets compatibility spelling and the stored workspace spelling on
    # the same physical identity without trusting either raw Markdown string as the key.
    ws_real = os.path.realpath(os.path.abspath(ws))
    cur_real = os.path.realpath(cur)
    try:
        resolved_relative = os.path.relpath(cur_real, ws_real).replace("\\", "/")
        resolved_relative = normalize_workspace_path(resolved_relative)
    except (OSError, ValueError) as exc:
        raise GuideError("%s 无法建立规范的 workspace 资产身份（%s）：%s"
                         % (label, exc, rel))
    taint_key = physical_asset_key(resolved_relative)
    if taint_key is None:
        raise GuideError("%s 无法建立安全的资产策略键：%s" % (label, rel))
    try:
        tainted = workspace_asset_is_student_attempt(
            resolved_relative, ws, student_attempt_tainted_keys.policy
        )
    except ValueError as exc:
        raise GuideError("%s 无法核验实时物理资产身份（%s）：%s" %
                         (label, exc, rel))
    if tainted:
        raise GuideError(
            (taint_message or "%s 绑定到 student_attempt 污染资产：%%s" % label) % rel
        )
    mime = mimetypes.guess_type(cur)[0]
    if not mime or not mime.startswith("image/"):
        raise GuideError("%s 不是可识别的图片文件：%s" % (label, rel))
    try:
        with open(cur, "rb") as f:
            blob = f.read()
    except OSError as exc:
        raise GuideError("%s 图片不可读（%s）：%s" % (label, exc, rel))
    _validate_image_blob(mime, blob, "%s（%s）" % (label, rel))
    return "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode("ascii"))


def _validate_image_blob(mime, blob, label):
    """Use deterministic signatures so a readable-but-corrupt file cannot become a broken figure."""
    if mime == "image/svg+xml":
        raise GuideError(
            "%s 是 SVG；自包含教材拒绝潜在可执行图片，请先转为 PNG" % label
        )
    supported = {
        "image/png", "image/jpeg", "image/jpg", "image/gif",
        "image/webp", "image/bmp", "image/x-ms-bmp",
    }
    if mime not in supported:
        raise GuideError("%s 使用不受支持的图片 MIME：%s" % (label, mime))
    try:
        _shared_validate_image_blob(mime, blob)
    except ImageValidationError as exc:
        raise GuideError(
            "%s 图片内容损坏或与扩展名不符（%s）" % (label, exc)
        )


def _read_text(ws, path, label):
    _guard_existing_file(ws, path, label)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise GuideError("%s 必须是可读 UTF-8 文本（%s）" % (label, exc))


def _read_json_array(ws, path, label, optional=False):
    path = _guard_optional_file(ws, path, label) if optional else _guard_existing_file(ws, path, label)
    if path is None:
        return [], True
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise GuideError("%s 不是合法 UTF-8 JSON（%s）" % (label, exc))
    if not isinstance(data, list):
        raise GuideError("%s 顶层必须是 JSON 数组" % label)
    return data, False


def _read_workspace_language(ws):
    path = _guard_optional_file(ws, os.path.join(ws, "study_state.json"), "study_state.json")
    if path is None:
        return "中文"
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise GuideError("study_state.json 不是合法 UTF-8 JSON（%s）" % exc)
    if not isinstance(state, dict):
        raise GuideError("study_state.json 顶层必须是对象")
    raw_language = state.get("language")
    if raw_language in (None, ""):
        return "中文"
    if not isinstance(raw_language, str):
        raise GuideError("study_state.json.language 必须是字符串或 null，当前为 %r" % raw_language)
    code, _warning = i18n.canon_language(raw_language)
    if code not in i18n.LANGS:
        raise GuideError(
            "study_state.json.language 必须是 canonical zh/en/bilingual（兼容旧显示词中文、English、双语），当前为 %r"
            % raw_language
        )
    return LANGUAGE_CODE_TO_UI[code]


def _chapter_matches(item, chapter):
    if not isinstance(item, dict):
        return False
    wanted = str(chapter)
    return any(item.get(k) is not None and str(item.get(k)) == wanted for k in ("chapter", "phase"))


def _find_wiki(ws, chapter):
    directory = os.path.join(ws, "references", "wiki")
    _reject_symlink_components(ws, directory, "references/wiki")
    if not os.path.isdir(directory) or not _contained(ws, directory):
        raise GuideError("缺少安全的 references/wiki 目录")
    pat = re.compile(r"^ch0*%d(?:[^0-9].*)?\.md$" % chapter, re.I)
    names = [n for n in os.listdir(directory) if pat.match(n)]
    if len(names) != 1:
        raise GuideError(
            "第 %d 章必须恰好对应一个 chNN*.md wiki；当前匹配 %d 个：%s"
            % (chapter, len(names), ", ".join(sorted(names)) or "无")
        )
    path = os.path.join(directory, names[0])
    return path, "references/wiki/" + names[0]


def load_chapter_sources(workspace, chapter):
    """Load only the selected chapter's render inputs and immediately discard other rows."""
    ws = _guard_workspace(workspace)
    language = _read_workspace_language(ws)
    wiki_path, wiki_rel = _find_wiki(ws, chapter)
    wiki = _read_text(ws, wiki_path, wiki_rel)

    try:
        asset_policy = workspace_asset_policy_snapshot(ws)
    except ValueError as exc:
        raise GuideError("无法建立完整 student-attempt 资产策略快照：%s" % exc)
    if asset_policy["unsafe_paths"]:
        raise GuideError("工作区含不安全资产声明：%s" % asset_policy["unsafe_paths"][0])
    if asset_policy["conflicts"]:
        raise GuideError(
            "工作区含 student-attempt/题面/答案资产角色冲突：%s"
            % asset_policy["conflicts"][0]
        )
    teaching_all = asset_policy["teaching_rows"]
    teaching_missing = not os.path.lexists(
        os.path.join(ws, "references", "teaching_examples.json")
    )
    teaching = [row for row in teaching_all if _chapter_matches(row, chapter)]
    quiz_all = asset_policy["quiz_rows"]
    quizzes = [row for row in quiz_all if _chapter_matches(row, chapter)]

    notebook_path = os.path.join(ws, "notebook", "ch%02d.md" % chapter)
    nb = _guard_optional_file(ws, notebook_path, "notebook/ch%02d.md" % chapter)
    notebook = _read_text(ws, nb, "notebook/ch%02d.md" % chapter) if nb else ""
    return {
        "workspace": ws,
        "chapter": chapter,
        "language": language,
        "wiki": wiki,
        "wiki_rel": wiki_rel,
        "teaching": teaching,
        "teaching_manifest_missing": teaching_missing,
        "quizzes": quizzes,
        "notebook": notebook,
        "student_attempt_tainted_keys": asset_policy["tainted_keys"],
    }


class _MathConverter:
    def __init__(self, converter=None):
        self.converter = converter

    def get(self):
        if self.converter is not None:
            return self.converter
        if os.environ.get("EXAMPREP_NO_MATHML") == "1":
            raise MissingMathDependency()
        installed_version = installed_distribution_version("latex2mathml")
        if installed_version != LATEX2MATHML_VERSION:
            raise MissingMathDependency(installed_version)
        try:
            from latex2mathml.converter import convert
        except (ImportError, ModuleNotFoundError):
            raise MissingMathDependency()
        self.converter = convert
        return self.converter


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _sanitize_mathml(value, display):
    if not isinstance(value, str):
        raise GuideError("latex2mathml.convert 必须返回字符串", 1)
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise GuideError("latex2mathml 返回了无效 MathML（%s）" % exc, 1)
    if _local_name(root.tag) != "math":
        raise GuideError("latex2mathml 输出根节点不是 <math>", 1)
    for parent in list(root.iter()):
        for child in list(parent):
            if _local_name(child.tag) in {"annotation", "annotation-xml"}:
                parent.remove(child)  # remove invisible raw-TeX payloads from the final artifact
        if _local_name(parent.tag) in MATHML_FORBIDDEN_TAGS:
            raise GuideError("MathML 输出包含禁止元素 <%s>" % _local_name(parent.tag), 1)
        for key in parent.attrib:
            low = _local_name(key)
            if low.startswith("on") or low in {"href", "src", "style"}:
                raise GuideError("MathML 输出包含不安全属性 %s" % key, 1)
    visible_math = "".join(root.itertext())
    if re.search(r"\\[A-Za-z]+", visible_math):
        raise GuideError("MathML 输出仍含人类可见的 raw LaTeX 命令", 1)
    root.set("display", "block" if display else "inline")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _legacy_formula(segment):
    for rx in (_LEGACY_PAREN_RE, _LEGACY_BRACKET_RE, LATEX_COMMAND_RE):
        m = rx.search(segment)
        if m:
            line = segment.count("\n", 0, m.start()) + 1
            snippet = m.group(0).replace("\n", " ")[:100]
            return line, snippet
    unresolved = re.search(r"\\(?:\(|\)|\[|\])|(?<!\\)\$\$", segment)
    if unresolved:
        line = segment.count("\n", 0, unresolved.start()) + 1
        return line, unresolved.group(0)
    return None


def _convert_math_segment(segment, converter, tokens):
    def replace(display):
        def inner(match):
            latex = match.group(1).strip()
            if not latex:
                raise GuideError("空 LaTeX 分隔符不是有效公式")
            try:
                rendered = converter.get()(latex, display="block" if display else "inline")
            except MissingMathDependency:
                raise
            except Exception as exc:
                raise GuideError("LaTeX 转 MathML 失败：%s" % exc, 1)
            safe = _sanitize_mathml(rendered, display)
            token = "STUDYGUIDEMATHTOKEN%06dZZ" % len(tokens)
            tokens[token] = (
                '<span class="math-display" role="math">%s</span>' % safe
                if display
                else '<span class="math-inline" role="math">%s</span>' % safe
            )
            return token
        return inner

    segment = _BLOCK_DOLLAR_RE.sub(replace(True), segment)
    segment = _INLINE_DOLLAR_RE.sub(replace(False), segment)
    legacy = _legacy_formula(segment)
    if legacy:
        line, snippet = legacy
        raise GuideError(
            "检测到 raw/伪 LaTeX（片段第 %d 行：%s）。不要猜改原文；请在 Markdown 事实源中改用 "
            "$...$ 或 $$...$$ 标准分隔符。\\(...\\) / \\[...\\] 也不属于本框架的事实源标准，"
            "请显式迁移后再渲染。" % (line, snippet)
        )
    return segment


def prepare_math(markdown, math_converter=None):
    """Convert math outside fenced code and return (tokenized_markdown, safe_html_tokens)."""
    if not isinstance(markdown, str):
        markdown = _display_value(markdown)
    prefixes = ("STUDYGUIDEMATHTOKEN", "STUDYGUIDEOPAQUETOKEN")
    for prefix in prefixes:
        if prefix in markdown:
            raise GuideError("源文本包含渲染器保留 token：%s" % prefix)
    conv = _MathConverter(math_converter)
    tokens = {}
    out, normal = [], []
    fence = None

    def flush():
        if normal:
            segment = "".join(normal)
            # Inline code is literal documentation, just like fenced code.  Protect it before the
            # raw-TeX lint/math pass so `\\frac{1}{2}` and `$x$` remain code instead of being rejected
            # or converted, then restore the original Markdown for the inline renderer.
            opaque = {}
            def protect_code(match):
                token = "STUDYGUIDEOPAQUETOKEN%06dZZ" % len(opaque)
                opaque[token] = match.group(0)
                return token
            segment = _INLINE_CODE_RE.sub(protect_code, segment)
            segment = _convert_math_segment(segment, conv, tokens)
            for token, original in opaque.items():
                segment = segment.replace(token, original)
            out.append(segment)
            normal[:] = []

    for line in markdown.splitlines(True):
        marker = _FENCE_RE.match(line)
        if fence is None and marker:
            flush()
            fence = (marker.group(1)[0], len(marker.group(1)))
            out.append(line)
        elif fence is not None:
            out.append(line)
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]:
                fence = None
        else:
            normal.append(line)
    flush()
    return "".join(out), tokens


def _display_value(value, language="中文"):
    if value is None:
        return ""
    if isinstance(value, bool):
        return _ui(language, "true" if value else "false")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


class MarkdownRenderer:
    def __init__(self, workspace, math_converter=None, allow_wiki_parent_assets=False,
                 language="中文", student_attempt_tainted_keys=_ASSET_POLICY_UNSET):
        self.workspace = workspace
        self.math_converter = math_converter
        self.allow_wiki_parent_assets = allow_wiki_parent_assets
        self.language = language
        self.student_attempt_tainted_keys = _coerce_tainted_asset_keys(
            workspace, student_attempt_tainted_keys, "Markdown renderer")

    def _protect(self, value, protected):
        token = "STUDYGUIDEPROTECTED%06dZZ" % len(protected)
        protected[token] = value
        return token

    def inline(self, text, math_tokens):
        text = _display_value(text, self.language)
        if "STUDYGUIDEPROTECTED" in text:
            raise GuideError("源文本包含渲染器保留 token：STUDYGUIDEPROTECTED")
        protected = {}
        text = _INLINE_CODE_RE.sub(
            lambda m: self._protect("<code>%s</code>" % html.escape(m.group(1)), protected),
            text,
        )

        def image(match):
            alt, rel = match.group(1), match.group(2)
            src = _resolve_asset(
                self.workspace, rel, "Markdown 图片",
                allow_wiki_parent_asset=self.allow_wiki_parent_assets,
                student_attempt_tainted_keys=self.student_attempt_tainted_keys,
                taint_message=(
                    "Markdown 图片绑定到 student_attempt 污染资产，不能作为概念/讲义证据：%s"
                ),
            )
            tag = '<figure><img src="%s" alt="%s"><figcaption>%s</figcaption></figure>' % (
                src,
                html.escape(alt or _ui(self.language, "material_image"), quote=True),
                html.escape(alt) if alt else _ui_html(self.language, "material_image"),
            )
            return self._protect(tag, protected)

        text = _MD_IMAGE_RE.sub(image, text)

        def link(match):
            # The guide is self-contained: flatten links instead of emitting navigable or remote href.
            label, target = match.group(1), match.group(2)
            flat = '<span class="citation">%s <code>%s</code></span>' % (
                self.inline(label, math_tokens), html.escape(target)
            )
            return self._protect(flat, protected)

        text = _MD_LINK_RE.sub(link, text)
        # Protect MathML only after code/images/links have become opaque.  This avoids nested reserved
        # tokens in link labels and makes restoration a single, deterministic pass.
        for token, rendered in math_tokens.items():
            if token in text:
                text = text.replace(token, self._protect(rendered, protected))
        value = html.escape(text, quote=False)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)
        for token, rendered in protected.items():
            value = value.replace(token, rendered)
        return value

    def render(self, markdown):
        prepared, math_tokens = prepare_math(markdown or "", self.math_converter)
        out = []
        in_ul = in_ol = in_table = False
        fence = None
        code_lines = []

        def close_blocks():
            nonlocal in_ul, in_ol, in_table
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if in_table:
                out.append("</tbody></table>")
                in_table = False

        for line in prepared.splitlines():
            marker = _FENCE_RE.match(line)
            if fence is not None:
                if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]:
                    out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code_lines)))
                    code_lines = []
                    fence = None
                else:
                    code_lines.append(line)
                continue
            if marker:
                close_blocks()
                fence = (marker.group(1)[0], len(marker.group(1)))
                continue
            stripped = line.strip()
            if not stripped:
                close_blocks()
                continue
            comment = _SOURCE_COMMENT_RE.match(line)
            if comment:
                close_blocks()
                out.append('<p class="source-anchor">%s</p>' %
                           _ui_fact_html(self.language, "source_anchor", comment.group(1)))
                continue
            heading = re.match(r"^\s*(#{1,6})\s+(.*)$", line)
            if heading:
                close_blocks()
                level = min(6, len(heading.group(1)) + 1)  # page h1 is reserved for guide title
                out.append("<h%d>%s</h%d>" %
                           (level, self.inline(heading.group(2), math_tokens), level))
                continue
            if re.match(r"^\s*(?:---+|\*\*\*+)\s*$", line):
                close_blocks()
                out.append("<hr>")
                continue
            if re.match(r"^\s*\|[\s:\-|]+\|?\s*$", line):
                continue
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if not in_table:
                    close_blocks()
                    out.append("<table><tbody>")
                    in_table = True
                    out.append("<tr>" + "".join("<th>%s</th>" % self.inline(c, math_tokens)
                                                 for c in cells) + "</tr>")
                else:
                    out.append("<tr>" + "".join("<td>%s</td>" % self.inline(c, math_tokens)
                                                 for c in cells) + "</tr>")
                continue
            bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
            if bullet:
                if in_ol or in_table:
                    close_blocks()
                if not in_ul:
                    out.append("<ul>")
                    in_ul = True
                out.append("<li>%s</li>" % self.inline(bullet.group(1), math_tokens))
                continue
            ordered = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
            if ordered:
                if in_ul or in_table:
                    close_blocks()
                if not in_ol:
                    out.append("<ol>")
                    in_ol = True
                out.append("<li>%s</li>" % self.inline(ordered.group(1), math_tokens))
                continue
            quote = re.match(r"^\s*>\s?(.*)$", line)
            if quote:
                close_blocks()
                out.append("<blockquote>%s</blockquote>" % self.inline(quote.group(1), math_tokens))
                continue
            close_blocks()
            out.append("<p>%s</p>" % self.inline(line, math_tokens))
        if fence is not None:
            raise GuideError("Markdown 代码围栏未闭合")
        close_blocks()
        return "\n".join(out)


def _source_kind(item, answer_missing=False):
    source = str(item.get("source") or "unknown").strip().lower()
    if answer_missing:
        status = str(item.get("answer_status") or "").strip().lower()
        if item.get("ai_generated") is True or source == "ai_generated" or status == "ai_generated":
            return "ai_generated"
        # A material-sourced question is not evidence of a material-sourced answer.  Missing
        # answers therefore fail closed to unknown rather than inheriting a green question label.
        return "unknown"
    if item.get("ai_generated") is True and source not in ("ai_generated", "mixed"):
        return "ai_generated"
    return source


def _source_label_pair(item, answer_missing=False):
    source = _source_kind(item, answer_missing=answer_missing)
    zh = SOURCE_LABELS_ZH.get(source, "来源未知（%s）" % source)
    en = SOURCE_LABELS_EN.get(source, "Source unknown (%s)" % source)
    return zh, en


def _source_label(item, language, answer_missing=False):
    zh, en = _source_label_pair(item, answer_missing=answer_missing)
    if language == "中文":
        return zh
    if language == "English":
        return en
    return "%s / %s" % (zh, en)


def _validate_pages(value, label):
    if value is None:
        return []
    if not isinstance(value, list) or any(type(p) is not int or p < 1 for p in value):
        raise GuideError("%s 必须是正整数页码数组" % label)
    return value


def _provenance_html(item, language, answer_missing=False):
    rows = []
    saw_answer_source = False
    for file_key, pages_key, title_key in (
        ("source_file", "source_pages", "prompt_source"),
        ("answer_source_file", "answer_source_pages", "answer_source"),
    ):
        source_file = item.get(file_key)
        pages = _validate_pages(item.get(pages_key), pages_key)
        if source_file is not None:
            _validate_provenance_path(source_file, file_key)
        if source_file or pages:
            if file_key == "answer_source_file":
                saw_answer_source = True
            page_suffix = " · p." + ", ".join(str(p) for p in pages) if pages else ""
            if source_file:
                rows.append(_ui_fact_html(language, title_key, str(source_file) + page_suffix))
                continue
            unknown_zh, unknown_en = _ui_pair("source_unknown_file")
            title_zh, title_en = _ui_pair(title_key)
            if pages:
                unknown_zh += page_suffix
                unknown_en += page_suffix
            rows.append(_language_blocks_html(
                language,
                "%s：%s" % (title_zh, unknown_zh),
                "%s: %s" % (title_en, unknown_en),
            ))
    if answer_missing and not saw_answer_source:
        unknown_zh, unknown_en = _ui_pair("source_unknown_file")
        answer_zh, answer_en = _ui_pair("answer_source")
        rows.append(_language_blocks_html(
            language,
            "%s：%s" % (answer_zh, unknown_zh),
            "%s: %s" % (answer_en, unknown_en),
        ))
    source_zh, source_en = _source_label_pair(item, answer_missing=answer_missing)
    label_zh, label_en = _ui_pair("source_label")
    rows.append(_language_blocks_html(
        language,
        "%s：%s" % (label_zh, source_zh),
        "%s: %s" % (label_en, source_en),
    ))
    separator = "" if language == "双语" else (" ｜ " if language == "中文" else " | ")
    return '<p class="provenance%s">%s</p>' % (
        " answer-missing-provenance" if answer_missing else "",
        separator.join(rows),
    )


def _asset_groups(workspace, item, language, tainted_keys=None):
    assets = item.get("assets") or []
    if not isinstance(assets, list):
        raise GuideError("item.assets 必须是数组")
    prompt, answer = [], []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise GuideError("assets[%d] 必须是对象" % index)
        role = asset.get("role")
        if role not in ALL_ROLES:
            raise GuideError("assets[%d].role 非法或缺失：%r" % (index, role))
        rel = asset.get("path")
        if role == "student_attempt":
            # Retain in the source JSON for audit, but never print a student's submission as a
            # prompt, official solution, or concept illustration.
            continue
        src = _resolve_asset(
            workspace, rel, "assets[%d].path" % index,
            student_attempt_tainted_keys=tainted_keys,
            taint_message=(
                "assets[%d] 把 student_attempt 污染资产作为正式渲染证据：%%s" % index
            ),
        )
        caption = _display_value(asset.get("caption") or role, language)
        label_key = "prompt_asset" if role in QUESTION_ROLES else "answer_asset"
        label = _ui_fact_html(language, label_key, role, separator=" · ")
        alt_label = _ui(language, label_key)
        alt_separator = ": " if language == "English" else "："
        block = (
            '<figure class="asset asset-%s"><div class="asset-label">%s</div>'
            '<img src="%s" alt="%s"><figcaption>%s</figcaption></figure>'
            % ("prompt" if role in QUESTION_ROLES else "answer", label,
               src, html.escape(alt_label + alt_separator + caption, quote=True), html.escape(caption))
        )
        (prompt if role in QUESTION_ROLES else answer).append(block)
    return prompt, answer


def _enforce_prompt_assets(item, prompt_assets):
    for key in ("requires_assets", "maybe_requires_assets"):
        if key in item and type(item.get(key)) is not bool:
            raise GuideError("%s 必须是真正的 JSON 布尔值" % key)
    status = item.get("question_text_status")
    if status is not None and status not in {"full", "stub", "page_reference"}:
        raise GuideError("question_text_status 非法：%r" % status)
    dependent = item.get("requires_assets") is True or item.get("maybe_requires_assets") is True
    incomplete = status in {"stub", "page_reference"}
    if (dependent or incomplete) and not prompt_assets:
        raise GuideError(
            "视觉依赖或题面不完整的 item 缺少可展示题面图；拒绝生成看不全题目的教材"
        )


def _render_text(renderer, value, empty_key):
    if isinstance(value, bool):
        return '<p>%s</p>' % _ui_html(renderer.language, "true" if value else "false")
    text = _display_value(value, renderer.language).strip()
    return (renderer.render(text) if text else
            '<p class="empty">%s</p>' % _ui_html(renderer.language, empty_key))


def _render_teaching(renderer, workspace, items, language):
    if not items:
        return '<p class="empty">%s</p>' % _ui_html(language, "teaching_empty")
    blocks = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise GuideError("teaching_examples 当前章第 %d 项必须是对象" % index)
        prompt_assets, answer_assets = _asset_groups(
            workspace, item, language, renderer.student_attempt_tainted_keys)
        _enforce_prompt_assets(item, prompt_assets)
        title = _display_value(item.get("title") or item.get("id") or "example-%d" % index,
                               language)
        question = item.get("question")
        answer = item.get("answer")
        explanation = item.get("explanation")
        if item.get("teaching_role") == "worked_example" and answer is None:
            body = [
                '<article class="card teaching-card worked-example-card">',
                '<h3>%s</h3>' % _ui_html(language, "example", index=index, title=title),
                _provenance_html(item, language),
                '<div class="answer-zone worked-demonstration"><h4>%s</h4>' %
                _ui_html(language, "worked_demonstration"),
                "".join(prompt_assets),
                _render_text(renderer, question, "teaching_answer_missing"),
            ]
            if explanation is not None and _display_value(explanation, language).strip():
                body += ['<h5>%s</h5>' % _ui_html(language, "plain_explanation"),
                         renderer.render(_display_value(explanation, language))]
            body += ["".join(answer_assets), "</div></article>"]
            blocks.append("\n".join(body))
            continue
        body = [
            '<article class="card teaching-card">',
            '<h3>%s</h3>' % _ui_html(language, "example", index=index, title=title),
            _provenance_html(item, language),
            '<div class="prompt-zone"><h4>%s</h4>' % _ui_html(language, "prompt"),
            "".join(prompt_assets),
            _render_text(renderer, question, "prompt_missing"),
            '</div><div class="answer-zone"><h4>%s</h4>' %
            _ui_html(language, "walkthrough_answer"),
            _render_text(renderer, answer, "teaching_answer_missing"),
        ]
        if explanation is not None and _display_value(explanation, language).strip():
            body += ['<h5>%s</h5>' % _ui_html(language, "plain_explanation"),
                     renderer.render(_display_value(explanation, language))]
        body += ["".join(answer_assets), "</div></article>"]
        blocks.append("\n".join(body))
    return "\n".join(blocks)


def _render_options(renderer, options):
    if options is None:
        return ""
    if not isinstance(options, list):
        raise GuideError("choice.options 必须是数组")
    rendered = []
    for option in options:
        if isinstance(option, bool):
            value = '<p>%s</p>' % _ui_html(
                renderer.language, "true" if option else "false"
            )
        else:
            value = renderer.render(_display_value(option, renderer.language))
        rendered.append("<li>%s</li>" % value)
    return '<ol class="options">%s</ol>' % "".join(rendered)


def _has_displayable_answer(value):
    """Match workspace validation: empty JSON containers and blank strings are no answer."""
    if value in (None, "", [], {}):
        return False
    return not (isinstance(value, str) and not value.strip())


def _render_quizzes(renderer, workspace, items, language):
    if not items:
        return '<p class="empty">%s</p>' % _ui_html(language, "quiz_empty")
    blocks = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise GuideError("quiz_bank 当前章第 %d 项必须是对象" % index)
        prompt_assets, answer_assets = _asset_groups(
            workspace, item, language, renderer.student_attempt_tainted_keys)
        _enforce_prompt_assets(item, prompt_assets)
        qid = _display_value(item.get("id") or "quiz-%d" % index, language)
        answer = item.get("answer")
        answer_text_missing = not _has_displayable_answer(answer)
        answer_asset_only = answer_text_missing and bool(answer_assets)
        answer_missing = answer_text_missing and not answer_assets
        if answer_missing:
            answer_html = '<p class="empty answer-abstention">%s</p>' % _ui_html(
                language, "unknown_answer"
            )
        elif answer_asset_only:
            answer_html = '<p class="notice answer-asset-only">%s</p>' % _ui_html(
                language, "answer_asset_only"
            )
        else:
            answer_html = _render_text(renderer, answer, "quiz_answer_missing")
        explanation = item.get("explanation")
        body = [
            '<article class="card quiz-card">',
            '<h3>%s</h3>' % _ui_html(language, "quiz", index=index, id=qid),
            _provenance_html(item, language, answer_missing=answer_missing),
            '<div class="prompt-zone"><h4>%s</h4>' % _ui_html(language, "prompt"),
            "".join(prompt_assets),
            _render_text(renderer, item.get("question"), "quiz_prompt_missing"),
            _render_options(renderer, item.get("options")),
            '</div>',
            '<details class="quiz-answer"><summary>%s</summary><div class="answer-zone">' %
            _ui_html(language, "details"),
            '<h4>%s</h4>' % _ui_html(language, "answer"), answer_html,
        ]
        if (not answer_text_missing and explanation is not None
                and _display_value(explanation, language).strip()):
            body += ['<h5>%s</h5>' % _ui_html(language, "analysis"),
                     renderer.render(_display_value(explanation, language))]
        if not answer_missing:
            body.append("".join(answer_assets))
        body.append("</div></details></article>")
        blocks.append("\n".join(body))
    return "\n".join(blocks)


def render_study_guide(sources, math_converter=None):
    """Render the legacy four-layer source packet.

    Kept as a Python compatibility API.  The CLI never labels this output a Study Guide; callers
    must explicitly select ``--artifact-type source_packet``.
    """
    ws, chapter = sources["workspace"], sources["chapter"]
    language = sources.get("language", "中文")
    if language not in CANON_LANGUAGES:
        raise GuideError("renderer language 必须是 canonical 中文、English 或 双语")
    # Re-read instead of trusting a compatibility caller to provide the policy key.  The public
    # render API must fail closed even when a new host constructs ``sources`` by hand.
    tainted_keys = _workspace_tainted_asset_keys(ws, "source-packet renderer")
    renderer = MarkdownRenderer(
        ws, math_converter, language=language,
        student_attempt_tainted_keys=tainted_keys,
    )
    wiki_renderer = MarkdownRenderer(
        ws, math_converter, allow_wiki_parent_assets=True, language=language,
        student_attempt_tainted_keys=tainted_keys,
    )
    wiki = wiki_renderer.render(sources["wiki"])
    teaching = _render_teaching(renderer, ws, sources["teaching"], language)
    quizzes = _render_quizzes(renderer, ws, sources["quizzes"], language)
    notebook = (
        renderer.render(sources["notebook"])
        if sources["notebook"].strip()
        else '<p class="empty">%s</p>' % _ui_html(language, "notebook_empty")
    )
    manifest_note = (
        '<p class="notice">%s</p>' % _ui_html(language, "manifest_missing")
        if sources["teaching_manifest_missing"] else ""
    )
    title = _ui(language, "title", chapter=chapter)
    title_html = _ui_html(language, "title", chapter=chapter)
    notebook_key = "notebook_yes" if sources["notebook"].strip() else "notebook_no"
    notebook_zh, notebook_en = _ui_pair(notebook_key)
    summary_values = {
        "wiki": sources["wiki_rel"], "teaching": len(sources["teaching"]),
        "quiz": len(sources["quizzes"]),
    }
    source_summary = _language_blocks_html(
        language,
        UI_ZH["guide_source"].format(notebook=notebook_zh, **summary_values),
        UI_EN["guide_source"].format(notebook=notebook_en, **summary_values),
    )
    document = """<!doctype html>
<html lang="%s"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>%s</title>
<style>
:root { color-scheme: light; --ink:#172033; --muted:#59677f; --line:#dbe3ef;
        --paper:#fff; --accent:#2457c5; --prompt:#eef6ff; --answer:#f6f2ff; }
* { box-sizing:border-box; } html { background:#edf1f7; }
body { margin:0 auto; max-width:1040px; padding:34px 42px 80px; background:var(--paper);
       color:var(--ink); font:17px/1.72 "Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif; }
h1 { font-size:2.15rem; line-height:1.2; margin:.2em 0 .35em; }
h2 { margin:2.2em 0 .8em; padding-bottom:.28em; border-bottom:3px solid var(--accent); font-size:1.55rem; }
h3 { margin:1.35em 0 .6em; font-size:1.24rem; } h4 { margin:.45em 0 .5em; } h5 { margin:.8em 0 .35em; }
p { margin:.55em 0; } ul,ol { padding-left:1.7em; } li { margin:.2em 0; }
.subtitle,.provenance,.source-anchor { color:var(--muted); font-size:.88rem; }
.source-anchor { border-left:3px solid #aec0db; padding:.1em .7em; }
.card { border:1px solid var(--line); border-radius:14px; margin:1.2em 0; overflow:hidden;
        box-shadow:0 3px 13px rgba(30,55,90,.06); }
.card > h3,.card > .provenance { margin-left:1.2rem; margin-right:1.2rem; }
.prompt-zone,.answer-zone { padding:1rem 1.2rem; } .prompt-zone { background:var(--prompt); }
.answer-zone { background:var(--answer); border-top:1px solid var(--line); }
details > summary { cursor:pointer; padding:.85rem 1.2rem; font-weight:700; color:var(--accent); }
.empty,.notice { padding:.75rem 1rem; background:#fff8df; border-left:4px solid #e2ad24; }
figure { margin:1rem auto; text-align:center; } figure img { display:block; max-width:100%%; max-height:72vh;
         margin:auto; border:1px solid var(--line); border-radius:8px; }
figcaption,.asset-label { color:var(--muted); font-size:.86rem; } .asset-label { font-weight:700; margin:.3em; }
.math-inline math { font-size:1.08em; } .math-display { display:block; text-align:center; overflow-x:auto;
        padding:.65em; margin:.75em 0; background:#f8fafc; border-radius:8px; }
pre { overflow:auto; background:#172033; color:#f5f7fb; padding:1em; border-radius:8px; }
code { font-family:Consolas,"SFMono-Regular",monospace; background:#eef1f5; padding:.08em .28em; border-radius:4px; }
pre code { background:none; padding:0; } table { width:100%%; border-collapse:collapse; margin:1em 0; }
th,td { border:1px solid #bfcbdc; padding:.55em .7em; vertical-align:top; } th { background:#edf3fb; }
blockquote { margin:.8em 0; padding:.35em 1em; border-left:4px solid #96add0; color:#40516c; }
  .citation { color:var(--muted); } .guide-source { color:var(--muted); font-size:.9rem; }
  .lang-block { display:block; } .lang-en { margin-top:.12em; }
@page { size:A4; margin:15mm; }
@media print {
  html,body { background:#fff; } body { max-width:none; padding:0; font-size:10.5pt; }
  main > section + section {
    break-before:page;
    page-break-before:always;
  }
  h2,h3,h4 { break-after:avoid; }
  .card { box-shadow:none; overflow:visible; }
  figure,table,pre,.prompt-zone,.answer-zone {
    break-inside:avoid-page;
    page-break-inside:avoid;
  }
  figure img { max-height:85mm; width:auto; }
  details > :not(summary) { display:block !important; } details > summary { display:none !important; }
  .answer-zone { display:block !important; }
}
</style></head><body>
<header><p class="subtitle">%s</p>
<h1>%s</h1><p class="guide-source">%s</p></header>
<main>
<section id="concepts"><h2>%s</h2>%s</section>
<section id="examples"><h2>%s</h2>%s%s</section>
<section id="quiz"><h2>%s</h2>%s</section>
<section id="notebook"><h2>%s</h2>%s</section>
</main></body></html>""" % (
        html.escape(HTML_LANG[language], quote=True), html.escape(title),
        _ui_html(language, "subtitle"), title_html, source_summary,
        _ui_html(language, "concepts_heading"), wiki,
        _ui_html(language, "examples_heading"), manifest_note, teaching,
        _ui_html(language, "quiz_heading"), quizzes,
        _ui_html(language, "notebook_heading"), notebook,
    )
    validate_generated_html(document)
    return document


render_source_packet = render_study_guide


class _SelfContainedHTMLCheck(HTMLParser):
    def __init__(self, materials_root=None):
        super().__init__(convert_charrefs=True)
        self.errors = []
        self.materials_root = (
            os.path.realpath(os.path.abspath(materials_root))
            if isinstance(materials_root, str) and materials_root else None
        )
        self.element_ids = set()
        self.fragment_links = []

    def _file_href_allowed(self, value):
        if self.materials_root is None:
            return False
        if (not os.path.isdir(self.materials_root) or os.path.islink(self.materials_root)):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if parsed.scheme.lower() != "file" or parsed.netloc not in ("", "localhost"):
            return False
        if parsed.query or not re.fullmatch(r"page=[1-9]\d*", parsed.fragment or ""):
            return False
        decoded = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        target = os.path.abspath(decoded.replace("/", os.sep))
        target_real = os.path.realpath(target)
        try:
            contained = os.path.commonpath((self.materials_root, target_real)) == self.materials_root
        except ValueError:
            contained = False
        if not contained or os.path.islink(target) or not os.path.isfile(target):
            return False
        canonical = Path(target_real).resolve().as_uri() + "#" + parsed.fragment
        return value == canonical

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id")
        if isinstance(element_id, str) and element_id:
            self.element_ids.add(element_id)
        if tag.lower() in {"script", "iframe", "object", "embed", "link"}:
            self.errors.append("禁止标签 <%s>" % tag)
        for key, value in attrs.items():
            low = key.lower()
            if low.startswith("on"):
                self.errors.append("事件属性 %s" % key)
            if low == "src" and not (value or "").startswith("data:image/"):
                self.errors.append("非内嵌 src")
            if low == "href":
                if (isinstance(value, str)
                        and re.fullmatch(r"#(?:example|kp)-[A-Za-z0-9_.:-]+", value)):
                    self.fragment_links.append(value[1:])
                elif not isinstance(value, str) or not self._file_href_allowed(value):
                    self.errors.append("不安全或未绑定的 href")


def validate_generated_html(document, workspace=None, materials_root=None):
    del workspace  # reserved for callers that keep both roots explicit
    if not document.lstrip().lower().startswith("<!doctype html>"):
        raise GuideError("生成 HTML 缺少 doctype", 1)
    check = _SelfContainedHTMLCheck(materials_root=materials_root)
    try:
        check.feed(document)
        check.close()
    except Exception as exc:
        raise GuideError("生成 HTML 无法解析（%s）" % exc, 1)
    if check.errors:
        raise GuideError("生成 HTML 未通过自包含安全检查：%s" % ", ".join(check.errors), 1)
    missing_fragments = sorted(set(check.fragment_links) - check.element_ids)
    if missing_fragments:
        raise GuideError("生成 HTML 含不存在的同页锚点：%s" % ", ".join(missing_fragments), 1)
    for prefix in ("STUDYGUIDEPROTECTED", "STUDYGUIDEMATHTOKEN", "STUDYGUIDEOPAQUETOKEN"):
        if prefix in document:
            raise GuideError("生成 HTML 残留渲染器保留 token：%s" % prefix, 1)


def _prepare_output_dir(ws):
    directory = os.path.join(ws, "study_guide")
    if os.path.lexists(directory):
        if os.path.islink(directory):
            raise GuideError("study_guide 输出目录不得是符号链接")
        if not os.path.isdir(directory):
            raise GuideError("study_guide 已存在但不是目录")
    else:
        os.mkdir(directory)
    if not _contained(ws, directory):
        raise GuideError("study_guide 输出目录逃出 workspace")
    return directory


def _guard_output(path, label):
    if os.path.lexists(path) and os.path.islink(path):
        raise GuideError("%s 不得是符号链接" % label)
    if os.path.lexists(path) and not os.path.isfile(path):
        raise GuideError("%s 已存在但不是普通文件" % label)


def _remove_stale(path, label):
    _guard_output(path, label)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            raise GuideError("无法移除过期 %s（%s）" % (label, exc), 1)


def _atomic_write(path, content, before_publish=None):
    """Atomically publish exact UTF-8 bytes, with a last-moment input recheck hook."""
    _guard_output(path, os.path.basename(path))
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".study-guide-", suffix=".tmp", dir=directory)
    try:
        payload = content.encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if before_publish is not None:
            before_publish()
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _atomic_json(path, value, before_publish=None):
    _guard_output(path, os.path.basename(path))
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".study-guide-receipt-", suffix=".tmp", dir=directory)
    try:
        payload = (json.dumps(
            value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if before_publish is not None:
            before_publish()
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _is_reparse_stat(value):
    attrs = getattr(value, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _stat_identity(value):
    """Return the filesystem identity used to reject same-bytes path replacement.

    ``st_ino`` is available for regular files on supported CPython/Windows and POSIX builds.  A
    zero inode cannot prove that a path still names the same file, so artifact publication fails
    closed instead of degrading to a hash-only check.
    """
    device = int(getattr(value, "st_dev", 0))
    inode = int(getattr(value, "st_ino", 0))
    if inode == 0:
        raise GuideError("filesystem does not expose a stable regular-file identity", 1)
    return device, inode


def _stat_generation(value):
    return (
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000))),
    )


def _capture_regular_file_snapshot(ws, path, label):
    """Read one stable regular-file generation and retain its exact bytes/hash/identity."""
    path = os.path.abspath(path)
    _guard_existing_file(ws, path, label)
    try:
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or _is_reparse_stat(before)
                or os.path.islink(path)):
            raise GuideError("%s is not a non-reparse regular file" % label)
        before_identity = _stat_identity(before)
        with open(path, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or _is_reparse_stat(opened)
                    or _stat_identity(opened) != before_identity):
                raise ArtifactDriftError("%s changed between path check and open" % label)
            opened_generation = _stat_generation(opened)
            payload = stream.read()
            stream.seek(0)
            confirmation = stream.read()
            after_read = os.fstat(stream.fileno())
        after = os.lstat(path)
    except ArtifactDriftError:
        raise
    except GuideError:
        raise
    except OSError as exc:
        raise GuideError("cannot read stable %s snapshot: %s" % (label, exc), 1)
    if (not stat.S_ISREG(after_read.st_mode) or not stat.S_ISREG(after.st_mode)
            or _is_reparse_stat(after_read) or _is_reparse_stat(after)
            or os.path.islink(path)):
        raise ArtifactDriftError("%s stopped being a regular file while it was read" % label)
    if (_stat_identity(after_read) != before_identity
            or _stat_identity(after) != before_identity):
        raise ArtifactDriftError("%s path identity changed while it was read" % label)
    if (_stat_generation(after_read) != opened_generation
            or _stat_generation(after) != opened_generation
            or len(payload) != opened_generation[0]
            or confirmation != payload):
        raise ArtifactDriftError("%s contents changed while its snapshot was read" % label)
    return {
        "path": path,
        "identity": before_identity,
        "sha256": _sha256_bytes(payload),
        "bytes": payload,
    }


def _verify_file_snapshot(ws, snapshot, label):
    """Reopen ``snapshot.path`` and prove both filesystem identity and bytes are unchanged."""
    try:
        current = _capture_regular_file_snapshot(ws, snapshot["path"], label)
    except ArtifactDriftError:
        raise
    except (GuideError, OSError, KeyError, TypeError) as exc:
        raise ArtifactDriftError("%s cannot be revalidated (%s)" % (label, exc))
    if current["identity"] != snapshot["identity"]:
        raise ArtifactDriftError("%s path was replaced, even though bytes may match" % label)
    if current["sha256"] != snapshot["sha256"]:
        raise ArtifactDriftError("%s SHA-256 changed during rendering" % label)
    return current


def _reject_duplicate_json_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = child
    return value


def _load_typed_manifest_snapshot(ws, chapter):
    """Parse and validate the exact bytes captured at the start of this render run."""
    import study_guide_content as content

    path = content.manifest_path(ws, chapter)
    snapshot = _capture_regular_file_snapshot(
        ws, path, "study-guide content manifest")
    if len(snapshot["bytes"]) > content.MAX_JSON_BYTES:
        raise GuideError("study-guide content manifest exceeds %d bytes" %
                         content.MAX_JSON_BYTES)
    try:
        manifest = json.loads(
            snapshot["bytes"].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise GuideError("study-guide content manifest is not strict UTF-8 JSON: %s" % exc)
    try:
        report = content.validate_manifest(ws, chapter, manifest)
    except (ValueError, OSError) as exc:
        raise GuideError("study-guide content manifest is invalid: %s" % exc)
    snapshot["fact_integrity"] = (
        (report.get("claim_verification") or {}).get("fact_integrity")
    )
    report["input_path"] = path
    return manifest, report, snapshot


def _canonical_hash(value):
    return _sha256_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8"))


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _preflight_or_raise(ws, chapter, backend, math_converter):
    if math_converter is not None:
        return {"status": "injected-test-converter", "pdf_backend": backend,
                "missing_needed": [], "probe_error": None}
    report = build_report(
        artifact_mode="visual", workspace=ws, chapter=chapter, pdf_backend=backend)
    if report.get("probe_error"):
        raise GuideError("visual dependency preflight failed: %s" % report["probe_error"], 3)
    if report.get("missing_needed"):
        raise GuideError("visual dependency preflight is missing: %s" %
                         ", ".join(report["missing_needed"]), 3)
    report["status"] = "passed"
    return report


def _start_gate_or_raise(ws):
    import exam_start

    gate = exam_start.check_registered_workspace_gate(ws)
    if not gate.get("ready_to_use"):
        blockers = []
        for attempt in gate.get("attempts") or []:
            blockers.extend(attempt.get("blockers") or [])
        detail = ", ".join(sorted(set(blockers))) or gate.get("reason") or "unknown"
        raise GuideError(
            "Study Guide rendering requires the confirmed workspace/materials pair, all "
            "learning choices, and a matching runtime receipt; rerun exam_start confirm "
            "(%s)" % detail,
            2,
        )
    return gate


def _start_gate_snapshot(gate):
    runtime = ((gate.get("runtime_provenance") or {}).get("receipt") or {})
    return {
        "ready_to_use": bool(gate.get("ready_to_use")),
        "workspace": gate.get("workspace"),
        "materials": gate.get("materials"),
        "registered_course": gate.get("registered_course"),
        "runtime_digest": runtime.get("runtime_digest"),
        "runtime_file_count": runtime.get("runtime_file_count"),
        "skill_version": runtime.get("skill_version"),
        "git_commit": runtime.get("git_commit"),
        "git_branch": runtime.get("git_branch"),
        "git_dirty": runtime.get("git_dirty"),
        "python_executable": runtime.get("python_executable"),
    }


def _verify_start_gate_snapshot(ws, expected):
    try:
        current_gate = _start_gate_or_raise(ws)
    except GuideError as exc:
        raise ArtifactDriftError("confirmed workspace/runtime gate no longer passes (%s)" % exc)
    current = _start_gate_snapshot(current_gate)
    if current != expected:
        raise ArtifactDriftError("confirmed workspace/runtime identity changed during rendering")
    return current_gate


def _verify_bound_fact_snapshot_files(ws, expected_facts):
    """Cheaply recheck every workspace file bound by a validated fact snapshot.

    The initial typed-manifest validation and the final receipt publication still
    perform the full deterministic derivation, ledger replay, and original-source
    revision check.  Intermediate render checkpoints only need to prove that none
    of the already validated manifest/input/sidecar bytes changed; this avoids
    re-hashing every original PDF at each HTML/PDF conversion checkpoint.
    """
    if not isinstance(expected_facts, dict):
        raise ArtifactDriftError("ingestion fact snapshot is not an object")
    rows = []
    build_manifest = expected_facts.get("build_manifest")
    if not isinstance(build_manifest, dict):
        raise ArtifactDriftError("ingestion fact snapshot lacks build_manifest")
    rows.append(("build manifest", build_manifest))
    for section in ("inputs", "sidecars"):
        bound = expected_facts.get(section)
        if not isinstance(bound, dict):
            raise ArtifactDriftError(
                "ingestion fact snapshot lacks %s bindings" % section
            )
        for label, row in sorted(bound.items()):
            rows.append(("%s %s" % (section, label), row))
    for label, row in rows:
        if not isinstance(row, dict):
            raise ArtifactDriftError("ingestion fact %s binding is invalid" % label)
        relative = row.get("path")
        expected_sha256 = row.get("sha256")
        if (not isinstance(relative, str) or not relative
                or not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
            raise ArtifactDriftError("ingestion fact %s binding is incomplete" % label)
        path = os.path.join(ws, *relative.replace("\\", "/").split("/"))
        try:
            current = _capture_regular_file_snapshot(ws, path, "ingestion fact %s" % label)
        except (GuideError, OSError, TypeError, ValueError) as exc:
            raise ArtifactDriftError(
                "ingestion fact %s cannot be revalidated (%s)" % (label, exc)
            ) from exc
        if current["sha256"] != expected_sha256:
            raise ArtifactDriftError(
                "ingestion fact %s changed during Study Guide rendering" % label
            )


def _verify_publication_inputs(ws, manifest_snapshot, start_gate_snapshot,
                               deep_fact_check=True):
    _verify_file_snapshot(ws, manifest_snapshot, "study-guide content manifest")
    expected_facts = manifest_snapshot.get("fact_integrity")
    if expected_facts is not None:
        _verify_bound_fact_snapshot_files(ws, expected_facts)
        if deep_fact_check:
            try:
                from ingestion.dedup import validate_workspace_fact_integrity

                current_facts = validate_workspace_fact_integrity(ws)["snapshot"]
            except (OSError, TypeError, ValueError) as exc:
                raise ArtifactDriftError(
                    "ingestion fact integrity no longer passes (%s)" % exc
                ) from exc
            if current_facts != expected_facts:
                raise ArtifactDriftError(
                    "ingestion fact inputs changed during Study Guide rendering"
                )
    _verify_start_gate_snapshot(ws, start_gate_snapshot)


def _conversion_run_hash(chapter, profile, language, html_sha256, pdf_sha256,
                         conversion_input_html_sha256, converter,
                         conversion_started_at, conversion_completed_at,
                         start_gate):
    return _canonical_hash({
        "artifact_type": "study_guide",
        "chapter": chapter,
        "profile": profile,
        "language": language,
        "html_sha256": html_sha256,
        "pdf_sha256": pdf_sha256,
        "conversion_input_html_sha256": conversion_input_html_sha256,
        "converter": converter,
        "conversion_started_at": conversion_started_at,
        "conversion_completed_at": conversion_completed_at,
        "start_gate": start_gate,
    })


def _write_guide_receipt(ws, receipt_path, chapter, manifest_snapshot,
                         manifest_report, html_snapshot, pdf_path, backend,
                         preflight, start_gate_snapshot, conversion=None):
    _verify_publication_inputs(
        ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)
    _verify_file_snapshot(ws, html_snapshot, "published Study Guide HTML")
    manifest_path = manifest_snapshot["path"]
    html_path = html_snapshot["path"]
    html_sha256 = html_snapshot["sha256"]
    has_pdf = conversion is not None
    if has_pdf and (backend != "browser" or not os.path.isfile(pdf_path)):
        raise GuideError("browser PDF receipt requires a PDF produced by this render run", 1)
    if has_pdf and not isinstance(conversion, dict):
        raise GuideError("browser converter did not return a conversion receipt", 1)
    pdf_snapshot = (
        _capture_regular_file_snapshot(ws, pdf_path, "published Study Guide PDF")
        if has_pdf else None
    )
    pdf_sha256 = pdf_snapshot["sha256"] if pdf_snapshot else None
    converter = conversion.get("converter") if has_pdf else None
    conversion_started_at = conversion.get("started_at") if has_pdf else None
    conversion_completed_at = conversion.get("completed_at") if has_pdf else None
    conversion_input_html_sha256 = conversion.get("input_html_sha256") if has_pdf else None
    if has_pdf:
        required_strings = {
            "converter": converter,
            "conversion_started_at": conversion_started_at,
            "conversion_completed_at": conversion_completed_at,
        }
        for label, value in required_strings.items():
            if (not isinstance(value, str) or not value.strip()
                    or any(char in value for char in ("\x00", "\r", "\n"))):
                raise GuideError("browser conversion receipt has invalid %s" % label, 1)
        for label, value in (
                ("conversion_input_html_sha256", conversion_input_html_sha256),
                ("source_html_sha256", conversion.get("source_html_sha256"))):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise GuideError("browser conversion receipt has invalid %s" % label, 1)
        if conversion.get("source_html_sha256") != html_sha256:
            raise GuideError("browser conversion receipt does not bind the current HTML", 1)
    conversion_run_sha256 = (
        _conversion_run_hash(
            chapter, manifest_report["profile"], manifest_report["language"],
            html_sha256, pdf_sha256, conversion_input_html_sha256, converter,
            conversion_started_at, conversion_completed_at, start_gate_snapshot,
        ) if has_pdf else None
    )
    receipt = {
        "schema_version": ARTIFACT_RECEIPT_SCHEMA_VERSION,
        "artifact_type": "study_guide",
        "chapter": chapter,
        "profile": manifest_report["profile"],
        "language": manifest_report["language"],
        "content_manifest": os.path.relpath(manifest_path, os.path.dirname(os.path.dirname(html_path))).replace("\\", "/"),
        "content_manifest_sha256": manifest_snapshot["sha256"],
        "expected_item_ids": manifest_report["expected_item_ids"],
        "rendered_item_ids": manifest_report["walkthrough_item_ids"],
        "omitted_item_ids": manifest_report["omitted_item_ids"],
        "html_file": "study_guide/%s" % os.path.basename(html_path),
        "html_sha256": html_sha256,
        "pdf_file": "study_guide/%s" % os.path.basename(pdf_path) if has_pdf else None,
        "pdf_sha256": pdf_sha256,
        "pdf_backend": backend,
        "converter": converter,
        "conversion_input_html_sha256": conversion_input_html_sha256,
        "conversion_started_at": conversion_started_at,
        "conversion_completed_at": conversion_completed_at,
        "conversion_run_sha256": conversion_run_sha256,
        "preflight": preflight,
        "start_gate": start_gate_snapshot,
        "generated_at": _utc_now(),
        "status": "qa_pending" if has_pdf else (
            "awaiting_native_pdf" if backend == "native" else "html_ready"),
        "visual_qa": {"schema_version": 1, "status": "pending"} if has_pdf else {
            "schema_version": 1, "status": "not_requested"},
    }

    def recheck_before_receipt_publish():
        _verify_publication_inputs(ws, manifest_snapshot, start_gate_snapshot)
        _verify_file_snapshot(ws, html_snapshot, "published Study Guide HTML")
        if pdf_snapshot is not None:
            _verify_file_snapshot(ws, pdf_snapshot, "published Study Guide PDF")

    _atomic_json(
        receipt_path, receipt, before_publish=recheck_before_receipt_publish)
    return receipt


def find_browser():
    if os.environ.get("EXAMPREP_NO_BROWSER") == "1":
        return None
    for command in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(command)
        if found:
            return found
    candidates = (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    return next((p for p in candidates if os.path.isfile(p)), None)


def _print_ready_html(document, workspace=None, materials_root=None):
    """Materialize open quiz disclosures for Chromium print.

    Chromium does not honor CSS that tries to expose children of a closed ``details`` element.
    Keep the persisted HTML closed for self-testing, but print a temp-only copy whose generated
    quiz disclosures carry ``open``.  The print stylesheet still removes the summary control.
    """
    ready = document.replace(
        '<details class="quiz-answer">', '<details open class="quiz-answer">'
    )
    validate_generated_html(ready, workspace=workspace, materials_root=materials_root)
    return ready


def print_pdf(browser, html_path, pdf_path, timeout=120, workspace=None,
              materials_root=None, expected_html_sha256=None):
    started_at = _utc_now()
    _remove_stale(pdf_path, os.path.basename(pdf_path))
    directory = os.path.dirname(pdf_path)
    fd, tmp_pdf = tempfile.mkstemp(prefix=".study-guide-", suffix=".pdf", dir=directory)
    os.close(fd)
    os.remove(tmp_pdf)  # Chromium requires a non-existing print target.
    fd, tmp_html = tempfile.mkstemp(prefix=".study-guide-print-", suffix=".html", dir=directory)
    try:
        snapshot_root = os.path.abspath(workspace or os.path.dirname(html_path))
        try:
            html_snapshot = _capture_regular_file_snapshot(
                snapshot_root, html_path, "Study Guide HTML conversion input")
        except ArtifactDriftError:
            raise
        except GuideError as exc:
            if expected_html_sha256 is not None:
                raise ArtifactDriftError(
                    "published HTML conversion input cannot be revalidated (%s)" % exc)
            raise
        if (expected_html_sha256 is not None
                and html_snapshot["sha256"] != expected_html_sha256):
            raise ArtifactDriftError(
                "published HTML bytes differ from the atomic render payload before PDF conversion")
        try:
            source_document = html_snapshot["bytes"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GuideError("published Study Guide HTML is not UTF-8: %s" % exc, 1)
        print_document = _print_ready_html(
            source_document, workspace=workspace, materials_root=materials_root)
        print_payload = print_document.encode("utf-8")
        source_html_sha256 = html_snapshot["sha256"]
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(print_document)
            f.flush()
            os.fsync(f.fileno())
        try:
            with tempfile.TemporaryDirectory(prefix="exam-study-guide-browser-") as profile:
                command = [
                    browser, "--headless=new", "--disable-gpu", "--disable-extensions",
                    "--disable-background-networking",
                    "--user-data-dir=%s" % profile,
                    "--no-pdf-header-footer",
                    "--print-to-pdf=%s" % os.path.abspath(tmp_pdf),
                    Path(tmp_html).resolve().as_uri(),
                ]
                result = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for the browser before raising.  Remove both the
            # final destination and temp print targets so a timed-out run can never masquerade as
            # a successfully refreshed guide.
            _remove_stale(pdf_path, os.path.basename(pdf_path))
            raise GuideError(
                "本地浏览器打印 PDF 超时（%s 秒）；已清理临时文件与目标 PDF。" % timeout,
                1,
            )
        if result.returncode != 0 or not os.path.isfile(tmp_pdf):
            detail = (result.stderr or b"")[:400].decode("utf-8", "replace")
            raise GuideError("本地浏览器打印 PDF 失败：%s" % detail, 1)
        with open(tmp_pdf, "rb") as f:
            head = f.read(5)
        if head != b"%PDF-" or os.path.getsize(tmp_pdf) < 100:
            raise GuideError("浏览器没有生成有效 PDF", 1)
        _verify_file_snapshot(
            snapshot_root, html_snapshot, "Study Guide HTML conversion input")
        os.replace(tmp_pdf, pdf_path)
        return {
            "converter": os.path.abspath(browser),
            "started_at": started_at,
            "completed_at": _utc_now(),
            "input_html_sha256": _sha256_bytes(print_payload),
            "source_html_sha256": source_html_sha256,
        }
    finally:
        if os.path.exists(tmp_pdf):
            os.remove(tmp_pdf)
        if os.path.exists(tmp_html):
            os.remove(tmp_html)


def run(argv=None, math_converter=None, _state_locked=False):
    parser = argparse.ArgumentParser(
        description="Render one chapter as a validated Study Guide or explicit source packet."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--artifact-type", choices=("study_guide", "source_packet"),
                        default="study_guide")
    parser.add_argument("--profile", choices=("full", "abridged"), default=None,
                        help="optional assertion; must match the typed chapter manifest")
    parser.add_argument("--pdf-backend", choices=("html", "browser", "native"), default="html")
    parser.add_argument("--pdf", action="store_true", help="also print chNN.pdf via local Edge/Chrome")
    args = parser.parse_args(argv)
    if args.chapter < 1:
        raise GuideError("--chapter 必须是正整数")
    ws = _guard_workspace(args.workspace)
    if not _state_locked:
        with workspace_publication_lock(ws):
            return run(argv, math_converter=math_converter, _state_locked=True)
    if args.pdf and args.pdf_backend != "browser":
        raise GuideError("--pdf requires --pdf-backend browser; native adapters run outside this script")
    if args.artifact_type == "source_packet" and args.profile is not None:
        raise GuideError("--profile applies only to artifact-type study_guide")
    if args.artifact_type == "study_guide" and args.profile is None:
        raise GuideError("artifact-type study_guide requires explicit --profile full|abridged", 2)
    output_dir = _prepare_output_dir(ws)
    suffix = "" if args.artifact_type == "study_guide" else ".source-packet"
    html_path = os.path.join(output_dir, "ch%02d%s.html" % (args.chapter, suffix))
    pdf_path = os.path.join(output_dir, "ch%02d%s.pdf" % (args.chapter, suffix))
    receipt_path = os.path.join(output_dir, "ch%02d.receipt.json" % args.chapter)
    _guard_output(html_path, os.path.basename(html_path))
    _guard_output(pdf_path, os.path.basename(pdf_path))
    if args.artifact_type == "study_guide":
        _guard_output(receipt_path, os.path.basename(receipt_path))
        # Invalidate all prior publication claims before beginning a new true-guide render.
        # In particular, an HTML-only/native run must never inherit or bind an older PDF.
        _remove_stale(receipt_path, os.path.basename(receipt_path))
        if not args.pdf:
            _remove_stale(pdf_path, os.path.basename(pdf_path))

    typed_guide = args.artifact_type == "study_guide"
    try:
        if args.artifact_type == "source_packet":
            sources = load_chapter_sources(ws, args.chapter)
            document = render_source_packet(sources, math_converter=math_converter)
            manifest = manifest_report = manifest_snapshot = None
            # The compatibility packet deliberately has no artifact-readiness claim.  Its own
            # renderer and browser checks remain fail-closed, while typed-guide readiness is not
            # incorrectly imposed on this explicitly separate output.
            preflight = None
            start_gate = start_gate_snapshot = None
        else:
            from study_guide_document import render_manifest
            start_gate = _start_gate_or_raise(ws)
            start_gate_snapshot = _start_gate_snapshot(start_gate)
            manifest, manifest_report, manifest_snapshot = (
                _load_typed_manifest_snapshot(ws, args.chapter))
            if manifest_report["profile"] != args.profile:
                raise GuideError("typed manifest profile=%s, not requested %s" %
                                 (manifest_report["profile"], args.profile))
            preflight = _preflight_or_raise(ws, args.chapter, args.pdf_backend, math_converter)
            _verify_publication_inputs(
                ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)
            document, rendered_report = render_manifest(
                ws, manifest, math_converter=math_converter,
                materials_root=start_gate.get("materials"))
            manifest_report.update(rendered_report)
            _verify_publication_inputs(
                ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)
    except GuideError:
        # Never let an older guide masquerade as the result of a failed source/math render.
        _remove_stale(html_path, os.path.basename(html_path))
        if args.pdf:
            _remove_stale(pdf_path, os.path.basename(pdf_path))
        if args.artifact_type == "study_guide":
            _remove_stale(receipt_path, os.path.basename(receipt_path))
        raise
    except Exception as exc:
        _remove_stale(html_path, os.path.basename(html_path))
        if args.pdf:
            _remove_stale(pdf_path, os.path.basename(pdf_path))
        if args.artifact_type == "study_guide":
            _remove_stale(receipt_path, os.path.basename(receipt_path))
        raise GuideError("章节渲染发生未预期错误：%s" % exc, 1)
    expected_html_sha256 = _sha256_bytes(document.encode("utf-8"))

    def recheck_before_html_publish():
        if typed_guide:
            _verify_publication_inputs(
                ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)

    try:
        if typed_guide:
            _verify_publication_inputs(
                ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)
        _atomic_write(
            html_path, document, before_publish=recheck_before_html_publish)
        html_snapshot = _capture_regular_file_snapshot(
            ws, html_path, "published Study Guide HTML")
        if html_snapshot["sha256"] != expected_html_sha256:
            raise ArtifactDriftError(
                "atomically published HTML bytes differ from the rendered payload")
        if typed_guide:
            _verify_publication_inputs(
                ws, manifest_snapshot, start_gate_snapshot, deep_fact_check=False)
    except ArtifactDriftError:
        _remove_stale(html_path, os.path.basename(html_path))
        _remove_stale(pdf_path, os.path.basename(pdf_path))
        if typed_guide:
            _remove_stale(receipt_path, os.path.basename(receipt_path))
        raise
    except GuideError as exc:
        _remove_stale(html_path, os.path.basename(html_path))
        _remove_stale(pdf_path, os.path.basename(pdf_path))
        if typed_guide:
            _remove_stale(receipt_path, os.path.basename(receipt_path))
        raise ArtifactDriftError(
            "HTML publication path could not be revalidated (%s)" % exc)
    print("[+] %s：%s" % ("Study Guide" if args.artifact_type == "study_guide" else "source packet",
                           html_path))

    browser = None
    conversion = None
    if args.pdf:
        if typed_guide:
            try:
                _verify_publication_inputs(
                    ws, manifest_snapshot, start_gate_snapshot,
                    deep_fact_check=False)
                _verify_file_snapshot(ws, html_snapshot, "published Study Guide HTML")
            except ArtifactDriftError:
                _remove_stale(html_path, os.path.basename(html_path))
                _remove_stale(pdf_path, os.path.basename(pdf_path))
                _remove_stale(receipt_path, os.path.basename(receipt_path))
                raise
        browser = find_browser()
        if not browser:
            _remove_stale(pdf_path, os.path.basename(pdf_path))
            if args.artifact_type == "study_guide":
                _remove_stale(receipt_path, os.path.basename(receipt_path))
            sys.stderr.write(
                "study_guide_render: no_browser: no local Edge/Chrome matched the selected "
                "browser preflight; validated HTML was preserved.\n"
            )
            return 3
        try:
            conversion = print_pdf(
                browser, html_path, pdf_path, workspace=ws,
                materials_root=start_gate.get("materials") if start_gate else None,
                expected_html_sha256=expected_html_sha256)
        except ArtifactDriftError:
            _remove_stale(html_path, os.path.basename(html_path))
            _remove_stale(pdf_path, os.path.basename(pdf_path))
            if typed_guide:
                _remove_stale(receipt_path, os.path.basename(receipt_path))
            raise
        if typed_guide:
            try:
                _verify_publication_inputs(
                    ws, manifest_snapshot, start_gate_snapshot,
                    deep_fact_check=False)
                _verify_file_snapshot(ws, html_snapshot, "published Study Guide HTML")
            except ArtifactDriftError:
                _remove_stale(html_path, os.path.basename(html_path))
                _remove_stale(pdf_path, os.path.basename(pdf_path))
                _remove_stale(receipt_path, os.path.basename(receipt_path))
                raise
        print("[+] PDF：%s" % pdf_path)

    if args.artifact_type == "study_guide":
        try:
            _write_guide_receipt(
                ws, receipt_path, args.chapter, manifest_snapshot, manifest_report,
                html_snapshot, pdf_path, args.pdf_backend, preflight,
                start_gate_snapshot, conversion=conversion)
        except ArtifactDriftError:
            _remove_stale(html_path, os.path.basename(html_path))
            _remove_stale(pdf_path, os.path.basename(pdf_path))
            _remove_stale(receipt_path, os.path.basename(receipt_path))
            raise
        print("[+] render receipt (visual QA still required for PDF): %s" % receipt_path)

    if not args.pdf:
        return 0
    return 0


def main(argv=None):
    try:
        return run(argv)
    except GuideError as exc:
        sys.stderr.write("study_guide_render: %s\n" % exc)
        return exc.code
    except OSError as exc:
        sys.stderr.write("study_guide_render: 文件操作失败：%s\n" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
