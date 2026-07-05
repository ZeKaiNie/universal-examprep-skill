# -*- coding: utf-8 -*-
"""Aggregate EXPLICIT answer + score rows into a summary.json-compatible matrix summary that
benchmark/report_matrix.py can render. Pure stdlib · deterministic · no network / LLM / API keys / deps.

This is the committed, tested aggregation step the matrix pipeline was missing. It takes explicit
file inputs (never silently reads results/matrix/summary.json) and emits a fresh summary:

    generated answers / judge scores
            ↓  aggregate_matrix.py  (this script)
    summary.json-compatible matrix summary
            ↓  report_matrix.py --summary <summary.json> --out-dir <dir>

Inputs (JSONL, one JSON object per line). `id`→item_id and `cost`→cost_usd are accepted (this repo's
gen.py / judge.py row shapes), and boolean flags accept JSON booleans OR 0/1 (judge.py emits integers):
  --answers : the ITEM UNIVERSE + each answer attempt. Required: course, model, arm, item_id.
              Optional: answerable (bool/0/1 — may instead live on the score row, the gen.py→judge
              path; if BOTH the answer and score rows carry it they must AGREE, a conflict fails loud),
              status ("ok" | "infra_error", default "ok"), cost_usd (number, default 0).
  --scores  : the JUDGMENTS. Required: course, model, arm, item_id. Optional: correct, hallucinated,
              abstained, judge_error (bool/0/1), faithfulness (number in [0,1]), answerable, scored_by.

Honesty rules (mirrors benchmark/rejudge.aggregate() so fixture and real runs agree):
  - infra_error answers are EXCLUDED from correctness denominators (they are not model answers) but
    counted in n_infra_error — never silently dropped.
  - a completed answerable item with NO matching score row is counted as judge_error (NOT correct):
    a missing judgment never inflates correctness.
  - correctness/hallucination are rates over completed answerable items; faithfulness averages over
    items that were actually judged (not judge_error). abstention_oos is over completed OOS items.

Output (--out): models / arms / courses / n_items / total_cost_usd / judge_model, a `matrix` block for
the --primary-course (the model|arm cells report_matrix.py reads), a GENERIC `course_matrix`
(every course's cells), `cost_per_q` (course→arm mean per-item cost), and — when --secondary-course is
given — a renderer-compatible `psyc` block (psyc|model|arm keys) for that course.
"""
import argparse
import json
import os
import sys

REQ_ANSWER = ("course", "model", "arm", "item_id")   # `answerable` may instead live on the score row
REQ_SCORE = ("course", "model", "arm", "item_id")


def _die(msg):
    sys.stderr.write("aggregate_matrix: " + msg + "\n")
    raise SystemExit(2)


def _load_jsonl(path, required, label, alias=None):
    if not os.path.isfile(path):
        _die("找不到 %s 文件: %s" % (label, path))
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError as e:
                _die("%s 第 %d 行不是合法 JSON: %s" % (label, ln, e))
            if not isinstance(d, dict):
                _die("%s 第 %d 行必须是 JSON 对象" % (label, ln))
            if alias:
                alias(d)   # normalize repo-native aliases (id→item_id, cost→cost_usd) before the field check
            missing = [k for k in required if k not in d]
            if missing:
                _die("%s 第 %d 行缺必需字段 %s" % (label, ln, missing))
            rows.append(d)
    return rows


def _key(d):
    return (d["course"], d["model"], d["arm"], str(d["item_id"]))


