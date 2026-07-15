#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Optional-dependency preflight (v4) — the ONE machine-readable manifest of everything the
skill may need beyond the stdlib, probed BEFORE it is needed, never discovered by a runtime crash.

Why: the CORE pipeline is pure stdlib by design, but four capabilities have optional backends —
PDF text extraction (pypdf or PyMuPDF), PDF page rendering for the visual index
(PyMuPDF or pypdfium2 + Pillow), audited LaTeX-to-MathML conversion, and printable-PDF output (a LOCAL Edge/Chrome,
not a pip package). Students used
to hit these as mid-ingest errors; agents are instructed (exam-ingest Workflow step 0) to run THIS
tool at setup, show the student what is missing and why, and offer the exact install commands —
installing is the agent's action WITH the student's one-line consent, never a silent side effect.

    python scripts/check_deps.py                          # show manifest; no input means no hard need
    python scripts/check_deps.py --materials <dir>        # scan materials → mark ingest needs
    python scripts/check_deps.py --workspace <ws> --chapter 1 --artifact-mode visual \
        --pdf-backend <native|browser|html>             # selected chapter + resolved route
    python scripts/check_deps.py --json                   # machine manifest for agents
Exit: 0 = nothing NEEDED is missing · 5 = a NEEDED dependency is missing ·
2 = usage or an input/content probe error (never reclassified as a missing dependency)
"""
import argparse
import importlib.util
import json
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdf_capabilities import (  # noqa: E402
    PDF_RENDER_CANDIDATES,
    PDF_TEXT_CANDIDATES,
    dependency_candidates,
)
from readiness import chapter_math_readiness  # noqa: E402


# ---- the manifest（唯一事实源；技能文本只指向本工具，不复制清单内容——防两处漂移）----
# candidates: ((import_name, ...), display packages)。同一候选里的 imports 必须全部可用
# （例如 pypdfium2 + Pillow），group 内任一完整候选可用即满足。这个结构与
# build_raw_input_from_workspace.detect_backend() 的真实能力保持一一对应。
LATEX2MATHML_VERSION = "3.60.0"
LATEX2MATHML_PIN = "latex2mathml==%s" % LATEX2MATHML_VERSION

GROUPS = (
    {"id": "pdf_text",
     "candidates": dependency_candidates(PDF_TEXT_CANDIDATES),
     "install": "pip install pymupdf",
     "needed_when": "materials_have_pdf",
     "purpose_zh": "读取 PDF 课件/试卷文本（ingest 建库）",
     "purpose_en": "extract text from PDF materials (ingest)"},
    {"id": "pdf_render",
     "candidates": dependency_candidates(PDF_RENDER_CANDIDATES),
     "install": "pip install pymupdf",
     "needed_when": "materials_have_pdf",
     "purpose_zh": "渲染 PDF 页面图（视觉索引/题面图，缺失时仅文字信号、召回打折）",
     "purpose_en": "render PDF pages for the visual index (text-only signals without it)"},
    {"id": "browser",
     "candidates": (),                      # 系统依赖，非 pip
     "needed_when": "cheatsheet_pdf",
     "purpose_zh": "考前小抄的打印级 PDF 输出（缺失时降级为 HTML + 手动打印，功能不缺失）",
     "purpose_en": "print-grade cheatsheet PDF (degrades to HTML + manual print)"},
    {"id": "mathml",
     "candidates": ((("latex2mathml",), LATEX2MATHML_PIN),),
     "install": "pip install %s" % LATEX2MATHML_PIN,
     "required_version": LATEX2MATHML_VERSION,
     "needed_when": "visual_artifacts",
     "purpose_zh": "把标准 LaTeX 公式离线转换为浏览器/PDF 可读的原生 MathML",
     "purpose_en": "convert standard LaTeX formulas to offline native MathML for HTML/PDF"},
)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_BLOCK_DOLLAR_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.S)
_INLINE_DOLLAR_RE = re.compile(r"(?<![\\$])\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


class DependencyProbeError(Exception):
    """The dependency *need* could not be determined from the requested local inputs."""


def _probe_import(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception as exc:
        raise DependencyProbeError("Python 模块 %s 探测失败：%s" % (name, exc))


def _normalise_distribution_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def installed_distribution_version(distribution_name):
    """Read installed package metadata without importing third-party package code.

    Python 3.8+ supplies importlib.metadata.  The small dist-info fallback keeps the skill's
    advertised Python 3.7 support without making importlib-metadata/setuptools a hidden dependency.
    """
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - exercised only on Python 3.7
        metadata = None
    if metadata is not None:
        try:
            return metadata.version(distribution_name)
        except metadata.PackageNotFoundError:
            return None
        except (OSError, ValueError):
            pass

    target = _normalise_distribution_name(distribution_name)
    for root in dict.fromkeys(sys.path):
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith(".dist-info"):
                continue
            stem = entry[:-10]
            if not _normalise_distribution_name(stem).startswith(target + "-"):
                continue
            metadata_path = os.path.join(root, entry, "METADATA")
            try:
                with open(metadata_path, "r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        if line.startswith("Version:"):
                            return line.split(":", 1)[1].strip() or None
            except OSError:
                continue
    return None


def _probe_browser():
    try:
        import cheatsheet_render
        return cheatsheet_render.find_browser() is not None
    except Exception as exc:
        raise DependencyProbeError("本地浏览器探测失败：%s" % exc)


def _materials_have_pdf(materials):
    if materials is None:
        return None                          # 未给材料目录 → 不可判
    if not isinstance(materials, str) or not materials.strip():
        raise DependencyProbeError("--materials 必须是非空目录路径")
    path = os.path.abspath(materials)
    if not os.path.isdir(path):
        raise DependencyProbeError("materials 目录不存在或不可读：%s" % materials)
    try:
        for _dirpath, _dirs, files in os.walk(path, onerror=lambda exc: (_ for _ in ()).throw(exc)):
            if any(f.lower().endswith(".pdf") for f in files):
                return True
    except OSError as exc:
        raise DependencyProbeError("materials 目录无法扫描：%s" % exc)
    return False


def _read_utf8(path, label):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise DependencyProbeError("%s 无法作为 UTF-8 文本读取：%s" % (label, exc))


def _reject_json_constant(value):
    raise ValueError("non-standard JSON constant %s" % value)


def _read_json_array(path, label, optional=False):
    if not os.path.exists(path):
        if optional:
            return []
        raise DependencyProbeError("缺少 %s" % label)
    if os.path.islink(path) or not os.path.isfile(path):
        raise DependencyProbeError("%s 必须是工作区内的普通文件" % label)
    raw = _read_utf8(path, label)
    try:
        data = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError) as exc:
        raise DependencyProbeError("%s 不是合法 JSON：%s" % (label, exc))
    if not isinstance(data, list):
        raise DependencyProbeError("%s 顶层必须是 JSON 数组" % label)
    return data


def _without_markdown_code(text):
    """Remove Markdown code regions because the renderer never converts dollars inside them."""
    output = []
    fence = None
    for line in text.splitlines(True):
        match = _FENCE_RE.match(line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1]:
                fence = None
            output.append("\n")
        elif fence is None:
            output.append(_INLINE_CODE_RE.sub("", line))
        else:
            output.append("\n")
    return "".join(output)


def _contains_standard_math(value):
    if isinstance(value, str):
        visible = _without_markdown_code(value)
        return bool(_BLOCK_DOLLAR_RE.search(visible) or _INLINE_DOLLAR_RE.search(visible))
    if isinstance(value, list):
        return any(_contains_standard_math(item) for item in value)
    if isinstance(value, dict):
        # MarkdownRenderer serializes dictionaries with json.dumps, so string keys are visible too.
        return any(_contains_standard_math(key) or _contains_standard_math(item)
                   for key, item in value.items())
    return False


def _has_displayable_answer(value):
    if value in (None, "", [], {}):
        return False
    return not (isinstance(value, str) and not value.strip())


def _chapter_matches(item, chapter):
    if not isinstance(item, dict):
        return False
    wanted = str(chapter)
    return any(item.get(key) is not None and str(item.get(key)) == wanted
               for key in ("chapter", "phase"))


def _guard_workspace(workspace):
    if not isinstance(workspace, str) or not workspace.strip():
        raise DependencyProbeError("--workspace 必须是非空目录路径")
    path = os.path.abspath(workspace)
    if os.path.islink(path):
        raise DependencyProbeError("workspace 不得是符号链接：%s" % workspace)
    if not os.path.isdir(path):
        raise DependencyProbeError("workspace 不存在或不是目录：%s" % workspace)
    return path


def _reject_symlink_components(workspace, path, label):
    try:
        relative = os.path.relpath(os.path.abspath(path), os.path.abspath(workspace))
    except ValueError:
        raise DependencyProbeError("%s 与 workspace 不在同一文件系统" % label)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise DependencyProbeError("%s 逃出 workspace" % label)
    current = workspace
    for part in (() if relative == "." else relative.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise DependencyProbeError("%s 含符号链接路径组件：%s" % (label, part))


def _guard_workspace_file(workspace, path, label, optional=False):
    real_ws = os.path.realpath(workspace)
    real_path = os.path.realpath(path)
    try:
        contained = os.path.commonpath((real_ws, real_path)) == real_ws
    except ValueError:
        contained = False
    if not contained:
        raise DependencyProbeError("%s 经路径解析逃出 workspace" % label)
    if not os.path.lexists(path):
        if optional:
            return None
        raise DependencyProbeError("缺少 %s" % label)
    _reject_symlink_components(workspace, path, label)
    if not os.path.isfile(path):
        raise DependencyProbeError("%s 必须是 workspace 内的普通文件" % label)
    return path


def _selected_chapter_has_math(workspace, chapter):
    """Compatibility boolean backed by the validated typed guide.

    Recovery is not equivalent to ``False``: callers that need the tri-state
    contract must use :func:`_selected_chapter_math_report`.
    """
    report = _selected_chapter_math_report(workspace, chapter)
    if report["status"] == "needs_recovery":
        raise DependencyProbeError(
            "第 %d 章公式状态需要恢复：%s" % (
                int(chapter), ", ".join(report["reason_codes"]) or "unknown"
            )
        )
    return report["status"] == "standard"


def _selected_chapter_math_report(workspace, chapter):
    """Inspect formula/substitution fields from a validated chNN.guide.json."""
    ws = _guard_workspace(workspace)
    if isinstance(chapter, bool):
        raise DependencyProbeError("--chapter 必须是正整数")
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        raise DependencyProbeError("--chapter 必须是正整数")
    if chapter < 1:
        raise DependencyProbeError("--chapter 必须是正整数")

    try:
        return chapter_math_readiness(ws, chapter)
    except Exception as exc:
        raise DependencyProbeError("第 %d 章公式状态探测失败：%s" % (chapter, exc))


def build_report(materials=None, artifact_mode="chat", workspace=None, chapter=None,
                 pdf_backend="auto"):
    if artifact_mode not in ("chat", "visual"):
        raise ValueError("artifact_mode must be chat or visual")
    if pdf_backend not in ("auto", "browser", "native", "html"):
        raise ValueError("pdf_backend must be auto, browser, native, or html")
    probe_errors = []
    backend_probe_error = None
    materials_probe_error = None
    try:
        has_pdf = _materials_have_pdf(materials)
    except DependencyProbeError as exc:
        has_pdf = None
        materials_probe_error = str(exc)
        probe_errors.append(materials_probe_error)
    chapter_has_math = None
    chapter_math_status = None
    chapter_math_reasons = []
    chapter_math_counts = {}
    chapter_probe_error = None
    if workspace is not None or chapter is not None:
        if workspace is None or chapter is None:
            chapter_probe_error = "--workspace 与 --chapter 必须同时提供"
            probe_errors.append(chapter_probe_error)
        else:
            try:
                math_report = _selected_chapter_math_report(workspace, chapter)
                chapter_math_status = math_report["status"]
                chapter_math_reasons = math_report.get("reason_codes") or []
                chapter_math_counts = math_report.get("counts") or {}
                if chapter_math_status == "needs_recovery" and artifact_mode == "visual":
                    detail = math_report.get("manifest_error")
                    chapter_probe_error = (
                        "第 %d 章公式证据需要恢复后才能生成 visual 产物：%s%s"
                        % (chapter, ", ".join(chapter_math_reasons) or "unknown",
                           ("；" + detail) if detail else "")
                    )
                    probe_errors.append(chapter_probe_error)
                elif chapter_math_status != "needs_recovery":
                    chapter_has_math = chapter_math_status == "standard"
            except DependencyProbeError as exc:
                chapter_probe_error = str(exc)
                probe_errors.append(chapter_probe_error)
    if (artifact_mode == "visual" and workspace is not None and chapter is not None
            and pdf_backend == "auto"):
        backend_probe_error = (
            "章节级 visual 预检必须显式选择 --pdf-backend native、browser 或 html；"
            "auto 只允许用于尚未解析产物后端的首次材料预检"
        )
        probe_errors.append(backend_probe_error)
    rows = []
    for g in GROUPS:
        if g["needed_when"] == "materials_have_pdf":
            needed = has_pdf if has_pdf is not None else (
                "probe_error" if materials_probe_error else "unknown"
            )
        elif g["needed_when"] == "visual_artifacts":
            if artifact_mode != "visual":
                needed = False
            elif chapter_probe_error:
                needed = "probe_error"
            elif workspace is None and chapter is None:
                needed = "unknown"
            else:
                needed = chapter_has_math
        elif g["id"] == "browser" and artifact_mode == "visual":
            needed = ("probe_error" if backend_probe_error else
                      {"browser": True, "native": False, "html": False,
                       "auto": "unknown"}[pdf_backend])
        else:
            needed = "optional"              # cheatsheet PDF：有降级路径，永不算硬缺

        capability_probe_error = None
        should_probe = needed is True
        if g["id"] == "browser":
            ok = None
            if should_probe:
                try:
                    ok = _probe_browser()
                except Exception as exc:
                    ok = False
                    capability_probe_error = (str(exc) if isinstance(exc, DependencyProbeError)
                                              else "本地浏览器探测失败：%s" % exc)
            available = ["edge/chrome"] if ok else []
            install = "安装 Microsoft Edge 或 Google Chrome（或忽略——自动降级 HTML 手动打印）"
        else:
            required_version = g.get("required_version")
            available = []
            usable = []
            if should_probe:
                for imports, pip in g["candidates"]:
                    try:
                        # One candidate may be a compound backend.  Partial presence is not a
                        # capability: pypdfium2 without PIL cannot execute the builder's to_pil()
                        # render path and therefore must not make this preflight green.
                        if not all(_probe_import(imp) for imp in imports):
                            continue
                        if required_version:
                            distribution = imports[0]
                            actual = installed_distribution_version(distribution)
                            available.append("%s==%s" % (distribution, actual or "unknown"))
                            if actual == required_version:
                                usable.append(pip)
                        else:
                            available.append(pip)
                            usable.append(pip)
                    except Exception as exc:
                        capability_probe_error = (str(exc) if isinstance(exc, DependencyProbeError)
                                                  else "Python 模块 %s 探测失败：%s"
                                                  % (" + ".join(imports), exc))
                        break
                ok = bool(usable)
            else:
                ok = None
            install = g.get("install") or ("pip install " + g["candidates"][-1][1])
        if capability_probe_error:
            probe_errors.append("%s: %s" % (g["id"], capability_probe_error))
        if capability_probe_error:
            needed = "probe_error"
        rows.append({"id": g["id"], "ok": ok, "available": available,
                     "needed": needed, "install": install,
                     "probed": should_probe,
                     "probe_error": capability_probe_error,
                     "purpose_zh": g["purpose_zh"], "purpose_en": g["purpose_en"]})
    missing_needed = [r for r in rows if r["needed"] is True and not r["ok"]]
    return {"groups": rows, "materials_scanned": materials,
            "artifact_mode": artifact_mode,
            "pdf_backend": pdf_backend,
            "workspace_scanned": workspace,
            "chapter_scanned": chapter,
            "chapter_has_standard_math": chapter_has_math,
            "chapter_math_status": chapter_math_status,
            "chapter_math_reasons": chapter_math_reasons,
            "chapter_math_counts": chapter_math_counts,
            "probe_error": "; ".join(probe_errors) if probe_errors else None,
            "materials_have_pdf": has_pdf,
            "missing_needed": [r["id"] for r in missing_needed]}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Probe optional dependencies BEFORE they are needed (stdlib only; "
                    "agents run this at setup and offer the printed install commands).")
    ap.add_argument("--materials", default=None,
                    help="materials dir to scan; PDFs there make the PDF backends NEEDED")
    ap.add_argument("--workspace", default=None,
                    help="exam workspace whose selected persisted chapter should be inspected")
    ap.add_argument("--chapter", type=int, default=None,
                    help="positive chapter number; must be supplied together with --workspace")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--artifact-mode", choices=("chat", "visual"), default="chat",
                    help="explicit output preference; visual enables content-aware artifact probes")
    ap.add_argument("--pdf-backend", choices=("auto", "browser", "native", "html"),
                    default="auto",
                    help="PDF route; only explicit browser is a hard local-browser requirement")
    args = ap.parse_args(argv)
    if (args.workspace is None) != (args.chapter is None):
        ap.error("--workspace and --chapter must be supplied together")
    if args.chapter is not None and args.chapter < 1:
        ap.error("--chapter must be a positive integer")
    rep = build_report(args.materials, args.artifact_mode, args.workspace, args.chapter,
                       args.pdf_backend)
    if args.as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        mark = {True: "✓", False: "✗", None: "?"}
        for r in rep["groups"]:
            need = {True: "【需要】", False: "（当前所选内容/后端不需要）",
                    "unknown": "（当前信息不足，暂不硬判）",
                    "optional": "（可选：缺失自动降级）",
                    "probe_error": "（探测失败：未判定）"}[r["needed"]]
            print("%s %s %s—— %s" % (mark[r["ok"]], r["id"].ljust(10), need, r["purpose_zh"]))
            if not r["ok"] and r["needed"] is True:
                print("    ↳ 安装：%s" % r["install"])
        if rep["probe_error"]:
            print("\n[!] probe_error: %s（未将探测失败误报为需安装依赖）"
                  % rep["probe_error"])
        if rep["missing_needed"]:
            print("\n[!] 缺少当前材料需要的依赖：%s——先装再跑 ingest，别等它中途报错"
                  % ", ".join(rep["missing_needed"]))
    if rep["probe_error"]:
        return 2
    return 5 if rep["missing_needed"] else 0


if __name__ == "__main__":
    sys.exit(main())
