#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Static validator for a built exam-cram workspace (stdlib only, no network/LLM).

Checks structure + quiz_bank.json schema + provenance + path safety against docs/file-format.md.
Cheap (Tier-1) engineering validation — runnable in CI or locally without any agent/benchmark run.

    python scripts/validate_workspace.py <workspace_dir>
    python scripts/validate_workspace.py <workspace_dir> --json

Exit codes:  0 = valid (warnings allowed)   1 = validation errors   2 = malformed/unreadable
"""
import os
import re
import sys
import json
import hashlib
import argparse
from urllib.parse import unquote

# 同包内的 notebook 引擎是锚点词汇（github_slug）的唯一定义点——按 select_hard_questions.py
# 的先例把 scripts/ 放进 sys.path 再导入，validator 与生成器绝不各养一套 slug 规则。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notebook as _notebook
import update_progress as _progress
import i18n as _i18n
import exam_start as _exam_start
import retrieve as _retrieve
import strict_json as _strict_json
import readiness as _readiness_matrix
from image_validation import (
    ImageValidationError as _ImageValidationError,
    validate_image_blob as _validate_image_blob,
)
from asset_policy import audit_asset_policy as _audit_asset_policy
from ingestion.identifiers import is_link_or_reparse
from ingestion.storage import ConflictError, workspace_validation_lock
from host_adapters.command_core import (
    CommandCoreError as _SnapshotError,
    collect_dependency_snapshot as _collect_dependency_snapshot,
    dependency_snapshot_receipt as _dependency_snapshot_receipt,
)
from math_text_policy import LATEX_COMMAND_RE, mask_windows_paths
from stable_ids import stable_item_id_problem

SIX_TYPES = {"choice", "subjective", "diagram", "fill_blank", "true_false", "code"}
MATERIAL_SOURCES = {"teacher", "material"}
ALL_SOURCES = {"teacher", "material", "ai_generated", "mixed", "unknown"}
# A2: 题目来源分类（正交于 source 的「答案来源」维度）——作业/讲义 quiz/例题/样卷/真题/其他
SOURCE_TYPES = {"homework", "lecture_quiz", "example", "practice_exam", "exam", "other"}
SAFE_WIKI = re.compile(r"^[\w.\-]+\.md$")
# capture the path token around references/wiki/. The LEADING class is path-only ([./\\]) so a real escape
# ("../", "/abs", "C:/…") is still captured and caught, while adjacent prose/CJK/punctuation
# (e.g. "见：references/wiki/ch1.md") is NOT swallowed into a false "path traversal" error.
WIKI_REF_RE = re.compile(r"([.\\/]*references/wiki/[\w.\-/\\]+)")
TRUE_FALSE_OK = {"true", "false", "t", "f", "yes", "no", "真", "假", "对", "错", "是", "否"}

# P0A/P0-V1: asset-aware quiz fields. A real EEC-160 test hit lecture Quiz/Example items
# that depend on a slide figure (e.g. a Venn diagram). The bank must be able to attach the
# source page/image, and the validator + quiz must fail-closed: never ask a
# diagram/figure-dependent item without its question-side context displayed first.
ASSET_ROLES = {
    "question_context", "answer_context", "figure", "table", "diagram",
    "worked_solution", "student_attempt",
}
# roles whose asset is shown to the student BEFORE asking — a requires_assets item needs one of these
# (an answer-side-only asset doesn't let the question be asked).
QUESTION_SIDE_ROLES = {"question_context", "figure", "diagram", "table"}
ASSET_TYPES = {"page_image", "crop_image", "diagram", "table_image", "other_image"}
QUESTION_TEXT_STATUS = {"full", "stub", "page_reference"}
RASTER_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
SUPPORTED_RASTER_MIMES = frozenset(RASTER_MIME_BY_EXTENSION.values())

# A lecture prompt produced by the deterministic Example/Quiz parser normally retains its heading.
# If such a prompt is labelled ``full`` but ends on a syntactic continuation cue, it is unsafe to
# treat as self-contained: this is the signature left when a wrapped inline cross-reference was
# mistaken for the next title boundary (for example ``... decisions in\nExample 1.9``).
_LECTURE_PROMPT_HEAD_RE = re.compile(
    r"^\s*(?:Example|Quiz)\s+\d+\s*\.\s*\d+(?:\s*\([A-Za-z]\))?\s+(?:Problems?\b\s*)?",
    re.I,
)
_DANGLING_LECTURE_PROMPT_RE = re.compile(
    r"(?:\b(?:as\s+)?(?:described|defined|shown|given|stated|proved|derived|illustrated|explained)\s+in"
    r"|\b(?:according\s+to|based\s+on|refer(?:ring)?\s+to)"
    r"|\b(?:in|from|of|with|for|by|and|or))\s*$",
    re.I,
)

def _unsafe_ref(s):
    """Reason a provenance file name (source_file/answer_source_file) is unsafe, or None. Subdir
    names like 'lecture/ch01.pdf' are fine; absolute / `..`-traversal / URL names are not — the quiz
    flow is told to surface the referenced page, so the name must not point outside the materials."""
    if "://" in s:
        return "URL"
    if s.startswith("/") or s.startswith("\\") or re.match(r"^[A-Za-z]:", s):
        return "绝对路径"   # incl. drive-relative like C:lecture.pdf (no slash), which resolves oddly
    if ".." in re.split(r"[\\/]", s):
        return ".. 穿越"
    return None


def _looks_like_truncated_full_lecture_prompt(item):
    """High-precision fail-closed audit for parser-produced, falsely-full lecture prompts."""
    if not isinstance(item, dict):
        return False
    if item.get("question_text_status") != "full":
        return False
    if item.get("requires_assets") is True or item.get("maybe_requires_assets") is True:
        return False
    question = item.get("question")
    if not isinstance(question, str):
        return False
    compact = " ".join(question.split())
    head = _LECTURE_PROMPT_HEAD_RE.match(compact)
    if not head:
        return False
    # Avoid diagnosing a heading-only/very short malformed record as this specific boundary bug;
    # the ordinary question/content checks own those cases.
    if len(re.findall(r"\b\w+\b", compact[head.end():])) < 5:
        return False
    return bool(_DANGLING_LECTURE_PROMPT_RE.search(compact))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


_FILE_SHA256_CACHE = {}


def _windows_file_change_time(path):
    """Return the NTFS/Win32 change timestamp, or ``None`` if unavailable.

    ``os.stat().st_ctime_ns`` is a creation timestamp on supported Windows
    Python versions, so it cannot protect a digest cache from a same-size
    rewrite whose mtime is restored.  ``FILE_BASIC_INFO.ChangeTime`` is the
    metadata-change clock maintained by the filesystem and is not settable by
    the ordinary ``SetFileTime`` API.  A failed probe deliberately disables
    caching for that file.
    """

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _FileBasicInfo(ctypes.Structure):
            _fields_ = (
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        )
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        # Metadata-only access.  Sharing does not weaken the before/after
        # generation sandwich below; it avoids disrupting student apps.
        handle = create_file(
            os.fspath(path),
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            return None
        try:
            info = _FileBasicInfo()
            if not get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                return None
            return int(info.ChangeTime)
        finally:
            close_handle(handle)
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def _file_hash_generation(path):
    stat_result = os.stat(path)
    if os.name == "nt":
        change_time = _windows_file_change_time(path)
        cacheable = change_time is not None
    else:
        change_time = int(getattr(
            stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1000000000)
        ))
        cacheable = True
    generation = (
        os.path.normcase(os.path.abspath(os.fspath(path))),
        int(getattr(stat_result, "st_dev", 0)),
        int(getattr(stat_result, "st_ino", 0)),
        int(stat_result.st_size),
        int(getattr(
            stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1000000000)
        )),
        change_time,
    )
    return generation, cacheable


def _sha256_file(path):
    absolute = os.path.normcase(os.path.abspath(os.fspath(path)))
    for unused_attempt in range(3):
        generation, cacheable = _file_hash_generation(absolute)
        if cacheable:
            cached = _FILE_SHA256_CACHE.get(generation)
            if cached is not None:
                return cached
        digest = hashlib.sha256()
        with open(absolute, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after, after_cacheable = _file_hash_generation(absolute)
        if after != generation:
            continue
        value = digest.hexdigest()
        if cacheable and after_cacheable:
            _FILE_SHA256_CACHE[generation] = value
        return value
    raise OSError("file changed repeatedly while hashing: %s" % absolute)


def _raster_mime(path, media_type=None):
    """Return the shared-validator MIME for a declared raster asset.

    The asset filename is authoritative when it has one of the advertised
    raster extensions.  A typed source/unit may additionally identify an
    extensionless asset through its media type.  Other formats (including the
    existing SVG path) retain their pre-existing handling and are not routed
    through a raster decoder by this helper.
    """

    if isinstance(path, str):
        by_extension = RASTER_MIME_BY_EXTENSION.get(
            os.path.splitext(path)[1].lower()
        )
        if by_extension:
            return by_extension
    normalized = str(media_type or "").lower().split(";", 1)[0].strip()
    if normalized == "image/jpg":
        normalized = "image/jpeg"
    if normalized == "image/x-ms-bmp":
        normalized = "image/bmp"
    return normalized if normalized in SUPPORTED_RASTER_MIMES else None


def _raster_file_validation_error(full_path, declared_path, media_type=None):
    """Return a deterministic corruption reason, or ``None`` when valid/N/A."""

    mime = _raster_mime(declared_path, media_type)
    if mime is None:
        return None
    try:
        with open(full_path, "rb") as stream:
            payload = stream.read()
        _validate_image_blob(mime, payload)
    except (OSError, _ImageValidationError) as exc:
        return str(exc)
    return None


def _plan_phase_nums(text):
    """Phase numbers in a study_plan.md, as strings — matches ALL supported word orders
    （「阶段N」「第N阶段」「Phase N」，与更新器/T4 解析器同款），否则合法计划会被当成
    没有阶段列表而跳过校验。"""
    nums = set()
    for ln in (text or "").splitlines():
        h = ln.lstrip()
        # 与更新器/T4 同口径：只认结构行、阶段号 ≥1
        if not (h.startswith("#") or h.startswith("|") or re.match(r"[-*]\s", h)
                or re.match(r"\d+\s*[.)、）]", h)):     # 有序列表（1. 阶段…）也是计划条目
            continue
        for m in re.finditer(r"阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+)", ln):
            g = next(x for x in m.groups() if x)
            if int(g) >= 1:
                nums.add(str(int(g)))      # 规范十进制——「阶段01」要能配上 current_phase=1
    return nums


def _reject_const(c):
    # json.loads accepts NaN/Infinity/-Infinity by default; reject them so quiz_bank.json is strict JSON.
    raise ValueError(f"非标准 JSON 常量 {c}（NaN/Infinity 不允许）")


def _scope_number(value):
    """Normalize an integer or common chapter label to its canonical positive string."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return str(value)
    if isinstance(value, str):
        match = re.fullmatch(
            r"\s*(?:第\s*)?(?:chapter\s*)?0*(\d+)(?:\s*章)?\s*", value, re.I)
        if match and int(match.group(1)) >= 1:
            return str(int(match.group(1)))
    return None


def _is_symlink(p):
    # dedicated seam for our own symlink checks. Tests mock THIS, not os.path.islink — because on
    # CPython <3.10 posixpath.realpath() itself calls os.path.islink, so mocking os.path.islink would
    # make realpath() try os.readlink() on a non-link and raise OSError (EINVAL) on Linux/py3.8.
    return is_link_or_reparse(p)


def _asset_safety(ws, p):
    """Path-safety for a quiz asset. Return (full_path_or_None, reason_or_None).
    full=None means the path string itself is malformed (non-string / abs / .. / URL).
    Paths are built from the raw `ws` (like the wiki checks); containment is realpath-based."""
    if not isinstance(p, str) or not p.strip():
        return None, "path 须为非空字符串"
    norm = p.replace("\\", "/")
    if "://" in norm:
        return None, "不得用 URL / 网络抓取（assets 必须是工作区内的本地文件）"
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return None, "不得用绝对路径"
    segs = [s for s in norm.split("/") if s not in ("", ".")]
    if ".." in segs:
        return None, "不得含 .. 路径穿越"
    full = os.path.join(ws, *segs)
    current = ws
    for segment in segs:
        current = os.path.join(current, segment)
        if os.path.lexists(current) and is_link_or_reparse(current):
            return full, "asset 路径不得经过符号链接/junction/reparse point"
    # normcase both sides so a Windows casing difference doesn't falsely reject a contained asset
    ws_real = os.path.normcase(os.path.realpath(ws))
    real = os.path.normcase(os.path.realpath(full))
    if real != ws_real and not real.startswith(ws_real + os.sep):
        return full, "asset 经符号链接 / 父目录逃出工作区"
    return full, None


def _asset_source_binding(asset, role, item, index=0):
    """Return the provenance field and file used to verify an asset hash.

    Explicit per-asset provenance wins. The role-based inference remains only
    for backward compatibility with banks written before asset.source_file.
    """
    explicit = asset.get("source_file") if isinstance(asset, dict) else None
    if isinstance(explicit, str) and explicit.strip():
        return "assets[%d].source_file" % index, explicit
    source_field = (
        "answer_source_file"
        if role in ("answer_context", "worked_solution")
        else "source_file"
    )
    return source_field, item.get(source_field) or item.get("source_file")


