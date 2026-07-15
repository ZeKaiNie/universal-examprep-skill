#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build the workspace's UNIVERSAL visual indices (P0-V2) — recall-first, subject-agnostic.

Primary goal: make sure NO image-dependent prompt, answer, or wiki source page silently loses its visual
context. Produces two indices (JSON, written into <workspace>/references/) plus three separate coverage nets:

  * image_question_index.json — every quiz_bank item's visual profile plus a separate teaching-example
    profile: requires/maybe flags, prompt/answer asset paths, source file+pages, whether an official answer
    exists, and whether the ANSWER pages are visual. Per-layer and combined chapter rollups are explicit.
  * figure_page_index.json — every VISUAL page in the course materials (lecture/homework PDFs): which file,
    which page, what kinds of visuals (figure/table/diagram/chart/graph/plot/screenshot/circuit/tree/map/
    geometry/flowchart …), detected by LAYERED signals — strongest first:
      1. structural: the page physically contains embedded images / many vector drawings (via optional
         PyMuPDF; no LLM) — catches pages with NO caption keywords at all;
      2. layout: figure/table numbering ("Figure 3", "表 2"), axis/legend vocabulary;
      3. keyword classes (weakest, multi-domain zh+en; NEVER tied to one subject like PDF/CDF).
  * prompt_suspects (legacy alias: suspects) — source pages are visual but prompt-side context is absent.
  * answer_suspects — answer pages are visual but answer_context/worked_solution is absent. --apply repairs
    both sides while preserving their roles: only prompt repair sets maybe_requires_assets=true.
  * wiki_visual_coverage — detected/embedded/missing visual pages correlated through
    `<!-- source.pdf p.N -->` anchors. --apply-wiki renders and page-locally attaches missing pages,
    idempotently, with a default per-chapter cap of 30 and a complete missing manifest.

Pure stdlib core; pypdf (text) / PyMuPDF (structural signals + render) / pypdfium2+Pillow (render fallback)
are OPTIONAL lazy imports. No network, no LLM, no API keys. Honest limits: the detector is a deterministic
heuristic (recall-first: prefer over-flagging `maybe` to missing a figure question); semantic/AI-vision
checking is a future opt-in, NOT implemented here.

    python scripts/build_visual_index.py --workspace <ws> --materials <course-folder>
    python scripts/build_visual_index.py --workspace <ws> --materials <dir> --apply
    python scripts/build_visual_index.py --workspace <ws> --materials <dir> --apply-wiki

Exit codes: 0 ok · 2 bad input · 3 materials contain PDFs but the needed backend is missing.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile

try:
    from .ingestion import workspace_publication_lock
