#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Optional-dependency preflight (v4) — the ONE machine-readable manifest of everything the
skill may need beyond the stdlib, probed BEFORE it is needed, never discovered by a runtime crash.

Why: the CORE pipeline is pure stdlib by design, but three capabilities have optional backends —
PDF text extraction (any of pypdf / pypdfium2 / PyMuPDF), PDF page rendering for the visual index
(PyMuPDF only), and printable-PDF output (a LOCAL Edge/Chrome, not a pip package). Students used
to hit these as mid-ingest errors; agents are instructed (exam-ingest Workflow step 0) to run THIS
tool at setup, show the student what is missing and why, and offer the exact install commands —
installing is the agent's action WITH the student's one-line consent, never a silent side effect.

    python scripts/check_deps.py                          # probe everything, human report
    python scripts/check_deps.py --materials <dir>        # scan materials → mark what is NEEDED
    python scripts/check_deps.py --json                   # machine manifest for agents
Exit: 0 = nothing NEEDED is missing · 5 = a NEEDED dependency is missing · 2 = usage
"""
import argparse
import importlib.util
import json
import os
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---- the manifest（唯一事实源；技能文本只指向本工具，不复制清单内容——防两处漂移）----
# candidates: (import_name, pip_name)。group 内任一可用即满足。
GROUPS = (
    {"id": "pdf_text",
     "candidates": (("pypdf", "pypdf"), ("pypdfium2", "pypdfium2"), ("fitz", "pymupdf")),
     "needed_when": "materials_have_pdf",
     "purpose_zh": "读取 PDF 课件/试卷文本（ingest 建库）",
     "purpose_en": "extract text from PDF materials (ingest)"},
    {"id": "pdf_render",
     "candidates": (("fitz", "pymupdf"),),
     "needed_when": "materials_have_pdf",
     "purpose_zh": "渲染 PDF 页面图（视觉索引/题面图，缺失时仅文字信号、召回打折）",
     "purpose_en": "render PDF pages for the visual index (text-only signals without it)"},
    {"id": "browser",
     "candidates": (),                      # 系统依赖，非 pip
     "needed_when": "cheatsheet_pdf",
     "purpose_zh": "考前小抄的打印级 PDF 输出（缺失时降级为 HTML + 手动打印，功能不缺失）",
     "purpose_en": "print-grade cheatsheet PDF (degrades to HTML + manual print)"},
)


def _probe_import(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _probe_browser():
    try:
        import cheatsheet_render
        return cheatsheet_render.find_browser() is not None
    except Exception:
        return False


def _materials_have_pdf(materials):
    if not materials or not os.path.isdir(materials):
        return None                          # 未给材料目录 → 不可判
    for dirpath, _dirs, files in os.walk(materials):
        if any(f.lower().endswith(".pdf") for f in files):
            return True
    return False


def build_report(materials=None):
    has_pdf = _materials_have_pdf(materials)
    rows = []
    for g in GROUPS:
        if g["id"] == "browser":
            ok = _probe_browser()
            available = ["edge/chrome"] if ok else []
            install = "安装 Microsoft Edge 或 Google Chrome（或忽略——自动降级 HTML 手动打印）"
        else:
            available = [pip for imp, pip in g["candidates"] if _probe_import(imp)]
            ok = bool(available)
            install = "pip install " + g["candidates"][-1][1]   # 推荐末位（pymupdf 功能最全）
        if g["needed_when"] == "materials_have_pdf":
            needed = has_pdf if has_pdf is not None else "unknown"
        else:
            needed = "optional"              # cheatsheet PDF：有降级路径，永不算硬缺
        rows.append({"id": g["id"], "ok": ok, "available": available,
                     "needed": needed, "install": install,
                     "purpose_zh": g["purpose_zh"], "purpose_en": g["purpose_en"]})
    missing_needed = [r for r in rows if r["needed"] is True and not r["ok"]]
    return {"groups": rows, "materials_scanned": materials,
            "materials_have_pdf": has_pdf,
            "missing_needed": [r["id"] for r in missing_needed]}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Probe optional dependencies BEFORE they are needed (stdlib only; "
                    "agents run this at setup and offer the printed install commands).")
    ap.add_argument("--materials", default=None,
                    help="materials dir to scan; PDFs there make the PDF backends NEEDED")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    rep = build_report(args.materials)
    if args.as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        mark = {True: "✓", False: "✗"}
        for r in rep["groups"]:
            need = {True: "【需要】", False: "（材料无 PDF，暂不需要）",
                    "unknown": "（未给 --materials，无法判定是否需要）",
                    "optional": "（可选：缺失自动降级）"}[r["needed"]]
            print("%s %s %s—— %s" % (mark[r["ok"]], r["id"].ljust(10), need, r["purpose_zh"]))
            if not r["ok"]:
                print("    ↳ 安装：%s" % r["install"])
        if rep["missing_needed"]:
            print("\n[!] 缺少当前材料需要的依赖：%s——先装再跑 ingest，别等它中途报错"
                  % ", ".join(rep["missing_needed"]))
    return 5 if rep["missing_needed"] else 0


if __name__ == "__main__":
    sys.exit(main())
