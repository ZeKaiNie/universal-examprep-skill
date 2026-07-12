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
import argparse
from urllib.parse import unquote

# 同包内的 notebook 引擎是锚点词汇（github_slug）的唯一定义点——按 select_hard_questions.py
# 的先例把 scripts/ 放进 sys.path 再导入，validator 与生成器绝不各养一套 slug 规则。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notebook as _notebook

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
ASSET_ROLES = {"question_context", "answer_context", "figure", "table", "diagram", "worked_solution"}
# roles whose asset is shown to the student BEFORE asking — a requires_assets item needs one of these
# (an answer-side-only asset doesn't let the question be asked).
QUESTION_SIDE_ROLES = {"question_context", "figure", "diagram", "table"}
ASSET_TYPES = {"page_image", "crop_image", "diagram", "table_image", "other_image"}
QUESTION_TEXT_STATUS = {"full", "stub", "page_reference"}


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


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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


def _is_symlink(p):
    # dedicated seam for our own symlink checks. Tests mock THIS, not os.path.islink — because on
    # CPython <3.10 posixpath.realpath() itself calls os.path.islink, so mocking os.path.islink would
    # make realpath() try os.readlink() on a non-link and raise OSError (EINVAL) on Linux/py3.8.
    return os.path.islink(p)


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
    if _is_symlink(full):
        return full, "asset 不得为符号链接（可能指向工作区外）"
    # normcase both sides so a Windows casing difference doesn't falsely reject a contained asset
    ws_real = os.path.normcase(os.path.realpath(ws))
    real = os.path.normcase(os.path.realpath(full))
    if real != ws_real and not real.startswith(ws_real + os.sep):
        return full, "asset 经符号链接 / 父目录逃出工作区"
    return full, None


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


