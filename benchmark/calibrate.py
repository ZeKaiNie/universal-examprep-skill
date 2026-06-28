#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Human-vs-judge calibration (Cohen's kappa) — the gate before trusting any LLM-judged number.

We just caught the LLM judge marking exact-match answers as hallucinations; this tool quantifies how
much the (fixed) judge can now be trusted, by comparing it against YOUR hand labels on a sample.

Two steps:

  1) sample — draw a STRATIFIED sample (half judge-correct, half judge-wrong, so kappa isn't
              degenerate) and write a calibration sheet that HIDES the judge's verdict. You fill the
              `human_correct` column with 1 (correct/acceptable) or 0 (wrong) — judging ONLY from the
              question, the gold answer, and the reference span.

         python calibrate.py sample --n 24 --course both --seed 7

  2) kappa  — after you fill the sheet, compute Cohen's kappa(human, judge) + raw agreement, and list
              every item where you and the judge DISAGREE (those are the judge's likely errors).

         python calibrate.py kappa

Files (under calibration/, gitignored — they embed answer details):
  calibration/calibration_sheet.csv   <- you edit the human_correct column here
  calibration/.calibration_key.jsonl  <- hidden judge verdicts, written by `sample`, read by `kappa`

Rule of thumb (README): trust the judge's numbers only at kappa >= ~0.6.
"""
import os
import sys
import csv
import json
import random
import argparse

import stats as S
import rejudge as RJ

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
CAL = os.path.join(HERE, "calibration")
SHEET = os.path.join(CAL, "calibration_sheet.csv")
KEY = os.path.join(CAL, ".calibration_key.jsonl")
CACHE = os.path.join(HERE, "results", "matrix", "judge_cache.jsonl")

_FIELDS = ["ref_id", "course", "answerable", "question", "gold_answer", "reference_span",
           "model_answer", "human_correct"]


def _flat(s):
    return " ".join(str(s or "").split())


def load_cache():
    cache = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding="utf-8"):
            line = line.strip()
            if line:
                d = json.loads(line)
                cache[d["k"]] = d["score"]
    return cache


def build_pool(course):
    gold = {"algo": RJ.load_gold(RJ.ALGO_GOLD), "psyc": RJ.load_gold(RJ.PSYC_GOLD)}
    cache = load_cache()
    pool = []
    for row in RJ.unified_rows(course):
        item = gold[row["course"]].get(row["id"])
        if not item:
            continue
        if RJ.is_infra_error(row["answer"]):          # skip rate-limit/API error strings — not answers
            continue
        sc = cache.get(RJ.cache_key(row["id"], row["answer"]))
        if not sc:                                    # only items with an authoritative verdict
            continue
        pool.append({
            "course": row["course"], "model": row["model"], "arm": row["arm"], "id": row["id"],
            "answerable": bool(item.get("answerable", True)),
            "question": item.get("question", ""), "gold_answer": item.get("gold_answer", ""),
            "reference_span": item.get("supporting_span", ""), "answer": row["answer"],
            "judge_correct": 1 if sc.get("correct") else 0,
        })
    return pool


def cmd_sample(args):
    pool = build_pool(args.course)
    if not pool:
        sys.exit("[-] 没有可抽样的条目（先跑 rejudge.py --llm 生成 judge_cache.jsonl）")
    rng = random.Random(args.seed)
    pos = [p for p in pool if p["judge_correct"] == 1]
    neg = [p for p in pool if p["judge_correct"] == 0]
    rng.shuffle(pos); rng.shuffle(neg)
    half = args.n // 2
    pick = pos[:half] + neg[:args.n - half]
    rng.shuffle(pick)

    os.makedirs(CAL, exist_ok=True)
    with open(SHEET, "w", encoding="utf-8-sig", newline="") as f, open(KEY, "w", encoding="utf-8") as kf:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for i, p in enumerate(pick, 1):
            ref = f"cal_{i:03d}"
            note = "" if p["answerable"] else "  ←【越界题：材料无答案，正确=老实弃答】"
            w.writerow({"ref_id": ref, "course": p["course"], "answerable": int(p["answerable"]),
                        "question": _flat(p["question"]) + note, "gold_answer": _flat(p["gold_answer"]),
                        "reference_span": _flat(p["reference_span"]), "model_answer": _flat(p["answer"]),
                        "human_correct": ""})
            kf.write(json.dumps({"ref_id": ref, "judge_correct": p["judge_correct"]}, ensure_ascii=False) + "\n")
    print(f"[+] 抽样 {len(pick)} 条（判对 {min(half,len(pos))} / 判错 {len(pick)-min(half,len(pos))}）"
          f"，已写出待填表：\n    {SHEET}")
    print("    用 Excel/编辑器打开，给 human_correct 列填 1（对/可接受）或 0（错）；填完跑：python calibrate.py kappa")
    print("    判定标准：只看 question + gold_answer + reference_span 判 model_answer 对不对；越界题以「是否老实弃答」为准。")


def cmd_kappa(args):
    if not (os.path.exists(SHEET) and os.path.exists(KEY)):
        sys.exit("[-] 找不到 calibration_sheet.csv / .calibration_key.jsonl，先跑：python calibrate.py sample")
    key = {d["ref_id"]: d["judge_correct"] for d in (json.loads(l) for l in open(KEY, encoding="utf-8") if l.strip())}
    rows = list(csv.DictReader(open(SHEET, encoding="utf-8-sig")))
    human, judge, disagree, blank = [], [], [], 0
    for r in rows:
        hv = (r.get("human_correct") or "").strip()
        if hv not in ("0", "1"):
            blank += 1
            continue
        ref = r["ref_id"]
        if ref not in key:
            continue
        h, j = int(hv), key[ref]
        human.append(h); judge.append(j)
        if h != j:
            disagree.append((ref, j, h, r.get("question", "")[:70]))
    n = len(human)
    if n == 0:
        sys.exit(f"[-] 还没有已填的 human_correct（{blank} 行为空）。先在 {SHEET} 填好再跑。")
    agree = sum(1 for h, j in zip(human, judge) if h == j) / n
    k = S.cohen_kappa(human, judge)
    print(f"=== 人工 vs 裁判一致性（n={n}，未填 {blank}）===")
    print(f"  原始一致率 agreement = {agree*100:.1f}%")
    print(f"  Cohen's kappa        = {k:.3f}   ->  {'可信(>=0.6)' if k >= 0.6 else '偏低，先改进裁判/题目再信任数字'}")
    if disagree:
        print(f"\n  人机分歧 {len(disagree)} 条（judge=裁判判, human=你判；这些是裁判最可能错的地方）：")
        for ref, j, h, q in disagree:
            print(f"    {ref}: judge={j} human={h} | {q}")
    else:
        print("\n  无分歧 —— 裁判与你完全一致。")


def main():
    ap = argparse.ArgumentParser(description="人工 vs 裁判 kappa 校准")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sample", help="抽样生成待填校准表")
    sp.add_argument("--n", type=int, default=24)
    sp.add_argument("--course", default="both", choices=["algo", "psyc", "both"])
    sp.add_argument("--seed", type=int, default=7)
    sp.set_defaults(func=cmd_sample)
    kp = sub.add_parser("kappa", help="读已填表，算 kappa + 列分歧")
    kp.set_defaults(func=cmd_kappa)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