def _md_anchors(path):
    """GitHub-style anchor set for every heading in a markdown file (fence-aware; duplicate
    headings get the -1/-2 suffixes GitHub assigns). Covers both notebook entry headings
    （`## [#<id>] <title>` → github_slug 后正是 notebook.entry_anchor 的锚）and hand-written
    plain `#`-headings, so a cheatsheet 溯源链接的 #锚点 能被逐一核实而不是被丢弃。"""
    anchors, counts, fence = set(), {}, None
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            # 围栏字符+长度都跟踪（Codex r5：反引号栏内的 ~~~ 是内容不是关栏）——与 notebook 同机
            fence, marker = _notebook._fence_step(fence, ln)
            if marker or fence is not None:
                continue
            hm = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*$", ln)
            if not hm:
                continue
            slug = _notebook.github_slug(hm.group(1))
            n = counts.get(slug, 0)
            counts[slug] = n + 1
            anchors.add(slug if n == 0 else "%s-%d" % (slug, n))
    return anchors


def _strip_markdown_code(text):
    """Replace fenced and inline-code content with whitespace while retaining newlines."""
    out, fence = [], None
    for line in (text or "").splitlines(True):
        fence, marker = _notebook._fence_step(fence, line)
        if marker or fence is not None:
            out.append("\n" if line.endswith(("\n", "\r")) else "")
            continue
        # Markdown inline-code spans can use any matching run of backticks.  Replace, do not
        # delete, so diagnostic line numbers remain stable.
        line = re.sub(r"(`+).*?\1", lambda m: " " * len(m.group(0)), line)
        out.append(line)
    return "".join(out)


def _strip_standard_math(text):
    """Blank complete $...$ / $$...$$ spans, preserving newlines for diagnostics."""
    chars = list(text or "")
    n, i = len(chars), 0
    while i < n:
        if chars[i] != "$" or (i and chars[i - 1] == "\\"):
            i += 1
            continue
        width = 2 if i + 1 < n and chars[i + 1] == "$" else 1
        j = i + width
        close = None
        while j < n:
            if chars[j] == "$" and (j == 0 or chars[j - 1] != "\\"):
                if width == 1:
                    close = j
                    break
                if j + 1 < n and chars[j + 1] == "$":
                    close = j
                    break
            # Inline math may not silently consume the next paragraph.  Display math may span.
            if width == 1 and chars[j] in "\r\n":
                break
            j += 1
        if close is None:
            i += width
            continue
        end = close + width
        for k in range(i, end):
            if chars[k] not in "\r\n":
                chars[k] = " "
        i = end
    return "".join(chars)


def _raw_latex_lines(text):
    """1-based lines containing TeX commands outside standard math/code spans."""
    prose = mask_windows_paths(_strip_standard_math(_strip_markdown_code(text)))
    return sorted({prose.count("\n", 0, m.start()) + 1 for m in LATEX_COMMAND_RE.finditer(prose)})


def _policy_json_array(ws, relative, optional=False):
    path = os.path.join(ws, *relative.split("/"))
    if optional and not os.path.lexists(path):
        return []
    ws_real = os.path.normcase(os.path.realpath(ws))
    path_real = os.path.normcase(os.path.realpath(path))
    if (_is_symlink(path) or not os.path.isfile(path)
            or (path_real != ws_real and not path_real.startswith(ws_real + os.sep))):
        raise ValueError("%s is missing, non-regular, or escapes the workspace" % relative)
    try:
        value = _strict_json.loads(_read(path))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("%s is not strict UTF-8 JSON: %s" % (relative, exc))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("%s must be an array of objects" % relative)
    return value


def _policy_content_units(ws):
    ingest = os.path.join(ws, ".ingest")
    if not os.path.lexists(ingest):
        return []
    path = os.path.join(ingest, "content_units.jsonl")
    ws_real = os.path.normcase(os.path.realpath(ws))
    path_real = os.path.normcase(os.path.realpath(path))
    if (_is_symlink(ingest) or not os.path.isdir(ingest) or _is_symlink(path)
            or not os.path.isfile(path)
            or (path_real != ws_real and not path_real.startswith(ws_real + os.sep))):
        raise ValueError(
            ".ingest exists but content_units.jsonl is missing, non-regular, or unsafe"
        )
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = _strict_json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("line %d is not an object" % line_number)
                rows.append(row)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(".ingest/content_units.jsonl is invalid: %s" % exc)
    return rows


def workspace_asset_policy_snapshot(ws):
    """Return the shared physical asset policy over every workspace evidence layer.

    This is the safe public workspace entry: every invocation rereads the live quiz bank,
    teaching-example sidecar, and (when ``.ingest`` exists) the complete content-unit file.
    Callers cannot substitute cached, chapter-filtered, or hand-built rows.  That invariant is
    deliberate: an empty/partial cache must never erase a student-attempt declaration from a
    different layer or chapter.  No chapter filter is applied.
    """

    quizzes = _policy_json_array(ws, "references/quiz_bank.json")
    teaching = _policy_json_array(
        ws, "references/teaching_examples.json", optional=True
    )
    units = _policy_content_units(ws)
    labelled = (
        ("references/quiz_bank.json", quizzes),
        ("references/teaching_examples.json", teaching),
        (".ingest/content_units.jsonl", units),
    )
    for label, rows in labelled:
        if any(not isinstance(row, dict) and not hasattr(row, "metadata") for row in rows):
            raise ValueError("%s must contain item/content-unit objects" % label)

    audit = _audit_asset_policy(
        quiz_rows=quizzes,
        teaching_rows=teaching,
        content_units=units,
        workspace=ws,
    )
    return {
        "quiz_rows": quizzes,
        "teaching_rows": teaching,
        "content_units": units,
        "tainted_keys": audit["tainted_keys"],
        "tainted_identity_keys": audit["tainted_identity_keys"],
        "conflicts": audit["conflicts"],
        "item_groups": audit["item_groups"],
        # Preserve the established consumer key while broadening it from unsafe
        # paths to every malformed/unknown asset declaration.
        "unsafe_paths": audit["invalid_declarations"],
    }