def validate(ws):
    """Return (errors, warnings, stats). errors may carry level 'error' or 'fatal'."""
    errors, warnings, stats = [], [], {}

    def err(msg, level="error"):
        errors.append({"level": level, "msg": msg})

    def warn(msg):
        warnings.append({"level": "warning", "msg": msg})

    if not os.path.isdir(ws):
        err(f"workspace directory不存在或不可读: {ws}", level="fatal")
        return errors, warnings, stats

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
    elif not has_wiki:
        err("缺少 references/wiki/ 目录")
    qb_path = os.path.join(ws, "references", "quiz_bank.json")
    qb_is_link = _is_symlink(qb_path)
    qb_escapes = os.path.isfile(qb_path) and not os.path.realpath(qb_path).startswith(ws_real + os.sep)
    has_qb = os.path.isfile(qb_path) and not qb_is_link and not qb_escapes
    if qb_is_link or qb_escapes:
        err("references/quiz_bank.json 经符号链接逃出工作区（指向工作区外的答案源）")
    elif not has_qb:
        err("缺少 references/quiz_bank.json")
    for name, label in (("study_plan.md", "复习计划"), ("study_progress.md", "进度文件")):
        rp = os.path.join(ws, name)
        if _is_symlink(rp) or (os.path.isfile(rp)
                                  and not os.path.realpath(rp).startswith(ws_real + os.sep)):
            err(f"{label} 经符号链接逃出工作区（技能会读/写这个路径）: {name}")
        elif not os.path.isfile(rp):
            err(f"缺少 {label}: {name}")

    # ---- wiki filenames must be safe ----
    wiki_files = set()
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
        stats["wiki_files"] = len(wiki_files)

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

    # ---- quiz_bank.json schema ----
    if has_qb:
        try:
            data = json.loads(_read(qb_path), parse_constant=_reject_const)
        except (ValueError, OSError) as e:
            err(f"quiz_bank.json 不是合法 JSON: {e}", level="fatal")
            return errors, warnings, stats
        if not isinstance(data, list):
            err("quiz_bank.json 顶层必须是 JSON 数组", level="fatal")
            return errors, warnings, stats
        stats["quiz_items"] = len(data)
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
            # id/type must be SCALAR before being used as set/dict keys: a malformed list/object id or
            # type would raise TypeError (unhashable) and crash before any structured error is returned.
            qid = q.get("id")
            if qid is not None and not isinstance(qid, (str, int, float, bool)):
                err(f"{tag} 的 id 必须是标量（字符串/数字），当前为 {type(qid).__name__}")
                qid = None
            if qid is not None:
                if qid in seen:
                    err(f"重复的题目 id: {qid}")
                seen.add(qid)
            t = q.get("type")
            if t is not None and not isinstance(t, str):
                err(f"{tag} 的 type 必须是字符串，当前为 {type(t).__name__}")
                t = None
            if t is not None:
                type_counts[t] = type_counts.get(t, 0) + 1
                if t not in SIX_TYPES:
                    err(f"{tag} 的 type 非法: {t!r}（应为 {sorted(SIX_TYPES)} 之一）")

            # per-type required/recommended
            if t == "choice" and not (isinstance(q.get("options"), list) and q.get("options")):
                err(f"{tag} choice 题必须有非空 options")
            if (t == "choice" and isinstance(q.get("options"), list) and q.get("options")
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
            if t == "subjective" and not q.get("keywords"):
                warn(f"{tag} subjective 题建议提供 keywords（要点检索判分）")
            if t == "diagram" and not q.get("diagram_type"):
                warn(f"{tag} diagram 题建议提供 diagram_type / 渲染说明（画图先跑算法再画）")
            if t == "code" and not (q.get("language") and (q.get("expected_behavior") or q.get("tests"))):
                warn(f"{tag} code 题建议提供 language 与 expected_behavior/tests")
            if t == "true_false":
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
                if role is not None and (not isinstance(role, str) or role not in ASSET_ROLES):
                    err(f"{tag} assets[{ai}] role 非法: {role!r}（应为 {sorted(ASSET_ROLES)} 中的字符串）")
                if atype is not None and (not isinstance(atype, str) or atype not in ASSET_TYPES):
                    err(f"{tag} assets[{ai}] type 非法: {atype!r}（应为 {sorted(ASSET_TYPES)} 中的字符串）")
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
                    asset_ok += 1
                    if isinstance(role, str) and role in QUESTION_SIDE_ROLES:
                        q_side_ok += 1
            if visual_required and not (isinstance(assets, list) and assets):
                err(f"{tag} {visual_gate_label} 但缺 assets——依赖图/表/Venn 的题没有上下文，"
                    "测验须 fail-closed（不可在不显示该图的情况下出此题）")
            elif visual_required and asset_ok == 0:
                err(f"{tag} {visual_gate_label} 但没有任何有效（安全且存在）的 asset，须 fail-closed")
            elif visual_required and q_side_ok == 0:
                err(f"{tag} {visual_gate_label} 但没有『题面侧』有效 asset（role 须含 "
                    f"{sorted(QUESTION_SIDE_ROLES)} 之一）——只有答案侧 asset（answer_context/worked_solution）"
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
            if not has_answer:
                if status == "unknown" or src in {"ai_generated", "unknown"}:
                    warn(f"{tag} 无 answer，已按 unknown/ai_generated 标注（考前需补全/核对）")
                else:
                    # ingest.py ACCEPTS answer-less questions (it warns, doesn't fail) and writes neither
                    # answer_status nor source — so a valid ingest output must NOT fail Tier 1. Keep this a
                    # WARNING (the "AI answer hidden as teacher" case above stays a hard error).
                    warn(f"{tag} 无 answer（建议补 answer，或标 answer_status=unknown / source=ai_generated）")
            elif src is None:
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
    if os.path.isfile(cheat_path):
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
    elif os.path.isfile(os.path.join(ws, "walkthrough.md")):
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
            st = json.loads(_read(state_path))
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
            if prefs is not None and not isinstance(prefs, dict):
                err(f"study_state.json 的 preferences 必须是对象，当前 {type(prefs).__name__}")
            # md is a GENERATED view — a phase mismatch means someone hand-patched it（下次渲染会丢）
            prog_path2 = os.path.join(ws, "study_progress.md")
            if isinstance(cp, int) and os.path.isfile(prog_path2):
                try:
                    m2 = re.search(r"(?:当前进行阶段|当前阶段)\D*?(\d+)", _read(prog_path2))
                    if m2 and int(m2.group(1)) != cp:
                        warn(f"study_progress.md 的阶段（{m2.group(1)}）与 study_state.json（{cp}）不一致——"
                             "md 是生成视图，请用 update_progress.py render 重建，不要手改 md")
                except OSError:
                    pass
        elif st is not None:
            err("study_state.json 顶层必须是 JSON 对象")

    return errors, warnings, stats


def _exit_code(errors):
    if any(e.get("level") == "fatal" for e in errors):
        return 2
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a cram workspace against docs/file-format.md")
    ap.add_argument("workspace", help="workspace directory")
    ap.add_argument("--json", action="store_true", help="output errors/warnings/stats as JSON")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    errors, warnings, stats = validate(args.workspace)
    code = _exit_code(errors)

    if args.json:
        print(json.dumps({"exit_code": code, "ok": code == 0, "workspace": args.workspace,
                          "errors": errors, "warnings": warnings, "stats": stats},
                         ensure_ascii=False, indent=2))
    else:
        print(f"工作区: {args.workspace}")
        if stats:
            print("  统计:", ", ".join(f"{k}={v}" for k, v in stats.items()))
        for e in errors:
            print(f"  [{'致命' if e['level'] == 'fatal' else '错误'}] {e['msg']}")
        for w in warnings:
            print(f"  [告警] {w['msg']}")
        verdict = {0: "✓ 通过（无错误）", 1: "✗ 有校验错误", 2: "✗ 工作区损坏/不可读"}[code]
        print(f"结论: {verdict}（错误 {sum(1 for e in errors)} / 告警 {len(warnings)}）")
    return code


if __name__ == "__main__":
    sys.exit(main())