def _validate_score(s):
    # accept JSON booleans OR 0/1 (this repo's judge.py emits integer flags); reject string-encoded
    # booleans etc. — "false" is truthy and would silently corrupt the rates. rate() treats 0/1 right.
    for b in ("correct", "hallucinated", "abstained", "judge_error"):
        v = s.get(b)
        if v is not None and not (isinstance(v, bool) or (isinstance(v, int) and v in (0, 1))):
            _die("scores %s 的 %s 必须是布尔值或 0/1（或省略/null），不能是 %r" % (_key(s), b, v))
    f = s.get("faithfulness")
    if f is not None:
        if isinstance(f, bool) or not isinstance(f, (int, float)):
            _die("scores %s 的 faithfulness 必须是数值（或省略/null），不能是 %r" % (_key(s), f))
        if not (0 <= f <= 1):
            _die("scores %s 的 faithfulness 必须在 [0,1] 内，当前 %r" % (_key(s), f))


def _as_bool(v):
    """Coerce a flag to bool — accepts JSON booleans and 0/1 (judge.py emits integers); None passes through."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    return None  # caller treats this as 'unusable'


def _alias_answer(d):
    # accept this repo's gen.py row shape: `id` → item_id, `cost` → cost_usd
    if "item_id" not in d and "id" in d:
        d["item_id"] = d["id"]
    if "cost_usd" not in d and "cost" in d:
        d["cost_usd"] = d["cost"]


def _alias_score(d):
    if "item_id" not in d and "id" in d:   # judge.py / rejudge.py score dicts carry `id`
        d["item_id"] = d["id"]


def _cell(items):
    """Compute one matrix cell from merged per-item dicts. Mirrors benchmark/rejudge.aggregate()
    (+ a cost_usd total). `items` carry: answerable, infra_error, judge_error, correct, faithfulness,
    hallucinated, abstained, scored_by, cost_usd."""
    ans = [s for s in items if s["answerable"] and not s["infra_error"]]
    oos = [s for s in items if not s["answerable"] and not s["infra_error"]]
    decided = [s for s in ans if not s["judge_error"] and s.get("faithfulness") is not None]

    def rate(xs, key):
        xs = [x for x in xs if x.get(key) is not None]
        return round(sum(1 for x in xs if x.get(key)) / len(xs), 4) if xs else None

    return {
        "n": len(items), "n_answerable": len(ans), "n_oos": len(oos),
        "correct": rate(ans, "correct"),
        "faithfulness": round(sum(s["faithfulness"] for s in decided) / len(decided), 4) if decided else None,
        "hallucination": rate(ans, "hallucinated"),
        "abstention_oos": rate(oos, "abstained"),
        "n_judge_error": sum(1 for s in ans if s["judge_error"]),
        "n_lexical": sum(1 for s in ans if s.get("scored_by") == "lexical"),
        "n_infra_error": sum(1 for s in items if s["infra_error"]),
        "cost_usd": round(sum((s.get("cost_usd") or 0) for s in items), 6),
    }


def merge_rows(answers, scores):
    """Join answers (item universe) with scores (judgments) by (course, model, arm, item_id).
    Returns a list of merged per-item dicts the cell aggregator consumes."""
    score_by = {}
    for s in scores:
        k = _key(s)
        if k in score_by:
            _die("scores 中出现重复 (course,model,arm,item_id): %s" % (k,))
        _validate_score(s)
        score_by[k] = s
    seen = set()
    merged = []
    for a in answers:
        k = _key(a)
        if k in seen:
            _die("answers 中出现重复 (course,model,arm,item_id): %s" % (k,))
        seen.add(k)
        cost = a.get("cost_usd", 0) or 0
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            _die("answers %s 的 cost_usd 必须是数值，当前 %r" % (k, cost))
        status = a.get("status", "ok")
        if status not in ("ok", "infra_error"):
            _die("answers %s 的 status 必须是 'ok' 或 'infra_error'，当前 %r" % (k, status))
        infra = status == "infra_error"
        s = score_by.get(k)
        # `answerable` may live on the answer row OR (the documented gen.py→judge path) on the score
        # row. A side "provides" it only when the key exists AND is non-null. When BOTH sides provide
        # it we REQUIRE agreement and fail loudly on a conflict — we never silently prefer one side.
        a_has = "answerable" in a and a["answerable"] is not None
        s_has = s is not None and "answerable" in s and s["answerable"] is not None
        if not a_has and not s_has:
            _die("answers/scores %s 都没有 answerable（无法区分可答题/越界探针题）" % (k,))
        a_val = s_val = None
        if a_has:
            a_val = _as_bool(a["answerable"])
            if a_val is None:
                _die("answers %s 的 answerable 必须是布尔或 0/1，当前 %r" % (k, a["answerable"]))
        if s_has:
            s_val = _as_bool(s["answerable"])
            if s_val is None:
                _die("scores %s 的 answerable 必须是布尔或 0/1，当前 %r" % (k, s["answerable"]))
        if a_has and s_has and a_val != s_val:
            _die("answers/scores %s 的 answerable 冲突：answer 行=%r、score 行=%r"
                 "（两侧必须一致，或只在其中一侧给出）" % (k, a["answerable"], s["answerable"]))
        answerable = a_val if a_has else s_val
        if s is not None:
            judge_error = bool(s.get("judge_error"))
            correct = s.get("correct")
        else:
            judge_error = answerable and not infra
            correct = None
        # a completed ANSWERABLE item with no usable verdict — whether the score row is MISSING or
        # PRESENT-but-undecided (judge_error, or no `correct` at all) — is counted NOT-correct: a
        # trustworthy lower bound. Neither a missing nor an undecided judgment may inflate correctness.
        if answerable and not infra and (judge_error or correct is None):
            judge_error, correct = True, False
        # symmetric lower bound for OOS: a completed out-of-scope item with no abstention verdict
        # (missing score, or a score without `abstained`) counts NOT-abstained — it never inflates
        # abstention_oos (i.e. we don't credit an unverified abstention).
        abstained = (s or {}).get("abstained")
        if (not answerable) and not infra and abstained is None:
            abstained = False
        merged.append({
            "course": a["course"], "model": a["model"], "arm": a["arm"], "item_id": str(a["item_id"]),
            "answerable": answerable, "infra_error": infra, "judge_error": judge_error,
            "correct": correct,
            "faithfulness": (s or {}).get("faithfulness"),
            "hallucinated": (s or {}).get("hallucinated"),
            "abstained": abstained,
            "scored_by": (s or {}).get("scored_by"),
            "cost_usd": cost,
        })
    # scores with no matching answer would be silently ignored — fail loud instead
    orphan = [k for k in score_by if k not in seen]
    if orphan:
        _die("scores 含 %d 条没有对应 answer 的行（每个判分必须对应一个作答），如 %s" % (len(orphan), orphan[0]))
    return merged


def build_summary(merged, primary_course, secondary_course=None, judge_model="unknown"):
    courses = sorted({m["course"] for m in merged})
    models = sorted({m["model"] for m in merged})
    arms = sorted({m["arm"] for m in merged})
    if primary_course not in courses:
        _die("--primary-course %r 不在数据的 course 里（有: %s）" % (primary_course, courses))
    if secondary_course is not None and secondary_course not in courses:
        _die("--secondary-course %r 不在数据的 course 里（有: %s）" % (secondary_course, courses))

    # generic: every course → {model|arm: cell}
    course_matrix = {}
    cells_by = {}
    for m in merged:
        cells_by.setdefault((m["course"], m["model"], m["arm"]), []).append(m)
    for (course, model, arm), items in cells_by.items():
        course_matrix.setdefault(course, {})["%s|%s" % (model, arm)] = _cell(items)

    # cost_per_q: course → arm → mean per-item cost (across models)
    # infra_error 行**不进均值分母**（与正确率同一条诚实规则：它们不是模型答案）——否则一堆 0 成本的
    # 失败行会把「每题成本」摊薄成漂亮数字，报告照登就是虚标。真实总花费仍在 total_cost_usd 里。
    cost_per_q = {}
    cost_acc = {}
    for m in merged:
        ca = cost_acc.setdefault((m["course"], m["arm"]), [0.0, 0])   # 臂键保留（全失败 → None 而非消失）
        if m["infra_error"]:
            continue
        ca[0] += (m.get("cost_usd") or 0)
        ca[1] += 1
    for (course, arm), (tot, n) in cost_acc.items():
        cost_per_q.setdefault(course, {})[arm] = round(tot / n, 4) if n else None

    # 齐平性：主课程各 (model,arm) 格的答题集应一致（「同题跨臂比较」的前提）。不齐平不硬拦
    # （续跑中途单独聚合是合法状态），但要**响亮标出**——headline n_items 是并集，各格分母是自己的子集。
    prim_sets = {}
    for m in merged:
        if m["course"] == primary_course:
            prim_sets.setdefault((m["model"], m["arm"]), set()).add(m["item_id"])
    ragged = len({frozenset(v) for v in prim_sets.values()}) > 1
    if ragged:
        sys.stderr.write("aggregate_matrix: ⚠️ 主课程各格答题集不齐平（%s）——跨臂/跨模型对比要小心，"
                         "n_items 是并集而非公共集\n"
                         % ", ".join("%s|%s=%d" % (k[0], k[1], len(v)) for k, v in sorted(prim_sets.items())))

    n_items = len({m["item_id"] for m in merged if m["course"] == primary_course})
    summary = {
        "n_items": n_items,
        "models": models,
        "arms": arms,
        "courses": courses,
        "judge_model": judge_model,
        "total_cost_usd": round(sum((m.get("cost_usd") or 0) for m in merged), 4),
        "matrix": course_matrix[primary_course],          # report_matrix.py reads this
        "course_matrix": course_matrix,                    # generic, every course
        "cost_per_q": cost_per_q,
        "ragged_matrix": ragged,                           # 主课程各格答题集不齐平（对比要小心）
        "aggregated_by": "aggregate_matrix.py",            # provenance: NOT the published rejudge output
    }
    if secondary_course is not None:
        # renderer-compatible psyc block (psyc|model|arm keys) for an explicitly chosen second course
        summary["psyc"] = {"psyc|%s" % mk: cell for mk, cell in course_matrix[secondary_course].items()}
    return summary


def main(argv=None):
    for s in ("stdout", "stderr"):
        try:
            getattr(sys, s).reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="把显式的 answer/score 行聚合成 report_matrix.py 可渲染的 summary.json（纯标准库，无网络/LLM）。")
    ap.add_argument("--answers", required=True, help="作答行 JSONL（题目全集 + 每次作答）")
    ap.add_argument("--scores", required=True, help="判分行 JSONL（每题的判分结果）")
    ap.add_argument("--out", required=True, help="输出 summary.json 路径")
    ap.add_argument("--primary-course", default=None,
                    help="进入 matrix 的主课程（默认取数据里 item 最多的课程）")
    ap.add_argument("--secondary-course", default=None,
                    help="可选：再产出一个 renderer 的 psyc 块（psyc|model|arm）用于该课程")
    ap.add_argument("--judge-model", default="unknown", help="判分模型标注（仅写入 summary，不调用）")
    args = ap.parse_args(argv)

    answers = _load_jsonl(args.answers, REQ_ANSWER, "answers", _alias_answer)
    scores = _load_jsonl(args.scores, REQ_SCORE, "scores", _alias_score)
    if not answers:
        _die("answers 为空——无可聚合的作答")
    merged = merge_rows(answers, scores)

    primary = args.primary_course
    if primary is None:   # default: the course with the most distinct items
        per_course = {}
        for m in merged:
            per_course.setdefault(m["course"], set()).add(m["item_id"])
        primary = max(sorted(per_course), key=lambda c: len(per_course[c]))
    summary = build_summary(merged, primary, args.secondary_course, args.judge_model)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[+] summary: %s（%d 课程 / %d 模型 / %d 臂；主课程 %s，n_items=%d）"
          % (args.out, len(summary["courses"]), len(summary["models"]), len(summary["arms"]),
             primary, summary["n_items"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
