#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Re-score existing benchmark runs with the FIXED judge — no answer regeneration.

The original judge silently scored unparseable replies (and exact-match answers like 'Word-RAM')
as hallucinations, suppressing the skill arm to a fake uniform 38%. This re-scores the SAME stored
answers against the gold with judge.py's hardened logic.

Two courses:
  * algo  (MIT 6.006)  : results/matrix/answers.jsonl  — 3 arms x 3 models (+ convergence rounds)
  * psyc  (Yale PSYC110): results/raw.jsonl            — 2 arms (baseline/skill), single model

Modes:
  --deterministic (default): ZERO Claude calls — numeric + lexical-exact-match + abstention only.
                             Undecidable items are flagged judge_error and counted NOT-correct,
                             so correctness is a trustworthy LOWER BOUND.
  --llm                    : also call `claude -p` for items the deterministic paths can't settle
                             (authoritative faithfulness / hallucination). Costs quota; ~hours.

Robustness: every LLM verdict is CACHED by (item id + answer) in results/matrix/judge_cache.jsonl,
so the long run is RESUMABLE (re-run continues where it stopped) and identical answers aren't paid
for twice. Writes results/matrix/summary_corrected.json (does NOT touch the published summary.json).

    python rejudge.py --deterministic            # free lower bound, both courses
    python rejudge.py --llm --course both        # authoritative, both courses (slow)
