#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured progress state (A4) — study_state.json is the SINGLE SOURCE OF TRUTH;
study_progress.md becomes a GENERATED human-readable view.

Why: hand-patching study_progress.md caused mojibake (GBK writes), patch-mismatch drift and
silently-lost rows in real sessions (EEC160 report #7). All mutations now go through this official
tool: it writes study_state.json (explicit UTF-8, atomic temp+rename) and re-renders the Markdown
view from it. A write failure is FAIL-LOUD (non-zero exit + message) — never silently "updated".

    python scripts/update_progress.py --workspace <ws> init                # migrate md → json (once)
    python scripts/update_progress.py --workspace <ws> set --phase 3
    python scripts/update_progress.py --workspace <ws> set --interaction-style step_by_step
    python scripts/update_progress.py --workspace <ws> set --scope homework-only --mode 查缺补漏
    python scripts/update_progress.py --workspace <ws> add-mistake --id hw_hw1_3 --chapter 2 --note "Venn 阴影判断错"
    python scripts/update_progress.py --workspace <ws> add-confusion --chapter 1 --note "循环队列取模"
    python scripts/update_progress.py --workspace <ws> record-phase-evidence --kind wiki --ref references/wiki/ch1.md
    python scripts/update_progress.py --workspace <ws> record-taught-example --id ex1 --notebook-ref notebook/ch01.md#ex1
    python scripts/update_progress.py --workspace <ws> complete-phase --status covered_unverified
    python scripts/update_progress.py --workspace <ws> render               # json → md（修复被手改的 md）
    python scripts/update_progress.py --workspace <ws> show                 # 打印当前状态 JSON
    python scripts/update_progress.py workspace-register --course 数据结构 --path <dir> [--materials <dir>]
    python scripts/update_progress.py workspace-list [--json]               # 全局注册表，不吃 --workspace

Backward compatible: a workspace WITHOUT study_state.json keeps working (no-Python fallback:
hand-written study_progress.md still validates); `init` adopts the existing md losslessly
(phase + mistake/confusion rows, both bullet and ingest-template table forms).
Exit codes: 0 ok · 1 write/render failure · 2 bad input/usage.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import unquote

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

import i18n
import notebook as _notebook
import list_teaching_examples as _teaching_examples
from stable_ids import stable_item_id_problem
from ingestion import workspace_publication_lock
from ingestion.storage import _exclusive_file_lock
try:
    from asset_policy import physical_asset_key
except ImportError:  # package import from repository root
    from scripts.asset_policy import physical_asset_key

STATE_NAME = "study_state.json"
MD_NAME = "study_progress.md"
SCHEMA_VERSION = 1

# v4.1 Step 4: phase completion is evidence-backed in manifest-aware workspaces.  The new
# field is additive within schema v1 so legacy state remains readable without an eager migration.
PHASE_EVIDENCE_STATUSES = ("covered_unverified", "verified")
PHASE_EVIDENCE_FIELDS = ("wiki", "visual", "teaching_examples", "notebook", "checkpoint")
PHASE_EVIDENCE_CORE = ("wiki", "visual", "teaching_examples", "notebook")
CHECKPOINT_OUTCOMES = ("passed", "wrong", "skipped")
PHASE_MANIFESTS = ("references/image_question_index.json",
                   "references/figure_page_index.json",
                   "references/teaching_examples.json")

# ---- v4：词表唯一定义点在 scripts/i18n.py——本文件只留兼容名 ----
# 持久化从此只存语言中性 canonical 代号（from_scratch / le1d / zh / in_window / to_review …）；
# 生成视图仍渲染中文显示词（i18n.display），md 回读经归一化收敛回代号，三代输入全兼容。
LEARNING_MODES = i18n.MODES
TIME_TIERS = i18n.TIERS
LANGUAGES = i18n.LANGS
ARTIFACT_MODES = i18n.ARTIFACT_MODES
INTERACTION_STYLES = ("batch", "step_by_step")
TEACHING_EXAMPLE_BINDING_FIELDS = frozenset((
    "id", "notebook_ref", "notebook_block_sha256", "manifest_item_sha256",
))

_normalize_mode = i18n.canon_mode
_normalize_tier = i18n.canon_tier
_normalize_language = i18n.canon_language
_normalize_artifact_mode = i18n.canon_artifact_mode


def _disp(kind, code):
    """code → 中文显示词（生成视图/回执用；未知值原样透传）。"""
    return i18n.display(kind, code, "zh")


def _die(msg, code=2):
    sys.stderr.write("update_progress: " + msg + "\n")
    raise SystemExit(code)


def _validated_interaction_style(value):
    """Return one canonical tutoring cadence or fail before state is written.

    Interaction cadence deliberately has no display aliases.  Keeping this small
    preference canonical prevents a generic ``--pref`` write from bypassing the
    dedicated CLI's validation.
    """
    if not isinstance(value, str) or value not in INTERACTION_STYLES:
        _die("interaction_style must be exactly one of: %s; got %r"
             % ("|".join(INTERACTION_STYLES), value))
    return value


def default_state():
    return {"version": SCHEMA_VERSION, "current_phase": 1, "scope": None, "mode": None,
            "time_budget": None, "language": None, "artifact_mode": "chat",
            "preferences": {"interaction_style": "batch"},
            "mistake_archive": [], "confusion_log": [], "knowledge_window": [],
            "phase_checklist": [], "phase_evidence": {}, "last_updated": None}


# ---------------- md → state (migration; tolerant of both bullet and table forms) ----------------

_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|?\s*$")
# 表头词表（匹配前整行 lower()）：zh 词来自历史模板；en 词来自 locales/en/templates/ 的
# 进度模板表头（多词短语，避免普通数据行里出现 chapter/status 就被误当表头吞掉）。
# 少了 en 词，`ingest --lang en` 的英文表头行会被当成真实错题行迁进 state（{id:"Mistake ID"}）。
_HDR_WORDS = ("错题id", "关联章节", "题目内容", "错误原因", "序号", "疑难点", "解答要点", "状态",
              "mistake id", "question summary", "error analysis", "trouble spot",
              "answer key points")
# 旧模板的空档占位既有全角括号形（（暂无））也有裸标签形（暂无错题 / 暂无疑难 / N/A）——
# 都不是真实条目；真实笔记只是【包含】这些字样（「暂无法求解」）不受影响（fullmatch 才跳过）
_PLACEHOLDER = re.compile(
    r"[（(]?\s*(?:暂无(?:错题|疑难|疑问|困惑|记录|内容|数据|条目)?|无|清空重来|none|n/?a|empty)\s*[）)]?",
    re.I)


def parse_md(text):
    """Lossless-enough adoption of an existing study_progress.md: phase + mistake/confusion rows."""
    t = text or ""
    pm = re.search(r"(?:当前进行阶段|当前阶段|current\s*phase)\D*?(\d+)", t, re.I)
    phase = int(pm.group(1)) if pm else 1
    # A2 范围/模式偏好也要随迁移带走（生成视图的 范围/模式 行）——丢了 scope 会静默放宽题池
    sm = re.search(r"范围/模式\**\s*：\s*(.*?)\s*｜\s*(.*?)\s*｜\s*时间预算\s*(.*)", t)

    def _unset(v, *defaults):
        v = (v or "").strip()
        return None if (not v or v in defaults) else v
    prefs = {"scope": _unset(sm.group(1), "混合题池") if sm else None,
             "mode": _unset(sm.group(2), "未设定") if sm else None,
             "time_budget": _unset(sm.group(3), "未设定") if sm else None}
    # ⚙️ 偏好区（讲解风格等）也随迁移带走——init --force 恢复路径不能把已存偏好静默丢掉
    preferences = {}
    pref_sec = re.search(r"##[^\n]*偏好[^\n]*\n((?:\s*-[^\n]*\n?)*)", t)
    if pref_sec:
        for pl in pref_sec.group(1).splitlines():
            pmatch = re.match(r"\s*-\s*([^:：]+)[:：]\s*(.+)$", pl)
            if pmatch:
                preferences[pmatch.group(1).strip()] = pmatch.group(2).strip()
    prefs["preferences"] = preferences
    lm = re.search(r"(?:语言偏好|Language preference)\**\s*[：:]\s*(.+)", t, re.I)
    prefs["language"] = lm.group(1).strip() if lm else None
    am = re.search(r"(?:输出资源模式|Artifact mode)\**\s*[：:]\s*(.+)", t, re.I)
    prefs["artifact_mode"] = am.group(1).strip() if am else None
    mistakes, confusions, checklist, window = [], [], [], []
    cur, in_checklist, in_window, tbl_cols, window_cols = None, False, False, None, None
    for ln in t.splitlines():
        h = ln.strip()
        is_heading = bool(re.match(r"^\s{0,3}(#{1,4}\s|\*\*)", ln))
        if is_heading and re.search(r"打卡|checklist|check-?in", h, re.I):
            cur, in_checklist, in_window, tbl_cols = None, True, False, None   # 📊 知识点打卡状态 区
            continue
        if is_heading and re.search(r"错题|mistake", h, re.I):
            cur, in_checklist, in_window, tbl_cols = mistakes, False, False, None
            continue
        # en 模板的疑难区标题是 "Concept trouble-spot log"（不含 confusion）——一并识别
        if is_heading and re.search(r"疑难|困惑|confusion|trouble[ -]?spot", h, re.I):
            cur, in_checklist, in_window, tbl_cols = confusions, False, False, None
            continue
        if is_heading and re.search(r"知识点窗口|窗口|🪟|window", h, re.I):
            # A6：知识点窗口区——init 恢复路径必须把窗口行迁回 state，否则窗口/已实测追踪不可逆丢
            cur, in_checklist, in_window, window_cols = None, False, True, None
            continue
        if is_heading:
            # 非已知区的任何标题（含 **加粗** 形式）都终结当前节——否则「**下一步**」之后的
            # 普通列表会被误并进上一个档案区
            cur, in_checklist, in_window, tbl_cols = None, False, False, None
            continue
        cm = re.match(r"^\s*[-*]\s*\[([ xX])\]\s*(\S.*)$", ln)
        if in_checklist and cm:
            # 每阶段完成/掌握状态必须随迁移进 state——渲染丢掉打卡区就是不可逆丢 per-phase 进度
            checklist.append({"text": cm.group(2).strip(), "done": cm.group(1).lower() == "x"})
            continue
        if in_window:
            # 窗口表：| 知识点 | 关联章节 | 状态 | 备注 |——按表头列角色映射，缺表头退位置映射
            if _TABLE_SEP.match(ln) or not h.startswith("|"):
                continue
            cells = [c.strip(" *`") for c in h.strip("|").split("|")]
            low = h.lower()
            if ("知识点" in low and "状态" in low) or \
                    ("knowledge point" in low and "status" in low):   # 手写 en 窗口表同样按表头映射
                window_cols = []
                for c in cells:
                    cl = c.lower()
                    if "知识点" in c or "knowledge point" in cl:
                        window_cols.append("point")
                    elif "章节" in c or "chapter" in cl:
                        window_cols.append("chapter")
                    elif "状态" in c or "status" in cl:
                        window_cols.append("status")
                    else:
                        window_cols.append("note")
                continue
            if not any(c and c != "-" for c in cells):
                continue
            got, wnotes = {}, []
            for c, role in zip(cells, window_cols or ["point", "chapter", "status", "note"]):
                if not c or c == "-":
                    continue
                if role == "note":
                    wnotes.append(c)
                elif role not in got:
                    got[role] = c
            if got.get("point"):
                # md 里的状态是中文显示词（或手写自由词）——回读时归一化成代号，未知词透传
                window.append({"point": got["point"], "chapter": got.get("chapter"),
                               "status": (i18n.canon_window_status(got["status"])
                                          if got.get("status") else "in_window"),
                               "note": " / ".join(wnotes)})
            continue
        if cur is None:
            continue
        # 疑难走 to_revisit→revisited 契约，错题走 to_review→corrected/reviewed（存代号，视图渲染中文）
        default_status = "to_revisit" if cur is confusions else "to_review"
        if re.match(r"^\s*[-*]\s+\S", ln):
            body = re.sub(r"^\s*[-*]\s+", "", h).strip()
            # 只有整条就是占位符才跳过——真实笔记里出现「（暂无）」字样（如问空集的题）不能被丢
            if _PLACEHOLDER.fullmatch(body):
                continue
            ids = re.findall(r"\[#([^\]\s]+)\]", h)
            cur.append({"id": ids[0] if ids else None, "chapter": None,
                        "note": body, "status": default_status})
        elif h.startswith("|") and not _TABLE_SEP.match(ln):
            low = h.lower()
            if sum(1 for w in _HDR_WORDS if w in low) >= 2:
                # 表头列名建立列角色映射——短表（| 序号 | 疑难点 | 状态 |）没有章节列，
                # 纯位置映射会把疑难点当章节、状态当 note，迁移后学生的记录被吞
                tbl_cols = []
                for c in (c0.strip(" *`") for c0 in h.strip("|").split("|")):
                    cl = c.lower()
                    if "章节" in c or "chapter" in cl:
                        tbl_cols.append("chapter")
                    elif "状态" in c or "status" in cl:
                        tbl_cols.append("status")
                    elif "id" in cl or "序号" in c or cl.rstrip(".") == "no":
                        tbl_cols.append("id")           # en 疑难表的 "No." 序号列
                    else:
                        tbl_cols.append("note")
                continue
            cells = [c.strip(" *`") for c in h.strip("|").split("|")]
            if not any(c and c != "-" for c in cells):
                continue
            # 整行占位（|（暂无）| - | - | - |）才跳过——数据行里含「（暂无）」字样照常迁移
            if cells and _PLACEHOLDER.fullmatch(cells[0] or "") \
                    and all(c in ("", "-") for c in cells[1:]):
                continue
            ids = re.findall(r"\[#([^\]\s]+)\]", h)
            if tbl_cols and len(cells) == len(tbl_cols):
                got, notes = {}, []
                for c, role in zip(cells, tbl_cols):
                    if not c or c == "-":
                        continue
                    if role == "note":
                        notes.append(c)
                    elif role not in got:
                        got[role] = c
                cur.append({"id": ids[0] if ids else got.get("id"),
                            "chapter": got.get("chapter"),
                            "note": " / ".join(notes) or got.get("id") or "",
                            "status": (i18n.canon_row_status(got["status"])
                                       if got.get("status") else default_status)})
                continue
            # 没见到表头或行宽与表头不符——退回位置映射（模板 5 列布局）
            tail = cells[2:]
            # 模板表最后一列是状态——迁移 note 时必须剔除，否则状态在 note 和状态列各出现一次；
            # 只有 3 列（无状态列）时整个尾部都是 note，状态回默认
            status = (i18n.canon_row_status(tail[-1])
                      if len(tail) >= 2 and tail[-1] else default_status)
            note_cells = tail[:-1] if len(tail) >= 2 else tail
            first_cell = cells[0] if cells and cells[0] not in ("-", "") else None   # 渲染的 '-' 占位≠id
            cur.append({"id": ids[0] if ids else first_cell, "chapter": cells[1] if len(cells) > 1 else None,
                        "note": " / ".join(c for c in note_cells if c) or (cells[0] if cells else ""),
                        "status": status})
    return phase, mistakes, confusions, checklist, window, prefs


# ---------------- v4.1 phase evidence (additive, legacy-safe) ----------------

def phase_manifest_status(ws):
    """Return (legacy|ready|broken, reason) without letting partial v4.1 output fail open."""
    refs = os.path.join(ws, "references")
    paths = {
        "figure": os.path.join(refs, "figure_page_index.json"),
        "image": os.path.join(refs, "image_question_index.json"),
        "teaching": os.path.join(refs, "teaching_examples.json"),
    }
    parsed, parse_errors = {}, {}
    for key, path in paths.items():
        if not os.path.lexists(path):
            continue
        if os.path.islink(path):
            parse_errors[key] = "符号链接"
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                parsed[key] = json.load(f)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            parse_errors[key] = str(e)

    figure = parsed.get("figure")
    image = parsed.get("image")
    teaching = parsed.get("teaching")
    markers = {
        # An unreadable/symlinked manifest cannot be classified as safely-legacy; treat it as a
        # capability marker and fail closed instead of using parse failure to bypass the gate.
        "figure": "figure" in parse_errors
                  or (isinstance(figure, dict) and "wiki_visual_coverage" in figure),
        "image": "image" in parse_errors
                 or (isinstance(image, dict)
                     and ("prompt_suspects" in image or "answer_suspects" in image)),
        # teaching_examples.json is itself a v4.1 marker; v4.0 never emitted it.
        "teaching": os.path.lexists(paths["teaching"]),
    }
    if not any(markers.values()):
        return "legacy", "未发现 v4.1 manifest 标记"
    ready = (not parse_errors
             and isinstance(figure, dict)
             and isinstance(figure.get("wiki_visual_coverage"), dict)
             and isinstance(image, dict)
             and isinstance(image.get("prompt_suspects"), list)
             and isinstance(image.get("answer_suspects"), list)
             and isinstance(teaching, list))
    if ready:
        return "ready", "v4.1 manifest 三件套齐备"
    missing = [key for key in paths if key not in parsed and key not in parse_errors]
    details = []
    if missing:
        details.append("缺失 " + "/".join(missing))
    if parse_errors:
        details.append("损坏/不安全 " + ", ".join("%s=%s" % x for x in sorted(parse_errors.items())))
    if not details:
        details.append("字段/schema 不完整")
    return "broken", "；".join(details)


def phase_manifest_present(ws):
    return phase_manifest_status(ws)[0] == "ready"


def phase_number_from_check(text):
    """Return the phase number represented by one checklist label, or None for non-phase rows."""
    m = re.search(r"阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+)", text or "")
    if not m:
        return None
    n = int(next(g for g in m.groups() if g))
    return n if n >= 1 else None


def _phase_evidence_shape_errors(state):
    ev = state.get("phase_evidence", {})
    if not isinstance(ev, dict):
        return ["phase_evidence 必须是对象（phase 字符串 → evidence 对象），当前 %s"
                % type(ev).__name__]
    errors = []
    for key, record in ev.items():
        if not isinstance(key, str) or not key.isdigit() or int(key) < 1 or str(int(key)) != key:
            errors.append("phase_evidence 的键必须是规范正整数字符串，当前 %r" % key)
            continue
        if not isinstance(record, dict):
            errors.append("phase_evidence[%s] 必须是对象，当前 %s" % (key, type(record).__name__))
            continue
        for field in PHASE_EVIDENCE_FIELDS:
            refs = record.get(field)
            if refs is None:
                continue
            if not isinstance(refs, list):
                errors.append("phase_evidence[%s].%s 必须是数组" % (key, field))
                continue
            if field == "checkpoint":
                checkpoint_ids = set()
                for item in refs:
                    if not isinstance(item, dict):
                        errors.append("phase_evidence[%s].checkpoint 必须是 {id,outcome} 对象数组；"
                                      "只有 ID 不能证明答对" % key)
                        continue
                    ident = item.get("id")
                    if stable_item_id_problem(ident):
                        errors.append("phase_evidence[%s].checkpoint[].id 必须是规范非空字符串" % key)
                    elif ident in checkpoint_ids:
                        errors.append("phase_evidence[%s].checkpoint[] 含重复 id %s" %
                                      (key, ident))
                    else:
                        checkpoint_ids.add(ident)
                    if item.get("outcome") not in CHECKPOINT_OUTCOMES:
                        errors.append("phase_evidence[%s].checkpoint[].outcome 必须是 %s"
                                      % (key, "/".join(CHECKPOINT_OUTCOMES)))
            else:
                if any(
                    not isinstance(value, str)
                    or not value.strip()
                    or value != value.strip()
                    for value in refs
                ):
                    errors.append("phase_evidence[%s].%s 必须是规范非空字符串数组" %
                                  (key, field))
                elif len(refs) != len(set(refs)):
                    errors.append("phase_evidence[%s].%s 含重复值" % (key, field))
                elif field == "teaching_examples" and any(
                        stable_item_id_problem(value) is not None for value in refs):
                    errors.append(
                        "phase_evidence[%s].teaching_examples 含不符合共享 "
                        "notebook/Guide 稳定 ID 规范的值" % key)
        bindings = record.get("teaching_example_bindings")
        if bindings is not None:
            if not isinstance(bindings, list):
                errors.append("phase_evidence[%s].teaching_example_bindings 必须是数组" % key)
            else:
                seen_binding_ids = set()
                seen_notebook_refs = set()
                for binding in bindings:
                    if (not isinstance(binding, dict)
                            or set(binding) != TEACHING_EXAMPLE_BINDING_FIELDS):
                        errors.append(
                            "phase_evidence[%s].teaching_example_bindings[] 必须精确包含 "
                            "id/notebook_ref/notebook_block_sha256/manifest_item_sha256" % key)
                        continue
                    binding_id = binding.get("id")
                    if stable_item_id_problem(binding_id):
                        errors.append(
                            "phase_evidence[%s].teaching_example_bindings[].id "
                            "必须是规范非空单行字符串" % key)
                    elif binding_id in seen_binding_ids:
                        errors.append(
                            "phase_evidence[%s].teaching_example_bindings 含重复 id %s" %
                            (key, binding_id))
                    else:
                        seen_binding_ids.add(binding_id)
                    notebook_ref = binding.get("notebook_ref")
                    if (not isinstance(notebook_ref, str)
                            or not notebook_ref.strip()
                            or notebook_ref != notebook_ref.strip()
                            or any(char in notebook_ref for char in ("\x00", "\r", "\n"))):
                        errors.append(
                            "phase_evidence[%s].teaching_example_bindings[].notebook_ref "
                            "必须是规范非空单行字符串" % key)
                    elif notebook_ref in seen_notebook_refs:
                        errors.append(
                            "phase_evidence[%s].teaching_example_bindings 含重复 "
                            "notebook_ref %s" % (key, notebook_ref))
                    else:
                        seen_notebook_refs.add(notebook_ref)
                    for digest_field in (
                        "notebook_block_sha256", "manifest_item_sha256",
                    ):
                        if not re.fullmatch(
                                r"[0-9a-f]{64}", str(binding.get(digest_field) or "")):
                            errors.append(
                                "phase_evidence[%s].teaching_example_bindings[].%s "
                                "必须是 lowercase SHA-256" % (key, digest_field))
        status = record.get("status")
        if status is not None and status not in PHASE_EVIDENCE_STATUSES:
            errors.append("phase_evidence[%s].status 必须是 %s，当前 %r"
                          % (key, "/".join(PHASE_EVIDENCE_STATUSES), status))
        for field in ("updated_at", "completed_at"):
            if record.get(field) is not None and not isinstance(record[field], str):
                errors.append("phase_evidence[%s].%s 必须是字符串或省略" % (key, field))
    return errors


def _local_evidence_ref(ws, ref):
    """Resolve a workspace-relative evidence ref. Return (rel, fragment, full, error)."""
    if not isinstance(ref, str) or not ref.strip():
        return None, None, None, "引用必须是非空字符串"
    raw = ref.strip().replace("\\", "/")
    if any(c in raw for c in ("\x00", "\r", "\n")):
        return None, None, None, "引用含控制字符"
    if "://" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None, None, None, "路径必须是工作区内的相对路径，不得是 URL/绝对路径"
    path_part, sep, fragment = raw.partition("#")
    segs = [s for s in path_part.split("/") if s not in ("", ".")]
    if not segs or ".." in segs:
        return None, None, None, "路径不得为空或含 .. 路径穿越"
    rel = "/".join(segs)
    full = os.path.join(ws, *segs)
    current = os.path.abspath(ws)
    for segment in segs:
        current = os.path.join(current, segment)
        if os.path.lexists(current) and _is_link_or_reparse(current):
            return None, None, None, "证据路径的父目录或文件不得是链接/重解析点"
    ws_real = os.path.normcase(os.path.realpath(ws))
    full_real = os.path.normcase(os.path.realpath(full))
    try:
        contained = os.path.commonpath((ws_real, full_real)) == ws_real
    except ValueError:
        contained = False
    if not contained:
        return None, None, None, "路径经符号链接逃出工作区"
    if not os.path.isfile(full):
        return None, None, None, "证据文件不存在: %s" % rel
    return rel, unquote(fragment) if sep else "", full, None


def _markdown_anchors(path):
    anchors, counts, fence = set(), {}, None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            fence, marker = _notebook._fence_step(fence, line)
            if marker or fence is not None:
                continue
            m = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*$", line)
            if not m:
                continue
            slug = _notebook.github_slug(m.group(1))
            n = counts.get(slug, 0)
            counts[slug] = n + 1
            anchors.add(slug if n == 0 else "%s-%d" % (slug, n))
    if fence is not None:
        raise ValueError("notebook contains an unterminated fenced code block")
    return anchors


def _json_ids(path, teaching_only=False):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, "无法读取/解析 %s: %s" % (os.path.basename(path), e)
    if not isinstance(data, list):
        return None, "%s 顶层必须是数组" % os.path.basename(path)
    ids = set()
    # quiz_bank.json and teaching_examples.json both define items ONLY at the top-level array.
    # Recursing through arbitrary metadata would let a nested decoy `{id: ...}` become checkpoint
    # evidence even though no runtime selector can ever serve or grade it.
    for value in data:
        if not isinstance(value, dict):
            continue
        ident = value.get("id")
        is_teaching = (value.get("source_type") == "example"
                       or value.get("teaching_role") in ("worked_example", "paired_problem"))
        if ident is not None and (not teaching_only or is_teaching):
            ids.add(str(ident))
    return ids, None


def _read_json_value(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, "%s 无法读取/解析: %s" % (os.path.basename(path), e)


def _workspace_digest(ws, rel):
    """Return (sha256, error) for a regular workspace child without following symlinks."""
    parts = rel.replace("\\", "/").split("/")
    if (not parts or any(part in ("", ".", "..") for part in parts)
            or os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel)):
        return None, "不安全的相对路径"
    current = os.path.abspath(ws)
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return None, "路径含符号链接"
    ws_real, path_real = os.path.realpath(ws), os.path.realpath(current)
    try:
        inside = (os.path.commonpath([os.path.normcase(path_real), os.path.normcase(ws_real)])
                  == os.path.normcase(ws_real))
    except ValueError:
        inside = False
    if not inside:
        return None, "路径解析到工作区外"
    if not os.path.isfile(current):
        return None, "文件不存在或不是普通文件"
    digest = hashlib.sha256()
    try:
        with open(current, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return None, "读取失败: %s" % exc
    return digest.hexdigest(), None


def _external_digest(root, rel):
    """Return (sha256, error) for a captured source-material child without following links."""
    if not isinstance(root, str) or not root.strip() or not os.path.isabs(root):
        return None, "materials_root 不是绝对路径"
    parts = str(rel or "").replace("\\", "/").split("/")
    if (not parts or any(part in ("", ".", "..") for part in parts)
            or os.path.isabs(str(rel or "")) or re.match(r"^[A-Za-z]:", str(rel or ""))):
        return None, "不安全的材料相对路径"
    current = os.path.abspath(root)
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return None, "材料路径含符号链接"
    root_real, path_real = os.path.realpath(root), os.path.realpath(current)
    try:
        inside = (os.path.commonpath([os.path.normcase(path_real), os.path.normcase(root_real)])
                  == os.path.normcase(root_real))
    except ValueError:
        inside = False
    if not inside:
        return None, "材料路径解析到根目录外"
    if not os.path.isfile(current):
        return None, "原始材料不存在或不是普通文件"
    digest = hashlib.sha256()
    try:
        with open(current, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return None, "读取失败: %s" % exc
    return digest.hexdigest(), None


def _material_inventory_digest(root):
    """Re-scan PDF paths using the visual builder's material-pruning contract."""
    if not isinstance(root, str) or not root.strip() or not os.path.isabs(root):
        return None, "materials_root 不是绝对路径"
    if not os.path.isdir(root) or os.path.islink(root):
        return None, "materials_root 不存在、不是目录或是符号链接"
    try:
        from build_raw_input_from_workspace import (
            ALWAYS_PRUNE, _is_leftover_workspace, _is_workspace_root)
    except Exception as exc:  # pragma: no cover - the package always ships this helper
        return None, "无法加载材料目录剪枝规则: %s" % exc
    paths = []
    for base, dirs, files in os.walk(root):
        for dirname in list(dirs):
            full = os.path.join(base, dirname)
            if (dirname in ALWAYS_PRUNE or _is_leftover_workspace(full, dirname)
                    or _is_workspace_root(full)):
                dirs.remove(dirname)
        for filename in sorted(files):
            if filename.lower().endswith(".pdf"):
                paths.append(os.path.relpath(os.path.join(base, filename), root).replace("\\", "/"))
    raw = json.dumps(sorted(paths), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), None


def _manifest_result_digest(value):
    try:
        payload = {key: val for key, val in value.items() if key != "integrity"}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return None, "派生结果无法 canonicalize: %s" % exc
    return hashlib.sha256(raw).hexdigest(), None


def _visual_integrity_problems(ws, figure, image, wiki_names):
    """Reject stale visual indices whose mutable inputs no longer match their snapshot."""
    figure_meta = figure.get("integrity") if isinstance(figure, dict) else None
    image_meta = image.get("integrity") if isinstance(image, dict) else None
    if not isinstance(figure_meta, dict) or not isinstance(image_meta, dict):
        return ["视觉索引缺 integrity freshness 快照；请重新运行 build_visual_index.py"]
    if figure_meta != image_meta:
        return ["figure/image 两份视觉索引的 integrity 快照不一致；请在同一次运行中重建"]
    meta = figure_meta
    problems = []
    if meta.get("schema_version") != 2:
        problems.append("视觉索引 integrity.schema_version 必须为 2；请重建以绑定原始 PDF 与派生结果")
    if not isinstance(meta.get("generated_at"), str) or not meta["generated_at"].strip():
        problems.append("视觉索引 integrity.generated_at 缺失")
    mode = meta.get("mode")
    if not isinstance(mode, dict):
        problems.append("视觉索引 integrity.mode 不是对象")
    elif mode.get("materials_scan") is not True:
        problems.append("视觉索引生成模式未运行 materials scan，freshness 证据不完整")
    inputs = meta.get("inputs")
    if not isinstance(inputs, dict):
        problems.append("视觉索引 integrity.inputs 不是对象")
        return problems
    required = {"references/quiz_bank.json", "references/teaching_examples.json"}
    if os.path.lexists(os.path.join(ws, ".ingest")):
        required.add(".ingest/content_units.jsonl")
    required.update("references/wiki/" + name for name in wiki_names)
    for optional_rel in ("references/teaching_baseline.json", "ingest_report.json"):
        if os.path.lexists(os.path.join(ws, *optional_rel.split("/"))):
            required.add(optional_rel)
    for rel in sorted(required - set(inputs)):
        problems.append("视觉索引 freshness 未记录 %s 的 sha256" % rel)
    # Verify every captured dependency, including prompt/answer and wiki image assets.  Asset
    # readability is part of the zero-suspect/embedded conclusion and must not go stale silently.
    for rel, recorded in sorted(inputs.items()):
        if not isinstance(rel, str):
            problems.append("视觉索引 integrity.inputs 含非字符串路径")
            continue
        if not isinstance(recorded, dict) or not re.fullmatch(r"[0-9a-f]{64}",
                                                              str(recorded.get("sha256") or "")):
            problems.append("视觉索引 freshness 未记录 %s 的 sha256" % rel)
            continue
        actual, error = _workspace_digest(ws, rel)
        if error:
            problems.append("视觉索引 freshness 无法核验 %s：%s" % (rel, error))
        elif actual != recorded["sha256"]:
            problems.append("视觉索引已 stale：%s 内容在索引生成后发生变化" % rel)
    materials = meta.get("materials")
    material_root = mode.get("materials_root") if isinstance(mode, dict) else None
    if not isinstance(materials, dict):
        problems.append("视觉索引 integrity.materials 不是对象")
    else:
        indexed_pdfs = set((figure.get("files") or {}).keys()) if isinstance(figure.get("files"), dict) else set()
        for rel in sorted(indexed_pdfs - set(materials)):
            problems.append("视觉索引 freshness 未记录原始 PDF %s 的 sha256" % rel)
        for rel, recorded in sorted(materials.items()):
            if not isinstance(rel, str):
                problems.append("视觉索引 integrity.materials 含非字符串路径")
                continue
            if not isinstance(recorded, dict) or not re.fullmatch(
                    r"[0-9a-f]{64}", str(recorded.get("sha256") or "")):
                problems.append("视觉索引 freshness 未记录原始 PDF %s 的 sha256" % rel)
                continue
            actual, error = _external_digest(material_root, rel)
            if error:
                problems.append("视觉索引 freshness 无法核验原始 PDF %s：%s" % (rel, error))
            elif actual != recorded["sha256"]:
                problems.append("视觉索引已 stale：原始 PDF %s 在索引生成后发生变化" % rel)
        recorded_inventory = meta.get("material_inventory_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(recorded_inventory or "")):
            problems.append("视觉索引 freshness 未记录原始材料 PDF 路径清单")
        else:
            actual_inventory, error = _material_inventory_digest(material_root)
            if error:
                problems.append("视觉索引 freshness 无法核验原始材料 PDF 路径清单：%s" % error)
            elif actual_inventory != recorded_inventory:
                problems.append("视觉索引已 stale：原始材料 PDF 路径清单在索引生成后发生变化")
    outputs = meta.get("outputs")
    manifests = {"figure_page_index.json": figure, "image_question_index.json": image}
    if not isinstance(outputs, dict):
        problems.append("视觉索引 integrity.outputs 不是对象")
    else:
        for name, manifest in manifests.items():
            recorded = outputs.get(name)
            if not isinstance(recorded, dict) or not re.fullmatch(
                    r"[0-9a-f]{64}", str(recorded.get("sha256") or "")):
                problems.append("视觉索引 freshness 未绑定派生结果 %s" % name)
                continue
            actual, error = _manifest_result_digest(manifest)
            if error:
                problems.append("视觉索引 freshness 无法核验派生结果 %s：%s" % (name, error))
            elif actual != recorded["sha256"]:
                problems.append("视觉索引已 stale：%s 的派生结果在生成后发生变化" % name)
    return problems


def _declared_prompt_asset_problems(ws, layers, chapter_keys):
    """Independently enforce current prompt-asset readability for the phase.

    Hash freshness catches post-index edits, while this check makes the invariant explicit even for
    a malformed/hand-built index: every current-chapter item declaring requires/maybe must have only
    readable question-side assets before phase completion.
    """
    roles = {"question_context", "figure", "diagram", "table"}
    problems = []
    for layer_name, items in layers:
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not (item.get("requires_assets") is True
                                                  or item.get("maybe_requires_assets") is True):
                continue
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope is not None and scope not in chapter_keys:
                continue
            ident = str(item.get("id") or "?")
            prompt_assets = [a for a in (item.get("assets") or [])
                             if isinstance(a, dict) and a.get("role") in roles]
            if not prompt_assets:
                problems.append("%s[%s] 声明图依赖但没有题面侧 asset" % (layer_name, ident))
                continue
            bad = []
            for asset in prompt_assets:
                raw = asset.get("path")
                if not isinstance(raw, str) or not raw.strip():
                    bad.append("<missing path>")
                    continue
                _digest, error = _workspace_digest(ws, raw.strip().replace("\\", "/"))
                if error:
                    bad.append("%s (%s)" % (raw, error))
            if bad:
                problems.append("%s[%s] 有不可读题面 asset：%s" %
                                (layer_name, ident, ", ".join(bad[:5])))
    return problems


def _scope_number(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return str(value)
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(?:第\s*)?(?:chapter\s*)?0*(\d+)(?:\s*章)?\s*", value, re.I)
        if m and int(m.group(1)) >= 1:
            return str(int(m.group(1)))
    return None


def _items_by_id(value):
    if not isinstance(value, list):
        return {}
    return {str(item["id"]): item for item in value
            if isinstance(item, dict) and item.get("id") is not None}


def _teaching_baseline_for_phase(ws, chapter_keys, teaching, quiz):
    """Return ``(expected_ids, problems)`` from ingest's durable teaching baseline.

    New reports persist an exact per-chapter ID map.  Early v4.1 reports only carried a flat ID list;
    retained IDs can still be scoped from either current layer, but an ID missing from both layers has
    lost its chapter too.  In that ambiguous legacy case every phase completion fails closed until the
    example/report is repaired instead of silently treating the phase as teaching-N/A.
    """
    baseline_path = os.path.join(ws, "references", "teaching_baseline.json")
    report_path = os.path.join(ws, "ingest_report.json")
    if os.path.lexists(baseline_path):
        if os.path.islink(baseline_path) or not os.path.isfile(baseline_path):
            return set(), ["references/teaching_baseline.json 不是安全的普通文件"]
        source_path = baseline_path
        source_name = "references/teaching_baseline.json"
    elif os.path.isfile(report_path) and not os.path.islink(report_path):
        source_path = report_path
        source_name = "ingest_report.json"
    else:
        return set(), []
    report, error = _read_json_value(source_path)
    if error:
        return set(), ["教学例题保留基线无法读取：%s" % error]
    if not isinstance(report, dict):
        return set(), ["%s 顶层必须是对象，无法核对教学例题保留基线" % source_name]
    if source_name.endswith("teaching_baseline.json"):
        if report.get("schema_version") != 1:
            return set(), ["references/teaching_baseline.json schema_version 必须为 1"]
        if report.get("policy") != "append_only":
            return set(), ["references/teaching_baseline.json policy 必须精确为 append_only"]
    raw_ids = report.get("teaching_example_ids")
    if raw_ids is None and source_name.endswith("teaching_baseline.json"):
        return set(), ["references/teaching_baseline.json 缺少 teaching_example_ids"]
    if raw_ids is None:                         # pre-baseline legacy report
        return set(), []
    if not (isinstance(raw_ids, list)
            and all(stable_item_id_problem(x) is None for x in raw_ids)
            and len(set(raw_ids)) == len(raw_ids)):
        return set(), ["%s.teaching_example_ids 必须遵循共享稳定 ID 契约且不得重复" % source_name]
    baseline = set(raw_ids)
    expected = set()
    problems = []
    mapped_ids = set()
    raw_map = report.get("teaching_example_ids_by_chapter")
    if raw_map is not None:
        if not isinstance(raw_map, dict):
            problems.append("%s.teaching_example_ids_by_chapter 必须是对象" % source_name)
        else:
            for raw_scope, raw_values in raw_map.items():
                scope = _scope_number(raw_scope)
                if scope is None or not (isinstance(raw_values, list)
                                         and all(stable_item_id_problem(x) is None
                                                 for x in raw_values)):
                    problems.append("teaching_example_ids_by_chapter[%r] 必须是可解析章节对应的稳定 ID 数组"
                                    % raw_scope)
                    continue
                values = set(raw_values)
                if len(values) != len(raw_values):
                    problems.append("teaching_example_ids_by_chapter[%r] 含重复 ID" % raw_scope)
                mapped_ids.update(values)
                if scope in chapter_keys:
                    expected.update(values)
            if mapped_ids != baseline:
                problems.append("teaching_example_ids_by_chapter 与 teaching_example_ids 全集不一致")

    teaching_by_id = _items_by_id(teaching)
    quiz_by_id = _items_by_id(quiz)
    missing_from_both = baseline - set(teaching_by_id) - set(quiz_by_id)
    if raw_map is None:
        # Scope every retained legacy ID from whichever live layer still carries it.
        for ident in sorted(baseline - missing_from_both):
            item = teaching_by_id.get(ident) or quiz_by_id.get(ident)
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope is None:
                problems.append("教学例题保留基线 %s 在现存层中没有可解析 chapter/phase" % ident)
            elif scope in chapter_keys:
                expected.add(ident)
        if missing_from_both:
            problems.append("教学例题保留基线同时从 quiz_bank 与 teaching_examples 消失，且旧报告没有"
                            "逐章映射，无法安全判定所属阶段: %s" % ", ".join(sorted(missing_from_both)))
    else:
        missing_here = missing_from_both & expected
        if missing_here:
            problems.append("本阶段教学例题保留基线同时从 quiz_bank 与 teaching_examples 消失: %s"
                            % ", ".join(sorted(missing_here)))
    return expected, problems


def _phase_scope_context(ws, record, phase):
    """Validate evidence belongs to this plan phase; return problems/wiki names/chapter keys."""
    plan_wikis = _plan_phase_wikis(ws)
    expected = set(plan_wikis.get(phase) or [])
    recorded = {ref.strip().replace("\\", "/").split("#", 1)[0]
                for ref in record.get("wiki") or [] if isinstance(ref, str)}
    problems = []
    if expected:
        missing = expected - recorded
        if missing:
            problems.append("wiki evidence 未命中阶段 %d 在 study_plan.md 指定的文件: %s"
                            % (phase, ", ".join(sorted(missing))))
        wrong = recorded - expected
        if wrong:
            problems.append("wiki evidence 含其他阶段文件: %s" % ", ".join(sorted(wrong)))
    selected = expected or recorded
    wiki_names = {x.rsplit("/", 1)[-1] for x in selected}
    wiki_chapters = set()
    for name in wiki_names:
        m = re.match(r"ch0*(\d+)(?:\D|$)", name, re.I)
        if m:
            wiki_chapters.add(str(int(m.group(1))))
    chapter_keys = wiki_chapters or {str(phase)}

    for ref in record.get("notebook") or []:
        if not isinstance(ref, str):
            continue
        m = re.search(r"(?:^|/)ch0*(\d+)(?:\D|$)", ref.replace("\\", "/"), re.I)
        if m and str(int(m.group(1))) not in chapter_keys:
            problems.append("notebook evidence 明显属于其他章: %s（本阶段章=%s）"
                            % (ref, "/".join(sorted(chapter_keys))))

    quiz, qerr = _read_json_value(os.path.join(ws, "references", "quiz_bank.json"))
    if not qerr:
        quiz_by_id = _items_by_id(quiz)
        for cp in record.get("checkpoint") or []:
            if not isinstance(cp, dict):
                continue
            item = quiz_by_id.get(str(cp.get("id")))
            if not item:
                continue
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope not in chapter_keys:
                problems.append("checkpoint %s 属于 phase/chapter=%s，不属于当前阶段章 %s"
                                % (cp.get("id"), scope or "未知", "/".join(sorted(chapter_keys))))

    teaching, terr = _read_json_value(os.path.join(ws, "references", "teaching_examples.json"))
    if not terr:
        teaching_by_id = _items_by_id(teaching)
        for ident in record.get("teaching_examples") or []:
            item = teaching_by_id.get(str(ident))
            if not item:
                continue
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope not in chapter_keys:
                problems.append("教学例题 %s 属于 phase/chapter=%s，不属于当前阶段章 %s"
                                % (ident, scope or "未知", "/".join(sorted(chapter_keys))))
    return problems, wiki_names, chapter_keys


def _phase_manifest_content(ws, state, record, phase):
    """Return (problems, teaching_required, chapter_keys) for the v4.1 manifest trio."""
    problems, wiki_names, chapter_keys = _phase_scope_context(ws, record, phase)
    if not phase_manifest_present(ws):
        return problems, True, chapter_keys
    refs = os.path.join(ws, "references")
    figure, ferr = _read_json_value(os.path.join(refs, "figure_page_index.json"))
    image, ierr = _read_json_value(os.path.join(refs, "image_question_index.json"))
    teaching, terr = _read_json_value(os.path.join(refs, "teaching_examples.json"))
    quiz, qerr = _read_json_value(os.path.join(refs, "quiz_bank.json"))
    problems.extend(x for x in (ferr, ierr, terr, qerr) if x)
    if any((ferr, ierr, terr, qerr)):
        return problems, True, chapter_keys
    if not isinstance(figure, dict):
        problems.append("figure_page_index.json 顶层必须是对象")
    if not isinstance(image, dict):
        problems.append("image_question_index.json 顶层必须是对象")
    if not isinstance(teaching, list):
        problems.append("teaching_examples.json 顶层必须是数组")
    if not isinstance(quiz, list):
        problems.append("quiz_bank.json 顶层必须是数组")
    if problems:
        return problems, True, chapter_keys
    teaching_structure_problems = (
        _teaching_examples.pending_manifest_structure_problems(teaching)
    )
    if teaching_structure_problems:
        problems.extend(teaching_structure_problems)
        return problems, True, chapter_keys

    # Zero suspects is meaningful only when the recall net actually ran with all needed backends and
    # source PDFs.  These warning prefixes make one or more denominators unknowable, so accepting the
    # numeric zeros would recreate the original false-ready failure.
    recall_blockers = (
        "no_materials", "no_pdfs_found", "no_media_backend", "pdf_text_failed", "media_failed",
        "source_pdf_not_indexed", "answer_source_pdf_not_indexed", "wiki_visual_no_wiki_dir",
        "wiki_visual_read_failed", "wiki_visual_unsafe_wiki_dir", "wiki_apply_unmapped",
        "wiki_answer_manual_exposure", "shared_prompt_answer_page",
        "integrity_input_unreadable", "integrity_material_unreadable",
    )
    blocked_warnings = []
    for name, manifest in (("figure_page_index", figure),
                           ("image_question_index", image)):
        warnings = manifest.get("warnings")
        if warnings is None:
            warnings = []
        if not isinstance(warnings, list):
            problems.append("%s.warnings 必须是数组" % name)
            continue
        for warning in warnings:
            text = str(warning)
            if text.startswith(recall_blockers) and text not in blocked_warnings:
                blocked_warnings.append(text)
    if blocked_warnings:
        problems.append("视觉召回网未完整运行，不能把 0 疑漏当作完成证据: %s"
                        % "；".join(blocked_warnings[:8]))

    problems.extend(_visual_integrity_problems(ws, figure, image, wiki_names))
    problems.extend(_declared_prompt_asset_problems(
        ws, (("quiz_bank", quiz), ("teaching_examples", teaching)), chapter_keys))

    coverage = figure.get("wiki_visual_coverage")
    if not isinstance(coverage, dict):
        problems.append("figure_page_index.wiki_visual_coverage 必须是对象")
        coverage = {}
    per_chapter = coverage.get("per_chapter")
    pages = coverage.get("pages")
    manual_answer_rows = coverage.get("manual_answer_exposure_pages")
    if not isinstance(manual_answer_rows, list):
        problems.append("wiki_visual_coverage.manual_answer_exposure_pages 不是数组")
    else:
        relevant_manual = [row for row in manual_answer_rows
                           if isinstance(row, dict)
                           and (row.get("wiki_file") in wiki_names
                                or row.get("wiki_file") is None)]
        if relevant_manual:
            labels = ["%s p.%s" % (row.get("source_file") or "?", row.get("page") or "?")
                      for row in relevant_manual[:5]]
            problems.append("当前阶段 wiki 仍提前暴露 %d 个答案专属页：%s"
                            % (len(relevant_manual), "、".join(labels)))
    shared_blocker_rows = coverage.get("shared_prompt_answer_blocker_pages")
    shared_blocker_count = coverage.get("shared_prompt_answer_blocker_count")
    if not isinstance(shared_blocker_rows, list):
        problems.append("wiki_visual_coverage.shared_prompt_answer_blocker_pages 不是数组")
    elif (not isinstance(shared_blocker_count, int) or isinstance(shared_blocker_count, bool)
          or shared_blocker_count < 0 or shared_blocker_count != len(shared_blocker_rows)):
        problems.append("wiki_visual_coverage 共享题解页 blocker 计数与列表不一致")
    else:
        relevant_shared = [row for row in shared_blocker_rows
                           if isinstance(row, dict)
                           and (row.get("wiki_file") in wiki_names
                                or row.get("wiki_file") is None)]
        if relevant_shared:
            labels = ["%s p.%s" % (row.get("source_file") or "?", row.get("page") or "?")
                      for row in relevant_shared[:5]]
            problems.append("当前阶段有 %d 个题面/答案共享整页尚无审核裁图：%s"
                            % (len(relevant_shared), "、".join(labels)))
    if not isinstance(per_chapter, dict):
        problems.append("figure_page_index 的 wiki_visual_coverage.per_chapter 不是对象")
    else:
        for wiki_name in wiki_names:
            counts = per_chapter.get(wiki_name)
            if counts is None:
                continue                         # no detected visual pages in this wiki = visual N/A
            if not isinstance(counts, dict) or not isinstance(counts.get("missing"), int):
                problems.append("%s 的 wiki_visual_coverage 计数无效" % wiki_name)
            elif counts["missing"] != 0:
                problems.append("%s 仍有 %d 个 wiki 视觉页 missing（必须为 0）"
                                % (wiki_name, counts["missing"]))
    if not isinstance(pages, list):
        problems.append("wiki_visual_coverage.pages 不是数组")
    else:
        missing_rows = [p for p in pages if isinstance(p, dict)
                        and p.get("wiki_file") in wiki_names and p.get("status") == "missing"]
        if missing_rows and not any("wiki 视觉页 missing" in p for p in problems):
            problems.append("当前阶段仍有 %d 个 wiki 视觉页 missing（必须为 0）" % len(missing_rows))
        unmapped = [p for p in pages if isinstance(p, dict) and p.get("wiki_file") is None
                    and p.get("status") == "missing"]
        if unmapped:
            problems.append("仍有 %d 个已检测视觉页未映射到任何 wiki，不能从阶段分母静默排除"
                            % len(unmapped))

    for field in ("prompt_suspects", "answer_suspects"):
        rows = image.get(field)
        if not isinstance(rows, list):
            problems.append("image_question_index.%s 不是数组" % field)
            continue
        unscoped = []
        scoped = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope is None:
                unscoped.append(item)
            elif scope in chapter_keys:
                scoped.append(item)
        if unscoped:
            ids = ", ".join(str(x.get("id")) for x in unscoped[:5])
            problems.append("%s 含 %d 个无可解析 chapter/phase 的疑漏（%s），不能静默排除"
                            % (field, len(unscoped), ids))
        if scoped:
            ids = ", ".join(str(x.get("id")) for x in scoped[:5])
            problems.append("当前阶段 %s=%d（%s），必须先补齐到 0" % (field, len(scoped), ids))

    expected = set()
    unscoped_teaching = []
    if isinstance(teaching, list):
        for item in teaching:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            scope = _scope_number(item.get("phase")) or _scope_number(item.get("chapter"))
            if scope is None:
                unscoped_teaching.append(str(item["id"]))
            elif scope in chapter_keys:
                expected.add(str(item["id"]))
    if unscoped_teaching:
        problems.append("teaching_examples 含无可解析 chapter/phase 的条目，不能从阶段全集静默排除: %s"
                        % ", ".join(unscoped_teaching[:8]))
    teaching_manifest_expected = set(expected)
    baseline_expected, baseline_problems = _teaching_baseline_for_phase(
        ws, chapter_keys, teaching, quiz)
    baseline_only = baseline_expected - teaching_manifest_expected
    if baseline_only:
        problems.append(
            "教学例题保留基线中的题目缺少当前 teaching_examples.json 快照: %s；"
            "quiz-only 条目不能替代教学 roster，请恢复教学快照或重新导入"
            % ", ".join(sorted(baseline_only)))
    expected.update(baseline_expected)
    problems.extend(baseline_problems)
    problems.extend(_step_roster_baseline_problems(ws, phase, teaching))
    binding_status = _step_teaching_binding_status(ws, state, phase, teaching)
    problems.extend(binding_status["problems"])
    recorded = set(binding_status["completed_ids"])
    missing_examples = expected - recorded
    if missing_examples:
        problems.append("教学例题 evidence 未覆盖本阶段 manifest 全集: %s"
                        % ", ".join(sorted(missing_examples)))
    return problems, bool(expected), chapter_keys


def _structured_study_guide_problems(ws, state, chapter_keys):
    """Fail closed on the typed chapter/visual gates for structured workspaces only.

    ``study_guide_content`` owns the expected-item denominator and source-reference validation.
    This phase gate deliberately does not duplicate that schema: it loads the canonical manifest,
    requires the non-abridged profile, and (only for standing visual mode) asks the shared
    capability model whether the rendered artifact and all-page QA are ready.
    """
    ingest = os.path.join(ws, ".ingest")
    if not os.path.lexists(ingest):
        return []
    if os.path.islink(ingest) or not os.path.isdir(ingest):
        return ["结构化工作区标记 .ingest 必须是工作区内的普通目录"]

    chapters = []
    for key in sorted(chapter_keys, key=lambda value: int(value) if str(value).isdigit() else 10 ** 9):
        try:
            chapter = int(key)
        except (TypeError, ValueError):
            return ["无法从当前阶段确定章节号，不能加载 typed Study Guide manifest"]
        if chapter < 1:
            return ["当前阶段章节号必须 >=1，不能加载 typed Study Guide manifest"]
        chapters.append(chapter)
    if not chapters:
        return ["无法从当前阶段确定章节号，不能加载 typed Study Guide manifest"]

    problems = []
    try:
        from study_guide_content import ContentError, load_and_validate_manifest
    except (ImportError, OSError) as exc:
        return ["无法加载 Study Guide manifest validator：%s" % exc]

    for chapter in chapters:
        try:
            _manifest, report = load_and_validate_manifest(ws, chapter)
        except (ContentError, OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append("当前章 ch%02d 的 typed Study Guide manifest 未通过验证：%s"
                            % (chapter, exc))
            continue
        if report.get("profile") != "full":
            problems.append("当前章 ch%02d 的 typed Study Guide manifest 必须 profile=full，当前为 %r"
                            % (chapter, report.get("profile")))

    if i18n.workspace_artifact_mode(state) != "visual":
        return problems

    # Lazy by design: chat-mode phase completion must not import the PDF/artifact stack.
    try:
        from readiness import capability_readiness
    except (ImportError, OSError) as exc:
        problems.append("visual 阶段完成无法加载 artifact readiness：%s" % exc)
        return problems
    for chapter in chapters:
        try:
            capabilities = capability_readiness(ws, chapter=chapter)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append("当前章 ch%02d 无法计算 artifact readiness：%s" % (chapter, exc))
            continue
        artifact = capabilities.get("artifact_ready") if isinstance(capabilities, dict) else None
        if not isinstance(artifact, dict) or artifact.get("status") != "ready":
            reasons = artifact.get("reason_codes") if isinstance(artifact, dict) else None
            suffix = ", ".join(str(reason) for reason in (reasons or [])) or "unknown"
            problems.append("当前章 ch%02d 的 artifact_ready 必须为 ready（原因：%s）；"
                            "先完成渲染、receipt 哈希绑定与逐页 QA" % (chapter, suffix))
    return problems


_COMPLETION_ASSET_POLICY_UNSET = object()


def _completion_asset_policy(ws):
    try:
        # Lazy import avoids validate_workspace -> update_progress -> validate_workspace at module
        # import time while keeping every runtime consumer on one three-layer policy snapshot.
        try:
            from . import validate_workspace as validator
        except ImportError:  # standalone scripts directory import
            import validate_workspace as validator
        snapshot = validator.workspace_asset_policy_snapshot(ws)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, "student-attempt 资产策略快照不完整：%s" % exc
    if snapshot["unsafe_paths"]:
        return snapshot, "student-attempt 资产策略含不安全路径：%s" % snapshot["unsafe_paths"][0]
    if snapshot["conflicts"]:
        return snapshot, "student-attempt 资产角色冲突：%s" % snapshot["conflicts"][0]
    return snapshot, None


def _item_completion_asset_error(item, tainted_keys):
    if not isinstance(item, dict):
        return None
    visual = (
        item.get("requires_assets") is True
        or item.get("maybe_requires_assets") is True
        or item.get("question_text_status") in ("stub", "page_reference")
    )
    if not visual:
        return None
    prompt_ok = any(
        isinstance(asset, dict)
        and asset.get("role") in ("question_context", "figure", "diagram", "table")
        and physical_asset_key(asset.get("path")) not in tainted_keys
        for asset in (item.get("assets") or [])
    )
    if not prompt_ok:
        return "视觉依赖 item 只有 student_attempt/无可用题面资产，不能计入完成证据"
    return None


def phase_evidence_ref_error(ws, field, ref, asset_policy=_COMPLETION_ASSET_POLICY_UNSET,
                             asset_policy_error=None):
    """Validate one evidence reference; return a human-readable error or None."""
    if asset_policy is _COMPLETION_ASSET_POLICY_UNSET:
        asset_policy, asset_policy_error = _completion_asset_policy(ws)
    if asset_policy_error:
        return asset_policy_error
    tainted_keys = asset_policy["tainted_keys"]
    if field in ("wiki", "visual", "notebook"):
        rel, fragment, full, error = _local_evidence_ref(ws, ref)
        if error:
            return error
        if field == "wiki" and not (rel.startswith("references/wiki/") and rel.endswith(".md")):
            return "wiki 证据必须位于 references/wiki/*.md"
        if field == "visual":
            visual_manifest = rel in PHASE_MANIFESTS[:2]
            visual_asset = rel.startswith("references/assets/")
            if not (visual_manifest or visual_asset):
                return "visual 证据必须是视觉 manifest 或 references/assets/ 下的本地资产"
            if visual_asset and physical_asset_key(rel) in tainted_keys:
                return "visual 证据指向 student_attempt 污染资产，不能计入完成证据"
        if field == "notebook":
            if not (rel.startswith("notebook/") and rel.endswith(".md")):
                return "notebook 证据必须位于 notebook/*.md"
            if not fragment:
                return "notebook 证据必须带真实条目锚点（path.md#anchor）"
            try:
                if fragment not in _markdown_anchors(full):
                    return "notebook 证据锚点不存在: %s#%s" % (rel, fragment)
            except (OSError, UnicodeDecodeError, ValueError) as e:
                return "notebook 证据无法读取: %s" % e
        return None

    checkpoint = field == "checkpoint"
    ident_value = ref.get("id") if checkpoint and isinstance(ref, dict) else ref
    if (not isinstance(ident_value, str) or not ident_value.strip()
            or any(c in ident_value for c in ("\x00", "\r", "\n"))):
        return "%s 证据 ID 必须是非空单行字符串" % field
    ident = ident_value.strip()
    if checkpoint:
        id_problem = stable_item_id_problem(ident)
        if id_problem:
            return "checkpoint ID 不符合共享稳定 ID 规范: %s" % id_problem
    if field == "teaching_examples":
        id_problem = stable_item_id_problem(ident)
        if id_problem:
            return "教学例题 ID 不符合共享 notebook/Guide 规范: %s" % id_problem
        teaching_path = os.path.join(ws, "references", "teaching_examples.json")
        if not os.path.isfile(teaching_path):
            return ("教学例题证据要求 references/teaching_examples.json；"
                    "quiz-only 条目不能替代教学 roster")
        ids, error = _json_ids(teaching_path)
        if error:
            return error
        if ident not in ids:
            return "教学例题 ID 不在 teaching_examples manifest 中: %s" % ident
        candidates = [
            row for row in asset_policy["teaching_rows"] + asset_policy["quiz_rows"]
            if isinstance(row, dict) and str(row.get("id")) == ident
        ]
        for item in candidates:
            problem = _item_completion_asset_error(item, tainted_keys)
            if problem:
                return problem
        return None
    if field == "checkpoint":
        ids, error = _json_ids(os.path.join(ws, "references", "quiz_bank.json"))
        if error:
            return error
        if ident not in ids:
            return "checkpoint ID 不在 references/quiz_bank.json 中: %s" % ident
        for item in asset_policy["quiz_rows"]:
            if isinstance(item, dict) and str(item.get("id")) == ident:
                problem = _item_completion_asset_error(item, tainted_keys)
                if problem:
                    return problem
        return None
    return "未知 phase evidence 字段: %s" % field


def _no_questions_preference(state):
    prefs = state.get("preferences") or {}
    if not isinstance(prefs, dict):
        return False
    for key in ("no_questions", "no-questions", "不要出题", "不要问我"):
        if key not in prefs:
            continue
        value = str(prefs.get(key) or "").strip().lower()
        if value in ("1", "true", "yes", "on", "是", "不出题", "不要问"):
            return True
    return False


def interaction_style_preference(state):
    """Return the persisted canonical preference, defaulting legacy state to batch."""
    prefs = state.get("preferences") if isinstance(state, dict) else None
    raw = prefs.get("interaction_style", "batch") if isinstance(prefs, dict) else "batch"
    return raw if raw in INTERACTION_STYLES else "batch"


def interaction_style_full_route(state):
    """Return whether this runtime's existing material route is full.

    PR #41 targets a baseline that predates the explicit lightweight/full
    selector.  On that baseline a missing field is the historical implicit
    full route; an explicitly present non-full value still keeps paced tutoring
    dormant.  The later processing-mode migration replaces this compatibility
    seam with its own missing->lightweight rule.
    """

    return (
        isinstance(state, dict)
        and state.get("processing_mode", "full") == "full"
    )


def effective_interaction_style(state):
    """Step cadence is active only for full mode with questions enabled."""
    if (isinstance(state, dict)
            and interaction_style_full_route(state)
            and not _no_questions_preference(state)
            and interaction_style_preference(state) == "step_by_step"):
        return "step_by_step"
    return "batch"


def interaction_style_dormant(state):
    return (interaction_style_preference(state) == "step_by_step"
            and effective_interaction_style(state) != "step_by_step")


def _phase_completion_problems(record, status, teaching_required=True):
    problems = []
    required_core = tuple(x for x in PHASE_EVIDENCE_CORE
                          if x != "teaching_examples" or teaching_required)
    missing = [field for field in required_core if not (record.get(field) or [])]
    if missing:
        problems.append("缺必需证据: %s" % ", ".join(missing))
    if status == "verified":
        checkpoints = record.get("checkpoint") or []
        distinct_ids = {x.get("id") for x in checkpoints
                        if isinstance(x, dict) and isinstance(x.get("id"), str)}
        if len(distinct_ids) < 2:
            problems.append("verified 至少需要 2 个不同题目的已处理 checkpoint")
        if not any(isinstance(x, dict) and x.get("outcome") == "passed" for x in checkpoints):
            problems.append("verified 至少需要 1 个 outcome=passed 的 checkpoint")
    return problems


def phase_evidence_errors(
        ws, state, enforce_manifest_gate=True, recoverable_stale=None):
    """Return strict phase errors, optionally separating mount-only repairs.

    ``recoverable_stale`` may be a caller-owned list.  When supplied, a
    structurally sound completed full phase whose current roster has an
    append-only new item or a stale live/revision binding is reported there.
    Completion and Guide callers omit the list and therefore stay strict.
    """
    errors = _phase_evidence_shape_errors(state)
    if errors:
        return errors
    manifest_state, manifest_reason = phase_manifest_status(ws)
    if manifest_state == "broken":
        errors.append("v4.1 manifest 处于 partial/broken 状态，阶段门禁拒绝 fail-open：" + manifest_reason)
    evidence = state.get("phase_evidence") or {}
    asset_policy, asset_policy_error = _completion_asset_policy(ws)
    for phase, record in evidence.items():
        status = record.get("status")
        recoverable_notebook_refs = set()
        if status:
            recoverable_step_problems = set()
            reopenable_pending_ids = set()
            if interaction_style_full_route(state):
                teaching, teaching_error = _read_json_value(
                    os.path.join(ws, "references", "teaching_examples.json"))
                if not teaching_error:
                    binding_status = _step_teaching_binding_status(
                        ws, state, int(phase), teaching)
                    direct_recoverable = {
                        "教学事件 %s：%s" % (ident, problem)
                        for ident, problem
                        in binding_status["repairable_binding_problems"].items()
                    }
                    fatal_step_problems = [
                        problem for problem in binding_status["problems"]
                        if problem not in direct_recoverable
                    ]
                    baseline_problems = _step_roster_baseline_problems(
                        ws, int(phase), teaching)
                    if not fatal_step_problems and not baseline_problems:
                        reopenable_pending_ids = {
                            item["id"] for item
                            in binding_status["pending_items"]
                        }
                    if reopenable_pending_ids:
                        recoverable_step_problems = direct_recoverable
                        stale_binding_ids = set(
                            binding_status["repairable_binding_problems"])
                        recoverable_notebook_refs = {
                            binding.get("notebook_ref")
                            for binding in (
                                record.get("teaching_example_bindings") or []
                            )
                            if isinstance(binding, dict)
                            and binding.get("id") in stale_binding_ids
                            and isinstance(binding.get("notebook_ref"), str)
                        }
            phase_record_problems = _phase_record_problems(
                ws, state, int(phase), status)
            repairable_phase_problems = set(recoverable_step_problems)
            if reopenable_pending_ids:
                for notebook_ref in recoverable_notebook_refs:
                    notebook_problem = phase_evidence_ref_error(
                        ws, "notebook", notebook_ref,
                        asset_policy, asset_policy_error)
                    if notebook_problem:
                        repairable_phase_problems.add(
                            "notebook 引用无效：%s" % notebook_problem)
                missing_problem = (
                    "教学例题 evidence 未覆盖本阶段 manifest 全集: "
                    + ", ".join(sorted(reopenable_pending_ids))
                )
                if missing_problem in phase_record_problems:
                    repairable_phase_problems.add(missing_problem)
                _scope_problems, _wiki_names, chapter_keys = (
                    _phase_scope_context(ws, record, int(phase)))
                repairable_phase_problems.update(
                    _structured_study_guide_problems(
                        ws, state, chapter_keys))
            for problem in phase_record_problems:
                formatted = "phase_evidence[%s] 状态 %s：%s" % (
                    phase, status, problem)
                if (recoverable_stale is not None
                        and problem in repairable_phase_problems):
                    recoverable_stale.append(formatted)
                else:
                    errors.append(formatted)
            continue

        # A persisted binding keeps its stronger live contract after a cadence
        # or no_questions switch. Active step mode also requires the baseline.
        if (record.get("teaching_example_bindings")
                or effective_interaction_style(state) == "step_by_step"):
            teaching, teaching_error = _read_json_value(
                os.path.join(ws, "references", "teaching_examples.json"))
            if teaching_error:
                errors.append(
                    "phase_evidence[%s] teaching roster 无法读取：%s" %
                    (phase, teaching_error))
            else:
                if effective_interaction_style(state) == "step_by_step":
                    for problem in _step_roster_baseline_problems(
                            ws, int(phase), teaching):
                        errors.append(
                            "phase_evidence[%s] step_by_step roster：%s" %
                            (phase, problem))
                binding_status = _step_teaching_binding_status(
                    ws, state, int(phase), teaching)
                direct_recoverable = {
                    "教学事件 %s：%s" % (ident, problem)
                    for ident, problem
                    in binding_status["repairable_binding_problems"].items()
                }
                stale_binding_ids = set(
                    binding_status["repairable_binding_problems"])
                recoverable_notebook_refs = {
                    binding.get("notebook_ref")
                    for binding in (record.get("teaching_example_bindings") or [])
                    if isinstance(binding, dict)
                    and binding.get("id") in stale_binding_ids
                    and isinstance(binding.get("notebook_ref"), str)
                }
                for problem in binding_status["problems"]:
                    formatted = (
                        "phase_evidence[%s] teaching evidence：%s"
                        % (phase, problem))
                    if (recoverable_stale is not None
                            and problem in direct_recoverable):
                        recoverable_stale.append(formatted)
                    else:
                        errors.append(formatted)
        for field in PHASE_EVIDENCE_FIELDS:
            for ref in record.get(field) or []:
                problem = phase_evidence_ref_error(
                    ws, field, ref, asset_policy, asset_policy_error)
                if problem:
                    formatted = "phase_evidence[%s].%s 引用无效：%s" % (
                        phase, field, problem)
                    if (recoverable_stale is not None
                            and field == "notebook"
                            and ref in recoverable_notebook_refs):
                        recoverable_stale.append(formatted)
                    else:
                        errors.append(formatted)
    if enforce_manifest_gate and manifest_state == "ready":
        for row in state.get("phase_checklist") or []:
            phase = phase_number_from_check(row.get("text") or "")
            if phase is None or not row.get("done"):
                continue
            record = evidence.get(str(phase))
            if not isinstance(record, dict) or record.get("status") not in PHASE_EVIDENCE_STATUSES:
                errors.append("phase_evidence[%d] 缺完成状态：新版 manifest 工作区不能只靠 done=true 完成阶段"
                              % phase)
    return errors


# ---------------- state → md (generated view; keeps validator/T4-parseable shape) ----------------

def _md_cell(v, default="-"):
    """One Markdown table cell / bullet payload: | 会断列、换行会把一行拆成多行——都必须归一，
    否则生成视图的行结构被破坏（进度面板与 md 回退解析都读不回这行）。"""
    s = str(v) if v not in (None, "") else default
    return re.sub(r"\s*[\r\n]+\s*", " ", s).replace("|", "/").strip()


def render_md(state):
    def _tbl(rows, headers, default_status):
        out = ["| " + " | ".join(headers) + " |",
               "| " + " | ".join(":---" for _ in headers) + " |"]
        if not rows:
            out.append("| " + " | ".join("（暂无）" if i == 0 else "-" for i in range(len(headers))) + " |")
        for r in rows:
            rid = ("[#%s]" % _md_cell(r["id"])) if r.get("id") else "-"
            # state 存代号（to_review…）——视图渲染中文显示词；自由词状态原样透传
            out.append("| %s | %s | %s | %s |" % (rid, _md_cell(r.get("chapter")),
                                                  _md_cell(r.get("note"), default=""),
                                                  _md_cell(_disp("row", r.get("status")),
                                                           default=default_status)))
        return "\n".join(out)

    lines = [
        "# 🎯 复习进度与错题档案（由 study_state.json 自动生成——请勿手改本文件，改动会在下次渲染时丢失）",
        "",
        "## ⏱️ 当前复习断点",
        "* **当前进行阶段**：阶段 %d" % state["current_phase"],
        "* **范围/模式**：%s ｜ %s ｜ 时间预算 %s" % (state.get("scope") or "混合题池",
                                                     _disp("mode", state.get("mode")) or "未设定",
                                                     _disp("tier", state.get("time_budget")) or "未设定"),
        "* **最后更新时间**：%s" % (state.get("last_updated") or "-"),
    ]
    if state.get("language"):
        # 语言偏好也要能从生成视图迁回来——init --force 恢复路径不丢 set --language
        lines.append("* **语言偏好**：%s" % _md_cell(_disp("lang", state["language"]), default=""))
    # Always render the resource-output mode so init --force can round-trip the explicit choice.
    # Old/missing/unknown state is effective chat-only; unknown raw values remain visible for audit.
    artifact = state.get("artifact_mode")
    lines.append("* **输出资源模式**：%s" % _md_cell(
        _disp("artifact", artifact) if artifact else _disp("artifact", "chat"), default=""))
    lines.append("")
    if state.get("phase_checklist"):
        # 打卡区随 state 一起渲染回来——迁移绝不丢每阶段完成状态；勾选走 set-check 官方路径
        lines += ["## 📊 知识点打卡状态",
                  "\n".join("- [%s] %s" % ("x" if r.get("done") else " ", _md_cell(r.get("text"), default=""))
                            for r in state["phase_checklist"]), ""]
    if state.get("phase_evidence"):
        status_words = {
            "covered_unverified": "已覆盖但未验证",
            "verified": "已验证",
            None: "证据收集中",
        }
        evidence_lines = []
        for phase in sorted(state["phase_evidence"], key=lambda x: int(x)):
            record = state["phase_evidence"][phase]
            counts = ", ".join("%s=%d" % (field, len(record.get(field) or []))
                               for field in PHASE_EVIDENCE_FIELDS)
            evidence_lines.append("- 阶段 %s：%s（%s）" %
                                  (phase, status_words.get(record.get("status"), record.get("status")), counts))
        lines += ["## 🧾 阶段证据状态", "\n".join(evidence_lines), ""]
    lines += [
        "## ❌ 错题档案记录",
        _tbl(state["mistake_archive"], ("错题ID", "关联章节", "错误原因分析", "状态"), "待复盘"),
        "",
        "## 💡 概念疑难点记录",
        _tbl(state["confusion_log"], ("疑难ID", "关联章节", "疑难点", "状态"), "待回顾"),
        "",
    ]
    # A6：知识点窗口（3-7 天/>7 天档的窗口系统落点）——窗口内默认还会、窗口外需回问或实测
    if state.get("knowledge_window"):
        rows = ["| 知识点 | 关联章节 | 状态 | 备注 |", "| --- | --- | --- | --- |"]
        for r in state["knowledge_window"]:
            rows.append("| %s | %s | %s | %s |" % (
                _md_cell(r.get("point"), default=""), _md_cell(r.get("chapter"), default="-"),
                _md_cell(_disp("window", r.get("status")), default="在窗口"),
                _md_cell(r.get("note"), default="")))
        lines += ["## 🪟 知识点窗口（近期掌握追踪）", "\n".join(rows), ""]
    if state.get("preferences"):
        lines += ["## ⚙️ 偏好（讲解风格等）",
                  # 键值同样过 _md_cell——带换行的偏好值会把假标题/假档案行注入生成视图，
                  # 再被 init --force 当真行迁回 state
                  "\n".join("- %s: %s" % (_md_cell(k, default=""), _md_cell(v, default=""))
                            for k, v in sorted(state["preferences"].items())), ""]
    return "\n".join(lines)


# ---------------- IO (explicit UTF-8, atomic, fail-loud) ----------------

def load_state(ws):
    path = os.path.join(ws, STATE_NAME)
    if os.path.islink(path):
        # A4 事实源绝不允许是符号链接：isfile 会跟随链接读工作区外的 JSON，悬空链接又会被当
        # 「无 state」绕过降级——两种都必须 fail-loud
        _die("study_state.json 是符号链接——事实源可能指向工作区外，拒绝读取（请替换为真实文件）", 1)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except OSError as e:
        _die("study_state.json 无法读取（%s）——本次读取/保存未成功，请告知用户并检查文件权限" % e, 1)
    except UnicodeDecodeError as e:
        _die("study_state.json 不是 UTF-8（%s）——状态文件已损坏，请从 study_progress.md 重新 init" % e, 1)
    except ValueError as e:
        _die("study_state.json 不是合法 JSON: %s" % e, 1)
    if not isinstance(st, dict):
        _die("study_state.json 顶层必须是对象", 1)
    return st


def save(ws, state, note):
    """Atomic UTF-8 write of BOTH the state json and the rendered md. Any failure is fail-loud."""
    state["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # two-phase: stage BOTH tmp files first, then replace md, then state (the source of truth) LAST —
    # a failure at any step leaves the truth un-advanced (worst case md is ahead; `render` repairs it)
    plan = ((MD_NAME, render_md(state)),
            (STATE_NAME, json.dumps(state, ensure_ascii=False, indent=2)))
    for name, _content in plan:
        target = os.path.join(ws, name)
        if os.path.lexists(target) and not os.path.islink(target) and not os.path.isfile(target):
            # 目标是目录/特殊文件时，md 会先被 replace、随后 state 的 replace 才失败——
            # 生成视图被打掉而事实源没写成，render 都救不回来。写任何 tmp 前先整体拒绝
            _die("%s 已存在但不是常规文件（目录/特殊文件）——拒绝写入，请先手动清理" % target, 1)
        tmp = target + ".tmp"
        if os.path.islink(tmp):
            # 可预测的 tmp 路径被替换成符号链接时，普通 open("w") 会顺着链接改写工作区外的任意文件
            # ——先整体拒绝（不创建任何 tmp），要求人工清理
            _die("检测到符号链接临时文件 %s——可能指向工作区外，拒绝写入（请手动清理后重试）" % tmp, 1)
    tmps = []
    try:
        for name, content in plan:
            tmp = os.path.join(ws, name) + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)                      # 上次崩溃残留的普通 tmp——清掉重建
            # O_EXCL 独占创建：文件必须不存在才成功，绝不跟随同名链接/复用旧文件
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)   # 数据文件不带可执行位
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            tmps.append((tmp, os.path.join(ws, name)))
        for tmp, path in tmps:
            os.replace(tmp, path)
    except OSError as e:
        for tmp, _ in tmps:
            try:
                os.remove(tmp)
            except OSError:
                pass
        _die("写入进度失败：%s——事实源 study_state.json 未被超前破坏；若 md 已先行更新，跑 render 即可"
             "恢复一致。请告知用户（绝不静默继续）" % e, 1)
    print("[+] %s（state + md 已同步更新）" % note)


# ---------------- commands ----------------

def cmd_init(ws, args):
    path = os.path.join(ws, STATE_NAME)
    if os.path.isfile(path) and not args.force:
        _die("study_state.json 已存在（init 幂等保护）；确要从 md 重建请加 --force")
    preserved_phase_evidence = {}
    if os.path.isfile(path) and args.force:
        old = load_state(ws)
        shape_errors = _phase_evidence_shape_errors(old)
        if shape_errors:
            _die("study_state.json 损坏：" + shape_errors[0], 1)
        preserved_phase_evidence = old.get("phase_evidence") or {}
    md_path = os.path.join(ws, MD_NAME)
    if os.path.islink(md_path):
        # isfile 会跟随链接把工作区外的文件迁进事实源——与 validator 同口径先拒
        _die("study_progress.md 是符号链接——可能指向工作区外，拒绝迁移（请替换为真实文件）", 1)
    phase, mistakes, confusions = 1, [], []
    if os.path.isfile(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            _die("study_progress.md 无法读取（%s）——迁移未进行，请检查文件权限后重试" % e, 1)
        except UnicodeDecodeError as e:
            _die("study_progress.md 不是 UTF-8（%s）——这正是结构化状态要根治的乱码；"
                 "请先把 md 转存为 UTF-8 再 init（不要猜编码静默迁移）" % e, 1)
        phase, mistakes, confusions, checklist, window, prefs = parse_md(text)
        if phase < 1:
            # 写入 0/负数会让下一次官方更新在 _require_state 处拒跑——迁移绝不产出损坏 state
            _die("study_progress.md 的当前阶段 %d 非法（须 ≥1）——请先修正 md 再 init" % phase, 1)
        plan = _plan_phases(ws)
        if plan and phase not in plan:
            # 与 cmd_set 同一守卫：迁移也不能产出 validator 拒收、下次会话恢复不进去的断点
            _die("study_progress.md 的当前阶段 %d 不在 study_plan.md 的阶段列表 %s 中——"
                 "请先修正 md/计划再 init" % (phase, sorted(plan)), 1)
    else:
        checklist, window, prefs = [], [], {}
        plan0 = _plan_phases(ws)
        if plan0 and phase not in plan0:
            phase = min(plan0)      # 空白初始化也要落在计划内——默认 1 可能不在阶段列表里
    st = default_state()
    st.update({"current_phase": phase, "mistake_archive": mistakes, "confusion_log": confusions,
               "phase_checklist": checklist, "knowledge_window": window,
               "phase_evidence": preserved_phase_evidence})
    # A6：迁移时把旧四模式归一到新模式（panic/sprint/…）、时间宽裕度归一到 4 档；未知值保留
    if prefs.get("mode"):
        cmode, mig_tier, _w = _normalize_mode(prefs["mode"])
        prefs["mode"] = cmode
        if _w:
            sys.stderr.write("update_progress[warn]: " + _w + "\n")
        if mig_tier and not prefs.get("time_budget"):
            prefs["time_budget"] = mig_tier
    if prefs.get("time_budget"):
        prefs["time_budget"], _tw = _normalize_tier(prefs["time_budget"])
        if _tw:
            sys.stderr.write("update_progress[warn]: " + _tw + "\n")
    if prefs.get("language"):
        prefs["language"], _lw = _normalize_language(prefs["language"])
        if _lw:
            sys.stderr.write("update_progress[warn]: " + _lw + "\n")
    if prefs.get("artifact_mode"):
        prefs["artifact_mode"], _aw = _normalize_artifact_mode(prefs["artifact_mode"])
        if _aw:
            sys.stderr.write("update_progress[warn]: " + _aw + "\n")
    for k in ("scope", "mode", "time_budget", "language", "artifact_mode"):
        if prefs.get(k):
            st[k] = prefs[k]
    if prefs.get("preferences"):                    # ⚙️ 偏好区（讲解风格等）同理——恢复路径不丢偏好
        st["preferences"].update(prefs["preferences"])
    _validated_interaction_style(st["preferences"].get("interaction_style", "batch"))
    st["preferences"].setdefault("interaction_style", "batch")
    save(ws, st, "init：从 %s 迁移（阶段 %d，错题 %d，疑难 %d，打卡 %d）"
         % (MD_NAME if os.path.isfile(md_path) else "空白", phase, len(mistakes), len(confusions),
            len(checklist)))
    return 0


def _migrate_enums(ws, st):
    """v4 枚举迁移：state 里的旧中文显示词/旧四模式 → 语言中性代号（原地改，返回是否有变化）。
    首次发生迁移时把旧 state 备份为 study_state.json.v3bak（已存在则不覆盖）——用户进度永不裸奔。
    未知自由词一律原样保留（canon_* 的透传语义），绝不静默改写。
    形态坏掉的字段（非字符串枚举/非列表档案）这里一律跳过——本函数跑在 _require_state 的
    形态校验【之前】，在坏 state 上迭代/strip 会裸 Traceback，把后面的 fail-loud _die 短路掉。"""
    changed = False
    if isinstance(st.get("mode"), str) and st["mode"]:
        code, mig_tier, _w = i18n.canon_mode(st["mode"])
        if code != st["mode"]:
            st["mode"], changed = code, True
        if mig_tier and not st.get("time_budget"):
            st["time_budget"], changed = mig_tier, True
    for field, canon in (("time_budget", lambda v: i18n.canon_tier(v)[0]),
                         ("language", lambda v: i18n.canon_language(v)[0]),
                         ("artifact_mode", lambda v: i18n.canon_artifact_mode(v)[0])):
        v = st.get(field)
        if isinstance(v, str) and v:
            c = canon(v)
            if c != v:
                st[field], changed = c, True
    rows = st.get("knowledge_window")
    for row in (rows if isinstance(rows, list) else []):
        if isinstance(row, dict) and isinstance(row.get("status"), str):
            c = i18n.canon_window_status(row["status"])
            if c != row["status"]:
                row["status"], changed = c, True
    for field in ("mistake_archive", "confusion_log"):
        rows = st.get(field)
        for row in (rows if isinstance(rows, list) else []):
            if isinstance(row, dict) and isinstance(row.get("status"), str):
                c = i18n.canon_row_status(row["status"])
                if c != row["status"]:
                    row["status"], changed = c, True
    if changed:
        src = os.path.join(ws, STATE_NAME)
        bak = src + ".v3bak"
        # 断链的 .v3bak 符号链接会让 exists() 判 False、随后 open("w") 顺着链接把学生进度
        # 写到工作区外（Codex r4）——与 save() 的 tmp 同款纪律：islink 先整体拒绝（不写任何
        # 字节，要求人工清理）；「已备份过」的跳过判断用 lexists（链接也算占位，绝不顺链写）
        if os.path.islink(bak):
            _die("检测到符号链接备份文件 %s——可能指向工作区外，拒绝写入（请手动清理后重试）"
                 % bak, 1)
        if os.path.isfile(src) and not os.path.lexists(bak):
            try:
                with open(src, "r", encoding="utf-8") as f:
                    old = f.read()
                with open(bak, "w", encoding="utf-8", newline="\n") as f:
                    f.write(old)
                sys.stderr.write("update_progress[migrate]: 检测到旧版词表，已归一为 v4 代号；"
                                 "原文件备份在 %s\n" % bak)
            except OSError:
                pass    # 备份失败不拦截主流程（迁移本身无损：显示词↔代号一一对应）
    return changed


def _require_state(ws, repairing_phase=False, repairing_interaction_style=False):
    st = load_state(ws)
    if st is None:
        _die("尚无 study_state.json——先跑 `update_progress.py --workspace <ws> init` 迁移")
    _migrate_enums(ws, st)   # v4：旧中文枚举 → canonical 代号（下一次 save 固化）
    # 官方持久化路径必须 fail-loud：半写/手改导致的坏形态要在【变更前】报清楚，不能改到一半 Traceback
    cp = st.get("current_phase")
    if not (isinstance(cp, int) and not isinstance(cp, bool) and cp >= 1):
        _die("study_state.json 损坏：current_phase 必须是 ≥1 的整数，当前 %r——请修复 state "
             "或 init --force 从 md 重建" % cp, 1)
    if not repairing_phase:
        # 任何变更都不许把「已不在计划里的阶段」再保存一次（手改/计划回滚后的陈旧断点）——
        # 只有 set --phase（修复路径，自己校验新值）豁免，否则连修复命令都进不来
        plan = _plan_phases(ws)
        if plan and cp not in plan:
            _die("study_state.json 的 current_phase=%d 已不在 study_plan.md 的阶段列表 %s 中——"
                 "先用 `set --phase <计划内阶段>` 修正断点再做其他更新" % (cp, sorted(plan)), 1)
    for field in ("mistake_archive", "confusion_log", "phase_checklist", "knowledge_window"):
        v = st.get(field)
        if v is None:
            st[field] = []                             # 旧 schema 兼容：缺字段按空列表补齐
        elif not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
            _die("study_state.json 损坏：%s 必须是对象数组，当前 %s——请修复 state "
                 "或 init --force 从 md 重建" % (field, type(v).__name__), 1)
    if st.get("phase_evidence") is None:
        st["phase_evidence"] = {}                    # additive schema：旧 state 无字段时无损补空
    shape_errors = _phase_evidence_shape_errors(st)
    if shape_errors:
        _die("study_state.json 损坏：" + shape_errors[0], 1)
    # 行内字段形态也要在变更/渲染前把关——render 对 note/text 调字符串方法，坏类型不能等到中途崩栈
    for field in ("mistake_archive", "confusion_log"):
        for x in st[field]:
            if not isinstance(x.get("note"), str) or not x["note"].strip():
                _die("study_state.json 损坏：%s 的行缺非空字符串 note: %r" % (field, x), 1)
            for k in ("id", "status"):
                if x.get(k) is not None and not isinstance(x[k], str):
                    _die("study_state.json 损坏：%s 行的 %s 必须是字符串或省略: %r" % (field, k, x), 1)
    for x in st["phase_checklist"]:
        if not isinstance(x.get("text"), str) or not x["text"].strip():
            _die("study_state.json 损坏：phase_checklist 行缺非空字符串 text: %r" % x, 1)
        if x.get("done") is not None and not isinstance(x["done"], bool):
            _die("study_state.json 损坏：phase_checklist 行的 done 必须是布尔: %r" % x, 1)
    if st.get("preferences") is None:
        st["preferences"] = {}
    elif not isinstance(st["preferences"], dict):
        _die("study_state.json 损坏：preferences 必须是对象，当前 %s"
             % type(st["preferences"]).__name__, 1)
    # Missing means batch for legacy state. Invalid stored values fail loudly;
    # aliases such as ``step-by-step`` never silently change semantics.
    raw_interaction_style = st["preferences"].get("interaction_style", "batch")
    if raw_interaction_style not in INTERACTION_STYLES and repairing_interaction_style:
        st["preferences"]["interaction_style"] = "batch"
    else:
        st["preferences"]["interaction_style"] = _validated_interaction_style(
            raw_interaction_style)
    if st.get("artifact_mode") is None:
        # Additive state field: v3 and early-v4 workspaces keep their chat-only behavior.
        st["artifact_mode"] = "chat"
    elif not isinstance(st["artifact_mode"], str):
        _die("study_state.json 损坏：artifact_mode 必须是字符串，当前 %s"
             % type(st["artifact_mode"]).__name__, 1)
    return st


def _plan_phases(ws):
    """Phase numbers listed in study_plan.md（阶段N / 第N阶段 / Phase N，与 T4 解析器同款），
    plan 缺失/无阶段时返回空集。"""
    plan_path = os.path.join(ws, "study_plan.md")
    if os.path.islink(plan_path) or (os.path.isfile(plan_path)
                                     and not os.path.realpath(plan_path).startswith(
                                         os.path.realpath(ws) + os.sep)):
        # validator 拒符号链接/逃出工作区的计划——官方变更路径不能反而信任外部计划，
        # 写出 validator 拒收、恢复不进去的断点
        _die("study_plan.md 是符号链接/经符号链接逃出工作区——阶段守卫不信任外部计划文件", 1)
    if not os.path.isfile(plan_path):
        return set()
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        # 静默当「无计划」会禁用阶段守卫，写出计划修好后才发现的坏断点——必须报错
        _die("study_plan.md 存在但无法读取/非 UTF-8（%s）——阶段校验无法进行，请先修复计划文件" % e, 1)
    nums = set()
    for ln in text.splitlines():
        h = ln.lstrip()
        # 只认结构行（标题/表格/清单）里的阶段号——散文提醒「不要进入阶段99」不是计划条目
        #（与 T4 解析器的 _is_structural 同口径）；且阶段号必须 ≥1——「阶段0」会让空白 init
        # 种下 current_phase=0 这种全工具链拒收的断点
        if not (h.startswith("#") or h.startswith("|") or re.match(r"[-*]\s", h)
                or re.match(r"\d+\s*[.)、）]", h)):     # 有序列表（1. 阶段…）也是计划条目
            continue
        for m in re.finditer(r"阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+)", ln):
            n = int(next(g for g in m.groups() if g))
            if n >= 1:
                nums.add(n)
    return nums


def _plan_phase_wikis(ws):
    """Map structural phase rows in study_plan.md to their declared references/wiki/*.md files."""
    path = os.path.join(ws, "study_plan.md")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        _die("study_plan.md 无法读取，不能校验 phase evidence 的章节归属: %s" % e, 1)
    mapping, current = {}, None
    for line in lines:
        stripped = line.lstrip()
        structural = (stripped.startswith("#") or stripped.startswith("|")
                      or bool(re.match(r"[-*]\s", stripped))
                      or bool(re.match(r"\d+\s*[.)、）]", stripped)))
        if not structural:
            continue
        matches = list(re.finditer(r"阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+)", line))
        if matches:
            current = int(next(g for g in matches[0].groups() if g))
            if current >= 1:
                mapping.setdefault(current, set())
        refs = {x.replace("\\", "/") for x in
                re.findall(r"references[\\/]wiki[\\/][\w.\-]+\.md", line, re.I)}
        if current is not None and refs:
            mapping.setdefault(current, set()).update(refs)
    return mapping


def cmd_set(ws, args):
    # 提供 --phase 时豁免陈旧断点检查（这是修复路径；新值下面自行对照计划校验）
    pref_repairs_style = any(
        "=" in value and value.split("=", 1)[0].strip() == "interaction_style"
        for value in (args.pref or []))
    st = _require_state(
        ws,
        repairing_phase=args.phase is not None,
        repairing_interaction_style=(
            args.interaction_style is not None or pref_repairs_style),
    )
    changed = []
    if args.phase is not None:
        if args.phase < 1:
            _die("--phase 必须 ≥ 1")
        plan = _plan_phases(ws)
        if plan and args.phase not in plan:
            # validator 事后能查出来，但官方变更路径必须【写之前】就拒绝——写入不存在的阶段
            # 会让下一次会话恢复进不存在的阶段/wiki
            _die("--phase %d 不在 study_plan.md 的阶段列表 %s 中——先改计划再设阶段"
                 % (args.phase, sorted(plan)))
        st["current_phase"] = args.phase
        changed.append("phase=%d" % args.phase)
    # A6：mode 走 canonical 归一化（旧四模式迁移 + 未知值保留并警告，绝不静默改写）
    if args.mode is not None:
        if not args.mode:
            st["mode"] = None
            changed.append("mode=（清除）")
        else:
            cmode, mig_tier, warn = _normalize_mode(args.mode)
            st["mode"] = cmode                                # state 存代号
            changed.append("mode=%s" % _disp("mode", cmode))  # 回执渲染中文显示词
            if warn:
                sys.stderr.write("update_progress[warn]: " + warn + "\n")
            # 旧模式迁移带出的时间档：本次未显式 --time-budget 就落它——旧模式名自带紧迫度语义，
            # panic 换到 sprint 必须把 ≤1天 刷成 1-3天，不能留旧迁移值让节奏判定错档（仅显式 --time-budget 才不覆盖）
            if mig_tier and args.time_budget is None:
                st["time_budget"] = mig_tier
                changed.append("time_budget=%s（旧模式迁移带出）" % _disp("tier", mig_tier))
    # A6：time_budget 归一化到 4 个 canonical 档
    if args.time_budget is not None:
        if not args.time_budget:
            st["time_budget"] = None
            changed.append("time_budget=（清除）")
        else:
            ctier, twarn = _normalize_tier(args.time_budget)
            st["time_budget"] = ctier
            changed.append("time_budget=%s" % _disp("tier", ctier))
            if twarn:
                sys.stderr.write("update_progress[warn]: " + twarn + "\n")
    if args.scope is not None:
        st["scope"] = args.scope or None
        changed.append("scope=%s" % (args.scope or "（清除）"))
    if args.language is not None:
        if not args.language:
            st["language"] = None
            changed.append("language=（清除）")
        else:
            clang, lwarn = _normalize_language(args.language)
            st["language"] = clang
            changed.append("language=%s" % _disp("lang", clang))
            if lwarn:
                sys.stderr.write("update_progress[warn]: " + lwarn + "\n")
    if args.artifact_mode is not None:
        if not args.artifact_mode:
            st["artifact_mode"] = "chat"
            changed.append("artifact_mode=%s（安全默认）" % _disp("artifact", "chat"))
        else:
            artifact_mode, awarn = _normalize_artifact_mode(args.artifact_mode)
            st["artifact_mode"] = artifact_mode
            changed.append("artifact_mode=%s" % _disp("artifact", artifact_mode))
            if awarn:
                sys.stderr.write("update_progress[warn]: " + awarn + "\n")
    if args.interaction_style is not None:
        st.setdefault("preferences", {})["interaction_style"] = (
            _validated_interaction_style(args.interaction_style))
        changed.append("interaction_style=%s" % args.interaction_style)
    for kv in (args.pref or []):
        if "=" not in kv:
            _die("--pref 需要 key=value 形式，当前 %r" % kv)
        k, v = kv.split("=", 1)
        key, value = k.strip(), v.strip()
        if not key:
            _die("--pref key must not be empty")
        if key == "interaction_style":
            value = _validated_interaction_style(value)
        st.setdefault("preferences", {})[key] = value
        changed.append("pref %s" % key)
    if not changed:
        _die("set 没有任何改动参数（--phase/--scope/--mode/--time-budget/--language/--artifact-mode/--interaction-style/--pref）")
    save(ws, st, "set：" + "、".join(changed))
    return 0


# 知识点窗口状态代号——in_window（近期讲过、默认还会）/ out_window（需回问或实测）/ verified（做题验证过）。
# CLI 同时接受代号与中文显示词（在窗口/窗口外/已实测），持久化只存代号。
_WINDOW_STATUSES = i18n.WINDOW_STATUSES


def _window_status_hint():
    return "/".join("%s（%s）" % (c, _disp("window", c)) for c in _WINDOW_STATUSES)


def cmd_window_add(ws, args):
    st = _require_state(ws)
    point = (args.point or "").strip()
    if not point:
        _die("--point 不能为空")
    status = i18n.canon_window_status(args.status or "in_window")
    if status not in _WINDOW_STATUSES:
        _die("--status 必须是 %s，当前 %r" % (_window_status_hint(), (args.status or "").strip()))
    win = st.setdefault("knowledge_window", [])
    # 同名知识点视为同一条：更新其状态/备注，不重复登记——窗口进出是状态迁移不是加行。
    # 章节相容即同一点：任一方未标章节、或两方章节相等 → 命中；先松登记再补章节的常见流程会
    # 回填章节而非产生 null-章节孤儿行（只有两方都标了且不同章，才是真正不同的同名点）。
    ac = args.chapter
    matches = [row for row in win
               if isinstance(row, dict) and row.get("point") == point
               and (row.get("chapter") is None or ac is None or str(row.get("chapter")) == str(ac))]
    if len(matches) > 1:
        # 同名点已分布在多章、本次又没带 --chapter：静默只改第一条会让其余章状态错位（与 window-set-status
        # 同一守卫）——fail-loud 要求精确定位，别私自替用户挑一条改
        chs = "、".join(sorted(str(r.get("chapter")) for r in matches))
        _die("知识点「%s」在多个章节（%s）都有登记——请加 --chapter 精确定位，避免误改其他章的同名点" % (point, chs))
    if matches:
        row = matches[0]
        row["status"] = status
        if args.note:
            row["note"] = args.note.strip()
        if ac is not None:                           # 后补的具体章节回填到该点（不新增行）
            row["chapter"] = ac
        save(ws, st, "window：更新「%s」→%s" % (point, _disp("window", status)))
        return 0
    win.append({"point": point, "chapter": ac, "status": status, "note": (args.note or "").strip()})
    save(ws, st, "window：登记「%s」（%s）" % (point, _disp("window", status)))
    return 0


def cmd_window_set_status(ws, args):
    st = _require_state(ws)
    status = i18n.canon_window_status(args.status or "")
    if status not in _WINDOW_STATUSES:
        _die("--status 必须是 %s，当前 %r" % (_window_status_hint(), (args.status or "").strip()))
    win = st.get("knowledge_window") or []
    hits = []
    if args.index is not None:
        if not (1 <= args.index <= len(win)):
            _die("--index 越界：窗口共 %d 条，index=%d" % (len(win), args.index))
        hits = [win[args.index - 1]]
    elif args.point:
        pt = args.point.strip()
        hits = [r for r in win if isinstance(r, dict) and r.get("point") == pt]
        if args.chapter is not None:                 # 指定章节时只命中该章的那条
            hits = [r for r in hits if str(r.get("chapter")) == str(args.chapter)]
        if not hits:
            _die("找不到知识点「%s」%s——先用 window-add 登记"
                 % (pt, ("（第%s章）" % args.chapter) if args.chapter is not None else ""))
        if len(hits) > 1:
            # 同名点分布在多章：不带 --chapter 定位会一次改错所有章的状态（把第5章点也标成已实测）
            chs = "、".join(sorted(str(r.get("chapter")) for r in hits))
            _die("知识点「%s」在多个章节（%s）都有登记——请加 --chapter 精确定位，避免误改其他章的同名点"
                 % (pt, chs))
    else:
        _die("window-set-status 需要 --point 或 --index 定位")
    for r in hits:
        r["status"] = status
    save(ws, st, "window：%d 条 →%s" % (len(hits), _disp("window", status)))
    return 0


def cmd_add(ws, args, field, label):
    st = _require_state(ws)
    if not (args.note or "").strip():
        _die("--note 不能为空")
    # 错题走 to_review→corrected/reviewed，疑难走 to_revisit→revisited——初始状态按目标契约给
    # 代号（复盘流按代号捞行；生成视图渲染回中文显示词）
    row = {"id": args.id, "chapter": args.chapter, "note": args.note.strip(),
           "status": "to_revisit" if field == "confusion_log" else "to_review"}
    st[field].append(row)
    save(ws, st, "%s +1（共 %d 条）" % (label, len(st[field])))
    return 0


def cmd_set_status(ws, args, field, label):
    """P1: the contract forbids hand-editing md, so status transitions (待复盘→已复盘/已解决) MUST have
    an official path too — locate by [#id] (all matching rows) or 1-based --index."""
    st = _require_state(ws)
    rows = st.get(field) or []
    if args.id is not None:
        hits = [r for r in rows if r.get("id") == args.id]
    elif args.index is not None:
        if not 1 <= args.index <= len(rows):
            _die("--index 超界（1..%d）" % len(rows))
        hits = [rows[args.index - 1]]
    else:
        _die("set-status 需要 --id 或 --index 定位行")
    if not hits:
        _die("没找到匹配行（%s id=%r）" % (label, args.id))
    status = i18n.canon_row_status(args.status)   # 已知词表归代号；未知自由词照旧透传
    for r in hits:
        r["status"] = status
    save(ws, st, "%s 状态更新 ×%d → %s" % (label, len(hits), _disp("row", status)))
    return 0


def _phase_check_row(state, phase):
    hits = [row for row in state.get("phase_checklist") or []
            if phase_number_from_check(row.get("text") or "") == phase]
    if not hits:
        _die("phase_checklist 中找不到阶段 %d；先修复进度模板再完成阶段" % phase)
    if len(hits) > 1:
        _die("phase_checklist 中阶段 %d 有 %d 行，无法确定要完成哪一行" % (phase, len(hits)))
    return hits[0]


def _phase_record_problems(ws, state, phase, status):
    record = (state.get("phase_evidence") or {}).get(str(phase))
    if not isinstance(record, dict):
        return ["缺 phase_evidence[%d]；先逐项运行 record-phase-evidence" % phase]
    problems = []
    asset_policy, asset_policy_error = _completion_asset_policy(ws)
    manifest_state, manifest_reason = phase_manifest_status(ws)
    if manifest_state == "broken":
        problems.append("v4.1 manifest 处于 partial/broken 状态：%s" % manifest_reason)
    for field in PHASE_EVIDENCE_FIELDS:
        for ref in record.get(field) or []:
            problem = phase_evidence_ref_error(
                ws, field, ref, asset_policy, asset_policy_error)
            if problem:
                problems.append("%s 引用无效：%s" % (field, problem))
    content_problems, teaching_required, chapter_keys = _phase_manifest_content(
        ws, state, record, phase)
    problems.extend(content_problems)
    problems.extend(_structured_study_guide_problems(ws, state, chapter_keys))
    problems.extend(_phase_completion_problems(record, status, teaching_required=teaching_required))
    if status == "verified" and _no_questions_preference(state):
        problems.append("preferences.no_questions=true 时不能标为 verified；阶段上限是 covered_unverified")
    return problems


def _complete_phase_in_state(ws, state, phase, status, row=None):
    problems = _phase_record_problems(ws, state, phase, status)
    if problems:
        _die("阶段 %d 无法完成为 %s：%s" % (phase, status, "；".join(problems)))
    record = state["phase_evidence"][str(phase)]
    record["status"] = status
    record["completed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    (row or _phase_check_row(state, phase))["done"] = True


def _teaching_manifest_item_sha256(item):
    try:
        payload = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return None, "teaching manifest item 不是严格 JSON：%s" % exc
    return hashlib.sha256(payload).hexdigest(), None


def _teaching_example_notebook_snapshot(ws, phase, example_id, notebook_ref):
    """Return ``(snapshot, problem, recoverable)`` for one notebook binding.

    Missing/replaced entry content is recoverable by writing a fresh marked
    walkthrough. Unsafe topology, invalid UTF-8/Markdown, or an impossible
    parsed block is structural and must keep mount validation blocked.
    """

    expected_rel = "notebook/ch%02d.md" % phase
    if (
        not isinstance(notebook_ref, str)
        or notebook_ref != notebook_ref.strip()
        or notebook_ref.count("#") != 1
        or any(char in notebook_ref for char in ("\x00", "\r", "\n"))
    ):
        return None, "notebook binding 必须是规范的单一 path#anchor", False
    rel, raw_fragment = notebook_ref.split("#", 1)
    fragment = unquote(raw_fragment)
    if (
        rel != expected_rel
        or not fragment
        or any(char in fragment for char in ("\x00", "\r", "\n"))
    ):
        return None, (
            "record-taught-example 的 notebook 必须是当前章 %s#<anchor>"
            % expected_rel), False
    notebook_dir = os.path.join(ws, "notebook")
    full = os.path.join(ws, *rel.split("/"))
    if os.path.lexists(notebook_dir) and _is_link_or_reparse(notebook_dir):
        return None, "workspace notebook 目录不得是 link/reparse point", False
    if os.path.lexists(notebook_dir) and not os.path.isdir(notebook_dir):
        return None, "workspace notebook 目标存在但不是目录", False
    if os.path.lexists(full) and _is_link_or_reparse(full):
        return None, "教学 notebook 文件不得是 link/reparse point", False
    workspace_real = os.path.normcase(os.path.realpath(ws))
    full_real = os.path.normcase(os.path.realpath(full))
    try:
        contained = os.path.commonpath((workspace_real, full_real)) == workspace_real
    except ValueError:
        contained = False
    if not contained:
        return None, "教学 notebook 路径解析后逃出工作区", False
    if not os.path.lexists(full):
        return None, "教学 notebook 文件不存在，必须重新精讲并记录", True
    if not os.path.isfile(full):
        return None, "教学 notebook 目标不是普通文件", False
    try:
        with open(full, "r", encoding="utf-8") as stream:
            pre, blocks = _notebook.parse_chapter(stream.read())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, "record-taught-example notebook 结构不可安全读取：%s" % exc, False
    matches = [block for block, anchor in zip(blocks, _notebook.anchors_for(pre, blocks))
               if anchor == fragment]
    if len(matches) != 1:
        return None, "notebook anchor 必须唯一定位一个条目，当前命中 %d 个" % len(matches), True
    block = matches[0]
    if block.get("id") != example_id:
        return None, "notebook anchor 对应 ID %r，不是教学例题 %r" % (
            block.get("id"), example_id), True
    label, _timestamp = _notebook._block_meta(block.get("lines") or [])
    if _notebook._label_to_type().get(label) != "walkthrough":
        return None, "notebook anchor 必须对应 notebook.py walkthrough 条目", True
    if not _notebook.block_has_teaching_example_marker(block, example_id):
        return None, (
            "notebook walkthrough 缺少本题 teaching-example marker；请先用 "
            "notebook.py add-entry --type walkthrough --teaching-example 重写该条目"), True
    try:
        block_digest = _notebook.block_sha256(block)
    except ValueError as exc:
        return None, "notebook walkthrough 结构无效：%s" % exc, False
    return {
        "id": example_id,
        "notebook_ref": "%s#%s" % (rel, fragment),
        "notebook_block_sha256": block_digest,
    }, None, False


def _teaching_example_binding_structure_problem(binding, phase):
    if not isinstance(binding, dict) or set(binding) != TEACHING_EXAMPLE_BINDING_FIELDS:
        return (
            "teaching_example_bindings 条目必须精确包含 "
            "id/notebook_ref/notebook_block_sha256/manifest_item_sha256"
        )
    ident = binding.get("id")
    if stable_item_id_problem(ident):
        return "binding ID 不符合共享 notebook/Guide 稳定 ID 规范"
    notebook_ref = binding.get("notebook_ref")
    expected_prefix = "notebook/ch%02d.md#" % phase
    raw_fragment = (
        notebook_ref[len(expected_prefix):]
        if isinstance(notebook_ref, str)
        and notebook_ref.startswith(expected_prefix)
        else ""
    )
    decoded_fragment = unquote(raw_fragment)
    if (
        not isinstance(notebook_ref, str)
        or notebook_ref != notebook_ref.strip()
        or not notebook_ref.startswith(expected_prefix)
        or notebook_ref.count("#") != 1
        or not decoded_fragment
        or any(char in notebook_ref for char in ("\x00", "\r", "\n"))
        or any(char in decoded_fragment for char in ("\x00", "\r", "\n"))
    ):
        return "binding notebook_ref 必须规范定位当前章 notebook 的唯一 anchor"
    for field in ("notebook_block_sha256", "manifest_item_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(field) or "")):
            return "binding %s 必须是 lowercase SHA-256" % field
    return None


def _teaching_example_binding_problem(ws, phase, binding, item, notebook_refs):
    if binding.get("id") != item.get("id"):
        return "binding ID 与 manifest item 不一致", False
    if binding.get("notebook_ref") not in notebook_refs:
        return "binding notebook_ref 未同时出现在 phase notebook evidence", False
    manifest_digest, error = _teaching_manifest_item_sha256(item)
    if error:
        return error, False
    if binding.get("manifest_item_sha256") != manifest_digest:
        return "manifest item revision 已变化，必须重新精讲并记录", True
    snapshot, error, repairable = _teaching_example_notebook_snapshot(
        ws, phase, binding["id"], binding["notebook_ref"])
    if error:
        return error, repairable
    if snapshot["notebook_block_sha256"] != binding.get("notebook_block_sha256"):
        return "已记录的教学 walkthrough 块被改写，必须重新精讲并记录", True
    return None, False


def _step_teaching_binding_status(ws, state, phase, items):
    """Validate batch IDs plus any stronger step bindings in manifest order."""
    record = (state.get("phase_evidence") or {}).get(str(phase), {})
    manifest_problems = (
        _teaching_examples.pending_manifest_structure_problems(items)
    )
    if manifest_problems:
        return {
            "problems": manifest_problems,
            "binding_problems": {},
            "repairable_binding_problems": {},
            "completed_ids": [],
            "completed_id_set": set(),
            "valid_bound_id_set": set(),
            "pending_items": [],
            "next": None,
            "items": [],
        }
    chapter = str(phase)
    problems = []
    hits = [item for item in items
            if chapter in _teaching_examples._chapter_keys(item)]
    item_by_id = {item["id"]: item for item in hits}
    binding_problems = {}
    repairable_binding_problems = {}
    recorded = record.get("teaching_examples") or []
    if len(recorded) != len(set(recorded)):
        problems.append("teaching_examples evidence 含重复 ID")
    bindings = record.get("teaching_example_bindings") or []
    binding_by_id = {}
    binding_ids_by_ref = {}
    for binding in bindings:
        structure_problem = _teaching_example_binding_structure_problem(
            binding, phase)
        if structure_problem:
            problems.append(
                "teaching_example_bindings 结构损坏：%s" % structure_problem)
            continue
        ident = binding["id"]
        if ident in binding_by_id:
            problems.append("teaching_example_bindings 含重复 ID %s" % ident)
        else:
            binding_by_id[ident] = binding
            notebook_ref = binding["notebook_ref"]
            previous = binding_ids_by_ref.get(notebook_ref)
            if previous is not None:
                problems.append(
                    "teaching_example_bindings 的 %s 与 %s 复用同一 notebook_ref"
                    % (previous, ident))
            else:
                binding_ids_by_ref[notebook_ref] = ident
    missing_ids = set(binding_by_id) - set(recorded)
    if missing_ids:
        problems.append("teaching_example_bindings 缺对应 teaching_examples ID：%s" %
                        ", ".join(sorted(missing_ids)))
    notebook_refs = set(record.get("notebook") or [])
    unexpected = set(recorded) - set(item_by_id)
    if unexpected:
        problems.append("teaching_examples evidence 不在当前章 manifest：%s" %
                        ", ".join(sorted(unexpected)))
    # Unbound strings are legal evidence created in batch mode. Once an ID has a
    # binding, that stronger live contract remains active after cadence changes.
    valid = (set(recorded) & set(item_by_id)) - set(binding_by_id)
    valid_bound = set()
    for ident, binding in binding_by_id.items():
        item = item_by_id.get(ident)
        if item is None:
            problems.append("教学事件 ID 不在当前章 manifest：%s" % ident)
            continue
        problem, repairable = _teaching_example_binding_problem(
            ws, phase, binding, item, notebook_refs)
        if problem:
            if repairable:
                binding_problems[ident] = problem
                repairable_binding_problems[ident] = problem
                problems.append("教学事件 %s：%s" % (ident, problem))
            else:
                problems.append("教学事件 %s 结构损坏：%s" % (ident, problem))
        else:
            valid.add(ident)
            valid_bound.add(ident)
    completed_ids = [item["id"] for item in hits if item["id"] in valid]
    pending_items = [item for item in hits if item["id"] not in valid]
    return {
        "problems": problems,
        "binding_problems": binding_problems,
        "repairable_binding_problems": repairable_binding_problems,
        "completed_ids": completed_ids,
        "completed_id_set": valid,
        "valid_bound_id_set": valid_bound,
        "pending_items": pending_items,
        "next": pending_items[0] if pending_items else None,
        "items": hits,
    }


def _step_roster_baseline_problems(ws, phase, items):
    """Require every retained baseline ID to have a current teaching snapshot."""
    manifest_problems = (
        _teaching_examples.pending_manifest_structure_problems(items)
    )
    if manifest_problems:
        return manifest_problems
    path = os.path.join(ws, "references", "teaching_baseline.json")
    if not os.path.lexists(path):
        return []
    if _is_link_or_reparse(path) or not os.path.isfile(path):
        return ["references/teaching_baseline.json 必须是安全的普通文件"]
    payload, error = _read_json_value(path)
    if error:
        return ["无法读取 teaching baseline：%s" % error]
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("policy") != "append_only"):
        return ["teaching_baseline 必须是 schema_version=1 的 append_only 基线"]
    mapping = payload.get("teaching_example_ids_by_chapter")
    flat = payload.get("teaching_example_ids")
    if not isinstance(mapping, dict) or not isinstance(flat, list):
        return ["teaching_baseline 必须包含逐章映射与 ID 全集"]
    mapped = set()
    baseline_scope_by_id = {}
    problems = []
    for raw_scope, values in mapping.items():
        scope = _scope_number(raw_scope)
        if (not isinstance(raw_scope, str) or scope is None or raw_scope != scope
                or not isinstance(values, list)
                or any(stable_item_id_problem(value) is not None
                       for value in values)
                or len(values) != len(set(values))):
            problems.append("teaching_baseline 章节 %r 的 ID 映射无效" % raw_scope)
            continue
        overlap = mapped & set(values)
        if overlap:
            problems.append("teaching_baseline ID 被分配到多个章节：%s" %
                            ", ".join(sorted(overlap)))
        mapped.update(values)
        for value in values:
            baseline_scope_by_id[value] = scope
    if (any(stable_item_id_problem(value) is not None for value in flat)
            or len(flat) != len(set(flat))
            or mapped != set(flat)):
        problems.append("teaching_baseline 的逐章映射与 ID 全集不一致")
    manifest_scope_by_id = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        chapter_scope = _scope_number(item.get("chapter"))
        phase_scope = _scope_number(item.get("phase"))
        if chapter_scope and phase_scope and chapter_scope != phase_scope:
            problems.append("teaching_examples[%s] chapter/phase 冲突" % item["id"])
            continue
        actual_scope = chapter_scope or phase_scope
        if actual_scope is not None:
            manifest_scope_by_id[item["id"]] = actual_scope
    missing = sorted(set(flat) - set(manifest_scope_by_id)) if not problems else []
    if missing:
        problems.append(
            "teaching roster 缺保留基线题目 %s；quiz-only 条目不能替代 "
            "teaching_examples.json，请先恢复完整 roster" % ", ".join(missing))
    drifted = sorted(
        ident for ident, baseline_scope in baseline_scope_by_id.items()
        if ident in manifest_scope_by_id
        and manifest_scope_by_id[ident] != baseline_scope
    ) if not problems else []
    if drifted:
        problems.append(
            "teaching roster 章节漂移：基线题目在当前 teaching_examples.json 中不再属于"
            "同一 canonical chapter：%s" % ", ".join(drifted))
    return problems


def _taught_example_notebook_binding(ws, phase, example_id, notebook_ref, item):
    snapshot, error, _repairable = _teaching_example_notebook_snapshot(
        ws, phase, example_id, notebook_ref)
    if error:
        _die("拒绝记录 taught example notebook evidence：%s" % error)
    manifest_digest, error = _teaching_manifest_item_sha256(item)
    if error:
        _die("拒绝记录 taught example manifest evidence：%s" % error)
    snapshot["manifest_item_sha256"] = manifest_digest
    return snapshot


def cmd_record_taught_example(ws, args):
    """Atomically bind one explicit tutoring walkthrough to both evidence lists."""
    st = _require_state(ws)
    if effective_interaction_style(st) != "step_by_step":
        _die("record-taught-example 只用于有效的 step_by_step：必须为完整路线"
             "（该过渡版缺 processing_mode 即旧版隐式 full），且 "
             "no_questions 必须为 false")
    phase = st["current_phase"]
    example_id = (args.id or "").strip()
    example_id_problem = stable_item_id_problem(example_id)
    if example_id_problem:
        _die("--id 不符合共享 notebook/Guide 稳定 ID 规范：%s" % example_id_problem)

    items, missing = _teaching_examples.load_manifest(ws)
    if missing:
        _die("record-taught-example 需要 references/teaching_examples.json")
    _teaching_examples._validate_pending_scopes(items)
    chapter = str(phase)
    matches = [item for item in items
               if item.get("id") == example_id
               and chapter in _teaching_examples._chapter_keys(item)]
    if len(matches) != 1:
        _die("教学例题 %r 必须在 current_phase=%d 的 manifest 中唯一存在，当前命中 %d 个"
             % (example_id, phase, len(matches)))

    baseline_problems = _step_roster_baseline_problems(ws, phase, items)
    if baseline_problems:
        _die(baseline_problems[0])
    status = _step_teaching_binding_status(ws, st, phase, items)
    stale_messages = {
        "教学事件 %s：%s" % (ident, problem)
        for ident, problem in status["repairable_binding_problems"].items()
    }
    blocking_problems = [
        problem for problem in status["problems"]
        if problem not in stale_messages
    ]
    if blocking_problems:
        _die("现有 step_by_step 教学证据无效：%s" % "；".join(blocking_problems))
    next_item = status["next"]
    if next_item is None or next_item.get("id") != example_id:
        _die("教学证据必须绑定 manifest 顺序中的当前第一道 pending %r，不能记录 %r" %
             (next_item.get("id") if next_item else None, example_id))

    notebook_binding = _taught_example_notebook_binding(
        ws, phase, example_id, (args.notebook_ref or "").strip(), matches[0])
    notebook_ref = notebook_binding["notebook_ref"]
    problem = phase_evidence_ref_error(ws, "teaching_examples", example_id)
    if problem:
        _die("拒绝记录 teaching-example evidence：%s" % problem)

    record = st.setdefault("phase_evidence", {}).setdefault(str(phase), {})
    notebooks = record.get("notebook")
    if notebooks is None:
        notebooks = record["notebook"] = []
    examples = record.get("teaching_examples")
    if examples is None:
        examples = record["teaching_examples"] = []
    if notebook_ref not in notebooks:
        notebooks.append(notebook_ref)
    if example_id not in examples:
        examples.append(example_id)
    bindings = record.get("teaching_example_bindings")
    if bindings is None:
        bindings = record["teaching_example_bindings"] = []
    existing_binding = next(
        (binding for binding in bindings
         if isinstance(binding, dict) and binding.get("id") == example_id),
        None,
    )
    if existing_binding is None:
        bindings.append(notebook_binding)
    else:
        old_notebook_ref = existing_binding.get("notebook_ref")
        if old_notebook_ref != notebook_ref and old_notebook_ref in notebooks:
            still_referenced = any(
                binding is not existing_binding
                and isinstance(binding, dict)
                and binding.get("notebook_ref") == old_notebook_ref
                for binding in bindings
            )
            if not still_referenced:
                notebooks.remove(old_notebook_ref)
        existing_binding.clear()
        existing_binding.update(notebook_binding)
    record["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # The marked walkthrough may have been rewritten in place while retaining
    # the same anchor. Re-evaluate phase completion even when both refs already
    # existed; a same-ID re-teach must not preserve a stale completion badge.
    record.pop("status", None)
    record.pop("completed_at", None)
    for row in st.get("phase_checklist") or []:
        if phase_number_from_check(row.get("text") or "") == phase:
            row["done"] = False
    save(ws, st, "taught example：阶段 %d #%s + notebook anchor + teaching evidence"
         % (phase, example_id))
    return 0


def cmd_record_phase_evidence(ws, args):
    st = _require_state(ws)
    manifest_state, manifest_reason = phase_manifest_status(ws)
    if manifest_state == "broken":
        _die("v4.1 manifest 处于 partial/broken 状态，拒绝记录阶段证据：" + manifest_reason)
    phase = args.phase if args.phase is not None else st["current_phase"]
    if phase < 1:
        _die("--phase 必须 ≥1")
    plan = _plan_phases(ws)
    if plan and phase not in plan:
        _die("--phase %d 不在 study_plan.md 的阶段列表 %s 中" % (phase, sorted(plan)))
    field = "teaching_examples" if args.kind == "teaching-example" else args.kind
    if (field == "teaching_examples"
            and effective_interaction_style(st) == "step_by_step"):
        _die("step_by_step 教学例题必须使用 record-taught-example --id <id> "
             "--notebook-ref notebook/chNN.md#anchor 原子绑定，不能分开写 evidence")
    if field == "checkpoint":
        if not args.outcome:
            _die("checkpoint evidence 必须带 --outcome passed|wrong|skipped；只有题目 ID 不能证明答对")
        value = {"id": args.ref.strip(), "outcome": args.outcome}
    else:
        if args.outcome:
            _die("--outcome 只用于 --kind checkpoint")
        value = args.ref.strip()
    problem = phase_evidence_ref_error(ws, field, value)
    if problem:
        _die("拒绝记录 %s evidence：%s" % (field, problem))

    record = st.setdefault("phase_evidence", {}).setdefault(str(phase), {})
    rows = record.setdefault(field, [])
    changed = False
    if field == "checkpoint":
        existing = next((x for x in rows if isinstance(x, dict) and x.get("id") == value["id"]), None)
        if existing is None:
            rows.append(value)
            changed = True
        elif existing.get("outcome") != value["outcome"]:
            existing["outcome"] = value["outcome"]
            changed = True
    elif value not in rows:
        rows.append(value)
        changed = True
    record["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if changed:
        # Evidence changed after completion: force an explicit re-evaluation instead of preserving a stale badge.
        record.pop("status", None)
        record.pop("completed_at", None)
        for row in st.get("phase_checklist") or []:
            if phase_number_from_check(row.get("text") or "") == phase:
                row["done"] = False
    save(ws, st, "phase evidence：阶段 %d +%s" % (phase, field))
    return 0


def cmd_complete_phase(ws, args):
    st = _require_state(ws)
    phase = args.phase if args.phase is not None else st["current_phase"]
    if phase != st["current_phase"]:
        _die("complete-phase 只能完成 current_phase=%d，不能直接完成阶段 %d"
             % (st["current_phase"], phase))
    if args.next_phase is not None:
        plan = sorted(_plan_phases(ws))
        if plan:
            try:
                pos = plan.index(phase)
            except ValueError:
                _die("current_phase=%d 不在 study_plan.md 阶段列表 %s 中" % (phase, plan))
            expected = plan[pos + 1] if pos + 1 < len(plan) else None
        else:
            expected = phase + 1
        if args.next_phase != expected:
            _die("--next-phase 必须是计划中紧接阶段 %d 的下一阶段 %s，不能跳跃或回退"
                 % (phase, expected if expected is not None else "（已无下一阶段）"))
    row = _phase_check_row(st, phase)
    _complete_phase_in_state(ws, st, phase, args.status, row=row)
    if args.next_phase is not None:
        st["current_phase"] = args.next_phase
    save(ws, st, "阶段 %d → %s%s" %
         (phase, args.status, ("；断点 → %d" % args.next_phase) if args.next_phase is not None else ""))
    return 0


def cmd_set_check(ws, args):
    """打卡官方路径：md 是生成视图不许手改——勾/取消勾知识点打卡项走这里（--index 或 --match 定位）。"""
    st = _require_state(ws)
    rows = st["phase_checklist"]
    if args.index is not None:
        if not 1 <= args.index <= len(rows):
            _die("--index 超界（1..%d）" % len(rows))
        hits = [rows[args.index - 1]]
    elif args.match:
        hits = [r for r in rows if args.match in (r.get("text") or "")]
        if not hits:
            _die("没有打卡项包含 %r" % args.match)
        if len(hits) > 1:
            _die("匹配到 %d 个打卡项（%r）——请用更具体的 --match 或 --index" % (len(hits), args.match))
    else:
        _die("set-check 需要 --index 或 --match 定位打卡项")
    row = hits[0]
    phase = phase_number_from_check(row.get("text") or "")
    manifest_state, manifest_reason = phase_manifest_status(ws)
    if args.undone:
        row["done"] = False
        if phase is not None:
            record = (st.get("phase_evidence") or {}).get(str(phase))
            if isinstance(record, dict):
                record.pop("status", None)
                record.pop("completed_at", None)
    elif phase is not None and manifest_state == "broken":
        _die("v4.1 manifest 处于 partial/broken 状态，set-check 拒绝 fail-open：" + manifest_reason)
    elif phase is not None and manifest_state == "ready":
        if phase != st["current_phase"]:
            _die("新版 manifest 工作区只能完成 current_phase=%d；当前选中阶段 %d"
                 % (st["current_phase"], phase))
        record = (st.get("phase_evidence") or {}).get(str(phase)) or {}
        checkpoints = record.get("checkpoint") or []
        can_verify = (len(checkpoints) >= 2
                      and any(isinstance(x, dict) and x.get("outcome") == "passed" for x in checkpoints)
                      and not _no_questions_preference(st))
        _complete_phase_in_state(ws, st, phase,
                                 "verified" if can_verify else "covered_unverified", row=row)
    else:
        row["done"] = True
        if phase is not None:
            sys.stderr.write("update_progress[warn]: 旧工作区没有视觉/教学 manifest，"
                             "set-check 继续按旧布尔打卡兼容；该勾选不代表已有 phase_evidence。\n")
    save(ws, st, "打卡%s：%s" % ("取消" if args.undone else "完成", (hits[0].get("text") or "")[:40]))
    return 0


def cmd_render(ws, _args):
    st = _require_state(ws)
    save(ws, st, "render：md 已从 state 重建")
    return 0


def cmd_show(ws, _args):
    st = _require_state(ws)
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


# ---------------- workspace registry（v4 §2.5：课程 → 工作区 跨会话映射；全局、非工作区级） ----------------

REGISTRY_NAME = "workspaces.json"
REGISTRY_VERSION = 1
WORKSPACE_CONFIRMATION_VERSION = 2


def _registry_home():
    # EXAMPREP_HOME 覆盖供部署重定向/测试隔离——默认落用户主目录 ~/.exam-cram（冻结位置）
    return os.environ.get("EXAMPREP_HOME") or os.path.expanduser("~/.exam-cram")


def _registry_path():
    return os.path.join(_registry_home(), REGISTRY_NAME)


def _canonical_path(path):
    """Return the comparison form used by workspace confirmation receipts."""
    # ``realpath`` can preserve a Windows 8.3 spelling (RUNNER~1) while pathlib and
    # user-facing receipts expose the long spelling (runneradmin).  Resolve once through
    # pathlib so both spellings have one stable comparison form.
    return os.path.normcase(str(Path(os.path.abspath(path)).resolve(strict=False)))


def _is_link_or_reparse(path):
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            if isjunction(path):
                return True
        except OSError:
            pass
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _path_has_link_or_reparse(path):
    current = os.path.abspath(path)
    while True:
        if os.path.lexists(current) and _is_link_or_reparse(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _same_canonical_path(left, right):
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return False
    try:
        return _canonical_path(left) == _canonical_path(right)
    except (OSError, TypeError, ValueError):
        return False


def workspace_confirmation_status(workspace, materials, registry=None):
    """Check whether *this exact* workspace/materials pair was confirmed.

    Legacy registry rows remain valid for discovery but do not grant write
    permission.  A confirmation receipt is intentionally tied to both paths;
    moving either side invalidates it instead of silently authorizing a new
    target.
    """
    workspace = os.path.abspath(workspace)
    materials = os.path.abspath(materials)
    if not os.path.isdir(workspace) or not os.path.isdir(materials):
        return {
            "confirmed": False,
            "reason": "confirmation_path_missing_or_not_directory",
            "workspace": workspace,
            "materials": materials,
        }
    if _path_has_link_or_reparse(workspace) or _path_has_link_or_reparse(materials):
        return {
            "confirmed": False,
            "reason": "confirmation_path_link_backed",
            "workspace": workspace,
            "materials": materials,
        }
    reg = registry if registry is not None else load_registry()
    workspace_key = _canonical_path(workspace)
    materials_key = _canonical_path(materials)
    path_rows = [
        row for row in reg.get("workspaces", [])
        if _same_canonical_path(row.get("path"), workspace_key)
    ]
    if not path_rows:
        return {
            "confirmed": False,
            "reason": "workspace_not_registered",
            "workspace": workspace,
            "materials": materials,
        }
    if len(path_rows) != 1:
        return {
            "confirmed": False,
            "reason": "workspace_registration_ambiguous",
            "workspace": workspace,
            "materials": materials,
            "candidate_count": len(path_rows),
        }
    material_rows = [
        row for row in path_rows
        if _same_canonical_path(row.get("materials"), materials_key)
    ]
    if not material_rows:
        return {
            "confirmed": False,
            "reason": "materials_mismatch",
            "workspace": workspace,
            "materials": materials,
            "course": path_rows[0].get("course"),
        }
    if len(material_rows) != 1:
        return {
            "confirmed": False,
            "reason": "workspace_registration_ambiguous",
            "workspace": workspace,
            "materials": materials,
            "candidate_count": len(material_rows),
        }
    row = material_rows[0]
    receipt = row.get("confirmation")
    if not isinstance(receipt, dict) \
            or receipt.get("version") != WORKSPACE_CONFIRMATION_VERSION \
            or receipt.get("confirmed") is not True:
        return {
            "confirmed": False,
            "reason": "confirmation_missing",
            "workspace": workspace,
            "materials": materials,
            "course": row.get("course"),
        }
    receipt_workspace = receipt.get("workspace")
    receipt_materials = receipt.get("materials")
    if not _same_canonical_path(receipt_workspace, workspace_key) \
            or not _same_canonical_path(receipt_materials, materials_key):
        return {
            "confirmed": False,
            "reason": "confirmation_path_mismatch",
            "workspace": workspace,
            "materials": materials,
            "course": row.get("course"),
        }
    if receipt.get("workspace_canonical") != workspace_key \
            or receipt.get("materials_canonical") != materials_key:
        return {
            "confirmed": False,
            "reason": "confirmation_target_drift",
            "workspace": workspace,
            "materials": materials,
            "course": row.get("course"),
        }
    return {
        "confirmed": True,
        "reason": "confirmed",
        "workspace": workspace,
        "materials": materials,
        "course": row.get("course"),
        "confirmation": dict(receipt),
    }


def load_registry():
    """缺文件 = 空注册表（首次引导的正路）；文件损坏 = fail-loud 且点名文件——注册表是学生跨会话
    找回自己工作区的唯一线索，静默重建等于把所有课程一起弄丢。"""
    path = _registry_path()
    if not os.path.isfile(path):
        return {"version": REGISTRY_VERSION, "workspaces": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        _die("工作区注册表 %s 损坏/无法读取（%s）——不会静默重建；请手工修复或删除该文件后重试"
             % (path, e), 1)
    if not isinstance(reg, dict) or not isinstance(reg.get("workspaces"), list) \
            or any(not isinstance(w, dict) for w in reg["workspaces"]):
        _die("工作区注册表 %s 结构损坏（应为 {\"version\":1,\"workspaces\":[…]}）——"
             "不会静默重建；请手工修复或删除该文件后重试" % path, 1)
    reg.setdefault("version", REGISTRY_VERSION)
    return reg


def save_registry(reg):
    """Atomic UTF-8 write at the frozen location（temp + os.replace，同 save 的 O_EXCL 反符号链接口径）。"""
    home = _registry_home()
    path = os.path.join(home, REGISTRY_NAME)
    tmp = path + ".tmp"
    if os.path.islink(tmp):
        _die("检测到符号链接临时文件 %s——可能指向注册表目录外，拒绝写入（请手动清理后重试）" % tmp, 1)
    try:
        os.makedirs(home, exist_ok=True)
        if os.path.exists(tmp):
            os.remove(tmp)                      # 上次崩溃残留的普通 tmp——清掉重建
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(reg, ensure_ascii=False, indent=2))
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        _die("写入工作区注册表失败：%s——注册表未更新，请告知用户（绝不静默继续）" % e, 1)


def cmd_workspace_register(args):
    course = (args.course or "").strip()
    confirmed = bool(getattr(args, "confirmed", False))
    urgent = bool(getattr(args, "urgent", False))
    if not course:
        _die("--course 不能为空")
    if urgent and not confirmed:
        _die("--urgent only applies to an explicit --confirmed registration")
    if confirmed and args.materials is None:
        _die("--confirmed requires --materials so the receipt binds both exact paths")
    if not os.path.isdir(args.path):
        _die("--path 不存在或不是目录: %s——workspace-register 只登记已存在的工作区（建区必确认，"
             "本命令不代建目录）" % args.path)
    path_lexical = os.path.abspath(args.path)
    materials_lexical = None
    if args.materials is not None:
        if not os.path.isdir(args.materials):
            _die("--materials 不存在或不是目录: %s" % args.materials)
        materials_lexical = os.path.abspath(args.materials)
    # Inspect the caller's lexical chain before resolving it so a user-controlled link cannot
    # disappear from the security check.  Store the resolved long spelling only afterwards.
    if confirmed and (_path_has_link_or_reparse(path_lexical)
                      or _path_has_link_or_reparse(materials_lexical)):
        _die("--confirmed paths must not contain a symlink/junction/reparse component")
    # Discovery-only rows retain the long-standing ``abspath`` display contract.  Exact
    # confirmations use the resolved long spelling because that value is also embedded in
    # runtime/artifact receipts.  Identity checks use ``_canonical_path`` for both forms.
    path = (str(Path(path_lexical).resolve(strict=False)) if confirmed else path_lexical)
    materials = (
        (str(Path(materials_lexical).resolve(strict=False))
         if confirmed else materials_lexical)
        if materials_lexical is not None else None
    )
    reg = load_registry()
    rows = reg["workspaces"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    course_hits = [w for w in rows if w.get("course") == course]
    if len(course_hits) > 1:
        _die("工作区注册表里课程 %r 有多个记录——拒绝猜测；请先修复重复记录再重试" % course, 1)
    hit = course_hits[0] if course_hits else None
    workspace_conflicts = [
        w for w in rows
        if w is not hit and _same_canonical_path(w.get("path"), path)
    ]
    if workspace_conflicts and not confirmed:
        _die("工作区 %s 已登记给其他课程——未确认登记不能改写其归属；请用 exam_start.py confirm 明确确认新的课程/材料组合"
             % path, 1)
    if confirmed and workspace_conflicts:
        # An explicit exact-pair confirmation transfers this workspace to one
        # owner.  Keeping stale rows would make artifact commands guess which
        # materials root supplies source links.
        rows[:] = [w for w in rows if w not in workspace_conflicts]
    if hit is not None:
        # 同课程重复登记 = 原地更新（换路径/补材料/刷新最近使用），绝不追加重复行；
        # 未显式给 --materials 时保留旧值——换工作区路径不应顺手抹掉材料线索
        hit["path"] = path
        if materials is not None:
            hit["materials"] = materials
        hit.setdefault("materials", None)
        hit["last_used"] = now
        verb = "更新"
    else:
        rows.append({"course": course, "path": path, "materials": materials, "last_used": now})
        hit = rows[-1]
        verb = "登记"
    if confirmed:
        hit["confirmation"] = {
            "version": WORKSPACE_CONFIRMATION_VERSION,
            "confirmed": True,
            "course": course,
            "workspace": path,
            "materials": materials,
            "workspace_canonical": _canonical_path(path),
            "materials_canonical": _canonical_path(materials),
            "urgent": urgent,
            "confirmed_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
    else:
        # A discovery-only re-registration may retain an existing receipt only
        # while it still names the exact same workspace/materials pair.
        receipt = hit.get("confirmation")
        if isinstance(receipt, dict):
            receipt_workspace = receipt.get("workspace")
            receipt_materials = receipt.get("materials")
            if not _same_canonical_path(receipt_workspace, hit.get("path")) \
                    or not _same_canonical_path(receipt_materials, hit.get("materials")):
                hit.pop("confirmation", None)
    save_registry(reg)
    print("[+] workspace-register：%s「%s」→ %s（注册表：%s）" % (verb, course, path, _registry_path()))
    return 0


def cmd_workspace_list(args):
    reg = load_registry()
    # 最近使用在前；last_used 为 YYYY-MM-DD HH:MM（字典序即时间序），同分钟并列时后登记的在前
    ordered = [w for _i, w in sorted(enumerate(reg["workspaces"]),
                                     key=lambda t: (str(t[1].get("last_used") or ""), t[0]),
                                     reverse=True)]
    if args.json:
        print(json.dumps({"workspaces": ordered}, ensure_ascii=False, indent=2))
        return 0
    if not ordered:
        print("注册表为空——还没有登记过课程工作区；用 workspace-register --course <课程> --path <目录> 登记第一门")
        return 0
    for w in ordered:
        print("- %s ｜ %s ｜ 材料：%s ｜ 最近使用：%s" % (
            w.get("course") or "-", w.get("path") or "-",
            w.get("materials") or "-", w.get("last_used") or "-"))
    return 0


def run(argv=None):
    ap = argparse.ArgumentParser(description="Structured study state (study_state.json is the single source of truth; the md is a generated view).")
    # 注册表子命令是全局的（跨课程/跨工作区）不吃 --workspace——因此这里放宽为可选，
    # 其余子命令在 parse 后补回「必填」检查（同 argparse 原生 required 的 usage+exit 2 行为）
    ap.add_argument("--workspace", required=False, default=None,
                    help="workspace dir (required for every subcommand except the global workspace-register/workspace-list)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--force", action="store_true")
    p_set = sub.add_parser("set")
    p_set.add_argument("--phase", type=int, default=None)
    p_set.add_argument("--scope", default=None)
    p_set.add_argument("--mode", default=None)
    p_set.add_argument("--time-budget", dest="time_budget", default=None)
    p_set.add_argument("--language", default=None)
    p_set.add_argument("--artifact-mode", dest="artifact_mode", default=None,
                       help="output resources: chat/visual (explicit choice; never inferred from subscription)")
    p_set.add_argument("--interaction-style", dest="interaction_style",
                       choices=INTERACTION_STYLES, default=None,
                       help="optional teaching cadence: batch or step_by_step")
    p_set.add_argument("--pref", action="append", default=None, help="key=value; repeatable")
    # A6：window 子命令——知识点窗口进出（3-7 天/>7 天档的窗口系统落点）
    p_wa = sub.add_parser("window-add")
    p_wa.add_argument("--point", required=True, help="knowledge-point name")
    p_wa.add_argument("--chapter", default=None)
    p_wa.add_argument("--status", default="in_window",
                      help="window status: in_window/out_window/verified (zh display words `在窗口`/`窗口外`/`已实测` also accepted; default in_window)")
    p_wa.add_argument("--note", default=None)
    p_ws = sub.add_parser("window-set-status")
    p_ws.add_argument("--point", default=None, help="locate by knowledge-point name")
    p_ws.add_argument("--chapter", default=None, help="disambiguate when the same point exists in multiple chapters")
    p_ws.add_argument("--index", type=int, default=None, help="locate by 1-based index")
    p_ws.add_argument("--status", required=True,
                      help="window status: in_window/out_window/verified (zh display words `在窗口`/`窗口外`/`已实测` also accepted)")
    for name in ("add-mistake", "add-confusion"):
        p = sub.add_parser(name)
        p.add_argument("--id", default=None)
        p.add_argument("--chapter", default=None)
        p.add_argument("--note", required=True)
    for name in ("set-mistake-status", "set-confusion-status"):
        p = sub.add_parser(name)
        p.add_argument("--id", default=None, help="locate by [#id] (hits all rows with the id)")
        p.add_argument("--index", type=int, default=None, help="locate by 1-based index")
        p.add_argument("--status", required=True,
                       help="e.g. reviewed/resolved/to_review (zh display words `已复盘`/`已解决`/`待复盘` also accepted; unknown strings kept as-is)")
    p_chk = sub.add_parser("set-check")
    p_chk.add_argument("--index", type=int, default=None, help="locate a check-in item by 1-based index")
    p_chk.add_argument("--match", default=None, help="locate by containing text (must match exactly one)")
    p_chk.add_argument("--undone", action="store_true", help="untick (default is tick-done)")
    p_evidence = sub.add_parser(
        "record-phase-evidence",
        help="record one safe phase artifact / checkpoint outcome; repeat for each evidence item")
    p_evidence.add_argument("--phase", type=int, default=None,
                            help="phase number (default: current_phase)")
    p_evidence.add_argument("--kind", required=True,
                            choices=("wiki", "visual", "teaching-example", "notebook", "checkpoint"))
    p_evidence.add_argument("--ref", required=True,
                            help="workspace-relative path, teaching example ID, or quiz ID")
    p_evidence.add_argument("--outcome", choices=CHECKPOINT_OUTCOMES, default=None,
                            help="required for checkpoint: passed/wrong/skipped")
    p_taught = sub.add_parser(
        "record-taught-example",
        help=("effective step_by_step only: bind the current first-pending item to its "
              "marked notebook block and manifest revision"),
    )
    p_taught.add_argument("--id", required=True, help="current-phase teaching example ID")
    p_taught.add_argument(
        "--notebook-ref", required=True,
        help="exact notebook/chNN.md#anchor produced by a marked walkthrough write",
    )
    p_complete = sub.add_parser(
        "complete-phase",
        help="evidence-gated completion: covered_unverified or verified")
    p_complete.add_argument("--phase", type=int, default=None,
                            help="phase number (default: current_phase)")
    p_complete.add_argument("--status", choices=PHASE_EVIDENCE_STATUSES, required=True)
    p_complete.add_argument("--next-phase", type=int, default=None,
                            help="optionally advance current_phase in the same atomic save")
    sub.add_parser("render")
    sub.add_parser("show")
    # v4 §2.5：全局工作区注册表（冻结位置 EXAMPREP_HOME|~/.exam-cram 下 workspaces.json）
    p_wreg = sub.add_parser("workspace-register")
    p_wreg.add_argument("--course", required=True, help="course name (registry key; re-register updates in place)")
    p_wreg.add_argument("--path", required=True, help="workspace directory (must already exist)")
    p_wreg.add_argument("--materials", default=None, help="materials directory (must exist if given)")
    p_wreg.add_argument(
        "--confirmed", action="store_true",
        help="record an explicit exact workspace/materials confirmation receipt",
    )
    p_wreg.add_argument(
        "--urgent", action="store_true",
        help="mark that the caller explicitly applied the documented urgent-open exception",
    )
    p_wlist = sub.add_parser("workspace-list")
    p_wlist.add_argument("--json", action="store_true",
                         help='machine shape {"workspaces":[...]} (newest first)')
    args = ap.parse_args(argv)
    if args.cmd == "workspace-register":
        # Registry writers are serialized globally, then lock every workspace row
        # whose ownership can change.  This keeps an exact-pair confirmation from
        # changing underneath a completion/artifact publication snapshot, including
        # the case where an existing course is moved from one workspace to another.
        if not os.path.isdir(args.path):
            return cmd_workspace_register(args)
        with _exclusive_file_lock(_registry_path() + ".lock"):
            registry = load_registry()
            target = os.path.abspath(args.path)
            affected = [target]
            for row in registry.get("workspaces", []):
                row_path = row.get("path") if isinstance(row, dict) else None
                if not isinstance(row_path, str) or not os.path.isdir(row_path):
                    continue
                if row.get("course") == (args.course or "").strip() \
                        or _same_canonical_path(row_path, target):
                    affected.append(row_path)
            unique = {}
            for value in affected:
                unique[_canonical_path(value)] = value
            with ExitStack() as locks:
                for key in sorted(unique):
                    locks.enter_context(workspace_publication_lock(unique[key]))
                return cmd_workspace_register(args)
    if args.cmd == "workspace-list":
        return cmd_workspace_list(args)
    if args.workspace is None:
        # 保持其余子命令的既有契约：--workspace 必填（复刻 argparse required 的报错与 exit 2）
        ap.error("the following arguments are required: --workspace")
    ws = args.workspace
    if not os.path.isdir(ws):
        _die("workspace 不存在: %s" % ws)
    if args.cmd == "show":
        return cmd_show(ws, args)
    with workspace_publication_lock(ws):
        if args.cmd == "init":
            return cmd_init(ws, args)
        if args.cmd == "set":
            return cmd_set(ws, args)
        if args.cmd == "add-mistake":
            return cmd_add(ws, args, "mistake_archive", "错题")
        if args.cmd == "add-confusion":
            return cmd_add(ws, args, "confusion_log", "疑难")
        if args.cmd == "set-mistake-status":
            return cmd_set_status(ws, args, "mistake_archive", "错题")
        if args.cmd == "set-confusion-status":
            return cmd_set_status(ws, args, "confusion_log", "疑难")
        if args.cmd == "window-add":
            return cmd_window_add(ws, args)
        if args.cmd == "window-set-status":
            return cmd_window_set_status(ws, args)
        if args.cmd == "set-check":
            return cmd_set_check(ws, args)
        if args.cmd == "record-phase-evidence":
            return cmd_record_phase_evidence(ws, args)
        if args.cmd == "record-taught-example":
            return cmd_record_taught_example(ws, args)
        if args.cmd == "complete-phase":
            return cmd_complete_phase(ws, args)
        return cmd_render(ws, args)


if __name__ == "__main__":
    sys.exit(run())
