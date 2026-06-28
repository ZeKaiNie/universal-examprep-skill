#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resumable, quota-aware answer GENERATOR for the benchmark (real `claude -p`).

Fills the gaps the first run left:
  * algo (6.006) `material` arm — re-generate ONLY the items that errored out (rate-limit / API
    error) the first time; the valid material answers are kept.
  * psyc (PSYC 110) — generate all 3 arms x 3 models for real (the old results/raw.jsonl was MOCK).

Reality: the `material` arm dumps the whole course (~100K tokens for 6.006, ~230K for PSYC), which
burns the Claude subscription's 5-hour cap fast and may exceed context. So this is RESUMABLE:
  - successful answers are cached (gen_cache.jsonl) and appended to gen_answers.jsonl;
  - transient errors ("temporarily limiting" / API error) are retried with backoff;
  - a HARD subscription limit ("hit your limit / resets ...") is NOT retried — after a few in a row
    the pass STOPS cleanly so it can be resumed after the quota resets (just run gen.py again).

Tasks are ordered by feasibility: PSYC closed-book+skill (small) -> algo material reruns -> PSYC
material (largest, may be infeasible). Run repeatedly until it reports 0 remaining.

    python gen.py            # one resumable pass
    python gen.py --limit 3  # smoke test: only the first 3 tasks
