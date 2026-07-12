# -*- coding: utf-8 -*-
"""Retrieval-recall evaluator (v4-P3 缺口8) — pure stdlib, zero LLM.

Joins trace-carrying answers (gen.py with EXAMPREP_TRACE=1 → rows with `files_opened`) against
gold items (each carries `source_file`, e.g. materials/psych_yale_psyc110/lecture02.md) and scores
CHAPTER ROUTING: did the agent open the wiki chapter that contains the gold span?

Mapping rule (default): the gold's trailing number → chNN. `lecture02.md` / `lec2.pdf` /
`ch02.md` all map to chapter 2; a files_opened entry hits when it references chNN (ch02.md,
wiki/ch02/, ch02/s03_x.md …). Items without a mappable number are reported as unmapped, never
silently dropped.

    python benchmark/retrieval_eval.py --answers results/matrix/gen_answers.jsonl \
        --items items/items_psyc_full.jsonl [--arm skill] [--json]

Output: per model|arm → n_traced / n_hit / recall (+ unmapped & untraced counts, loudly).
Exit: 0 ok · 2 bad input.
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

_NUM_RE = re.compile(r"(?:lecture|lec|ch|chapter|ps)[\s_\-]*0*(\d+)", re.I)


def _die(msg, code=2):
    sys.stderr.write("retrieval_eval: " + msg + "\n")
    raise SystemExit(code)


def chapter_of(path):
    """'materials/.../lecture02.md' → 2; None when no chapter number is recognizable."""
    if not path:
        return None
    m = _NUM_RE.search(os.path.basename(str(path))) or _NUM_RE.search(str(path))
    return int(m.group(1)) if m else None


def opened_chapters(files_opened):
    out = set()
    for f in files_opened or []:
        n = chapter_of(f)
        if n is not None:
            out.add(n)
    return out


def _load_jsonl(path, label):
    if not os.path.isfile(path):
        _die("找不到 %s: %s" % (label, path))
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                _die("%s 第 %d 行不是合法 JSON: %s" % (label, ln, e))
    return rows


def evaluate(answers, items, arm=None):
    gold = {}
    unmapped = []
    for it in items:
        iid = it.get("id")
        ch = chapter_of(it.get("source_file"))
        if it.get("answerable") is False:
            continue                       # 越界探针无「正确章」，不进 recall 分母
        if ch is None:
            unmapped.append(iid)
        else:
            gold[iid] = ch
    cells = {}
    untraced = 0
    for a in answers:
        if arm and a.get("arm") != arm:
            continue
        iid = a.get("id") or a.get("item_id")
        if iid not in gold:
            continue
        key = "%s|%s" % (a.get("model"), a.get("arm"))
        cell = cells.setdefault(key, {"n_traced": 0, "n_hit": 0, "n_untraced": 0})
        files = a.get("files_opened")
        if not files:
            cell["n_untraced"] += 1
            untraced += 1
            continue
        cell["n_traced"] += 1
        if gold[iid] in opened_chapters(files):
            cell["n_hit"] += 1
    for cell in cells.values():
        cell["recall"] = round(cell["n_hit"] / cell["n_traced"], 4) if cell["n_traced"] else None
    return {"cells": cells, "n_gold": len(gold), "unmapped_items": unmapped,
            "n_untraced_answers": untraced}


def main(argv=None):
    ap = argparse.ArgumentParser(description="chapter-routing recall from tool traces (stdlib)")
    ap.add_argument("--answers", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--arm", default=None, help="score one arm only (e.g. skill)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    res = evaluate(_load_jsonl(args.answers, "answers"), _load_jsonl(args.items, "items"), args.arm)
    if args.as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[retrieval_eval] 金标可映射题 %d；无轨迹作答 %d（跑 gen 时设 EXAMPREP_TRACE=1 才有轨迹）"
              % (res["n_gold"], res["n_untraced_answers"]))
        if res["unmapped_items"]:
            print("[!] %d 题的 source_file 提不出章号（未静默丢弃，列出）: %s"
                  % (len(res["unmapped_items"]), ", ".join(map(str, res["unmapped_items"][:10]))))
        for key in sorted(res["cells"]):
            c = res["cells"][key]
            rc = ("%.1f%%" % (100 * c["recall"])) if c["recall"] is not None else "--"
            print("  %-24s traced=%-4d hit=%-4d recall=%s  (untraced=%d)"
                  % (key, c["n_traced"], c["n_hit"], rc, c["n_untraced"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
