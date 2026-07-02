#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""List image-dependent questions per chapter (P0-V2 official tool — no ad-hoc shell parsing).

Answers the cross-check that keyword-only counting failed: for EVERY chapter, how many questions
total × how many require assets × how many are maybe (conservative) × how many are SUSPECTED missed
(from image_question_index.json, if built). quiz_bank.json is always the source of truth for flags;
the index only contributes suspects.

    python scripts/list_image_questions.py --workspace <ws> [--chapter N] [--list] [--json]

Exit codes: 0 ok · 2 bad input.
"""
import argparse
import json
import os
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass


def _die(msg):
    sys.stderr.write("list_image_questions: " + msg + "\n")
    raise SystemExit(2)


def run(argv=None):
    ap = argparse.ArgumentParser(description="按章列出图依赖题（总数×requires×maybe×疑漏）。")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--chapter", default=None, help="只看这一章")
    ap.add_argument("--list", action="store_true", help="逐题列出（id + 标记 + 资产）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    args = ap.parse_args(argv)

    bank_path = os.path.join(args.workspace, "references", "quiz_bank.json")
    if not os.path.isfile(bank_path):
        _die("找不到 quiz_bank.json: %s" % bank_path)
    try:
        bank = json.load(open(bank_path, encoding="utf-8"))
    except ValueError as e:
        _die("quiz_bank.json 不是合法 JSON: %s" % e)
    if not isinstance(bank, list):
        _die("quiz_bank.json 必须是数组")

    suspects_by_id = {}
    recall_net, recall_note = False, None
    idx_path = os.path.join(args.workspace, "references", "image_question_index.json")
    if os.path.isfile(idx_path):
        try:
            idx = json.load(open(idx_path, encoding="utf-8"))
            suspects_by_id = {s["id"]: s for s in idx.get("suspects", []) if isinstance(s, dict) and "id" in s}
            idx_warnings = [str(w) for w in (idx.get("warnings") or [])]
            # an index built WITHOUT --materials never cross-checked anything: suspects=0 is NOT evidence;
            # a PDF that failed to scan AT ALL leaves that file's suspects invisible — also untrustworthy
            if any(w.startswith("no_materials") for w in idx_warnings):
                recall_net, recall_note = False, "索引构建时未给 --materials，疑漏交叉核对未运行"
            elif any(w.startswith("no_pdfs_found") for w in idx_warnings):
                recall_net, recall_note = False, "材料目录里没扫到任何 PDF（路径错误/目录为空）——疑漏交叉核对未运行"
            elif any(w.startswith(("pdf_text_failed", "source_pdf_not_indexed")) for w in idx_warnings):
                bad = [w.split(":", 1)[1].strip() for w in idx_warnings
                       if w.startswith(("pdf_text_failed", "source_pdf_not_indexed"))]
                recall_net, recall_note = False, "有出处 PDF 未被扫描/未被索引（%s）——这些文件的疑漏不可见" % "; ".join(bad)
            elif any(w.startswith(("no_media_backend", "media_failed")) for w in idx_warnings):
                recall_net, recall_note = True, "结构信号缺失/部分失败（PyMuPDF）——疑漏口径仅靠文字信号，可能有漏"
            else:
                recall_net = True
        except ValueError:
            sys.stderr.write("[!] image_question_index.json 损坏，忽略疑漏口径\n")

    rows, per = [], {}
    for q in bank:
        if not isinstance(q, dict) or q.get("id") is None:
            continue
        chap = q.get("chapter") if q.get("chapter") is not None else q.get("phase")   # phase-tagged banks
        ch = str(chap) if chap is not None else "?"
        if args.chapter is not None and ch != str(args.chapter):
            continue
        requires = q.get("requires_assets") is True
        maybe = q.get("maybe_requires_assets") is True
        suspect = str(q["id"]) in suspects_by_id
        c = per.setdefault(ch, {"questions": 0, "requires": 0, "maybe": 0, "suspects": 0})
        c["questions"] += 1
        c["requires"] += int(requires)
        c["maybe"] += int(maybe)
        c["suspects"] += int(suspect)
        if args.list and (requires or maybe or suspect):
            side = {"question_context", "figure", "diagram", "table"}
            assets = [a.get("path") for a in (q.get("assets") or [])
                      if isinstance(a, dict) and a.get("role") in side and a.get("path")]
            rows.append({"id": str(q["id"]), "chapter": ch,
                         "flag": ("requires" if requires else "maybe" if maybe else "suspect"),
                         "prompt_assets": assets})

    if args.json:
        print(json.dumps({"per_chapter": per, "items": rows,
                          "index_present": os.path.isfile(idx_path),
                          "recall_net": recall_net, "recall_note": recall_note},
                         ensure_ascii=False, indent=2))
        return 0
    print("章节 | 题目总数 | requires | maybe | 疑漏(未标)")
    for ch in sorted(per, key=lambda c: (c == "?", len(c), c)):
        c = per[ch]
        print("%4s | %8d | %8d | %5d | %d" % (ch, c["questions"], c["requires"], c["maybe"], c["suspects"]))
    tot = {k: sum(c[k] for c in per.values()) for k in ("questions", "requires", "maybe", "suspects")}
    print("合计 | %8d | %8d | %5d | %d" % (tot["questions"], tot["requires"], tot["maybe"], tot["suspects"]))
    if not os.path.isfile(idx_path):
        print("[!] 尚未构建 image_question_index.json（疑漏口径=0 不可信）——先跑 build_visual_index.py")
    elif not recall_net:
        print("[!] 疑漏口径=0 不可信：%s——用 --materials 重跑 build_visual_index.py" % (recall_note or "召回网未运行"))
    elif recall_note:
        print("[!] " + recall_note)
    for r in rows:
        print("  [%s] %s (ch%s) assets=%s" % (r["flag"], r["id"], r["chapter"], ",".join(r["prompt_assets"]) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(run())