def _validate_unlocked(ws):
    """Return (errors, warnings, stats). errors may carry level 'error' or 'fatal'."""
    errors, warnings, stats = [], [], {}
    ingestion_source_hashes = {}

    def err(msg, level="error"):
        errors.append({"level": level, "msg": msg})

    def warn(msg):
        warnings.append({"level": "warning", "msg": msg})

    if not os.path.isdir(ws):
        err(f"workspace directory不存在或不可读: {ws}", level="fatal")
        return errors, warnings, stats

    # Determine the effective processing route before enforcing full-build structure.
    # This is a best-effort hint only; the authoritative state parser below still
    # reports every malformed/symlinked state error.  Missing/damaged legacy state
    # keeps the historical full-workspace requirements rather than failing open.
    processing_mode_hint = "full"
    state_hint_path = os.path.join(ws, "study_state.json")
    if (os.path.isfile(state_hint_path) and not _is_symlink(state_hint_path)
            and os.path.realpath(state_hint_path).startswith(os.path.realpath(ws) + os.sep)):
        try:
            state_hint = _strict_json.loads(_read(state_hint_path))
            if isinstance(state_hint, dict):
                processing_mode_hint = _i18n.workspace_processing_mode(state_hint)
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            pass
    lightweight_workspace = processing_mode_hint == "lightweight"
    stats["processing_mode_hint"] = processing_mode_hint

    # ---- structure ----
    wiki_dir = os.path.join(ws, "references", "wiki")
    wiki_is_link = _is_symlink(wiki_dir)
    # realpath containment also catches a symlinked PARENT (e.g. references/ itself is a symlink),
    # which islink(wiki_dir) alone misses.
    ws_real = os.path.realpath(ws)
    wiki_real = os.path.realpath(wiki_dir)
    wiki_escapes = (os.path.isdir(wiki_dir) and wiki_real != ws_real
                    and not wiki_real.startswith(ws_real + os.sep))
    has_wiki = os.path.isdir(wiki_dir) and not wiki_is_link and not wiki_escapes
    if wiki_is_link or wiki_escapes:
        err("references/wiki/ 经符号链接逃出工作区（本身或其父目录是指向工作区外的软链）")
    elif not has_wiki and not lightweight_workspace:
        err("缺少 references/wiki/ 目录")
    qb_path = os.path.join(ws, "references", "quiz_bank.json")
    qb_is_link = _is_symlink(qb_path)
    qb_escapes = os.path.isfile(qb_path) and not os.path.realpath(qb_path).startswith(ws_real + os.sep)
    has_qb = os.path.isfile(qb_path) and not qb_is_link and not qb_escapes
    if lightweight_workspace:
        # Existing full-build products are dormant history in lightweight mode.
        # Preserve only the path-safety verdicts; do not walk their contents.
        stats["dormant_wiki_present"] = bool(has_wiki)
        stats["dormant_quiz_bank_present"] = bool(has_qb)
        has_wiki = False
        has_qb = False
    if qb_is_link or qb_escapes:
        err("references/quiz_bank.json 经符号链接逃出工作区（指向工作区外的答案源）")
    elif not has_qb and not lightweight_workspace:
        err("缺少 references/quiz_bank.json")
    for name, label in (("study_plan.md", "复习计划"), ("study_progress.md", "进度文件")):
        rp = os.path.join(ws, name)
        if _is_symlink(rp) or (os.path.isfile(rp)
                                  and not os.path.realpath(rp).startswith(ws_real + os.sep)):
            err(f"{label} 经符号链接逃出工作区（技能会读/写这个路径）: {name}")
        elif not os.path.isfile(rp):
            if lightweight_workspace and name == "study_plan.md":
                # A lightweight session is intentionally state-led and may never
                # build the full plan artifact.  Record that fact without
                # downgrading readiness or interrupting every teaching turn.
                stats["lightweight_study_plan"] = "not_applicable_missing"
            else:
                err(f"缺少 {label}: {name}")

    # ---- structured ingestion truth + typed AI review queue ----
    ingest_dir = os.path.join(ws, ".ingest")
    ingest_active = os.path.lexists(ingest_dir) and not lightweight_workspace
    if lightweight_workspace and os.path.lexists(ingest_dir):
        ingest_real = os.path.realpath(ingest_dir)
        if (_is_symlink(ingest_dir) or not os.path.isdir(ingest_dir)
                or (ingest_real != ws_real and not ingest_real.startswith(ws_real + os.sep))):
            err("dormant .ingest/ 不是工作区内的常规目录（轻量模式同样拒绝不安全路径）")
        else:
            stats["ingest_dormant"] = True
            stats["ingest_dormant_validation"] = "path_safety_and_pending_only"
            for pending_name in (
                    "material_build_pending.json", "pending_ingest.json",
                    "pending_patch.json"):
                pending_path = os.path.join(ingest_dir, pending_name)
                if not os.path.lexists(pending_path):
                    continue
                pending_real = os.path.realpath(pending_path)
                if (_is_symlink(pending_path) or not os.path.isfile(pending_path)
                        or not pending_real.startswith(ingest_real + os.sep)):
                    err(
                        ".ingest/%s is unsafe; an interrupted transaction cannot "
                        "be ignored in lightweight mode" % pending_name,
                        level="fatal",
                    )
                else:
                    err(
                        ".ingest/%s records an interrupted full-build transaction; "
                        "repair or roll it back before teaching" % pending_name
                    )
        # Do not traverse or validate heavyweight derived facts in lightweight mode.
    if ingest_active:
        ingest_real = os.path.realpath(ingest_dir)
        if (_is_symlink(ingest_dir) or not os.path.isdir(ingest_dir)
                or (ingest_real != ws_real and not ingest_real.startswith(ws_real + os.sep))):
            err(".ingest/ 不是工作区内的常规目录（拒绝读取结构化事实源）")
        else:
            material_pending_path = os.path.join(
                ingest_dir, "material_build_pending.json"
            )
            if os.path.lexists(material_pending_path):
                if (_is_symlink(material_pending_path)
                        or not os.path.isfile(material_pending_path)):
                    err(
                        ".ingest/material_build_pending.json is unsafe; "
                        "the workspace remains blocked",
                        level="fatal",
                    )
                else:
                    err(
                        ".ingest/material_build_pending.json means material "
                        "compilation did not finish; rerun ingest.py or "
                        "ingest_course.py before teaching"
                    )
            try:
                from ingestion import IngestionStore, read_json
                from ingestion.pipeline import (
                    _validate_parser_review_consistency,
                    _validated_parser_receipts,
                    verify_material_build_receipt,
                )
                from ingestion.dedup import validate_workspace_fact_integrity
            except ImportError as exc:
                err(f"无法加载结构化 ingestion 校验器: {exc}", level="fatal")
                IngestionStore = None
                read_json = None
                validate_workspace_fact_integrity = None
                _validate_parser_review_consistency = None
                _validated_parser_receipts = None
                verify_material_build_receipt = None

            build_path = os.path.join(ingest_dir, "build_manifest.json")
            if not os.path.isfile(build_path) or _is_symlink(build_path):
                err("存在 .ingest/ 但缺少常规文件 build_manifest.json")
                build_manifest = None
            else:
                try:
                    build_manifest = read_json(build_path) if read_json else None
                except Exception as exc:
                    err(f".ingest/build_manifest.json 无法严格读取: {exc}", level="fatal")
                    build_manifest = None

            if (isinstance(build_manifest, dict)
                    and verify_material_build_receipt is not None):
                try:
                    verify_material_build_receipt(
                        ws, build_manifest=build_manifest
                    )
                except Exception as exc:
                    err(
                        "material build generation receipt is invalid: %s" % exc,
                        level="fatal",
                    )

            if isinstance(build_manifest, dict):
                manifest_schema = build_manifest.get("schema_version")
                if (type(manifest_schema) is not int
                        or manifest_schema not in (1, 2)):
                    err(
                        ".ingest/build_manifest.json schema_version must be integer 1 or 2",
                        level="fatal",
                    )

            if isinstance(build_manifest, dict) and IngestionStore is not None:
                pipeline_version = build_manifest.get("pipeline_version")
                if pipeline_version not in ("ingestion-v1", "ingestion-v2"):
                    err(
                        ".ingest/build_manifest.json 的 pipeline_version 缺失或不受支持；"
                        "请用当前 ingest 重建"
                    )
                source_root = build_manifest.get("source_root")
                if not isinstance(source_root, str) or not os.path.isdir(source_root):
                    err(".ingest/build_manifest.json 的 source_root 不存在；原材料已移动或不可读")
                    store = None
                else:
                    try:
                        store = IngestionStore(ws, source_root=source_root)
                        sources = store.manifest.records()
                        units = store.units()
                        mappings = store.mappings()
                        base_units = store.base_units()
                        base_mappings = store.base_mappings()
                        ledger_entries = store.ledger_entries()
                        issues = store.review_queue.issues()
                        store.verify_compiled_matches_ledger()
                    except Exception as exc:
                        err(f".ingest 结构化文件 schema/路径校验失败: {exc}", level="fatal")
                        store = None

                if store is not None:
                    stats["ingestion_sources"] = len(sources)
                    stats["ingestion_units"] = len(units)
                    stats["ingestion_mappings"] = len(mappings)
                    stats["ingestion_base_units"] = len(base_units)
                    stats["ingestion_review_patches"] = len(ledger_entries)
                    stats["ingestion_review_issues"] = len(issues)
                    ingestion_source_hashes.update(
                        (source.path, source.sha256) for source in sources
                    )
                    if os.path.lexists(store.pending_patch_path):
                        err(
                            ".ingest/pending_patch.json 表示上次审核补丁写入中断；"
                            "请用原 patch 重试 apply，不能继续教学"
                        )
                    if os.path.lexists(store.pending_ingest_path):
                        err(
                            ".ingest/pending_ingest.json 表示上次材料入库事务中断；"
                            "请先重跑入库以触发自动回滚/恢复，不能继续教学"
                        )
                    expected_counts = {
                        "source_count": len(sources),
                        "unit_count": len(units),
                        "review_issue_count": len(issues),
                    }
                    for count_key, actual_count in expected_counts.items():
                        if build_manifest.get(count_key) != actual_count:
                            err(
                                ".ingest/build_manifest.json 的 %s=%r 与事实源 %d 不一致"
                                % (count_key, build_manifest.get(count_key), actual_count)
                            )
                    # ingestion-v2 performs a before/after source-revision check inside
                    # the shared deterministic fact-integrity gate below.  Avoid a third
                    # full hash pass over large course PDFs here; legacy v1 still needs
                    # this direct check.
                    if pipeline_version != "ingestion-v2":
                        for source in sources:
                            try:
                                store.manifest.verify_current(
                                    source.source_id, source.sha256
                                )
                            except Exception as exc:
                                err(
                                    "原材料版本漂移，review patch/索引不可再信任: "
                                    f"{source.path}（{exc}）"
                                )

                    source_media_types = {
                        source.source_id: source.media_type for source in sources
                    }
                    for unit in units.values():
                        if not unit.asset_path:
                            continue
                        asset_path = os.path.join(
                            ws, *unit.asset_path.replace("\\", "/").split("/")
                        )
                        asset_real = os.path.realpath(asset_path)
                        if (_is_symlink(asset_path) or not os.path.isfile(asset_path)
                                or not asset_real.startswith(ws_real + os.sep)):
                            err(
                                "ContentUnit 资产缺失/不安全: %s（unit %s）"
                                % (unit.asset_path, unit.unit_id)
                            )
                        else:
                            raster_error = _raster_file_validation_error(
                                asset_path,
                                unit.asset_path,
                                source_media_types.get(unit.source_id),
                            )
                            if raster_error:
                                err(
                                    "ContentUnit 光栅资产损坏或与扩展名不符: "
                                    "%s（unit %s；%s）"
                                    % (unit.asset_path, unit.unit_id, raster_error)
                                )
                            expected_asset_hash = unit.metadata.get("asset_sha256")
                            if (expected_asset_hash is not None
                                    and _sha256_file(asset_path) != expected_asset_hash):
                                err(
                                    "ContentUnit 资产哈希漂移: %s（unit %s）"
                                    % (unit.asset_path, unit.unit_id)
                                )

                    for issue in issues:
                        if issue.status in ("pending", "claimed", "validated", "blocked"):
                            message = (
                                f"未接管 ingestion issue {issue.issue_id}: "
                                f"{','.join(issue.reason_codes)}；{issue.description}；"
                                f"建议：{issue.suggested_action}"
                            )
                            if issue.severity == "blocking":
                                err(message)
                            else:
                                warn(message)
                        elif issue.status == "unrecoverable":
                            warn(
                                f"ingestion issue {issue.issue_id} 已标记不可恢复："
                                f"{','.join(issue.reason_codes)}（内容完整性存在已知缺口）"
                            )

                    # Every page known to the quality router must have an explicit
                    # page_anchor, including blank scanned pages.
                    page_quality = build_manifest.get("page_quality")
                    if not isinstance(page_quality, list):
                        err(".ingest/build_manifest.json 缺少 page_quality 数组")
                    else:
                        expected_pages = set()
                        duplicate_pages = set()
                        for row in page_quality:
                            if not isinstance(row, dict):
                                err(".ingest page_quality 条目必须是对象")
                                continue
                            key = (row.get("source_file"), row.get("page"))
                            if key in expected_pages:
                                duplicate_pages.add(key)
                            expected_pages.add(key)
                        if duplicate_pages:
                            err(f".ingest page_quality 含重复 source/page: {sorted(duplicate_pages)!r}")
                        actual_pages = {
                            (unit.source_file, unit.page)
                            for unit in units.values() if unit.kind == "page_anchor"
                        }
                        missing_pages = expected_pages - actual_pages
                        if missing_pages:
                            err(
                                ".ingest 页面记账不完整，缺 page_anchor: "
                                + "、".join("%s p.%s" % key for key in sorted(missing_pages)[:20])
                            )
                        stats["ingestion_pages"] = len(expected_pages)

                    if pipeline_version == "ingestion-v2":
                        parser_receipts_path = os.path.join(
                            ingest_dir, "parser_receipts.json"
                        )
                        try:
                            parser_receipt_document = read_json(parser_receipts_path)
                        except Exception as exc:
                            err(f".ingest/parser_receipts.json 无法严格读取: {exc}")
                            parser_receipt_document = None
                        receipts = (
                            parser_receipt_document.get("receipts")
                            if isinstance(parser_receipt_document, dict)
                            and parser_receipt_document.get("schema_version") == 1
                            else None
                        )
                        if not isinstance(receipts, list):
                            err(".ingest/parser_receipts.json 缺少 v1 receipts 数组")
                        elif (_validated_parser_receipts is None
                              or _validate_parser_review_consistency is None):
                            err("无法加载 parser receipt 校验器", level="fatal")
                        else:
                            try:
                                validated_receipts = _validated_parser_receipts(
                                    {"schema_version": 2, "parser_receipts": receipts},
                                    list(sources),
                                    page_quality if isinstance(page_quality, list) else [],
                                )
                                _validate_parser_review_consistency(
                                    validated_receipts,
                                    list(sources),
                                    page_quality if isinstance(page_quality, list) else [],
                                    issues,
                                    ledger_entries,
                                )
                            except Exception as exc:
                                err(f"parser receipt 与来源/页面事实不一致: {exc}")
                            else:
                                stats["ingestion_parser_receipts"] = len(receipts)

                        source_conflicts = ()
                        if validate_workspace_fact_integrity is None:
                            err("无法加载共享 fact integrity 校验器", level="fatal")
                        else:
                            try:
                                fact_integrity = validate_workspace_fact_integrity(ws)
                            except Exception as exc:
                                err(
                                    "dedup/conflict sidecars 与 manifest/live units/"
                                    "sources/ledger 的确定性完整性校验失败: %s" % exc
                                )
                            else:
                                source_conflicts = fact_integrity["conflicts"]
                                stats.update({
                                    "ingestion_duplicate_candidates": fact_integrity[
                                        "candidate_count"],
                                    "ingestion_canonical_groups": fact_integrity[
                                        "canonical_group_count"],
                                    "ingestion_source_conflicts": fact_integrity[
                                        "conflict_count"],
                                })
                                if "near_candidate_comparison_cap_reached" in fact_integrity[
                                        "warnings"]:
                                    warn(
                                        "近重复比较达到显式上限；未比较候选仍需人工抽查，"
                                        "不能声称 near-duplicate recall 完整"
                                    )
                            for conflict in source_conflicts:
                                if conflict.status != "unresolved":
                                    continue
                                message = (
                                    "未解决来源冲突 %s: %s（%s）"
                                    % (
                                        conflict.conflict_id,
                                        conflict.conflict_kind,
                                        ",".join(conflict.reason_codes),
                                    )
                                )
                                # Every unresolved source conflict is a missing
                                # evidence decision.  Visual/context variants may
                                # be lower-risk than disagreeing answer keys, but
                                # neither is safe to silently pass as a usable
                                # knowledge base.  The typed review queue must
                                # explicitly keep both, correct the source-backed
                                # unit, or mark the conflict unrecoverable first.
                                err(message + "；必须先通过 typed review 明确裁决")
                    else:
                        stats["ingestion_parser_receipts"] = 0

                    unbound_path = os.path.join(ingest_dir, "unbound_review.json")
                    try:
                        unbound = read_json(unbound_path)
                    except Exception as exc:
                        err(f".ingest/unbound_review.json 无法严格读取: {exc}")
                        unbound = None
                    entries = unbound.get("entries") if isinstance(unbound, dict) else None
                    if not isinstance(entries, list):
                        err(".ingest/unbound_review.json 缺少 entries 数组")
                    else:
                        stats["ingestion_unbound_reviews"] = len(entries)
                        for entry in entries:
                            if not isinstance(entry, dict):
                                err(".ingest unbound review 条目必须是对象")
                                continue
                            message = (
                                "未绑定来源的 ingestion 告警：%s；%s"
                                % (
                                    ",".join(entry.get("reason_codes") or ["review_required"]),
                                    entry.get("description") or "无描述",
                                )
                            )
                            if entry.get("severity") == "blocking":
                                err(message)
                            else:
                                warn(message)

                    # Build-manifest hashes prove that AI fixes, compiled units, and
                    # retrieval products belong to the same build.
                    integrity_rows = {}
                    for group_name in ("artifacts", "derived_artifacts"):
                        group = build_manifest.get(group_name, {})
                        if group is None:
                            continue
                        if not isinstance(group, dict):
                            err(f".ingest/build_manifest.json 的 {group_name} 必须是对象")
                            continue
                        integrity_rows.update(group)
                    for label, row in integrity_rows.items():
                        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                            err(f".ingest build artifact {label!r} schema 无效")
                            continue
                        relative = row.get("path")
                        expected_hash = row.get("sha256")
                        if not isinstance(relative, str) or _unsafe_ref(relative):
                            err(f".ingest build artifact {label!r} 路径不安全: {relative!r}")
                            continue
                        absolute = os.path.join(ws, *relative.replace("\\", "/").split("/"))
                        real = os.path.realpath(absolute)
                        if (_is_symlink(absolute) or not os.path.isfile(absolute)
                                or (real != ws_real and not real.startswith(ws_real + os.sep))):
                            err(f".ingest build artifact {label!r} 缺失/逃逸: {relative}")
                        elif not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
                            err(f".ingest build artifact {label!r} sha256 无效")
                        elif _sha256_file(absolute) != expected_hash:
                            err(f".ingest build artifact {label!r} 已漂移: {relative}（请重建）")

            elif build_manifest is not None:
                err(".ingest/build_manifest.json 顶层必须是对象")

    # A present retrieval index must be self-consistent.  Missing remains a
    # legacy-compatible degradation; a stale index is worse than no index.
    retrieval_path = os.path.join(ws, "references", "retrieval_index.json")
    if (not lightweight_workspace and os.path.isfile(retrieval_path)
            and not _is_symlink(retrieval_path)):
        try:
            _retrieve.load_index(ws)
        except SystemExit as exc:
            err(f"references/retrieval_index.json 被运行时检索器拒绝（exit {exc.code}）")
        except (OSError, UnicodeDecodeError) as exc:
            err(f"references/retrieval_index.json 无法读取: {exc}")

    # ---- wiki filenames must be safe ----
    wiki_files = set()
    raw_math_hits = {}
    if has_wiki:
        try:
            entries = sorted(os.listdir(wiki_dir))
        except OSError as e:
            err(f"references/wiki/ 无法读取（权限/瞬时移除）: {e}", level="fatal")
            entries = []
        for entry in entries:
            full_e = os.path.join(wiki_dir, entry)
            if _is_symlink(full_e):
                err(f"references/wiki/ 下不应有符号链接（可能指向工作区外）: {entry}")
                continue
            if os.path.isdir(full_e):
                err(f"references/wiki/ 下不应有子目录: {entry}")
                continue
            if not SAFE_WIKI.match(entry):
                err(f"不安全的 wiki 文件名（疑似路径穿越/非法字符）: {entry}")
            wiki_files.add(entry)
            if os.path.isfile(full_e):
                try:
                    wiki_text = _read(full_e)
                    nul_count = wiki_text.count("\x00")
                    if nul_count:
                        warn(f"references/wiki/{entry} 含 {nul_count} 个 NUL 字节——PDF 文本可能把图/"
                             "空间布局退化成二进制残渣，须对照原页复核")
                    math_lines = _raw_latex_lines(wiki_text)
                    if math_lines:
                        raw_math_hits[f"references/wiki/{entry}"] = math_lines
                except (OSError, UnicodeDecodeError) as e:
                    err(f"references/wiki/{entry} 无法按 UTF-8 读取: {e}")
        stats["wiki_files"] = len(wiki_files)

    # Notebook/mistake entries are the other durable teaching sources.  Do not follow symlinks
    # just to lint them; path validation elsewhere remains fail-closed for referenced artifacts.
    for dirname in ("notebook", "mistakes"):
        root = os.path.join(ws, dirname)
        if not os.path.isdir(root) or _is_symlink(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if not entry.lower().endswith(".md") or not os.path.isfile(full) or _is_symlink(full):
                continue
            try:
                math_lines = _raw_latex_lines(_read(full))
            except (OSError, UnicodeDecodeError):
                continue
            if math_lines:
                raw_math_hits[f"{dirname}/{entry}"] = math_lines

    cheat_math = os.path.join(ws, "cheatsheet.md")
    if os.path.isfile(cheat_math) and not _is_symlink(cheat_math):
        try:
            math_lines = _raw_latex_lines(_read(cheat_math))
        except (OSError, UnicodeDecodeError):
            math_lines = []
        if math_lines:
            raw_math_hits["cheatsheet.md"] = math_lines

    for rel, lines in sorted(raw_math_hits.items()):
        preview = ", ".join(str(n) for n in lines[:8])
        suffix = "…" if len(lines) > 8 else ""
        warn(f"{rel} 第 {preview}{suffix} 行含标准数学分隔符之外的 raw/伪分隔 LaTeX；"
             "请改用 $...$ 或 $$...$$，再由 study_guide_render.py 转为 MathML")
    if raw_math_hits:
        stats["raw_latex_files"] = len(raw_math_hits)
        stats["raw_latex_occurrences"] = sum(len(v) for v in raw_math_hits.values())

    # ---- path-traversal in wiki references inside the .md files ----
    def scan_refs(text, where):
        norm = (text or "").replace("\\", "/")   # treat Windows separators as path separators so
        for m in WIKI_REF_RE.finditer(norm):      # references\wiki\..\..\x.md is matched & traversal-checked
            full = m.group(1).rstrip("。．.")   # drop a trailing sentence period (a real name ends in ".md")
            idx = full.find("references/wiki/")
            prefix, fname = full[:idx], full[idx + len("references/wiki/"):]
            if (".." in full or full.startswith(("/", "\\")) or (len(full) >= 2 and full[1] == ":")
                    or (prefix and prefix != "./")):
                err(f"{where} 中存在路径穿越/逃逸的 wiki 引用: {full}")
            elif not SAFE_WIKI.match(fname):
                err(f"{where} 的 wiki 引用不符合扁平 references/wiki/*.md 规范（不应有子目录）: references/wiki/{fname}")
            elif has_wiki and fname not in wiki_files:
                # ingest.py writes each phase's wiki file BEFORE rendering study_plan.md from the same
                # phase list, so a freshly-ingested workspace never dangles. A missing ref therefore means
                # a renamed/deleted wiki (the skill must load it before teaching) -> hard error, not warning.
                err(f"{where} 引用的 wiki 文件不存在: references/wiki/{fname}（阶段加载前必须存在）")
    for name in ("study_plan.md", "study_progress.md"):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            try:
                scan_refs(_read(p), name)
            except OSError as e:
                err(f"{name}（必需文件）无法读取: {e}", level="fatal")

    # ---- v4.1 completeness: optional visual index, warning-level (runnable schema stays compatible) ----
    fig_index_path = os.path.join(ws, "references", "figure_page_index.json")
    if (not lightweight_workspace and os.path.isfile(fig_index_path)
            and not _is_symlink(fig_index_path)):
        try:
            fig_index = _strict_json.loads(_read(fig_index_path))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            warn(f"figure_page_index.json 无法读取，wiki 视觉覆盖未经核对: {e}")
            fig_index = None
        if isinstance(fig_index, dict):
            coverage = fig_index.get("wiki_visual_coverage")
            if coverage is None:
                warn("figure_page_index.json 未包含 wiki_visual_coverage（旧索引仍可用，但不能证明 wiki 视觉完整；"
                     "请重跑 build_visual_index.py）")
            elif not isinstance(coverage, dict):
                warn("figure_page_index.json 的 wiki_visual_coverage 不是对象，视觉完整性统计不可用")
            else:
                detected = coverage.get("detected")
                embedded = coverage.get("embedded")
                missing = coverage.get("missing")
                if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
                           for x in (detected, embedded, missing)):
                    warn("wiki_visual_coverage 的 detected/embedded/missing 必须是非负整数")
                else:
                    stats["wiki_visual_detected"] = detected
                    stats["wiki_visual_embedded"] = embedded
                    stats["wiki_visual_missing"] = missing
                    if detected != embedded + missing:
                        warn("wiki_visual_coverage 计数不一致：detected != embedded + missing（索引可能陈旧/损坏）")
                    if missing:
                        rows = coverage.get("missing_pages")
                        preview = []
                        if isinstance(rows, list):
                            for row in rows[:8]:
                                if isinstance(row, dict):
                                    preview.append("%s p.%s" % (row.get("source_file", "?"), row.get("page", "?")))
                        warn("wiki 视觉覆盖缺口：已检测 %d 页、已嵌入 %d 页、缺失 %d 页%s——"
                             "工作区可运行，但不能据此宣称内容完整"
                             % (detected, embedded, missing,
                                 ("（" + "、".join(preview) + ("…" if isinstance(rows, list) and len(rows) > 8 else "") + "）")
                                 if preview else ""))
                deferred = coverage.get("deferred_answer_count", 0)
                manual_exposure = coverage.get("manual_answer_exposure_count", 0)
                shared_count = coverage.get("shared_prompt_answer_count", 0)
                shared_blockers = coverage.get("shared_prompt_answer_blocker_count", 0)
                if not isinstance(deferred, int) or isinstance(deferred, bool) or deferred < 0:
                    warn("wiki_visual_coverage.deferred_answer_count 必须是非负整数")
                else:
                    stats["wiki_visual_deferred_answer"] = deferred
                if (not isinstance(manual_exposure, int) or isinstance(manual_exposure, bool)
                        or manual_exposure < 0):
                    warn("wiki_visual_coverage.manual_answer_exposure_count 必须是非负整数")
                else:
                    stats["wiki_manual_answer_exposure"] = manual_exposure
                    if manual_exposure:
                        warn("wiki 仍提前暴露 %d 个答案专属页；必须移除手工/旧式嵌图后重建视觉索引，"
                             "否则不得宣称内容完整" % manual_exposure)
                if (not isinstance(shared_count, int) or isinstance(shared_count, bool)
                        or shared_count < 0):
                    warn("wiki_visual_coverage.shared_prompt_answer_count 必须是非负整数")
                else:
                    stats["wiki_visual_shared_prompt_answer"] = shared_count
                if (not isinstance(shared_blockers, int) or isinstance(shared_blockers, bool)
                        or shared_blockers < 0):
                    warn("wiki_visual_coverage.shared_prompt_answer_blocker_count 必须是非负整数")
                else:
                    stats["wiki_shared_prompt_answer_blockers"] = shared_blockers
                    if isinstance(shared_count, int) and not isinstance(shared_count, bool) \
                            and shared_blockers > shared_count:
                        warn("wiki_visual_coverage 计数不一致：共享页 blocker 数大于共享页总数")
                    if shared_blockers:
                        rows = coverage.get("shared_prompt_answer_blocker_pages")
                        preview = []
                        if isinstance(rows, list):
                            for row in rows[:8]:
                                if isinstance(row, dict):
                                    preview.append("%s p.%s" %
                                                   (row.get("source_file", "?"), row.get("page", "?")))
                        warn("题面与答案共页且缺少经审核的独立题面裁图：%d 页%s——整页图不得在提问前展示"
                             % (shared_blockers,
                                ("（" + "、".join(preview) + "）") if preview else ""))

    image_index_path = os.path.join(ws, "references", "image_question_index.json")
    if (not lightweight_workspace and os.path.isfile(image_index_path)
            and not _is_symlink(image_index_path)):
        try:
            image_index = _strict_json.loads(_read(image_index_path))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            warn(f"image_question_index.json 无法读取，题面/答案视觉疑漏未经核对: {e}")
            image_index = None
        if isinstance(image_index, dict):
            prompt_rows = image_index.get("prompt_suspects", image_index.get("suspects", []))
            answer_rows = image_index.get("answer_suspects")
            if not isinstance(prompt_rows, list):
                warn("image_question_index.json 的 prompt_suspects/suspects 不是数组")
            else:
                stats["visual_prompt_suspects"] = len(prompt_rows)
                if prompt_rows:
                    ids = [str(x.get("id")) for x in prompt_rows[:8] if isinstance(x, dict) and x.get("id") is not None]
                    warn("题面侧视觉疑漏 %d 道%s——未补 question_context 前不能把疑漏数 0/非0 当作内容完整结论"
                         % (len(prompt_rows), ("（" + "、".join(ids) + "）") if ids else ""))
            if answer_rows is None:
                warn("image_question_index.json 未包含 answer_suspects（旧索引未核对答案侧视觉覆盖）")
            elif not isinstance(answer_rows, list):
                warn("image_question_index.json 的 answer_suspects 不是数组")
            else:
                stats["visual_answer_suspects"] = len(answer_rows)
                if answer_rows:
                    ids = [str(x.get("id")) for x in answer_rows[:8] if isinstance(x, dict) and x.get("id") is not None]
                    warn("答案侧视觉疑漏 %d 道%s——须补 answer_context，且只能在解答阶段展示"
                         % (len(answer_rows), ("（" + "、".join(ids) + "）") if ids else ""))

    # ---- quiz_bank.json schema ----
    if has_qb:
        try:
            data = _strict_json.loads(_read(qb_path))
        except (ValueError, OSError) as e:
            err(f"quiz_bank.json 不是合法 JSON: {e}", level="fatal")
            return errors, warnings, stats
        if not isinstance(data, list):
            err("quiz_bank.json 顶层必须是 JSON 数组", level="fatal")
            return errors, warnings, stats
        stats["quiz_items"] = len(data)
        stats["quiz_items_gradable"] = sum(
            1 for item in data
            if isinstance(item, dict) and item.get("gradable") is not False
        )
        stats["quiz_items_non_gradable"] = sum(
            1 for item in data
            if isinstance(item, dict) and item.get("gradable") is False
        )
        seen, type_counts = set(), {}
        for i, q in enumerate(data):
            if not isinstance(q, dict):
                err(f"题[{i}] 必须是对象")
                continue
            tag = f"题[{q.get('id', i)}]"
            for fld in ("id", "type", "question"):
                v = q.get(fld)
                # presence check (not truthiness) so id=0 stays valid; whitespace-only strings count as blank
                if v in (None, "") or (isinstance(v, str) and not v.strip()):
                    err(f"{tag} 缺少必需字段 {fld}")
            if q.get("question") not in (None, "") and not isinstance(q.get("question"), str):
                err(f"{tag} 的 question 必须是非空字符串，当前为 {type(q.get('question')).__name__}")
            if q.get("chapter") in (None, "") and q.get("phase") in (None, ""):
                # ingest.py does NOT require chapter/phase, so a hard error would reject valid ingest
                # output. Keep it a WARNING (章节测验按它过滤抽题，缺了会抽不到，但不判工作区无效).
                warn(f"{tag} 缺少 chapter 或 phase（章节测验按它过滤抽题，缺了会抽不到该题）")
            for f2 in ("chapter", "phase"):
                cv = q.get(f2)
                # schema限定整数/字符串；数组/对象不可用于过滤。bool 是 int 子类，需显式排除
                # （chapter:true 会被当成 1，把题分到错误阶段）。
                if cv is not None and (isinstance(cv, bool) or not isinstance(cv, (str, int))):
                    err(f"{tag} 的 {f2} 必须是整数或字符串，当前为 {type(cv).__name__}")
            # Current ingestion writes canonical string IDs.  Readable legacy integer
            # IDs remain accepted through the same string identity; float/bool and
            # Guide-unsafe strings fail closed instead of diverging across selectors.
            qid = q.get("id")
            if (isinstance(qid, bool)
                    or not isinstance(qid, (str, int))):
                err(f"{tag} 的 id 必须是稳定字符串（旧整数可读），当前为 {type(qid).__name__}")
                qid = None
            if qid is not None:
                canonical_qid = str(qid)
                id_problem = stable_item_id_problem(canonical_qid)
                if id_problem:
                    err(f"{tag} 的 id 不符合稳定 notebook/Guide 契约：{id_problem}")
                    qid = None
                elif canonical_qid in seen:
                    err(f"重复的题目 id: {canonical_qid}")
                else:
                    seen.add(canonical_qid)
            gradable_raw = q.get("gradable")
            if gradable_raw is not None and not isinstance(gradable_raw, bool):
                err(f"{tag} gradable 必须是布尔型 true/false，当前 {gradable_raw!r}")
            is_gradable = gradable_raw is not False
            t = q.get("type")
            if t is not None and not isinstance(t, str):
                err(f"{tag} 的 type 必须是字符串，当前为 {type(t).__name__}")
                t = None
            if t is not None:
                if is_gradable:
                    type_counts[t] = type_counts.get(t, 0) + 1
                if t not in SIX_TYPES:
                    err(f"{tag} 的 type 非法: {t!r}（应为 {sorted(SIX_TYPES)} 之一）")

            # per-type required/recommended
            if (is_gradable and t == "choice"
                    and not (isinstance(q.get("options"), list) and q.get("options"))):
                err(f"{tag} choice 题必须有非空 options")
            if (is_gradable and t == "choice"
                    and isinstance(q.get("options"), list) and q.get("options")
                    and q.get("answer") not in (None, "")):
                # the answer may name an option by label ("A"), full option string, or just the option
                # TEXT after the label ("先进后出" for "A. 先进后出" — common in exported banks)
                def _label(o):
                    m = re.match(r"\s*([A-Za-z0-9]+)\s*[.．)：:、]", str(o))
                    return (m.group(1) if m else str(o).strip()).upper()
                def _text(o):
                    return re.sub(r"^\s*[A-Za-z0-9]+\s*[.．)：:、]\s*", "", str(o)).strip()
                labels = {_label(o) for o in q["options"]}
                texts = {_text(o) for o in q["options"]}
                am = re.match(r"\s*([A-Za-z0-9]+)", str(q["answer"]))
                ans_label = (am.group(1) if am else str(q["answer"]).strip()).upper()
                if (q["answer"] not in q["options"] and ans_label not in labels
                        and str(q["answer"]).strip() not in texts):
                    err(f"{tag} choice 的 answer {q['answer']!r} 不在 options 中")
            if is_gradable and t == "subjective" and not q.get("keywords"):
                warn(f"{tag} subjective 题建议提供 keywords（要点检索判分）")
            if is_gradable and t == "diagram" and not q.get("diagram_type"):
                warn(f"{tag} diagram 题建议提供 diagram_type / 渲染说明（画图先跑算法再画）")
            if (is_gradable and t == "code"
                    and not (q.get("language") and (q.get("expected_behavior") or q.get("tests")))):
                warn(f"{tag} code 题建议提供 language 与 expected_behavior/tests")
            if is_gradable and t == "true_false":
                a = q.get("answer")
                if a is not None and not (isinstance(a, bool) or str(a).strip().lower() in TRUE_FALSE_OK):
                    # a present-but-non-boolean answer has no usable gold -> error (missing answer stays a warning)
                    err(f"{tag} true_false 的 answer 必须是布尔型（true/false/真/假/对/错），当前 {a!r}")

            # ---- asset-aware fields (P0A): fail-closed on diagram/figure/table-dependent items ----
            ra_raw = q.get("requires_assets")
            if ra_raw is not None and not isinstance(ra_raw, bool):
                err(f"{tag} requires_assets 必须是布尔型 true/false（不能是字符串/数字），当前 {ra_raw!r}")
            maybe_raw = q.get("maybe_requires_assets")
            if maybe_raw is not None and not isinstance(maybe_raw, bool):
                err(f"{tag} maybe_requires_assets 必须是布尔型 true/false（不能是字符串/数字），当前 {maybe_raw!r}")
            requires = ra_raw is True  # only a real boolean True triggers fail-closed; "false"/0 等不算
            maybe_requires = maybe_raw is True
            visual_required = requires or maybe_requires
            visual_gate_label = "requires_assets=true" if requires else "maybe_requires_assets=true"
            for pf in ("source_pages", "answer_source_pages"):
                pv = q.get(pf)
                if pv is not None and not (isinstance(pv, list) and pv and all(
                        isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in pv)):
                    err(f"{tag} {pf} 必须是非空的正整数列表（页码，从 1 起），当前 {pv!r}")
            assets = q.get("assets")
            asset_ok = 0       # safe + existing assets
            q_side_ok = 0      # of those, ones whose role is shown BEFORE asking (question-side)
            if assets is not None and not isinstance(assets, list):
                err(f"{tag} assets 必须是数组")
                assets = []
            for ai, a in enumerate(assets or []):
                if not isinstance(a, dict):
                    err(f"{tag} assets[{ai}] 必须是对象（含 path/role/type/caption）")
                    continue
                role, atype, apath = a.get("role"), a.get("type"), a.get("path")
                if not isinstance(role, str) or role not in ASSET_ROLES:
                    err(f"{tag} assets[{ai}] role 非法: {role!r}（应为 {sorted(ASSET_ROLES)} 中的字符串）")
                if atype is not None and (not isinstance(atype, str) or atype not in ASSET_TYPES):
                    err(f"{tag} assets[{ai}] type 非法: {atype!r}（应为 {sorted(ASSET_TYPES)} 中的字符串）")
                asset_source_file = a.get("source_file")
                if asset_source_file is not None:
                    if not isinstance(asset_source_file, str) or not asset_source_file.strip():
                        err(f"{tag} assets[{ai}] source_file 必须是非空字符串")
                    elif _unsafe_ref(asset_source_file):
                        err(
                            f"{tag} assets[{ai}] source_file 路径不安全"
                            f"（{_unsafe_ref(asset_source_file)}）: {asset_source_file!r}"
                        )
                full, unsafe = _asset_safety(ws, apath)
                readable = full and os.path.isfile(full) and os.access(full, os.R_OK)
                if unsafe:
                    err(f"{tag} assets[{ai}] 不安全的 path: {unsafe}（{apath!r}）")
                elif not readable:
                    if visual_required:
                        err(f"{tag} assets[{ai}] 必需资源文件不存在或不可读: {apath}"
                            f"（{visual_gate_label} 须存在且可读）")
                    else:
                        warn(f"{tag} assets[{ai}] 资源文件不存在或不可读: {apath}（建议补齐 references/assets/ 下的文件）")
                else:
                    raster_error = _raster_file_validation_error(
                        full, apath, a.get("media_type") or a.get("mime_type")
                    )
                    if raster_error:
                        err(
                            f"{tag} assets[{ai}] 光栅图像损坏或与扩展名不符: "
                            f"{apath}（{raster_error}）"
                        )
                        continue
                    asset_ok += 1
                    expected_asset_hash = a.get("sha256")
                    if expected_asset_hash is not None:
                        if not (isinstance(expected_asset_hash, str)
                                and re.fullmatch(r"[0-9a-f]{64}", expected_asset_hash)):
                            err(f"{tag} assets[{ai}] sha256 非法")
                            continue
                        if _sha256_file(full) != expected_asset_hash:
                            err(f"{tag} assets[{ai}] 内容哈希与题库记录不一致: {apath}")
                            continue
                    expected_source_hash = a.get("source_sha256")
                    if expected_source_hash is not None:
                        source_field, source_file = _asset_source_binding(
                            a, role, q, ai
                        )
                        if (not isinstance(expected_source_hash, str)
                                or not re.fullmatch(r"[0-9a-f]{64}", expected_source_hash)
                                or ingestion_source_hashes.get(source_file) != expected_source_hash):
                            err(
                                f"{tag} assets[{ai}] source_sha256 与当前 {source_field} 不一致"
                            )
                            continue
                    if isinstance(role, str) and role in QUESTION_SIDE_ROLES:
                        q_side_ok += 1
            if visual_required and not (isinstance(assets, list) and assets):
                err(f"{tag} {visual_gate_label} 但缺 assets——依赖图/表/Venn 的题没有上下文，"
                    "测验须 fail-closed（不可在不显示该图的情况下出此题）")
            elif visual_required and asset_ok == 0:
                err(f"{tag} {visual_gate_label} 但没有任何有效（安全且存在）的 asset，须 fail-closed")
            elif visual_required and q_side_ok == 0:
                err(f"{tag} {visual_gate_label} 但没有『题面侧』有效 asset（role 须含 "
                    f"{sorted(QUESTION_SIDE_ROLES)} 之一）——只有非题面 asset（answer_context/worked_solution/student_attempt）"
                    "无法在出题前展示题面，测验须 fail-closed")
            # source_file / answer_source_file, when present, must be a non-empty string (not obj/list/blank)
            for sf in ("source_file", "answer_source_file"):
                sv = q.get(sf)
                if sv is not None and not (isinstance(sv, str) and sv.strip()):
                    err(f"{tag} {sf} 必须是非空字符串（原始文件名），当前 {sv!r}")
                elif isinstance(sv, str) and _unsafe_ref(sv):
                    err(f"{tag} {sf} 路径不安全（{_unsafe_ref(sv)}）: {sv!r}——provenance 文件名不得绝对/穿越/URL")
            qts = q.get("question_text_status")
            if qts is not None and (not isinstance(qts, str) or qts not in QUESTION_TEXT_STATUS):
                err(f"{tag} question_text_status 非法: {qts!r}（应为 {sorted(QUESTION_TEXT_STATUS)} 中的字符串）")
            sfile = q.get("source_file")
            has_src_ref = isinstance(sfile, str) and sfile.strip() and q.get("source_pages")
            if qts == "stub" and not (has_src_ref or q_side_ok):
                err(f"{tag} question_text_status=stub 必须有 source_file+source_pages 或一个『题面侧』有效 asset"
                    "（光给 source_pages 而无 source_file 指不到哪个文件；答案侧 asset 不能在出题前展示；"
                    "仅声明但缺失/不安全的 asset 也不算；否则题面无法独立成题）")
            if qts == "page_reference" and not has_src_ref:
                err(f"{tag} question_text_status=page_reference 必须有非空字符串 source_file + source_pages（指向原始页）")
            if _looks_like_truncated_full_lecture_prompt(q):
                err(
                    f"{tag} question_text_status=full 但题面以未完成的引用/连接词结尾，"
                    "疑似在行首 Example/Quiz 交叉引用处被截断；不得与无题面 asset 的 "
                    "requires_assets=false 状态一起静默通过，请重建入库或逐项审核"
                )

            # provenance + answer presence
            src = q.get("source")
            if src is not None and not isinstance(src, str):
                err(f"{tag} 的 source 必须是字符串，当前为 {type(src).__name__}")
                src = None
            if src is not None and src not in ALL_SOURCES:
                err(f"{tag} 的 source 取值非法: {src!r}（应为 {sorted(ALL_SOURCES)}）")
            if bool(q.get("ai_generated")) and src not in {"ai_generated", "mixed"}:
                err(f"{tag} 为 AI 生成答案，但 source 未标注为 ai_generated/mixed——"
                    "严禁把 AI 生成答案伪装成老师提供或隐藏来源")
            answer_val = q.get("answer")
            has_answer = (answer_val not in (None, "", [], {})
                          and not (isinstance(answer_val, str) and not answer_val.strip()))
            status = str(q.get("answer_status", "")).strip().lower()
            if is_gradable and not has_answer:
                if status == "unknown" or src in {"ai_generated", "unknown"}:
                    warn(f"{tag} 无 answer，已按 unknown/ai_generated 标注（考前需补全/核对）")
                else:
                    # ingest.py ACCEPTS answer-less questions (it warns, doesn't fail) and writes neither
                    # answer_status nor source — so a valid ingest output must NOT fail Tier 1. Keep this a
                    # WARNING (the "AI answer hidden as teacher" case above stays a hard error).
                    warn(f"{tag} 无 answer（建议补 answer，或标 answer_status=unknown / source=ai_generated）")
            elif is_gradable and src is None:
                warn(f"{tag} 有答案但未标 source（建议标 teacher/material/ai_generated）")

            # ---- A2: source taxonomy + tag schema（可选字段，老题库不带照常有效）----
            st = q.get("source_type")
            if st is not None and (not isinstance(st, str) or st not in SOURCE_TYPES):
                err(f"{tag} source_type 非法: {st!r}（应为 {sorted(SOURCE_TYPES)} 中的字符串）")
            kps = q.get("knowledge_points")
            if kps is not None and not (isinstance(kps, list) and kps
                                        and all(isinstance(k, str) and k.strip() for k in kps)):
                err(f"{tag} knowledge_points 必须是非空字符串数组，当前 {kps!r}")
            diff = q.get("difficulty")
            if diff is not None and (isinstance(diff, bool) or not isinstance(diff, int)
                                     or not 1 <= diff <= 5):
                err(f"{tag} difficulty 必须是 1–5 的整数，当前 {diff!r}")
            dr = q.get("difficulty_reason")
            if dr is not None and (not isinstance(dr, str) or not dr.strip()):
                err(f"{tag} difficulty_reason 必须是非空字符串，当前 {dr!r}")
            elif dr is not None and diff is None:
                warn(f"{tag} 有 difficulty_reason 但无 difficulty（建议补 1–5 评分）")
        stats["quiz_types"] = type_counts

    # ---- v4.1 teaching-example reachability (optional, backward compatible) ----
    # This is a PARALLEL teaching snapshot, not another assessment bank.  IDs may therefore
    # deliberately overlap quiz_bank.  The retained baseline requires a current, same-chapter
    # teaching snapshot; quiz overlap is diagnostic and never substitutes for the teaching layer.
    teaching_path = os.path.join(ws, "references", "teaching_examples.json")
    teaching_items, teaching_ids = [], set()
    teaching_scope_by_id = {}
    teaching_exists = not lightweight_workspace and os.path.exists(teaching_path)
    if teaching_exists:
        teaching_link = _is_symlink(teaching_path)
        teaching_real = os.path.realpath(teaching_path)
        teaching_escapes = (os.path.isfile(teaching_path)
                            and teaching_real != ws_real
                            and not teaching_real.startswith(ws_real + os.sep))
        if teaching_link or teaching_escapes:
            err("references/teaching_examples.json 经符号链接逃出工作区")
        elif not os.path.isfile(teaching_path):
            err("references/teaching_examples.json 必须是普通文件")
        else:
            try:
                teaching_items = _strict_json.loads(_read(teaching_path))
            except (ValueError, OSError, UnicodeDecodeError) as e:
                err(f"teaching_examples.json 不是合法 JSON: {e}", level="fatal")
                teaching_items = None
            if teaching_items is not None and not isinstance(teaching_items, list):
                err("teaching_examples.json 顶层必须是 JSON 数组", level="fatal")
                teaching_items = None

    if isinstance(teaching_items, list):
        stats["teaching_examples"] = len(teaching_items)
        role_counts = {"paired_problem": 0, "worked_example": 0}
        for i, ex in enumerate(teaching_items):
            if not isinstance(ex, dict):
                err(f"教学例题[{i}] 必须是对象")
                continue
            ex_id = ex.get("id")
            tag = f"教学例题[{ex_id if ex_id is not None else i}]"
            unique_teaching_id = False
            teaching_id_problem = stable_item_id_problem(ex_id)
            if teaching_id_problem:
                err(
                    f"{tag} id violates the stable notebook/Guide contract: "
                    f"{teaching_id_problem}"
                )
            elif ex_id in teaching_ids:
                err(f"重复的教学例题 id: {ex_id}")
            else:
                teaching_ids.add(ex_id)
                unique_teaching_id = True
            role = ex.get("teaching_role")
            if role not in role_counts:
                err(f"{tag} teaching_role 非法: {role!r}（应为 paired_problem/worked_example）")
            else:
                role_counts[role] += 1
            teaching_gradable = ex.get("gradable")
            if teaching_gradable is not None and not isinstance(teaching_gradable, bool):
                err(f"{tag} gradable 必须是布尔型 true/false，当前 {teaching_gradable!r}")
            chapter_scope = (
                _scope_number(ex.get("chapter"))
                if ex.get("chapter") not in (None, "")
                else None
            )
            phase_scope = (
                _scope_number(ex.get("phase"))
                if ex.get("phase") not in (None, "")
                else None
            )
            if ex.get("chapter") not in (None, "") and chapter_scope is None:
                err(f"{tag} chapter 无法规范化为正整数章节")
            if ex.get("phase") not in (None, "") and phase_scope is None:
                err(f"{tag} phase 无法规范化为正整数章节")
            if ex.get("chapter") in (None, "") and ex.get("phase") in (None, ""):
                err(f"{tag} 缺少 chapter 或 phase（无法按当前章惰性列举）")
            elif chapter_scope is None and phase_scope is None:
                err(f"{tag} chapter/phase 无法规范化为正整数章节")
            elif (
                chapter_scope is not None
                and phase_scope is not None
                and chapter_scope != phase_scope
            ):
                err(
                    f"{tag} chapter={ex.get('chapter')!r} 与 "
                    f"phase={ex.get('phase')!r} 冲突"
                )
            if unique_teaching_id:
                teaching_scope_by_id[ex_id] = chapter_scope or phase_scope
            question = ex.get("question")
            if not isinstance(question, str) or not question.strip():
                err(f"{tag} 缺少非空教学内容 question")
            if _looks_like_truncated_full_lecture_prompt(ex):
                err(
                    f"{tag} question_text_status=full 但题面以未完成的引用/连接词结尾，"
                    "疑似在行首 Example/Quiz 交叉引用处被截断；不得与无题面 asset 的 "
                    "requires_assets=false 状态一起静默通过，请重建入库或逐项审核"
                )
            source_file = ex.get("source_file")
            if not isinstance(source_file, str) or not source_file.strip():
                err(f"{tag} 缺少非空字符串 source_file")
            elif _unsafe_ref(source_file):
                err(f"{tag} source_file 路径不安全（{_unsafe_ref(source_file)}）: {source_file!r}")
            answer_source_file = ex.get("answer_source_file")
            if answer_source_file is not None:
                if not isinstance(answer_source_file, str) or not answer_source_file.strip():
                    err(f"{tag} answer_source_file 必须是非空字符串")
                elif _unsafe_ref(answer_source_file):
                    err(f"{tag} answer_source_file 路径不安全（{_unsafe_ref(answer_source_file)}）: "
                        f"{answer_source_file!r}")
            for pf in ("source_pages", "answer_source_pages"):
                pages = ex.get(pf)
                if pf == "answer_source_pages" and pages is None:
                    continue
                if not (isinstance(pages, list) and pages and all(
                        isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in pages)):
                    err(f"{tag} {pf} 必须是非空正整数页码数组")

            teaching_requires_raw = ex.get("requires_assets")
            teaching_maybe_raw = ex.get("maybe_requires_assets")
            if teaching_requires_raw is not None and not isinstance(teaching_requires_raw, bool):
                err(f"{tag} requires_assets 必须是布尔型 true/false，当前 {teaching_requires_raw!r}")
            if teaching_maybe_raw is not None and not isinstance(teaching_maybe_raw, bool):
                err(f"{tag} maybe_requires_assets 必须是布尔型 true/false，当前 {teaching_maybe_raw!r}")
            requires = teaching_requires_raw is True or teaching_maybe_raw is True
            assets = ex.get("assets")
            valid_assets = 0
            valid_prompt_assets = 0
            if assets is not None and not isinstance(assets, list):
                err(f"{tag} assets 必须是数组")
                assets = []
            for ai, asset in enumerate(assets or []):
                if not isinstance(asset, dict):
                    err(f"{tag} assets[{ai}] 必须是对象")
                    continue
                role, atype = asset.get("role"), asset.get("type")
                if not isinstance(role, str) or role not in ASSET_ROLES:
                    err(f"{tag} assets[{ai}] role 非法: {role!r}")
                if atype is not None and (not isinstance(atype, str) or atype not in ASSET_TYPES):
                    err(f"{tag} assets[{ai}] type 非法: {atype!r}")
                full, unsafe = _asset_safety(ws, asset.get("path"))
                readable = full and os.path.isfile(full) and os.access(full, os.R_OK)
                if unsafe:
                    err(f"{tag} assets[{ai}] 不安全的 path: {unsafe}")
                elif readable:
                    asset_path = asset.get("path")
                    raster_error = _raster_file_validation_error(
                        full,
                        asset_path,
                        asset.get("media_type") or asset.get("mime_type"),
                    )
                    if raster_error:
                        err(
                            f"{tag} assets[{ai}] 光栅图像损坏或与扩展名不符: "
                            f"{asset_path}（{raster_error}）"
                        )
                    else:
                        valid_assets += 1
                        if isinstance(role, str) and role in QUESTION_SIDE_ROLES:
                            valid_prompt_assets += 1
                elif requires:
                    err(f"{tag} 必需教学资源文件不存在或不可读: {asset.get('path')}")
                else:
                    warn(f"{tag} 教学资源文件不存在或不可读: {asset.get('path')}")
            if requires and not assets:
                err(f"{tag} requires_assets/maybe_requires_assets=true 但缺 assets")
            elif requires and valid_assets == 0:
                err(f"{tag} requires_assets/maybe_requires_assets=true 但无有效 asset")
            elif requires and valid_prompt_assets == 0:
                err(f"{tag} requires_assets/maybe_requires_assets=true 但无题面侧有效 asset（role 须含 "
                    f"{sorted(QUESTION_SIDE_ROLES)} 之一）；只有 answer_context/worked_solution/student_attempt 不能先展示题面")
        stats["teaching_example_roles"] = role_counts

    # Student submissions are audit evidence, never prompt/answer evidence.  Taint is
    # workspace-global and physical-path based: a later official-looking declaration must not
    # launder a path that any quiz, teaching row, or ContentUnit identifies as student work.
    # Run this after both human-facing arrays have been loaded so file order cannot affect the
    # verdict, and include every ContentUnit (including foreign chapters).
    quiz_policy_rows = data if has_qb and isinstance(data, list) else []
    teaching_policy_rows = teaching_items if isinstance(teaching_items, list) else []
    try:
        # Re-read the live files at this security boundary.  The arrays above are useful for
        # schema diagnostics, but accepting them as a policy cache would let a public caller (or
        # a stale validator-local slice) omit another layer's student-attempt declaration.
        asset_policy = (
            {"tainted_keys": set(), "conflicts": [], "unsafe_paths": []}
            if lightweight_workspace else workspace_asset_policy_snapshot(ws)
        )
    except ValueError as exc:
        # The underlying schema/path checks above already classify malformed JSON and unsafe
        # entries.  The policy layer must still block, but it should not upgrade an ordinary
        # missing/symlinked quiz-bank validation error to the CLI's malformed-input exit code.
        err("student-attempt asset policy 无法建立完整快照: %s" % exc)
        asset_policy = {"tainted_keys": set(), "conflicts": [], "unsafe_paths": []}
    stats["student_attempt_tainted_assets"] = len(asset_policy["tainted_keys"])
    for problem in asset_policy["conflicts"]:
        err("student-attempt asset policy conflict: %s" % problem)
    for problem in asset_policy["unsafe_paths"]:
        err("student-attempt asset policy path error: %s" % problem)

    quiz_items_for_overlap = quiz_policy_rows
    quiz_ids = {q.get("id") for q in quiz_items_for_overlap
                if isinstance(q, dict)
                and q.get("gradable") is not False
                and isinstance(q.get("id"), (str, int, float, bool))}
    reachable_quiz_ids = {q.get("id") for q in quiz_items_for_overlap
                          if isinstance(q, dict)
                          and isinstance(q.get("id"), (str, int, float, bool))}
    stats["teaching_quiz_overlap"] = len(teaching_ids & quiz_ids)

    ingest_report_path = os.path.join(ws, "ingest_report.json")
    teaching_baseline_path = os.path.join(ws, "references", "teaching_baseline.json")
    expected_teaching_ids = set()
    expected_teaching_by_chapter = {}
    baseline_source = None
    baseline_name = None
    if not lightweight_workspace and os.path.lexists(teaching_baseline_path):
        if _is_symlink(teaching_baseline_path) or not os.path.isfile(teaching_baseline_path):
            err("references/teaching_baseline.json 必须是安全的普通文件")
        else:
            baseline_source = teaching_baseline_path
            baseline_name = "references/teaching_baseline.json"
    elif (not lightweight_workspace and os.path.isfile(ingest_report_path)
          and not _is_symlink(ingest_report_path)):
        baseline_source = ingest_report_path
        baseline_name = "ingest_report.json"
    if baseline_source:
        try:
            ingest_report = _strict_json.loads(_read(baseline_source))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            err(f"{baseline_name} 无法读取，不能核对教学例题保留性: {e}")
            ingest_report = None
        if (baseline_name == "references/teaching_baseline.json"
                and isinstance(ingest_report, dict)
                and ingest_report.get("schema_version") != 1):
            err("references/teaching_baseline.json schema_version 必须为 1")
        if (baseline_name == "references/teaching_baseline.json"
                and isinstance(ingest_report, dict)
                and ingest_report.get("policy") != "append_only"):
            err(
                "references/teaching_baseline.json policy 必须精确为 append_only"
            )
        raw_expected = ingest_report.get("teaching_example_ids") if isinstance(ingest_report, dict) else None
        if raw_expected is not None:
            if not (isinstance(raw_expected, list)
                    and all(stable_item_id_problem(x) is None for x in raw_expected)
                    and len(raw_expected) == len(set(raw_expected))):
                err(f"{baseline_name} 的 teaching_example_ids 不是非空字符串数组，不能核对保留性")
            else:
                expected_teaching_ids = set(raw_expected)
        elif baseline_name == "references/teaching_baseline.json":
            err("references/teaching_baseline.json 缺少 teaching_example_ids")
        raw_by_chapter = (ingest_report.get("teaching_example_ids_by_chapter")
                          if isinstance(ingest_report, dict) else None)
        if raw_by_chapter is not None:
            if not isinstance(raw_by_chapter, dict):
                err(f"{baseline_name} 的 teaching_example_ids_by_chapter 不是对象，不能逐章核对保留性")
            else:
                mapped = set()
                valid_map = True
                for raw_chapter, raw_ids in raw_by_chapter.items():
                    chapter = _scope_number(raw_chapter)
                    if chapter is None or not (isinstance(raw_ids, list)
                                               and all(stable_item_id_problem(x) is None
                                                       for x in raw_ids)):
                        err(f"{baseline_name} teaching_example_ids_by_chapter[{raw_chapter!r}] "
                             "必须是可解析章节对应的字符串数组")
                        valid_map = False
                        continue
                    values = {x.strip() for x in raw_ids}
                    if len(values) != len(raw_ids):
                        err(f"{baseline_name} teaching_example_ids_by_chapter[{raw_chapter!r}] 含重复 ID")
                        valid_map = False
                    if chapter in expected_teaching_by_chapter:
                        err(
                            f"{baseline_name} teaching_example_ids_by_chapter contains "
                            f"duplicate canonical chapter {chapter}"
                        )
                        valid_map = False
                        continue
                    expected_teaching_by_chapter[chapter] = values
                    mapped.update(values)
                if valid_map and expected_teaching_ids and mapped != expected_teaching_ids:
                    err(f"{baseline_name} 的逐章教学例题 ID 与 teaching_example_ids 全集不一致")
        elif baseline_name == "references/teaching_baseline.json":
            err("references/teaching_baseline.json 缺少 teaching_example_ids_by_chapter")
    # The retained baseline is a teaching-roster invariant.  A quiz row may
    # overlap the same stable ID, but it is not a substitute for the parallel
    # teaching snapshot used by tutoring, completion, and Study Guide flows.
    missing_from_teaching = expected_teaching_ids - teaching_ids
    quiz_only_baseline = missing_from_teaching & reachable_quiz_ids
    stats["teaching_examples_expected"] = len(expected_teaching_ids)
    stats["teaching_example_baseline_chapters"] = len(expected_teaching_by_chapter)
    stats["teaching_examples_retained"] = len(expected_teaching_ids & teaching_ids)
    stats["teaching_examples_missing_from_teaching"] = len(missing_from_teaching)
    stats["teaching_examples_quiz_only"] = len(quiz_only_baseline)
    if missing_from_teaching:
        suffix = (
            "（其中仅在 quiz_bank.json 可见: %s）" % "、".join(sorted(quiz_only_baseline))
            if quiz_only_baseline else ""
        )
        err("教学例题保留基线缺少 teaching_examples.json 快照: %s%s——"
            "quiz 条目不能替代教学 roster，必须恢复教学快照或重新导入"
            % ("、".join(sorted(missing_from_teaching)), suffix))
    for chapter, baseline_ids in sorted(expected_teaching_by_chapter.items()):
        wrong_scope = sorted(
            ident for ident in baseline_ids
            if ident in teaching_scope_by_id
            and teaching_scope_by_id.get(ident) != chapter
        )
        if wrong_scope:
            err(
                "%s chapter %s baseline IDs no longer have same-chapter current "
                "teaching snapshots: %s"
                % (baseline_name, chapter, ", ".join(wrong_scope))
            )

    # ---- study_progress consistency (best-effort, lenient → warnings only) ----
    prog_path = os.path.join(ws, "study_progress.md")
    if os.path.isfile(prog_path):
        try:
            prog = _read(prog_path)
            if "疑难点" not in prog and "confusion" not in prog.lower():
                warn("study_progress.md 未见「概念疑难点记录」区（confusion-tracker 应维护此区）")
            # current checkpoint phase should correspond to a phase listed in study_plan.md, else the
            # agent can't resume correctly. Best-effort + lenient (skip silently if unparseable).
            plan_path = os.path.join(ws, "study_plan.md")
            m_cur = re.search(r"当前[^#]*?阶段\s*(\d+)", prog, re.S)
            if m_cur and os.path.isfile(plan_path):
                plan_phases = _plan_phase_nums(_read(plan_path))
                if plan_phases and m_cur.group(1) not in plan_phases:
                    warn(f"study_progress.md 当前阶段 {m_cur.group(1)} 不在 study_plan.md 的阶段列表 "
                         f"{sorted(int(x) for x in plan_phases)} 中（断点可能无法正确恢复）")
        except OSError:
            pass

    # ---- v4-P5: cheatsheet 溯源 lint（PLAN §2.4：小抄每个要点须携带可解析锚点，坏锚即红）----
    cheat_path = os.path.join(ws, "cheatsheet.md")
    if not lightweight_workspace and os.path.isfile(cheat_path):
        try:
            cheat = _read(cheat_path)
        except OSError:
            cheat = None
            err("cheatsheet.md 存在但无法读取")
        if cheat is not None:
            _SRC_LINK = re.compile(r"\]\(((?:notebook|mistakes)/[^)#\s]+|references/wiki/[^)#\s]+)(#[^)\s]*)?\)")
            fence, n_bullets, bad = None, 0, []
            anchor_cache = {}    # rel → set(锚点) | None(目标不可读)；同文件多链接只读一次
            for i, ln in enumerate(cheat.splitlines(), 1):
                fence, marker = _notebook._fence_step(fence, ln)   # 字符+长度（Codex r5）
                if marker:
                    continue
                if fence is not None or not re.match(r"^- \S", ln):
                    continue   # 只查顶层要点 bullet；围栏内示例/缩进子弹/标题不计
                n_bullets += 1
                links = _SRC_LINK.findall(ln)
                if not links:
                    bad.append(f"L{i} 无溯源链接: {ln[:60]}")
                    continue
                for rel, anchor in links:
                    # 溯源链接不得穿越（Codex r3）：notebook/../../外部文件 会解析到工作区外，
                    # 「存在且有锚」也不算合法溯源——段级拒 ..，再 realpath 包含性双保险
                    segs = [s for s in rel.split("/") if s not in ("", ".")]
                    if ".." in segs:
                        bad.append(f"L{i} 溯源链接含 .. 穿越: {rel}")
                        continue
                    target = os.path.join(ws, *segs)
                    ws_real = os.path.normcase(os.path.realpath(ws))
                    t_real = os.path.normcase(os.path.realpath(target))
                    if t_real != ws_real and not t_real.startswith(ws_real + os.sep):
                        bad.append(f"L{i} 溯源链接逃出工作区: {rel}")
                        continue
                    if not os.path.isfile(target):
                        bad.append(f"L{i} 链接目标不存在: {rel}")
                        continue
                    # notebook/mistakes 目标带 #锚点时必须真实存在于目标文件——只查文件存在
                    # 会放行 #typo 这种点开跳不到条目的死锚。references/wiki 目标保持文件级
                    # 校验（章节文件没有保证的标题结构）。
                    frag = unquote(anchor[1:]) if anchor else ""
                    if frag and rel.split("/", 1)[0] in ("notebook", "mistakes"):
                        if rel not in anchor_cache:
                            try:
                                anchor_cache[rel] = _md_anchors(target)
                            except (OSError, UnicodeDecodeError):
                                anchor_cache[rel] = None
                        aset = anchor_cache[rel]
                        if aset is None:
                            bad.append(f"L{i} 链接目标无法读取，锚点无法校验: {rel}")
                        elif frag not in aset:
                            bad.append(f"L{i} 坏锚点: {rel}{anchor}（目标文件里没有对应的标题锚，"
                                       "点开跳不到条目）")
            for b in bad:
                err("cheatsheet.md " + b + "（编译产物每个要点必须可溯源到 notebook/mistakes/wiki——"
                    "详见 PLAN 溯源契约）")
            stats["cheatsheet_bullets"] = n_bullets
    elif (not lightweight_workspace
          and os.path.isfile(os.path.join(ws, "walkthrough.md"))):
        warn("检测到旧版 walkthrough.md 且无 cheatsheet.md——v4 小抄改为带溯源的编译产物"
             "（exam-cheatsheet 重新编译即可，旧文件保留不删）")

    # ---- A4: structured state (study_state.json = source of truth when present) ----
    state_path = os.path.join(ws, "study_state.json")
    # 悬空符号链接 isfile 为 False——不先查 islink 会整段跳过校验、放行一个更新器拒跑的工作区
    if _is_symlink(state_path) or (os.path.isfile(state_path)
                                   and not os.path.realpath(state_path).startswith(
                                       os.path.realpath(ws) + os.sep)):
        err("study_state.json 是符号链接/经符号链接逃出工作区（技能会读/写这个事实源）")
    elif os.path.lexists(state_path) and not os.path.isfile(state_path):
        err("study_state.json 存在但不是常规文件（目录/特殊文件）——官方更新器无法持久化 state")
    elif os.path.isfile(state_path):
        try:
            st = _strict_json.loads(_read(state_path))
        except OSError as e:
            # isfile 通过后仍可能读失败（权限变更/竞态删除）——Tier-1 要给结构化报错，不许崩栈
            err(f"study_state.json 存在但无法读取（{e}）——请检查文件权限")
            st = None
        except UnicodeDecodeError:
            err("study_state.json 不是 UTF-8——状态文件损坏（应由 update_progress.py 以 UTF-8 原子写入）")
            st = None
        except ValueError as e:
            err(f"study_state.json 不是合法 JSON: {e}")
            st = None
        if isinstance(st, dict):
            cp = st.get("current_phase")
            if not (isinstance(cp, int) and not isinstance(cp, bool) and cp >= 1):
                err(f"study_state.json 的 current_phase 必须是 ≥1 的整数，当前 {cp!r}")
            else:
                # state 是断点事实源——阶段号必须真实存在于 study_plan.md，否则下次会话会恢复进
                # 不存在的阶段/wiki（不能只靠生成视图 md 的那条 warning 兜底）
                plan_path_a4 = os.path.join(ws, "study_plan.md")
                if os.path.isfile(plan_path_a4):
                    try:
                        plan_phases_a4 = _plan_phase_nums(_read(plan_path_a4))
                        if plan_phases_a4 and str(cp) not in plan_phases_a4:
                            err(f"study_state.json 的 current_phase={cp} 不在 study_plan.md 的阶段列表 "
                                f"{sorted(int(x) for x in plan_phases_a4)} 中（事实源指向不存在的阶段，"
                                "断点无法恢复）")
                    except OSError:
                        pass
            for field in ("mistake_archive", "confusion_log", "knowledge_window"):
                v = st.get(field)
                if v is not None and not (isinstance(v, list)
                                          and all(isinstance(x, dict) for x in v)):
                    err(f"study_state.json 的 {field} 必须是对象数组，当前 {type(v).__name__}")
                    continue                          # 标量/坏形态不再往下迭代（1 会 TypeError 崩栈）
                if field == "knowledge_window":
                    continue    # 与更新器同 schema：knowledge_window 只要求对象数组——
                                # 行内元数据不强求 note，validator 不能拒收官方更新器可用的 state
                for x in (v or []):
                    if not isinstance(x, dict):
                        continue
                    if not (isinstance(x.get("note"), str) and x["note"].strip()):
                        err(f"study_state.json 的 {field} 行缺非空 note 字段: {x!r}")
                    # 与 update_progress 的行 schema 对齐——validator 放行的 state 官方更新器必须能用
                    for k in ("id", "status"):
                        if x.get(k) is not None and not isinstance(x[k], str):
                            err(f"study_state.json 的 {field} 行 {k} 必须是字符串或省略: {x!r}")
            pc = st.get("phase_checklist")
            if pc is not None and not (isinstance(pc, list) and all(isinstance(x, dict) for x in pc)):
                err(f"study_state.json 的 phase_checklist 必须是对象数组，当前 {type(pc).__name__}")
            else:
                for x in (pc or []):
                    if not (isinstance(x.get("text"), str) and x["text"].strip()):
                        err(f"study_state.json 的 phase_checklist 行缺非空 text 字段: {x!r}")
                    if x.get("done") is not None and not isinstance(x["done"], bool):
                        err(f"study_state.json 的 phase_checklist 行 done 必须是布尔: {x!r}")
            prefs = st.get("preferences")
            if prefs is None:
                pass
            elif not isinstance(prefs, dict):
                err(f"study_state.json 的 preferences 必须是对象，当前 {type(prefs).__name__}")
            else:
                interaction_style = prefs.get("interaction_style", "batch")
                if interaction_style not in _progress.INTERACTION_STYLES:
                    err("study_state.json 的 preferences.interaction_style 必须是 "
                        f"batch|step_by_step，当前 {interaction_style!r}")
            stats["interaction_style_preference"] = (
                _i18n.workspace_interaction_style_preference(st))
            stats["interaction_style_effective"] = (
                _i18n.workspace_effective_interaction_style(st))
            stats["interaction_style_dormant"] = (
                _i18n.workspace_interaction_style_dormant(st))
            if stats["interaction_style_dormant"]:
                stats["interaction_style_dormant_reason"] = (
                    "processing_mode_lightweight"
                    if _i18n.workspace_processing_mode(st) != "full"
                    else "no_questions"
                )
            processing = st.get("processing_mode")
            if processing is not None and not isinstance(processing, str):
                err("study_state.json processing_mode must be a string; got %s"
                    % type(processing).__name__)
            elif isinstance(processing, str):
                processing_code, _processing_warning = _i18n.canon_processing_mode(
                    processing
                )
                if processing_code not in _i18n.PROCESSING_MODES:
                    warn(
                        "study_state.json processing_mode=%r is non-standard; "
                        "runtime falls back safely to lightweight" % processing
                    )
            stats["processing_mode_effective"] = _i18n.workspace_processing_mode(st)
            artifact = st.get("artifact_mode")
            if artifact is not None and not isinstance(artifact, str):
                err(f"study_state.json 的 artifact_mode 必须是字符串，当前 {type(artifact).__name__}")
            elif isinstance(artifact, str):
                artifact_code, _artifact_warning = _i18n.canon_artifact_mode(artifact)
                if artifact_code not in _i18n.ARTIFACT_MODES:
                    warn(f"study_state.json 的 artifact_mode={artifact!r} 非标准；"
                         "运行时将安全回退为 chat（请用 update_progress.py set --artifact-mode 修正）")
            stats["artifact_mode_preference"] = _i18n.workspace_artifact_mode(st)
            stats["artifact_mode_effective"] = _i18n.workspace_effective_artifact_mode(st)
            stats["artifact_mode_dormant"] = _i18n.workspace_artifact_mode_dormant(st)
            raw_explanation_mode = st.get("answer_explanation_mode")
            explanation_mode, explanation_warning = (
                _i18n.canon_answer_explanation_mode(raw_explanation_mode)
            )
            if explanation_warning:
                warn(
                    "study_state.json answer_explanation_mode=%r is non-standard; "
                    "runtime falls back safely to ordinary"
                    % raw_explanation_mode
                )
            stats["answer_explanation_mode_effective"] = explanation_mode
            # Phase-completion evidence is a learning-progress gate, not an ingestion-readiness
            # gate.  A fresh/chat-mode workspace may intentionally omit visual manifests; only
            # enforce the manifest trio once a phase has evidence/done state or visual output is the
            # standing preference.  Shape validation remains unconditional.
            pe = st.get("phase_evidence")
            checklist_started = any(
                isinstance(row, dict) and row.get("done") is True
                for row in (st.get("phase_checklist") or ())
            )
            evidence_started = isinstance(pe, dict) and bool(pe)
            phase_stale_warnings = []
            if (checklist_started or evidence_started
                    or _i18n.workspace_effective_artifact_mode(st) == "visual"):
                phase_problems = _progress.phase_evidence_errors(
                    ws, st, enforce_manifest_gate=True,
                    recoverable_stale=phase_stale_warnings,
                )
            else:
                phase_problems = _progress._phase_evidence_shape_errors(st)
            for problem in phase_problems:
                err("study_state.json phase_evidence：" + problem)
            for problem in phase_stale_warnings:
                warn(
                    "study_state.json phase_evidence requires manifest-order "
                    "step-by-step re-teaching before Guide/completion reuse: "
                    + problem
                )
            if isinstance(pe, dict):
                stats["phases_covered_unverified"] = sum(
                    1 for x in pe.values() if isinstance(x, dict)
                    and x.get("status") == "covered_unverified")
                stats["phases_verified"] = sum(
                    1 for x in pe.values() if isinstance(x, dict) and x.get("status") == "verified")
            if _i18n.workspace_processing_mode(st) == "lightweight":
                try:
                    import lightweight_session as _lightweight
                    # Routine validation is deliberately metadata-only.  The phase
                    # evidence gate above checks immutable receipt/event identity;
                    # health now checks current taught and active file metadata plus
                    # physical identity without streaming course bytes.
                    health = _lightweight.workspace_health(
                        ws, st, live_current_taught=True, exact_live=False
                    )
                except (ImportError, OSError, TypeError, ValueError) as exc:
                    err("lightweight session validator unavailable: %s" % exc)
                else:
                    for problem in health.get("errors") or []:
                        err("lightweight session：" + str(problem))
                    for problem in health.get("warnings") or []:
                        warn("lightweight session：" + str(problem))
                    if isinstance(health.get("stats"), dict):
                        stats.update(health["stats"])
            # md is a GENERATED view — a phase mismatch means someone hand-patched it（下次渲染会丢）
            prog_path2 = os.path.join(ws, "study_progress.md")
            if isinstance(cp, int) and os.path.isfile(prog_path2):
                try:
                    progress_text = _read(prog_path2)
                    m2 = re.search(r"(?:当前进行阶段|当前阶段)\D*?(\d+)", progress_text)
                    if m2 and int(m2.group(1)) != cp:
                        warn(f"study_progress.md 的阶段（{m2.group(1)}）与 study_state.json（{cp}）不一致——"
                             "md 是生成视图，请用 update_progress.py render 重建，不要手改 md")
                    ma = re.search(r"(?:输出资源模式|Artifact mode)\**\s*[：:]\s*(.+)",
                                   progress_text, re.I)
                    if ma and _i18n.workspace_artifact_mode(ma.group(1).strip()) != \
                            _i18n.workspace_artifact_mode(st):
                        warn("study_progress.md 的输出资源模式与 study_state.json 不一致——"
                             "md 是生成视图，请用 update_progress.py render 重建，不要手改 md")
                except OSError:
                    pass
        elif st is not None:
            err("study_state.json 顶层必须是 JSON 对象")

    return errors, warnings, stats


