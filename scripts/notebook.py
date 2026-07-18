#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Notebook engine (v4 P4, G3/G4) — substantive answers land on DISK, chat only carries
a summary + a link. notebook/chNN.md holds the full walkthrough/feedback/confusion/review
entries; mistakes/chNN.md mirrors the mistake entries; both index.md files are DERIVED
views, deterministically rebuilt from the chapter files on every write.

Why: before v4 every seven-step walkthrough and grading feedback evaporated with the chat;
the durable notebook contract is documented in the shipped docs/file-format.md.
This official tool is the only write path: entry bodies arrive on STDIN (UTF-8), files are
written atomically (temp + os.replace, same conventions as update_progress.save), and the
same --id AND --type in the same chapter REPLACES the entry in place, so re-teaching a
question never duplicates it — while a DIFFERENT type under the same id (walkthrough then
feedback on the same question, the exact flow the contracts prescribe) is appended, never
silently deleted. A write failure is FAIL-LOUD (non-zero exit) — never silently "recorded".

    python scripts/notebook.py --workspace <ws> add-entry --chapter 2 --type walkthrough \
        --id q13 --title "Venn 图判断" < body.md      # body via STDIN; rebuilds notebook/index.md
    python scripts/notebook.py --workspace <ws> add-entry --chapter 2 --type walkthrough \
        --id ex13 --teaching-example < body.md        # explicit step-by-step evidence marker
    python scripts/notebook.py --workspace <ws> add-entry --chapter 2 --type feedback \
        --id q13 --mistake < body.md                  # also mirrors into mistakes/ + its index
    python scripts/notebook.py --workspace <ws> rebuild              # regenerate both index.md
    python scripts/notebook.py --workspace <ws> list [--json]        # entries inventory

Headings/labels come from i18n msgids (--lang zh|en; default: study_state.json language,
falling back to zh). Entry anchors are GitHub-style slugs of "[#<id>] <title>", so index
links jump straight to the entry. Chapter files are parsed back by their "## [#<id>]"
block markers, fence-aware: a "## " line inside a fenced code block is content, never a
block boundary. Exit codes: 0 ok · 1 read/write failure · 2 bad input/usage.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

