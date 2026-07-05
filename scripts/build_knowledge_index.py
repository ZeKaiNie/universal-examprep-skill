#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build the knowledge-point index (A2) — 知识点 ↔ 章节/wiki/题目 的映射地基。

Reads quiz_bank.json (knowledge_points tags) + study_plan.md (chapter→wiki placement, same parser
family as the visual index) and writes <workspace>/references/knowledge_index.json:

    {"knowledge_points": {"条件概率": {"chapters": ["2"], "wiki_files": ["ch2_trees.md"],
                                       "question_ids": ["q1", ...]}, ...},
     "untagged_questions": N, "warnings": [...]}

Downstream: A5 的讲解模板第 7 步（知识点溯源/可点击定位）、A7 难度评分（跨知识点数）。
Honest scope: 页码级引用需要 wiki 内容标注，A5 再补；本索引只到 章节/wiki 文件/题目 三级。
Pure stdlib; exit 0 ok · 2 bad input.
"""
import argparse
import json
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass


def _die(msg):
    sys.stderr.write("build_knowledge_index: " + msg + "\n")
    raise SystemExit(2)


_PHASE_RE = r"(?:阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+))"


def plan_wiki_map(text):
    """{chapter_str: [wiki basenames]} from study_plan.md (headings/table/checklist, 同视觉索引口径)."""
    m, cur = {}, None
    for ln in (text or "").splitlines():
        s = ln.strip()
        structural = s.startswith("#") or s.startswith("|") or bool(re.match(r"[-*]\s", s))
        pm = re.search(_PHASE_RE, s) if structural else None
        n = int(next(g for g in pm.groups() if g)) if pm else None
        if n is not None and (s.startswith("#") or s.startswith("|") or re.match(r"[-*]\s", s)):
            cur = n                                    # checklist 行（- [ ] 阶段 1）也建立阶段上下文
            m.setdefault(str(cur), [])
        target = n if n is not None else cur
        for w in re.findall(r"references/wiki/([^\s\)\]\"'`]+?\.md)", s):
            if target is not None and w not in m.setdefault(str(target), []):
                m[str(target)].append(w)
    return m


def run(argv=None):
    ap = argparse.ArgumentParser(description="Build the knowledge-point index from quiz_bank knowledge_points + study_plan.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", default=None, help="default <workspace>/references/knowledge_index.json")
    args = ap.parse_args(argv)

    bank_path = os.path.join(args.workspace, "references", "quiz_bank.json")
    if not os.path.isfile(bank_path):
        _die("找不到题库: %s" % bank_path)
    try:
        bank = json.load(open(bank_path, encoding="utf-8"))
    except ValueError as e:
        _die("quiz_bank.json 不是合法 JSON: %s" % e)
    if not isinstance(bank, list):
        _die("quiz_bank.json 顶层必须是数组")

    plan_path = os.path.join(args.workspace, "study_plan.md")
    wiki_map, warnings = {}, []
    if os.path.isfile(plan_path):
        wiki_map = plan_wiki_map(open(plan_path, encoding="utf-8").read())
    else:
        warnings.append("no_study_plan: 无法映射 章节→wiki 文件（索引仍含 章节/题目 两级）")

    kp_index, untagged = {}, 0
    for q in bank:
        if not (isinstance(q, dict) and q.get("id") is not None):
            continue
        kps = q.get("knowledge_points")
        if not kps or not isinstance(kps, list):
            untagged += 1
            continue
        # a bank item may carry BOTH the original chapter label and the review phase（chapter:3, phase:1），
        # and the chapter value may be a wiki basename——都要能解析到 plan 的 wiki 放置
        keys = []
        for v in (q.get("chapter"), q.get("phase")):
            if v is not None and str(v) not in keys:
                keys.append(str(v))
        wikis = []
        for kkey in keys:
            for w in wiki_map.get(kkey, []):
                if w not in wikis:
                    wikis.append(w)
            base = kkey if kkey.endswith(".md") else kkey + ".md"
            for files in wiki_map.values():            # chapter 写的是 wiki 文件名 → 反查
                for w in files:
                    if w == base or w == kkey or w.startswith(kkey + "_"):   # ch1 → ch1_stack_queue.md
                        if w not in wikis:
                            wikis.append(w)
        for k in kps:
            if not isinstance(k, str) or not k.strip():
                continue
            rec = kp_index.setdefault(k.strip(), {"chapters": [], "wiki_files": [], "question_ids": []})
            for kkey in keys:
                if kkey not in rec["chapters"]:
                    rec["chapters"].append(kkey)
            for w in wikis:
                if w not in rec["wiki_files"]:
                    rec["wiki_files"].append(w)
            if str(q["id"]) not in rec["question_ids"]:   # 同一题重复同名标签只记一次
                rec["question_ids"].append(str(q["id"]))

    out = args.out or os.path.join(args.workspace, "references", "knowledge_index.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"knowledge_points": kp_index, "untagged_questions": untagged,
                   "warnings": warnings, "generated_by": "build_knowledge_index.py"},
                  f, ensure_ascii=False, indent=2)
    print("[+] knowledge_index: %s（%d 个知识点；%d 题未打标签）" % (out, len(kp_index), untagged))
    if untagged:
        print("[!] 未打标签的题不进知识点索引——补 knowledge_points 后重跑")
    return 0


if __name__ == "__main__":
    sys.exit(run())