def _validation_conflict_result(exc):
    return ([{
        "level": "fatal",
        "msg": "workspace validation snapshot is unavailable: %s" % exc,
    }], [], {})


def _validation_gate_result(exc):
    """Return a stable fail-closed result for runtime/workspace publication gates."""

    reason = getattr(exc, "reason", None) or "full_processing_gate_blocked"
    blockers = list(getattr(exc, "blockers", ()) or ())
    return ([{
        "level": "fatal",
        "msg": "workspace validation runtime/full-processing gate is blocked: %s" % exc,
    }], [], {
        "validation_gate": "full_processing_required",
        "reason": reason,
        "blockers": blockers,
    })


def validate(ws):
    """Validate one lock-consistent workspace snapshot."""

    if not os.path.isdir(ws):
        return _validate_unlocked(ws)
    try:
        with workspace_validation_lock(ws):
            return _validate_unlocked(ws)
    except _exam_start.FullProcessingRequired as exc:
        return _validation_gate_result(exc)
    except (ConflictError, OSError, ValueError) as exc:
        # An unsafe workspace root or an unavailable lock must never downgrade
        # into an unlocked read.  Doing so would reintroduce the mixed-snapshot
        # window this wrapper exists to close.
        return _validation_conflict_result(exc)


def _exit_code(errors):
    if any(e.get("level") == "fatal" for e in errors):
        return 2
    return 1 if errors else 0