"""
import os
import sys
import json
import time
import hashlib
import argparse
import subprocess

import judge as J

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX = os.path.join(HERE, "results", "matrix")
ALGO_ANS = os.path.join(MATRIX, "answers.jsonl")
PSYC_RAW = os.path.join(HERE, "results", "raw.jsonl")
ALGO_GOLD = os.path.join(HERE, "items", "items_algo_full.jsonl")
PSYC_GOLD = os.path.join(HERE, "items", "items_psyc_full.jsonl")
CACHE = os.path.join(MATRIX, "judge_cache.jsonl")
PROGRESS = os.path.join(MATRIX, "rejudge_progress.txt")


def load_jsonl(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(json.loads(line))
    return out


def load_gold(path):
    return {d["id"]: d for d in load_jsonl(path)}


def claude_judge(model):
    def ask(prompt):
        args = ["claude", "-p", prompt, "--output-format", "json"]
        if model:
            args += ["--model", model]
        try:
            p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=600)
            return json.loads(p.stdout).get("result", "") or ""
        except Exception:
            return ""                                   # -> judge_error (flagged, never a fake hallucination)
    return ask


GEN_ANS = os.path.join(MATRIX, "gen_answers.jsonl")
_ERR = ("hit your limit", "api error", "rate limited", "temporarily limiting", "usage limit",
        "你已达到", "prompt is too long")


def is_infra_error(a):
    """An answer that is a rate-limit / API / context error string, NOT a model answer — must be
    EXCLUDED from correctness (counting it as 'wrong' is what made the dump arm look like 2%)."""
    a = (a or "").lower()
    return any(e in a for e in _ERR)


def _gen_rows():
    return load_jsonl(GEN_ANS) if os.path.exists(GEN_ANS) else []


def unified_rows(course):
    """Normalize runs to {course, tag, model, arm, id, answer}. PSYC now comes from the REAL
    generated answers (gen_answers.jsonl), NOT the old MOCK results/raw.jsonl; algo `material`
    items that errored out the first time are PATCHED with their reruns from gen_answers.jsonl."""
    rows, gen = [], _gen_rows()
    if course in ("algo", "both"):
        rerun = {(d["model"], d["id"]): d for d in gen
                 if d["course"] == "algo" and d["arm"] == "material"}
        for d in load_jsonl(ALGO_ANS):
            ans, cost = d.get("answer", ""), d.get("cost")
            if d["tag"] == "matrix" and d["arm"] == "material" and is_infra_error(ans) \
                    and (d["model"], d["id"]) in rerun:
                rd = rerun[(d["model"], d["id"])]
                ans = rd.get("answer", "")                         # patch error with a clean rerun
                cost = rd.get("cost", cost)                        # and ITS OWN cost, not the failed attempt's
            rows.append({"course": "algo", "tag": d["tag"], "model": d["model"],
                         "arm": d["arm"], "id": d["id"], "answer": ans, "cost": cost})
        for d in gen:                                              # NEW: fair no-skill agentic arm
            if d["course"] == "algo" and d["arm"] == "rawfiles":
                rows.append({"course": "algo", "tag": "matrix", "model": d["model"], "arm": "rawfiles",
                             "id": d["id"], "answer": d.get("answer", ""), "cost": d.get("cost")})
    if course in ("psyc", "both"):
        for d in gen:                                              # REAL psyc answers, 3 arms x 3 models
            if d["course"] == "psyc":
                rows.append({"course": "psyc", "tag": "psyc", "model": d["model"], "arm": d["arm"],
                             "id": d["id"], "answer": d.get("answer", ""), "cost": d.get("cost")})
    return rows


def cache_key(item_id, answer):
    return hashlib.sha1((item_id + "\x00" + (answer or "")).encode("utf-8")).hexdigest()


def load_cache():
    cache = {}
    if os.path.exists(CACHE):
        for d in load_jsonl(CACHE):
            cache[d["k"]] = d["score"]
    return cache


def cell_label(row):
    if row["course"] == "psyc":
        return f"psyc|{row['model']}|{row['arm']}"
    if row["tag"] == "matrix":
        return f"{row['model']}|{row['arm']}"
    return row["tag"]                                   # conv_r1/r2/r3


def aggregate(scores):
    # infra-errors (rate-limit / context) are EXCLUDED — they are not model answers, so counting
    # them as wrong is exactly what made the dump arm look like a fake 2%.
    ans = [s for s in scores if s.get("answerable", True) and not s.get("infra_error")]
    oos = [s for s in scores if not s.get("answerable", True) and not s.get("infra_error")]
    decided = [s for s in ans if not s.get("judge_error") and s.get("faithfulness") is not None]

    def rate(xs, key):
        xs = [x for x in xs if x.get(key) is not None]
        return round(sum(1 for x in xs if x.get(key)) / len(xs), 4) if xs else None

    return {
        "n": len(scores), "n_answerable": len(ans), "n_oos": len(oos),
        "correct": rate(ans, "correct"),
        "faithfulness": round(sum(s["faithfulness"] for s in decided) / len(decided), 4) if decided else None,
        "hallucination": rate(ans, "hallucinated"),
        "abstention_oos": rate(oos, "abstained"),
        "n_judge_error": sum(1 for s in ans if s.get("judge_error")),
        "n_lexical": sum(1 for s in ans if s.get("scored_by") == "lexical"),
        "n_infra_error": sum(1 for s in scores if s.get("infra_error")),
    }


def export_rows(row, item, sc, infra=False):
    """Build (answer_row, score_row) for benchmark/aggregate_matrix.py from ONE judged item.

    PURE — no IO / LLM / network. `row` carries course/model/arm/id; `item` is the gold (for
    `answerable`); `sc` is the per-item judge verdict (or the infra placeholder). This is the
    committed bridge between rejudge/judge and aggregate_matrix.py: the score row carries exactly
    the fields aggregate_matrix consumes, and the answer row is keyed identically so the join is
    1:1 (no orphans / missing rows). The answer row also carries `status` ("infra_error"/"ok") and,
    when the source row has one, the per-answer `cost_usd` — so the aggregator can exclude infra
    failures and report real costs (not a fake $0). `scored_by` preserves the judge's own label
    (lexical/llm/judge_error/infra_error) and falls back to "deterministic" for the unlabeled
    deterministic paths (numeric / unanswerable-abstention)."""
    answerable = bool(item.get("answerable", True))
    base = {"course": row["course"], "model": row["model"], "arm": row["arm"], "item_id": row["id"]}
    answer_row = dict(base, answerable=answerable, status=("infra_error" if infra else "ok"))
    cost = row.get("cost")                              # carry the per-answer generation cost through
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        answer_row["cost_usd"] = cost                  # so the bridge's cost_per_q/totals aren't a fake $0
    score_row = dict(
        base,
        answerable=answerable,
        correct=sc.get("correct"),
        hallucinated=sc.get("hallucinated"),
        abstained=sc.get("abstained"),
        judge_error=bool(sc.get("judge_error", False)),
        faithfulness=sc.get("faithfulness"),
        scored_by=(sc.get("scored_by") or "deterministic"),
    )
    return answer_row, score_row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--deterministic", action="store_true",
                    help="确定性模式（默认）：零 LLM 调用，仅数值/词面精确/弃答；与不加 --llm 等价。"
                         "显式给出便于与 --scores-out 搭配照文档跑；与 --llm 互斥。")
    ap.add_argument("--course", default="both", choices=["algo", "psyc", "both"])
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 行（验证用）")
    ap.add_argument("--scores-out", default=None,
                    help="把每题判分导出为 aggregate_matrix.py 可读的 score 行 JSONL（显式路径；零成本，"
                         "deterministic 模式不调用 LLM；只导出 matrix/psyc 单元格，不含 conv_* 收敛轮）")
    ap.add_argument("--answers-out", default=None,
                    help="与 score 行严格按 (course,model,arm,item_id) 对齐的 answer 行 JSONL（带 "
                         "status/cost_usd）；用 --scores-out 时【必填】，aggregate_matrix.py 靠它排除 "
                         "infra 失败并统计成本")
    args = ap.parse_args()
    if args.deterministic and args.llm:
        sys.stderr.write("rejudge: --deterministic 与 --llm 互斥（确定性是默认；要 LLM 复判就只给 --llm）\n")
        raise SystemExit(2)
    if args.scores_out and not args.answers_out:
        # the bridge must emit BOTH halves: the answer row carries status (infra_error) + cost_usd that
        # aggregate_matrix.py needs. Pairing --scores-out with native answer rows (no status) would make
        # the aggregator miss infra exclusions and costs — so require the aligned answer export too.
        sys.stderr.write("rejudge: --scores-out 需同时给 --answers-out——导出的 answer 行带 status/cost，"
                         "aggregate_matrix.py 要靠它排除 infra 失败并统计成本；缺了会把失败误计为可答题\n")
        raise SystemExit(2)
    if args.scores_out and args.answers_out \
            and os.path.abspath(args.scores_out) == os.path.abspath(args.answers_out):
        # two open(...,"w") to the same file would truncate over each other → a corrupt, half-written
        # export. The bridge needs TWO distinct JSONL files.
        sys.stderr.write("rejudge: --scores-out 与 --answers-out 不能指向同一个文件（会互相截断覆盖）\n")
        raise SystemExit(2)

    gold = {"algo": load_gold(ALGO_GOLD), "psyc": load_gold(PSYC_GOLD)}
    rows = unified_rows(args.course)
    if args.limit:
        rows = rows[:args.limit]
    ask = claude_judge(args.judge_model) if args.llm else None
    cache = load_cache() if args.llm else {}
    cfile = open(CACHE, "a", encoding="utf-8") if args.llm else None

    # optional aggregate_matrix.py export (explicit paths only; never writes results/** on its own)
    scores_out = open(args.scores_out, "w", encoding="utf-8") if args.scores_out else None
    answers_out = open(args.answers_out, "w", encoding="utf-8") if args.answers_out else None
    exported = set()
    n_exported = [0]

    def _export(row, item, sc, infra):
        if scores_out is None and answers_out is None:
            return
        if "|" not in cell_label(row):     # only matrix/psyc cells; conv_* rounds share arm+ids → would
            return                         # collide on (course,model,arm,item_id), so exclude them
        a_row, s_row = export_rows(row, item, sc, infra)
        ek = (a_row["course"], a_row["model"], a_row["arm"], a_row["item_id"])
        if ek in exported:
            # FAIL LOUD, never silently drop: rejudge.aggregate() counts the duplicate while a silent
            # skip would omit it from the export, so the export→aggregate path would disagree with
            # summary_corrected.json. (aggregate_matrix.py also rejects duplicate keys.)
            sys.stderr.write("rejudge: 导出时遇到重复 (course,model,arm,item_id)=%s——"
                             "输入里有重复行，请先去重再导出\n" % (ek,))
            raise SystemExit(2)
        exported.add(ek)
        n_exported[0] += 1
        if answers_out:
            answers_out.write(json.dumps(a_row, ensure_ascii=False) + "\n")
        if scores_out:
            scores_out.write(json.dumps(s_row, ensure_ascii=False) + "\n")

    cells, missing, llm_calls, t0 = {}, 0, 0, time.time()
    for i, row in enumerate(rows, 1):
        item = gold[row["course"]].get(row["id"])
        if not item:
            missing += 1
            continue
        if is_infra_error(row["answer"]):              # rate-limit/context error -> exclude, don't judge
            isc = {"id": row["id"], "answerable": bool(item.get("answerable", True)),
                   "infra_error": True, "correct": False, "abstained": False,
                   "hallucinated": None, "faithfulness": None, "scored_by": "infra_error"}
            cells.setdefault(cell_label(row), []).append(isc)
            _export(row, item, isc, True)
            continue
        k = cache_key(row["id"], row["answer"])
        if k in cache:
            sc = cache[k]
        else:
            before = J.judge_answer(item, row["answer"], None, 1)        # try deterministic first (free)
            if args.llm and before.get("scored_by") not in ("lexical", "numeric") \
                    and item.get("answerable", True) and before.get("answer_type") != "numeric":
                sc = J.judge_answer(item, row["answer"], ask, args.repeats)  # needs the LLM
                llm_calls += 1
            else:
                sc = before
            if args.llm:
                cache[k] = sc
                cfile.write(json.dumps({"k": k, "score": sc}, ensure_ascii=False) + "\n")
                cfile.flush()
        cells.setdefault(cell_label(row), []).append(sc)
        _export(row, item, sc, False)
        if i % 20 == 0:
            msg = f"{i}/{len(rows)} rows | {llm_calls} llm calls | {int(time.time()-t0)}s"
            with open(PROGRESS, "w", encoding="utf-8") as pf:
                pf.write(msg + "\n")
            if args.llm:
                print("  ..." + msg)
    if cfile:
        cfile.close()
    for fh in (scores_out, answers_out):
        if fh:
            fh.close()
    if scores_out or answers_out:
        print("[+] 导出 %d 条聚合用行%s%s（喂给 aggregate_matrix.py）" % (
            n_exported[0],
            (" -> scores: %s" % args.scores_out) if scores_out else "",
            (" -> answers: %s" % args.answers_out) if answers_out else ""))

    algo_matrix = {k: aggregate(v) for k, v in cells.items() if "|" in k and not k.startswith("psyc|")}
    algo_conv = {k: aggregate(v) for k, v in cells.items() if k.startswith("conv_")}
    psyc = {k: aggregate(v) for k, v in cells.items() if k.startswith("psyc|")}
    mode = "llm" if args.llm else "deterministic(exact-match lower bound)"
    out = {"mode": mode, "judge_model": args.judge_model if args.llm else None,
           "missing_gold_rows": missing, "llm_calls": llm_calls,
           "algo": {"matrix": algo_matrix, "convergence": algo_conv}, "psyc": {"matrix": psyc}}
    with open(os.path.join(MATRIX, "summary_corrected.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    old = {}
    if os.path.exists(os.path.join(MATRIX, "summary.json")):
        old = json.load(open(os.path.join(MATRIX, "summary.json"), encoding="utf-8")).get("matrix", {})
    print(f"\n=== 重新判分（{mode}）===  llm_calls={llm_calls}  缺金标={missing}  用时={int(time.time()-t0)}s")
    print(f"{'cell':22}{'原correct':>11}{'新correct':>11}{'faith':>8}{'hallu':>8}{'oos弃答':>9}")
    for label in sorted(algo_matrix) + sorted(algo_conv) + sorted(psyc):
        s = {**algo_matrix, **algo_conv, **psyc}[label]
        o = old.get(label, {}).get("correct")
        def p(x): return "—" if x is None else f"{x*100:.0f}%"
        print(f"{label:22}{p(o):>11}{p(s['correct']):>11}{p(s['faithfulness']):>8}{p(s['hallucination']):>8}{p(s['abstention_oos']):>9}")
    print(f"\n[+] -> {MATRIX}/summary_corrected.json（未覆盖 summary.json）")


if __name__ == "__main__":
    main()
