#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build the workspace's UNIVERSAL visual indices (P0-V2) — recall-first, subject-agnostic.

Primary goal: make sure NO image-dependent question slips through unlabeled. Produces two indices
(JSON, written into <workspace>/references/) plus a "suspected missed visual question" report:

  * image_question_index.json — every quiz_bank item's visual profile: requires/maybe flags, prompt/answer
    asset paths, source file+pages, whether an official answer exists, whether the ANSWER pages are visual.
    Per-chapter rollup (total × requires × maybe × suspects) so "which chapter has the most figures" style
    cross-checks are one lookup.
  * figure_page_index.json — every VISUAL page in the course materials (lecture/homework PDFs): which file,
    which page, what kinds of visuals (figure/table/diagram/chart/graph/plot/screenshot/circuit/tree/map/
    geometry/flowchart …), detected by LAYERED signals — strongest first:
      1. structural: the page physically contains embedded images / many vector drawings (via optional
         PyMuPDF; no LLM) — catches pages with NO caption keywords at all;
      2. layout: figure/table numbering ("Figure 3", "表 2"), axis/legend vocabulary;
      3. keyword classes (weakest, multi-domain zh+en; NEVER tied to one subject like PDF/CDF).
  * suspects — quiz_bank items that carry source_file/source_pages landing on a visual page but are NOT
    labeled requires/maybe_requires_assets and have no prompt-side asset. Default: report only.
    --apply renders each suspect's page to references/assets/ (page_image, question_context role), attaches
    it and sets maybe_requires_assets=true — so the fail-closed validator gate stays satisfiable.

Pure stdlib core; pypdf (text) / PyMuPDF (structural signals + render) / pypdfium2+Pillow (render fallback)
are OPTIONAL lazy imports. No network, no LLM, no API keys. Honest limits: the detector is a deterministic
heuristic (recall-first: prefer over-flagging `maybe` to missing a figure question); semantic/AI-vision
checking is a future opt-in, NOT implemented here.

    python scripts/build_visual_index.py --workspace <ws> --materials <course-folder>
    python scripts/build_visual_index.py --workspace <ws> --materials <dir> --apply   # render+attach suspects

