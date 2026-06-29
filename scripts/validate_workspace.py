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

SIX_TYPES = {"choice", "subjective", "diagram", "fill_blank", "true_false", "code"}
MATERIAL_SOURCES = {"teacher", "material"}
ALL_SOURCES = {"teacher", "material", "ai_generated", "mixed", "unknown"}
SAFE_WIKI = re.compile(r"^[\w.\-]+\.md$")
# capture the path token around references/wiki/. The LEADING class is path-only ([./\\]) so a real escape
# ("../", "/abs", "C:/…") is still captured and caught, while adjacent prose/CJK/punctuation
# (e.g. "见：references/wiki/ch1.md") is NOT swallowed into a false "path traversal" error.
WIKI_REF_RE = re.compile(r"([.\\/]*references/wiki/[\w.\-/\\]+)")
TRUE_FALSE_OK = {"true", "false", "t", "f", "yes", "no", "真", "假", "对", "错", "是", "否"}


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _reject_const(c):
    # json.loads accepts NaN/Infinity/-Infinity by default; reject them so quiz_bank.json is strict JSON.
    raise ValueError(f"非标准 JSON 常量 {c}（NaN/Infinity 不允许）")


def _is_symlink(p):
    # dedicated seam for our own symlink checks. Tests mock THIS, not os.path.islink — because on
    # CPython <3.10 posixpath.realpath() itself calls os.path.islink, so mocking os.path.islink would
    # make realpath() try os.readlink() on a non-link and raise OSError (EINVAL) on Linux/py3.8.
    return os.path.islink(p)


def validate(ws):
    """Return (errors, warnings, stats). errors may carry level 'error' or 'fatal'."""
    errors, warnings, stats = [], [], {}

    def err(msg, level="error"):
        errors.append({"level": level, "msg": msg})

    def warn(msg):
        warnings.append({"level": "warning", "msg": msg})

    if not os.path.isdir(ws):
        err(f"工作区目录不存在或不可读: {ws}", level="fatal")
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
                plan_phases = set(re.findall(r"阶段\s*(\d+)", _read(plan_path)))
                if plan_phases and m_cur.group(1) not in plan_phases:
                    warn(f"study_progress.md 当前阶段 {m_cur.group(1)} 不在 study_plan.md 的阶段列表 "
                         f"{sorted(int(x) for x in plan_phases)} 中（断点可能无法正确恢复）")
        except OSError:
            pass

    return errors, warnings, stats


def _exit_code(errors):
    if any(e.get("level") == "fatal" for e in errors):
        return 2
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验一个备考工作区是否符合 docs/file-format.md")
    ap.add_argument("workspace", help="工作区目录")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出 errors/warnings/stats")
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
