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
    python scripts/update_progress.py --workspace <ws> set --scope homework-only --mode 查缺补漏
    python scripts/update_progress.py --workspace <ws> add-mistake --id hw_hw1_3 --chapter 2 --note "Venn 阴影判断错"
    python scripts/update_progress.py --workspace <ws> add-confusion --chapter 1 --note "循环队列取模"
    python scripts/update_progress.py --workspace <ws> render               # json → md（修复被手改的 md）
    python scripts/update_progress.py --workspace <ws> show                 # 打印当前状态 JSON

Backward compatible: a workspace WITHOUT study_state.json keeps working (no-Python fallback:
hand-written study_progress.md still validates); `init` adopts the existing md losslessly
(phase + mistake/confusion rows, both bullet and ingest-template table forms).
Exit codes: 0 ok · 1 write/render failure · 2 bad input/usage.
"""
import argparse
import datetime
import json
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

STATE_NAME = "study_state.json"
MD_NAME = "study_progress.md"
SCHEMA_VERSION = 1

# ---- A6：3 学习模式 × 4 时间宽裕度（canonical 词表 + 旧四模式迁移废弃）----
# 学习模式（首次对话必须问清，存 state.mode）：
LEARNING_MODES = ("零基础从头讲", "某章起步补弱", "查缺补漏")
# 时间宽裕度（叠加在模式上，存 state.time_budget）：
TIME_TIERS = ("≤1天", "1-3天", "3-7天", ">7天")
# 旧四模式（normal/sprint/panic/mock）已废弃 → 迁移为 (新模式, 时间宽裕度提示或 None)。
# 依据计划：panic→零基础+当天、sprint→查缺补漏+短时限、normal/mock→查缺补漏。
_MODE_MIGRATION = {
    "panic": ("零基础从头讲", "≤1天"),
    "sprint": ("查缺补漏", "1-3天"),
    "normal": ("查缺补漏", None),
    "mock": ("查缺补漏", None),
}
# 时间宽裕度的宽松别名 → canonical 档。
_TIER_ALIASES = {
    "≤1天": "≤1天", "<=1天": "≤1天", "1天": "≤1天", "当天": "≤1天", "今天": "≤1天",
    "一天": "≤1天", "考前一天": "≤1天", "明天考": "≤1天",
    "1-3天": "1-3天", "1—3天": "1-3天", "1~3天": "1-3天", "2-3天": "1-3天", "几天": "1-3天",
    "3-7天": "3-7天", "3—7天": "3-7天", "3~7天": "3-7天", "一周": "3-7天", "一周内": "3-7天",
    ">7天": ">7天", "＞7天": ">7天", "7天以上": ">7天", "一周以上": ">7天", "还早": ">7天",
    "时间充裕": ">7天",
}


def _normalize_mode(v):
    """→ (canonical_mode 或原值, migrated_tier 或 None, warning 或 None)。
    新模式原样；旧四模式迁移 + 警告；未知值保留但警告（AI 侧再决定，不静默改写）。"""
    v = (v or "").strip()
    if v in LEARNING_MODES:
        return v, None, None
    if v in _MODE_MIGRATION:
        new_mode, tier = _MODE_MIGRATION[v]
        return new_mode, tier, ("旧模式「%s」已废弃，迁移为「%s」%s（新模式仅 %s）"
                                % (v, new_mode, ("＋时间宽裕度「%s」" % tier) if tier else "",
                                   "/".join(LEARNING_MODES)))
    return v, None, ("非标准学习模式「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
                     % (v, "/".join(LEARNING_MODES)))


def _normalize_tier(v):
    """→ (canonical_tier 或原值, warning 或 None)。"""
    v = (v or "").strip()
    if v in TIME_TIERS:
        return v, None
    if v in _TIER_ALIASES:
        return _TIER_ALIASES[v], None
    return v, ("非标准时间宽裕度「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
               % (v, "/".join(TIME_TIERS)))


# A8b：回复语言偏好（chat 层渲染语言；持久化文件与脚本输出在任何模式下都保持中文 canonical）
LANGUAGES = ("中文", "English", "双语")
_LANG_ALIASES = {
    "zh": "中文", "zh-cn": "中文", "chinese": "中文", "简体中文": "中文", "汉语": "中文", "中": "中文",
    "en": "English", "english": "English", "英文": "English", "英语": "English",
    "bilingual": "双语", "bi": "双语", "zh+en": "双语", "中英": "双语", "中英双语": "双语",
}


def _normalize_language(v):
    """→ (canonical_language 或原值, warning 或 None)。ASCII 别名不区分大小写；未知值原样保留并告警。"""
    v = (v or "").strip()
    if v in LANGUAGES:
        return v, None
    key = v.lower()
    if key in _LANG_ALIASES:
        return _LANG_ALIASES[key], None
    return v, ("非标准语言偏好「%s」——canonical 仅 %s；已按原值保留，请确认是否规范化"
               % (v, "/".join(LANGUAGES)))


def _die(msg, code=2):
    sys.stderr.write("update_progress: " + msg + "\n")
    raise SystemExit(code)


def default_state():
    return {"version": SCHEMA_VERSION, "current_phase": 1, "scope": None, "mode": None,
            "time_budget": None, "language": None, "preferences": {},
            "mistake_archive": [], "confusion_log": [], "knowledge_window": [],
            "phase_checklist": [], "last_updated": None}


# ---------------- md → state (migration; tolerant of both bullet and table forms) ----------------

_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|?\s*$")
_HDR_WORDS = ("错题id", "关联章节", "题目内容", "错误原因", "序号", "疑难点", "解答要点", "状态")
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
    lm = re.search(r"语言偏好\**\s*：\s*(.+)", t)
    prefs["language"] = lm.group(1).strip() if lm else None
    mistakes, confusions, checklist, window = [], [], [], []
    cur, in_checklist, in_window, tbl_cols, window_cols = None, False, False, None, None
    for ln in t.splitlines():
        h = ln.strip()
        is_heading = bool(re.match(r"^\s{0,3}(#{1,4}\s|\*\*)", ln))
        if is_heading and re.search(r"打卡|checklist", h, re.I):
            cur, in_checklist, in_window, tbl_cols = None, True, False, None   # 📊 知识点打卡状态 区
            continue
        if is_heading and re.search(r"错题|mistake", h, re.I):
            cur, in_checklist, in_window, tbl_cols = mistakes, False, False, None
            continue
        if is_heading and re.search(r"疑难|困惑|confusion", h, re.I):
            cur, in_checklist, in_window, tbl_cols = confusions, False, False, None
            continue
        if is_heading and re.search(r"知识点窗口|窗口|🪟", h):
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
            if "知识点" in low and "状态" in low:
                window_cols = []
                for c in cells:
                    if "知识点" in c:
                        window_cols.append("point")
                    elif "章节" in c:
                        window_cols.append("chapter")
                    elif "状态" in c:
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
                window.append({"point": got["point"], "chapter": got.get("chapter"),
                               "status": got.get("status") or "在窗口", "note": " / ".join(wnotes)})
            continue
        if cur is None:
            continue
        default_status = "待回顾" if cur is confusions else "待复盘"   # 疑难走 待回顾→已回顾 契约
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
                    if "章节" in c:
                        tbl_cols.append("chapter")
                    elif "状态" in c:
                        tbl_cols.append("status")
                    elif "id" in c.lower() or "序号" in c:
                        tbl_cols.append("id")
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
                            "status": got.get("status") or default_status})
                continue
            # 没见到表头或行宽与表头不符——退回位置映射（模板 5 列布局）
            tail = cells[2:]
            # 模板表最后一列是状态——迁移 note 时必须剔除，否则状态在 note 和状态列各出现一次；
            # 只有 3 列（无状态列）时整个尾部都是 note，状态回默认
            status = tail[-1] if len(tail) >= 2 and tail[-1] else default_status
            note_cells = tail[:-1] if len(tail) >= 2 else tail
            first_cell = cells[0] if cells and cells[0] not in ("-", "") else None   # 渲染的 '-' 占位≠id
            cur.append({"id": ids[0] if ids else first_cell, "chapter": cells[1] if len(cells) > 1 else None,
                        "note": " / ".join(c for c in note_cells if c) or (cells[0] if cells else ""),
                        "status": status})
    return phase, mistakes, confusions, checklist, window, prefs


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
            out.append("| %s | %s | %s | %s |" % (rid, _md_cell(r.get("chapter")),
                                                  _md_cell(r.get("note"), default=""),
                                                  _md_cell(r.get("status"), default=default_status)))
        return "\n".join(out)

    lines = [
        "# 🎯 复习进度与错题档案（由 study_state.json 自动生成——请勿手改本文件，改动会在下次渲染时丢失）",
        "",
        "## ⏱️ 当前复习断点",
        "* **当前进行阶段**：阶段 %d" % state["current_phase"],
        "* **范围/模式**：%s ｜ %s ｜ 时间预算 %s" % (state.get("scope") or "混合题池",
                                                     state.get("mode") or "未设定",
                                                     state.get("time_budget") or "未设定"),
        "* **最后更新时间**：%s" % (state.get("last_updated") or "-"),
    ]
    if state.get("language"):
        # 语言偏好也要能从生成视图迁回来——init --force 恢复路径不丢 set --language
        lines.append("* **语言偏好**：%s" % _md_cell(state["language"], default=""))
    lines.append("")
    if state.get("phase_checklist"):
        # 打卡区随 state 一起渲染回来——迁移绝不丢每阶段完成状态；勾选走 set-check 官方路径
        lines += ["## 📊 知识点打卡状态",
                  "\n".join("- [%s] %s" % ("x" if r.get("done") else " ", _md_cell(r.get("text"), default=""))
                            for r in state["phase_checklist"]), ""]
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
                _md_cell(r.get("status"), default="在窗口"), _md_cell(r.get("note"), default="")))
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
               "phase_checklist": checklist, "knowledge_window": window})
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
    for k in ("scope", "mode", "time_budget", "language"):   # A2 范围/模式/语言偏好随迁移带走
        if prefs.get(k):
            st[k] = prefs[k]
    if prefs.get("preferences"):                    # ⚙️ 偏好区（讲解风格等）同理——恢复路径不丢偏好
        st["preferences"].update(prefs["preferences"])
    save(ws, st, "init：从 %s 迁移（阶段 %d，错题 %d，疑难 %d，打卡 %d）"
         % (MD_NAME if os.path.isfile(md_path) else "空白", phase, len(mistakes), len(confusions),
            len(checklist)))
    return 0


def _require_state(ws, repairing_phase=False):
    st = load_state(ws)
    if st is None:
        _die("尚无 study_state.json——先跑 `update_progress.py --workspace <ws> init` 迁移")
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


def cmd_set(ws, args):
    # 提供 --phase 时豁免陈旧断点检查（这是修复路径；新值下面自行对照计划校验）
    st = _require_state(ws, repairing_phase=args.phase is not None)
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
            st["mode"] = cmode
            changed.append("mode=%s" % cmode)
            if warn:
                sys.stderr.write("update_progress[warn]: " + warn + "\n")
            # 旧模式迁移带出的时间档：本次未显式 --time-budget 就落它——旧模式名自带紧迫度语义，
            # panic 换到 sprint 必须把 ≤1天 刷成 1-3天，不能留旧迁移值让节奏判定错档（仅显式 --time-budget 才不覆盖）
            if mig_tier and args.time_budget is None:
                st["time_budget"] = mig_tier
                changed.append("time_budget=%s（旧模式迁移带出）" % mig_tier)
    # A6：time_budget 归一化到 4 个 canonical 档
    if args.time_budget is not None:
        if not args.time_budget:
            st["time_budget"] = None
            changed.append("time_budget=（清除）")
        else:
            ctier, twarn = _normalize_tier(args.time_budget)
            st["time_budget"] = ctier
            changed.append("time_budget=%s" % ctier)
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
            changed.append("language=%s" % clang)
            if lwarn:
                sys.stderr.write("update_progress[warn]: " + lwarn + "\n")
    for kv in (args.pref or []):
        if "=" not in kv:
            _die("--pref 需要 key=value 形式，当前 %r" % kv)
        k, v = kv.split("=", 1)
        st.setdefault("preferences", {})[k.strip()] = v.strip()
        changed.append("pref %s" % k.strip())
    if not changed:
        _die("set 没有任何改动参数（--phase/--scope/--mode/--time-budget/--language/--pref）")
    save(ws, st, "set：" + "、".join(changed))
    return 0


# A6：知识点窗口状态词——窗口内（近期讲过、默认还会）/ 窗口外（需回问或实测）/ 已实测（做题验证过）。
_WINDOW_STATUSES = ("在窗口", "窗口外", "已实测")


def cmd_window_add(ws, args):
    st = _require_state(ws)
    point = (args.point or "").strip()
    if not point:
        _die("--point 不能为空")
    status = (args.status or "在窗口").strip()
    if status not in _WINDOW_STATUSES:
        _die("--status 必须是 %s，当前 %r" % ("/".join(_WINDOW_STATUSES), status))
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
        save(ws, st, "window：更新「%s」→%s" % (point, status))
        return 0
    win.append({"point": point, "chapter": ac, "status": status, "note": (args.note or "").strip()})
    save(ws, st, "window：登记「%s」（%s）" % (point, status))
    return 0


def cmd_window_set_status(ws, args):
    st = _require_state(ws)
    status = (args.status or "").strip()
    if status not in _WINDOW_STATUSES:
        _die("--status 必须是 %s，当前 %r" % ("/".join(_WINDOW_STATUSES), status))
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
    save(ws, st, "window：%d 条 →%s" % (len(hits), status))
    return 0


def cmd_add(ws, args, field, label):
    st = _require_state(ws)
    if not (args.note or "").strip():
        _die("--note 不能为空")
    # 错题走 待复盘→已订正，疑难走 待回顾→已回顾——初始状态必须按目标契约给，复盘流按状态词捞行
    row = {"id": args.id, "chapter": args.chapter, "note": args.note.strip(),
           "status": "待回顾" if field == "confusion_log" else "待复盘"}
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
    for r in hits:
        r["status"] = args.status
    save(ws, st, "%s 状态更新 ×%d → %s" % (label, len(hits), args.status))
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
    for r in hits:
        r["done"] = not args.undone
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


def run(argv=None):
    ap = argparse.ArgumentParser(description="Structured study state (study_state.json is the single source of truth; the md is a generated view).")
    ap.add_argument("--workspace", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--force", action="store_true")
    p_set = sub.add_parser("set")
    p_set.add_argument("--phase", type=int, default=None)
    p_set.add_argument("--scope", default=None)
    p_set.add_argument("--mode", default=None)
    p_set.add_argument("--time-budget", dest="time_budget", default=None)
    p_set.add_argument("--language", default=None)
    p_set.add_argument("--pref", action="append", default=None, help="key=value; repeatable")
    # A6：window 子命令——知识点窗口进出（3-7 天/>7 天档的窗口系统落点）
    p_wa = sub.add_parser("window-add")
    p_wa.add_argument("--point", required=True, help="knowledge-point name")
    p_wa.add_argument("--chapter", default=None)
    p_wa.add_argument("--status", default="在窗口", help="window status: 在窗口/窗口外/已实测 (default 在窗口)")
    p_wa.add_argument("--note", default=None)
    p_ws = sub.add_parser("window-set-status")
    p_ws.add_argument("--point", default=None, help="locate by knowledge-point name")
    p_ws.add_argument("--chapter", default=None, help="disambiguate when the same point exists in multiple chapters")
    p_ws.add_argument("--index", type=int, default=None, help="locate by 1-based index")
    p_ws.add_argument("--status", required=True, help="window status: 在窗口/窗口外/已实测")
    for name in ("add-mistake", "add-confusion"):
        p = sub.add_parser(name)
        p.add_argument("--id", default=None)
        p.add_argument("--chapter", default=None)
        p.add_argument("--note", required=True)
    for name in ("set-mistake-status", "set-confusion-status"):
        p = sub.add_parser(name)
        p.add_argument("--id", default=None, help="locate by [#id] (hits all rows with the id)")
        p.add_argument("--index", type=int, default=None, help="locate by 1-based index")
        p.add_argument("--status", required=True, help="e.g. 已复盘/已解决/待复盘")
    p_chk = sub.add_parser("set-check")
    p_chk.add_argument("--index", type=int, default=None, help="locate a check-in item by 1-based index")
    p_chk.add_argument("--match", default=None, help="locate by containing text (must match exactly one)")
    p_chk.add_argument("--undone", action="store_true", help="untick (default is tick-done)")
    sub.add_parser("render")
    sub.add_parser("show")
    args = ap.parse_args(argv)
    ws = args.workspace
    if not os.path.isdir(ws):
        _die("workspace 不存在: %s" % ws)
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
    if args.cmd == "render":
        return cmd_render(ws, args)
    return cmd_show(ws, args)


if __name__ == "__main__":
    sys.exit(run())