def _readiness(errors, warnings):
    """Separate structural runnability (`ok`) from content readiness."""
    if errors:
        return "blocked"
    return "usable_with_gaps" if warnings else "ready"


def _details_path(workspace, value):
    if value is None:
        return None
    root = os.path.abspath(workspace)
    candidate = os.path.abspath(os.path.join(root, value))
    try:
        contained = os.path.commonpath((root, candidate)) == root
    except ValueError:
        contained = False
    if not contained:
        raise ValueError("--details-file 必须位于 workspace 内")
    parent = os.path.dirname(candidate)
    if not os.path.isdir(parent):
        raise ValueError("--details-file 父目录不存在：%s" % parent)
    if os.path.lexists(candidate) and os.path.islink(candidate):
        raise ValueError("--details-file 不得是符号链接")
    return candidate


def _atomic_json(path, payload):
    temporary = path + ".tmp"
    if os.path.lexists(temporary) and os.path.islink(temporary):
        raise ValueError("details 临时文件不得是符号链接")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)


def _snapshot_blocked_capabilities(chapter, reason_code):
    chapter = chapter if type(chapter) is int and chapter > 0 else 1
    return {
        "chapter": chapter,
        "workspace_structural": {
            "status": "blocked", "ready": False,
            "reason_codes": [reason_code], "counts": {},
        },
        "teaching_ready": {
            "status": "blocked", "ready": False,
            "reason_codes": [reason_code], "counts": {"chapter": chapter},
        },
        "quiz_ready": {
            "status": "blocked", "ready": False,
            "reason_codes": [reason_code], "counts": {"chapter": chapter},
        },
        "artifact_ready": {
            "status": "blocked", "ready": False,
            "reason_codes": [reason_code], "counts": {"chapter": chapter},
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a cram workspace against docs/file-format.md")
    ap.add_argument("workspace", help="workspace directory")
    ap.add_argument("--json", action="store_true", help="output errors/warnings/stats as JSON")
    ap.add_argument("--chapter", type=int, help="capability readiness chapter; default current phase")
    ap.add_argument(
        "--dependency-snapshot", action="store_true",
        help="bind validation and capability reads to a bounded dependency-tree digest",
    )
    ap.add_argument(
        "--max-items", type=int, default=25,
        help="maximum errors and warnings returned inline (1-200; default 25)",
    )
    ap.add_argument("--full", action="store_true", help="return every error and warning inline")
    ap.add_argument(
        "--details-file",
        help="optional workspace-relative JSON file receiving every error and warning",
    )
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.max_items < 1 or args.max_items > 200:
        ap.error("--max-items 必须在 1 到 200 之间")
    if args.dependency_snapshot and args.details_file:
        ap.error("--dependency-snapshot cannot be combined with --details-file")

    snapshot_workspace = os.path.abspath(args.workspace)

    def locked_validation_generation():
        """Create the dependency receipt around the reads it authenticates.

        The caller holds ``workspace_validation_lock`` for this entire helper
        when the workspace exists.  In particular, neither snapshot endpoint
        may sit outside the lock: otherwise A -> validate(B) -> A can sign a B
        result with an A receipt.
        """

        dependency_snapshot = None
        snapshot_before = None
        snapshot_failure = None
        if args.dependency_snapshot:
            try:
                snapshot_before = _collect_dependency_snapshot(snapshot_workspace)
            except _SnapshotError as exc:
                snapshot_failure = str(exc)

        if snapshot_failure is None:
            errors, warnings, stats = _validate_unlocked(args.workspace)
            capabilities = _readiness_matrix.capability_readiness(
                args.workspace, errors, warnings, stats, chapter=args.chapter
            )
        else:
            errors = [{
                "level": "fatal",
                "msg": "dependency_snapshot_failed before validation: %s"
                       % snapshot_failure,
            }]
            warnings = []
            stats = {}
            capabilities = _snapshot_blocked_capabilities(
                args.chapter, "dependency_snapshot_failed")

        if args.dependency_snapshot and snapshot_failure is None:
            try:
                snapshot_after = _collect_dependency_snapshot(snapshot_workspace)
            except _SnapshotError as exc:
                snapshot_failure = str(exc)
            else:
                before_receipt = _dependency_snapshot_receipt(snapshot_before)
                after_receipt = _dependency_snapshot_receipt(snapshot_after)
                if before_receipt != after_receipt:
                    snapshot_failure = "dependencies changed across validator reads"
                elif (snapshot_before.get("_generation_sha256")
                      != snapshot_after.get("_generation_sha256")):
                    snapshot_failure = (
                        "dependency generation changed across validator reads "
                        "(possible ABA rewrite)"
                    )
                else:
                    dependency_snapshot = after_receipt
            if snapshot_failure is not None:
                errors.append({
                    "level": "fatal",
                    "msg": "dependency_snapshot_drift: %s" % snapshot_failure,
                })
                capabilities = _snapshot_blocked_capabilities(
                    capabilities.get("chapter"), "dependency_snapshot_drift")
        return errors, warnings, stats, capabilities, dependency_snapshot

    try:
        if not os.path.isdir(args.workspace):
            result = locked_validation_generation()
        else:
            with workspace_validation_lock(args.workspace):
                result = locked_validation_generation()
        errors, warnings, stats, capabilities, dependency_snapshot = result
    except _exam_start.FullProcessingRequired as exc:
        errors, warnings, stats = _validation_gate_result(exc)
        capabilities = _snapshot_blocked_capabilities(
            args.chapter, "full_processing_gate_blocked")
        dependency_snapshot = None
    except (ConflictError, OSError, ValueError) as exc:
        errors, warnings, stats = _validation_conflict_result(exc)
        capabilities = _snapshot_blocked_capabilities(
            args.chapter, "workspace_snapshot_unavailable")
        dependency_snapshot = None

    code = _exit_code(errors)
    readiness = _readiness(errors, warnings)
    limit = max(len(errors), len(warnings)) if args.full else args.max_items
    shown_errors = errors[:limit]
    shown_warnings = warnings[:limit]
    try:
        details_path = _details_path(args.workspace, args.details_file)
        if details_path:
            _atomic_json(details_path, {
                "workspace": os.path.abspath(args.workspace),
                "readiness": readiness,
                "capabilities": capabilities,
                "errors": errors,
                "warnings": warnings,
                "stats": stats,
            })
    except (OSError, ValueError) as exc:
        sys.stderr.write("validate_workspace: 无法写入 details：%s\n" % exc)
        return 2

    if args.json:
        document = {
            "exit_code": code, "ok": code == 0, "workspace": args.workspace,
            "readiness": readiness, "capabilities": capabilities,
            "error_count": len(errors), "warning_count": len(warnings),
            "error_summary": _readiness_matrix.summarize_messages(errors),
            "warning_summary": _readiness_matrix.summarize_messages(warnings),
            "truncated": {
                "errors": max(0, len(errors) - len(shown_errors)),
                "warnings": max(0, len(warnings) - len(shown_warnings)),
            },
            "details_file": details_path,
            "errors": shown_errors, "warnings": shown_warnings, "stats": stats,
        }
        if args.dependency_snapshot:
            document["dependency_snapshot"] = dependency_snapshot
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(f"工作区: {args.workspace}")
        if stats:
            print("  统计:", ", ".join(f"{k}={v}" for k, v in stats.items()))
        for e in shown_errors:
            print(f"  [{'致命' if e['level'] == 'fatal' else '错误'}] {e['msg']}")
        for w in shown_warnings:
            print(f"  [告警] {w['msg']}")
        if len(errors) > len(shown_errors) or len(warnings) > len(shown_warnings):
            print("  [摘要] 已限制内联输出；另有错误 %d 条、告警 %d 条。使用 --full 或 --details-file 查看。" % (
                len(errors) - len(shown_errors), len(warnings) - len(shown_warnings)))
        print("  能力:", ", ".join(
            "%s=%s" % (name, value["status"])
            for name, value in capabilities.items() if name != "chapter"))
        verdict = {
            "ready": "✓ ready（可运行，且静态检查无告警）",
            "usable_with_gaps": "△ usable_with_gaps（可运行，但仍有告警/完整性缺口）",
            "blocked": "✗ blocked（存在校验错误）",
        }[readiness]
        print(f"结论: {verdict}（错误 {sum(1 for e in errors)} / 告警 {len(warnings)}）")
    return code


if __name__ == "__main__":
    sys.exit(main())