"""
import os
import re
import sys
import json
import time
import argparse
import subprocess

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
GEN_ANS = os.path.join(HERE, "results", "matrix", "gen_answers.jsonl")
GEN_CACHE = os.path.join(HERE, "results", "matrix", "gen_cache.jsonl")
GEN_PROG = os.path.join(HERE, "results", "matrix", "gen_progress.txt")
MODELS = ["opus", "sonnet", "haiku"]

COURSES = {
    "algo": {"combined": "materials/algorithms_mit6006/_combined.txt",
             "skill_ws": "skill_workspace/mit6006_full", "raw_ws": "skill_workspace/rawfiles_algo",
             "qfile": "items/items_algo_full_q.jsonl"},
    "psyc": {"combined": "materials/psych_yale_psyc110/_combined.txt",
             "skill_ws": "skill_workspace/psyc110_full", "raw_ws": "skill_workspace/rawfiles_psyc",
             "qfile": "items/items_psyc_full_q.jsonl"},
}

CLOSEDBOOK = ("请仅凭你自己的知识回答下面的问题。如果你不确定或不知道，请直接回答「材料中未涵盖」或「不确定」，"
              "不要编造。\n\n问题：{q}\n\n请直接给出简洁答案。")
MATERIAL = ("请只依据下面的【课程材料】回答问题；材料中没有的内容，请直接回答「材料中未涵盖」，不要编造。\n\n"
            "【课程材料】\n{material}\n\n【问题】{q}\n\n请直接给出简洁答案。")
SKILL = ("你是备考教练。请依据本工作区已建立的 references/wiki/ 知识库回答问题；材料未涵盖的内容请回答"
         "「材料中未涵盖」，不要现场编造或重新推导。\n\n【问题】{q}\n\n请直接给出简洁答案。")
# 公平对照：无 skill，但 agent 能按需读取本文件夹里的原始讲义/习题（用 Read/Glob/Grep），而不是一次性全塞进提问
RAWFILES = ("本文件夹里是这门课的原始材料文件（讲义、习题）。请先用工具查阅相关文件，再据此回答问题；"
            "材料未涵盖的内容请回答「材料中未涵盖」，不要编造或凭记忆作答。\n\n【问题】{q}\n\n请直接给出简洁答案。")

_HARD = ("hit your limit", "usage limit", "resets ")
_TRANSIENT = ("temporarily limiting", "rate limited", "api error", "overloaded")


def load_q(path):
    return {d["id"]: d["question"] for d in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip())}


def read(path):
    p = os.path.join(HERE, path)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def classify(text):
    t = (text or "").lower()
    if any(h in t for h in _HARD):
        return "hard"
    if any(x in t for x in _TRANSIENT):
        return "transient"
    return "ok"


def run_claude(prompt, model, cwd=None, skill=False, timeout=900):
    # Pass the prompt via STDIN, not argv — the material arm dumps a ~100-230K-char course, which
    # blows Windows' ~32K command-line limit (WinError 206) if passed as an argument.
    args = ["claude", "-p", "--output-format", "json", "--model", model]
    if skill:
        args += ["--allowedTools", "Read", "Glob", "Grep"]
    try:
        p = subprocess.run(args, cwd=os.path.join(HERE, cwd) if cwd else HERE, input=prompt,
                           capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        try:
            data = json.loads(p.stdout)
            return data.get("result", "") or "", data.get("total_cost_usd")
        except json.JSONDecodeError:
            return (p.stdout or p.stderr or "").strip(), None
    except subprocess.TimeoutExpired:
        return "TIMEOUT", None
    except Exception as e:                             # never let one bad call kill the whole pass
        return f"API Error: {e}", None


def generate_one(course, model, arm, qid, q, combined):
    if arm == "closedbook":
        return run_claude(CLOSEDBOOK.format(q=q), model)
    if arm == "material":
        return run_claude(MATERIAL.format(material=combined, q=q), model)
    if arm == "rawfiles":
        return run_claude(RAWFILES.format(q=q), model, cwd=COURSES[course]["raw_ws"], skill=True)
    return run_claude(SKILL.format(q=q), model, cwd=COURSES[course]["skill_ws"], skill=True)


def build_tasks():
    """Ordered by feasibility. Returns list of (course, model, arm, id)."""
    tasks = []
    qalgo = load_q(os.path.join(HERE, COURSES["algo"]["qfile"]))
    qpsyc = load_q(os.path.join(HERE, COURSES["psyc"]["qfile"]))
    # 0) rawfiles arm — fair no-skill agentic baseline (read raw files on demand), BOTH courses x 3 models
    for course, q in (("algo", qalgo), ("psyc", qpsyc)):
        for m in MODELS:
            for qid in q:
                tasks.append((course, m, "rawfiles", qid))
    # 1) PSYC closed-book + skill (small prompts) — the feasible, high-value real second course
    for arm in ("closedbook", "skill"):
        for m in MODELS:
            for qid in qpsyc:
                tasks.append(("psyc", m, arm, qid))
    # 2) algo material reruns — only the items that errored the first time
    ERR = ("hit your limit", "api error", "rate limited", "temporarily limiting", "usage limit")
    for r in (json.loads(l) for l in open(os.path.join(HERE, "results/matrix/answers.jsonl"),
                                          encoding="utf-8") if l.strip()):
        if r["tag"] == "matrix" and r["arm"] == "material":
            a = (r.get("answer") or "").lower()
            if any(e in a for e in ERR):
                tasks.append(("algo", r["model"], "material", r["id"]))
    # 3) PSYC material (largest; may exceed context) — attempted last
    for m in MODELS:
        for qid in qpsyc:
            tasks.append(("psyc", m, "material", qid))
    return tasks


def load_cache():
    cache = set()
    if os.path.exists(GEN_CACHE):
        for d in (json.loads(l) for l in open(GEN_CACHE, encoding="utf-8") if l.strip()):
            cache.add(tuple(d["key"]))
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    qcache = {c: load_q(os.path.join(HERE, COURSES[c]["qfile"])) for c in COURSES}
    combined = {c: read(COURSES[c]["combined"]) for c in COURSES}
    tasks = build_tasks()
    if args.limit:
        tasks = tasks[:args.limit]
    done = load_cache()
    cf = open(GEN_CACHE, "a", encoding="utf-8")
    af = open(GEN_ANS, "a", encoding="utf-8")

    n_ok = n_fail = hard_streak = 0
    t0 = time.time()
    todo = [t for t in tasks if t not in done]
    print(f"[gen] 任务 {len(tasks)}，已完成 {len(tasks)-len(todo)}，本次待跑 {len(todo)}")
    for i, (course, model, arm, qid) in enumerate(todo, 1):
        q = qcache[course].get(qid, "")
        ans, cost = "", None
        for attempt in range(3):                      # retry transient errors with backoff
            ans, cost = generate_one(course, model, arm, qid, q, combined[course])
            kind = classify(ans)
            if kind == "ok":
                break
            if kind == "hard":
                break                                 # don't retry a hard subscription limit
            time.sleep(5 * (attempt + 1) ** 2)        # 5s, 20s, 45s
        kind = classify(ans)
        if kind == "ok" and ans.strip():
            key = [course, model, arm, qid]
            cf.write(json.dumps({"key": key}, ensure_ascii=False) + "\n"); cf.flush()
            af.write(json.dumps({"course": course, "model": model, "arm": arm, "id": qid,
                                 "answer": ans, "cost": cost}, ensure_ascii=False) + "\n"); af.flush()
            n_ok += 1; hard_streak = 0
        else:
            n_fail += 1
            if kind == "hard":
                hard_streak += 1
        msg = f"{i}/{len(todo)} ok={n_ok} fail={n_fail} hard_streak={hard_streak} {int(time.time()-t0)}s | last={course}/{model}/{arm}/{qid}:{kind}"
        with open(GEN_PROG, "w", encoding="utf-8") as pf:
            pf.write(msg + "\n")
        if i % 5 == 0:
            print("  " + msg)
        if hard_streak >= 6:                          # quota exhausted -> stop cleanly, resume later
            print(f"\n[gen] 连续 {hard_streak} 次撞订阅配额上限，本次到此停（已存进度）。配额恢复后再跑 gen.py 续。")
            break
    cf.close(); af.close()
    remaining = len([t for t in tasks if t not in load_cache()])
    print(f"\n[gen] 本次完成 {n_ok}，失败 {n_fail}，剩余 {remaining}。用时 {int(time.time()-t0)}s")
    print(f"      -> {GEN_ANS}")


if __name__ == "__main__":
    main()