except ImportError:
    from ingestion import workspace_publication_lock

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
# Structural thresholds — recall-first: any embedded image counts.  The original >=6 drawing
# threshold missed real four-object Venn diagrams in EEC 160; >=4 recovers those pages while the
# ubiquitous one-object slide frame still stays below the gate.  Text signals remain independent.
_MIN_DRAWINGS = 4
_COLUMN_GAP_RE = re.compile(r"\S[ \t]{5,}\S")


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
    # A captionless mapping table may have no embedded raster and only the slide frame as a drawing.
    # Three aligned, widely-separated text rows are conservative evidence that layout itself carries
    # meaning (e.g. "Experiment   Set Theory" followed by Outcome/Element rows).
    layout_table = sum(1 for line in (text or "").splitlines()
                       if _COLUMN_GAP_RE.search(line)) >= 3
    if layout_table and "table" not in kinds:
        kinds.append("table")
        kinds.sort()
    structural = (images or 0) > 0 or (drawings or 0) >= _MIN_DRAWINGS
    figref = bool(_FIGREF_RE.search(t))
    axis = _any_hit(t, _AXIS_PATTERNS)
    return {
        "has_visual": bool(structural or figref or axis or kinds),
        "visual_kinds": kinds,
        "signals": {"images": int(images or 0), "drawings": int(drawings or 0),
                    "structural": structural, "layout_table": layout_table,
                    "figref": figref, "axis": axis},
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


def _canonical_rel_posix(root, path):
    """Return a relative Markdown path after canonicalizing both endpoints.

    Windows can expose the same temp directory once through an 8.3 alias (``RUNNER~1``) and once
    through its long name (``runneradmin``).  Relativizing those lexical spellings directly walks
    up to the drive root even though both files are siblings.  ``realpath`` on both sides gives the
    path calculation one namespace; callers still get a portable POSIX-style Markdown link.
    """
    return _rel_posix(os.path.realpath(root), os.path.realpath(path))


def _atomic_write_bytes(path, payload):
    """Write without following a predictable destination/temp symlink, then atomically replace."""
    directory = os.path.dirname(path) or "."
    if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):
        raise OSError("refusing symbolic-link or special-file destination: %s" % path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _path_has_symlink_component(root, path):
    """Return True when a lexical child component is a symlink (including a broken one).

    ``realpath`` containment catches links that currently escape, but it does not reveal a parent
    link which happens to point back inside the workspace.  Mutating wiki source files through any
    such indirection is needlessly ambiguous, so the apply path rejects it explicitly.
    """
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    try:
        rel = os.path.relpath(path_abs, root_abs)
    except ValueError:
        return True
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return True
    current = root_abs
    for part in (() if rel == "." else rel.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return True
    return False


def _safe_workspace_dir(ws, path, label, create=False):
    """Return an absolute mutable directory below ws, rejecting lexical/real escapes and links."""
    ws_abs, path_abs = os.path.abspath(ws), os.path.abspath(path)
    if os.path.islink(ws_abs):
        _die("--workspace 本身不能是符号链接: %s" % ws_abs)
    try:
        lexical_inside = (os.path.commonpath([os.path.normcase(path_abs), os.path.normcase(ws_abs)])
                          == os.path.normcase(ws_abs))
    except ValueError:
        lexical_inside = False
    if not lexical_inside or _path_has_symlink_component(ws_abs, path_abs):
        _die("%s 必须是工作区内且不含符号链接的目录: %s" % (label, path_abs))
    if create:
        try:
            os.makedirs(path_abs, exist_ok=True)
        except OSError as exc:
            _die("无法创建 %s：%s" % (label, exc))
    if not os.path.isdir(path_abs):
        _die("%s 不是目录: %s" % (label, path_abs))
    ws_real, path_real = os.path.realpath(ws_abs), os.path.realpath(path_abs)
    try:
        real_inside = (os.path.commonpath([os.path.normcase(path_real), os.path.normcase(ws_real)])
                       == os.path.normcase(ws_real))
    except ValueError:
        real_inside = False
    if not real_inside:
        _die("%s 解析到工作区外: %s" % (label, path_abs))
    return path_abs


def _safe_workspace_file(ws, path, label):
    """Validate a read/mutate target is a regular workspace file with no linked component."""
    ws_abs, path_abs = os.path.abspath(ws), os.path.abspath(path)
    try:
        inside = (os.path.commonpath([os.path.normcase(path_abs), os.path.normcase(ws_abs)])
                  == os.path.normcase(ws_abs))
    except ValueError:
        inside = False
    if not inside or _path_has_symlink_component(ws_abs, path_abs):
        _die("%s 必须是工作区内且不含符号链接的普通文件: %s" % (label, path_abs))
    if not os.path.isfile(path_abs):
        _die("找不到%s或目标不是普通文件: %s" % (label, path_abs))
    return path_abs


def _safe_wiki_file(ws, wiki_file):
    """Resolve one mutable wiki filename, rejecting traversal, links and non-regular files."""
    if (not isinstance(wiki_file, str) or not wiki_file.strip()
            or os.path.basename(wiki_file) != wiki_file or wiki_file in (".", "..")):
        return None, "unsafe wiki filename"
    wiki_dir = os.path.join(ws, "references", "wiki")
    path = os.path.join(wiki_dir, wiki_file)
    if _path_has_symlink_component(ws, wiki_dir) or _path_has_symlink_component(ws, path):
        return None, "wiki path contains a symbolic-link component"
    ws_real, path_real = os.path.realpath(ws), os.path.realpath(path)
    try:
        inside = (os.path.commonpath([os.path.normcase(path_real), os.path.normcase(ws_real)])
                  == os.path.normcase(ws_real))
    except ValueError:
        inside = False
    if not inside:
        return None, "wiki path resolves outside workspace"
    if not os.path.isfile(path):
        return None, "wiki file is missing or not regular"
    return path, None


def _sha256_regular_file(ws, rel):
    """Hash one workspace-relative regular file without following symlink components."""
    path = os.path.join(ws, *rel.split("/"))
    if _path_has_symlink_component(ws, path) or not os.path.isfile(path):
        return None
    ws_real, path_real = os.path.realpath(ws), os.path.realpath(path)
    try:
        if os.path.commonpath([os.path.normcase(path_real), os.path.normcase(ws_real)]) \
                != os.path.normcase(ws_real):
            return None
    except ValueError:
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _sha256_root_file(root, rel):
    """Hash a regular relative file below ``root`` without following any symlink component."""
    norm = str(rel or "").replace("\\", "/")
    parts = norm.split("/")
    if (not norm or os.path.isabs(norm) or re.match(r"^[A-Za-z]:", norm)
            or any(part in ("", ".", "..") for part in parts)):
        return None
    current = os.path.abspath(root)
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return None
    root_real, path_real = os.path.realpath(root), os.path.realpath(current)
    try:
        inside = os.path.commonpath([os.path.normcase(path_real), os.path.normcase(root_real)]) \
            == os.path.normcase(root_real)
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(current):
        return None
    digest = hashlib.sha256()
    try:
        with open(current, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _manifest_result_sha256(value):
    """Hash one derived index payload while excluding its self-referential integrity block."""
    payload = {key: val for key, val in value.items() if key != "integrity"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _integrity_snapshot(ws, args, backend, warnings, item_layers=(), coverage=None,
                        fig_files=None):
    """Capture the exact mutable inputs used by both visual indices.

    The phase-completion gate compares these digests with the current bank, teaching manifest and
    phase wiki.  Without this snapshot a stale index with zero suspects could be replayed after any
    of those sources changed and incorrectly certify the phase.
    """
    rels = {"references/quiz_bank.json"}
    for optional_rel in (
            "references/teaching_examples.json",
            "references/teaching_baseline.json",
            "ingest_report.json"):
        if os.path.lexists(os.path.join(ws, *optional_rel.split("/"))):
            rels.add(optional_rel)
    wiki_dir = os.path.join(ws, "references", "wiki")
    if (os.path.isdir(wiki_dir) and not _path_has_symlink_component(ws, wiki_dir)):
        rels.update("references/wiki/" + fn for fn in sorted(os.listdir(wiki_dir))
                    if fn.lower().endswith(".md")
                    and os.path.isfile(os.path.join(wiki_dir, fn))
                    and not os.path.islink(os.path.join(wiki_dir, fn)))
    # Index conclusions also depend on whether declared assets were readable at generation time.
    # Bind those files too, so deleting/replacing an image cannot replay a stale zero-suspect index.
    for layer in item_layers:
        for item in layer or []:
            if not isinstance(item, dict):
                continue
            for asset in item.get("assets") or []:
                raw = asset.get("path") if isinstance(asset, dict) else None
                rel = str(raw or "").strip().replace("\\", "/")
                if (rel and "://" not in rel and not rel.startswith("/")
                        and not re.match(r"^[A-Za-z]:", rel) and ".." not in rel.split("/")):
                    rels.add(rel)
    for page in (coverage or {}).get("pages") or []:
        if not isinstance(page, dict):
            continue
        for raw in page.get("asset_paths") or []:
            rel = str(raw or "").strip().replace("\\", "/")
            if rel:
                rels.add(rel)
    inputs = {}
    for rel in sorted(rels):
        digest = _sha256_regular_file(ws, rel)
        if digest is None:
            warnings.append("integrity_input_unreadable: %s（视觉索引 freshness 无法建立）" % rel)
            continue
        inputs[rel] = {"sha256": digest}
    material_inputs = {}
    materials_root = os.path.abspath(args.materials) if args.materials else None
    for rel in sorted((fig_files or {}).keys()):
        digest = _sha256_root_file(materials_root, rel) if materials_root else None
        if digest is None:
            warnings.append("integrity_material_unreadable: %s（原始 PDF freshness 无法建立）" % rel)
            continue
        material_inputs[rel] = {"sha256": digest}
    inventory = sorted(_rel_posix(materials_root, path)
                       for path in (_material_pdf_paths(materials_root) if materials_root else []))
    inventory_sha256 = hashlib.sha256(json.dumps(
        inventory, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": 2,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": {
            "materials_scan": bool(args.materials),
            "materials_root": materials_root,
            "apply_questions": bool(args.apply),
            "apply_wiki": bool(args.apply_wiki),
            "backend": str(getattr(backend, "name", type(backend).__name__)),
        },
        "inputs": inputs,
        "materials": material_inputs,
        "material_inventory_sha256": inventory_sha256,
    }


def _material_pdf_paths(materials):
    """List source PDFs with the same pruning policy used by scan and freshness checks."""
    pdfs = []
    for base, dirs, files in os.walk(materials):
        for d in list(dirs):
            full = os.path.join(base, d)
            if d in ALWAYS_PRUNE or _is_leftover_workspace(full, d) or _is_workspace_root(full):
                dirs.remove(d)
        for fn in sorted(files):
            if fn.lower().endswith(".pdf"):
                pdfs.append(os.path.join(base, fn))
    return sorted(pdfs)


def scan_materials(materials, backend, warnings):
    """Walk the course folder for PDFs (skipping tooling/leftover-workspace dirs) and classify every page.
    Returns {rel_pdf_path: {"pages": n, "visual": {page_no(1-based): classify_page(...)}}}"""
    pdfs = _material_pdf_paths(materials)
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
            if "\x00" in (text or ""):
                warnings.append("nul_text: %s p.%d（提取文本含 %d 个 NUL 字节——该页可能把图/空间布局"
                                "退化成二进制残渣，须对照原页复核）"
                                % (rel, i + 1, (text or "").count("\x00")))
            imgs, draws = (media[i] if media and i < len(media) else (0, 0))
            cls = classify_page(text, images=imgs, drawings=draws)
            if cls["has_visual"]:
                visual[i + 1] = cls                     # 1-based page numbers, matching source_pages
        out[rel] = {"pages": len(texts), "visual": visual}
    return out


def _prompt_side_assets(q):
    side = {"question_context", "figure", "diagram", "table"}
    return [a for a in (q.get("assets") or []) if isinstance(a, dict) and a.get("role") in side]


def _answer_side_assets(q):
    side = {"answer_context", "worked_solution"}
    return [a for a in (q.get("assets") or []) if isinstance(a, dict) and a.get("role") in side]


def _usable_asset(ws, a):
    """Same rules as validate_workspace: safe path AND the file actually exists/readable. A declared but
    stale/missing asset must NOT suppress a suspect (it can't be displayed)."""
    try:
        import validate_workspace as V
        full, unsafe = V._asset_safety(ws, a.get("path"))
    except Exception:                                  # pragma: no cover — validator should be importable
        return bool(a.get("path"))
    return (not unsafe) and full and os.path.isfile(full) and os.access(full, os.R_OK)


def _usable_prompt_asset(ws, a):
    """Backward-compatible named wrapper used by older callers/tests."""
    return _usable_asset(ws, a)


_AUDITED_PROMPT_ASSET_TYPES = {"crop_image", "diagram", "table_image"}


def _usable_audited_prompt_asset(ws, a):
    """Whether a prompt asset is explicitly independent of a whole source-page screenshot.

    A shared prompt/answer page cannot safely be exposed before the question is answered.  Only a
    readable question-side crop (or a standalone diagram/table) is sufficient evidence that a human
    separated the prompt from the worked answer.  ``page_image`` and untyped/``other_image`` assets
    remain fail-closed because they may still be the leaking whole page.
    """
    return (isinstance(a, dict)
            and a.get("type") in _AUDITED_PROMPT_ASSET_TYPES
            and _usable_asset(ws, a))


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


def build_question_index(ws, bank, fig_files, warnings=None, source_layer="quiz_bank",
                         shared_prompt_answer_pages=None):
    """Per-item visual profile + per-chapter rollup + prompt/answer recall nets.

    The fourth return value is intentionally separate: answer-side screenshots must never be mistaken
    for prompt-side context or flip the fail-closed question gate.
    """
    questions, prompt_suspects, answer_suspects = [], [], []
    per_chapter = {}
    warnings = warnings if warnings is not None else []
    unindexed = set()
    shared_prompt_answer_pages = set(shared_prompt_answer_pages or ())
    for q in bank:
        if not isinstance(q, dict) or q.get("id") is None:
            continue
        qid = str(q["id"])
        requires = q.get("requires_assets") is True
        maybe = q.get("maybe_requires_assets") is True
        prompt_assets = [a.get("path") for a in _prompt_side_assets(q) if a.get("path")]
        usable_prompt = any(_usable_prompt_asset(ws, a) for a in _prompt_side_assets(q))
        audited_prompt = any(_usable_audited_prompt_asset(ws, a)
                             for a in _prompt_side_assets(q))
        answer_assets = [a.get("path") for a in _answer_side_assets(q) if a.get("path")]
        usable_answer = any(_usable_asset(ws, a) for a in _answer_side_assets(q))
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
        item_prompt_pages = _resolved_page_pairs(
            fig_files, q.get("source_file"), q.get("source_pages"))
        item_shared_pages = sorted(item_prompt_pages & shared_prompt_answer_pages)
        item_shared_refs = [{"source_file": source, "page": page}
                            for source, page in item_shared_pages]
        # a provenance PDF that was never scanned means this item is UNVERIFIABLE, not non-visual —
        # stay silent and the recall net looks trusted while whole files slipped through
        if fig_files and q.get("source_file") and q.get("source_pages") \
                and not _file_indexed(fig_files, q.get("source_file")):
            sf_disp = str(q.get("source_file"))
            if sf_disp not in unindexed:
                unindexed.add(sf_disp)
                warnings.append("source_pdf_not_indexed: %s（题目出处 PDF 不在 --materials 扫描结果里，"
                                "这些题的疑漏不可核对）" % sf_disp)
        ans_sf = q.get("answer_source_file") or q.get("source_file")
        if fig_files and ans_sf and q.get("answer_source_pages") and not _file_indexed(fig_files, ans_sf):
            sf_disp = str(ans_sf)
            key = "answer:" + sf_disp
            if key not in unindexed:
                unindexed.add(key)
                warnings.append("answer_source_pdf_not_indexed: %s（答案出处 PDF 不在 --materials 扫描结果里，"
                                "答案侧视觉疑漏不可核对）" % sf_disp)
        chap = q.get("chapter") if q.get("chapter") is not None else q.get("phase")   # phase-tagged banks
        rec = {
            "id": qid, "chapter": chap, "source_layer": source_layer,
            "requires_assets": requires, "maybe_requires_assets": maybe,
            "prompt_assets": prompt_assets, "answer_assets": answer_assets,
            "source_file": q.get("source_file"), "source_pages": q.get("source_pages"),
            "has_official_answer": has_answer,
            "answer_source_file": q.get("answer_source_file"),
            "answer_source_pages": q.get("answer_source_pages"),
            "question_pages_visual": q_hits,
            "answer_pages_visual": (a_hits if q.get("answer_source_pages") else None),
            "shared_prompt_answer_pages": item_shared_refs,
            "has_audited_prompt_asset": audited_prompt,
        }
        questions.append(rec)
        ch = str(chap) if chap is not None else "?"
        c = per_chapter.setdefault(ch, {"questions": 0, "requires": 0, "maybe": 0,
                                        "suspects": 0, "prompt_suspects": 0,
                                        "answer_suspects": 0,
                                        "shared_prompt_answer_blockers": 0})
        c["questions"] += 1
        c["requires"] += int(requires)
        c["maybe"] += int(maybe)
        # A declared visual gate with no readable question-side asset is itself a blocking suspect,
        # even when the PDF scan found no visual signal (or could not run).  Otherwise
        # ``requires_assets=true`` plus a stale path could disappear behind an empty q_hits list.
        declared_but_unusable = (requires or maybe) and not usable_prompt
        inferred_unlabelled = bool(q_hits) and not requires and not maybe and not usable_prompt
        shared_unresolved = bool(item_shared_pages) and not audited_prompt
        if declared_but_unusable or inferred_unlabelled or shared_unresolved:
            c["suspects"] += 1
            c["prompt_suspects"] += 1
            c["shared_prompt_answer_blockers"] += int(shared_unresolved)
            candidate_pages = q_hits or [p for p in (q.get("source_pages") or [])
                                         if isinstance(p, int) and not isinstance(p, bool) and p >= 1]
            reason = ("题面页同时也是答案页；整页截图会提前泄露答案，须提供经审核的独立题面裁剪图"
                      if shared_unresolved else
                      "题目已声明 requires_assets/maybe_requires_assets，但没有可读的题面 asset"
                      if declared_but_unusable else
                      "题目出处页面命中视觉页（结构/排版/词面信号），但当前来源层未标图依赖且无可用题面 asset")
            suspect = {"id": qid, "chapter": chap, "source_layer": source_layer,
                       "source_file": q.get("source_file"),
                       "visual_pages": candidate_pages,
                       "reason": reason}
            if shared_unresolved:
                suspect.update({
                    "blocker": "shared_prompt_answer_page",
                    "auto_apply_blocked": True,
                    "shared_prompt_answer_pages": item_shared_refs,
                })
                warnings.append(
                    "shared_prompt_answer_page: %s:%s 的题面页与答案页重合（%s）；"
                    "禁止自动挂整页题面图，须提供可读的 crop_image/diagram/table_image"
                    % (source_layer, qid,
                       "、".join("%s p.%d" % (r["source_file"], r["page"])
                                for r in item_shared_refs)))
            prompt_suspects.append(suspect)
        if a_hits and not usable_answer:
            c["answer_suspects"] += 1
            answer_suspects.append({"id": qid, "chapter": chap, "source_layer": source_layer,
                                    "source_file": ans_sf, "visual_pages": a_hits,
                                    "reason": "答案出处页面命中视觉页，但当前来源层没有可用 answer_context/worked_solution asset"})
    return questions, per_chapter, prompt_suspects, answer_suspects


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def _apply_page_suspects(ws, materials, bank, suspects, backend, asset_root, warnings, side):
    """Shared all-or-nothing renderer for prompt/answer suspects.

    `side=prompt` preserves the historical --apply behavior and flips maybe_requires_assets only after
    every visual prompt page is safely attached. `side=answer` adds answer_context assets only; it must
    never alter the prompt gate.
    """
    if side == "prompt":
        safe_to_render = []
        for suspect in suspects:
            if suspect.get("blocker") != "shared_prompt_answer_page":
                safe_to_render.append(suspect)
                continue
            refs = ["%s p.%d" % (r.get("source_file"), r.get("page"))
                    for r in (suspect.get("shared_prompt_answer_pages") or [])
                    if isinstance(r, dict)]
            warnings.append(
                "apply_skip_shared_prompt_answer_page: %s（%s 与答案同页；整页题面截图会泄题，"
                "仅接受已有可读的独立题面裁剪资源）"
                % (suspect["id"], "、".join(refs) or "共享页"))
        suspects = safe_to_render
        if not suspects:
            return 0
    if not backend.can_render():
        _die("--apply 需要渲染后端（pip install pymupdf，或 pypdfium2+Pillow）才能把原页截图挂入题库", 3)
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
        if not (s.get("visual_pages") or []):
            warnings.append("%sapply_skip_no_pages: %s（图依赖声明缺少可渲染 source_pages，未回写）"
                            % ("answer_" if side == "answer" else "", s["id"]))
            continue
        fallback_sf = ((q.get("answer_source_file") or q.get("source_file")) if side == "answer"
                       else q.get("source_file"))
        sf = str(s.get("source_file") or fallback_sf or "").replace("\\", "/")
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
                warnings.append("%sapply_skip_render_failed: %s p.%d（跨页%s须整套渲染，本题未回写）"
                                % ("answer_" if side == "answer" else "", s["id"], page,
                                   "答案" if side == "answer" else "题面"))
                complete = False
                break
            renders.append((page, png))
        if not complete:
            continue
        if not isinstance(q.get("assets"), list):      # normalize "assets": null / non-list before append
            q["assets"] = []
        # Prompt apply flips maybe=true, upgrading every declared asset to fail-closed: prune all stale
        # declarations first. Answer apply is not a prompt gate, so prune only stale answer-side entries.
        kept = []
        for a in q["assets"]:
            is_answer_asset = isinstance(a, dict) and a.get("role") in ("answer_context", "worked_solution")
            keep = (isinstance(a, dict) and _usable_asset(ws, a)) if side == "prompt" \
                else not is_answer_asset or _usable_asset(ws, a)
            if keep:
                kept.append(a)
            else:
                warnings.append("%sapply_pruned_stale_asset: %s（移除不可用旧 asset %r，避免回写后仍是假覆盖）"
                                % ("answer_" if side == "answer" else "", s["id"],
                                   a.get("path") if isinstance(a, dict) else a))
        q["assets"] = kept
        layer = str(s.get("source_layer") or "quiz_bank")
        digest_seed = "%s\0%s\0%s\0%s" % (layer, s["id"], side, sf)
        digest = hashlib.sha1(digest_seed.encode("utf-8")).hexdigest()[:8]
        for page, png in renders:                                        # sanitization/truncation
            name = "%s_%s%s_p%d.png" % (_SAFE_NAME_RE.sub("_", s["id"])[:60], digest,
                                           "_answer" if side == "answer" else "", page)
            _atomic_write_bytes(os.path.join(root_abs, name), png)
            rel_asset = _rel_posix(ws_abs, os.path.join(root_abs, name))
            role = "answer_context" if side == "answer" else "question_context"
            if not any(isinstance(a, dict) and a.get("path") == rel_asset and a.get("role") == role
                       for a in q["assets"]):
                q["assets"].append({
                    "path": rel_asset, "role": role, "type": "page_image",
                    "caption": "原页截图 %s p.%d（%s侧视觉，保守展示）"
                               % (sf, page, "答案" if side == "answer" else "题面")})
        if side == "prompt":
            q["maybe_requires_assets"] = True
        applied += 1
    return applied


def apply_suspects(ws, materials, bank, suspects, backend, asset_root, warnings):
    """Backward-compatible prompt-side --apply entry point."""
    return _apply_page_suspects(ws, materials, bank, suspects, backend, asset_root, warnings, "prompt")


def apply_answer_suspects(ws, materials, bank, suspects, backend, asset_root, warnings):
    """Attach visual official-answer pages as answer_context without changing prompt-side gating."""
    return _apply_page_suspects(ws, materials, bank, suspects, backend, asset_root, warnings, "answer")


def _merge_per_chapter(*rollups):
    merged = {}
    for rollup in rollups:
        for chapter, counts in (rollup or {}).items():
            row = merged.setdefault(chapter, {})
            for key, value in counts.items():
                row[key] = row.get(key, 0) + value
    return merged


def _write_json_with_backup(path, value):
    """Atomically replace a JSON manifest after preserving its exact previous bytes."""
    with open(path, "rb") as src:
        original = src.read()
    backup = path + ".bak"
    _atomic_write_bytes(backup, original)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, payload)


# ---------------- wiki-side visual coverage + idempotent repair ----------------

_WIKI_PAGE_RE = re.compile(r"<!--(?!\s*wiki-visual-index:)\s*(.*?)\s+p\.(\d+)\s*-->")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_OLD_GALLERY_ALT_RE = re.compile(r"^(.*?)\s+第\s*(\d+)\s*页图示\s*$")
_INDEX_ALT_RE = re.compile(r"^原页图示\s+(.*?)\s+p\.(\d+)\s*$")
_WIKI_MARKED_IMAGE_RE = re.compile(
    r"<!--\s*wiki-visual-index:\s*(.*?)\s+p\.(\d+)\s*-->\s*"
    r"!\[([^\]]*)\]\(([^)]+)\)", re.S)


def _resolve_fig_file(fig_files, source_file):
    """Resolve a wiki provenance token against the scanned index; basename fallback is unique-only."""
    sf = str(source_file or "").strip().replace("\\", "/")
    if sf in fig_files:
        return sf
    if "/" in sf:
        return None
    hits = [rel for rel in fig_files if os.path.basename(rel) == sf]
    return hits[0] if len(hits) == 1 else None


def _resolve_fig_files(fig_files, source_file):
    """Resolve item provenance conservatively; a bare basename covers every matching PDF.

    Wiki anchors require a unique basename so a page is never attached to the wrong chapter.  Side
    classification has the opposite safety goal: an ambiguous official-answer basename must defer
    every possible matching page, otherwise one duplicate could leak a solution into the wiki.
    """
    sf = str(source_file or "").strip().replace("\\", "/")
    if sf in fig_files:
        return [sf]
    if not sf or "/" in sf:
        return []
    return sorted(rel for rel in fig_files if os.path.basename(rel) == sf)


def _resolved_page_pairs(fig_files, source_file, pages):
    """Resolve provenance pages, preserving an unindexed token for same-source safety checks."""
    rels = _resolve_fig_files(fig_files, source_file)
    if not rels:
        unresolved = str(source_file or "").strip().replace("\\", "/")
        rels = [unresolved] if unresolved else []
    return {(rel, page)
            for rel in rels
            for page in (pages or [])
            if type(page) is int and page >= 1}


def derive_item_page_sides(fig_files, *item_layers):
    """Return ``(prompt_pages, answer_pages)`` as resolved ``(source, page)`` pairs.

    A page used on both sides remains prompt-eligible; only ``answer_pages - prompt_pages`` is
    deferred from wiki galleries.  Quiz-bank and teaching-example provenance participate equally so
    an answer page cannot leak merely because it came from the optional teaching layer.
    """
    prompt_pages, answer_pages = set(), set()
    for layer in item_layers:
        for item in layer or []:
            if not isinstance(item, dict):
                continue
            prompt_pages.update(_resolved_page_pairs(
                fig_files, item.get("source_file"), item.get("source_pages")))
            answer_source = item.get("answer_source_file") or item.get("source_file")
            answer_pages.update(_resolved_page_pairs(
                fig_files, answer_source, item.get("answer_source_pages")))
    return prompt_pages, answer_pages


def _wiki_asset_record(ws, wiki_path, raw_link, origin=None):
    """Resolve one Markdown image relative to its wiki file and classify it without network access."""
    link = str(raw_link or "").strip()
    if link.startswith("<") and link.endswith(">"):
        link = link[1:-1].strip()
    norm = link.replace("\\", "/")
    if not norm or "://" in norm or norm.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", norm):
        return {"markdown_path": link, "workspace_path": None, "usable": False,
                "unsafe": True, "origin": origin}
    full = os.path.realpath(os.path.join(os.path.dirname(wiki_path), *norm.split("/")))
    ws_real = os.path.realpath(ws)
    try:
        inside = os.path.commonpath([os.path.normcase(full), os.path.normcase(ws_real)]) == os.path.normcase(ws_real)
    except ValueError:
        inside = False
    rel = _rel_posix(ws_real, full) if inside else None
    return {"markdown_path": link, "workspace_path": rel, "origin": origin,
            "usable": bool(inside and os.path.isfile(full) and os.access(full, os.R_OK)),
            "unsafe": not inside}


def _wiki_inventory(ws, fig_files, warnings):
    """Return anchors/assets plus the source-page claims made by every wiki file.

    ``claims[source][wiki]`` lets coverage retain an image-only PDF page even when an older wiki
    omitted that exact page anchor.  The page is assigned to the nearest page-claiming wiki (or the
    sole claimant), marked as inferred, and can then be repaired by ``--apply-wiki``.
    """
    wiki_dir = os.path.join(ws, "references", "wiki")
    anchors, assets, manual_block_assets, claims = {}, {}, {}, {}
    if not os.path.isdir(wiki_dir):
        warnings.append("wiki_visual_no_wiki_dir: references/wiki 不存在，无法核对视觉覆盖")
        return anchors, assets, manual_block_assets, claims
    if _path_has_symlink_component(ws, wiki_dir):
        warnings.append("wiki_visual_unsafe_wiki_dir: references/wiki 含符号链接父级，拒绝把外部内容计入覆盖")
        return anchors, assets, manual_block_assets, claims
    for fn in sorted(os.listdir(wiki_dir)):
        path = os.path.join(wiki_dir, fn)
        if not fn.lower().endswith(".md") or not os.path.isfile(path) or os.path.islink(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text0 = f.read()
        except (OSError, UnicodeDecodeError) as e:
            warnings.append("wiki_visual_read_failed: %s (%s)" % (fn, e))
            continue
        marks = list(_WIKI_PAGE_RE.finditer(text0))
        for i, mark in enumerate(marks):
            rel = _resolve_fig_file(fig_files, mark.group(1))
            if rel is None:
                continue
            page = int(mark.group(2))
            key = (fn, rel, page)
            anchors[key] = True
            claims.setdefault(rel, {}).setdefault(fn, set()).add(page)
            block = text0[mark.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text0))]
            claimed_spans = []
            # New repairs carry a machine-readable marker immediately before the image.  The marker,
            # not merely the image's position in a source block, establishes page provenance.
            for marked in _WIKI_MARKED_IMAGE_RE.finditer(block):
                exp_rel = _resolve_fig_file(fig_files, marked.group(1))
                if exp_rel is None:
                    continue
                target = (fn, exp_rel, int(marked.group(2)))
                assets.setdefault(target, []).append(
                    _wiki_asset_record(ws, path, marked.group(4).strip(), "generated"))
                claimed_spans.append(marked.span())
            # Legacy galleries predate the marker but encode the same source/page pair in their alt
            # text.  Arbitrary images (logos, icons, decorative screenshots) no longer count merely
            # because they happen to sit after a source anchor.
            for image in _MD_IMAGE_RE.finditer(block):
                if any(start <= image.start() and image.end() <= end for start, end in claimed_spans):
                    continue
                alt, raw_link = image.group(1).strip(), image.group(2).strip()
                manual = _wiki_asset_record(ws, path, raw_link, "manual")
                # Any unmarked image inside an answer-only source block is a potential solution leak,
                # even when its alt text is generic.  It must be reported, never auto-deleted.
                manual_block_assets.setdefault(key, []).append(manual)
                explicit = _OLD_GALLERY_ALT_RE.match(alt)
                if not explicit:
                    continue
                exp_rel = _resolve_fig_file(fig_files, explicit.group(1))
                if exp_rel is None:
                    continue
                target = (fn, exp_rel, int(explicit.group(2)))
                assets.setdefault(target, []).append(manual)
    return anchors, assets, manual_block_assets, claims


def _visual_wiki_keys(source_file, page, anchors, claims):
    """Map one detected material page to wiki coverage keys without dropping an absent anchor."""
    exact = sorted(k for k in anchors if k[1] == source_file and k[2] == page)
    if exact:
        return [(key, False) for key in exact]
    candidates = claims.get(source_file) or {}
    if not candidates:
        return [((None, source_file, page), True)]
    ranked = []
    for wiki_file, known_pages in candidates.items():
        distance = min(abs(page - known) for known in known_pages)
        ranked.append((distance, wiki_file))
    # A page without its own anchor is necessarily inferred.  Choose deterministically by nearest
    # known source page; ties use the filename.  This is conservative reachability bookkeeping, not
    # a semantic chapter classifier, and the record exposes anchor_inferred=true.
    _distance, chosen = min(ranked)
    return [((chosen, source_file, page), True)]


def _inventory_wiki_keys(source_file, page, anchors, assets, manual_block_assets):
    """Return exact wiki inventory keys even when visual heuristics missed the source page."""
    inventory = set(anchors) | set(assets) | set(manual_block_assets)
    return sorted(key for key in inventory if key[1] == source_file and key[2] == page)


def build_wiki_visual_coverage(ws, fig_files, warnings=None, missing_reasons=None,
                               emit_warnings=True, prompt_pages=None, answer_pages=None,
                               shared_blocker_pages=None):
    """Cross-check wiki-eligible pages while deferring every solution-sensitive whole page.

    ``detected`` intentionally remains the backward-compatible wiki coverage denominator, so the
    invariant ``detected == embedded + missing`` is preserved.  Answer-only and shared
    prompt/answer pages are reported in separate deferred buckets.  Exact wiki inventory entries
    for those pages are inspected even when visual heuristics classified the source page as text;
    otherwise a hand-authored full-page screenshot could evade the solution-leak guard.
    """
    local_warnings = []
    anchors, assets, manual_block_assets, claims = _wiki_inventory(ws, fig_files, local_warnings)
    reasons = missing_reasons or {}
    prompt_pages = set(prompt_pages or ())
    answer_pages = set(answer_pages or ())
    shared_pages = prompt_pages & answer_pages
    shared_blocker_pages = set(shared_blocker_pages or ())
    pages = []

    def refs(records, usable_only=False):
        values = set()
        for asset in records:
            if usable_only and not asset.get("usable"):
                continue
            value = asset.get("workspace_path") or asset.get("markdown_path")
            if value:
                values.add(value)
        return sorted(values)

    visual_pairs = {(source_file, page)
                    for source_file, info in fig_files.items()
                    for page in (info.get("visual") or {})}
    candidates = []
    for source_file, info in sorted(fig_files.items()):
        for page, cls in sorted((info.get("visual") or {}).items()):
            for key, inferred in _visual_wiki_keys(source_file, page, anchors, claims):
                candidates.append((key, inferred, cls, False))
    # A solution-sensitive page with an exact wiki anchor/image must be audited even if the
    # heuristic detector saw no figure.  Do not infer a chapter for inventory-only rows: the exact
    # inventory key is the evidence that a potentially leaking screenshot is already present.
    for source_file, page in sorted(answer_pages):
        if (source_file, page) in visual_pairs:
            continue
        inventory_keys = _inventory_wiki_keys(
            source_file, page, anchors, assets, manual_block_assets)
        # Shared pages are explicit safety blockers even without a detected visual or existing wiki
        # anchor.  Answer-only text pages need a concrete inventory key because this coverage layer
        # is auditing actual wiki exposure, not every textual answer provenance page.
        if not inventory_keys and (source_file, page) in shared_pages:
            inventory_keys = [(None, source_file, page)]
        for key in inventory_keys:
            candidates.append((key, False, {}, True))

    seen = set()
    for (wiki_file, source_file, page), inferred, cls, inventory_only in candidates:
        key = (wiki_file, source_file, page)
        if key in seen:
            continue
        seen.add(key)
        declared = assets.get(key, [])
        usable = refs(declared, usable_only=True)
        rec = {"wiki_file": wiki_file, "source_file": source_file, "page": page,
               "visual_kinds": cls.get("visual_kinds") or [],
               "signals": cls.get("signals") or {},
               "visual_candidate": not inventory_only,
               "inventory_only": inventory_only,
               "anchor_inferred": inferred,
               "asset_paths": usable,
               "declared_assets": refs(declared)}
        if (source_file, page) in answer_pages:
            generated = [a for a in declared if a.get("origin") == "generated"]
            manual = [a for a in declared if a.get("origin") != "generated"]
            manual += manual_block_assets.get(key, [])
            is_shared = (source_file, page) in shared_pages
            rec.update({
                "status": "shared_prompt_answer" if is_shared else "deferred_answer",
                "reason": ("shared_prompt_answer_page_deferred_from_wiki" if is_shared
                           else "answer_only_page_deferred_from_wiki"),
                "generated_assets": refs(generated),
                "manual_assets": refs(manual),
                "manual_usable_assets": refs(manual, usable_only=True),
            })
            issues = []
            if is_shared and (source_file, page) in shared_blocker_pages:
                rec["blocker"] = "audited_question_side_crop_required"
                issues.append("shared_prompt_answer_page")
            # A machine marker establishes ownership and is removable by --apply-wiki.
            # Unmarked/legacy markup may be user-authored, so retain and fail closed.
            if rec["manual_assets"]:
                rec["coverage_issue"] = "manual_answer_exposure"
                issues.append("manual_answer_exposure")
            if issues:
                rec["coverage_issues"] = issues
        else:
            rec["status"] = "embedded" if usable else "missing"
            if not usable:
                default_reason = "wiki_source_unmapped" if wiki_file is None \
                    else "source_page_anchor_missing" if inferred else "not_embedded"
                rec["reason"] = reasons.get(key, default_reason)
        pages.append(rec)

    deferred = [p for p in pages if p["status"] == "deferred_answer"]
    shared = [p for p in pages if p["status"] == "shared_prompt_answer"]
    eligible = [p for p in pages if p["status"] in ("embedded", "missing")]
    missing = [p for p in eligible if p["status"] == "missing"]
    embedded = [p for p in eligible if p["status"] == "embedded"]
    manual_exposures = [p for p in deferred + shared
                        if p.get("coverage_issue") == "manual_answer_exposure"]
    shared_blockers = [p for p in shared if p.get("blocker")]
    per_chapter = {}
    for p in pages:
        chapter_key = p["wiki_file"] if p["wiki_file"] is not None else "__unmapped__"
        c = per_chapter.setdefault(
            chapter_key, {"detected": 0, "embedded": 0, "missing": 0,
                          "deferred_answer": 0, "shared_prompt_answer": 0,
                          "total_visual_pages": 0})
        c["total_visual_pages"] += 1
        if p["status"] == "deferred_answer":
            c["deferred_answer"] += 1
        elif p["status"] == "shared_prompt_answer":
            c["shared_prompt_answer"] += 1
        else:
            c["detected"] += 1
            c[p["status"]] += 1
    if emit_warnings:
        for fn, counts in sorted(per_chapter.items()):
            if counts["missing"]:
                target_fn = None if fn == "__unmapped__" else fn
                refs0 = ["%s p.%d" % (p["source_file"], p["page"])
                         for p in missing if p["wiki_file"] == target_fn]
                local_warnings.append("wiki_visual_missing: %s 有 %d/%d 个已检测视觉页未嵌入：%s"
                                      % (fn, counts["missing"], counts["detected"], "、".join(refs0)))
        for rec in manual_exposures:
            local_warnings.append(
                "wiki_answer_manual_exposure: %s %s p.%d 含手工/旧式图片 %s；解答相关页不会计入 "
                "wiki missing，且 --apply-wiki 不会静默删除，请人工移除后重跑"
                % (rec.get("wiki_file") or "__unmapped__", rec["source_file"], rec["page"],
                   "、".join(rec.get("manual_assets") or ["(unknown)"])))
        for rec in shared:
            local_warnings.append(
                "%s: %s %s p.%d 同时是题面页和答案页；整页不会回挂 wiki%s"
                % ("shared_prompt_answer_page" if rec.get("blocker")
                   else "wiki_shared_prompt_answer_deferred",
                   rec.get("wiki_file") or "__unmapped__", rec["source_file"], rec["page"],
                   "，须提供经审核的独立题面裁剪图" if rec.get("blocker") else ""))
    coverage = {
        # Legacy-compatible coverage equation; solution-sensitive pages have separate buckets.
        "detected": len(eligible), "embedded": len(embedded), "missing": len(missing),
        "total_visual_pages": len(pages),
        "deferred_answer_count": len(deferred),
        "deferred_answer_pages": deferred,
        "shared_prompt_answer_count": len(shared),
        "shared_prompt_answer_pages": shared,
        "shared_prompt_answer_blocker_count": len(shared_blockers),
        "shared_prompt_answer_blocker_pages": shared_blockers,
        "inventory_only_answer_page_count": sum(int(p.get("inventory_only"))
                                                for p in deferred + shared),
        "manual_answer_exposure_count": len(manual_exposures),
        "manual_answer_exposure_pages": manual_exposures,
        "per_chapter": per_chapter, "pages": pages, "missing_pages": missing,
        "warnings": local_warnings,
    }
    if warnings is not None:
        warnings.extend(local_warnings)
    return coverage


def _material_pdf(materials, source_file, warnings, label):
    """Resolve an indexed provenance path under materials without accepting traversal/URL/ambiguity."""
    sf = str(source_file or "").replace("\\", "/")
    if os.path.isabs(sf) or re.match(r"^[A-Za-z]:", sf) or "://" in sf or ".." in sf.split("/"):
        warnings.append("wiki_apply_unsafe_source: %s (%s)" % (label, sf))
        return None
    exact = os.path.join(materials, *sf.split("/"))
    if os.path.isfile(exact):
        candidate = exact
    elif "/" not in sf:
        candidates = []
        for base, dirs, files in os.walk(materials):
            for d in list(dirs):
                full = os.path.join(base, d)
                if d in ALWAYS_PRUNE or _is_leftover_workspace(full, d) or _is_workspace_root(full):
                    dirs.remove(d)
            if sf in files:
                candidates.append(os.path.join(base, sf))
        if len(candidates) != 1:
            warnings.append("wiki_apply_%s_source: %s (%s)" %
                            ("ambiguous" if candidates else "missing", label, sf))
            return None
        candidate = candidates[0]
    else:
        warnings.append("wiki_apply_missing_source: %s (%s)" % (label, sf))
        return None
    mat_real, real = os.path.realpath(materials), os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([os.path.normcase(real), os.path.normcase(mat_real)]) == os.path.normcase(mat_real)
    except ValueError:
        inside = False
    if not inside:
        warnings.append("wiki_apply_outside_materials: %s (%s)" % (label, sf))
        return None
    return candidate


def _insert_wiki_visual(wiki_path, source_file, page, asset_full):
    """Insert one generated image at its page anchor, synthesizing a missing anchor when needed."""
    with open(wiki_path, encoding="utf-8") as f:
        text0 = f.read()
    marker = "<!-- wiki-visual-index: %s p.%d -->" % (source_file, page)
    if marker in text0:
        return False
    marks = list(_WIKI_PAGE_RE.finditer(text0))
    chosen = None
    same_source = []
    for mark in marks:
        sf = mark.group(1).strip().replace("\\", "/")
        matches_source = sf == source_file or ("/" not in sf and os.path.basename(source_file) == sf)
        if matches_source:
            same_source.append(mark)
        if int(mark.group(2)) == page and matches_source:
            chosen = mark
            break
    if chosen is None and not same_source:
        return False
    alt_source = source_file.replace("]", "_").replace("\n", " ").replace("\r", " ")
    link = _canonical_rel_posix(os.path.dirname(wiki_path), asset_full)
    image_block = "%s\n![原页图示 %s p.%d](%s)" % (marker, alt_source, page, link)
    if chosen is not None:
        insertion = chosen.end()
        addition = "\n\n%s\n" % image_block
    else:
        # Older builders skipped empty-text pages.  Insert an explicit provenance anchor before the
        # next page of the same source (or after the last source block) so the repair is stable and
        # the next coverage pass observes an exact page-local anchor.
        later = [m for m in same_source if int(m.group(2)) > page]
        if later:
            insertion = min(later, key=lambda m: int(m.group(2))).start()
        else:
            last = max(same_source, key=lambda m: m.start())
            following = [m for m in marks if m.start() > last.start()]
            insertion = min(following, key=lambda m: m.start()).start() if following else len(text0)
        source_anchor = "<!-- %s p.%d -->" % (alt_source, page)
        addition = "\n\n%s\n%s\n\n" % (source_anchor, image_block)
    text1 = text0[:insertion] + addition + text0[insertion:]
    _atomic_write_bytes(wiki_path, text1.encode("utf-8"))
    return True


def _remove_generated_answer_visuals(ws, fig_files, solution_sensitive_pages, warnings):
    """Remove machine-owned wiki blocks for answer-only/shared pages, atomically per file.

    A ``wiki-visual-index`` marker is the ownership proof.  Unmarked Markdown images are never
    touched here; coverage reports them as manual answer exposures instead.
    """
    wiki_dir = os.path.join(ws, "references", "wiki")
    if not os.path.isdir(wiki_dir) or not solution_sensitive_pages:
        return 0
    plans, removed = [], []
    for fn in sorted(name for name in os.listdir(wiki_dir) if name.lower().endswith(".md")):
        path, error = _safe_wiki_file(ws, fn)
        if error:
            _die("--apply-wiki 拒绝不安全的 references/wiki/%s：%s" % (fn, error))
        try:
            with open(path, encoding="utf-8") as source:
                text0 = source.read()
        except (OSError, UnicodeDecodeError) as exc:
            _die("--apply-wiki 无法读取 references/wiki/%s：%s" % (fn, exc))

        def replace(match):
            resolved = _resolve_fig_file(fig_files, match.group(1))
            page = int(match.group(2))
            if resolved is not None and (resolved, page) in solution_sensitive_pages:
                removed.append((fn, resolved, page))
                return ""
            return match.group(0)

        text1 = _WIKI_MARKED_IMAGE_RE.sub(replace, text0)
        if text1 != text0:
            plans.append((path, text1))
    for path, text in plans:
        _atomic_write_bytes(path, text.encode("utf-8"))
    if removed:
        refs = ["%s %s p.%d" % row for row in removed]
        warnings.append("wiki_answer_generated_removed: 已移除 %d 个答案专属页自动回挂块：%s"
                        % (len(removed), "、".join(refs)))
    return len(removed)


def apply_wiki_visuals(ws, materials, coverage, backend, asset_root, warnings, per_chapter_cap=30,
                       fig_files=None, solution_sensitive_pages=None):
    """Repair prompt/wiki visuals and remove generated answer-only blocks.

    Returns ``(inserted, missing_reason_map, removed_answer_blocks)``.
    """
    wiki_dir = os.path.join(ws, "references", "wiki")
    if _path_has_symlink_component(ws, wiki_dir):
        _die("--apply-wiki 拒绝 references/wiki 的符号链接或符号链接父级")
    if os.path.isdir(wiki_dir):
        linked = [fn for fn in os.listdir(wiki_dir)
                  if fn.lower().endswith(".md") and os.path.islink(os.path.join(wiki_dir, fn))]
        if linked:
            _die("--apply-wiki 拒绝符号链接 wiki 文件: %s" % ", ".join(sorted(linked)))
    removed_answer_blocks = _remove_generated_answer_visuals(
        ws, fig_files or {}, set(solution_sensitive_pages or ()), warnings)
    missing = coverage.get("missing_pages") or []
    if not missing:
        return 0, {}, removed_answer_blocks
    if not backend.can_render():
        _die("--apply-wiki 需要渲染后端（pip install pymupdf，或 pypdfium2+Pillow）", 3)
    # Validate every mutable wiki path before rendering or writing even one asset.  In particular,
    # references/wiki (or references itself) may be a parent symlink: a basename-only check on the
    # final .md file would still follow that parent outside the workspace.
    safe_wiki_paths = {}
    for wiki_file in sorted({rec.get("wiki_file") for rec in missing if rec.get("wiki_file")}):
        safe_path, error = _safe_wiki_file(ws, wiki_file)
        if error:
            _die("--apply-wiki 拒绝不安全的 references/wiki/%s：%s" % (wiki_file, error))
        safe_wiki_paths[wiki_file] = safe_path
    ws_real, root_real = os.path.realpath(ws), os.path.realpath(asset_root)
    try:
        inside = os.path.commonpath([os.path.normcase(root_real), os.path.normcase(ws_real)]) == os.path.normcase(ws_real)
    except ValueError:
        inside = False
    if not inside:
        _die("--asset-root 必须真实位于工作区内（--apply-wiki 不写到外部）: %s" % asset_root)
    os.makedirs(root_real, exist_ok=True)
    by_chapter = {}
    for rec in missing:
        by_chapter.setdefault(rec["wiki_file"], []).append(rec)
    reasons, inserted = {}, 0
    for wiki_file, records in sorted(by_chapter.items(), key=lambda item: str(item[0] or "")):
        if wiki_file is None:
            for rec in records:
                key = (None, rec["source_file"], rec["page"])
                reasons[key] = "wiki_source_unmapped"
            warnings.append("wiki_apply_unmapped: %d 个视觉页没有任何 wiki 来源声明，无法安全回挂"
                            % len(records))
            continue
        embedded_now = (coverage.get("per_chapter", {}).get(wiki_file, {}).get("embedded") or 0)
        budget = max(0, int(per_chapter_cap) - int(embedded_now))
        records = sorted(records, key=lambda r: (r["source_file"], r["page"]))
        selected, capped = records[:budget], records[budget:]
        if capped:
            refs = ["%s p.%d" % (r["source_file"], r["page"]) for r in capped]
            warnings.append("wiki_visual_cap: %s 每章上限 %d，以下 %d 页未回挂：%s"
                            % (wiki_file, per_chapter_cap, len(capped), "、".join(refs)))
            for r in capped:
                reasons[(wiki_file, r["source_file"], r["page"])] = "chapter_cap"
        wiki_path = safe_wiki_paths[wiki_file]
        for rec in selected:
            key = (wiki_file, rec["source_file"], rec["page"])
            label = "%s %s p.%d" % (wiki_file, rec["source_file"], rec["page"])
            digest = hashlib.sha1(rec["source_file"].encode("utf-8")).hexdigest()[:10]
            name = "wiki_%s_p%04d.png" % (digest, rec["page"])
            full = os.path.join(root_real, name)
            if not (os.path.isfile(full) and os.access(full, os.R_OK)):
                pdf = _material_pdf(materials, rec["source_file"], warnings, label)
                if pdf is None:
                    reasons[key] = "source_unavailable"
                    continue
                try:
                    png = backend.render_page_png(pdf, rec["page"] - 1)
                except Exception as e:
                    warnings.append("wiki_apply_render_failed: %s (%s)" % (label, e))
                    png = None
                if not png:
                    reasons[key] = "render_failed"
                    continue
                _atomic_write_bytes(full, png)
            try:
                changed = _insert_wiki_visual(wiki_path, rec["source_file"], rec["page"], full)
            except (OSError, UnicodeDecodeError) as e:
                warnings.append("wiki_apply_write_failed: %s (%s)" % (label, e))
                reasons[key] = "wiki_write_failed"
                continue
            if changed:
                inserted += 1
            elif not os.path.isfile(wiki_path):
                reasons[key] = "wiki_missing"
    return inserted, reasons, removed_answer_blocks


def run(argv=None, backend=None, _state_locked=False):
    ap = argparse.ArgumentParser(description="Build the generic dual visual index (recall-first; pure stdlib + optional PDF backend; no LLM/network).")
    ap.add_argument("--workspace", required=True, help="cram workspace (contains references/quiz_bank.json)")
    ap.add_argument("--materials", default=None, help="course materials folder (scan PDFs to build figure_page_index)")
    ap.add_argument("--out-dir", default=None, help="index output dir (default <workspace>/references/)")
    ap.add_argument("--apply", action="store_true",
                    help="repair prompt suspects as question_context and answer suspects as answer_context")
    ap.add_argument("--apply-wiki", action="store_true",
                    help="idempotently render detected visual pages and insert them at matching wiki page anchors")
    ap.add_argument("--wiki-page-cap", type=int, default=30,
                    help="maximum embedded visual pages per wiki chapter for --apply-wiki (default 30)")
    ap.add_argument("--asset-root", default=None,
                    help="screenshot dir for --apply/--apply-wiki (default <workspace>/references/assets)")
    args = ap.parse_args(argv)
    if args.wiki_page_cap < 1:
        _die("--wiki-page-cap 必须是 >=1 的整数")

    ws = os.path.abspath(args.workspace)
    if not os.path.isdir(ws) or os.path.islink(ws):
        _die("--workspace 必须是现有的非符号链接目录: %s" % ws)
    if not _state_locked:
        with workspace_publication_lock(ws):
            return run(argv, backend=backend, _state_locked=True)
    references_dir = _safe_workspace_dir(ws, os.path.join(ws, "references"),
                                          "references", create=False)
    bank_path = _safe_workspace_file(
        ws, os.path.join(references_dir, "quiz_bank.json"), "quiz_bank.json")
    bank = _read_json(bank_path, "quiz_bank.json")
    if not isinstance(bank, list):
        _die("quiz_bank.json 必须是数组")
    teaching_path = os.path.join(references_dir, "teaching_examples.json")
    teaching = []
    teaching_exists = os.path.lexists(teaching_path)
    if teaching_exists:
        _safe_workspace_file(ws, teaching_path, "teaching_examples.json")
        teaching = _read_json(teaching_path, "teaching_examples.json")
        if not isinstance(teaching, list):
            _die("teaching_examples.json 必须是数组")
    out_dir = _safe_workspace_dir(
        ws, args.out_dir or references_dir, "--out-dir", create=True)
    warnings = []
    backend = backend or RealBackend()

    fig_files = {}
    if args.materials:
        if not os.path.isdir(args.materials):
            _die("找不到材料目录: %s" % args.materials)
        fig_files = scan_materials(args.materials, backend, warnings)
    else:
        warnings.append("no_materials: 未给 --materials——只建题目索引，无法交叉核对疑漏（召回网关闭）")

    prompt_pages, answer_pages = derive_item_page_sides(fig_files, bank, teaching)
    shared_pages = prompt_pages & answer_pages
    questions, per_chapter, bank_prompt_suspects, bank_answer_suspects = \
        build_question_index(ws, bank, fig_files, warnings, "quiz_bank", shared_pages)
    teaching_questions, teaching_per_chapter, teaching_prompt_suspects, teaching_answer_suspects = \
        build_question_index(ws, teaching, fig_files, warnings, "teaching_examples", shared_pages)
    prompt_suspects = bank_prompt_suspects + teaching_prompt_suspects
    answer_suspects = bank_answer_suspects + teaching_answer_suspects
    shared_blockers = [s for s in prompt_suspects
                       if s.get("blocker") == "shared_prompt_answer_page"]
    shared_blocker_pages = {
        (row["source_file"], row["page"])
        for suspect in shared_blockers
        for row in (suspect.get("shared_prompt_answer_pages") or [])
        if isinstance(row, dict) and row.get("source_file") and type(row.get("page")) is int}
    # Build once without gap warnings so a successful --apply-wiki run does not retain a stale pre-repair warning.
    coverage = build_wiki_visual_coverage(
        ws, fig_files, emit_warnings=False,
        prompt_pages=prompt_pages, answer_pages=answer_pages,
        shared_blocker_pages=shared_blocker_pages)

    asset_root = args.asset_root or os.path.join(references_dir, "assets")
    if args.apply or args.apply_wiki:
        asset_root = _safe_workspace_dir(ws, asset_root, "--asset-root", create=True)
    bank_prompt_applied = bank_answer_applied = 0
    teaching_prompt_applied = teaching_answer_applied = 0
    if args.apply and (prompt_suspects or answer_suspects):
        if bank_prompt_suspects:
            bank_prompt_applied = apply_suspects(ws, args.materials or "", bank,
                                                 bank_prompt_suspects, backend, asset_root, warnings)
        if bank_answer_suspects:
            bank_answer_applied = apply_answer_suspects(ws, args.materials or "", bank,
                                                        bank_answer_suspects, backend, asset_root, warnings)
        if teaching_prompt_suspects:
            teaching_prompt_applied = apply_suspects(ws, args.materials or "", teaching,
                                                     teaching_prompt_suspects, backend, asset_root, warnings)
        if teaching_answer_suspects:
            teaching_answer_applied = apply_answer_suspects(
                ws, args.materials or "", teaching, teaching_answer_suspects,
                backend, asset_root, warnings)
        if bank_prompt_applied or bank_answer_applied:
            _write_json_with_backup(bank_path, bank)
        if teaching_prompt_applied or teaching_answer_applied:
            _write_json_with_backup(teaching_path, teaching)
        if (bank_prompt_applied or bank_answer_applied
                or teaching_prompt_applied or teaching_answer_applied):
            questions, per_chapter, bank_prompt_suspects, bank_answer_suspects = \
                build_question_index(ws, bank, fig_files, source_layer="quiz_bank",
                                     shared_prompt_answer_pages=shared_pages)
            teaching_questions, teaching_per_chapter, teaching_prompt_suspects, \
                teaching_answer_suspects = build_question_index(
                    ws, teaching, fig_files, source_layer="teaching_examples",
                    shared_prompt_answer_pages=shared_pages)
            prompt_suspects = bank_prompt_suspects + teaching_prompt_suspects
            answer_suspects = bank_answer_suspects + teaching_answer_suspects

    shared_blockers = [s for s in prompt_suspects
                       if s.get("blocker") == "shared_prompt_answer_page"]
    shared_blocker_pages = {
        (row["source_file"], row["page"])
        for suspect in shared_blockers
        for row in (suspect.get("shared_prompt_answer_pages") or [])
        if isinstance(row, dict) and row.get("source_file") and type(row.get("page")) is int}

    prompt_applied = bank_prompt_applied + teaching_prompt_applied
    answer_applied = bank_answer_applied + teaching_answer_applied

    wiki_applied, wiki_deferred_removed, wiki_reasons, wiki_apply_warnings = 0, 0, {}, []
    if args.apply_wiki:
        if not args.materials:
            _die("--apply-wiki 必须同时给 --materials，才能从已索引原 PDF 渲染页面")
        start = len(warnings)
        wiki_applied, wiki_reasons, wiki_deferred_removed = apply_wiki_visuals(
            ws, args.materials, coverage, backend, asset_root, warnings, args.wiki_page_cap,
            fig_files=fig_files, solution_sensitive_pages=answer_pages)
        wiki_apply_warnings = warnings[start:]
    coverage = build_wiki_visual_coverage(ws, fig_files, warnings=warnings,
                                          missing_reasons=wiki_reasons, emit_warnings=True,
                                          prompt_pages=prompt_pages, answer_pages=answer_pages,
                                          shared_blocker_pages=shared_blocker_pages)
    coverage["removed_deferred_answer_blocks"] = wiki_deferred_removed
    if wiki_apply_warnings:
        coverage["warnings"] = wiki_apply_warnings + coverage["warnings"]

    # Snapshot after every optional repair so hashes describe the exact bank/teaching/wiki content
    # summarized by the indices, not their pre-apply state.
    integrity = _integrity_snapshot(ws, args, backend, warnings,
                                    item_layers=(bank, teaching), coverage=coverage,
                                    fig_files=fig_files)

    fig_index = {
        "generated_by": "build_visual_index.py",
        "media_signals": backend.can_media(),
        "note": "确定性启发式（结构/排版/词面分层，召回优先）；不是语义判定，AI 识图为未来 opt-in",
        "files": {rel: {"pages": info["pages"],
                        "visual_pages": [{"page": p, **cls} for p, cls in sorted(info["visual"].items())]}
                  for rel, info in sorted(fig_files.items())},
        "wiki_visual_coverage": coverage,
        "shared_prompt_answer_count": coverage["shared_prompt_answer_count"],
        "shared_prompt_answer_blocker_count": coverage["shared_prompt_answer_blocker_count"],
        "manual_answer_exposure_count": coverage["manual_answer_exposure_count"],
        "warnings": warnings,
    }
    shared_page_rows = [{"source_file": source, "page": page}
                        for source, page in sorted(shared_pages)]
    q_index = {
        "generated_by": "build_visual_index.py",
        "questions": questions, "per_chapter": per_chapter,
        "teaching_questions": teaching_questions,
        "teaching_per_chapter": teaching_per_chapter,
        "combined_per_chapter": _merge_per_chapter(per_chapter, teaching_per_chapter),
        "suspects": prompt_suspects,                 # backward-compatible prompt-side alias
        "prompt_suspects": prompt_suspects, "answer_suspects": answer_suspects,
        "applied": bank_prompt_applied,              # backward-compatible quiz-bank prompt count
        "prompt_applied": prompt_applied, "answer_applied": answer_applied,
        "bank_prompt_applied": bank_prompt_applied,
        "bank_answer_applied": bank_answer_applied,
        "teaching_prompt_applied": teaching_prompt_applied,
        "teaching_answer_applied": teaching_answer_applied,
        "wiki_applied": wiki_applied,
        "wiki_deferred_answer_blocks_removed": wiki_deferred_removed,
        "shared_prompt_answer_pages": shared_page_rows,
        "shared_prompt_answer_count": len(shared_page_rows),
        "shared_prompt_answer_blockers": shared_blockers,
        "shared_prompt_answer_blocker_count": len(shared_blockers),
        "manual_answer_exposure_count": coverage["manual_answer_exposure_count"],
        "warnings": warnings,
    }
    # Bind the DERIVED findings as well as their inputs.  Otherwise changing only
    # prompt_suspects/answer_suspects/coverage to false zeros would leave every input hash valid.
    integrity["outputs"] = {
        "figure_page_index.json": {"sha256": _manifest_result_sha256(fig_index)},
        "image_question_index.json": {"sha256": _manifest_result_sha256(q_index)},
    }
    fig_index["integrity"] = integrity
    q_index["integrity"] = integrity
    _atomic_write_bytes(os.path.join(out_dir, "figure_page_index.json"),
                        (json.dumps(fig_index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    _atomic_write_bytes(os.path.join(out_dir, "image_question_index.json"),
                        (json.dumps(q_index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    n_vis = sum(len(i["visual"]) for i in fig_files.values())
    print("[+] figure_page_index: %d 个文件 / %d 个视觉页 / wiki %d/%d + deferred-answer %d"
          " + shared-prompt-answer %d；"
          "image_question_index: "
          "%d 题 / 题面疑漏 %d / 答案疑漏 %d / 回写 %d+%d"
          % (len(fig_files), n_vis, coverage["embedded"], coverage["detected"],
             coverage["deferred_answer_count"], coverage["shared_prompt_answer_count"],
             len(questions) + len(teaching_questions),
             len(prompt_suspects), len(answer_suspects), prompt_applied, answer_applied))
    for w in warnings:
        print("[!] " + w)
    if prompt_suspects and not args.apply:
        print("[!] 有 %d 道疑似漏标的图依赖题（详见 image_question_index.json 的 suspects）；"
              "用 --apply 渲染原页并标 maybe_requires_assets" % len(prompt_suspects))
    if answer_suspects and not args.apply:
        print("[!] 有 %d 道答案侧视觉疑漏（详见 answer_suspects）；用 --apply 追加 answer_context"
              % len(answer_suspects))
    if coverage["missing"] and not args.apply_wiki:
        print("[!] wiki 有 %d/%d 个已检测视觉页未嵌入；用 --apply-wiki 回挂原页图"
              % (coverage["missing"], coverage["detected"]))
    if coverage["manual_answer_exposure_count"]:
        print("[!] wiki 检出 %d 个解答相关页手工图片暴露；已保留原文并写入 coverage issue，"
              "本次 fail-closed 返回 2" % coverage["manual_answer_exposure_count"])
        return 2
    if shared_blockers:
        print("[!] 有 %d 道题的题面页与答案页重合，未找到经审核的独立题面裁剪图；"
              "已阻止整页自动回写，本次 fail-closed 返回 2" % len(shared_blockers))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
