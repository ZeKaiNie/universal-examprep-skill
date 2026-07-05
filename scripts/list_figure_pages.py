#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""List VISUAL pages in the course materials from figure_page_index.json (P0-V2 official tool).

The second metric of the dual bookkeeping: not quiz-bank questions, but which lecture/homework PAGES
carry figures/tables/charts/… — so "which chapter has the most figures" can be answered on BOTH
denominators instead of silently using only quiz items.

    python scripts/list_figure_pages.py --workspace <ws> [--file substr] [--kind circuit] [--json]

Exit codes: 0 ok · 2 index missing/bad (run build_visual_index.py first).
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
    sys.stderr.write("list_figure_pages: " + msg + "\n")
    raise SystemExit(2)


def run(argv=None):
    ap = argparse.ArgumentParser(description="List visual pages in lectures/materials (grouped by file; filterable by type).")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--index", default=None, help="figure_page_index.json path (default under references/)")
    ap.add_argument("--file", default=None, help="only files whose path contains this substring")
    ap.add_argument("--kind", default=None, help="only pages with this visual type (figure/table/circuit/...)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = args.index or os.path.join(args.workspace, "references", "figure_page_index.json")
    if not os.path.isfile(path):
        _die("找不到 figure_page_index.json（先跑 scripts/build_visual_index.py --materials …）: %s" % path)
    try:
        idx = json.load(open(path, encoding="utf-8"))
    except ValueError as e:
        _die("索引不是合法 JSON: %s" % e)

    out = {}
    for rel, info in sorted((idx.get("files") or {}).items()):
        if args.file and args.file.lower() not in rel.lower():
            continue
        pages = [p for p in info.get("visual_pages", [])
                 if not args.kind or args.kind in (p.get("visual_kinds") or [])]
        if pages:
            out[rel] = {"pages_total": info.get("pages"), "visual": pages}

    scan_warnings = [str(w) for w in (idx.get("warnings") or [])
                     if str(w).startswith(("pdf_text_failed", "media_failed", "no_media_backend"))]
    if args.json:
        print(json.dumps({"files": out, "media_signals": idx.get("media_signals"),
                          "warnings": scan_warnings}, ensure_ascii=False, indent=2))
        return 0
    if not idx.get("media_signals", True):
        print("[!] 本索引缺结构信号（无 PyMuPDF）——仅词面判定，可能有漏")
    for w in scan_warnings:                            # a skipped/degraded PDF = missing pages in this metric
        print("[!] " + w)
    total = 0
    for rel, info in out.items():
        total += len(info["visual"])
        print("%s（共 %s 页，视觉页 %d）" % (rel, info["pages_total"], len(info["visual"])))
        for p in info["visual"]:
            print("  p.%-4d %s" % (p["page"], ",".join(p.get("visual_kinds") or []) or "structural-only"))
    print("合计视觉页: %d" % total)
    return 0


if __name__ == "__main__":
    sys.exit(run())