Exit codes: 0 ok · 2 bad input · 3 materials contain PDFs but the needed backend is missing.
"""
import argparse
import hashlib
import json
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# workspace-leftover / tooling dirs to skip while scanning materials (same policy as the P0B builder)
try:
    from build_raw_input_from_workspace import ALWAYS_PRUNE, _is_leftover_workspace, _is_workspace_root
except Exception:                                      # pragma: no cover - builder should always be present
    ALWAYS_PRUNE = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}

    def _is_leftover_workspace(path, name):
        return False

    def _is_workspace_root(path):
        return False

# ---------------- universal visual-kind vocabulary (multi-domain, zh+en; NOT subject-bound) ----------------
VISUAL_KIND_WORDS = {
    "figure": ("figure", "fig.", "illustration", "插图", "如图", "下图", "上图"),
    "table": ("table", "tabular", "表格", "数据表"),
    "diagram": ("diagram", "schematic", "示意图", "结构图", "装置图", "受力图", "解剖图", "机械结构", "状态机",
                "state machine"),
    "chart": ("chart", "bar chart", "pie chart", "柱状图", "饼图", "统计图"),
    "graph": ("graph", "curve", "waveform", "曲线", "波形", "信号图", "函数图"),
    "plot": ("plot", "scatter", "散点", "坐标图", "sample path"),
    "screenshot": ("screenshot", "截图", "运行结果", "界面图", "console output"),
    "circuit": ("circuit", "电路", "原理图"),
    "tree": ("tree", "树状", "二叉树", "决策树"),
    "map": ("map", "地图"),
    "geometry": ("geometry", "几何图", "三角形", "venn", "文氏图", "韦恩图"),
    "flowchart": ("flowchart", "flow chart", "流程图"),
}
_FIGREF_RE = re.compile(r"(?<![A-Za-z0-9])(?:figure|fig\.?|table|图|表)\s*\d", re.I)   # not 'comfortable 1'
_AXIS_WORDS = ("x-axis", "y-axis", "x轴", "y轴", "横轴", "纵轴", "坐标轴", "图例", "legend", "axis")
# structural thresholds — recall-first: any embedded image counts; >=6 vector drawings (below that is
# usually just rules/underlines in text layout)
_MIN_DRAWINGS = 6


def _compile_words(words):
    """ASCII words match on TOKEN BOUNDARIES (so 'paragraph' doesn't hit 'graph', 'comfortable' doesn't
    hit 'table'); CJK words keep substring matching (Chinese has no word delimiters)."""
    out = []
    for w in words:
        if all(ord(c) < 128 for c in w):
            out.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I))
        else:
            out.append(w)
    return out


VISUAL_KIND_PATTERNS = {k: _compile_words(ws) for k, ws in VISUAL_KIND_WORDS.items()}
_AXIS_PATTERNS = _compile_words(_AXIS_WORDS)


def _any_hit(text, patterns):
    return any(p.search(text) if hasattr(p, "search") else (p in text) for p in patterns)


def classify_page(text, images=0, drawings=0):
    """Deterministic layered judgment for ONE page. Returns {has_visual, visual_kinds, signals}.
    Recall-first: structural evidence alone (a page with an embedded image but NO caption keywords)
    is enough — the exact gap that made keyword-only detection miss figure pages."""
    t = (text or "").lower()
    kinds = sorted(k for k, pats in VISUAL_KIND_PATTERNS.items() if _any_hit(t, pats))
    structural = (images or 0) > 0 or (drawings or 0) >= _MIN_DRAWINGS
    figref = bool(_FIGREF_RE.search(t))
    axis = _any_hit(t, _AXIS_PATTERNS)
    return {
        "has_visual": bool(structural or figref or axis or kinds),
        "visual_kinds": kinds,
        "signals": {"images": int(images or 0), "drawings": int(drawings or 0),
                    "structural": structural, "figref": figref, "axis": axis},
    }


# ---------------- optional backends (lazy; injectable for tests) ----------------

class RealBackend(object):
    """text via pypdf · structural media counts + render via PyMuPDF · render fallback pypdfium2+Pillow."""

    def __init__(self):
        self._pypdf = self._fitz = self._pdfium = None
        try:
            import pypdf
            self._pypdf = pypdf
        except Exception:
            pass
        try:
            import fitz
            self._fitz = fitz
        except Exception:
            pass
        try:                                           # ALWAYS load the render fallback when available —
            import pypdfium2                           # fitz may fail on a particular PDF/page and the
            from PIL import Image  # noqa: F401 — pypdfium2 render needs Pillow to save PNG
            self._pdfium = pypdfium2
        except Exception:
            pass
        self.name = "+".join(n for n, m in (("pypdf", self._pypdf), ("pymupdf", self._fitz),
                                            ("pypdfium2", self._pdfium)) if m) or "none"

    def can_text(self):
        return self._pypdf is not None or self._fitz is not None

    def can_media(self):
        return self._fitz is not None

    def can_render(self):
        return self._fitz is not None or self._pdfium is not None

    def pages_text(self, pdf_path):
        if self._pypdf is not None:
            try:
                reader = self._pypdf.PdfReader(pdf_path)
                return [(p.extract_text() or "") for p in reader.pages]
            except Exception:
                if self._fitz is None:                 # no second text backend → let the caller skip this file
                    raise
        doc = self._fitz.open(pdf_path)                # fall back: some PDFs parse under fitz but not pypdf
        try:
            return [doc[i].get_text() or "" for i in range(doc.page_count)]
        finally:
            doc.close()

    def pages_media(self, pdf_path):
        """[(image_count, drawing_count)] per page, or None when PyMuPDF is unavailable."""
        if self._fitz is None:
            return None
        doc = self._fitz.open(pdf_path)
        try:
            out = []
            for i in range(doc.page_count):
                page = doc[i]
                try:
                    imgs = len(page.get_images(full=True))
                except Exception:
                    imgs = 0
                try:
                    draws = len(page.get_drawings())
                except Exception:
                    draws = 0
                out.append((imgs, draws))
            return out
        finally:
            doc.close()

    def render_page_png(self, pdf_path, page_index):
        if self._fitz is not None:
            try:
                doc = self._fitz.open(pdf_path)
                try:
                    return doc[page_index].get_pixmap(dpi=150).tobytes("png")
                finally:
                    doc.close()
            except Exception:
                pass                                   # fall through: pdfium may still render this page
        if self._pdfium is not None:
            try:
                import io
                pdf = self._pdfium.PdfDocument(pdf_path)
                img = pdf[page_index].render(scale=2.0).to_pil()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                return None
        return None


def _die(msg, code=2):
    sys.stderr.write("build_visual_index: " + msg + "\n")
    raise SystemExit(code)


def _read_json(path, label):
    if not os.path.isfile(path):
        _die("找不到%s: %s" % (label, path))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        _die("%s 不是合法 JSON: %s" % (label, e))


def _rel_posix(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def scan_materials(materials, backend, warnings):
    """Walk the course folder for PDFs (skipping tooling/leftover-workspace dirs) and classify every page.
    Returns {rel_pdf_path: {"pages": n, "visual": {page_no(1-based): classify_page(...)}}}"""
    pdfs = []
    for base, dirs, files in os.walk(materials):
        pruned = []
        for d in list(dirs):
            full = os.path.join(base, d)
            if d in ALWAYS_PRUNE or _is_leftover_workspace(full, d) or _is_workspace_root(full):
                dirs.remove(d)
                pruned.append(d)
        for fn in sorted(files):
            if fn.lower().endswith(".pdf"):
                pdfs.append(os.path.join(base, fn))
    if not pdfs:
        # a wrong/empty --materials must not read as "all clear": nothing was cross-checked at all
        warnings.append("no_pdfs_found: 材料目录里没有扫到任何 PDF（路径给错或目录为空？）——疑漏交叉核对未运行")
    if pdfs and not backend.can_text():
        _die("材料里有 PDF 但缺文本后端——pip install pypdf（或 pymupdf）后重跑", 3)
    if pdfs and not backend.can_media():
        warnings.append("no_media_backend: 缺 PyMuPDF（pip install pymupdf），无法枚举页内图片/矢量对象——"
                        "结构信号缺失，仅靠文字信号，召回会打折")
    out = {}
    for pdf in sorted(pdfs):
        rel = _rel_posix(materials, pdf)
        try:
            texts = backend.pages_text(pdf)
        except Exception as e:
            warnings.append("pdf_text_failed: %s (%s)" % (rel, e))
            continue
        media = None
        if backend.can_media():
            try:
                media = backend.pages_media(pdf)
            except Exception as e:                     # one PDF's media failure degrades THAT file only
                warnings.append("media_failed: %s (%s)——该文件仅文字信号，召回打折" % (rel, e))
        visual = {}
        for i, text in enumerate(texts):
            imgs, draws = (media[i] if media and i < len(media) else (0, 0))
            cls = classify_page(text, images=imgs, drawings=draws)
            if cls["has_visual"]:
                visual[i + 1] = cls                     # 1-based page numbers, matching source_pages
        out[rel] = {"pages": len(texts), "visual": visual}
    return out


def _prompt_side_assets(q):
    side = {"question_context", "figure", "diagram", "table"}
    return [a for a in (q.get("assets") or []) if isinstance(a, dict) and a.get("role") in side]


def _usable_prompt_asset(ws, a):
    """Same rules as validate_workspace: safe path AND the file actually exists/readable. A declared but
    stale/missing prompt asset must NOT suppress the suspect (it can't be displayed)."""
    try:
        import validate_workspace as V
        full, unsafe = V._asset_safety(ws, a.get("path"))
    except Exception:                                  # pragma: no cover — validator should be importable
        return bool(a.get("path"))
    return (not unsafe) and full and os.path.isfile(full) and os.access(full, os.R_OK)


def _file_indexed(fig_files, source_file):
    """Was this source_file actually scanned into the figure index? A QUALIFIED path (has a directory
    part) must match exactly — a same-named PDF elsewhere is NOT the declared source; only a bare
    basename may match by name."""
    if not source_file:
        return False
    sf = str(source_file).replace("\\", "/")
    if sf in fig_files:
        return True
    if "/" in sf:
        return False
    return any(os.path.basename(r) == sf for r in fig_files)


def _visual_hits(fig_files, source_file, pages):
    """Which of `pages` are visual pages of `source_file`. EXACT relative-path match wins; a QUALIFIED
    path that doesn't match exactly gets NO fallback (an unrelated same-named PDF must not stand in for
    the declared source). Only a bare basename falls back — then take the UNION across duplicates
    (recall-first, a hit in any candidate must not be hidden)."""
    if not source_file or not pages:
        return []
    sf = str(source_file).replace("\\", "/")
    if sf in fig_files:
        vis = fig_files[sf]["visual"]
        return sorted(p for p in pages if isinstance(p, int) and p in vis)
    if "/" in sf:
        return []
    hits = set()
    for rel, info in fig_files.items():
        if os.path.basename(rel) == sf:
            hits.update(p for p in pages if isinstance(p, int) and p in info["visual"])
    return sorted(hits)


def build_question_index(ws, bank, fig_files, warnings=None):
    """Per-question visual profile + per-chapter rollup + suspects (recall net)."""
    questions, suspects = [], []
    per_chapter = {}
    warnings = warnings if warnings is not None else []
    unindexed = set()
    for q in bank:
        if not isinstance(q, dict) or q.get("id") is None:
            continue
        qid = str(q["id"])
        requires = q.get("requires_assets") is True
        maybe = q.get("maybe_requires_assets") is True
        prompt_assets = [a.get("path") for a in _prompt_side_assets(q) if a.get("path")]
        usable_prompt = any(_usable_prompt_asset(ws, a) for a in _prompt_side_assets(q))
        answer_assets = [a.get("path") for a in (q.get("assets") or [])
                         if isinstance(a, dict) and a.get("role") in ("answer_context", "worked_solution")
                         and a.get("path")]
        # official answer = teacher/material provenance ONLY — mixed/unknown/missing/ai_generated all
        # mean the answer is not (fully) from the teacher/material and must not read as official
        try:
            import validate_workspace as V
            official_sources = V.MATERIAL_SOURCES
        except Exception:                              # pragma: no cover — validator should be importable
            official_sources = {"teacher", "material"}
        official_src = q.get("source") in official_sources and q.get("ai_generated") is not True
        has_answer = official_src and any(q.get(k) not in (None, "", []) for k in ("answer", "answer_keywords"))
        q_hits = _visual_hits(fig_files, q.get("source_file"), q.get("source_pages"))
        a_hits = _visual_hits(fig_files, q.get("answer_source_file") or q.get("source_file"),
                              q.get("answer_source_pages"))
        # a provenance PDF that was never scanned means this item is UNVERIFIABLE, not non-visual —
        # stay silent and the recall net looks trusted while whole files slipped through
        if fig_files and q.get("source_file") and q.get("source_pages") \
                and not _file_indexed(fig_files, q.get("source_file")):
            sf_disp = str(q.get("source_file"))
            if sf_disp not in unindexed:
                unindexed.add(sf_disp)
                warnings.append("source_pdf_not_indexed: %s（题目出处 PDF 不在 --materials 扫描结果里，"
                                "这些题的疑漏不可核对）" % sf_disp)
        chap = q.get("chapter") if q.get("chapter") is not None else q.get("phase")   # phase-tagged banks
        rec = {
            "id": qid, "chapter": chap,
            "requires_assets": requires, "maybe_requires_assets": maybe,
            "prompt_assets": prompt_assets, "answer_assets": answer_assets,
            "source_file": q.get("source_file"), "source_pages": q.get("source_pages"),
            "has_official_answer": has_answer,
            "answer_source_file": q.get("answer_source_file"),
            "answer_source_pages": q.get("answer_source_pages"),
            "question_pages_visual": q_hits,
            "answer_pages_visual": (a_hits if q.get("answer_source_pages") else None),
        }
        questions.append(rec)
        ch = str(chap) if chap is not None else "?"
        c = per_chapter.setdefault(ch, {"questions": 0, "requires": 0, "maybe": 0, "suspects": 0})
        c["questions"] += 1
        c["requires"] += int(requires)
        c["maybe"] += int(maybe)
        # a declared-but-unusable (missing/unsafe) prompt asset must not suppress the suspect
        if q_hits and not requires and not maybe and not usable_prompt:
            c["suspects"] += 1
            suspects.append({"id": qid, "chapter": chap,
                             "source_file": q.get("source_file"), "visual_pages": q_hits,
                             "reason": "题目出处页面命中视觉页（结构/排版/词面信号），但题库未标图依赖且无可用题面 asset"})
    return questions, per_chapter, suspects


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def apply_suspects(ws, materials, bank, suspects, backend, asset_root, warnings):
    """Render each suspect's first visual page → attach as question_context page_image + set
    maybe_requires_assets=true. Backs up quiz_bank.json first. Returns number applied."""
    if not backend.can_render():
        _die("--apply 需要渲染后端（pip install pymupdf，或 pypdfium2+Pillow）才能把原页截图挂上题面", 3)
    # realpath containment — a symlinked asset-root living under ws but POINTING outside must be rejected
    # (the validator's realpath check would fail the written paths anyway; refuse up front)
    ws_abs, root_abs = os.path.realpath(ws), os.path.realpath(asset_root)
    if os.path.commonprefix([os.path.normcase(root_abs) + os.sep, os.path.normcase(ws_abs) + os.sep]) \
            != os.path.normcase(ws_abs) + os.sep:
        _die("--asset-root 必须真实位于工作区内（符号链接指向工作区外也不行）: %s" % asset_root)
    os.makedirs(root_abs, exist_ok=True)
    mat_real = os.path.realpath(materials) if materials else ""
    by_id = {str(q.get("id")): q for q in bank if isinstance(q, dict) and q.get("id") is not None}
    applied = 0
    for s in suspects:
        q = by_id.get(s["id"])
        if q is None:
            continue
        sf = str(q.get("source_file") or "").replace("\\", "/")
        # never resolve an absolute / drive-letter / '..'-escaping provenance name — the screenshot must
        # come from an INDEXED course file, not something outside --materials
        if os.path.isabs(sf) or re.match(r"^[A-Za-z]:", sf) or "://" in sf or ".." in sf.split("/"):
            warnings.append("apply_skip_unsafe_source: %s（source_file 不安全: %s）" % (s["id"], sf))
            continue
        pdf = None
        exact = os.path.join(materials, sf.replace("/", os.sep))
        if os.path.isfile(exact):
            pdf = exact
        elif "/" not in sf:                            # basename fallback ONLY for a bare name — a
            cands = []                                 # qualified path must match exactly (an unrelated
            for base, dirs, files in os.walk(materials):   # same-named PDF is not the declared source);
                for d in list(dirs):                       # prune leftover workspaces like the scan
                    full = os.path.join(base, d)
                    if d in ALWAYS_PRUNE or _is_leftover_workspace(full, d) or _is_workspace_root(full):
                        dirs.remove(d)
                if os.path.basename(sf) in files:
                    cands.append(os.path.join(base, os.path.basename(sf)))
            if len(cands) == 1:
                pdf = cands[0]
            elif len(cands) > 1:
                warnings.append("apply_skip_ambiguous: %s（%s 在材料里有 %d 个同名文件，无法确定出处，"
                                "请把 source_file 写成相对路径）" % (s["id"], sf, len(cands)))
                continue
        if pdf is None:
            warnings.append("apply_skip_no_pdf: %s（找不到 %s）" % (s["id"], sf))
            continue
        real = os.path.realpath(pdf)
        if os.path.commonprefix([os.path.normcase(real) + os.sep, os.path.normcase(mat_real) + os.sep]) \
                != os.path.normcase(mat_real) + os.sep:
            warnings.append("apply_skip_outside_materials: %s（%s 解析到材料目录外）" % (s["id"], sf))
            continue
        # ALL-or-nothing rendering: a question spanning several visual pages must not surface with a
        # partial prompt (one page attached, the continuation missing) — render the whole set first,
        # write/attach only when complete.
        renders = []
        complete = True
        for page in s["visual_pages"]:
            png = backend.render_page_png(pdf, page - 1)
            if not png:
                warnings.append("apply_skip_render_failed: %s p.%d（跨页题面须整套渲染，本题未回写）"
                                % (s["id"], page))
                complete = False
                break
            renders.append((page, png))
        if not complete:
            continue
        if not isinstance(q.get("assets"), list):      # normalize "assets": null / non-list before append
            q["assets"] = []
        # flipping maybe_requires_assets=true upgrades EVERY declared asset to fail-closed (the validator
        # errors on any unreadable one) — prune stale/unsafe leftovers first, loudly, so the applied
        # workspace stays valid (quiz_bank.json.bak already preserves the original)
        kept = []
        for a in q["assets"]:
            if isinstance(a, dict) and _usable_prompt_asset(ws, a):
                kept.append(a)
            else:
                warnings.append("apply_pruned_stale_asset: %s（移除不可用旧 asset %r，避免回写后校验失败）"
                                % (s["id"], a.get("path") if isinstance(a, dict) else a))
        q["assets"] = kept
        digest = hashlib.sha1(s["id"].encode("utf-8")).hexdigest()[:8]   # distinct ids never collide on
        for page, png in renders:                                        # sanitization/truncation
            name = "%s_%s_p%d.png" % (_SAFE_NAME_RE.sub("_", s["id"])[:60], digest, page)
            with open(os.path.join(root_abs, name), "wb") as f:
                f.write(png)
            q["assets"].append({
                "path": _rel_posix(ws_abs, os.path.join(root_abs, name)),
                "role": "question_context", "type": "page_image",
                "caption": "原页截图 %s p.%d（疑似图依赖，保守展示）" % (sf, page)})
        q["maybe_requires_assets"] = True
        applied += 1
    return applied


def run(argv=None, backend=None):
    ap = argparse.ArgumentParser(description="Build the generic dual visual index (recall-first; pure stdlib + optional PDF backend; no LLM/network).")
    ap.add_argument("--workspace", required=True, help="cram workspace (contains references/quiz_bank.json)")
    ap.add_argument("--materials", default=None, help="course materials folder (scan PDFs to build figure_page_index)")
    ap.add_argument("--out-dir", default=None, help="index output dir (default <workspace>/references/)")
    ap.add_argument("--apply", action="store_true",
                    help="attach rendered original pages as question-side assets for suspects and mark maybe_requires_assets=true (default: report only)")
    ap.add_argument("--asset-root", default=None, help="screenshot dir for --apply (default <workspace>/references/assets)")
    args = ap.parse_args(argv)

    ws = args.workspace
    bank_path = os.path.join(ws, "references", "quiz_bank.json")
    bank = _read_json(bank_path, "quiz_bank.json")
    if not isinstance(bank, list):
        _die("quiz_bank.json 必须是数组")
    out_dir = args.out_dir or os.path.join(ws, "references")
    os.makedirs(out_dir, exist_ok=True)
    warnings = []
    backend = backend or RealBackend()

    fig_files = {}
    if args.materials:
        if not os.path.isdir(args.materials):
            _die("找不到材料目录: %s" % args.materials)
        fig_files = scan_materials(args.materials, backend, warnings)
    else:
        warnings.append("no_materials: 未给 --materials——只建题目索引，无法交叉核对疑漏（召回网关闭）")

    questions, per_chapter, suspects = build_question_index(ws, bank, fig_files, warnings)

    applied = 0
    if args.apply and suspects:
        applied = apply_suspects(ws, args.materials or "", bank, suspects,
                                 backend, args.asset_root or os.path.join(ws, "references", "assets"), warnings)
        if applied:
            bak = bank_path + ".bak"
            with open(bak, "w", encoding="utf-8") as f, open(bank_path, "r", encoding="utf-8") as src:
                f.write(src.read())
            with open(bank_path, "w", encoding="utf-8") as f:
                json.dump(bank, f, ensure_ascii=False, indent=2)
            questions, per_chapter, suspects = build_question_index(ws, bank, fig_files)   # re-index post-apply

    fig_index = {
        "generated_by": "build_visual_index.py",
        "media_signals": backend.can_media(),
        "note": "确定性启发式（结构/排版/词面分层，召回优先）；不是语义判定，AI 识图为未来 opt-in",
        "files": {rel: {"pages": info["pages"],
                        "visual_pages": [{"page": p, **cls} for p, cls in sorted(info["visual"].items())]}
                  for rel, info in sorted(fig_files.items())},
        "warnings": warnings,
    }
    q_index = {
        "generated_by": "build_visual_index.py",
        "questions": questions, "per_chapter": per_chapter, "suspects": suspects,
        "applied": applied, "warnings": warnings,
    }
    with open(os.path.join(out_dir, "figure_page_index.json"), "w", encoding="utf-8") as f:
        json.dump(fig_index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "image_question_index.json"), "w", encoding="utf-8") as f:
        json.dump(q_index, f, ensure_ascii=False, indent=2)

    n_vis = sum(len(i["visual"]) for i in fig_files.values())
    print("[+] figure_page_index: %d 个文件 / %d 个视觉页；image_question_index: %d 题 / 疑漏 %d / 已回写 %d"
          % (len(fig_files), n_vis, len(questions), len(suspects), applied))
    for w in warnings:
        print("[!] " + w)
    if suspects and not args.apply:
        print("[!] 有 %d 道疑似漏标的图依赖题（详见 image_question_index.json 的 suspects）；"
              "用 --apply 渲染原页并标 maybe_requires_assets" % len(suspects))
    return 0


if __name__ == "__main__":
    sys.exit(run())