for _s in ("stdin", "stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

import i18n
from ingestion import is_link_or_reparse, workspace_publication_lock
from stable_ids import STABLE_ITEM_ID_RE, stable_item_id_problem

NOTEBOOK_DIR = "notebook"
MISTAKES_DIR = "mistakes"
INDEX_NAME = "index.md"
STATE_NAME = "study_state.json"
TYPES = ("walkthrough", "feedback", "confusion", "review")

# 条目块标记：'## [#<id>] <title>'——id 不含空白/']'（与 update_progress 的 [#id] 提取同口径）
_HEAD_RE = re.compile(r"^##\s+\[#([^\]\s]+)\]\s*(.*)$")
# 条目元行：'> <type-label> · <YYYY-MM-DD HH:MM>'
_META_RE = re.compile(r"^>\s*(.+?)\s*·\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
# 围栏行（CommonMark 缩进 ≤3 空格）；开闭都按长度配对，围栏内的 '## ' 是内容不是标题
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# 章文件名：chNN.md（生成时 NN 补零到 2 位；回读容忍手写的 ch3.md）
_CH_FILE_RE = re.compile(r"^ch(\d+)\.md$")
# 条目 id：进标题的 [#id] 与锚点，禁空白与破坏解析/链接的字符
_ID_RE = STABLE_ITEM_ID_RE
_TEACHING_EXAMPLE_MARKER_RE = re.compile(
    r"^<!-- exam-cram-teaching-example-id-sha256: ([0-9a-f]{64}) -->$")


def _die(msg, code=2):
    sys.stderr.write("notebook: " + msg + "\n")
    raise SystemExit(code)


# ---------------- shared plumbing (containment / atomic IO — update_progress conventions) ----

def _read_regular(path):
    """Read a UTF-8 regular file; symlinks are refused (they can point outside the workspace)."""
    if is_link_or_reparse(path):
        _die("%s 是 link/reparse point——可能指向工作区外，拒绝读取（请替换为真实文件）" % path, 1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        _die("%s 无法读取（%s）——本次操作未进行，请检查文件权限" % (path, e), 1)
    except UnicodeDecodeError as e:
        _die("%s 不是 UTF-8（%s）——笔记本文件必须是 UTF-8，请先转存再重试" % (path, e), 1)


def _guard_dir(ws, name, create=False):
    """notebook/ 与 mistakes/ 必须是工作区内的真实目录：符号链接目录或经链接逃出工作区都拒绝
    （load_state 同款口径）——否则每次落盘都在往用户不知道的地方写。"""
    d = os.path.join(ws, name)
    if os.path.lexists(d) and is_link_or_reparse(d):
        _die("%s 是 link/reparse point——可能指向工作区外，拒绝读写（请替换为真实目录）" % d, 1)
    if os.path.lexists(d) and not os.path.isdir(d):
        _die("%s 已存在但不是目录——拒绝读写，请先修复工作区结构" % d, 1)
    if create and not os.path.lexists(d):
        try:
            os.makedirs(d)
        except OSError as e:
            _die("无法创建目录 %s（%s）" % (d, e), 1)
    if os.path.isdir(d):
        ws_real = os.path.normcase(os.path.realpath(ws))
        d_real = os.path.normcase(os.path.realpath(d))
        try:
            contained = os.path.commonpath((ws_real, d_real)) == ws_real
        except ValueError:
            contained = False
        if not contained:
            _die("%s 经链接或重解析点逃出工作区——拒绝读写" % d, 1)
    return d


def _save_files(plan, note):
    """Atomic UTF-8 write of every (path, content) in the plan: pre-check ALL targets, stage
    ALL tmp files, then replace — a failure at any step leaves no half-written file behind
    (worst case an index is stale; `rebuild` repairs it). Same conventions as
    update_progress.save: O_EXCL tmp creation, symlink-tmp refusal, fail-loud."""
    for path, _content in plan:
        if os.path.lexists(path) and is_link_or_reparse(path):
            _die("%s 是 link/reparse point——可能指向工作区外，拒绝写入（请替换为真实文件）" % path, 1)
        if os.path.lexists(path) and not os.path.isfile(path):
            _die("%s 已存在但不是常规文件（目录/特殊文件）——拒绝写入，请先手动清理" % path, 1)
        tmp = path + ".tmp"
        if os.path.lexists(tmp) and is_link_or_reparse(tmp):
            _die("检测到 link/reparse 临时文件 %s——可能指向工作区外，拒绝写入（请手动清理后重试）" % tmp, 1)
    tmps = []
    try:
        for path, content in plan:
            tmp = path + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)                      # 上次崩溃残留的普通 tmp——清掉重建
            # O_EXCL 独占创建：文件必须不存在才成功，绝不跟随同名链接/复用旧文件
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            tmps.append((tmp, path))
        for tmp, path in tmps:
            os.replace(tmp, path)
    except OSError as e:
        for tmp, _p in tmps:
            try:
                os.remove(tmp)
            except OSError:
                pass
        _die("写入笔记本失败：%s——章文件与目录未被半写破坏；若目录已陈旧，跑 rebuild 即可恢复"
             "一致。请告知用户（绝不静默继续）" % e, 1)
    print("[+] %s" % note)


def _load_state(ws):
    """study_state.json（可缺省）——只为读语言偏好与错题状态；load_state 同款 fail-loud。"""
    path = os.path.join(ws, STATE_NAME)
    if os.path.lexists(path) and is_link_or_reparse(path):
        _die("study_state.json 是 link/reparse point——事实源可能指向工作区外，拒绝读取（请替换为真实文件）", 1)
    if os.path.lexists(path) and not os.path.isfile(path):
        _die("study_state.json 已存在但不是普通文件——拒绝把它当作缺失状态", 1)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        _die("study_state.json 无法读取/不是合法 JSON（%s）——语言与错题状态无从获取；"
             "请先修复 state（或 update_progress init --force 重建）" % e, 1)
    if not isinstance(st, dict):
        _die("study_state.json 顶层必须是对象", 1)
    return st


def _resolve_lang(arg_lang, state):
    """--lang 显式覆盖 > study_state.json 的 language（经 i18n 归一） > zh；
    bilingual 的落盘标题走 zh（双语=zh 包+镜像组合，文件只落一份标题）。"""
    if arg_lang:
        return arg_lang
    lang = i18n.workspace_language(state)
    return lang if lang in ("zh", "en") else "zh"


# ---------------- chapter-file parsing / rendering (fence-aware block model) ----------------

def _fence_step(state, line):
    """CommonMark 围栏状态机（Codex r5）：state = (字符, 长度) 或 None。反引号开的栏只有反引号
    能关（`~~~` 在其中是内容）；关栏须同字符且长度 ≥ 开栏。返回 (new_state, is_marker_line)。"""
    fm = _FENCE_RE.match(line)
    if not fm:
        return state, False
    tick = fm.group(1)
    ch, ln = tick[0], len(tick)
    if state is None:
        return (ch, ln), True
    if ch == state[0] and ln >= state[1]:
        return None, True
    return state, False        # 异字符 / 更短 = 开栏内的内容行，不是围栏标记


def parse_chapter(text):
    """→ (preamble_lines, [{id, title, lines}]) — blocks keyed by '## [#<id>]' markers.
    Fence-aware: a '## [#x]' line inside a fenced code block is CONTENT (stays in the
    current block), so bodies holding markdown examples never corrupt the block model.
    Preamble (hand-added prose above the first entry) is preserved verbatim on rewrite."""
    pre, blocks, cur, fence = [], [], None, None
    for line in (text or "").splitlines():
        fence, marker = _fence_step(fence, line)
        if marker:
            (cur["lines"] if cur else pre).append(line)
            continue
        if fence is None:
            hm = _HEAD_RE.match(line)
            if hm:
                cur = {"id": hm.group(1), "title": hm.group(2).strip() or hm.group(1),
                       "lines": [line]}
                blocks.append(cur)
                continue
        (cur["lines"] if cur else pre).append(line)
    if fence is not None:
        raise ValueError(
            "notebook chapter contains an unterminated fenced code block"
        )
    return pre, blocks


def render_chapter(pre, blocks):
    """Deterministic serialization: preamble + blocks, exactly one blank line between parts,
    single trailing newline. Blocks keep their raw lines verbatim (hand edits survive)."""
    parts = []
    pre_text = "\n".join(pre).rstrip()
    if pre_text:
        parts.append(pre_text)
    for b in blocks:
        parts.append("\n".join(b["lines"]).rstrip())
    return "\n\n".join(parts) + "\n"


def github_slug(text):
    """GitHub-style heading anchor: lowercase; drop punctuation; spaces → hyphens;
    letters/digits (CJK included) and _- survive. Anchor source is '[#<id>] <title>'."""
    out = []
    for ch in (text or "").strip().lower():
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        elif ch in " \t":
            out.append("-")
    return "".join(out)


def teaching_example_marker(eid):
    digest = hashlib.sha256(eid.encode("utf-8")).hexdigest()
    return "<!-- exam-cram-teaching-example-id-sha256: %s -->" % digest


def block_has_teaching_example_marker(block, eid):
    expected = hashlib.sha256(eid.encode("utf-8")).hexdigest()
    lines = block.get("lines") or []
    markers = []
    for line in lines:
        match = _TEACHING_EXAMPLE_MARKER_RE.match(line)
        if match:
            markers.append(match.group(1))
    return (len(lines) > 4
            and lines[4] == teaching_example_marker(eid)
            and markers == [expected])


def block_sha256(block):
    """Hash one parsed entry in the canonical form used by progress bindings."""
    lines = block.get("lines") if isinstance(block, dict) else None
    if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
        raise ValueError("notebook block lines must be a string array")
    rendered = "\n".join(lines).rstrip() + "\n"
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def make_entry_lines(eid, title, type_label, ts, body, teaching_example=False):
    """One entry block: heading + one-line meta (type · timestamp) + body + trailing ---."""
    lines = ["## [#%s] %s" % (eid, title), "", "> %s · %s" % (type_label, ts), ""]
    if teaching_example:
        lines += [teaching_example_marker(eid), ""]
    lines += body.splitlines()
    lines += ["", "---"]
    return lines


def _block_meta(lines):
    """(type_label, timestamp) from a block's meta line, fence-aware; (None, None) if absent
    (hand-written blocks are legal notebook citizens — they list/index fine without meta)."""
    fence = None
    for line in lines[1:]:
        fence, marker = _fence_step(fence, line)
        if marker:
            continue
        if fence is None:
            m = _META_RE.match(line)
            if m:
                return m.group(1), m.group(2)
    return None, None


def _label_to_type():
    """Reverse map: zh/en display labels (and the canonical codes themselves) → type code."""
    rev = {t: t for t in TYPES}
    for lang in ("zh", "en"):
        cat = i18n.catalog(lang)
        for t in TYPES:
            lab = cat.get("notebook_type." + t)
            if lab:
                rev[lab] = t
    return rev


# ---------------- collection + index rendering (derived views) ----------------

def _collect(ws, dirname, overrides=None):
    """[(num, fname, blocks)] for every chapter file in <ws>/<dirname>, sorted by chapter.
    `overrides` maps fname → not-yet-written content so an in-flight add-entry can rebuild
    the index in the SAME atomic plan as the chapter write (no read-back window)."""
    d = _guard_dir(ws, dirname)
    names = {}
    if os.path.isdir(d):
        for name in os.listdir(d):
            if _CH_FILE_RE.match(name):
                names[name] = None
    for name, content in (overrides or {}).items():
        names[name] = content
    out = []
    for name, content in names.items():
        text = content if content is not None else _read_regular(os.path.join(d, name))
        pre, blocks = parse_chapter(text)
        out.append((int(_CH_FILE_RE.match(name).group(1)), name, blocks, pre))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _mistake_status_map(state):
    """id → status from study_state.mistake_archive (last row wins — latest registration)."""
    rows = (state or {}).get("mistake_archive")
    m = {}
    for r in (rows if isinstance(rows, list) else []):
        if isinstance(r, dict) and r.get("id") and isinstance(r.get("status"), str):
            m[r["id"]] = r["status"]
    return m


def entry_anchor(eid, title):
    return github_slug("[#%s] %s" % (eid, title))


_PRE_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*$")


def anchors_for(pre, blocks):
    """每个条目块的**实际** GitHub 锚（Codex r4/r5）：GitHub 对**文件里全部标题**按出现顺序
    计数，同 slug 第二个起加 -1/-2 后缀——前言标题、条目标题、**条目正文里的标题**都参与
    （正文含 `### [#q2] Second` 时，后面真正的 q2 条目锚是 `…-1`）。计数口径与
    validate_workspace._md_anchors 完全一致（fence-aware，围栏字符+长度都跟踪）。"""
    counts = {}
    fence = None

    def _feed(line):
        nonlocal fence
        fence, marker = _fence_step(fence, line)
        if marker or fence is not None:
            return
        m = _PRE_HEADING_RE.match(line)
        if m:
            slug = github_slug(m.group(1))
            counts[slug] = counts.get(slug, 0) + 1

    for line in pre:
        _feed(line)
    out = []
    for b in blocks:
        slug = entry_anchor(b["id"], b["title"])
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        out.append(slug if n == 0 else "%s-%d" % (slug, n))
        for line in b["lines"][1:]:        # 正文标题按序参与后续计数（条目标题行本身已计）
            _feed(line)
    return out


def render_index(title_msgid, chapters, lang, status_map=None):
    """Deterministic TOC: title heading, one '## 章' section per non-empty chapter, one link
    per entry. mistakes/index.md additionally joins each entry to its study_state mistake
    row by id and appends the status (rendered in the pack language)."""
    lines = [i18n.msg(title_msgid, lang), ""]
    for num, fname, blocks, pre in chapters:
        if not blocks:
            continue
        lines.append("## " + i18n.msg("notebook.chapter_heading", lang, num=num))
        lines.append("")
        for b, anchor in zip(blocks, anchors_for(pre, blocks)):
            # 链接文字里的 [] 会破坏 md 链接结构——换全角括号（标题原文在章文件里，无损）
            text = b["title"].replace("[", "〔").replace("]", "〕")
            line = "- [%s](%s#%s)" % (text, fname, anchor)
            if status_map and b["id"] in status_map:
                code = i18n.canon_row_status(status_map[b["id"]])
                line += " " + i18n.msg("mistakes.status_suffix", lang,
                                       status=i18n.display("row", code, lang))
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------- commands ----------------

def _read_body():
    """Entry body from STDIN (UTF-8). Empty body is a usage error — an empty notebook entry
    means the agent forgot to pipe the content; recording nothing must be LOUD, not silent."""
    body = sys.stdin.read()
    body = body.lstrip("\n").rstrip()
    if not body.strip():
        _die("正文为空——add-entry 的正文走 STDIN（UTF-8）；空条目拒绝写入（不要静默记录空讲解）")
    # 正文里裸露的条目标题行会把本条目从中间劈开（回读时被当成新块边界）；
    # 未闭合围栏会把后续条目整个吞进围栏——两种都在写入前拒绝，保住章文件永远可解析
    fence = None
    for line in body.splitlines():
        if _TEACHING_EXAMPLE_MARKER_RE.match(line):
            _die("正文包含保留的 teaching-example evidence 标记；请删掉，改用 --teaching-example")
        fence, marker = _fence_step(fence, line)
        if marker:
            continue
        if fence is None and _HEAD_RE.match(line):
            _die("正文包含裸露的条目标记行 %r——会破坏章文件解析；请放进 ``` 围栏再提交" % line)
    if fence is not None:
        _die("正文的代码围栏未闭合——会把后续条目吞进围栏；请补齐 ``` 再提交")
    return body


def _upsert(ws, dirname, ch_name, eid, title, new_lines, etype):
    """Replace-or-append the entry block in <dirname>/<ch_name>.
    替换键 = (id, type)（Codex r4）：同题先精讲再判分是契约规定的正常流——只按 id 匹配会让
    feedback 静默删掉学生的 walkthrough。类型从块元行反解（zh/en 标签都认）；无元行的手写块
    视为独立类型，永不被官方写入覆盖。Returns (path, new_content, pre, blocks, idx)."""
    d = _guard_dir(ws, dirname, create=True)
    path = os.path.join(d, ch_name)
    pre, blocks = ([], [])
    if os.path.lexists(path):
        pre, blocks = parse_chapter(_read_regular(path))
    rev = _label_to_type()
    idx = None
    for i, b in enumerate(blocks):
        b_type = rev.get(_block_meta(b["lines"])[0])
        if b["id"] == eid and b_type == etype:   # 同章同 id 同类型 = 同一条目：原地替换（幂等重讲）
            b["lines"], b["title"] = list(new_lines), title
            idx = i
            break
    if idx is None:
        blocks.append({"id": eid, "title": title, "lines": list(new_lines)})
        idx = len(blocks) - 1
    return path, render_chapter(pre, blocks), pre, blocks, idx


def cmd_add_entry(ws, args):
    if args.chapter < 1:
        _die("--chapter 必须 ≥ 1，当前 %d" % args.chapter)
    eid = (args.id or "").strip()
    id_problem = stable_item_id_problem(eid)
    if id_problem:
        _die("--id 非法：%r——%s" % (args.id or "", id_problem))
    title = re.sub(r"\s*[\r\n]+\s*", " ", args.title or "").strip() or eid
    if not entry_anchor(eid, title):
        _die(
            "--id/--title 生成的 Markdown anchor 为空；当 ID 只含标点或符号时，"
            "请提供至少含一个字母、数字、中文字符、下划线或连字符的 --title"
        )
    teaching_example = bool(getattr(args, "teaching_example", False))
    if teaching_example and args.type != "walkthrough":
        _die("--teaching-example requires --type walkthrough")
    body = _read_body()
    state = _load_state(ws)
    lang = _resolve_lang(args.lang, state)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    label = i18n.display("notebook_type", args.type, lang)
    new_lines = make_entry_lines(
        eid, title, label, ts, body, teaching_example=teaching_example)
    ch_name = "ch%02d.md" % args.chapter

    plan, overrides = [], {ch_name: None}
    nb_path, nb_content, nb_pre, nb_blocks, nb_idx = _upsert(
        ws, NOTEBOOK_DIR, ch_name, eid, title, new_lines, args.type)
    overrides[ch_name] = nb_content
    plan.append((nb_path, nb_content))
    # 目录与章文件同一原子计划：目录由（含本次改动的）全部章文件确定性重建
    nb_index = render_index("notebook.index_title",
                            _collect(ws, NOTEBOOK_DIR, overrides), lang)
    plan.append((os.path.join(ws, NOTEBOOK_DIR, INDEX_NAME), nb_index))
    if args.mistake:
        mi_path, mi_content, _p, _b, _i = _upsert(
            ws, MISTAKES_DIR, ch_name, eid, title, new_lines, args.type)
        plan.append((mi_path, mi_content))
        mi_index = render_index("mistakes.index_title",
                                _collect(ws, MISTAKES_DIR, {ch_name: mi_content}), lang,
                                status_map=_mistake_status_map(state))
        plan.append((os.path.join(ws, MISTAKES_DIR, INDEX_NAME), mi_index))
    # 回执必须给条目的**实际**锚（重复 slug 时 GitHub 加 -N 后缀）——小抄溯源链接照抄回执即合法
    anchor = anchors_for(nb_pre, nb_blocks)[nb_idx]
    _save_files(plan, "add-entry：%s/%s#%s（%s）%s｜ index.md 已重建"
                % (NOTEBOOK_DIR, ch_name, anchor, label,
                   "＋镜像 %s/%s " % (MISTAKES_DIR, ch_name) if args.mistake else ""))
    return 0


def cmd_rebuild(ws, args):
    state = _load_state(ws)
    lang = _resolve_lang(args.lang, state)
    plan = [
        (os.path.join(_guard_dir(ws, NOTEBOOK_DIR, create=True), INDEX_NAME),
         render_index("notebook.index_title", _collect(ws, NOTEBOOK_DIR), lang)),
        (os.path.join(_guard_dir(ws, MISTAKES_DIR, create=True), INDEX_NAME),
         render_index("mistakes.index_title", _collect(ws, MISTAKES_DIR), lang,
                      status_map=_mistake_status_map(state))),
    ]
    _save_files(plan, "rebuild：%s/index.md 与 %s/index.md 已从章文件确定性重建"
                % (NOTEBOOK_DIR, MISTAKES_DIR))
    return 0


def cmd_list(ws, args):
    rev = _label_to_type()
    mistake_ids = {(num, b["id"])
                   for num, _f, blocks, _p in _collect(ws, MISTAKES_DIR) for b in blocks}
    entries = []
    for num, fname, blocks, pre in _collect(ws, NOTEBOOK_DIR):
        # anchor 必须是重复后缀调整后的**实际**锚（Codex r5）——裸 slug 会让同名条目全跳到第一条
        for b, anchor in zip(blocks, anchors_for(pre, blocks)):
            label, ts = _block_meta(b["lines"])
            entries.append({
                "chapter": num,
                "id": b["id"],
                "title": b["title"],
                "type": rev.get(label, label),
                "time": ts,
                "file": NOTEBOOK_DIR + "/" + fname,
                "anchor": anchor,
                "mistake": (num, b["id"]) in mistake_ids,
            })
    if args.json:
        print(json.dumps({"entries": entries}, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("（笔记本为空——还没有任何落盘条目；add-entry 是唯一官方写入路径）")
        return 0
    for e in entries:
        print("- ch%02d [#%s] %s ｜ %s ｜ %s%s" % (
            e["chapter"], e["id"], e["title"], e["type"] or "-", e["time"] or "-",
            " ｜ 错题本" if e["mistake"] else ""))
    return 0


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Notebook engine: entries land in notebook/chNN.md (mistakes mirrored "
                    "into mistakes/); index.md files are deterministic derived views.")
    ap.add_argument("--workspace", required=True, help="workspace dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add-entry")
    p_add.add_argument("--chapter", type=int, required=True,
                       help="chapter number (>=1); the entry lands in notebook/ch<NN>.md")
    p_add.add_argument("--type", required=True, choices=list(TYPES),
                       help="entry kind (walkthrough/feedback/confusion/review)")
    p_add.add_argument("--id", required=True,
                       help="stable entry slug; the same --id in the same chapter REPLACES "
                            "the entry in place (idempotent re-teach)")
    p_add.add_argument("--title", default=None, help="entry title (defaults to the id)")
    p_add.add_argument("--mistake", action="store_true",
                       help="also mirror the entry into mistakes/ch<NN>.md and rebuild "
                            "mistakes/index.md")
    p_add.add_argument(
        "--teaching-example", action="store_true",
        help="mark this walkthrough as an explicitly taught manifest example; required by "
             "update_progress record-taught-example",
    )
    p_add.add_argument("--lang", choices=["zh", "en"], default=None,
                       help="heading language (default: study_state.json language, "
                            "falling back to zh)")
    p_reb = sub.add_parser("rebuild")
    p_reb.add_argument("--lang", choices=["zh", "en"], default=None,
                       help="heading language (default: study_state.json language, "
                            "falling back to zh)")
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true",
                        help='machine shape {"entries":[...]} (chapter order)')
    args = ap.parse_args(argv)
    ws = args.workspace
    if not os.path.isdir(ws):
        _die("workspace 不存在: %s" % ws)
    if args.cmd == "list":
        return cmd_list(ws, args)
    with workspace_publication_lock(ws):
        if args.cmd == "add-entry":
            return cmd_add_entry(ws, args)
        return cmd_rebuild(ws, args)


if __name__ == "__main__":
    sys.exit(run())
