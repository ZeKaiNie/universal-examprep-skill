#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Official pre-ingest: scan a course-materials folder → raw_input.json (+ optional page-image
assets + a parse report) for scripts/ingest.py.

This is not itself an OCR engine. It is a deterministic, honest ingestion entrypoint that:
  - preserves page provenance (source_file / source_pages),
  - preserves full-page renders for figure-dependent lecture pages (so diagram questions keep context),
  - extracts DOCX/PPTX, XLSX worksheets, standalone raster files, and text/Markdown through
    dedicated local paths,
  - extracts obvious lecture Example/Quiz problem-solution pairs into quiz_bank items,
  - never pretends lossy text extraction is complete, and
  - fails / warns clearly when an optional backend is unavailable.

stdlib-only core + tests. PDF *text extraction* and *page rendering* are OPTIONAL backends:
  - text:   pypdf OR PyMuPDF (`fitz`)
  - render: PyMuPDF (`fitz`, native PNG, no extra deps) OR pypdfium2 + Pillow (its to_pil adapter)
Install only if you need them, e.g.:  pip install pypdf pymupdf   (or: pip install pypdf pypdfium2 Pillow)
Rendering also needs --asset-root <workspace>/references/assets (where the page PNGs are written).
Docling/MinerU are explicit-request, remote/cloud host capabilities only. This local
script never probes, installs, imports, or runs them and accepts no local heavy-parser runner.

Usage:
  python scripts/build_raw_input_from_workspace.py \\
      --materials ./course_materials \\
      --out skill_workspace/.ingest/source_raw_input.json \\
      --asset-root skill_workspace/references/assets \\
      --render-pages auto --extract-lecture-questions auto \\
      --report skill_workspace/.ingest/parse_report.json
  python scripts/ingest.py -i skill_workspace/.ingest/source_raw_input.json -o skill_workspace
  python scripts/validate_workspace.py skill_workspace
"""
import argparse
import copy
import hashlib
import importlib
import importlib.metadata
import zlib
import json
import os
import re
import shutil
import sys
import tempfile

try:
    import strict_json
except ImportError:  # imported as scripts.build_raw_input_from_workspace
    from scripts import strict_json

try:
    import exam_start
except ImportError:  # imported as scripts.build_raw_input_from_workspace
    from scripts import exam_start

try:
    from ingestion import (
        ConflictError,
        IngestionStore,
        MATERIAL_TEXT_LANGUAGE_CODES,
        SOURCE_UNIT_LANGUAGE_CODES,
        is_language_neutral_formula,
        is_link_or_reparse,
        make_source_id,
        normalize_workspace_path,
        read_json,
        source_language_evidence,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from ingestion.ooxml import OOXMLExtractionError, extract_ooxml
    from ingestion.adapters import (
        AdapterError, ExtractionRequest, resolve_adapter,
    )
    from ingestion.raster import RasterExtractionError, extract_raster
    from ingestion.xlsx import XLSXExtractionError, extract_xlsx
    from ingestion.pipeline import build_payload as build_ingestion_payload
    from asset_policy import (
        ANSWER_ASSET_ROLES,
        PROMPT_ASSET_ROLES,
        STUDENT_ATTEMPT,
        audit_asset_policy,
        iter_asset_declarations,
        legacy_attempt_promotion_receipts,
        physical_asset_key,
        workspace_asset_identity_key,
        workspace_asset_is_student_attempt,
    )
    from validate_workspace import workspace_asset_policy_snapshot
    from image_validation import ImageValidationError, png_dimensions
    from asset_crops import (
        CropAnnotation,
        CropContractError,
        CropReceipt,
        annotation_bbox_pdf_points,
        canonical_sha256 as crop_canonical_sha256,
        compact_asset_from_receipt,
        crop_receipt_index as validate_crop_receipt_index,
        make_crop_spec_sha256,
        render_crop_png,
        validate_crop_asset_binding,
    )
except ImportError:  # imported as scripts.build_raw_input_from_workspace in unit tests
    from scripts.ingestion import (
        ConflictError,
        IngestionStore,
        MATERIAL_TEXT_LANGUAGE_CODES,
        SOURCE_UNIT_LANGUAGE_CODES,
        is_language_neutral_formula,
        is_link_or_reparse,
        make_source_id,
        normalize_workspace_path,
        read_json,
        source_language_evidence,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from scripts.ingestion.ooxml import OOXMLExtractionError, extract_ooxml
    from scripts.ingestion.adapters import (
        AdapterError, ExtractionRequest, resolve_adapter,
    )
    from scripts.ingestion.raster import RasterExtractionError, extract_raster
    from scripts.ingestion.xlsx import XLSXExtractionError, extract_xlsx
    from scripts.ingestion.pipeline import build_payload as build_ingestion_payload
    from scripts.asset_policy import (
        ANSWER_ASSET_ROLES,
        PROMPT_ASSET_ROLES,
        STUDENT_ATTEMPT,
        audit_asset_policy,
        iter_asset_declarations,
        legacy_attempt_promotion_receipts,
        physical_asset_key,
        workspace_asset_identity_key,
        workspace_asset_is_student_attempt,
    )
    from scripts.validate_workspace import workspace_asset_policy_snapshot
    from scripts.image_validation import ImageValidationError, png_dimensions
    from scripts.asset_crops import (
        CropAnnotation,
        CropContractError,
        CropReceipt,
        annotation_bbox_pdf_points,
        canonical_sha256 as crop_canonical_sha256,
        compact_asset_from_receipt,
        crop_receipt_index as validate_crop_receipt_index,
        make_crop_spec_sha256,
        render_crop_png,
        validate_crop_asset_binding,
    )

try:
    from material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )
except ImportError:
    from scripts.material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )

try:
    from pdf_capabilities import PDF_RENDER_CANDIDATES, PDF_TEXT_CANDIDATES
except ImportError:
    from scripts.pdf_capabilities import PDF_RENDER_CANDIDATES, PDF_TEXT_CANDIDATES

# ---------------------------------------------------------------------------
# Heading detection / lecture extraction — PURE, stdlib, unit-tested on synthetic page text.
# A "page" is a dict: {"file": str, "page": int (1-based), "text": str}.
# ---------------------------------------------------------------------------

_NUM = r"(\d+)\s*\.\s*(\d+)"
# anchor markers to a line START (after optional bullet/number/markdown-heading prefix) so inline
# prose like "see Example 1.1" or a TOC entry isn't mistaken for a heading, while `## Quiz 1.1` (a
# Markdown heading in .md materials) and `- Example 1.1` (a bullet) still match.
_HEAD = r"^[ \t>*•·\-\d.)）、#]*"
_EXAMPLE_RE = re.compile(_HEAD + r"Example\s+" + _NUM, re.I | re.M)
_QUIZ_RE = re.compile(_HEAD + r"Quiz\s+" + _NUM, re.I | re.M)

# ---- A3: homework / solution files (separate PDFs paired by filename; inline solutions supported) ----
# a homework FILE is recognized by its path (folder or stem), NOT by content guessing
_HW_FILE_RE = re.compile(r"(?:^|[\\/_\-. ])(?:hw|homework|assignments?|problem[ _-]?sets?|psets?[ _-]?\d|ps[ _\-]?\d|作业|习题)",
                         re.I)
# ---- D1: 试卷类材料（sample exam / past paper / midterm / 真题 / 期中期末 / 模拟卷）----
# EN 一律词边界（exam ≠ example，实测坑）；final 单独太歧义（final version/report），
# 要求跟数字或 exam；CN 高置信词 + 消歧（模拟卷 ✓ / 模拟滤波器 ✗ —— 模拟后必须紧跟 题/卷/试/考）
_EXAM_FILE_RE = re.compile(
    r"(?:^|[\\/_\-. ])(?:exam(?!ple)s?|examinations?|mid[ _-]?terms?\d*|finals?[ _-]?exams?"
    r"|finals?[ _-]?\d+|quiz(?:zes)?\d*|past[ _-]?papers?|question[ _-]?papers?"
    r"|sample[ _-]?exams?|practice[ _-]?(?:exams?|midterms?|finals?)|mock[ _-]?exams?"
    r"|specimen[ _-]?papers?|prelims?[ _-]?\d+|makeup[ _-]?exams?)(?![A-Za-z])"
    r"|试卷|试题|考题|真题|模拟[题卷]|模拟试[卷题]|模拟考|期[中末]|半期|样[卷题]|测验|月考|补考", re.I)
# 试卷负例守卫：期末【复习提纲/串讲/笔记】是讲义、期中考试【安排/范围】是行政通知——
# 这些词与试卷词同现时不按试卷收（讲义类照常走讲义管线，绝不猜）
_EXAM_NEG_RE = re.compile(
    r"课件|讲义|讲稿|教案|提纲|笔记|总结|归纳|串讲|考点|知识点|重点|复习|安排|通知|范围|时间表|考试时间"
    r"|题型|说明|须知|指南|信息"
    r"|(?<![A-Za-z])(?:lectures?|slides?|notes?|handouts?|reviews?|outlines?|syllabus|schedules?"
    r"|format|info(?:rmation)?|instructions?|logistics|polic(?:y|ies)|guide(?:lines?)?)(?![A-Za-z])",
    re.I)


# 「像卷子」的文件名（1.pdf / 2020a.pdf / A卷.pdf / 2020-05.pdf）——目录/根的试卷上下文
# 只覆盖这类名字；混合备考文件夹里的 sorting.pdf / ch01.pdf 是讲义，不被上下文吞掉
_PAPERISH_RE = re.compile(r"^\d{1,4}(?:[-_ .]?\d{1,4})?\s*[abAB]?\s*卷?$|^[abAB]\s*卷?$|^卷[一二三四五12345]$"
                          r"|^finals?$|^papers?$", re.I)   # 裸 final/paper 单独太歧义，但在试卷
                                                           # 目录/根的上下文里就是卷子本体


def _deglue_exam(stem):
    """试卷词与解答/键词胶连（midtermsolutions / finalanswers / quizkey）——插分隔符让
    解答记号检测、配对键剥离与试卷记号边界照常工作。"""
    return re.sub(r"(?i)(midterms?\d*|finals?\d*|exams?\d*|examinations?|quiz(?:zes)?\d*|prelims?\d*"
                  r"|(?:past|question|specimen)?papers?\d*)"
                  r"((?:solutions?|answers?|soln?s?|ans)(?:keys?|manuals?)?|keys?)(?![a-z])",
                  lambda m: m.group(1) + "_" + m.group(2), stem)


# 宽松试卷记号：裸 finals?/midterms? 等对【题面卷】分类太歧义（final_version），但一个
# 已判为解答册的文件带这些词（final_solutions / final_key）就足以证明它是考试答案——
# 只用于除名守卫，绝不用于题面卷分类
_EXAMISH_LOOSE_RE = re.compile(
    r"(?:^|[\\/_\-. ])(?:finals?|midterms?|exams?|quiz(?:zes)?|prelims?|(?:past|question|specimen)?papers?)"
    r"(?![A-Za-z])|试卷|真题|期[中末]|考试", re.I)


def _seg_exam(seg):
    """单个路径段是试卷信号：带试卷记号且【同段】没有讲义/行政负例词——
    midterm_notes/、期中复习/ 说自己是笔记/复习材料，不是试卷。"""
    return bool(_EXAM_FILE_RE.search("/" + seg)) and not _EXAM_NEG_RE.search(seg)


def _is_exam_path(rel, root_name=""):
    """路径任一段（目录/文件名/根目录名）是试卷信号、且文件名自身没有负例词——判为试卷材料。
    试卷与作业共用抽取/配对机械，但 source_type=exam、绝不进 wiki。"""
    rel = rel.replace(chr(92), "/")
    segs = [sg for sg in rel.split("/") if sg]
    if not segs:
        return False
    segs[-1] = _deglue_exam(os.path.splitext(segs[-1])[0])
    if _EXAM_NEG_RE.search(segs[-1]):
        return False                       # 文件名负例词一票否决（exams/期末复习笔记.pdf 是笔记）
    if _seg_exam(segs[-1]):
        return True                        # 文件名自身就是试卷信号
    if not _PAPERISH_RE.match(segs[-1].strip()):
        return False                       # 目录/根上下文只覆盖「像卷子」的文件名
    if any(_seg_exam(sg) for sg in segs[:-1]):
        return True
    return bool(root_name and _seg_exam(root_name))
# 根目录名误判会把整个讲义库当作业上下文——记号后必须在词元边界收尾（HW3 / ps4 /
# problem_set_2 / hw2solutions ✓；mkdtemp 随机名 tmp_ps4abc / tmpa_hw3x ✗，正是 CI 偶发
# 挂掉 lecture 抽取的 flake 根源）
_HW_ROOT_RE = re.compile(
    r"(?:^|[\\/_\-. ])(?:(?:hw|homework|assignments?|problem[ _-]?sets?|psets?)[ _\-]?\d*"
    r"|ps[ _\-]?\d+)"
    r"(?=$|[\\/_\-. ()]|(?:solutions?|answers?|sols?|ans|keys?|manuals?)(?:$|[\\/_\-. ()0-9])"
    r"|(?:v\d+|ver(?:sion)?|final|rev(?:ised)?|updated?|new|latest|copy|draft|fixed|corrected)"
    r"(?:$|[\\/_\-. ()0-9]))"
    r"|(?<![非无免])(?:作业|习题)(?=$|[\\/_\-. ()0-9]|答案|解答|册|本|集|资料|材料)", re.I)
# tokens that mark a SOLUTION companion file (hw1_sol.pdf / HW2_Answers.pdf / 作业3答案.pdf)。
# solution/answer 需要词元边界：前面不能是字母（unanswered ≠ answers；hw1solution 的数字前缀合法），
# 后面须是分隔符/括号/串尾——纯子串匹配会把 unanswered_hw1 误判成解答文件
_SOL_TOKEN_RE = re.compile(r"(?<![A-Za-z])(?:solutions?|answers?)(?=[_\-. ()\\/]|$)"
                           r"|(?<![A-Za-z])(?:soln?s?|ans)(?=[_\-. ()\\/]|$)|答案|解答", re.I)
# 题号支持 教材式小数（1.1.2）与 字母小题（1(a) / 1a）——折叠会把真小题当重复丢掉
# 裸字母小问要求后面不再跟字母——PDF 抽取丢空格的「Problem 2Compute」不许把 C 吞成小问 2c
_HW_NUM_PAT = r"(\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z](?![A-Za-z]))?)"
# problem headings inside homework/solution files（行首锚定，与 lecture 标记同族）
_HW_PROB_RES = (re.compile(_HEAD + r"(?:Problem|Exercise|Question)\s*#?\s*" + _HW_NUM_PAT, re.I | re.M),
                re.compile(_HEAD + r"(?:第\s*" + _HW_NUM_PAT + r"\s*题|习题\s*" + _HW_NUM_PAT
                           + r"[.:：]?|题目\s*" + _HW_NUM_PAT + r"[.:：]?)", re.M))
# inline solution heading (same file, follows its problem)。解答词与编号之间只许同行空白——
# \s* 会跨换行把「Answers」节标题和下一行的「1.」吞成一个 num=1 标记，让整块答案区错归第一题
_HW_SOL_RE = re.compile(_HEAD + r"(?:Solutions?|Answers?|解答|答案)[ \t]*(?:Keys?|Manuals?)?[ \t]*(?:(?:to|for|of)[ \t]+(?:(?:Problem|Exercise|Question)[ \t]*)?)?(?:#?[ \t]*" + _HW_NUM_PAT + r")?[ \t]*(?:[.:：]|$)",
                        re.I | re.M)
# 「Problem 1 Solution」这类解答段标题：号后【同一行】剩余部分必须整体就是 解答/答案 标记
#（可带编号/收尾标点）——「Problem 1: Answer the following…」是题面动词，绝不能翻成解答段
_HW_SOL_HEAD_RE = re.compile(r"^\s*[\)\.:\-]?\s*\(?\s*(?:solutions?|answers?|解答|答案)"
                             r"\s*(?:#?\s*\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z])?)?\s*\)?\s*[.:：]?\s*$",
                             re.I)
# 「Problem 1 Solution: A1」——解答词后带冒号+同行内容也是解答段；注意首字符不许是冒号，
# 否则「Problem 1: Answer the following…」（冒号在解答词之前）会被误翻
_HW_SOL_HEAD_CONTENT_RE = re.compile(r"^\s*[\)\.\-:：]?\s*\(?\s*(?:solutions?|answers?|解答|答案)"
                                     r"\s*(?:#?\s*\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z])?)?\s*\)?"
                                     r"\s*[:：]\s*\S", re.I)
# answer_key / solution_manual 的 key/manual 是解答后缀描述词——分类与配对键计算前一并剥掉
_KEY_TOKEN_RE = re.compile(r"(?<![A-Za-z])(?:keys?|manuals?)(?=[_\-. ()]|$)", re.I)
# 解答记号后允许的尾缀词：连接词 + 版本/修订描述词（hw1_solutions_v2 / hw1_sol_final）——
# 其余实义词（hw1_answer_questions 的 questions）仍按动词短语归作业
_SOL_TRAIL_OK = frozenset((
    "for", "to", "of", "the", "v", "ver", "version", "final", "rev", "revised",
    "updated", "update", "new", "latest", "copy", "draft", "fixed", "corrected", "review", "reviewed",
    "修订", "最终", "更新", "终版", "新版"))
# answer-key 常见的「1. Answer: …」形式：编号在标记前面，被 _HEAD 吞掉——从匹配前缀里找回
_SOL_PREFIX_NUM_RE = re.compile(r"^[ \t>*•·\-#]*(\d+(?:\.\d+)*(?:\([A-Za-z]\))?)\s*[.)）、]")
# 「1a. Answer:」「1(a). Answer:」——字母/括号不在 _HEAD 字符类里，_HW_SOL_RE 根本到不了 Answer；
# 用专门的带号前缀形式补上（编号含字母小问）
_HW_SOL_PRE_RE = re.compile(r"^[ \t>*•·\-#]*(\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z])?)\s*[.)）、]?\s*"
                            r"(?:Solutions?|Answers?|解答|答案)\s*(?:[.:：]|$)", re.I | re.M)
# 题号之后紧跟的解答词（「Problem 1 Solution: …」的 Solution: 部分）——空白判定剥复合标题用
# 「Answer:」标签行的答题栏指示语（告诉学生往哪儿写：in the box below / space provided /
# 在下方作答）——其后再无内容时并回题面，绝不存成官方答案让测验拿指示语判分
_HW_ANSBOX_INSTR_RE = re.compile(
    r"(?i)\b(?:in|into|on|onto|using)[ \t]+(?:the|a|your)[ \t]+(?:answer[ \t]+|separate[ \t]+)?"
    r"(?:box(?:es)?|space|blank|line|lines|sheet|grid|area)\b"
    r"|\b(?:box(?:es)?|space|blank|line|lines|sheet|grid|area)[ \t]+(?:below|provided)\b"
    r"|\bshow[ \t]+(?:all[ \t]+)?(?:your[ \t]+)?work\b"
    r"|\b(?:explain|justify)[ \t]+your[ \t]+(?:reasoning|answer|steps)\b"
    r"|答题[框栏区]|在下[方面]|空白处|写出(?:计算|解题)?过程|说明理由")

# 键控答案行的统一正则：编号 + [.)、] 分隔（1. A / 1a. x / 1 (a). y，紧凑形 1.A），或
# 教材式小数号 + 空白直接跟内容（1.1 A——小数号后常不写分隔点）。捕获组二选一，
# 用 _keyline_num 归一取号；所有拆分点都要求号集 ⊆ 已知题号，小数-空白形不会误拆
# 行首数值（3.14 is pi 不在题号集合里）
_HW_KEYLINE_RE = re.compile(
    r"^[ \t]*(?:(\d+(?:\.\d+)*(?:[ \t]*\([A-Za-z]\)|[A-Za-z](?![A-Za-z]))?)[.)、]"
    r"(?:[ \t]+|(?=[A-Za-z一-鿿（(]))|(\d+(?:\.\d+)+)[ \t]+(?=\S))", re.M)


def _keyline_num(m):
    return _hw_num(m.group(1) or m.group(2))


def _keyline_filter(keyed_ms, prob_nums):
    """小数-空白形（group 2）是弱信号——只有号真在题号集合里才算键，说明行「3.14 is pi」
    直接丢弃、不毒化整节的 ⊆ 判定；定界形（group 1，1. A）是强信号，全部保留、由调用方的
    全集 ⊆ 题号守卫把关（编号解答步骤因此不拆）。"""
    return [m for m in keyed_ms
            if m.group(1) is not None or _hw_num(m.group(2)) in prob_nums]


_HW_PROB_SOL_HEAD_RE = re.compile(r"^\s*[\).\-]?\s*\(?\s*(?:solutions?|answers?|解答|答案)\s*[.:：]?", re.I)

# Two classes of asset cue. ASSET_EXCLUDE masks known false-positive phrases first.
ASSET_EXCLUDE = ("table of contents", "figure it out", "figure out", "graph theory", "figure caption")
# STRONG: the question explicitly references a figure SHOWN to the student ("at right", "Venn", "shade
# the region", "image below"). Asset-dependent on ANY source — a .txt that says "shade the Venn at right"
# is fail-closed because the figure is genuinely missing from the text.
STRONG_CUES = [re.compile(p, re.I) for p in (
    r"venn", r"at right", r"to the right", r"shown (on the right|below|above)", r"as shown",
    r"\bshaded?\b",
    r"(figure|diagram|table|image|picture|chart|graph|tree|plot)s?\s+(below|above|at right|to the right)",
    r"(shown|given)\s+in\s+(figure|table|fig\.?)\s*\d*",
    # 复合形才算「图已给出」——裸「区域/图示」会把 求定义域区域 这类纯文字题误封（审计实测），
    # 尤其 .txt 课程会被 fail-closed 卡死
    "文氏图", "如图", "阴影", "示意图",
    "如下图", "见下图", "如上图", "见上图", "下图所示", "上图所示",
    "如下表", "见下表", "如上表", "见上表", "下表所示", "上表所示",
)]
# WEAK: a figure NOUN that might instead be a "produce" prompt ("draw the graph of y=x^2", "sketch the
# tree"). Asset-dependent only for a renderable PDF source (where over-flagging just renders an extra
# page, harmless); on .txt/.md the text is already complete, so don't fail-close a drawing prompt.
WEAK_CUES = [re.compile(p, re.I) for p in (
    r"\bdiagram\b", r"\bfigure\b", r"\btable\b", r"\bgraph\b", r"\bplot\b", r"\btree\b", r"\bcircuit\b",
    r"\bdraw\b", r"\bdrawn\b", r"\baxes\b", r"\brectangle\b", r"\btriangle\b",
    r"\bhistograms?\b", r"\bflow\s*charts?\b", "流程图", "柱状图", "折线图", "饼图", "图示", "区域",
    # 跨页指涉只对可渲染 PDF 生效——.txt 里的 see next page 是文字指引，fail-closed 会误封；
    # matrix below 同理：.txt 的矩阵常直接写在题面里
    r"[见如]\s*[上下]一?页", r"(?:see|on)\s+(?:the\s+)?(?:next|previous)\s+page",
    r"matrix\s+(below|above|shown)",
)]


def _cue_in(text, patterns):
    masked = (text or "").lower()
    for ex in ASSET_EXCLUDE:
        masked = masked.replace(ex, " ")   # drop known false-positive phrases before matching
    return any(p.search(masked) for p in patterns)


def requires_assets_heuristic(text, renderable=True):
    """True if the question depends on a figure that isn't in the text. STRONG figure-SHOWN cues fire on
    any source; WEAK figure-noun cues (possibly a 'draw the X' produce-prompt) fire only for a renderable
    PDF source. Fail-closed by design: when unsure on a PDF we prefer attaching a page image."""
    return _cue_in(text, STRONG_CUES) or (renderable and _cue_in(text, WEAK_CUES))


# role is decided by the word IMMEDIATELY after the marker number (anchored), NOT a loose tail scan —
# otherwise a problem whose text merely contains "solution" ("find the solution set") is misread.
_ROLE_PROBLEM_RE = re.compile(r"^\s*[\)\.:\-]?\s*\(?\s*problems?\b", re.I)             # incl. plural "Problems"
_ROLE_SOLUTION_RE = re.compile(r"^\s*[\)\.:\-]?\s*\(?\s*(?:solutions?|answers?)\b", re.I)  # Solution(s)/Answer(s)
_ROLE_SUBPART_PREFIX_RE = re.compile(r"^\s*[\)\.:\-]?\s*\(\s*([A-Za-z])\s*\)\s*", re.I)
_ROLE_CONTINUED_PREFIX_RE = re.compile(
    r"^\s*[\)\.:\-]?\s*\(?\s*continued\b\s*\d*\s*\)?[\s:.\-]*", re.I)
_EXAMPLE_REFERENCE_TAIL_RE = re.compile(r"^\s*[\)\.:\-]?\s*(?:says|states)\s+that\b", re.I)
# PDF text extraction preserves visual line wraps. A prose cross-reference can therefore land at
# a physical line start and satisfy _EXAMPLE_RE/_QUIZ_RE even though it is not a title, for example
# ``... decisions in\nExample 1.9, each outcome ...``. A bare marker after one of these continuation
# cues has no reliable heading boundary; explicit ``Problem``/``Solution`` markers remain trusted.
_WRAPPED_REFERENCE_LEAD_IN_RE = re.compile(
    r"(?:\b(?:see|in|from|of|via|using|consult|review|recall|following)"
    r"|\b(?:as\s+)?(?:described|defined|shown|given|stated|proved|derived|illustrated|explained)\s+in)"
    r"\s*[:(]?\s*$",
    re.I,
)
_REFERENCE_NUMBER_TAIL_RE = re.compile(r"^\s*[,.;](?:\s|$)")
_TOC_RE = re.compile(r"\.{4,}")   # 4+ dot-leaders → a table-of-contents line, not a heading


def _normalized_role_tail(tail):
    tail_role = tail
    for _ in range(2):
        before = tail_role
        tail_role = _ROLE_SUBPART_PREFIX_RE.sub("", tail_role, count=1)
        tail_role = _ROLE_CONTINUED_PREFIX_RE.sub("", tail_role, count=1)
        if tail_role == before:
            break
    return tail_role


def _variant_of_tail(tail):
    match = _ROLE_SUBPART_PREFIX_RE.match(tail)
    return match.group(1).lower() if match else None


def _role_of_tail(tail):
    tail_role = _normalized_role_tail(tail)
    if _ROLE_PROBLEM_RE.match(tail) or _ROLE_PROBLEM_RE.match(tail_role):
        return "problem"
    if _ROLE_SOLUTION_RE.match(tail) or _ROLE_SOLUTION_RE.match(tail_role):
        return "solution"
    return "problem"   # bare "Quiz 1.1" with no keyword → a problem


def _heading_form_of_tail(tail):
    """Distinguish explicit problems/solutions from bare worked examples."""
    tail_role = _normalized_role_tail(tail)
    if _ROLE_PROBLEM_RE.match(tail) or _ROLE_PROBLEM_RE.match(tail_role):
        return "explicit_problem"
    if _ROLE_SOLUTION_RE.match(tail) or _ROLE_SOLUTION_RE.match(tail_role):
        return "explicit_solution"
    return "bare"


def _has_lecture_heading_evidence(text, marker, tail):
    """Return whether a line-start marker is structurally credible as a lecture title.

    Line-start anchoring alone is insufficient for wrapped PDF prose. Explicit role words are
    strong structure. A bare marker is accepted at a page/paragraph boundary, but rejected when
    the preceding physical line is syntactically continuing into it or the number is immediately
    punctuated like an inline citation.
    """
    if _heading_form_of_tail(tail) != "bare":
        return True
    label = re.search(r"\b(?:Example|Quiz)\b", marker.group(0), re.I)
    if label and marker.group(0)[:label.start()].strip():
        return True  # Markdown/list/blockquote prefix is explicit document structure.

    before = text[:marker.start()]
    if not before:
        return True
    # ``marker.start()`` is the beginning of its line, so ``before`` normally ends in that line's
    # separator. Remove exactly that separator before asking for the preceding physical line.
    previous_text = before[:-1] if before.endswith("\n") else before
    previous_line = previous_text.rsplit("\n", 1)[-1].rstrip("\r").strip()
    if not previous_line:  # blank-line / paragraph boundary
        return True
    if _WRAPPED_REFERENCE_LEAD_IN_RE.search(previous_line):
        return False
    if _REFERENCE_NUMBER_TAIL_RE.match(tail):
        return False
    return True


def _iter_markers(text):
    """Return non-TOC lecture markers in text order."""
    text = text or ""
    out = []
    for kind, rx in (("example", _EXAMPLE_RE), ("quiz", _QUIZ_RE)):
        for m in rx.finditer(text):
            nl = text.find("\n", m.end())
            line = text[m.start():(nl if nl >= 0 else len(text))][:300]   # the whole heading line
            if _TOC_RE.search(line):   # dot-leaders anywhere on the line → TOC entry (even long titles), skip
                continue
            tail = text[m.end():m.end() + 48]
            if kind == "example" and _EXAMPLE_REFERENCE_TAIL_RE.match(_normalized_role_tail(tail)):
                continue
            if not _has_lecture_heading_evidence(text, m, tail):
                continue
            out.append({"start": m.start(), "kind": kind, "chapter": int(m.group(1)), "num": int(m.group(2)),
                        "variant": _variant_of_tail(tail),
                        "role": _role_of_tail(tail), "heading_form": _heading_form_of_tail(tail),
                        "continued": bool(re.search(r"\bContinued\b", tail, re.I))})
    out.sort(key=lambda d: d["start"])
    return out


def detect_lecture_markers(text):
    out = []
    for marker in _iter_markers(text):
        public = {k: marker[k] for k in ("kind", "chapter", "num", "role", "continued")}
        if marker.get("variant") is not None:
            public["variant"] = marker["variant"]
        out.append(public)
    return out


def orphan_solution_keys(pages):
    """Return solution markers without a matching problem."""
    marked = _markers_with_pages(pages)
    probs = {_key(mk) for _, mk in marked if mk["role"] == "problem"}
    sols = {_key(mk) for _, mk in marked if mk["role"] == "solution"}
    public = [key if key[3] is not None else key[:3] for key in sols - probs]
    return sorted(public, key=lambda key: (
        key[0], key[1], key[2], key[3] if len(key) > 3 else ""))


def _markers_with_pages(pages):
    marked = []
    for i, pg in enumerate(pages):
        for raw in _iter_markers(pg.get("text", "")):
            mk = {k: raw[k] for k in
                  ("kind", "chapter", "num", "variant", "role", "continued", "heading_form")}
            marked.append((i, mk))
    return marked


def _key(mk):
    return (mk["kind"], mk["chapter"], mk["num"], mk.get("variant"))


_ANY_VARIANT = object()


def _problem_statement(page_text, kind, chapter, num, variant=_ANY_VARIANT):
    """Extract matching problem slices up to the next marker."""
    text = page_text or ""
    mks = _iter_markers(text)
    starts = [d["start"] for d in mks]
    parts = []
    for d in mks:
        variant_matches = variant is _ANY_VARIANT or d.get("variant") == variant
        if (d["kind"] == kind and d["chapter"] == chapter and d["num"] == num
                and variant_matches and d["role"] != "solution"):
            after = [st for st in starts if st > d["start"]]
            e = min(after) if after else len(text)
            parts.append(" ".join(text[d["start"]:e].split()).strip())
    return " ".join(parts).strip()


def _body_after_marker(stmt, kind, chapter, num):
    """Strip the leading problem heading."""
    rx = _EXAMPLE_RE if kind == "example" else _QUIZ_RE
    m = rx.search(stmt or "")
    if not m:
        return (stmt or "").strip()
    rest = stmt[m.end():]
    rest = _normalized_role_tail(rest)
    rest = re.sub(r"^\s*[\):.\-]?\s*\(?\s*problems?\b\)?", "", rest, flags=re.I)  # drop a trailing "Problem(s)"
    rest = _ROLE_CONTINUED_PREFIX_RE.sub("", rest, count=1)
    return rest.strip(" .:：、)）-—\t\n")


def _solution_statement(page_text, kind, chapter, num, variant=_ANY_VARIANT):
    """Extract matching solution slices up to the next marker."""
    text = page_text or ""
    mks = _iter_markers(text)
    starts = [d["start"] for d in mks]
    parts = []
    for d in mks:
        variant_matches = variant is _ANY_VARIANT or d.get("variant") == variant
        if (d["kind"] == kind and d["chapter"] == chapter and d["num"] == num
                and variant_matches and d["role"] == "solution"):
            after = [st for st in starts if st > d["start"]]
            e = min(after) if after else len(text)
            parts.append(" ".join(text[d["start"]:e].split()).strip())
    return " ".join(parts).strip()


def extract_lecture_items(pages, backend=None):
    """Pair lecture problems and solutions, preserving source-file identity."""
    marked = _markers_with_pages(pages)
    sol_by_key = {}
    for mj, (pj, mk2) in enumerate(marked):
        if mk2["role"] == "solution":
            sol_by_key.setdefault(_key(mk2), []).append((mj, pj))
    prob_files = {}
    for (pj, mk2) in marked:
        if mk2["role"] == "problem":
            prob_files.setdefault(_key(mk2), set()).add(pages[pj]["file"])
    ambiguous = {k for k, fs in prob_files.items() if len(fs) > 1}
    file_idx = {}
    for k in ambiguous:
        for n, f in enumerate(sorted(prob_files[k])):
            file_idx[(k, f)] = n

    claimed = set()
    items, seen = [], set()
    for mi, (i, mk) in enumerate(marked):
        if mk["role"] != "problem":
            continue
        key = _key(mk)
        prob_page = pages[i]
        pf = prob_page["file"]
        if (key, pf) in seen:
            continue
        seen.add((key, pf))
        prob_text = prob_page.get("text", "")

        prob_idxs = sorted({i} | {pj2 for (pj2, mk2) in marked
                                  if _key(mk2) == key and mk2["role"] == "problem"
                                  and pages[pj2]["file"] == pf and mk2.get("continued")})
        q_pages = sorted({(pages[k]["file"], pages[k]["page"]) for k in prob_idxs}, key=lambda fp: (fp[1], fp[0]))

        other_prob_files = prob_files.get(key, set()) - {pf}
        ambiguous_key = key in ambiguous
        chosen = [(mj, pj) for (mj, pj) in sol_by_key.get(key, []) if mj not in claimed
                  and (pages[pj]["file"] == pf
                       or (not ambiguous_key and pages[pj]["file"] not in other_prob_files))]
        for (mj, pj) in chosen:
            claimed.add(mj)
        ans_idx = sorted({pj for (mj, pj) in chosen})

        kind = mk["kind"]
        label = "Example" if kind == "example" else "Quiz"
        variant = key[3]
        number_label = "%d.%d%s" % (
            key[1], key[2], "(%s)" % variant.upper() if variant else "")
        stmt = _problem_statement(prob_text, kind, key[1], key[2], variant)
        renderable = pf.lower().endswith(".pdf")
        needs = (requires_assets_heuristic(stmt or prob_text, renderable) or any(
            requires_assets_heuristic(_problem_statement(
                pages[k].get("text", ""), kind, key[1], key[2], variant),
                                      renderable) for k in prob_idxs if k != i))
        # marker-only: extraction yielded just the heading on a single page (real prompt likely in an
        # image) → NOT a standalone question. Detect by ABSENCE of any word/CJK content after the
        # heading (not a char-length cutoff — a terse CJK prompt like "求导"/"证明" is a real question).
        # real prompt content = a LETTER, CJK char, or math operator/relation. A bare page-number body
        # ("Quiz 1.1\n12") is a slide footer → marker_only; a symbolic prompt ("2+2=?", "√4=?") is real.
        _mo_body = "\n".join(l for l in _body_after_marker(stmt, kind, key[1], key[2]).splitlines()
                              if not _PAGE_RESIDUE_RE.match(l.strip()))   # 页脚残渣行不算正文
        # _problem_statement 已把页面折叠成单行——行内再清一遍词形页脚（Page 12 of 20 Slide 3）
        _mo_body = re.sub(r"(?i)(?:pages?|slides?)\s*\d+(?:\s*(?:of|/)\s*\d+)?"
                          r"|第\s*\d+\s*[页张]", " ", _mo_body)
        marker_only = ((not needs) and len(prob_idxs) == 1
                       and not re.search(r"[A-Za-z一-鿿=+√∫∑^?×÷<>≤≥]", _mo_body))
        question_crop_plan = _lecture_item_crop_plan(
            pages, prob_idxs, key, backend, "problem"
        ) if backend is not None and (needs or marker_only) else None
        # 完整原始题面（锚页 + 续页切片）——needs/marker_only 的 question 会被替换成指引句，
        # 跨页图线索（见下页图 在续页上）要靠它检查
        _cont_parts = [_problem_statement(
                           pages[k].get("text", ""), kind, key[1], key[2], variant)
                       or " ".join((pages[k].get("text") or "").split()) for k in prob_idxs if k != i]
        orig_question = " ".join([stmt] + _cont_parts).strip()
        if needs:
            qts = "page_reference"
            question = ("（%s %s）本题依赖原始讲义 %s 第 %d 页的图/表，须配合所附 asset 作答。"
                        % (label, number_label, pf, prob_page["page"]))
        elif marker_only:
            qts = "page_reference"
            question = ("（%s %s）题面未能从文本提取（可能在图片中），见原始讲义 %s 第 %d 页。"
                        % (label, number_label, pf, prob_page["page"]))
        else:
            qts = "full"
            # continued 切片已在 orig_question 内按题裁好（Quiz 1.1 (Continued) 页不吞 Quiz 1.2）
            question = orig_question
        item_id = "lecture_%s_%d_%d%s" % (
            kind, key[1], key[2], "_%s" % variant if variant else "")
        if key in ambiguous:   # readable stem + injective index (so a/b.pdf vs a_b.pdf don't collide)
            item_id += "__%s_%d" % (re.sub(r"[^\w]", "_", os.path.splitext(pf)[0]), file_idx[(key, pf)])
        _qt, _opts = _classify_question_type(question)
        item = {
            "id": item_id,
            "chapter": key[1],
            "source_type": "example" if kind == "example" else "lecture_quiz",
            "type": "diagram" if needs else _qt,
            "question": question,
            "source": "material",
            "source_file": pf,
            "source_pages": [p for (f, p) in q_pages],
            "_question_pages": ([] if question_crop_plan else q_pages),
            "_prompt_text": orig_question,                  # 原始题面含续页（needs 项 question 被替换成指引句）
            "_render": bool(needs or marker_only),          # render the page for figure- AND image-prompt items
            "requires_assets": bool(needs),
            "question_text_status": qts,
        }
        if question_crop_plan:
            item["_question_crops"] = question_crop_plan
        if variant is not None:
            item["variant"] = variant
        if kind == "example":
            item["_teaching_role"] = (
                "paired_problem"
                if bool(ans_idx) or any(
                    _key(mk2) == key and mk2.get("role") == "problem"
                    and mk2.get("heading_form") == "explicit_problem"
                    and pages[pj2]["file"] == pf
                    for pj2, mk2 in marked
                )
                else "worked_example"
            )
            item["_teaching_title"] = "%s %s" % (label, number_label)
            if item["_teaching_role"] == "worked_example":
                # An inline worked Example remains teaching-only even when its
                # exact source text can serve as solution evidence.  It must
                # never enter the selectable/gradable question bank merely
                # because that material evidence is now preserved.
                item["gradable"] = False
        if not needs and _qt == "choice" and _opts:
            item["options"] = _opts
        # ``keywords`` is optional for subjective items.  Do not emit an empty
        # placeholder: the structured ingestion envelope deliberately treats a
        # present-but-empty keyword list as malformed review metadata.
        if ans_idx:
            ans = sorted({(pages[j]["file"], pages[j]["page"]) for j in ans_idx}, key=lambda fp: (fp[1], fp[0]))
            first_file = ans[0][0]
            item["answer_source_file"] = first_file
            item["answer_source_pages"] = [p for (f, p) in ans if f == first_file]
            answer_crop_plan = _lecture_item_crop_plan(
                pages, ans_idx, key, backend, "solution"
            ) if backend is not None and (needs or marker_only) else None
            if answer_crop_plan:
                item["_answer_crops"] = answer_crop_plan
            else:
                item["_answer_pages"] = ans
            ref = "见原始讲义 %s 第 %s 页的解答。" % (
                first_file, "、".join(str(p) for (f, p) in ans if f == first_file))
            sol = " ".join(t for t in (_solution_statement(
                                           pages[j].get("text", ""), kind, key[1], key[2], variant)
                                       for j in ans_idx) if t).strip()
            if sol:
                # Keep extracted material evidence byte-faithful to the source language.
                # Asset requirements belong in typed metadata and answer-side page/asset
                # records; appending an agent-authored Chinese hint here would later be
                # claimable as if it were part of an English official solution.
                item["answer"] = sol
                _apply_typed_answer(item)
            else:
                item["answer"] = ref
                _apply_typed_answer(item)
        else:
            # A bare Example heading plus non-empty body does not prove that the
            # page contains a complete worked answer.  Keep the source-derived
            # teaching item, but require an explicit typed review that binds a
            # same-page native text unit and a current semantic-v2 prompt crop
            # before an ``inline_material`` answer unit can be added.
            item["answer_status"] = "unknown"
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# A3: homework / solution extraction — deterministic, filename-paired, fail-loud
# ---------------------------------------------------------------------------



def _strip_sol_desc(stem):
    """Strip solution DESCRIPTOR words（key/manual，含与解答词胶连的 solutionmanual/solmanual/answerkey）
    ——它们是解答后缀的一部分，分类与配对键都不该被它们挡住。"""
    stem = re.sub(r"(?i)(solutions?|answers?|sol|ans)(keys?|manuals?)", lambda m: m.group(1), stem)
    return _KEY_TOKEN_RE.sub("", stem)


def _pure_sol_stem(stem):
    """文件名剥掉解答描述词后【只剩】解答记号/连接词/版本词/hw 记号——纯解答名
    （solutions / final_answers / hw1_key ✓；questions_answers 的 questions 是实义词 ✗）。
    比 _sol_dir_segment 的目录后缀规则窄：Q&A 讲义（questions_answers.pdf）不能被误杀出 wiki。"""
    # keys? 描述词本身就是答案册记号（key.pdf / final_key.pdf）；manuals? 单独出现是课程
    # 手册（manual.pdf），只有伴随解答/答案记号（solution_manual）才算——不误杀正常讲义
    has_key = bool(re.search(r"(?<![A-Za-z])keys?(?=[_\-. ()]|$)", stem, re.I))
    stem = _strip_sol_desc(stem)
    if not (_SOL_TOKEN_RE.search(stem) or has_key):
        return False
    rest = _SOL_TOKEN_RE.sub("", stem)
    toks = re.findall(r"[A-Za-z一-鿿]+", rest)
    return all(t.lower() in _SOL_TRAIL_OK or _HW_FILE_RE.search(t) for t in toks)


def _sol_dir_segment(seg):
    """One path segment is a SOLUTION directory only if, after removing 解答记号/描述词/连接词,
    nothing but hw-ish tokens remains（solutions/ ✓、hw1_solutions/ ✓、answerkeys/ ✓）——
    「answer_questions/」这类动词短语目录装的是题面，整目录判解答会把作业全丢掉。"""
    seg = _strip_sol_desc(seg)         # answerkeys/solutionmanuals 的胶连描述词先剥掉再认记号
    if not _SOL_TOKEN_RE.search(seg):
        return False
    if re.search(r"(?i)(?:solutions?|answers?|sols?|ans)[_. ()-]*$", seg):
        return True                        # 记号在段尾（week1_solutions/ ↔ answerkeys/）——后缀式解答目录
    rest = _SOL_TOKEN_RE.sub("", seg)
    toks = re.findall(r"[A-Za-z一-鿿]+", rest)
    # 剩余词允许连接词 + 版本/修订描述词（solutions_final/ answerkeys_v2/）——与文件名侧同口径
    return all(t.lower() in _SOL_TRAIL_OK or _HW_FILE_RE.search(t) for t in toks)


def _hw_sepform(stem):
    """Separator-PRESERVING normal form（hw1_probability_worksheet / hw1a）——前缀配对要靠边界
    区分「作业名延长」（hw1_…）与「字母变体」（hw1a 是另一份作业），纯 alnum 规范形丢了这个信号。"""
    s = re.sub(r"(?:\s*\(\d+\))+\s*$", "", stem)
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", s.lower()).strip("_")
    s = re.sub(r"^homework", "hw", s)
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def _hw_norm(stem):
    """Normalized pairing key: lowercase, keep alnum + CJK only（hw1_sol → hw1 after token strip）."""
    # 浏览器重复下载的副本后缀「hw2 (4)(1)」不参与配对——真重名副本会归到同一键，
    # 触发歧义→fail-loud 不配对，不会串答案
    s = re.sub(r"(?:\s*\(\d+\))+\s*$", "", stem)
    s = re.sub(r"[^a-z0-9一-鿿]+", "", s.lower())
    s = re.sub(r"^homework", "hw", s)              # homework2solutions ↔ hw2：常见同义词归一
    # 零填充变体也要配对（HW01.pdf ↔ HW1_sol.pdf）——去掉数字组前导零；
    # 只删「前面不是数字」的 0，hw10 不受影响，hw1/hw10 的边界检查照旧
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def classify_homework_files(files, root_name="", notes=None):
    """Split material files into (homework_files, {solution_file: paired_homework_file|None}).
    Solutions pair by stripped-stem equality or prefix; an unpairable solution file is fail-loud."""
    hw, sols = [], []
    # --materials 直接指向作业文件夹时，相对路径丢失目录线索——用根目录名补上
    root_is_hw = bool(root_name and _HW_ROOT_RE.search("/" + root_name))
    for f in files:
        rel = f.replace("\\", "/")
        stem = _deglue_exam(os.path.splitext(os.path.basename(f))[0])
        stem_nokey = _strip_sol_desc(stem)             # key/manual 描述词（含胶连形）属于解答后缀
        sol_m = _SOL_TOKEN_RE.search(stem_nokey)
        hw_m = _HW_FILE_RE.search(stem_nokey)
        # 「answer_questions_hw1」的 answer 是动词开头、不是解答记号——文件名里的 sol 记号须在
        # hw 记号【之后】（hw1_sol / homework2solutions），或在其前但两者【紧邻】（solutions_hw1 /
        # 答案_作业3：中间只有分隔符，没有别的词——动词短语 answer_questions_hw1 因此仍归作业），
        # 或由目录名（solutions/）标记
        _between = stem_nokey[sol_m.end():hw_m.start()] if (sol_m and hw_m) else ""
        _btokens = re.findall(r"[A-Za-z一-鿿]+", _between)
        # sol 记号在 hw 记号之前时，中间只允许分隔符或连接词（for/to/of/the：solutions_for_hw1 /
        # answers_to_hw1）——动词短语（answer_questions_hw1 的 questions）仍归作业
        sol_before_adjacent = bool(sol_m and hw_m and sol_m.start() < hw_m.start()
                                   and not re.search(r"[0-9]", _between)
                                   and all(t.lower() in ("for", "to", "of", "the") for t in _btokens))
        def _terminal_solish(m):
            # 后置解答记号必须是「终端形」：其后只剩分隔符/连接词/hw 记号——
            # hw1_answer_questions 的 answer 是动词（后跟宾语 questions），不是解答后缀
            rest_toks = re.findall(r"[A-Za-z一-鿿]+", stem_nokey[m.end():])
            return all(t.lower() in _SOL_TRAIL_OK or _HW_FILE_RE.search(t)
                       for t in rest_toks)
        sol_after_hw = bool(hw_m and any(m.start() > hw_m.start() and _terminal_solish(m)
                                         for m in _SOL_TOKEN_RE.finditer(stem_nokey)))
        # 裸 key/manual 后缀（hw1_key.pdf）：描述词在 hw 记号之后且是终端形——本身就是答案册记号。
        # 注意在【原始 stem】上找（stem_nokey 已剥掉描述词）
        hw_m_raw = _HW_FILE_RE.search(stem)
        if hw_m_raw is None:
            # midterm_key.pdf：试卷记号与 hw 记号同权当锚。这里不做负例否决——
            # midterm_key_review 的 review 否决的是题面卷分类，不否决它是答案册
            hw_m_raw = _EXAM_FILE_RE.search(stem)
        # 试卷上下文来自目录/根（exams/2020_key.pdf、2023期末真题/1_key.pdf）时锚定串首——
        # 试卷语境下键描述词后缀本身就是答案册记号
        _in_exam_ctx = (bool(root_name and _seg_exam(root_name))
                        or any(_seg_exam(sg) for sg in os.path.dirname(rel).split("/") if sg))
        _a0 = 0 if (hw_m_raw is None and (_in_exam_ctx or _is_exam_path(rel, root_name))) else None
        desc_after_hw = bool((hw_m_raw is not None or _a0 is not None) and any(
            (m.start() > hw_m_raw.start() if hw_m_raw is not None else m.start() >= _a0)
            and all(t.lower() in _SOL_TRAIL_OK or _HW_FILE_RE.search(t)
                    for t in re.findall(r"[A-Za-z一-鿿]+", stem[m.end():]))
            for m in _KEY_TOKEN_RE.finditer(stem)))
        sol_after_hw = sol_after_hw or desc_after_hw
        is_sol = bool(any(_sol_dir_segment(seg) for seg in os.path.dirname(rel).split("/"))
                      or (sol_m and not hw_m) or sol_after_hw
                      or sol_before_adjacent)
        if not (_HW_FILE_RE.search(rel) or is_sol or root_is_hw
                or _is_exam_path(rel, root_name)):
            continue                                   # solutions/hw1.pdf：目录名也是 solution 记号
        (sols if is_sol else hw).append(f)
    # 目录感知配对：week1/hw1_sol 只配 week1/hw1（同名跨目录不串）；同目录找不到才允许全局唯一回退
    hw_by_key = {}
    for f in hw:
        rel = f.replace("\\", "/")
        hw_by_key.setdefault((os.path.dirname(rel), _hw_norm(os.path.splitext(os.path.basename(f))[0])),
                             []).append(f)
    pairing = {}
    for sf in sols:
        rel = sf.replace("\\", "/")
        sdir = os.path.dirname(rel)
        stem = _deglue_exam(os.path.splitext(os.path.basename(sf))[0])
        # 配对键要把 解答记号、key/manual 描述词（含胶连形）与连接词（solutions_for_hw1 的 for）一并剥掉
        stem_pair = _SOL_TOKEN_RE.sub("", _strip_sol_desc(stem))
        stem_pair = re.sub(r"(?<![A-Za-z])(?:for|to|of|the)(?=[_. ()-]|$)", "", stem_pair, flags=re.I)
        stripped = _hw_norm(stem_pair)
        # 版本/修订尾缀剥掉后的备选键——先按原键精确配（hw2_v2_sol ↔ hw2_v2 优先），
        # 原键配不上才试去版本键（hw1_solutions_v2 ↔ hw1）
        stripped_ver = _hw_norm(re.sub(
            r"(?i)(?<![0-9A-Za-z])(?:v\d+|ver(?:sion)?|final|rev(?:ised|iewed|iew)?|updated?|new|latest|"
            r"copy|draft|fixed|corrected|修订|最终|更新|终版|新版)(?=[_. ()-]|$)", "", stem_pair))

        sol_sep = _hw_sepform(stem_pair)

        _AMBIG = object()   # 本层有多个候选——必须终止，放宽范围只会配到别人的作业

        def _lookup(dirs):
            for key in ((stripped,) if stripped == stripped_ver else (stripped, stripped_ver)):
                exact = [f for (d, n), fs in hw_by_key.items() if d in dirs and n == key for f in fs]
                if len(exact) == 1:
                    return exact[0]
                if exact:
                    return _AMBIG
            # 前缀回退只允许【作业名延长解答名】方向，且延长处必须是分隔符边界
            # （hw1_probability ← hw1_sol ✓；hw10 数字边界 ✗）——反方向（hw1a_sol/hw1_extra_sol → hw1）
            # 会把别的作业的答案安到 hw1 头上。字母变体（hw1a/hw1b）是另一份作业：本层存在变体即歧义，
            # 就地终止，绝不放宽到别处配错
            cands, variants = [], 0
            for (d, n), fs in hw_by_key.items():
                if d not in dirs or not n or not sol_sep:
                    continue
                for f in fs:
                    fsep = _hw_sepform(os.path.splitext(os.path.basename(f))[0])
                    if fsep.startswith(sol_sep + "_"):
                        cands.append(f)
                    elif fsep and sol_sep.endswith("_" + fsep):
                        # 描述性前缀 + 作业名 + 解答后缀（answer_questions_hw1_sol ↔ hw1）：
                        # 剥完解答记号后作业名是残键的分隔符边界后缀——同层多候选照样歧义终止
                        cands.append(f)
                    elif fsep != sol_sep and fsep.startswith(sol_sep) \
                            and not fsep[len(sol_sep)].isdigit():
                        variants += 1
            if len(cands) == 1 and not variants:
                return cands[0]
            return _AMBIG if (cands or variants) else None
        # 逐级放宽：同目录 → 同父家族（week1/solutions ↔ week1/homework、week1 根）→ 镜像子树
        # （solutions/week1 ↔ homework/week1：去掉第一段后的相对子路径相同）→ 全局唯一。
        # 家族/镜像层让每周各配各的；某层出现歧义（如 week1 同时有 hw1a/hw1b）就地放弃，
        # 绝不落到更大范围去配错 week2 的同名文件
        parent = os.path.dirname(sdir)
        family = {d for (d, _n) in hw_by_key if d == parent or os.path.dirname(d) == parent}

        def _mirror_key(d):
            # 段内剥掉 hw/sol 记号与描述词后拼回（不是整段丢弃）——week1_solutions 与
            # week1_homework 同键 week1，course/homework/week1 与 course/solutions/week1
            # 同键 course/week1；纯记号段（solutions/）归空
            segs = []
            for seg in d.split("/"):
                seg2 = _SOL_TOKEN_RE.sub("", _strip_sol_desc(seg))
                seg2 = _HW_FILE_RE.sub("", seg2)
                seg2 = re.sub(r"[^0-9A-Za-z一-鿿]+", "", seg2)
                segs.append(seg2)
            return "/".join(sg for sg in segs if sg)
        mirror = {d for (d, _n) in hw_by_key
                  if d != sdir and _mirror_key(d) == _mirror_key(sdir)}
        match = None
        ambiguous = False
        for tier in ({sdir}, mirror, family, {d for (d, _n) in hw_by_key}):
            got = _lookup(tier)
            if got is _AMBIG:
                ambiguous = True
                break
            if got is not None:
                match = got
                break
        if ambiguous and notes is not None:
            notes.append(("ambiguous", sf))    # 同层多候选，为防配错放弃——必须留痕
        pairing[sf] = match
    for sf in [k for k, v in pairing.items() if v is None]:
        # 配不上且路径/文件名没有任何作业线索（solutions/ch01.pdf 这类通用解答目录装的
        # 是讲义解答）——从作业管线除名，交还讲义配对，不再据为己有。
        # 根目录本身就是作业文件夹时全部文件都在作业上下文里，不除名（否则根级
        # solutions.pdf 会漏进 wiki 泄答案）
        # 试卷答案册（midterm_solutions / midterm_solutions_review / 期末试卷答案）绝不除名——
        # 除名会让它逃过 wiki 排除、标准答案整册泄进复习材料（审计实测）。这里用【裸试卷记号】
        # 判定（不做负例否决：review 后缀否决的是题面卷分类，不否决它是答案册的事实），
        # 目录/根的试卷上下文同样保留
        sf_rel = sf.replace(chr(92), "/")
        sf_stem = _deglue_exam(os.path.splitext(os.path.basename(sf))[0])
        sf_exam_ctx = (bool(root_name and _seg_exam(root_name))
                       or any(_seg_exam(sg) for sg in os.path.dirname(sf_rel).split("/") if sg))
        if not root_is_hw and not sf_exam_ctx and not _HW_FILE_RE.search(sf_rel) \
                and not _EXAM_FILE_RE.search("/" + sf_stem) \
                and not _EXAMISH_LOOSE_RE.search("/" + sf_stem):
            if notes is not None:
                notes.append(("reassigned", sf))   # 交还讲义管线也要留痕，绝不静默改道
            del pairing[sf]
    return hw, pairing


def _file_stream(pages, f):
    """Concatenate one file's page texts into a single stream + (offset, page_no) bounds table, so a
    problem spanning pages slices naturally."""
    parts, bounds, cur = [], [], 0
    for pg in pages:
        if pg["file"] != f:
            continue
        t = pg.get("text", "") or ""
        bounds.append((cur, pg["page"]))
        parts.append(t)
        cur += len(t) + 1
    return "\n".join(parts), bounds


def _pages_for_span(bounds, a, b):
    """Page numbers whose text overlaps stream span [a, b)."""
    out = []
    for i, (start, pno) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else float("inf")
        if start < b and end > a:
            out.append(pno)
    return out


_HW_ROSTER_LINE_RE = re.compile(
    r"^[ \t]*\d+\s*[.)]\s*(?:Problem|Exercise|Question)\s*#?\s*"
    + _HW_NUM_PAT
    + r"\s*(?:\([^?\n]{0,200}\))?\s*$",
    re.I,
)


def _page_for_stream_offset(bounds, offset):
    """Return the physical page containing a concatenated-file offset."""
    page = bounds[0][1] if bounds else None
    for start, page_number in bounds:
        if start > offset:
            break
        page = page_number
    return page


def _homework_roster(pages, source_file):
    """Detect an enumerated assignment roster, not a page of real prompts.

    A roster has at least two ``1. Problem X.Y``-style one-line entries and at
    least 80 percent of its problem markers are bare list entries.  Requiring a
    list ordinal separate from the textbook problem number prevents ordinary
    ``Problem 1`` worksheets from entering this special route.
    """
    entries = []
    roster_pages = []
    for page in sorted(
            (row for row in pages if row.get("file") == source_file),
            key=lambda row: row.get("page", 0)):
        text = page.get("text") or ""
        markers = [
            marker for marker in _hw_markers(text)
            if marker.get("role") == "problem" and marker.get("num") is not None
        ]
        if len(markers) < 2 or any(
                marker.get("role") == "solution" for marker in _hw_markers(text)):
            continue
        bare = 0
        for marker in markers:
            line_end = text.find("\n", marker["start"])
            if line_end < 0:
                line_end = len(text)
            if _HW_ROSTER_LINE_RE.match(text[marker["start"]:line_end]):
                bare += 1
        if bare < 2 or bare * 5 < len(markers) * 4:
            continue
        roster_pages.append(page["page"])
        entries.extend(marker["num"] for marker in markers)
    if not entries:
        return None
    if len(set(entries)) != len(entries):
        return {
            "status": "review",
            "pages": sorted(set(roster_pages)),
            "entries": entries,
            "reason": "duplicate problem numbers in assignment roster",
        }
    return {
        "status": "detected",
        "pages": sorted(set(roster_pages)),
        "entries": entries,
    }


def _clip_bbox(bbox, page_bbox):
    x0 = max(float(page_bbox[0]), float(bbox[0]))
    y0 = max(float(page_bbox[1]), float(bbox[1]))
    x1 = min(float(page_bbox[2]), float(bbox[2]))
    y1 = min(float(page_bbox[3]), float(bbox[3]))
    return [x0, y0, x1, y1] if x1 > x0 and y1 > y0 else None


def _bbox_union(boxes):
    return [
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    ]


def _boxes_connected(left, right, gap):
    horizontal_gap = max(0.0, max(left[0] - right[2], right[0] - left[2]))
    vertical_gap = max(0.0, max(left[1] - right[3], right[1] - left[3]))
    horizontal_overlap = min(left[2], right[2]) >= max(left[0], right[0])
    vertical_overlap = min(left[3], right[3]) >= max(left[1], right[1])
    return ((horizontal_gap <= gap and vertical_overlap)
            or (vertical_gap <= gap and horizontal_overlap))


def _prompt_image_components(layout):
    """Return wide-image components after excluding repeated page motifs."""
    if not isinstance(layout, dict):
        return None
    page_bbox = layout.get("page_bbox")
    if not (isinstance(page_bbox, (list, tuple)) and len(page_bbox) == 4):
        return None
    width = float(page_bbox[2]) - float(page_bbox[0])
    height = float(page_bbox[3]) - float(page_bbox[1])
    if width <= 0 or height <= 0:
        return None
    grouped = {}
    for image in layout.get("images") or ():
        if not isinstance(image, dict):
            continue
        clipped = _clip_bbox(image.get("bbox") or (), page_bbox) \
            if len(image.get("bbox") or ()) == 4 else None
        if clipped is not None:
            grouped.setdefault(str(image.get("image_id")), []).append(clipped)
    eligible = []
    for boxes in grouped.values():
        # Three or more placements of one image are a repeated background or
        # answer-area template, not independently evidenced prompt content.
        if len(boxes) >= 3:
            continue
        for box in boxes:
            box_width = box[2] - box[0]
            box_height = box[3] - box[1]
            area_ratio = box_width * box_height / (width * height)
            if box_width >= 0.25 * width and 0.005 <= area_ratio <= 0.80:
                eligible.append(box)
    gap = max(8.0, 0.02 * height)
    components = []
    for box in eligible:
        touching = [
            index for index, component in enumerate(components)
            if _boxes_connected(component, box, gap)
        ]
        if not touching:
            components.append(list(box))
            continue
        base = touching[0]
        components[base] = _bbox_union((components[base], box))
        for index in reversed(touching[1:]):
            components[base] = _bbox_union((components[base], components.pop(index)))
    return list(page_bbox), width, height, components


def _prompt_image_cluster(layout):
    """Find one conservative upper-page prompt image cluster.

    Repeated image motifs (tiled paper backgrounds and repeated answer-table
    templates) are excluded.  The accepted cluster must be wide, start near
    the top, and leave enough page area that it cannot be a whole-page scan
    containing both prompt and handwritten answer.
    """
    facts = _prompt_image_components(layout)
    if facts is None:
        return None
    page_bbox, width, height, components = facts
    candidates = []
    for component in components:
        component_width = component[2] - component[0]
        component_height = component[3] - component[1]
        area_ratio = component_width * component_height / (width * height)
        if (component_width >= 0.55 * width
                and component[1] <= float(page_bbox[1]) + 0.25 * height
                and area_ratio <= 0.80
                and component[3] <= float(page_bbox[1]) + 0.90 * height):
            candidates.append(component)
    if not candidates:
        return None
    return min(candidates, key=lambda box: (box[1], -(box[2] - box[0])))


def _inset_bbox(bbox, inset):
    """Inset all sides of a PDF-point rectangle, or return no usable interior."""
    inner = [
        float(bbox[0]) + float(inset), float(bbox[1]) + float(inset),
        float(bbox[2]) - float(inset), float(bbox[3]) - float(inset),
    ]
    return inner if inner[2] > inner[0] and inner[3] > inner[1] else None


def _expanded_prompt_image_clusters(layout, text_inset=8.0):
    """Find roster-only prompt candidates at any vertical page position.

    This deliberately returns *all* candidates so the caller can require both
    exact roster cardinality and at most one candidate on each page.  A native
    text box intersecting the candidate's 8-point inset interior rejects it;
    the inset tolerates harmless PDF extraction noise that only touches a crop
    boundary while still failing closed on text/handwriting inside the raster.
    """
    facts = _prompt_image_components(layout)
    if facts is None:
        return []
    unused_page_bbox, width, height, components = facts
    page_area = width * height
    candidates = []
    for component in components:
        component_width = component[2] - component[0]
        component_height = component[3] - component[1]
        area_ratio = component_width * component_height / page_area
        if component_width < 0.55 * width or area_ratio > 0.80:
            continue
        interior = _inset_bbox(component, text_inset)
        if interior is None or _bbox_has_text(layout, interior):
            continue
        candidates.append(component)
    return sorted(candidates, key=lambda box: (box[1], box[0], box[3], box[2]))


def _bbox_has_text(layout, bbox):
    for raw in (layout or {}).get("text_boxes") or ():
        if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
            continue
        if (min(float(raw[2]), float(bbox[2])) > max(float(raw[0]), float(bbox[0]))
                and min(float(raw[3]), float(bbox[3])) > max(float(raw[1]), float(bbox[1]))):
            return True
    return False


def _layout_has_scan_evidence(layout):
    """Return whether image geometry makes extracted text unsafe by itself.

    A PDF scan can carry an OCR text layer containing both the printed prompt
    and an unlabeled handwritten response.  Matching problem numbers in that
    text layer therefore do not prove prompt/answer separation.  One large
    image, or images covering most of the page in aggregate, is enough to keep
    a roster file on the visual/fail-closed route.
    """
    if not isinstance(layout, dict):
        return False
    page_bbox = layout.get("page_bbox")
    if not (isinstance(page_bbox, (list, tuple)) and len(page_bbox) == 4):
        return False
    page_area = ((float(page_bbox[2]) - float(page_bbox[0]))
                 * (float(page_bbox[3]) - float(page_bbox[1])))
    if page_area <= 0:
        return False
    image_areas = []
    for image in layout.get("images") or ():
        raw = image.get("bbox") if isinstance(image, dict) else None
        clipped = _clip_bbox(raw or (), page_bbox) if len(raw or ()) == 4 else None
        if clipped is not None:
            image_areas.append(
                (clipped[2] - clipped[0]) * (clipped[3] - clipped[1])
            )
    return bool(image_areas) and (
        max(image_areas) >= 0.40 * page_area
        or sum(image_areas) >= 0.65 * page_area
    )


def _layout_marker_rows(layout):
    """Return item-heading rows with their conservative vertical bounds."""

    rows = []
    for block in (layout or {}).get("text_blocks") or ():
        if not isinstance(block, dict):
            continue
        bbox = block.get("bbox")
        text = block.get("text")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                and isinstance(text, str) and text.strip()):
            continue
        for marker in _hw_markers(text):
            if marker.get("num") is None or marker.get("continued"):
                continue
            heading = text[marker["start"]:marker["start"] + 180]
            role = marker.get("role")
            if re.search(r"\b(?:solutions?|answers?)\b|解答|答案", heading, re.I):
                role = "solution"
            rows.append({
                "num": marker["num"],
                "role": role,
                "bbox": [float(value) for value in bbox],
            })
    return sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))


def _target_item_page_crop(layout, item_number, *, continuation=False,
                           margin=8.0):
    """Infer a target-only vertical crop from answer-heading layout.

    A page in an already parsed answer span belongs to the target item.  Its
    first page must expose the target heading; a continuation page may start at
    the page top.  The first different item heading ends the crop.  This is the
    deterministic counterpart to a model/human CropAnnotation and never
    silently selects a different item.
    """

    page_box = (layout or {}).get("page_bbox")
    if not (isinstance(page_box, (list, tuple)) and len(page_box) == 4):
        return None
    markers = _layout_marker_rows(layout)
    target = [row for row in markers if _hw_num(row["num"]) == _hw_num(item_number)]
    if target:
        start = min(row["bbox"][1] for row in target)
    elif continuation:
        start = float(page_box[1])
    else:
        return None
    later_other = [
        row["bbox"][1] for row in markers
        if _hw_num(row["num"]) != _hw_num(item_number)
        and row["bbox"][1] > start + 1.0
    ]
    end = min(later_other) if later_other else float(page_box[3])
    top = max(float(page_box[1]), start - margin)
    bottom = min(float(page_box[3]), end - margin)
    if bottom <= top + 12.0:
        return None
    return [float(page_box[0]), top, float(page_box[2]), bottom]


def _homework_answer_crop_plan(pages, answer, item_number, backend):
    """Return one strict crop per parsed answer page, or ``None`` for review."""

    if not answer or not answer[2]:
        return None
    layout_reader = getattr(backend, "page_layout", None)
    clip_renderer = getattr(backend, "render_page_clip_png", None)
    if not callable(layout_reader) or not callable(clip_renderer):
        return None
    source_file = answer[0]
    page_rows = {
        row.get("page"): row for row in pages if row.get("file") == source_file
    }
    crops = []
    try:
        for position, page_number in enumerate(answer[2]):
            page_row = page_rows.get(page_number)
            pdf_path = page_row.get("_pdf") if isinstance(page_row, dict) else None
            if not pdf_path:
                return None
            layout = layout_reader(pdf_path, page_number - 1)
            bbox = _target_item_page_crop(
                layout, item_number, continuation=position > 0,
            )
            if bbox is None:
                return None
            crops.append((source_file, page_number, bbox))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return crops


def _layout_lecture_marker_rows(layout):
    rows = []
    for block in (layout or {}).get("text_blocks") or ():
        if not isinstance(block, dict):
            continue
        bbox = block.get("bbox")
        text = block.get("text")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                and isinstance(text, str) and text.strip()):
            continue
        for marker in _iter_markers(text):
            rows.append({
                "key": _key(marker),
                "role": marker.get("role"),
                "bbox": [float(value) for value in bbox],
            })
    return sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))


def _lecture_item_crop_plan(pages, page_indexes, item_key, backend, role):
    if not page_indexes:
        return None
    layout_reader = getattr(backend, "page_layout", None)
    clip_renderer = getattr(backend, "render_page_clip_png", None)
    if not callable(layout_reader) or not callable(clip_renderer):
        return None
    crops = []
    try:
        for position, page_index in enumerate(page_indexes):
            page_row = pages[page_index]
            pdf_path = page_row.get("_pdf")
            if not pdf_path:
                return None
            layout = layout_reader(pdf_path, page_row["page"] - 1)
            page_box = layout.get("page_bbox") if isinstance(layout, dict) else None
            if not (isinstance(page_box, (list, tuple)) and len(page_box) == 4):
                return None
            markers = _layout_lecture_marker_rows(layout)
            target = [row for row in markers
                      if row["key"] == item_key and row["role"] == role]
            if target:
                start = min(row["bbox"][1] for row in target)
            elif position > 0:
                start = float(page_box[1])
            else:
                return None
            later = [row["bbox"][1] for row in markers
                     if row["key"] != item_key and row["bbox"][1] > start + 1.0]
            end = min(later) if later else float(page_box[3])
            bbox = [float(page_box[0]), max(float(page_box[1]), start - 8.0),
                    float(page_box[2]), min(float(page_box[3]), end - 8.0)]
            if bbox[3] <= bbox[1] + 12.0:
                return None
            crops.append((page_row["file"], page_row["page"], bbox))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return crops


def _homework_roster_route(pages, source_file, backend):
    """Resolve a roster to text prompts, safe image crops, or typed review."""
    roster = _homework_roster(pages, source_file)
    if roster is None:
        return None
    if roster.get("status") == "review":
        return roster
    roster_pages = set(roster["pages"])
    source_pages = sorted(
        (row for row in pages if row.get("file") == source_file),
        key=lambda row: row.get("page", 0),
    )
    non_roster_numbers = []
    for page in source_pages:
        if page.get("page") in roster_pages:
            continue
        non_roster_numbers.extend(
            marker["num"] for marker in _hw_markers(page.get("text") or "")
            if marker.get("role") == "problem" and marker.get("num") is not None
        )
    # Equality must include cardinality and order.  Set equality hid duplicate
    # OCR markers and allowed a different page sequence to masquerade as a
    # one-to-one roster mapping.
    text_markers_match = non_roster_numbers == roster["entries"]
    if non_roster_numbers and not text_markers_match:
        return {
            "status": "review",
            "pages": [page["page"] for page in source_pages],
            "entries": roster["entries"],
            "reason": "only some roster problems have independent text markers",
        }
    layout_reader = getattr(backend, "page_layout", None)
    clip_renderer = getattr(backend, "render_page_clip_png", None)
    if not callable(layout_reader):
        return {
            "status": "review",
            "pages": [page["page"] for page in source_pages],
            "entries": roster["entries"],
            "reason": (
                "PDF layout/crop capability is unavailable, so matching OCR/text "
                "markers cannot prove that handwritten answers are absent"
            ),
        }
    layouts = {}
    conservative_anchors = []
    expanded_by_page = {}
    scan_evidence = False
    scan_pages = []
    try:
        for page in source_pages:
            if page.get("page") in roster_pages:
                continue
            pdf_path = page.get("_pdf")
            if not pdf_path:
                raise ValueError("source page lacks its PDF revision path")
            layout = layout_reader(pdf_path, page["page"] - 1)
            if not isinstance(layout, dict):
                raise ValueError("layout backend returned no page facts")
            layouts[page["page"]] = layout
            if _layout_has_scan_evidence(layout):
                scan_evidence = True
                scan_pages.append(page["page"])
            crop = _prompt_image_cluster(layout)
            if crop is not None:
                scan_evidence = True
                scan_pages.append(page["page"])
                conservative_anchors.append({
                    "page": page["page"], "crop": crop,
                    "page_bbox": list(layout["page_bbox"]),
                })
            expanded_by_page[page["page"]] = _expanded_prompt_image_clusters(layout)
    except Exception as exc:
        return {
            "status": "review",
            "pages": [page["page"] for page in source_pages],
            "entries": roster["entries"],
            "reason": "layout inspection failed: %s" % exc,
        }
    geometry_route = "conservative_upper_page"
    anchors = conservative_anchors
    if len(conservative_anchors) != len(roster["entries"]):
        expanded_anchors = []
        ambiguous_pages = []
        for page in source_pages:
            if page.get("page") in roster_pages:
                continue
            candidates = expanded_by_page.get(page["page"]) or []
            if len(candidates) > 1:
                ambiguous_pages.append((page["page"], len(candidates)))
                continue
            if len(candidates) == 1:
                expanded_anchors.append({
                    "page": page["page"], "crop": candidates[0],
                    "page_bbox": list(layouts[page["page"]]["page_bbox"]),
                })
        expanded_pages = [anchor["page"] for anchor in expanded_anchors]
        if expanded_pages or ambiguous_pages:
            scan_evidence = True
            scan_pages.extend(expanded_pages)
            scan_pages.extend(page for page, unused_count in ambiguous_pages)
        if ambiguous_pages:
            return {
                "status": "review",
                "pages": sorted(set(
                    roster["pages"] + scan_pages
                    + [page for page, unused_count in ambiguous_pages]
                )),
                "entries": roster["entries"],
                "reason": (
                    "expanded roster fallback requires at most one prompt image "
                    "candidate per page after the 8-point inset native-text gate; "
                    "found %s"
                ) % ", ".join(
                    "page %d=%d" % pair for pair in ambiguous_pages
                ),
            }
        if len(expanded_anchors) == len(roster["entries"]):
            anchors = expanded_anchors
            geometry_route = "expanded_roster_fallback"
    # Native text pages may use the ordinary slicer.  OCR text over a scan is
    # categorically different: it can contain an unlabeled handwritten answer,
    # so its matching problem-number set is only a routing hint, never proof.
    if text_markers_match and not scan_evidence:
        return {
            "status": "text", "pages": roster["pages"],
            "entries": roster["entries"],
        }
    if scan_evidence and not callable(clip_renderer):
        return {
            "status": "review",
            "pages": sorted(set(roster["pages"] + scan_pages)),
            "entries": roster["entries"],
            "reason": (
                "scan/layout evidence makes OCR text unsafe and prompt-only "
                "crop rendering is unavailable"
            ),
        }
    if len(anchors) != len(roster["entries"]):
        return {
            "status": "review",
            "pages": sorted(set(
                roster["pages"] + scan_pages + [row["page"] for row in anchors]
            )),
            "entries": roster["entries"],
            "reason": (
                "roster has %d problems but prompt-only image anchors do not "
                "match: the conservative upper-page rule proves %d anchors and "
                "the expanded roster fallback proves %d single-candidate anchors "
                "after the 8-point inset native-text gate"
            ) % (
                len(roster["entries"]), len(conservative_anchors),
                len(expanded_anchors),
            ),
        }
    page_positions = {page["page"]: index for index, page in enumerate(source_pages)}
    plans = {}
    for index, (number, anchor) in enumerate(zip(roster["entries"], anchors)):
        start_index = page_positions[anchor["page"]]
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        stop_index = (page_positions[next_anchor["page"]]
                      if next_anchor is not None else len(source_pages))
        covered_pages = [
            page["page"] for page in source_pages[start_index:stop_index]
            if page["page"] not in roster_pages
        ]
        answer_crops = []
        page_bbox = anchor["page_bbox"]
        if float(page_bbox[3]) - float(anchor["crop"][3]) >= 12.0:
            answer_crops.append((
                source_file, anchor["page"],
                [page_bbox[0], anchor["crop"][3], page_bbox[2], page_bbox[3]],
            ))
        for page in source_pages[start_index + 1:stop_index]:
            if page["page"] in roster_pages:
                continue
            current_bbox = layouts[page["page"]]["page_bbox"]
            answer_crops.append((source_file, page["page"], list(current_bbox)))
        if next_anchor is not None:
            top_crop = [
                next_anchor["page_bbox"][0], next_anchor["page_bbox"][1],
                next_anchor["page_bbox"][2], next_anchor["crop"][1],
            ]
            if ((top_crop[3] - top_crop[1]) >= 12.0
                    and _bbox_has_text(layouts[next_anchor["page"]], top_crop)):
                answer_crops.append((source_file, next_anchor["page"], top_crop))
                covered_pages.append(next_anchor["page"])
        plans[number] = {
            # Prompt provenance names only pages that contribute question-side
            # pixels.  Handwriting continuations belong exclusively to the
            # answer-side crop list below.
            "source_pages": [anchor["page"]],
            "question_crops": [(source_file, anchor["page"], anchor["crop"])],
            "answer_crops": answer_crops,
            "anchor_page": anchor["page"],
        }
    return {
        "status": "visual", "pages": roster["pages"],
        "entries": roster["entries"], "plans": plans,
        "geometry_route": geometry_route,
    }


def _chapter_from_homework_number(number):
    """Infer chapter only from a textbook-style dotted problem number."""
    match = re.match(r"^(\d+)\.(?:\d+)(?:\.|$)", str(number or ""))
    return int(match.group(1)) if match else None


def _hw_num(s):
    """Normalize integer or string problem numbers through one contract.

    Layout markers may already carry an integer, while text-facing callers use
    strings.  In both cases 1.1 ≠ 1 and 1(a) is equivalent to 1a.
    """
    if s is None:
        return None
    s = str(s)
    s = re.sub(r"[()\s]", "", s).lower()
    return int(s) if s.isdigit() else s


def _hw_line(stream, m):
    nl = stream.find("\n", m.end())
    return stream[m.start():(nl if nl >= 0 else len(stream))]


def _hw_markers(stream):
    """All problem/inline-solution markers in stream order: {start, num|None, role, continued}."""
    marks = []
    for rx in _HW_PROB_RES:
        for m in rx.finditer(stream):
            num = next((g for g in m.groups() if g), None)
            line = _hw_line(stream, m)
            if _TOC_RE.search(line[:300]):
                continue
            # 角色词只看标题【同一行】号后的文字——下一行以 Answer 开头的题面（"Problem 1\nAnswer the
            # following…"）绝不能把题目翻成解答段
            tail = line[m.end() - m.start():][:48]
            continued = bool(re.search(r"continued|[（(]\s*续", tail, re.I))
            # 「Problem 1 Solution」是解答段标题不是新题——要求号后剩余整行就是 解答/答案 标记
            #（先剥掉 continued 记号；行尾锚定，「: Answer the following…」这类题面动词不受影响）
            tail_role = re.sub(r"^\s*\(?\s*continued\b\s*\d*\s*\)?[\s:.\-]*", "", tail, flags=re.I)
            hard_sol = bool(_HW_SOL_HEAD_RE.match(tail) or _HW_SOL_HEAD_RE.match(tail_role))
            colon_lead = tail.lstrip()[:1] in (":", "：")
            content_sol = bool(_HW_SOL_HEAD_CONTENT_RE.match(tail)
                               or _HW_SOL_HEAD_CONTENT_RE.match(tail_role))
            role = "solution" if (hard_sol or content_sol) else "problem"
            mk_new = {"start": m.start(), "num": _hw_num(num), "role": role, "continued": continued}
            if role == "solution" and content_sol and not hard_sol and colon_lead:
                # 「Problem 1: Answer: …」既可能是解答段也可能是答题栏指示——只有当同号题面
                # 已在前面出现过（这是它的解答）才翻转；首现保持题面（后置解析阶段裁决）
                mk_new["_colon_sol"] = True
                mk_new["role"] = "problem"
            marks.append(mk_new)
    for m in _HW_SOL_PRE_RE.finditer(stream):  # 「1a. Answer:」——先收带号前缀形式（同起点时它带号获胜）
        if _TOC_RE.search(_hw_line(stream, m)[:300]):
            continue
        marks.append({"start": m.start(), "num": _hw_num(m.group(1)), "role": "solution", "continued": False})
    for m in _HW_SOL_RE.finditer(stream):
        if _TOC_RE.search(_hw_line(stream, m)[:300]):
            continue                           # 「1. Answer ........ 5」目录行不是答案
        num = next((g for g in m.groups() if g), None)
        if num is None:                        # 「1. Answer: …」——编号在标记前、被 _HEAD 吞掉，从前缀找回
            pm = _SOL_PREFIX_NUM_RE.match(m.group(0))
            if pm:
                num = pm.group(1)
        marks.append({"start": m.start(), "num": _hw_num(num), "role": "solution", "continued": False})
    marks.sort(key=lambda d: d["start"])
    seen_probs = set()
    for mk in marks:
        if mk.get("_colon_sol"):
            if mk["num"] in seen_probs:
                mk["role"] = "solution"    # 同号题面已在前——这是它的解答段
            # 否则保持题面（答题栏指示语场景）
        if mk["role"] == "problem" and mk["num"] is not None and not mk.get("continued"):
            seen_probs.add(mk["num"])
    # de-dup identical (start) collisions (EN/CN patterns can't overlap, but be safe)
    dedup, seen = [], set()
    for mk in marks:
        if mk["start"] in seen:
            continue
        seen.add(mk["start"])
        dedup.append(mk)
    return dedup



def _hw_blank_line(line):
    """A worksheet BLANK line: nothing but filler（下划线/点/破折号）且至少 3 个连续填充符——
    图表残渣里的单个 '-'/'.' 不是填空线，不能拿它否掉真实解答。
    前导下划线填空后跟评分/指示标注（________ (5 pts) / show your work）同样是空栏。"""
    if re.match(r"^[\s:：]*[_＿]{3,}", line):
        return True
    content = re.sub(r"[（(][^（）()]{0,24}[)）]", "", line)   # 尾随 (5 pts) 类短标注不算内容
    content = re.sub(r"[\s:：]+", "", content)
    return bool(re.fullmatch(r"[_＿.．。…\-—]{3,}", content))


def _hw_nonblank_slice(stream, bounds, fname, s_start, s_end):
    """Answer slice unless it's a worksheet blank（Answer: ______）。判定看标记【同一行】：
    标记后同行是可见填空线 → 整段拒绝——哪怕后面还有「Show your work」这类指示语；
    标记后同行为空 → 看后续第一条非空行（多行空栏同理）。独立解答文件与同文件切片共用
    这一判定，空白答卷绝不落成官方答案。"""
    a_body = stream[s_start:s_end].strip()
    first, _, rest = a_body.partition(chr(10))
    line_rest = re.sub(_HW_SOL_PRE_RE, "", first, count=1)   # 「1(a). Answer:」带号前缀也要剥掉
    if line_rest == first:
        line_rest = re.sub(_HW_SOL_RE, "", first, count=1)
    if line_rest == first:
        # 「Problem 1 Solution: ________」——题号+解答词的复合标题也要剥掉才能看清填空线
        m0 = next((mm for mm in (rx.match(first) for rx in _HW_PROB_RES) if mm), None)
        if m0:
            m1 = _HW_PROB_SOL_HEAD_RE.match(first[m0.end():])
            if m1:
                line_rest = first[m0.end() + m1.end():]
    if line_rest == first:
        # 键控答案行「1. ________」——裸编号键不是内容，剥掉才能看清填空线
        #（「1. 4」这类真实数值答案剥键后仍有内容，不受影响）
        # 小数-空白分支放前——定界分支的 [ \t]* 会回溯成「1.」抢走「1.1 ____」的匹配
        km = re.match(r"^[ \t]*(?:\d+(?:\.\d+)+[ \t]+"
                      r"|\d+(?:\.\d+)*(?:[ \t]*\([A-Za-z]\)|[A-Za-z])?[.)、][ \t]*)", first)
        if km:
            line_rest = first[km.end():]
    if line_rest.strip() and _hw_blank_line(line_rest):
        return None                        # 同行是填空线——worksheet 空栏，不是答案
    a_tail = rest if rest else line_rest
    # 键控空栏册（1. ________ 换行 2. ____）——逐行剥裸编号键后再判空白/内容，键号本身不是答案
    a_eval = re.sub(r"(?m)^[ \t]*(?:\d+(?:\.\d+)+[ \t]+"
                    r"|\d+(?:\.\d+)*(?:[ \t]*\([A-Za-z]\)|[A-Za-z])?[.)、][ \t]*)", "", a_tail)
    first_content = next((ln for ln in a_eval.splitlines() if ln.strip()), "")
    if first_content and _hw_blank_line(first_content):
        return None                        # 多行空栏（Answer: 换行后接填空线与指示语）同理
    if re.sub(r"[_\s.．。:：…\-—＿]+", "", a_eval):
        return (fname, a_body, _pages_for_span(bounds, s_start, s_end))
    return None


def extract_homework_items(pages, root_name="", exclude=frozenset(), backend=None):
    """Extract homework problems (+ answers from paired solution files OR inline Solution blocks)
    into bank items with source_type='homework'. Returns (items, hw_report)."""
    files = sorted({pg["file"] for pg in pages})
    is_pdf = {f: any(pg.get("_pdf") for pg in pages if pg["file"] == f) for f in files}
    _cls_notes = []
    hw_files, pairing = classify_homework_files(files, root_name, _cls_notes)
    if exclude:
        # 已归还讲义管线的讲义式试卷文件——这边绝不再当题面册解析（防双吃/答案卷进题面）；
        # 指向它们的配对项一并摘除（解答册自会走 unpaired 警告，不静默）
        hw_files = [f for f in hw_files if f not in exclude]
        pairing = {sf: (None if hf in exclude else hf) for sf, hf in pairing.items()
                   if sf not in exclude}
    exam_files = {f for f in hw_files if _is_exam_path(f, root_name)}
    _note_msgs = []
    for _kind, _sf in _cls_notes:
        if _kind == "ambiguous":
            _note_msgs.append("hw_pairing_ambiguous: %s（同层多个候选作业，为防配错放弃自动配对——"
                              "请人工指认或重命名后重建）" % _sf)
        elif _kind == "reassigned":
            _note_msgs.append("hw_solution_reassigned_to_lecture: %s（配不上作业且无作业/试卷线索——"
                              "按讲义解答交还讲义配对；若它其实是作业答案册请重命名带 hw/sol 记号）" % _sf)
    report = {"exam_files": sorted(exam_files),
              "homework_files": hw_files,
              "homework_solution_files": sorted(pairing),
              "homework_pairs": sorted([s, h] for s, h in pairing.items() if h),
              "homework_problems": 0, "homework_answered": 0,
              "homework_roster_files": 0, "homework_prompt_crops": 0,
              "homework_answer_crops": 0, "ai_review": [], "warnings": _note_msgs}
    for sf, h in sorted(pairing.items()):
        if h is not None:
            continue
        # 自含题面+解答的 solutions 册（常见 LMS 导出只有 hw1_solutions.pdf 一个文件）——
        # 有题面标记且有解答标记就按作业文件解析（inline/尾部解答照常配对），不再整册丢弃；
        # 只有题面标记的孤儿答案册仍拒导入（把答案文本当题目会污染题库）
        _st, _b = _file_stream(pages, sf)
        _mks = _hw_markers(_st)

        def _has_prompt_text():
            # 至少一道题在【解答标记之前】有真实题面文字（标题同行的题面也算——与常规抽取同口径）
            # ——否则这是纯答案册（marker-only 题会把解答页渲染成 question_context，提问前泄答案）
            for _j, _m in enumerate(_mks):
                if _m["role"] != "problem":
                    continue
                _end = _mks[_j + 1]["start"] if _j + 1 < len(_mks) else len(_st)
                _body = _st[_m["start"]:_end]
                _first, _, _rest = _body.partition("\n")
                _m0 = next((mm for mm in (rx.match(_first) for rx in _HW_PROB_RES) if mm), None)
                _same = _first[_m0.end():] if _m0 else ""
                _txt = _same + " " + _rest
                if re.search(r"[0-9A-Za-z一-鿿]", _txt) and re.search(
                        r"[A-Za-z一-鿿+*/=^%<>?？()（）-]", _txt):
                    return True                        # 2+2=? 这类符号题面与 marker_only 同口径
            return False
        if any(m["role"] == "problem" for m in _mks) and any(m["role"] == "solution" for m in _mks) \
                and _has_prompt_text():
            hw_files.append(sf)
            report["homework_files"] = sorted(set(report["homework_files"]) | {sf})
            _sf_rel = sf.replace(chr(92), "/")
            if (_is_exam_path(sf, root_name)
                    or bool(root_name and _seg_exam(root_name))
                    or any(_seg_exam(sg) for sg in os.path.dirname(_sf_rel).split("/") if sg)):
                exam_files.add(sf)                     # 只发布了带解答试卷时它就是唯一试卷来源；
                report["exam_files"] = sorted(exam_files)   # 试卷根/目录里的 generic 自含册同权
            report["warnings"].append("hw_selfcontained_solutions: %s（未配对但自含题面+解答，按作业解析）" % sf)
        else:
            report["warnings"].append("hw_unpaired_solution_file: %s（配不到对应作业题面文件，未导入答案）" % sf)

    # answers available per (hw_file, num) from paired solution files
    sol_answers = {}
    hw_nums_cache = {}

    def _hw_prob_nums(hf0):
        # 配对作业的已知题号集合（惰性解析缓存）——单条键控行是否按键拆要对照它
        if hf0 not in hw_nums_cache:
            st0, _b0 = _file_stream(pages, hf0)
            hw_nums_cache[hf0] = {m["num"] for m in _hw_markers(st0)
                                  if m["role"] == "problem" and m["num"] is not None}
        return hw_nums_cache[hf0]
    for sf, hf in pairing.items():
        if hf is None:
            continue
        stream, bounds = _file_stream(pages, sf)
        marks_all = _hw_markers(stream)
        # 独立解答册常见排版「Problem 1 复述 → 无号 Solution → 真解答」：无号解答段继承前一个
        # 带号题目的题号——否则被过滤后整段并进题面复述切片，真解答被埋没
        # 全无标记的纯编号答案册（1. A1 / 2. A2，连 Answers/Problem 标题都没有）——
        # 文件名配对已锁定伴随关系，整册按号拆
        if not marks_all:
            keyed_ms = _keyline_filter(_HW_KEYLINE_RE.finditer(stream), _hw_prob_nums(hf))
            keyset0 = {_keyline_num(m2) for m2 in keyed_ms}
            # 拆分条件：号集 ⊆ 已知题号（单条键可拆），或命中 ≥2 个已知题号（真键清单——
            # 尾随「3. Grading note」注记不打碎拆分）；只命中 1 个的编号解答步骤仍不拆
            if keyset0 and (keyset0 <= _hw_prob_nums(hf)
                            or len(keyset0 & _hw_prob_nums(hf)) >= 2):
                # 分段边界只用题号条目——界外编号行（前一题解答里的编号步骤/注记）随前一条
                # 答案保留，绝不当边界把官方解答截断
                keyed_ms = [m2 for m2 in keyed_ms if _keyline_num(m2) in _hw_prob_nums(hf)]
                for x, m2 in enumerate(keyed_ms):
                    seg_end = keyed_ms[x + 1].start() if x + 1 < len(keyed_ms) else len(stream)
                    numk = _keyline_num(m2)
                    if (hf, numk) in sol_answers:
                        continue
                    got0 = _hw_nonblank_slice(stream, bounds, sf, m2.start(), seg_end)
                    if got0:
                        sol_answers[(hf, numk)] = got0 + ("solution",)
        # 无号「Answers」节 + 编号行（1. A1 / 2. A2）——与作业内联同规，按号拆给各题
        for m in marks_all:
            if m["role"] != "solution" or m["num"] is not None:
                continue
            end0 = next((m2["start"] for m2 in marks_all if m2["start"] > m["start"]), len(stream))
            seg = stream[m["start"]:end0]
            keyed_ms = _keyline_filter(_HW_KEYLINE_RE.finditer(seg), _hw_prob_nums(hf))
            keyset0 = {_keyline_num(m2) for m2 in keyed_ms}
            # 拆分条件：号集 ⊆ 已知题号，或命中 ≥2 个已知题号（尾随注记不打碎拆分）；
            # 只命中 1 个的编号解答步骤仍不拆
            if not (keyset0 and (keyset0 <= _hw_prob_nums(hf)
                                 or len(keyset0 & _hw_prob_nums(hf)) >= 2)):
                continue
            m["_section"] = True       # 已按号拆分的节头——继承不得再把它归给上一题
            # 分段边界只用题号条目——界外编号行随前一条答案保留，不截断官方解答
            keyed_ms = [m2 for m2 in keyed_ms if _keyline_num(m2) in _hw_prob_nums(hf)]
            for x, m2 in enumerate(keyed_ms):
                seg_end = keyed_ms[x + 1].start() if x + 1 < len(keyed_ms) else len(seg)
                numk = _keyline_num(m2)
                key = (hf, numk)
                if key in sol_answers:
                    continue
                got0 = _hw_nonblank_slice(stream, bounds, sf, m["start"] + m2.start(),
                                          m["start"] + seg_end)
                if got0:
                    sol_answers[key] = got0 + ("solution",)
        # 节标题位置要在继承改写 num 之前先记下（无号 solution 标记就是 Solutions/Answers 标题行）
        header_starts = [m["start"] for m in marks_all
                         if m["role"] == "solution" and m["num"] is None]
        first_seen = {}
        for m in marks_all:
            if m["role"] == "problem" and m["num"] is not None and not m.get("continued") \
                    and m["num"] not in first_seen:
                first_seen[m["num"]] = m["start"]
        if first_seen and header_starts:
            tail_head = min(h for h in header_starts) if header_starts else None
            last_first = max(first_seen.values())
            heads_after = [h for h in header_starts if h > last_first]
            if heads_after:
                sec_start = min(heads_after)
                for m in marks_all:
                    if m["role"] == "problem" and m["num"] is not None \
                            and m["start"] > sec_start and m["num"] in first_seen \
                            and m["start"] > first_seen[m["num"]]:
                        m["role"] = "solution"   # 带标题解答区里的重复题号标题＝该题解答段
        last_num = None
        for m in marks_all:
            if m["role"] == "problem" and m["num"] is not None:
                last_num = m["num"]
            elif m["role"] == "solution" and m["num"] is None and last_num is not None \
                    and not m.get("_section"):
                m["num"] = last_num
        # (Continued) 标记是上一段的续页——切片要越过它，解答的后续页并入前一切片
        marks = [m for m in marks_all if m["num"] is not None and not m.get("continued")]
        sol_nums = {m["num"] for m in marks if m["role"] == "solution"}
        for i, mk in enumerate(marks):
            end = marks[i + 1]["start"] if i + 1 < len(marks) else len(stream)
            # 独立解答册的切片同样过 worksheet 空白判定——空白答卷（Answer 1: ______）
            # 绝不落成官方答案，让题目如实 answer_status=unknown
            got = _hw_nonblank_slice(stream, bounds, sf, mk["start"], end)
            if got is None:
                continue
            if mk["role"] == "problem":
                if mk["num"] in sol_nums:
                    continue           # 同号有 Answer/Solution 标记：答案以它为准——
                                       # 它若是空白填空，题目就该如实 unknown，不能拿题面复述顶包
                bfirst, _, brest = got[1].partition(chr(10))
                bm0 = next((mm for mm in (rx.match(bfirst) for rx in _HW_PROB_RES) if mm), None)
                bsame = bfirst[bm0.end():] if bm0 else bfirst
                leftover = re.sub(r"[_" + chr(92) + r"s.．。:：…" + chr(92) + r"-—＿]+", "",
                                  bsame + brest)
                if not leftover:
                    continue           # 光秃的「Problem 1」标题不是复述兜底——空白答卷保持 unknown；
                                       # 数字/符号答案（4、π/2）有剩余内容，照常保留
            key = (hf, mk["num"])
            prev = sol_answers.get(key)
            # 同号既有 Problem 复述又有 Answer 段时，答案段优先（不再"先到先得"存题面复述）
            if prev is None or (mk["role"] == "solution" and prev[3] != "solution"):
                sol_answers[key] = got + (mk["role"],)
        # 单题作业 + 无号单块解答册（Solution\n4 / Answer: 4）：文件名配对已无歧义地锁定伴随关系，
        # 整册（从第一个解答标记起）就是这道题的答案——不再因标记无号而丢弃
        if not any(k[0] == hf for k in sol_answers):
            hw_marks = _hw_markers(_file_stream(pages, hf)[0])
            hw_nums = {m["num"] for m in hw_marks if m["role"] == "problem" and m["num"] is not None}
            first_sol = next((m for m in marks_all if m["role"] == "solution"), None)
            if len(hw_nums) == 1:
                if first_sol is not None:
                    a_from = first_sol["start"]
                elif not marks_all:
                    a_from = 0             # 连标记都没有的裸答案册（内容就是「4」）——配对关系已无歧义
                else:
                    a_from = None
                if a_from is not None:
                    got = _hw_nonblank_slice(stream, bounds, sf, a_from, len(stream))
                    if got:
                        sol_answers[(hf, next(iter(hw_nums)))] = got + ("solution",)

    # id 词干必须对文件【单射】：消毒把 a/b/hw1 与 a_b/hw1 折叠成同串时，按原始相对路径哈希消歧
    #（不撞名的文件保持原有可读 id 不变）
    import hashlib as _hl

    def _stem_of(hf):
        s = re.sub(r"[^0-9A-Za-z_\-一-鿿]+", "_",
                   os.path.splitext(hf.replace("\\", "/"))[0])   # 含子目录，week1/hw1 ≠ week2/hw1
        if len(s) > 60:                                # 截断会撞 id——加内容哈希后缀保唯一
            s = s[:52] + "_" + _hl.sha1(s.encode("utf-8")).hexdigest()[:7]
        return s
    hw_stems = {hf: _stem_of(hf) for hf in hw_files}
    _counts = {}
    for _s in hw_stems.values():
        _counts[_s] = _counts.get(_s, 0) + 1
    for hf, _s in list(hw_stems.items()):
        if _counts[_s] > 1:
            hw_stems[hf] = _s + "_" + _hl.sha1(hf.replace("\\", "/").encode("utf-8")).hexdigest()[:7]

    items = []
    for hf in hw_files:
        stream, bounds = _file_stream(pages, hf)
        marks = _hw_markers(stream)
        roster_route = _homework_roster_route(pages, hf, backend)
        roster_plans = {}
        if roster_route is not None:
            report["homework_roster_files"] += 1
            if roster_route.get("status") == "review":
                reason = roster_route.get("reason") or "prompt/answer separation is not proven"
                review_pages = roster_route.get("pages") or []
                report["warnings"].append(
                    "homework_prompt_crop_unsafe_leakage: %s (%s)" % (hf, reason)
                )
                report["ai_review"].append({
                    "kind": "homework_prompt_crop_unsafe_leakage",
                    "file": hf,
                    "pages": review_pages,
                    "action": (
                        "The assignment roster could not be mapped one-to-one to independently "
                        "separable prompt image regions (%s). Inspect the cited pages, create one "
                        "prompt-only crop per roster problem, attach handwriting/solutions only as "
                        "answer-side evidence, and rebuild. Do not publish the roster page or a "
                        "whole answer-bearing page as a question asset." % reason
                    ),
                })
                continue
            if roster_route.get("status") == "text":
                roster_pages = set(roster_route.get("pages") or ())
                marks = [
                    marker for marker in marks
                    if not (marker.get("role") == "problem"
                            and _page_for_stream_offset(bounds, marker["start"]) in roster_pages)
                ]
            elif roster_route.get("status") == "visual":
                roster_plans = roster_route.get("plans") or {}
                report["homework_prompt_crops"] += sum(
                    len(plan.get("question_crops") or ()) for plan in roster_plans.values()
                )
                report["homework_answer_crops"] += sum(
                    len(plan.get("answer_crops") or ()) for plan in roster_plans.values()
                )
        probs = [m for m in marks if m["role"] == "problem"]
        if not probs:
            if hf in exam_files:
                _st1, _b1 = _file_stream(pages, hf)
                if detect_lecture_markers(_st1):
                    report["warnings"].append(
                        "exam_lecture_style: %s（试卷命名但内容是讲义式 Quiz/Example 标记——"
                        "已按讲义题导入 quiz_bank，正文不进 wiki）" % hf)
                else:
                    # 试卷排版五花八门（大题号/纯图卷）——抽不出题绝不硬猜，整卷移交 AI 人工处理
                    report["warnings"].append(
                        "exam_no_markers: %s（识别为试卷但没抽出任何题——排版不在识别范围内，"
                        "请 AI 直接阅读原文件人工出题/讲解，不要把它当讲义）" % hf)
            else:
                report["warnings"].append("hw_no_markers: %s（识别为作业文件但没找到 Problem/第N题 标记）" % hf)
            continue
        stem = hw_stems[hf]
        if roster_plans:
            roster_entries = list(roster_route.get("entries") or ())
            review_pages = sorted(set(
                list(roster_route.get("pages") or ())
                + [plan["anchor_page"] for plan in roster_plans.values()]
            ))
            report["warnings"].append(
                "homework_roster_visual_mapping_unverified: %s" % hf
            )
            report["ai_review"].append({
                "kind": "homework_roster_visual_mapping_unverified",
                "file": hf,
                "pages": review_pages,
                "geometry_route": roster_route.get("geometry_route"),
                "external_ids": [
                    "%s_%s_%s" % (
                        "exam" if hf in exam_files else "hw", stem,
                        str(number).replace(".", "_"),
                    )
                    for number in roster_entries
                ],
                "action": (
                    "The parser proved prompt-only geometry but not the semantic roster-to-crop "
                    "labels. Visually compare every printed prompt crop with the roster in order; "
                    "confirm each problem number, that no handwriting is question-side, and that "
                    "the prompt is self-contained. A referenced prerequisite, missing table, or "
                    "missing subquestion must be attached as additional prompt-only evidence; a "
                    "crop is never made complete merely by passing geometry. Resolve this "
                    "artifact-critical review only after those checks."
                ),
            })
        prob_nums = {m["num"] for m in probs if m["num"] is not None}
        seen_nums = set()
        dup_counts = {}

        def _nonblank_slice(s_start, s_end):
            return _hw_nonblank_slice(stream, bounds, hf, s_start, s_end)

        # 同文件「先全部题目、后统一 Answer 1/Answer 2」的 answer-key 段——按题号索引，
        # 不要求解答紧跟在题面后面
        inline_keys = {}
        for j, mk2 in enumerate(marks):
            if mk2["role"] != "solution":
                continue
            end2 = marks[j + 1]["start"] if j + 1 < len(marks) else len(stream)
            if mk2["num"] is not None:
                if mk2["num"] not in inline_keys:
                    got2 = _nonblank_slice(mk2["start"], end2)
                    if got2:
                        inline_keys[mk2["num"]] = got2
                continue
            # 无号「Answers」节头：其下的「1. …」「2. …」编号行是整卷答案区——按号拆给各题
            seg = stream[mk2["start"]:end2]
            keyed_ms = _keyline_filter(_HW_KEYLINE_RE.finditer(seg), prob_nums)
            keyset = {_keyline_num(m2) for m2 in keyed_ms}
            # 拆分条件：号集 ⊆ 已知题号，或命中 ≥2 个已知题号（尾随注记不打碎拆分）；
            # 只命中 1 个的编号步骤/普通列表不拆
            if not (keyset and (keyset <= prob_nums or len(keyset & prob_nums) >= 2)):
                continue
            # 分段边界只用题号条目——界外编号行随前一条答案保留
            keyed_ms = [m2 for m2 in keyed_ms if _keyline_num(m2) in prob_nums]
            for x, m2 in enumerate(keyed_ms):
                seg_end = keyed_ms[x + 1].start() if x + 1 < len(keyed_ms) else len(seg)
                numk = _keyline_num(m2)
                if numk in inline_keys:
                    continue
                got2 = _hw_nonblank_slice(stream, bounds, hf,
                                          mk2["start"] + m2.start(), mk2["start"] + seg_end)
                if got2:
                    inline_keys[numk] = got2
        # 合并文件的「解答区」：全部题面首现之后的重复 Problem N 标题串，且这串标题之前有一条
        # 独立的 Solutions/Answers/解答 节标题行——没有节标题的多号重现（续页页眉没写 Continued）
        # 只能按重复去重，绝不能把续页题面错当官方答案
        first_start = {}
        for mk2 in marks:
            if mk2["role"] == "problem" and not mk2.get("continued") and mk2["num"] not in first_start:
                first_start[mk2["num"]] = mk2["start"]
        tail_answers, tail_starts, sol_title_start = {}, set(), None
        if first_start:
            tail_begin = max(first_start.values())
            tail_marks = [(j, mk2) for j, mk2 in enumerate(marks)
                          if mk2["role"] == "problem" and not mk2.get("continued")
                          and mk2["num"] in first_start and mk2["start"] > tail_begin
                          and mk2["start"] > first_start[mk2["num"]]]
            titled = False
            if tail_marks:
                first_tail = min(mk2["start"] for _j, mk2 in tail_marks)
                tm = re.search(r"^[ 	>*#]*(?:solutions?|answers?|解答|答案)\s*[:：]?\s*$",
                               stream[tail_begin:first_tail], re.I | re.M)
                if tm:
                    titled = True
                    sol_title_start = tail_begin + tm.start()   # 题面边界收到节标题——prompt 不含 Solutions 行
            if titled:
                for j, mk2 in tail_marks:
                    tail_starts.add(mk2["start"])
                    end2 = marks[j + 1]["start"] if j + 1 < len(marks) else len(stream)
                    got2 = _nonblank_slice(mk2["start"], end2)
                    if got2 and mk2["num"] not in tail_answers:
                        tail_answers[mk2["num"]] = got2
        for i, mk in enumerate(marks):
            if mk["role"] != "problem":
                continue
            if mk["num"] in seen_nums:
                if not mk.get("continued") and mk["start"] not in tail_starts:
                    dup_counts[mk["num"]] = dup_counts.get(mk["num"], 0) + 1   # 真实 PDF 里题号会反复出现
                continue                                                    #（分页眉重现）——去重计数
            seen_nums.add(mk["num"])
            # 跨页续题（Problem 1 (continued)）：同号 continued 标题是同一道题的续页——
            # 切片越过它们，续页文字/页码并入本题，不当成重复丢弃
            k = i + 1
            while k < len(marks) and marks[k]["role"] == "problem" \
                    and marks[k]["num"] == mk["num"] and marks[k].get("continued"):
                k += 1
            nxt = marks[k]["start"] if k < len(marks) else len(stream)
            # 有 Solutions 节标题时，题面边界收到标题行首——最后一题的 prompt 不吞节标题
            nxt_q = nxt
            if sol_title_start is not None and mk["start"] < sol_title_start < nxt:
                nxt_q = sol_title_start
            q_text = stream[mk["start"]:nxt_q].strip()
            visual_plan = roster_plans.get(mk["num"])
            if visual_plan is not None:
                q_text = (
                    "Problem %s - see the attached prompt-only crop from %s p.%d."
                    % (mk["num"], hf, visual_plan["anchor_page"])
                )
            # inline solution: the next (non-continued) marker is an un/same-numbered Solution → the answer
            ans = None
            if k < len(marks) and marks[k]["role"] == "solution" \
                    and marks[k]["num"] in (None, mk["num"]):
                head_line_end = stream.find(chr(10), mk["start"])
                body_before = stream[head_line_end:marks[k]["start"]] if 0 <= head_line_end < marks[k]["start"] else ""
                first_line_full = stream[mk["start"]:(head_line_end if head_line_end >= 0 else len(stream))]
                bm0 = next((mm for mm in (rx.match(first_line_full) for rx in _HW_PROB_RES) if mm), None)
                same_rest = first_line_full[bm0.end():] if bm0 else ""
                no_pre = not re.search(r"[0-9A-Za-z一-鿿]", body_before + same_rest)
                label_like = False
                if marks[k]["num"] is None and no_pre:
                    # 题面为空时还要分辨：答题栏标签（指示语/空白栏）才并回题面；
                    # 「Problem 1\nSolution\n答案正文」是真解答——并回题面等于把答案泄进题干
                    lbl_end = next((m2["start"] for m2 in marks[k + 1:] if m2["role"] == "problem"),
                                   len(stream))
                    lbl_seg = stream[marks[k]["start"]:lbl_end]
                    lbl_line, _, lbl_rest = lbl_seg.partition(chr(10))
                    rest_lines = [l for l in lbl_rest.splitlines()
                                  if re.search(r"[0-9A-Za-z一-鿿]", l)]
                    # 标签的三种形态：空白栏；标签行本身是指示语且后无内容；标签行光秃但
                    # 后续每一行都是指示语（Answer:\nWrite your answer in the box below）
                    label_like = (_nonblank_slice(marks[k]["start"], lbl_end) is None
                                  or (bool(_HW_ANSBOX_INSTR_RE.search(lbl_line))
                                      and not rest_lines)
                                  or (bool(rest_lines)
                                      and all(_HW_ANSBOX_INSTR_RE.search(l)
                                              and not re.search(r"[=＝<>×÷^√]|\d", l)
                                              for l in rest_lines)))
                    # 指示语行带实际算式/数字（Show your work: 2+2=4）就是真解答不是标签
                if marks[k]["num"] is None and no_pre and label_like:
                    ans = None         # 题面在答案标记前毫无内容且标记段是标签/空白栏——
                                       # 属题面指示语，不是官方答案
                    s_start = None
                    ext_end = next((m2["start"] for m2 in marks[k:] if m2["role"] == "problem"),
                                   len(stream))
                    if ext_end > nxt_q:
                        nxt_q = ext_end            # 指示语并回题面——题目保持完整可问的全文题
                        q_text = stream[mk["start"]:nxt_q].strip()
                else:
                    s_start = marks[k]["start"]
                if s_start is not None:
                    s_end = next((m2["start"] for m2 in marks[k + 1:] if m2["role"] == "problem"),
                                 len(stream))
                    keyed_norm = {_keyline_num(m2) for m2 in _keyline_filter(
                        _HW_KEYLINE_RE.finditer(stream[s_start:s_end]), prob_nums)}
                    sol_line, _, sol_rest = stream[s_start:s_end].partition(chr(10))
                    if marks[k]["num"] is None and keyed_norm \
                            and (keyed_norm <= prob_nums
                                 or len(keyed_norm & prob_nums) >= 2):
                        ans = None      # 无号「Answers」节头 + 键控列表（多号，或单号但都是已知
                                        # 题号）——是整卷答案区，按号拆给各题（见 inline_keys），
                                        # 不是节前那道题的相邻答案
                    elif (marks[k]["num"] is None
                          or (marks[k]["num"] == mk["num"] and no_pre)) \
                            and _HW_ANSBOX_INSTR_RE.search(sol_line) \
                            and not re.search(r"[0-9A-Za-z一-鿿]", sol_rest):
                        # 「Answer: Give a short proof in the box below」——答题栏指示语，并回
                        # 题面保持完整可问，题目如实无官方答案。带号形「1. Answer: 指示语」在
                        # 题面为空时同治；「Answer 1: 真实答案」无指示语词不受影响
                        ans = None
                        got_lbl = inline_keys.get(mk["num"])
                        if got_lbl and got_lbl[1] == stream[s_start:s_end].strip():
                            # 同一标签段先被 inline_keys 收走——一并撤销，防兜底把指示语捡回当答案
                            del inline_keys[mk["num"]]
                        ext_end = next((m2["start"] for m2 in marks[k:] if m2["role"] == "problem"),
                                       len(stream))
                        if ext_end > nxt_q:
                            nxt_q = ext_end
                            q_text = stream[mk["start"]:nxt_q].strip()
                    else:
                        ans = _nonblank_slice(s_start, s_end)
            paired_official = sol_answers.get((hf, mk["num"]))
            if visual_plan is not None:
                # A roster/submission route proves that this file contains student work.  OCR
                # from its inline/tail ``Answer`` blocks is therefore never material-answer
                # evidence.  A separately paired solution wins unconditionally; without one,
                # keep the attempt visuals but leave the answer missing for typed review.
                ans = paired_official[:3] if paired_official else None
            else:
                if ans is None:
                    ans = inline_keys.get(mk["num"])   # same-file answer-key section
                if ans is None:
                    ans = tail_answers.get(mk["num"])  # titled same-file tail solution section
                if ans is None:
                    ans = paired_official[:3] if paired_official else None
            q_pages = (list(visual_plan["source_pages"])
                       if visual_plan is not None
                       else _pages_for_span(bounds, mk["start"], nxt_q))
            # marker-only prompt: the heading is all the text extractor got — the real prompt is an
            # image on the page → page_reference（镜像 lecture 的 marker_only 语义），并渲染原页
            body_txt = q_text.split("\n", 1)[1] if "\n" in q_text else ""
            # 标题与题面同一行（"Problem 1 Compute 2+2."）——标记之后的同行文本也是正文，
            # 不能因为没有换行就把完整文字题当成图片题
            first_line = q_text.split("\n", 1)[0]
            m0 = next((mm for mm in (rx.match(first_line) for rx in _HW_PROB_RES) if mm), None)
            if m0:
                same_line = first_line[m0.end():].lstrip(" \t.:：、,，)）-—").strip()
                if same_line:
                    body_txt = (same_line + "\n" + body_txt).strip()
            # 只有正文【没有实质内容】才算 marker-only——"2+2=?"/"求导" 这类短而完整的题面仍是 full；
            # 纯数字正文（如 "Problem 1\n12" 的页脚页码）没有字母/CJK/运算符，是抽取残渣 → 按图片题处理
            _mo_body = "\n".join(l for l in body_txt.splitlines()
                                  if not _PAGE_RESIDUE_RE.match(l.strip()))   # 页脚残渣行不算正文
            marker_only = (True if visual_plan is not None else (
                len(re.findall(r"[0-9A-Za-z一-鿿]", _mo_body)) == 0
                or not re.search(r"[A-Za-z一-鿿+\-*/=^%<>?？()（）]", _mo_body)
            ))
            # chapter：题文/文件名可明说；教材式 X.Y / X.Y.Z 题号的首段也是章节锚点。
            # 单段作业序号（Problem 1）仍不是章节号，绝不据此猜测。
            chm = (re.search(r"(?:第\s*(\d+)\s*章|Chapter\s+(\d+))", q_text, re.I)
                   or re.search(r"(?:^|[\/_\-. ])ch\s*0*(\d+)", hf, re.I))
            _src = "exam" if hf in exam_files else "homework"
            _qt, _opts = _classify_question_type(q_text)
            item = {"id": "%s_%s_%s" % (_src if _src == "exam" else "hw", stem,
                                        str(mk["num"]).replace(".", "_")),   # 1.1 → _1_1，id 保持安全字符
                    "type": _qt,
                    "question": q_text, "source": "material", "ai_generated": False,   # 不静默截断（长题保完整）
                    "source_type": _src, "homework_number": mk["num"],
                    "question_text_status": "page_reference" if marker_only else "full",
                    "source_file": hf, "source_pages": q_pages or [bounds[0][1] if bounds else 1]}
            if _qt == "choice" and _opts:
                item["options"] = _opts
            if chm:
                item["chapter"] = int(next(g for g in chm.groups() if g))
            elif _chapter_from_homework_number(mk["num"]) is not None:
                item["chapter"] = _chapter_from_homework_number(mk["num"])
            if ans:
                sf, body, apages = ans
                item["answer"] = body                     # 不静默截断
                _apply_typed_answer(item)
                item["answer_source_file"] = sf
                item["answer_source_pages"] = apages or None
                if item["answer_source_pages"] is None:
                    del item["answer_source_pages"]
                report["homework_answered"] += 1
            else:
                item["answer_status"] = "unknown"
                report["warnings"].append("hw_unanswered: %s（没找到配对答案，考前需人工核对）" % item["id"])
            answer_crop_plan = _homework_answer_crop_plan(
                pages, ans, mk["num"], backend
            ) if ans else None

            def _attach_answer_visual_plan():
                if not ans:
                    return
                if answer_crop_plan:
                    item["_render"] = True
                    item.setdefault("_answer_crops", []).extend(answer_crop_plan)
                else:
                    message = (
                        "answer_item_crop_review_required: %s（自动版面定位未能证明答案页中的"
                        "目标题专属区域；拒绝把可能含其他题目的整页声明为答案图）" % item["id"]
                    )
                    report["warnings"].append(message)
                    report["ai_review"].append({
                        "kind": "item_asset_crop_not_materialized",
                        "file": ans[0],
                        "pages": list(ans[2] or []),
                        "external_ids": [item["id"]],
                        "side": "answer",
                        "action": (
                            message
                            + "; provide a source/preview-bound --crop-annotations "
                              "JSONL bbox. Whole-page fallback is forbidden."
                        ),
                    })
            if visual_plan is not None:
                # The roster route has already proved a one-to-one prompt crop for this
                # problem.  Bind only that crop as question-side evidence.  Handwriting
                # below it and continuation pages stay answer-side and are rendered only
                # after the question; never fall back to an answer-bearing whole page.
                item["requires_assets"] = True
                item["_render"] = True
                item["_prompt_text"] = q_text
                item["_question_crops"] = list(visual_plan["question_crops"])
                item["_answer_crops"] = list(visual_plan["answer_crops"])
                if (ans and requires_assets_heuristic(
                        ans[1], renderable=is_pdf.get(ans[0], False))):
                    # Student-work crops and official solution visuals are distinct evidence.
                    # Keep both in the render plan; role assignment later prevents the former
                    # from satisfying official-answer or Study Guide coverage.
                    _attach_answer_visual_plan()
                items.append(item)
                report["homework_problems"] += 1
                continue
            # visual dependence — same heuristic family as lecture items; renderable only for PDF sources
            # 题面图渲染整页：若该页同时含本题的 inline 答案文本，整页作 question_context 会在
            # 提问前泄答案（visual-first 契约）——这些页从题面图剔除；剔完没剩就 fail-loud 降级
            ans_same_file_pages = set(ans[2] or []) if (ans and ans[0] == hf) else set()
            safe_q_pages = [p for p in (q_pages or []) if p not in ans_same_file_pages]
            if ans_same_file_pages and len(safe_q_pages) < len(q_pages or []):
                report["warnings"].append("hw_prompt_page_contains_answer: %s（题面页与 inline 答案同页，"
                                          "该页不作题面图；无独立题面页时按 page_reference 留待人工处理）"
                                          % item["id"])
            if marker_only and is_pdf.get(hf, False):
                if safe_q_pages:
                    item["requires_assets"] = True
                    item["_render"] = True
                    item["_question_pages"] = [(hf, p) for p in safe_q_pages]
                # 没有干净题面页：保持 page_reference 且不设 requires_assets——quiz 流对无资产的
                # page_reference 会 fail-closed 跳过，绝不整页泄答案
            elif requires_assets_heuristic(q_text, renderable=is_pdf.get(hf, False)):
                if safe_q_pages or not is_pdf.get(hf, False):
                    item["requires_assets"] = True
                    item["_render"] = True
                    item["_question_pages"] = [(hf, p) for p in safe_q_pages]
                else:
                    # 图依赖题的唯一题面页含 inline 答案：不能渲染也不能当 full 出题——
                    # 降级 page_reference（quiz 对无资产的 page_reference fail-closed 跳过）
                    item["question_text_status"] = "page_reference"
                if ans and requires_assets_heuristic(ans[1], renderable=is_pdf.get(ans[0], False)):
                    item["_render"] = True
                    _attach_answer_visual_plan()
            elif ans and requires_assets_heuristic(ans[1], renderable=is_pdf.get(ans[0], False)):
                # 题面纯文本、官方解答依赖图（see the graph below）——渲染答案侧原页作 answer_context，
                # 复盘讲解不至于指着看不见的图；不设 requires_assets（题面本身完整可问）
                _attach_answer_visual_plan()
            items.append(item)
            report["homework_problems"] += 1
        if dup_counts:
            report["warnings"].append("hw_duplicate_problem: %s（%s——每题保留第一处标记，重现多为页眉/"
                                      "解答区重复）" % (hf, "、".join("Problem %s×%d" % (n, c)
                                                                      for n, c in sorted(dup_counts.items(),
                                                                                         key=lambda kv: str(kv[0])))))
        # chapter 来自明示文本/文件名或教材式点号题号；单段作业号绝不当章节号。没章节的题 --chapter 过滤
        # 取不到，必须让用户知道并给出补标注的路径，而不是静默漏检索
        no_ch = sum(1 for it in items if it["source_file"] == hf and "chapter" not in it)
        if no_ch:
            report["warnings"].append("hw_no_chapter: %s（%d 题无章节线索——select_questions 的 --chapter "
                                      "过滤不会返回它们，可用 --source-type homework 全量取；要参与章节复习"
                                      "请在题面或文件名标注 第N章/Chapter N/chNN）" % (hf, no_ch))
    return items, report


def group_sections(pages, notes=None):
    """Group pages into chapters. A chapter number comes from a lecture marker on the page, else from
    a `ch<NN>` token in the filename, else the chapter CARRIED FORWARD from the previous page of the
    same file (so an unmarked ch-2 prose page after `Example 2.1` stays in ch 2, not ch 1), else 1.
    传入 notes 列表时，把【全文件无任何章节线索】（默认并入第 1 章属于猜测）的文件名收集进去，
    让调用方 fail-loud——绝不静默猜章节。Returns ordered list of {chapter, files, pages, text}."""
    by_ch = {}
    order = []
    last_ch_by_file = {}
    clue_files, all_files = set(), []
    for pg in pages:
        f = pg.get("file")
        if f not in all_files:
            all_files.append(f)
        markers = detect_lecture_markers(pg.get("text", ""))
        # 词元边界：march-2024.pdf 的 "ch" 前面是字母，不是章节记号（审计实测捏造第 2024 章）；
        # CamelCase（LectureChapter02）单列大小写敏感分支放行
        _base = os.path.basename(f or "")
        m = (re.search(r"(?<![A-Za-z])ch(?:apter)?[ _-]?0*(\d+)", _base, re.I)
             or re.search(r"(?<=[a-z])Ch(?:apter)?[ _-]?0*(\d+)", _base))   # 驼峰缩写 LectureCh02 也放行（大写 C 区分 march）
        if markers:
            ch = markers[0]["chapter"]
            clue_files.add(f)
        elif m:
            ch = int(m.group(1))
            clue_files.add(f)
        else:
            ch = last_ch_by_file.get(f, 1)   # carry forward the previous marked page's chapter (same file)
        last_ch_by_file[f] = ch
        if ch not in by_ch:
            by_ch[ch] = {"chapter": ch, "files": [], "pages": [], "text_blocks": []}
            order.append(ch)
        sec = by_ch[ch]
        if pg.get("file") not in sec["files"]:
            sec["files"].append(pg.get("file"))
        sec["pages"].append(pg.get("page"))
        sec.setdefault("page_keys", []).append((f, pg.get("page")))
        # Keep a page anchor even when the text backend returns nothing.  A blank/image-only PDF
        # page can still be a semantically essential visual page; dropping its anchor made the
        # downstream wiki coverage denominator silently shrink to zero and left --apply-wiki with
        # nowhere to attach the rendered page.
        page_text = (pg.get("text") or "").strip()
        if not page_text:
            page_text = "（本页未提取到文本；保留原页锚点供视觉覆盖核对）"
        sec["text_blocks"].append("<!-- %s p.%d -->\n%s" %
                                  (pg.get("file"), pg.get("page"), page_text))
    if notes is not None:
        notes.extend(f for f in all_files if f not in clue_files)
    return [by_ch[c] for c in sorted(order)]


def _safe_asset_name(
        file, page, item_id, suffix="", source_sha256=None,
        crop_spec_sha256=None):
    # keep subdirs (sanitized) so lecture/ch01.pdf and solutions/ch01.pdf don't collide on the same page
    stem = re.sub(r"[^\w.\-]", "_", os.path.splitext(file or "src")[0])
    if re.fullmatch(r"[.\-_]*", stem):         # all-dots/dashes/underscores (e.g. a ".." name) → a token
        stem = "src"
    sid = re.sub(r"[^\w.\-]", "_", str(item_id))
    revision = "_%s" % source_sha256[:12] if source_sha256 else ""
    crop_revision = (
        "_crop_%s" % crop_spec_sha256[:12]
        if crop_spec_sha256 else ""
    )
    return "%s%s_p%03d_%s%s%s.png" % (
        stem, revision, int(page), sid, suffix, crop_revision
    )


def _crop_renderer_contract(backend):
    """Return stable renderer identity/config for a crop spec."""

    renderer_id = getattr(backend, "render_lib", None) or getattr(
        backend, "name", "unknown"
    )
    renderer_id = re.sub(
        r"[^a-z0-9_.-]", "-", str(renderer_id).lower()
    ).strip("-.") or "unknown"
    explicit_version = getattr(backend, "renderer_version", None)
    if explicit_version:
        renderer_version = str(explicit_version).strip()
    else:
        distribution = {
            "pymupdf": "PyMuPDF",
            "pypdfium2": "pypdfium2",
        }.get(renderer_id)
        try:
            renderer_version = (
                importlib.metadata.version(distribution)
                if distribution else "unknown"
            )
        except importlib.metadata.PackageNotFoundError:
            renderer_version = "unknown"
    config = getattr(backend, "crop_renderer_config", None)
    if not isinstance(config, dict):
        config = {
            "clip_coordinate_space": "pdf_points",
            "output_format": "png",
            "scale": 2.0,
            "whole_page_fallback": False,
        }
    return renderer_id, renderer_version, crop_canonical_sha256(config)


def _item_crop_source_pages(item, side):
    """Return the source locations already attributed to one item side."""

    pages = set()
    if side == "prompt":
        source_file = item.get("source_file")
        pages.update(
            (source_file, page) for page in (item.get("source_pages") or ())
            if isinstance(source_file, str) and type(page) is int
        )
        tuple_fields = ("_question_pages", "_question_crops")
    else:
        # ``inline_material`` binds exact text on the prompt's own page; it is
        # not a request to publish that physical page a second time as an
        # answer-side model asset.  Explicit answer crop/page plans, if ever
        # introduced by a reviewed producer, remain visible below.
        if item.get("answer_origin") != "inline_material":
            source_file = item.get("answer_source_file") or item.get("source_file")
            pages.update(
                (source_file, page) for page in (item.get("answer_source_pages") or ())
                if isinstance(source_file, str) and type(page) is int
            )
        tuple_fields = ("_answer_pages", "_answer_crops")
    for field in tuple_fields:
        for row in item.get(field) or ():
            if (isinstance(row, (list, tuple)) and len(row) >= 2
                    and isinstance(row[0], str) and type(row[1]) is int):
                pages.add((row[0].replace("\\", "/"), row[1]))
    return pages


def _load_crop_annotations(
        annotation_path, materials, source_snapshot_hashes, page_pdf_all,
        items, backend):
    """Load and verify explicit JSONL annotations without materializing assets.

    Each JSONL row contains the exact ``CropAnnotation`` fields plus one
    ``preview_path`` relative to the JSONL directory.  The locator is excluded
    from the annotation hash; the exact preview bytes/hash/dimensions are in the
    annotation itself.  Source revision, source page, the exact live preview
    bytes produced by this builder backend, and item-side attribution are all
    checked before a bbox is accepted.  A merely similar-sized or
    aspect-ratio-compatible preview is deliberately insufficient.
    """

    annotation_path = os.path.abspath(annotation_path)
    if _path_has_link_or_reparse(annotation_path):
        raise ValueError("crop annotation path contains a link/reparse point")
    payload, snapshot = stable_read_bytes(annotation_path)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("crop annotations must be UTF-8 JSONL") from exc
    source_hash_by_file = {
        _rel(path, materials): digest
        for path, digest in source_snapshot_hashes.items()
    }
    items_by_id = {
        str(item.get("id")): item for item in items
        if isinstance(item, dict) and item.get("id") not in (None, "")
    }
    layout_reader = getattr(backend, "page_layout", None)
    if not callable(layout_reader):
        raise ValueError(
            "crop annotations require PDF page geometry; whole-page fallback is forbidden"
        )
    base = os.path.dirname(annotation_path)
    expected_row_fields = set(CropAnnotation.FIELDS) | {"preview_path"}
    seen = set()
    records = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = strict_json.loads(line)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "crop annotations line %d is not strict JSON" % line_number
            ) from exc
        if not isinstance(row, dict) or set(row) != expected_row_fields:
            missing = sorted(expected_row_fields - set(row or ())) if isinstance(row, dict) else []
            unknown = sorted(set(row) - expected_row_fields) if isinstance(row, dict) else []
            raise ValueError(
                "crop annotations line %d schema mismatch; missing=%r unknown=%r"
                % (line_number, missing, unknown)
            )
        preview_relative = normalize_workspace_path(row["preview_path"])
        if preview_relative != row["preview_path"]:
            raise ValueError(
                "crop annotations line %d preview_path must use POSIX separators"
                % line_number
            )
        annotation_payload = {
            field: row[field] for field in CropAnnotation.FIELDS
        }
        try:
            annotation = CropAnnotation.from_dict(annotation_payload)
        except CropContractError as exc:
            raise ValueError(
                "crop annotations line %d: %s" % (line_number, exc)
            ) from exc
        if annotation.selection_method == "layout_auto":
            raise ValueError(
                "crop annotations line %d must use model_vision or human"
                % line_number
            )
        item = items_by_id.get(annotation.item_id)
        if item is None:
            raise ValueError(
                "crop annotations line %d references an unknown item_id"
                % line_number
            )
        source_key = (annotation.source_file, annotation.source_page)
        if source_key not in _item_crop_source_pages(item, annotation.side):
            raise ValueError(
                "crop annotations line %d source page is not attributed to that item side"
                % line_number
            )
        pdf_path = page_pdf_all.get(source_key)
        expected_source_sha256 = source_hash_by_file.get(annotation.source_file)
        if pdf_path is None or expected_source_sha256 is None:
            raise ValueError(
                "crop annotations line %d does not identify a current PDF page"
                % line_number
            )
        if annotation.source_sha256 != expected_source_sha256:
            raise ValueError(
                "crop annotations line %d source_sha256 drifted" % line_number
            )
        preview_path = os.path.join(base, *preview_relative.split("/"))
        if not _under(base, preview_path) or _path_has_link_or_reparse(preview_path):
            raise ValueError(
                "crop annotations line %d preview path is unsafe" % line_number
            )
        preview_payload, preview_snapshot = stable_read_bytes(preview_path)
        if preview_snapshot["sha256"] != annotation.preview_sha256:
            raise ValueError(
                "crop annotations line %d preview_sha256 drifted" % line_number
            )
        try:
            preview_width, preview_height = png_dimensions(preview_payload)
        except ImageValidationError as exc:
            raise ValueError(
                "crop annotations line %d preview is not a valid PNG: %s"
                % (line_number, exc)
            ) from exc
        if (preview_width != annotation.preview_width
                or preview_height != annotation.preview_height):
            raise ValueError(
                "crop annotations line %d preview dimensions drifted" % line_number
            )
        preview_renderer = getattr(backend, "render_page_png", None)
        if not callable(preview_renderer):
            raise ValueError(
                "crop annotations line %d cannot reproduce the bound preview "
                "with the current builder backend" % line_number
            )
        try:
            live_preview = preview_renderer(
                pdf_path, annotation.source_page - 1
            )
        except Exception as exc:
            raise ValueError(
                "crop annotations line %d live preview rendering failed: %s"
                % (line_number, exc)
            ) from exc
        if not live_preview:
            raise ValueError(
                "crop annotations line %d current builder backend returned no "
                "live preview" % line_number
            )
        live_preview = bytes(live_preview)
        try:
            live_width, live_height = png_dimensions(live_preview)
        except ImageValidationError as exc:
            raise ValueError(
                "crop annotations line %d current builder backend returned an "
                "invalid preview PNG: %s" % (line_number, exc)
            ) from exc
        live_sha256 = hashlib.sha256(live_preview).hexdigest()
        if (live_preview != preview_payload
                or live_sha256 != annotation.preview_sha256
                or live_width != annotation.preview_width
                or live_height != annotation.preview_height):
            raise ValueError(
                "crop annotations line %d preview is not the exact current-page "
                "render from this builder backend (bytes/hash/dimensions differ)"
                % line_number
            )
        layout = layout_reader(pdf_path, annotation.source_page - 1)
        page_box = layout.get("page_bbox") if isinstance(layout, dict) else None
        if not (isinstance(page_box, (list, tuple)) and len(page_box) == 4):
            raise ValueError(
                "crop annotations line %d cannot bind PDF page geometry" % line_number
            )
        page_width = float(page_box[2]) - float(page_box[0])
        page_height = float(page_box[3]) - float(page_box[1])
        if page_width <= 0 or page_height <= 0:
            raise ValueError(
                "crop annotations line %d has invalid PDF page dimensions" % line_number
            )
        bbox_pdf_points = annotation_bbox_pdf_points(annotation, page_box)
        # The reviewer signs the exact target-only crop bytes, not merely a
        # page preview plus an unchecked rectangle.  Reproduce that crop with
        # the current backend now and close the explicit semantic verdict to
        # its bytes and to the full preview/source/bbox evidence packet.
        try:
            reviewed_crop, _crop_width, _crop_height = render_crop_png(
                backend,
                pdf_path,
                annotation.source_page - 1,
                bbox_pdf_points,
            )
        except CropContractError as exc:
            raise ValueError(
                "crop annotations line %d cannot reproduce the reviewed crop: %s"
                % (line_number, exc)
            ) from exc
        reviewed_crop_sha256 = hashlib.sha256(reviewed_crop).hexdigest()
        try:
            annotation.semantic_purity.validate(
                expected_item_id=annotation.item_id,
                expected_side=annotation.side,
                expected_crop_sha256=reviewed_crop_sha256,
            )
        except CropContractError as exc:
            raise ValueError(
                "crop annotations line %d semantic verdict does not bind the "
                "reproduced crop: %s" % (line_number, exc)
            ) from exc
        semantic_evidence = {
            "schema_version": 1,
            "evidence_kind": "builder_preview_bbox_crop",
            "target_item_id": annotation.item_id,
            "side": annotation.side,
            "source_id": annotation.source_id,
            "source_file": annotation.source_file,
            "source_sha256": annotation.source_sha256,
            "source_page": annotation.source_page,
            "preview_sha256": annotation.preview_sha256,
            "preview_width": annotation.preview_width,
            "preview_height": annotation.preview_height,
            "bbox_preview_pixels": list(annotation.bbox_preview_pixels),
            "crop_sha256": reviewed_crop_sha256,
        }
        if annotation.semantic_purity.schema_version >= 2:
            semantic_evidence["required_context_ids"] = list(
                annotation.semantic_purity.required_context_ids
            )
        evidence_binding_sha256 = crop_canonical_sha256(semantic_evidence)
        if (annotation.semantic_purity.evidence_binding_sha256
                != evidence_binding_sha256):
            raise ValueError(
                "crop annotations line %d semantic evidence binding does not "
                "match the exact source/preview/bbox/crop evidence" % line_number
            )
        identity = (
            annotation.item_id, annotation.side,
            annotation.source_file, annotation.source_page,
        )
        if identity in seen:
            raise ValueError(
                "crop annotations line %d duplicates one item/side/source/page"
                % line_number
            )
        seen.add(identity)
        records.append({
            "item_id": annotation.item_id,
            "side": annotation.side,
            "role": annotation.role,
            "content_scope": annotation.content_scope,
            "source_file": annotation.source_file,
            "source_page": annotation.source_page,
            "source_sha256": annotation.source_sha256,
            "page_box_pdf_points": [float(value) for value in page_box],
            "bbox_pdf_points": bbox_pdf_points,
            "selection_method": annotation.selection_method,
            "selection_evidence_sha256": annotation.annotation_sha256,
            "preview_sha256": annotation.preview_sha256,
            "semantic_purity": annotation.semantic_purity.to_dict(),
        })
    if not records:
        raise ValueError("crop annotations JSONL contains no records")
    confirmation, confirmation_snapshot = stable_read_bytes(annotation_path)
    if (confirmation != payload
            or confirmation_snapshot["sha256"] != snapshot["sha256"]):
        raise ValueError("crop annotations changed while previews were verified")
    return sorted(
        records,
        key=lambda row: (
            row["item_id"], row["side"], row["source_file"], row["source_page"]
        ),
    ), {
        "file_sha256": snapshot["sha256"],
        "record_count": len(records),
    }


def _sha256_path(path, cache):
    key = os.path.abspath(path)
    if key not in cache:
        digest = hashlib.sha256()
        with open(key, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        cache[key] = digest.hexdigest()
    return cache[key]


def _write_png_atomic(asset_root, name, payload):
    try:
        png_dimensions(payload)
    except ImageValidationError as exc:
        raise ValueError(
            "render backend returned an invalid or undecodable PNG: %s" % exc
        )
    os.makedirs(asset_root, exist_ok=True)
    destination = os.path.join(asset_root, name)
    if os.path.lexists(destination):
        if (_path_has_link_or_reparse(destination)
                or not os.path.isfile(destination)):
            raise ValueError("refusing to reuse a non-regular or link-backed asset target")
        with open(destination, "rb") as stream:
            current = stream.read()
        if current == bytes(payload):
            return hashlib.sha256(bytes(payload)).hexdigest()
        raise ValueError("refusing to overwrite an existing asset target with different bytes")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % name, suffix=".tmp", dir=asset_root
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(bytes(payload)).hexdigest()


def _write_new_asset_atomic(asset_root, name, payload):
    """Publish one already-validated staged asset without replacing a target."""

    os.makedirs(asset_root, exist_ok=True)
    destination = os.path.join(asset_root, name)
    if os.path.lexists(destination):
        raise ValueError("asset target appeared after publication preflight")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % name, suffix=".tmp", dir=asset_root
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(destination):
            raise ValueError("asset target appeared during atomic publication")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_raw_input(course_name, sections, lecture_items, homework_items=None):
    """Assemble a raw_input.json compatible with scripts/ingest.py.

    `teaching_examples` is a PARALLEL snapshot of every detected lecture Example.  It may
    deliberately overlap the canonical bank for assessable problem/solution pairs.  A completed
    worked demonstration without an independent grading key stays teaching-only instead of
    manufacturing a missing-answer quiz blocker.
    """
    phases = []
    for n, sec in enumerate(sections, 1):
        body = "\n\n".join(sec["text_blocks"]) or "（本章未提取到文本，请结合原始页/asset 复习）"
        phases.append({
            "phase_num": n,
            "phase_id": "phase%02d" % n,
            "chapter": sec["chapter"],
            "chapter_id": "ch%02d" % sec["chapter"],
            "phase_name": "第 %d 章" % sec["chapter"],
            "wiki_filename": "ch%02d.md" % sec["chapter"],
            "wiki_content": "# 第 %d 章\n\n来源文件：%s\n\n%s" % (
                sec["chapter"], "、".join(sec["files"]), body),
            "source_pages": sorted(set(p for p in sec["pages"] if p)),
        })
    if not phases:
        phases = [{"phase_num": 1, "phase_id": "phase01", "chapter": 1,
                   "chapter_id": "ch01", "phase_name": "第 1 章", "wiki_filename": "ch01.md",
                   "wiki_content": "# 第 1 章\n\n（未提取到内容）"}]
    # strip internal render-only keys (e.g. _answer_pages) so they don't leak into the bank
    def _clean(it):
        return {k: v for (k, v) in it.items() if not k.startswith("_")}

    def _has_independent_grade_key(it):
        def _nonblank(value):
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (list, tuple)):
                return any(_nonblank(part) for part in value)
            if isinstance(value, dict):
                return bool(value)
            return value is not None

        return any(_nonblank(it.get(field)) for field in (
            "answer", "answer_keywords", "keywords"
        ))

    def _teaching_role(it):
        return (it.get("_teaching_role") or it.get("teaching_role")
                or ("worked_example"
                    if str(it.get("id", "")).startswith("lecture_example_") else None))

    def _teaching_only_worked_example(it):
        return (
            _teaching_role(it) == "worked_example"
            and (
                it.get("answer_origin") == "inline_material"
                or not _has_independent_grade_key(it)
            )
        )

    bank = [
        _clean(it) for it in lecture_items
        if not _teaching_only_worked_example(it)
    ] + [_clean(it) for it in (homework_items or [])]
    teaching_examples = []
    for it in lecture_items:
        if not str(it.get("id", "")).startswith("lecture_example_"):
            continue
        snap = _clean(it)
        snap["teaching_role"] = _teaching_role(it)
        if _teaching_only_worked_example(it):
            snap["gradable"] = False
        snap["title"] = it.get("_teaching_title") or str(it.get("id"))
        teaching_examples.append(snap)
    expected_teaching_ids = [
        str(it.get("id")) for it in lecture_items
        if str(it.get("id", "")).startswith("lecture_example_")
    ]
    actual_teaching_ids = [str(item.get("id")) for item in teaching_examples]
    if (
        len(expected_teaching_ids) != len(set(expected_teaching_ids))
        or len(actual_teaching_ids) != len(set(actual_teaching_ids))
        or set(actual_teaching_ids) != set(expected_teaching_ids)
    ):
        raise ValueError(
            "teaching_examples postcondition failed: every detected lecture example "
            "must have exactly one current teaching snapshot"
        )
    return {"course_name": course_name, "phases": phases, "quiz_bank": bank,
            "teaching_examples": teaching_examples}   # optional parallel teaching layer


def _structured_question_union(quiz_bank, teaching_examples):
    """Return every typed question exactly once, keeping canonical-bank precedence.

    ``teaching_examples`` is a parallel reachability layer, not a second bank.  Paired
    examples overlap the bank by stable ID, while non-gradable worked examples exist only
    in the teaching layer.  The ingestion IR needs the union so Guide completeness can
    account for every authored teaching item without manufacturing a quiz.
    """
    result = []
    seen = set()
    for item in list(quiz_bank or ()) + list(teaching_examples or ()):
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        identity = str(raw_id) if raw_id not in (None, "") else None
        if identity is not None and identity in seen:
            continue
        result.append(item)
        if identity is not None:
            seen.add(identity)
    return result


# ---------------------------------------------------------------------------
# PDF backends — OPTIONAL. Core/tests never import these; tests inject a fake backend.
# ---------------------------------------------------------------------------

class NoBackend(object):
    name = "none"

    def can_text(self):
        return False

    def can_render(self):
        return False

    def page_texts(self, pdf_path):
        raise RuntimeError(
            "没有可用的 PDF 文本后端。请安装可选依赖 `pypdf`（pip install pypdf）或 "
            "PyMuPDF（pip install pymupdf）后重试——PDF 文本提取需要其中之一"
            "（.txt/.md 材料无需任何后端）。")

    def render_page_png(self, pdf_path, page_index):
        return None


class RealBackend(object):
    def __init__(self, text_lib=None, render_lib=None):
        if isinstance(text_lib, (list, tuple)):
            self.text_libs = tuple(dict.fromkeys(value for value in text_lib if value))
        else:
            self.text_libs = (text_lib,) if text_lib else ()
        self.text_lib = self.text_libs[0] if self.text_libs else None
        self.render_lib = render_lib
        self.last_text_methods = []
        self.name = "+".join(self.text_libs + ((render_lib,) if render_lib else ())) or "none"

    def can_text(self):
        return bool(self.text_libs)

    def can_render(self):
        return bool(self.render_lib)

    def page_texts(self, pdf_path):
        candidates = []
        failures = []
        for library in self.text_libs:
            try:
                if library == "pypdf":
                    import pypdf
                    reader = pypdf.PdfReader(pdf_path)
                    texts = [(page.extract_text() or "") for page in reader.pages]
                elif library == "pymupdf":
                    import fitz
                    doc = fitz.open(pdf_path)
                    try:
                        texts = [(page.get_text("text") or "") for page in doc]
                    finally:
                        doc.close()
                else:
                    continue
                candidates.append((library, texts))
            except Exception as exc:
                failures.append("%s: %s" % (library, exc))
        if not candidates:
            if failures:
                raise RuntimeError("all PDF text backends failed (%s)" % "; ".join(failures))
            return NoBackend().page_texts(pdf_path)

        # Prefer the page inventory with the strongest usable-text coverage, then
        # improve individual pages from another backend when page counts agree.
        def quality(item):
            _library, values = item
            return (
                sum(1 for value in values if _page_has_content(value)),
                sum(len(value.strip()) for value in values),
                len(values),
            )

        primary_library, primary = max(candidates, key=quality)
        result = list(primary)
        methods = [primary_library] * len(result)
        for library, values in candidates:
            if library == primary_library or len(values) != len(result):
                continue
            for index, value in enumerate(values):
                current_score = (_page_has_content(result[index]), len(result[index].strip()))
                alternate_score = (_page_has_content(value), len(value.strip()))
                if alternate_score > current_score:
                    result[index] = value
                    methods[index] = library
        self.last_text_methods = methods
        return result

    def render_page_png(self, pdf_path, page_index):
        if self.render_lib == "pypdfium2":
            import io
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_path)
            page = bitmap = image = None
            try:
                page = doc[page_index]
                bitmap = page.render(scale=2.0)
                image = bitmap.to_pil()
                buf = io.BytesIO()
                image.save(buf, format="PNG")   # PIL adapter — Pillow verified at detect time
                return buf.getvalue()
            finally:
                for value in (image, bitmap, page, doc):
                    close = getattr(value, "close", None)
                    if callable(close):
                        close()
        if self.render_lib == "pymupdf":
            import fitz
            doc = fitz.open(pdf_path)
            try:
                return doc[page_index].get_pixmap(
                    matrix=fitz.Matrix(2.0, 2.0), alpha=False
                ).tobytes("png")   # ~144 dpi native PNG, no Pillow needed
            finally:
                doc.close()
        return None

    def page_layout(self, pdf_path, page_index):
        """Return local-only image/text geometry for deterministic crop gates."""
        import fitz
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_index]
            images = []
            for image in page.get_images(full=True):
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    images.append({
                        "image_id": str(xref),
                        "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "pixel_width": image[2],
                        "pixel_height": image[3],
                    })
            raw_blocks = [
                block for block in page.get_text("blocks")
                if len(block) >= 5 and str(block[4]).strip()
            ]
            text_boxes = [
                [block[0], block[1], block[2], block[3]]
                for block in raw_blocks
            ]
            text_blocks = [
                {
                    "bbox": [block[0], block[1], block[2], block[3]],
                    "text": str(block[4]),
                }
                for block in raw_blocks
            ]
            return {
                "page_bbox": [page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1],
                "images": images,
                "text_boxes": text_boxes,
                "text_blocks": text_blocks,
            }
        finally:
            doc.close()

    def render_page_clip_png(self, pdf_path, page_index, bbox):
        """Render one proven PDF-point crop; never fall back to a whole page."""
        import fitz
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_index]
            clip = fitz.Rect(*bbox) & page.rect
            if clip.is_empty or clip.is_infinite:
                return None
            return page.get_pixmap(
                matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False
            ).tobytes("png")
        finally:
            doc.close()


def detect_backend():
    def available(imports):
        try:
            for module_name in imports:
                importlib.import_module(module_name)
            return True
        except Exception:
            return False

    text_libs = [
        adapter_id for adapter_id, imports, unused in PDF_TEXT_CANDIDATES
        if available(imports)
    ]
    # Order is policy: native PyMuPDF first, PDFium + Pillow second.  The latter
    # is a compound capability and cannot be claimed when Pillow is absent.
    render_lib = next((
        adapter_id for adapter_id, imports, unused in PDF_RENDER_CANDIDATES
        if available(imports)
    ), None)
    return RealBackend(text_libs, render_lib) if (text_libs or render_lib) else NoBackend()


# ---------------------------------------------------------------------------
# Path safety + filesystem
# ---------------------------------------------------------------------------

def _under(root, child):
    root_r = os.path.normcase(os.path.realpath(root))
    child_r = os.path.normcase(os.path.realpath(child))
    return child_r == root_r or child_r.startswith(root_r + os.sep)


def _path_has_link_or_reparse(path):
    current = os.path.abspath(path)
    while True:
        if os.path.lexists(current) and is_link_or_reparse(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


# Tooling/VCS dirs that NEVER hold course material → always pruned from the materials scan.
ALWAYS_PRUNE = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
                ".idea", ".vscode", ".pytest_cache", ".ipynb_checkpoints"}
# generated skill-workspace files (not course material) → skipped even if they sit at the materials root.
SKIP_FILES = {"study_plan.md", "study_progress.md", "walkthrough.md", "raw_input.json",
              "parse_report.json", "ingest_report.json", "ai_review_manifest.json"}
GENERATED_CONTROL_FILE_PATTERNS = (
    re.compile(r"^universal-examprep-e2e-audit(?:-[0-9]{4}-[0-9]{2}-[0-9]{2})?\.md$", re.I),
)


def _is_generated_control_file(filename):
    """Return whether a root file is framework output rather than course material.

    The E2E auditor intentionally writes its report beside the selected source
    PDFs so a maintainer can inspect one evidence folder.  Re-ingesting that
    report as lecture notes contaminates the course corpus with implementation
    findings.  Keep this allow-list narrow: ordinary ``audit.md`` or student
    Markdown remains valid material.
    """
    low = str(filename or "").lower()
    return low in SKIP_FILES or any(
        pattern.fullmatch(str(filename or ""))
        for pattern in GENERATED_CONTROL_FILE_PATTERNS
    )


def _is_leftover_workspace(path, name):
    """True only if a `references`/`scratch` dir looks like a generated skill workspace / prior-attempt
    scratch — NOT a legitimate course `references/` of real PDFs. Keyed on the workspace SIGNATURE
    (references/wiki, scratch/extracted|images) so we don't drop a real `materials/references/ch02.pdf`."""
    low = name.lower()
    if low == "references":
        # only `references/wiki` is a reliable skill-workspace signature (ingest always creates it);
        # `references/assets` alone is NOT — a course may legitimately store PDFs under references/assets.
        return os.path.isdir(os.path.join(path, "wiki"))
    if low == "scratch":
        return any(os.path.isdir(os.path.join(path, s)) for s in ("extracted", "images"))
    return False


def _is_workspace_root(path):
    """True if a directory IS a generated skill workspace (a prior run's output nested under
    --materials, e.g. `skill_workspace/`) — has `references/wiki/` or `references/quiz_bank.json`.
    The WHOLE dir is pruned so its study_progress.md / wiki / etc. never leak in as materials."""
    return (os.path.isdir(os.path.join(path, "references", "wiki"))
            or os.path.isfile(os.path.join(path, "references", "quiz_bank.json")))


def _scan_materials(materials_dir):
    """Return sorted (pdf_paths, document_paths, pruned_dirs, other_paths).

    ``document_paths`` contains text/Markdown plus safe local DOCX/PPTX OOXML sources.  Prunes tooling/VCS dirs unconditionally, and
    a `references/`+`scratch/` dir ONLY when it carries a generated-workspace signature — so a prior
    workspace inside the course folder isn't re-ingested, but a real course `references/` of PDFs is kept.
    (Real case: D:\\EEC 160 held a previous ad-hoc workspace → without pruning every lecture marker was
    triplicated across the pdf + extracted .txt + wiki .md, blowing up the bank with broken items.)"""
    pdfs, documents, pruned, others = [], [], [], []
    for dirpath, dirs, files in os.walk(materials_dir):
        keep = []
        for d in dirs:
            full = os.path.join(dirpath, d)
            if (is_link_or_reparse(full) or d.lower() in ALWAYS_PRUNE
                    or _is_leftover_workspace(full, d) or _is_workspace_root(full)):
                pruned.append(os.path.relpath(full, materials_dir).replace(os.sep, "/"))
            else:
                keep.append(d)
        dirs[:] = keep   # os.walk: prune in place
        at_root = os.path.realpath(dirpath) == os.path.realpath(materials_dir)
        for fn in sorted(files):
            low = fn.lower()
            # These are generated workspace/control artifacts when they appear
            # at the selected materials root.  Requiring an already-complete
            # workspace signature here lets partial/failed prior runs leak back
            # into the next build as course notes.
            if at_root and _is_generated_control_file(fn):
                pruned.append(os.path.relpath(os.path.join(dirpath, fn), materials_dir).replace(os.sep, "/"))
                continue
            full = os.path.join(dirpath, fn)
            if is_link_or_reparse(full):
                others.append(full)
                continue
            if low.endswith(".pdf"):
                pdfs.append(full)
            elif low.endswith((
                    ".txt", ".md", ".docx", ".pptx", ".xlsx",
                    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
            )):
                documents.append(full)
            elif not fn.startswith((".", "~$")) and low not in ("thumbs.db", "desktop.ini"):
                others.append(full)                    # 不支持的格式也要留痕，绝不零痕迹丢弃
                                                       #（只豁免已知 OS 垃圾名，不按扩展名类猜）
    return sorted(pdfs), sorted(documents), sorted(pruned), sorted(others)


# 页码/页眉残渣行（纯数字、Page N of M、第N页）——扫描件每页常只残留这一行，
# 精确空判定会被骗过（审计实测）
_PAGE_RESIDUE_RE = re.compile(
    r"^(?:[-–—•·.\s]*\d{1,4}[-–—•·.\s]*|\d{1,4}\s*/\s*\d{1,4}"
    r"|(?:pages?|slides?)\s*\d+(?:\s*(?:of|/)\s*\d+)?"
    r"|第\s*\d+\s*[页张](?:\s*[/，,]?\s*共\s*\d+\s*[页张])?)$", re.I)


def _page_has_content(text):
    """页面是否有【有效】文本——剥掉页码/页眉残渣行后仍有实义字符（字母/CJK，或带运算符的
    数字）才算。只用于检测/警报，不改变管线喂给 wiki/题库的原文。"""
    t = (text or "").strip()
    if not t:
        return False
    kept = [ln for ln in (l.strip() for l in t.splitlines()) if ln and not _PAGE_RESIDUE_RE.match(ln)]
    joined = " ".join(kept)
    # [^\W\d_] = 任意 Unicode 字母（拉丁/CJK/假名/谚文/西里尔…）——非中英文课程材料不误判空；
    # 残渣剥完还剩 ≥2 行数字（统计数据表/纯数值答案键 0.12/0.37）也算内容——整页页码是
    # 残渣行早被剥掉，能留下的多行数字是真数据
    return bool(re.search(r"[^\W\d_]", joined) or re.search(r"\d\s*[+\-*=^%<>]", joined)
                or (len(kept) >= 2 and re.search(r"\d", joined)))


def _fmt_pages(nums):
    """页号列表 → 紧凑区间串（1,3-5,9）。"""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else "%d-%d" % (nums[i], nums[j]))
        i = j + 1
    return ",".join(out)


def _source_location_label(source_file, ordinal):
    """Return an honest, format-specific human locator for one extracted record.

    ``page`` remains the stable integer field in the ingestion envelope, but it
    does not always denote a rendered physical page.  In particular, DOCX
    records are logical segments created only by explicit page-break markup.
    """

    extension = os.path.splitext(str(source_file or ""))[1].lower()
    if extension == ".docx":
        return "DOCX explicit-break logical segment %d" % ordinal
    if extension == ".pptx":
        return "PPTX slide %d" % ordinal
    if extension == ".pdf":
        return "PDF page %d" % ordinal
    return "source location %d" % ordinal


# ---- D4: 保守题型启发——只认高置信形态，判不准保持 subjective（汇总警报交 AI 复核）----
# 选项行只认【大写】A-D（(a)(b) 小写通常是小问不是选项，绝不猜）
_OPTION_LINE_RE = re.compile(r"^[ \t]*[（(]?([A-H])[）)．.、:：][ \t]*(?![a-z]\.)(\S.*)$")
_BLANK_RUN_RE = re.compile(r"[_＿]{3,}")


def _strip_answer_prefix(ans_text):
    """剥解答标题（Quiz X.Y Solution / Answer: / 答案：）与键前缀（1. / 1(a). / 1a.）——
    choice/fill_blank 的答案归一共用。"""
    t = " ".join(str(ans_text or "").split())
    # 带编号的标题形无歧义，可无分隔符剥（Quiz 1.1 Solution … / Problem 1 Solution …）
    t = re.sub(r"(?i)^(?:quiz|example)\s+\d+(?:\.\d+)*\s*(?:solutions?|answers?)\s*[:：.]?\s*", "", t)
    t = re.sub(r"(?i)^(?:problem|exercise|question)\s*#?\s*\d+(?:\.\d+)*"
               r"(?:\s*\([A-Za-z]\)|[A-Za-z])?\s*(?:solutions?|answers?|解答|答案)\s*[:：.]?\s*", "", t)
    # 裸题号标签键（Question 1: B / Problem 2. / 第1题：）——分隔符必带，防误剥正文
    t = re.sub(r"(?i)^(?:problem|exercise|question)\s*#?\s*\d+(?:\.\d+)*"
               r"(?:\s*\([A-Za-z]\)|[A-Za-z])?\s*[:：.]\s*", "", t)
    t = re.sub(r"^第?\s*\d+\s*题\s*[:：.]\s*", "", t)
    # 裸解答词必须带编号或分隔符才剥——「Answer 1: B」「Answer: LIFO」剥，
    # 「Solution set」这类正文短语绝不剥（过剥实测）
    t = re.sub(r"(?i)^(?:solutions?|answers?|解答|答案)"
               r"(?:\s*#?\s*\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z])?\s*[:：.]?|\s*[:：.])\s*", "", t)
    # 句点分隔符后紧跟数字的不是键是小数（Answer: 0.5 / 1.5 表示比例）——绝不剥；
    # )、 不是小数分隔符（1、0.5 / 1)0.5 的键照剥）
    t = re.sub(r"^\d+(?:\.\d+)*(?:\s*\([A-Za-z]\)|[A-Za-z])?\s*(?:[)、]\s*|\.[ \t]*(?!\d))", "", t)
    return t.strip()


def _normalize_choice_answer(ans_text, options):
    """把解答切片归一成选项字母（validator 只认 裸标签/选项全文/选项正文）：剥前缀后剩
    单个 A-H（可带括号/句点）才认；整段恰是某选项全文/正文也认；否则 None。"""
    t = _strip_answer_prefix(ans_text)
    m = re.fullmatch(r"[（(]?([A-Ha-h])[）)．.。]?", t)
    if m:
        letter = m.group(1).upper()
        labels = {str(o)[:1].upper() for o in options or []}
        # 键字母不在已抽选项里（抽取漏了选项/键错位）——不归一，让调用方降级，
        # 绝不发一个 answer 不在 options 里的 choice
        return letter if letter in labels else None
    t_cmp = re.sub(r"[\s,，;；、.。]+$", "", t)
    for o in options or []:
        body = re.sub(r"^[A-H][.．、:：)]\s*", "", str(o)).strip()
        if t == o or t_cmp == re.sub(r"[\s,，;；、.。]+$", "", body):
            return str(o)[:1]
    # 头部字母 + 分隔符/理由词（B. Because… / B 因为…）——标签无歧义，理由只是附注
    m2 = re.match(r"[（(]?([A-Ha-h])(?:[）)．.。:：]|\s+(?:because|since|因为|由于))", t)
    if m2:
        letter = m2.group(1).upper()
        labels = {str(o)[:1].upper() for o in options or []}
        return letter if letter in labels else None
    return None


def _apply_typed_answer(item):
    """按启发式题型归一答案：choice 归一成字母（归一不了降级主观——宁少标不发 validator
    必拒的题）；fill_blank 剥解答标题/键前缀（判分要对的是填的值，不是标题文本）。"""
    if not item.get("answer"):
        return
    if item.get("type") == "choice":
        na = _normalize_choice_answer(item["answer"], item.get("options"))
        if na is not None:
            item["answer"] = na
        else:
            item["type"] = "subjective"
            item.pop("options", None)
            item.pop("keywords", None)
    elif item.get("type") == "fill_blank":
        stripped = _strip_answer_prefix(item["answer"])
        if stripped:
            item["answer"] = stripped
    elif item.get("type") == "subjective" and "options" not in item:
        # 晋升通道：键归一出裸字母（1. B）说明这就是选择题——二次判型越过线索门，
        # 字母还得落在再抽出的选项标签里才晋升（双保险，绝不硬猜）
        t = _strip_answer_prefix(item["answer"])
        m = re.fullmatch(r"[（(]?([A-Ha-h])[）)．.。]?", t)
        if m:
            qt2, opts2 = _classify_question_type(item.get("question") or "", assume_choice=True)
            if qt2 == "choice" and opts2 \
                    and m.group(1).upper() in {str(o)[:1].upper() for o in opts2}:
                item["type"] = "choice"
                item["options"] = opts2
                item["answer"] = m.group(1).upper()
                item.pop("keywords", None)


def _source_language_evidence(value, kind=None, latex=None):
    """Return an auditable unit-level language classification.

    ``zxx`` is intentionally returned only for genuinely formula/symbol-only
    payloads. It is not suitable for a page/document language.
    """

    if value in (None, "", [], {}):
        return "unknown"
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    return source_language_evidence(value, kind=kind, latex=latex)


def _classify_source_language(value, kind=None, latex=None):
    """Conservatively classify only Chinese-script or clearly English prose.

    Latin script alone is not enough because it could be another language.  An
    ambiguous or mixed payload deliberately returns ``None`` and is routed to
    typed review by the ingestion layer. A formula/symbol-only semantic unit
    returns ``zxx``; this never supplies Chinese or English prose evidence.
    """

    value = _source_language_evidence(value, kind=kind, latex=latex)
    return value if value in SOURCE_UNIT_LANGUAGE_CODES else None


def _formula_only_language_value(element):
    return is_language_neutral_formula(
        element.get("text"),
        latex=element.get("latex"),
        kind=element.get("kind"),
    )


def _annotate_ingestion_languages(pages, report):
    """Attach only evidence-backed page/element language labels.

    Mixed prose deliberately remains unlabeled. A formula/symbol-only element
    gets unit-level ``zxx`` but a page never does. A short/ambiguous element
    is classified only from its own payload; page/document language is never
    copied into a semantic element.
    """

    counters = {
        "pages_inferred": 0,
        "pages_language_neutral": 0,
        "pages_unresolved": 0,
        "elements_inferred": 0,
        "elements_language_neutral": 0,
        "elements_unresolved": 0,
    }
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_language = page.get("source_language")
        if page_language not in MATERIAL_TEXT_LANGUAGE_CODES:
            page.pop("source_language", None)
            page_evidence = _source_language_evidence(page.get("text"))
            if page_evidence in MATERIAL_TEXT_LANGUAGE_CODES:
                page_language = page_evidence
                page["source_language"] = page_language
                counters["pages_inferred"] += 1
            else:
                page_language = None
                if str(page.get("text") or "").strip():
                    if page_evidence == "zxx":
                        counters["pages_language_neutral"] += 1
                    else:
                        counters["pages_unresolved"] += 1
        for element in page.get("elements") or ():
            if not isinstance(element, dict):
                continue
            explicit = element.get("source_language")
            if explicit in MATERIAL_TEXT_LANGUAGE_CODES:
                continue
            if explicit == "zxx" and _formula_only_language_value(element):
                counters["elements_language_neutral"] += 1
                continue
            element.pop("source_language", None)
            evidence = _source_language_evidence(
                element.get("text"), kind=element.get("kind"), latex=element.get("latex"))
            if evidence in MATERIAL_TEXT_LANGUAGE_CODES:
                element["source_language"] = evidence
                counters["elements_inferred"] += 1
            elif evidence == "zxx":
                element["source_language"] = "zxx"
                counters["elements_language_neutral"] += 1
            elif (str(element.get("text") or "").strip()
                  or str(element.get("latex") or "").strip()):
                counters["elements_unresolved"] += 1
    report["source_language_annotations"] = counters


def _annotate_source_languages(items, report):
    inferred = 0
    unresolved = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        original_prompt = item.get("_prompt_text")
        question_payload = (
            original_prompt
            if isinstance(original_prompt, str) and original_prompt.strip()
            else item.get("question")
        )
        explicit_question = item.get("source_language")
        if (explicit_question not in SOURCE_UNIT_LANGUAGE_CODES
                or (explicit_question == "zxx" and not is_language_neutral_formula(
                    question_payload, kind="question"))):
            item.pop("source_language", None)
            detected = _classify_source_language(question_payload, kind="question")
            if detected:
                item["source_language"] = detected
                inferred += 1
            else:
                unresolved += 1
        if item.get("answer") not in (None, "", [], {}):
            explicit_answer = item.get("answer_source_language")
            if (explicit_answer not in SOURCE_UNIT_LANGUAGE_CODES
                    or (explicit_answer == "zxx" and not is_language_neutral_formula(
                        item.get("answer"), kind="answer"))):
                item.pop("answer_source_language", None)
                detected = _classify_source_language(item.get("answer"), kind="answer")
                if detected:
                    item["answer_source_language"] = detected
                    inferred += 1
                else:
                    unresolved += 1
    if inferred:
        report["warnings"].append(
            "source_language_inferred: %d question/answer payloads were classified by "
            "conservative Unicode/English-signal/formula-only rules" % inferred
        )
    if unresolved:
        report["warnings"].append(
            "source_language_review_required: %d question/answer payloads remain "
            "unclassified and will enter typed review" % unresolved
        )


# 选择题提示词——题干里得有「选/哪/下列/which/choose…」这类线索，大写小问
# （A. Find f'(x). B. Compute…）才不会光凭字母序列被误判成选择题
_CHOICE_CUE_RE = re.compile(
    r"(?i)which|select|choose|circle|correct|incorrect|true|false|multiple\s*choice"
    r"|下列|以下|哪|选|正确|错误|属于|符合|判断")


def _classify_question_type(q_text, assume_choice=False):
    """(type, options)。≥2 个从 A 起按序排列的大写选项行 → choice + options（续行并入上一项）；
    题面带填空线 → fill_blank；其余一律 subjective——启发式判不准绝不硬猜别的型。"""
    letters, opts, block_open = [], [], False
    for ln in (q_text or "").splitlines():
        m = _OPTION_LINE_RE.match(ln)
        if m:
            letters.append(m.group(1))
            opts.append("%s. %s" % (m.group(1), m.group(2).strip()))
            block_open = True
        elif not ln.strip():
            block_open = False                         # 空行结束选项块——其后说明行不并入
        elif opts and block_open:
            if re.match(r"(?i)^\s*(?:(?:explain|show|justify|prove|describe|discuss|note|hints?|"
                        r"circle|select|choose|mark|write)\b|(?:e\.?\s*g|i\.?\s*e|n\.?\s*b)\.?[.:：\s]"
                        r"|请|说明|解释|证明|注意|提示|选出|圈出|写出)",
                        ln) or _HW_ANSBOX_INSTR_RE.search(ln):
                block_open = False                     # 紧随选项的答题指令不是选项续行
            else:
                opts[-1] = opts[-1] + " " + ln.strip()     # 选项跨行——续行并入上一项
    if len(letters) >= 2 and letters == [chr(65 + i) for i in range(len(letters))]:
        stem_lines = []
        for ln in (q_text or "").splitlines():
            if _OPTION_LINE_RE.match(ln):
                break
            stem_lines.append(ln)
        if assume_choice or _CHOICE_CUE_RE.search(" ".join(stem_lines)):
            return "choice", opts
        return "subjective", None          # 无选择线索的字母清单是小问列表，不猜
    # 行内选项：讲义题面常被空白折叠成一行（… A. 对的 B. 错的）——只认 A．/A:/（A）
    # 这类点号冒号/全角括号形，不认英文半角 (A)（散文引用「见(A)节」会误判）
    t = q_text or ""
    # 选项前可以是全角冒号/逗号/顿号（选一个：A. …）——不只空白
    ms = list(re.finditer(r"(?:^|[\s：:，,。；;、])(?:（([A-H])）|([A-H])[．.、:：])[ \t]*(?![a-z]\.)", t))
    seq = [(m.group(1) or m.group(2)) for m in ms]
    if len(seq) >= 2 and seq == [chr(65 + i) for i in range(len(seq))] \
            and (assume_choice or _CHOICE_CUE_RE.search(t[:ms[0].start()])):
        opts2 = []
        for j, m in enumerate(ms):
            end = ms[j + 1].start() if j + 1 < len(ms) else len(t)
            body = t[m.end():end].strip()
            if j == len(ms) - 1 and body:
                # 折叠成单行的题面里，末选项后常粘答题指令——在「小写/CJK 后接大写指令动词
                # 或中文指令词」的句界截断；write-back 这类小写正文不受影响
                cut = re.search(r"(?<=[a-z0-9）)\u4e00-\u9fff])\s+(?=(?:Explain|Show|Justify|"
                                r"Prove|Describe|Discuss|Note|Hints?|Circle|Select|Choose|Mark|"
                                r"Write)\b)|(?<=[a-z0-9）)\u4e00-\u9fff])\s*"
                                r"(?=请|说明|解释|证明|注意|提示|选出|圈出|写出)", body)
                if cut:
                    body = body[:cut.start()].strip()
            body = body.rstrip(",，;；、 ")             # 行内分隔符（A. foo, B. bar）不留在选项体
            if not body:
                opts2 = []
                break
            opts2.append("%s. %s" % (seq[j], body))
        if opts2:
            return "choice", opts2
    prev = ""
    for ln in (q_text or "").splitlines():
        if _BLANK_RUN_RE.search(ln):
            # 只有【标签形状】才算答题栏：标签紧贴空线（Answer: ____）、整行是指示语、
            # 或上一行以答案标签结尾——「Answer the following: f(x)= ____」是真填空题
            label_before = re.search(r"(?i)(?:answers?|solutions?|答案|解答|作答|答题)"
                                     r"[处栏]?\s*[:：]?\s*[_＿]", ln)
            label_prev = re.search(r"(?i)(?:answers?|solutions?|答案|解答|作答|答题)"
                                   r"[处栏]?\s*[:：]?\s*$", prev)
            residual = _BLANK_RUN_RE.sub("", ln)
            residual = re.sub(r"[（(][^（）()]{0,24}[)）]", "", residual)          # (5 pts) 类标注不算内容
            residual = re.sub(r"[/／]?\s*\d+\s*(?:(?:points?|pts?|marks?)\b|分)", "", residual).strip()
            embedded = bool(re.search(r"[^\W_]", residual))
            # 「Fill in the blank / 填空」是明说的正面信号——优先于答题栏抑制
            #（指示语词典的 in the blank 恰好会撞上它）
            if re.search(r"(?i)fill\s+in\s+the\s+blanks?|填空", ln + " " + prev):
                return "fill_blank", None
            # 整行只有下划线（证明题末尾的书写区）不是题面挖空——空线必须嵌在文字里
            if embedded and not (label_before or label_prev or _HW_ANSBOX_INSTR_RE.search(ln)):
                return "fill_blank", None
        if ln.strip():
            prev = ln
    return "subjective", None


def _rel(path, base):
    """Workspace-relative POSIX identifier for a material file (keeps subdir uniqueness, e.g.
    lecture/ch01.pdf vs homework/ch01.pdf, so same-named files in different folders don't collide)."""
    return os.path.relpath(path, base).replace(os.sep, "/")


def _explicit_raster_sidecars(image_path):
    """Return only unambiguous OCR-sidecar naming conventions.

    A generic same-stem ``.txt``/``.md`` file may be unrelated course material,
    so it is never auto-bound to an image.  The supported explicit conventions
    are ``diagram.ocr.txt`` and ``diagram.png.txt``.
    """

    stem, unused_extension = os.path.splitext(image_path)
    candidates = (stem + ".ocr.txt", image_path + ".txt")
    return [candidate for candidate in candidates if os.path.isfile(candidate)]


def _media_type_for_path(path):
    extension = os.path.splitext(path)[1].lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(extension, "application/octet-stream")


def _installed_parser_versions(backend):
    candidates = {
        "pypdf": "pypdf",
        "fitz": "PyMuPDF",
        "pymupdf": "PyMuPDF",
        "pypdfium2": "pypdfium2",
        "pillow": "Pillow",
    }
    tokens = set(str(getattr(backend, "name", "core")).lower().split("+"))
    output = []
    for token in sorted(tokens):
        distribution = candidates.get(token)
        if distribution is None:
            continue
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        output.append("%s=%s" % (distribution, version))
    return ";".join(output) or None


def _core_parser_receipts(materials, source_paths, pages, source_hashes, backend, report):
    pages_by_source = {}
    for page in pages:
        source_file = page.get("file") if isinstance(page, dict) else None
        page_number = page.get("page") if isinstance(page, dict) else None
        if isinstance(source_file, str) and type(page_number) is int and page_number >= 1:
            pages_by_source.setdefault(source_file, set()).add(page_number)
    skipped = {
        str(row.get("file")).replace("\\", "/")
        for row in report.get("skipped", ())
        if isinstance(row, dict) and row.get("file")
    }
    backend_name = str(getattr(backend, "name", "core"))
    version = _installed_parser_versions(backend)
    receipts = []
    for source_path in sorted(source_paths):
        source_file = _rel(source_path, materials)
        produced = sorted(pages_by_source.get(source_file, ()))
        extension = os.path.splitext(source_path)[1].lower()
        if extension in (".txt", ".md"):
            module = "stdlib:text"
            adapter_version = sys.version.split()[0]
        elif extension in (".docx", ".pptx", ".xlsx"):
            module = "stdlib:xlsx" if extension == ".xlsx" else "stdlib:ooxml"
            adapter_version = sys.version.split()[0]
        elif extension in (
                ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"):
            module = "stdlib:raster"
            adapter_version = sys.version.split()[0]
        elif extension == ".pdf":
            module = backend_name
            adapter_version = version
        else:
            module = "unsupported"
            adapter_version = None
        config = {
            "backend": backend_name,
            "parser": module,
        }
        config_hash = hashlib.sha256(json.dumps(
            config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        receipts.append({
            "schema_version": 1,
            "adapter": "core",
            "adapter_version": adapter_version,
            "module": module,
            "distribution": None,
            "source_file": source_file,
            "source_sha256": source_hashes[source_path],
            "media_type": _media_type_for_path(source_path),
            "requested_pages": [],
            "produced_pages": produced,
            "discovered_page_count": len(produced),
            "config_sha256": config_hash,
            "policy": {"network": False, "upload": False, "install": False},
            "status": (
                "success" if produced else "unsupported" if extension not in (
                    ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md",
                    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
                ) else "failed" if source_file in skipped else "review_required"
            ),
        })
    return receipts


def _merge_parser_receipts(core_receipts, optional_receipts):
    merged = {receipt["source_file"]: receipt for receipt in core_receipts}
    for receipt in optional_receipts:
        merged[receipt["source_file"]] = receipt
    return [merged[key] for key in sorted(merged)]


# 常用汉字频表（简体+繁体各 ~120 高频字 + 课程域词）——GBK/Big5 双解码都成功时的确定性裁决：
# 正确解码产出常用字、错误解码产出生僻乱码，占比一目了然；两边都低分则按不确定移交 AI
_COMMON_HAN = set(
    "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以会家可"
    "下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美总从无情己面"
    "最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间知世什两次使身者被高已"
    "亲其进此话常与活正感讲义课作业答案试卷题章节复习练习繁简编码点"
    "這中大來上國個到說們為子和地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多"
    "天而能好都然沒日於起還發成事作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動"
    "兩長知民樣現分將外但身些與高意進法此月者必講義課作業答案試卷題章節複習練習繁簡編碼點體")


def _han_score(t):
    """解码文本里 CJK 字符落在常用字表的占比（无 CJK 记 0）。"""
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    if not cjk:
        return 0.0
    return sum(1 for c in cjk if c in _COMMON_HAN) / len(cjk)


def _read_text_file_pages(path, rel, report=None):
    """UTF-8 严格解码；失败对 GBK/Big5 双解码——只解得开一个就用它（带警告），两个都解得开
    按常用字频裁决（Big5 字节流常常也是合法 GBK，先到先得会把繁体解成乱码入库）；
    两边都像乱码则移交 AI，绝不 errors=replace 静默灌乱码。"""
    with open(path, "rb") as f:
        raw_b = f.read()
    raw = None
    try:
        raw = raw_b.decode("utf-8-sig")
    except (UnicodeDecodeError, ValueError):
        cands = []
        for enc in ("gbk", "big5"):
            try:
                cands.append((enc, raw_b.decode(enc)))
            except (UnicodeDecodeError, ValueError):
                continue
        if len(cands) == 1:
            enc, raw = cands[0]
        elif len(cands) == 2:
            # 相对胜者入库（严格解码成功的数据绝不丢）；平局偏 GBK（简体为主要受众，已文档化）。
            # 置信度低（常用字占比 <0.25）时额外进核对清单——入库但大声标注，不静默也不丢
            scored = sorted(((_han_score(t), i, enc, t) for i, (enc, t) in enumerate(cands)),
                            key=lambda x: (-x[0], x[1]))
            _sc, _, enc, raw = scored[0]
            if _sc < 0.25 and report is not None:
                report["warnings"].append(
                    "encoding_uncertain: %s（GBK/Big5 均可解且常用字占比低——已按 %s 入库，请核对是否乱码）"
                    % (rel, enc.upper()))
                report["ai_review"].append({
                    "kind": "encoding_uncertain", "file": rel,
                    "action": "该文本非 UTF-8 且 GBK/Big5 都能解码、置信度低。已按 %s 解码入库——"
                              "请 AI 检查工作区里该文件内容是否乱码，乱码则转存 UTF-8 后重新构建。" % enc.upper()})
        if raw is not None and report is not None:
            report["warnings"].append(
                "encoding_fallback: %s（不是 UTF-8，按 %s 解码（常用字占比裁决）——"
                "若内容乱码请转存 UTF-8 后重新构建）" % (rel, enc.upper()))
    if raw is None:
        if report is not None:
            report["skipped"].append({"file": rel, "why": "无法解码（UTF-8/GBK/Big5 都失败）"})
            report["warnings"].append("undecodable_text: %s（跳过——请转存 UTF-8 后重新构建）" % rel)
            report["ai_review"].append({
                "kind": "undecodable_text", "file": rel,
                "action": "该文本文件无法解码，内容未导入。请 AI 读取原文件判断编码、转存 UTF-8 后重新运行构建。"})
        return []
    parts = raw.split("\f") if "\f" in raw else [raw]
    return [{"file": rel, "page": i + 1, "text": p} for i, p in enumerate(parts)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Official course materials -> raw_input.json (+ optional page-image assets + parse report), consumed by ingest.py.",
        epilog="Optional deps: text extraction pip install pypdf or pymupdf; rendering pip install pymupdf (bundles PNG) or pypdfium2 Pillow. "
               "(.txt/.md materials need none.)")
    p.add_argument("--materials", required=True,
                   help="course materials folder (PDF / DOCX / PPTX / XLSX / images / txt / md)")
    p.add_argument(
        "--out", default="raw_input.json",
        help=("output raw_input.json path; with --asset-root workspace mode this must be "
              "<workspace>/.ingest/source_raw_input.json"),
    )
    p.add_argument(
        "--report", default="parse_report.json",
        help=("parse report JSON path; with --asset-root workspace mode this must be "
              "<workspace>/.ingest/parse_report.json"),
    )
    p.add_argument("--asset-root", default=None,
                   help="directory for rendered page images; should point at <workspace>/references/assets. "
                        "with rendering on but unset: auto skips with a warning, required errors out")
    p.add_argument("--render-pages", choices=["never", "auto", "required"], default="auto",
                   help="render figure-dependent pages: never/auto/required (required errors out without a rendering backend / --asset-root)")
    p.add_argument("--extract-lecture-questions", choices=["never", "auto"], default="auto",
                   help="extract lecture Example/Quiz items: never/auto")
    p.add_argument("--extract-homework", choices=["never", "auto"], default="auto",
                   help="extract homework items (incl. auto-pairing of split question/answer PDFs and inline Solutions): never/auto")
    p.add_argument("--course-name", default=None, help="subject name (defaults to the materials directory name)")
    p.add_argument(
        "--crop-annotations",
        default=None,
        help=(
            "optional UTF-8 JSONL of explicit model/human target-item crop "
            "annotations; each row adds preview_path beside the strict "
            "CropAnnotation fields"
        ),
    )
    p.add_argument(
        "--ingest-adapter", choices=("core", "docling", "mineru"), default="core",
        help=argparse.SUPPRESS,
    )
    return p


def _publication_targets_alias(left, right):
    """Return whether two output spellings can address the same filesystem entry."""
    left = os.path.abspath(str(left))
    right = os.path.abspath(str(right))
    if os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right)):
        return True
    try:
        if os.path.lexists(left) and os.path.lexists(right) and os.path.samefile(left, right):
            return True
    except OSError:
        pass
    return (
        os.path.normcase(os.path.normpath(os.path.realpath(left)))
        == os.path.normcase(os.path.normpath(os.path.realpath(right)))
    )


def _validate_distinct_publication_targets(args):
    out = getattr(args, "out", None)
    report = getattr(args, "report", None)
    if not out or not report:
        raise ValueError("--out and --report must both be non-empty paths")
    manifest = os.path.join(
        os.path.dirname(os.path.abspath(report)), "ai_review_manifest.json"
    )
    pairs = (
        (out, report, "--out and --report"),
        (out, manifest, "--out and ai_review_manifest.json"),
        (report, manifest, "--report and ai_review_manifest.json"),
    )
    for left, right, label in pairs:
        if _publication_targets_alias(left, right):
            raise ValueError("%s must be distinct physical publication targets" % label)


def _publication_workspace(args, explicit=None):
    """Resolve the workspace whose validator-visible outputs this run publishes.

    The official workspace shape is unambiguous from ``.ingest/*.json`` and/or
    ``references/assets``.  Reject mixed roots instead of locking one workspace
    while writing another.
    """

    _validate_distinct_publication_targets(args)
    candidates = []
    if explicit is not None:
        candidates.append(os.path.abspath(str(explicit)))
    for value in (getattr(args, "out", None), getattr(args, "report", None)):
        if not value:
            continue
        parent = os.path.dirname(os.path.abspath(value))
        if os.path.basename(parent) == ".ingest":
            candidates.append(os.path.dirname(parent))
        elif os.path.basename(parent).casefold() == ".ingest":
            raise ValueError(
                "workspace ingestion outputs must use the canonical .ingest directory spelling"
            )
    asset_root = getattr(args, "asset_root", None)
    if asset_root:
        asset_root = os.path.abspath(asset_root)
        references = os.path.dirname(asset_root)
        if not (os.path.basename(asset_root) == "assets"
                and os.path.basename(references) == "references"):
            raise ValueError(
                "--asset-root must be exactly <workspace>/references/assets"
            )
        candidates.append(os.path.dirname(references))
    if not candidates:
        raise ValueError(
            "standalone material scanning is not exposed by the student CLI; use the "
            "lightweight visual path or canonical ingest_course.py with an explicitly "
            "confirmed full workspace"
        )
    canonical = {os.path.normcase(os.path.normpath(path)) for path in candidates}
    if len(canonical) != 1:
        raise ValueError("validator-visible builder outputs target different workspaces")
    workspace = candidates[0]
    expected_out = os.path.join(workspace, ".ingest", "source_raw_input.json")
    expected_report = os.path.join(workspace, ".ingest", "parse_report.json")
    supplied_out = os.path.abspath(str(getattr(args, "out", "")))
    supplied_report = os.path.abspath(str(getattr(args, "report", "")))
    if (os.path.basename(os.path.dirname(supplied_out)) != ".ingest"
            or os.path.basename(supplied_out) != "source_raw_input.json"
            or os.path.normcase(os.path.normpath(supplied_out))
            != os.path.normcase(os.path.normpath(expected_out))):
        raise ValueError(
            "workspace mode requires --out exactly <workspace>/.ingest/source_raw_input.json"
        )
    if (os.path.basename(os.path.dirname(supplied_report)) != ".ingest"
            or os.path.basename(supplied_report) != "parse_report.json"
            or os.path.normcase(os.path.normpath(supplied_report))
            != os.path.normcase(os.path.normpath(expected_report))):
        raise ValueError(
            "workspace mode requires --report exactly <workspace>/.ingest/parse_report.json"
        )
    if _path_has_link_or_reparse(workspace):
        raise ValueError("builder publication workspace is link/reparse-backed")
    try:
        exam_start.require_full_processing(
            workspace,
            materials=getattr(args, "materials", None),
            purpose="workspace material builder publication",
        )
    except exam_start.FullProcessingRequired as exc:
        raise ValueError(str(exc)) from exc
    os.makedirs(workspace, exist_ok=True)
    if not os.path.isdir(workspace) or _path_has_link_or_reparse(workspace):
        raise ValueError("builder publication workspace is not a safe directory")
    return workspace


def _writes_ingestion_files(args, workspace):
    ingest = os.path.normcase(os.path.normpath(os.path.join(workspace, ".ingest")))
    return any(
        os.path.normcase(os.path.normpath(os.path.dirname(os.path.abspath(value))))
        == ingest
        for value in (getattr(args, "out", None), getattr(args, "report", None))
        if value
    )


def _run_unlocked_core(args, backend=None, adapter_runner=None):
    """Core run. `backend` injectable for tests (a fake with page_texts/render_page_png)."""
    backend = backend or detect_backend()
    report = {"materials": args.materials, "backend": getattr(backend, "name", "none"),
              "files_scanned": [], "pages_extracted": 0, "pages_rendered": 0,
              "examples_detected": 0, "quizzes_detected": 0, "pairs_detected": 0,
              "teaching_examples_detected": 0,
              "teaching_example_roles": {"paired_problem": 0, "worked_example": 0},
              "skipped": [], "warnings": [], "ai_review": [],
              "crop_receipts": []}

    # Heavy parsers belong to a separately configured remote/cloud integration,
    # not this local builder process.  Reject before scanning or hashing any course
    # material, and never accept a callable as an implied local integration.
    if args.ingest_adapter != "core":
        try:
            selected = resolve_adapter(args.ingest_adapter, runner=adapter_runner)
            report["parser_adapter_capability"] = selected.probe().to_dict()
        except AdapterError as exc:
            return 3, {"error": str(exc)}, report
        return 3, {
            "error": (
                "%s is remote/cloud-host-only. The local student runtime never "
                "probes, downloads, installs, imports, executes, or accepts a local "
                "runner for Docling/MinerU. Use a separately configured remote host "
                "only after an explicit named request and upload/privacy consent."
                % args.ingest_adapter
            )
        }, report

    materials = args.materials
    if not os.path.isdir(materials):
        return 2, {"error": "materials 目录不存在: %s" % materials}, None

    pdfs, documents, pruned, others = _scan_materials(materials)
    unsafe_sources = [
        path for path in (pdfs + documents + others)
        if _path_has_link_or_reparse(path)
    ]
    if unsafe_sources:
        unsafe_set = set(unsafe_sources)
        pdfs = [path for path in pdfs if path not in unsafe_set]
        documents = [path for path in documents if path not in unsafe_set]
        others = [path for path in others if path not in unsafe_set]
        for path in sorted(unsafe_sources):
            relative = _rel(path, materials)
            report["skipped"].append({
                "file": relative,
                "why": "源文件路径经过符号链接/junction/reparse point，拒绝读取",
            })
            report["ai_review"].append({
                "kind": "unsafe_source_link",
                "file": relative,
                "action": "请把材料复制为 materials 目录内的常规文件后重新构建。",
            })
    texts = [path for path in documents if path.lower().endswith((".txt", ".md"))]
    ooxmls = [path for path in documents if path.lower().endswith((".docx", ".pptx"))]
    xlsxs = [path for path in documents if path.lower().endswith(".xlsx")]
    rasters = [path for path in documents if path.lower().endswith((
        ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ))]
    for op in others:
        rel_o = _rel(op, materials)
        report["skipped"].append({
            "file": rel_o,
            "why": "不支持的格式（当前解析 PDF/DOCX/PPTX/XLSX/常见图片/.txt/.md）",
        })
        report["warnings"].append("unsupported_format: %s（内容不会进 wiki/题库——请 AI 接管）" % rel_o)
        report["ai_review"].append({
            "kind": "unsupported_format", "file": rel_o,
            "action": "该文件格式本工具不解析、内容未导入。请 AI 直接读取该文件，把知识点/题目"
                      "以带证据的 review patch 补进工作区，或转换为受支持格式后重新构建。"})
    all_source_paths = texts + ooxmls + xlsxs + rasters + pdfs + others
    report["files_scanned"] = [_rel(p, materials) for p in all_source_paths]
    report["pruned_dirs"] = pruned
    if pruned:   # fail-loud: a prior workspace/tooling dir was skipped, so the user knows why it's ignored
        report["warnings"].append("pruned_non_material_dirs: %s（不当作课程材料扫描）" % "、".join(pruned[:8]))
    if not all_source_paths:
        report["warnings"].append("no_material_files")
        return 4, {"error": "--materials 中没有发现任何课程材料文件。"}, report

    source_snapshot_hashes = {}
    try:
        for source_path in all_source_paths:
            _sha256_path(source_path, source_snapshot_hashes)
    except OSError as exc:
        report["warnings"].append("source_snapshot_failed: %s" % exc)
        return 5, {"error": "提取前无法建立材料哈希快照：%s" % exc}, report

    registered_source_paths = {
        os.path.normcase(os.path.abspath(path)): path for path in all_source_paths
    }

    selected_adapter = None

    # Honest dependency failure: PDFs present but no text backend → stop with a clear, actionable error.
    if pdfs and selected_adapter is None and not backend.can_text():
        report["warnings"].append("no_pdf_text_backend")
        return 3, {"error": "发现 %d 个 PDF，但没有可用的 PDF 文本后端。请安装可选依赖："
                            "`pip install pypdf` 或 `pip install pymupdf`（PDF 文本提取需要其一；把页面渲染成图还需 "
                            "`pip install pymupdf` 或 `pypdfium2 Pillow`——只装 pypdfium2 而无 Pillow 不会启用渲染）。"
                            "纯 .txt/.md 材料无需任何依赖。" % len(pdfs)}, report

    pages = []
    optional_parser_receipts = []
    residue_files = {}
    residue_page_keys = set()
    page_pdf_all_raw = {}

    def record_adapter_signals(records, relative):
        for record in records:
            for signal in record.get("review_signals") or ():
                reason = signal.get("reason_code") or "adapter_review_required"
                detail = signal.get("detail") or "adapter output needs visual review"
                location = _source_location_label(relative, record["page"])
                report["warnings"].append(
                    "%s: %s %s（%s）" % (reason, relative, location, detail)
                )
                report["ai_review"].append({
                    "kind": reason,
                    "file": relative,
                    "pages": [record["page"]],
                    "action": (
                        "%s。请直接视觉核对原始文件的 %s，并用带来源证据的 review "
                        "patch 补录遗漏内容。" % (detail, location)
                    ),
                })

    def optional_extract(source_path, relative):
        request = ExtractionRequest.from_path(
            source_path,
            relative,
            _media_type_for_path(source_path),
            asset_root=args.asset_root,
            config={"route": args.ingest_adapter},
        )
        result = selected_adapter.extract(request)
        optional_parser_receipts.append(result.receipt.to_dict())
        report["warnings"].extend(result.warnings)
        records = [dict(record) for record in result.pages]
        record_adapter_signals(records, relative)
        return records

    for tp in texts:
        pages.extend(_read_text_file_pages(tp, _rel(tp, materials), report))
    for package_path in ooxmls:
        rel = _rel(package_path, materials)
        try:
            extracted = (
                optional_extract(package_path, rel)
                if selected_adapter is not None
                else extract_ooxml(
                    package_path,
                    rel,
                    asset_root=args.asset_root,
                    expected_sha256=source_snapshot_hashes[package_path],
                )
            )
            for record in extracted:
                # OOXML assets are filenames relative to --asset-root.  The ingestion
                # envelope later canonicalizes them as references/assets/<filename>.
                pages.append(record)
                for signal in (() if selected_adapter is not None else (
                        record.get("review_signals") or ())):
                    reason = signal.get("reason_code") or "ooxml_review_required"
                    detail = signal.get("detail") or "OOXML content needs visual review"
                    location = _source_location_label(rel, record["page"])
                    report["warnings"].append(
                        "%s: %s %s（%s）" % (reason, rel, location, detail)
                    )
                    report["ai_review"].append({
                        "kind": reason,
                        "file": rel,
                        "pages": [record["page"]],
                        "action": (
                            "%s。请直接视觉核对原始文件的 %s，并用带来源证据的 "
                            "review patch 补录遗漏内容。" % (detail, location)
                        ),
                    })
        except (OOXMLExtractionError, AdapterError) as exc:
            report["skipped"].append({"file": rel, "why": "OOXML 提取失败: %s" % exc})
            report["warnings"].append("ooxml_extract_failed: %s（%s——整份内容未导入）" % (rel, exc))
            report["ai_review"].append({
                "kind": "ooxml_extract_failed",
                "file": rel,
                "action": "DOCX/PPTX 安全解析失败。请核对文件是否损坏、加密或含外部关系；"
                          "修复后重建，或直接视觉阅读并提交带证据的 review patch。",
            })
    for workbook_path in xlsxs:
        rel = _rel(workbook_path, materials)
        try:
            extracted = extract_xlsx(
                workbook_path,
                rel,
                asset_root=args.asset_root,
                expected_sha256=source_snapshot_hashes[workbook_path],
            )
            pages.extend(extracted)
            record_adapter_signals(extracted, rel)
        except XLSXExtractionError as exc:
            report["skipped"].append({"file": rel, "why": "XLSX 提取失败: %s" % exc})
            report["warnings"].append("xlsx_extract_failed: %s（%s）" % (rel, exc))
            report["ai_review"].append({
                "kind": "xlsx_extract_failed", "file": rel,
                "action": "工作簿未导入；请检查损坏/加密/外部关系，或转成受支持格式后重建。",
            })
    for raster_path in rasters:
        rel = _rel(raster_path, materials)
        try:
            sidecars = []
            for candidate in _explicit_raster_sidecars(raster_path):
                registered = registered_source_paths.get(
                    os.path.normcase(os.path.abspath(candidate))
                )
                if registered is not None and registered not in sidecars:
                    sidecars.append(registered)
            sidecar_path = None
            if len(sidecars) == 1:
                sidecar_path = sidecars[0]
            elif len(sidecars) > 1:
                report["warnings"].append(
                    "raster_sidecar_ambiguous: %s (%s)"
                    % (rel, ", ".join(_rel(path, materials) for path in sidecars))
                )
                report["ai_review"].append({
                    "kind": "raster_sidecar_ambiguous",
                    "file": rel,
                    "pages": [1],
                    "action": (
                        "Multiple explicitly named OCR sidecars exist. Inspect the image and "
                        "sidecars, retain one exact revision link, and rebuild."
                    ),
                })
            sidecar_rel = _rel(sidecar_path, materials) if sidecar_path else None
            extracted = extract_raster(
                raster_path,
                rel,
                asset_root=args.asset_root,
                sidecar_path=sidecar_path,
                auto_sidecar=False,
                sidecar_source_file=sidecar_rel,
                expected_sha256=source_snapshot_hashes[raster_path],
                expected_sidecar_sha256=(
                    source_snapshot_hashes[sidecar_path] if sidecar_path else None
                ),
            )
            pages.extend(extracted)
            record_adapter_signals(extracted, rel)
        except RasterExtractionError as exc:
            report["skipped"].append({"file": rel, "why": "图片提取失败: %s" % exc})
            report["warnings"].append("raster_extract_failed: %s（%s）" % (rel, exc))
            report["ai_review"].append({
                "kind": "raster_extract_failed", "file": rel,
                "action": "独立图片未导入；请检查格式/损坏，或用视觉工具读取后提交证据补丁。",
            })
    for pdf in pdfs:
        rel = _rel(pdf, materials)   # subdir-qualified identifier, not bare basename (avoids collisions)
        try:
            nonblank, no_content, total = 0, [], 0
            extracted_pages = []
            if selected_adapter is not None:
                adapted = optional_extract(pdf, rel)
                pdf_texts = [record.get("text") or "" for record in adapted]
                text_methods = []
            else:
                adapted = None
                pdf_texts = list(backend.page_texts(pdf))
                text_methods = list(getattr(backend, "last_text_methods", ()) or ())
            default_text_method = getattr(
                backend, "name", backend.__class__.__name__.lower()
            )
            for i, txt in enumerate(pdf_texts):
                if adapted is not None:
                    page_record = dict(adapted[i])
                    page_record["_pdf"] = pdf
                    page_record["_text_method"] = args.ingest_adapter
                else:
                    page_record = {
                        "file": rel,
                        "page": i + 1,
                        "text": txt,
                        "_pdf": pdf,
                        "_text_method": (
                            text_methods[i] if i < len(text_methods) else default_text_method
                        ),
                    }
                extracted_pages.append(page_record)
                total += 1
                # 残渣感知判定：每页只剩页码「12」的扫描件不能算有文本（审计实测骗过精确空判定）
                actual_page = page_record["page"]
                if _page_has_content(txt):
                    nonblank += 1
                else:
                    no_content.append(actual_page)
            if text_methods:
                report.setdefault("pdf_text_methods", {})[rel] = text_methods
            # Commit per source only after the backend reaches EOF.  A generator
            # that yields p.1 then raises on p.2 must not masquerade as a complete
            # one-page document.
            pages.extend(extracted_pages)
            page_pdf_all_raw.update(
                ((rel, record["page"]), pdf) for record in extracted_pages
            )
            if nonblank == 0:   # image-only/scanned PDF: pypdf returns "" per page → no usable text
                # 先记账不立刻丢——整册可能是配对管线认领的裸答案册（整本就一个「4」）；
                # 分类后未被认领的才从管线剔除并移交 AI
                residue_files[rel] = no_content
            elif no_content:    # 混排文件里的扫描页：内容不会进 wiki/题库，必须留痕移交
                residue_page_keys.update((rel, n) for n in no_content)
                report["warnings"].append(
                    "pdf_pages_no_text: %s（%d/%d 页无有效文本：p%s——疑似扫描/纯图页，这些页的内容"
                    "不会进 wiki/题库）" % (rel, len(no_content), total, _fmt_pages(no_content)))
                report["ai_review"].append({
                    "kind": "pages_no_text", "file": rel, "pages": no_content,
                    "action": "这些页在 PDF 里无有效文本（疑似扫描/插图页）。请 AI 用多模态阅读该 PDF "
                              "的第 %s 页，判断是否有知识点/题目需要手工补录。" % _fmt_pages(no_content)})
        except Exception as e:  # backend present but failed on this one file → skip it, keep going
            report["skipped"].append({"file": rel, "why": "PDF 文本提取失败: %s" % e})
            report["warnings"].append("pdf_extract_failed: %s（%s——整本内容未导入）" % (rel, e))
            report["ai_review"].append({
                "kind": "extract_failed", "file": rel,
                "action": "PDF 文本提取抛异常，整本内容未导入。请 AI 用多模态直接阅读该 PDF 补录，"
                          "或检查文件是否损坏/加密。"})

    report["pages_extracted"] = len(pages)
    # Preserve the loss-resistant page inventory before legacy quiz/wiki filters
    # discard answer books, residue pages, or fully scanned sources.
    ingestion_pages = [dict(page) for page in pages]

    def _flush_residue(claimed):
        # 整本无有效文本的 PDF：被作业/试卷管线认领的（裸答案册，整本就一个「4」）内容有效使用；
        # 未被认领的从管线剔除（残渣绝不写进 wiki）并移交 AI
        nonlocal pages
        for rf in sorted(set(residue_files) - claimed):
            pages = [pg for pg in pages if pg["file"] != rf]
            report["skipped"].append({"file": rf, "why": "PDF 文本为空（可能是扫描件/图片 PDF，需 OCR，本工具不做）"})
            report["warnings"].append("pdf_no_text: %s" % rf)
            report["ai_review"].append({
                "kind": "scanned_pdf", "file": rf, "pages": residue_files[rf],
                "action": "整本无有效文本（疑似扫描件/纯图 PDF），内容未导入。请 AI 用多模态直接"
                          "阅读该 PDF，把知识点/题目手工补进工作区。"})
        residue_files.clear()

    # Process success and study readiness are separate: a fully scanned/unsupported
    # collection still produces an inspectable blocked workspace and typed review queue.
    if not any(_page_has_content(p.get("text")) for p in pages):
        _flush_residue(set())
        report["warnings"].append("no_text_extracted")
        report["ai_review"].append({
            "kind": "no_text_extracted",
            "file": "(all)",
            "action": "没有自动提取到可教学文本。工作区会以 blocked 状态生成；请逐页视觉/OCR 接管，"
                      "形成带页码证据的内容单元后再解除阻塞。",
        })

    _mat_root_name = os.path.basename(os.path.normpath(os.path.abspath(materials)))
    # 作业/解答文件在【抽取前】就按文件名剔出讲义管线——lecture 的题/答配对跨页进行，
    # 事后过滤只能拦 source_file，拦不住讲义题从作业文件吸走 answer_source_file
    hw_related = set()
    exam_rerouted = set()
    if getattr(args, "extract_homework", "auto") != "never":
        _hwf, _pairing = classify_homework_files(sorted({pg["file"] for pg in pages}), _mat_root_name)
        hw_related = set(_hwf) | set(_pairing)
        # 试卷命名但内容是讲义式 Quiz/Example 标记（quiz3.pdf 内含 Quiz 1.1）——作业/试卷解析器
        # 不认 Quiz X.Y Solution，硬解析会把解答文本卷进题面（泄答案）；讲义标记数不少于
        # 作业式题标记数就归还讲义管线（wiki 排除照旧：文件仍按作业类挡在 wiki 外），
        # 归还集同时传给作业抽取端，绝不两头都吃
        exam_rerouted = set()
        for _f in sorted(hw_related):
            if not _is_exam_path(_f, _mat_root_name):
                continue
            _st0, _b0 = _file_stream(pages, _f)
            _lecm = detect_lecture_markers(_st0)
            _probct = sum(1 for m in _hw_markers(_st0) if m.get("role") == "problem")
            if _lecm and len(_lecm) >= max(1, _probct):
                hw_related.discard(_f)
                exam_rerouted.add(_f)
                report["warnings"].append(
                    "exam_lecture_style: %s（试卷命名但内容以讲义式 Quiz/Example 标记为主——"
                    "已按讲义题导入 quiz_bank，正文不进 wiki）" % _f)
        # 解答册随行：配对到已归还卷子的、或试卷上下文（目录/根）里的无主解答册——
        # 内容同样以讲义式标记为主就随卷归还，让讲义配对拿到官方解答（正文照旧不进 wiki）
        for _sf, _hf in _pairing.items():
            if _sf in exam_rerouted or _sf not in hw_related:
                continue
            _sf_rel = _sf.replace(chr(92), "/")
            _ctx = (_hf in exam_rerouted) or (_hf is None and (
                bool(_mat_root_name and _seg_exam(_mat_root_name))
                or any(_seg_exam(sg) for sg in os.path.dirname(_sf_rel).split("/") if sg)))
            if not _ctx:
                continue
            _st0, _b0 = _file_stream(pages, _sf)
            _lecm = detect_lecture_markers(_st0)
            _probct = sum(1 for m in _hw_markers(_st0) if m.get("role") == "problem")
            if _lecm and len(_lecm) >= max(1, _probct):
                hw_related.discard(_sf)
                exam_rerouted.add(_sf)
                report["warnings"].append(
                    "exam_lecture_style: %s（试卷解答册且内容以讲义式标记为主——随卷归还"
                    "讲义管线配对官方解答，正文不进 wiki）" % _sf)
    # 认领 = 真能当答案消费的配对解答册：整册剥空后只剩【单行】内容（裸答案「4」——
    # markerless 单答兜底的形态）；多行残渣（12\n13 的逐页页码）绝不能灌进 quiz_bank 当
    # 官方答案，照常移交 AI。扫描的作业/试卷【题面册】与未配对残渣册同样移交
    def _single_line_content(rel0):
        lines = [ln.strip() for pg in pages if pg["file"] == rel0
                 for ln in (pg.get("text") or "").splitlines() if ln.strip()]
        return len(lines) == 1

    def _single_problem_hw(hf0):
        # markerless 单答兜底只对单题作业成立——多题作业吃不下整册单值，认领只会白丢
        st0 = chr(10).join((pg.get("text") or "") for pg in pages if pg["file"] == hf0)
        nums = {m["num"] for m in _hw_markers(st0)
                if m.get("role") == "problem" and m.get("num") is not None}
        return len(nums) <= 1
    _claimed_sols = ({sf for sf, hf in (_pairing or {}).items()
                      if hf and sf in residue_files and _single_line_content(sf)
                      and _single_problem_hw(hf)}
                     if getattr(args, "extract_homework", "auto") != "never" else set())
    _flush_residue(_claimed_sols)
    # 混排文件里的残渣页在【抽取前】剥离——否则页脚「37」会卷进答案切片与 answer_source_pages
    #（认领的整册残渣答案文件不在 residue_page_keys 里，不受影响）
    if residue_page_keys:
        pages = [pg for pg in pages if (pg["file"], pg["page"]) not in residue_page_keys]
    lecture_pages = [pg for pg in pages if pg["file"] not in hw_related]
    lecture_items = []
    if args.extract_lecture_questions != "never":
        lecture_items = extract_lecture_items(lecture_pages, backend=backend)
        report["examples_detected"] = sum(1 for it in lecture_items if it["id"].startswith("lecture_example"))
        report["quizzes_detected"] = sum(1 for it in lecture_items if it["id"].startswith("lecture_quiz"))
        report["pairs_detected"] = sum(
            1 for it in lecture_items
            if it.get("answer_source_pages")
            and it.get("answer_origin") != "inline_material"
        )
        report["inline_material_examples_detected"] = sum(
            1 for it in lecture_items
            if it.get("answer_origin") == "inline_material"
        )
        _teaching = [it for it in lecture_items if it["id"].startswith("lecture_example")]
        report["teaching_examples_detected"] = len(_teaching)
        report["teaching_example_roles"] = {
            role: sum(1 for it in _teaching if it.get("_teaching_role") == role)
            for role in ("paired_problem", "worked_example")
        }
        for item in _teaching:
            if (
                item.get("_teaching_role") == "worked_example"
                and item.get("answer") in (None, "", [], {})
                and len(item.get("source_pages") or ()) == 1
            ):
                report["ai_review"].append({
                    "kind": "inline_worked_answer_candidate",
                    "file": item.get("source_file"),
                    "pages": list(item.get("source_pages") or ()),
                    "external_ids": [item.get("id")],
                    "action": (
                        "A bare Example is teaching-only but is not automatically an answer. "
                        "After a semantic-v2 target-scoped prompt crop exists, run "
                        "ingest_review.py register-inline-worked with the exact question, "
                        "same-page native material text unit, and crop receipt; then claim, "
                        "draft, validate, and apply the resulting ledger patch."
                    ),
                })
        # fail-loud: a solution detected with no matching problem (mis-detected pair) → surface it
        for k in orphan_solution_keys(lecture_pages):
            variant_label = "(%s)" % k[3].upper() if len(k) > 3 else ""
            report["warnings"].append(
                "solution_without_problem: %s %d.%d%s" % (k[0], k[1], k[2], variant_label))
        if hw_related:
            overlap = sum(len(detect_lecture_markers(pg.get("text", "")))
                          for pg in pages if pg["file"] in hw_related)
            if overlap:
                report["warnings"].append("hw_lecture_overlap: 作业/解答文件里发现 %d 个讲义型标记，"
                                          "未按讲义题导入（该内容属于作业管线）" % overlap)

    homework_items = []
    if getattr(args, "extract_homework", "auto") != "never":
        homework_items, hw_rep = extract_homework_items(
            pages, _mat_root_name, exclude=exam_rerouted, backend=backend
        )
        report["warnings"].extend(hw_rep.pop("warnings"))
        report["ai_review"].extend(hw_rep.pop("ai_review", []))
        report.update(hw_rep)
    _annotate_source_languages(lecture_items + homework_items, report)
    # ---- render assets for figure-dependent items ----
    asset_root = args.asset_root
    page_pdf = {(pg["file"], pg["page"]): pg["_pdf"] for pg in pages if pg.get("_pdf")}
    page_pdf_all = dict(page_pdf_all_raw)   # 含残渣页——「见下页图」的图页往往正是无文本页
    page_pdf_all.update(page_pdf)

    explicit_crop_records = []
    if getattr(args, "crop_annotations", None):
        try:
            explicit_crop_records, annotation_summary = _load_crop_annotations(
                args.crop_annotations,
                materials,
                source_snapshot_hashes,
                page_pdf_all,
                list(lecture_items) + list(homework_items),
                backend,
            )
        except (OSError, TypeError, ValueError) as exc:
            message = "explicit crop annotations rejected: %s" % exc
            report["warnings"].append("crop_annotation_invalid: %s" % exc)
            report["ai_review"].append({
                "kind": "item_asset_crop_not_materialized",
                "file": os.path.basename(str(args.crop_annotations)),
                "pages": [],
                "external_ids": [],
                "action": (
                    message
                    + "; regenerate a source/preview-hash-bound JSONL annotation. "
                      "The builder will not fall back to a whole-page item image."
                ),
            })
            return 2, {"error": message}, report
        report["crop_annotations"] = annotation_summary
        by_item = {}
        for record in explicit_crop_records:
            by_item.setdefault(record["item_id"], {}).setdefault(
                record["side"], []
            ).append(record)
        for item in list(lecture_items) + list(homework_items):
            records = by_item.get(str(item.get("id")))
            if records:
                item["_crop_annotations"] = records
                item["_render"] = True

    want_render = args.render_pages in ("auto", "required")
    render_materials_present = bool(pdfs)
    if want_render and render_materials_present and not backend.can_render():
        if args.render_pages == "required":
            return 3, {"error": "render-pages=required 但没有渲染后端。请安装 PyMuPDF（pip install pymupdf）"
                                "或 pypdfium2+Pillow（pip install pypdfium2 Pillow）。"}, report
        report["warnings"].append("render_unavailable")
    if want_render and render_materials_present and not asset_root:
        if args.render_pages == "required":
            return 2, {"error": "--render-pages required 但未指定 --asset-root（应指向 "
                                "<workspace>/references/assets）。"}, report
        report["warnings"].append("asset_root_not_set: 未指定 --asset-root，跳过页图渲染——依赖图的题将因"
                                  "缺图被校验器 fail-closed；请用 --asset-root <workspace>/references/assets 渲染")
    # asset paths in raw_input are recorded as references/assets/<name>; warn if --asset-root, when given,
    # isn't the conventional <workspace>/references/assets (else on-disk files and JSON paths diverge).
    if asset_root and not os.path.normpath(asset_root).replace("\\", "/").lower().endswith("references/assets"):
        report["warnings"].append("asset_root_not_standard: JSON 里 asset 路径按 references/assets/ 记，"
                                  "请把 --asset-root 指向 <workspace>/references/assets，否则文件与路径会对不上")
    unsafe_asset_root = bool(asset_root) and _path_has_link_or_reparse(asset_root)
    if unsafe_asset_root:
        if args.render_pages == "required":
            return 2, {"error": "--asset-root 或其父目录包含符号链接/junction/reparse point，拒绝写入。"}, report
        report["warnings"].append(
            "unsafe_asset_root: 路径含符号链接/junction/reparse point，已跳过所有页图写入"
        )
    elif asset_root:
        # Keep the explicit output contract stable even when this particular
        # material set has no pages that need rendering.  Tests and downstream
        # tools may safely enumerate an empty, caller-requested asset directory.
        try:
            os.makedirs(asset_root, exist_ok=True)
            if _path_has_link_or_reparse(asset_root):
                raise OSError("asset root became a link/reparse point while being created")
        except OSError as exc:
            return 2, {"error": "无法安全创建 --asset-root：%s" % exc}, report

    can_write = bool(asset_root) and want_render and backend.can_render() and not unsafe_asset_root
    rendered, missing_required = 0, []
    source_hash_cache = dict(source_snapshot_hashes)
    # Low-text PDF pages are first-class evidence, not silent holes.  When the
    # caller enabled the existing render route, materialize every such page and
    # attach it to the ingestion IR as a source_page asset for AI/OCR takeover.
    for pg in ingestion_pages:
        if not pg.get("_pdf") or _page_has_content(pg.get("text")):
            continue
        file, page = pg["file"], pg["page"]
        wrote = False
        try:
            source_sha256 = _sha256_path(pg["_pdf"], source_hash_cache)
        except OSError as exc:
            source_sha256 = None
            report["skipped"].append({
                "file": file,
                "why": "AI 接管证据源读取失败 p.%d: %s" % (page, exc),
            })
        asset_name = _safe_asset_name(
            file, page,
            "review%08x" % (zlib.crc32(file.encode("utf-8")) & 0xffffffff),
            "_source", source_sha256=source_sha256,
        )
        if can_write and source_sha256:
            try:
                png = backend.render_page_png(pg["_pdf"], page - 1)
            except Exception as exc:
                png = None
                report["skipped"].append({
                    "file": file,
                    "why": "AI 接管证据页渲染失败 p.%d: %s" % (page, exc),
                })
            if png:
                full = os.path.join(asset_root, asset_name)
                if _under(asset_root, full):
                    try:
                        _write_png_atomic(asset_root, asset_name, png)
                        wrote = True
                        rendered += 1
                    except (OSError, ValueError) as exc:
                        report["skipped"].append({
                            "file": file,
                            "why": "AI 接管证据 PNG 写入失败 p.%d: %s" % (page, exc),
                        })
        if wrote:
            pg.setdefault("embedded_assets", []).append(asset_name)
            pg.setdefault("elements", []).append({
                "kind": "figure",
                "text": "Source page %d for visual review" % page,
                "asset": asset_name,
                "asset_role": "source_page",
                "ordinal": len(pg.get("elements") or []),
                "bbox": None,
            })
        elif args.render_pages == "required":
            missing_required.append("%s p.%d (AI 接管证据页不可用)" % (file, page))

    render_items = list(lecture_items) + list(homework_items)
    page_item_owners = {"prompt": {}, "answer": {}}
    for candidate in render_items:
        candidate_id = str(candidate.get("id"))
        for side in ("prompt", "answer"):
            for key in _item_crop_source_pages(candidate, side):
                page_item_owners[side].setdefault(key, set()).add(candidate_id)
    page_marker_counts = {}
    for page_row in pages:
        key = (page_row.get("file"), page_row.get("page"))
        text = page_row.get("text") or ""
        lecture_markers = _iter_markers(text)
        homework_markers = _hw_markers(text)
        page_marker_counts[key] = {
            "prompt": (
                sum(1 for marker in lecture_markers
                    if marker.get("role") == "problem" and not marker.get("continued"))
                + sum(1 for marker in homework_markers
                      if marker.get("role") == "problem" and not marker.get("continued"))
            ),
            "answer": (
                sum(1 for marker in lecture_markers
                    if marker.get("role") == "solution" and not marker.get("continued"))
                + sum(1 for marker in homework_markers
                      if marker.get("role") == "solution" and not marker.get("continued"))
            ),
        }
    crop_reviewed = set()

    def _whole_page_item_safe(item, side, source_file, source_page):
        key = (source_file, source_page)
        item_id = str(item.get("id"))
        side_owners = page_item_owners[side].get(key, set())
        opposite = "answer" if side == "prompt" else "prompt"
        opposite_owners = page_item_owners[opposite].get(key, set())
        marker_counts = page_marker_counts.get(key, {})
        side_marker_count = marker_counts.get(side, 0)
        opposite_marker_count = marker_counts.get(opposite, 0)

        # A whole page may be represented as a target-only crop only when the
        # parsed evidence positively proves exclusivity.  Absence of detected
        # text is not proof: scans/OCR failures commonly report zero markers,
        # and checking only one side can leak an answer on a prompt page (or a
        # different prompt on an answer page).  Continuations and markerless
        # figure pages therefore need a smaller deterministic crop or an exact
        # source/preview-bound vision/human CropAnnotation.
        proven_exclusive = (
            side_owners == {item_id}
            and not opposite_owners
            and side_marker_count == 1
            and opposite_marker_count == 0
        )
        if proven_exclusive:
            return True
        review_key = (item_id, side, source_file, source_page)
        if review_key not in crop_reviewed:
            crop_reviewed.add(review_key)
            report["warnings"].append(
                "item_asset_crop_not_materialized: %s (%s %s p.%d)"
                % (item_id, side, source_file, source_page)
            )
            report["ai_review"].append({
                "kind": "item_asset_crop_not_materialized",
                "file": source_file,
                "pages": [source_page],
                "external_ids": [item_id],
                "side": side,
                "action": (
                    "The builder cannot positively prove that this whole page "
                    "contains exactly one target-side item and no opposite-side "
                    "question/answer content. Provide a source- and preview-hash-"
                    "bound --crop-annotations JSONL bbox or repair deterministic "
                    "layout anchors. Zero OCR markers and same-page prompt/answer "
                    "content are not accepted as target-item isolation evidence."
                ),
            })
        return False

    for it in render_items:
        ans_files = {f for (f, _p) in it.get("_answer_pages", [])}
        if len(ans_files) > 1:   # answer pages span >1 source file → page numbers are ambiguous
            report["warnings"].append("answer_spans_multiple_files: %s (%s)"
                                      % (it["id"], "、".join(sorted(ans_files))))
        if not it.get("_render"):   # figure-dependent (requires_assets) OR image-prompt (marker_only)
            continue
        assets = []
        # one asset PER (file, page) — render every question page AND every (continued) answer page,
        # each from its OWN source file.
        _qtxt = (it.get("_prompt_text") or it.get("question") or "")   # needs 项的 question 是指引句
        _adj = []
        if re.search(r"[上下]一?页|(?:next|previous|following|preceding)\s+page", _qtxt, re.I):
            # 锚定到【含线索的那一页】：多页题在 p1 说「见下页」只该带上 p2，
            # 不能给每个题面页都 +1 把下一题/答案页(p3)也卷进来
            for (f, p) in it.get("_question_pages", []):
                _pt = next((pg.get("text") or "" for pg in pages
                            if pg["file"] == f and pg["page"] == p), "")
                if re.search(r"下一?页|(?:next|following)\s+page", _pt, re.I):
                    _adj.append((f, p + 1))
                if p > 1 and re.search(r"上一?页|(?:previous|preceding)\s+page", _pt, re.I):
                    _adj.append((f, p - 1))
        # 「见下页图」的图在相邻页——一并渲染。查全量页映射（图页常是无文本残渣页）。
        # 排除：已有题面页、_answer_pages、以及【页面文本带解答标记】的页——_answer_pages
        # 只在答案依赖图时才设，纯文本答案页得靠内容判；解答页当题面资产就是泄题，宁缺勿泄
        _ans_keys = set(it.get("_answer_pages", []))
        _asf = it.get("answer_source_file") or it.get("source_file")
        _ans_keys |= {(_asf, p0) for p0 in (it.get("answer_source_pages") or [])}

        def _adj_ok(f0, p0):
            if (f0, p0) in _ans_keys or (f0, p0) in it.get("_question_pages", []):
                return False
            _t0 = next((pg.get("text") or "" for pg in pages
                        if pg["file"] == f0 and pg["page"] == p0), "")
            return not re.search(r"(?im)^[ \t>*#]*(?:solutions?|answers?|解答|答案)\b", _t0)
        _adj = [(f, p) for (f, p) in _adj if (f, p) in page_pdf_all and _adj_ok(f, p)]
        # Crop plans are structurally distinct from legacy page plans: a proven
        # question crop must never degrade to render_page_png(), because the
        # rest of that homework page can contain the student's answer.
        submission_sources = {
            str(value).replace("\\", "/")
            for value in report.get("homework_files") or ()
        }
        official_solution_sources = {
            str(value).replace("\\", "/")
            for value in report.get("homework_solution_files") or ()
        }

        def _answer_visual_role(source_file):
            normalized = str(source_file).replace("\\", "/")
            if (normalized in submission_sources
                    and normalized not in official_solution_sources):
                return "student_attempt"
            return "answer_context"

        annotation_groups = it.get("_crop_annotations") or {}
        explicit_prompt = list(annotation_groups.get("prompt") or ())
        explicit_answer = list(annotation_groups.get("answer") or ())
        explicit_prompt_pages = {
            (row["source_file"], row["source_page"])
            for row in explicit_prompt
        }
        explicit_answer_pages = {
            (row["source_file"], row["source_page"])
            for row in explicit_answer
        }
        question_crops = [
            row for row in (it.get("_question_crops") or ())
            if (row[0], row[1]) not in explicit_prompt_pages
        ]
        answer_crops = [
            row for row in (it.get("_answer_crops") or ())
            if (row[0], row[1]) not in explicit_answer_pages
        ]

        def _safe_page_plans(side, rows, suffix, role_factory):
            output = []
            for source_file, source_page in rows:
                if ((source_file, source_page) in (
                        explicit_prompt_pages if side == "prompt"
                        else explicit_answer_pages)):
                    continue
                if _whole_page_item_safe(
                        it, side, source_file, source_page):
                    # A page that passed the one-item exclusivity gate is still
                    # materialized through the crop protocol.  The page box is
                    # an explicit target-item isolation selection, so downstream
                    # authoring receives the same receipt/spec/source controls as
                    # a smaller layout or vision crop.  It must never silently
                    # regress to a bare ``page_image`` declaration.
                    pdf = page_pdf_all.get((source_file, source_page))
                    layout_reader = getattr(backend, "page_layout", None)
                    page_box = None
                    detail = None
                    if pdf is None:
                        detail = "current PDF page is unavailable"
                    elif not callable(layout_reader):
                        detail = "PDF page geometry is unavailable"
                    else:
                        try:
                            layout = layout_reader(pdf, source_page - 1)
                            candidate = (
                                layout.get("page_bbox")
                                if isinstance(layout, dict) else None
                            )
                            if (isinstance(candidate, (list, tuple))
                                    and len(candidate) == 4):
                                page_box = [float(value) for value in candidate]
                                if (page_box[2] <= page_box[0]
                                        or page_box[3] <= page_box[1]):
                                    page_box = None
                                    detail = "PDF page box has no positive area"
                            else:
                                detail = "PDF page geometry has no page_bbox"
                        except Exception as exc:
                            detail = "PDF page geometry failed: %s" % exc
                    if page_box is not None:
                        output.append((
                            role_factory(source_file), source_file, source_page,
                            suffix + "_isolated", page_box, "crop_image", None,
                        ))
                    else:
                        review_key = (
                            str(it["id"]), side, source_file, source_page
                        )
                        if review_key not in crop_reviewed:
                            crop_reviewed.add(review_key)
                            report["warnings"].append(
                                "item_asset_crop_not_materialized: %s (%s %s p.%d)"
                                % (it["id"], side, source_file, source_page)
                            )
                            report["ai_review"].append({
                                "kind": "item_asset_crop_not_materialized",
                                "file": source_file,
                                "pages": [source_page],
                                "external_ids": [str(it["id"])],
                                "side": side,
                                "action": (
                                    "The page passed the one-item gate but its "
                                    "page-box crop could not be materialized (%s). "
                                    "Repair page geometry or provide a source- and "
                                    "preview-hash-bound --crop-annotations record. "
                                    "Whole-page fallback is forbidden."
                                ) % (detail or "unknown geometry failure"),
                            })
                        if it.get("_render"):
                            missing_required.append(
                                "%s (%s page-box crop required, %s p.%d)"
                                % (it["id"], side, source_file, source_page)
                            )
                elif it.get("_render"):
                    missing_required.append(
                        "%s (%s crop required, %s p.%d)"
                        % (it["id"], side, source_file, source_page)
                    )
            return output

        plan = (
            [
                (row["role"], row["source_file"], row["source_page"],
                 "_qannotation%d" % index, row["bbox_pdf_points"],
                 "crop_image", row)
                for index, row in enumerate(explicit_prompt, 1)
            ]
            + [
                ("question_context", f, p, "_qcrop%d" % index, bbox,
                 "crop_image", None)
                for index, (f, p, bbox) in enumerate(question_crops, 1)
            ]
            + _safe_page_plans(
                "prompt", it.get("_question_pages") or (), "",
                lambda unused_source: "question_context",
            )
            + _safe_page_plans(
                "prompt", _adj, "_adj",
                lambda unused_source: "question_context",
            )
            + [
                (row["role"], row["source_file"], row["source_page"],
                 "_aannotation%d" % index, row["bbox_pdf_points"],
                 "crop_image", row)
                for index, row in enumerate(explicit_answer, 1)
            ]
            + [
                (_answer_visual_role(f), f, p, "_acrop%d" % index, bbox,
                 "crop_image", None)
                for index, (f, p, bbox) in enumerate(answer_crops, 1)
            ]
            + _safe_page_plans(
                "answer", it.get("_answer_pages") or (), "_sol",
                _answer_visual_role,
            )
        )
        for role, file, page, suffix, bbox, asset_type, crop_annotation in plan:
            wrote = False
            asset_sha256 = None
            crop_spec = None
            crop_spec_sha256 = None
            crop_receipt = None
            crop_asset = None
            crop_dimensions = None
            crop_setup_error = None
            pdf = page_pdf_all.get((file, page))
            try:
                source_sha256 = _sha256_path(pdf, source_hash_cache) if pdf else None
            except OSError as exc:
                source_sha256 = None
                report["skipped"].append({
                    "file": file, "why": "页图源读取失败 p.%d: %s" % (page, exc)
                })
            if bbox is not None and pdf is not None and source_sha256:
                try:
                    layout_reader = getattr(backend, "page_layout", None)
                    if not callable(layout_reader):
                        raise CropContractError(
                            "crop page geometry is unavailable; whole-page fallback is forbidden"
                        )
                    layout = layout_reader(pdf, page - 1)
                    page_box = (
                        layout.get("page_bbox") if isinstance(layout, dict) else None
                    )
                    if page_box is None:
                        raise CropContractError("crop page geometry has no page_bbox")
                    if crop_annotation is not None:
                        if source_sha256 != crop_annotation["source_sha256"]:
                            raise CropContractError(
                                "annotated crop source revision drifted after validation"
                            )
                        if ([float(value) for value in page_box]
                                != crop_annotation["page_box_pdf_points"]):
                            raise CropContractError(
                                "annotated crop page geometry drifted after validation"
                            )
                        if ([float(value) for value in bbox]
                                != crop_annotation["bbox_pdf_points"]):
                            raise CropContractError(
                                "annotated crop bbox drifted after validation"
                            )
                        side = crop_annotation["side"]
                        content_scope = crop_annotation["content_scope"]
                        selection_method = crop_annotation["selection_method"]
                        selection_evidence_sha256 = crop_annotation[
                            "selection_evidence_sha256"
                        ]
                        semantic_purity = crop_annotation["semantic_purity"]
                    else:
                        # Keep automatic layout crops usable for ordinary
                        # teaching/quiz ingestion, but do not mint a current
                        # strict receipt without an explicit semantic review.
                        # Visual Study Guide authoring will fail closed on this
                        # unreceipted legacy declaration and can upgrade it via
                        # the incremental crop-receipt backfill command.
                        side = "prompt" if role == "question_context" else "answer"
                        content_scope = (
                            "full_prompt" if side == "prompt" else "full_answer"
                        )
                    try:
                        chapter_id = "ch%02d" % int(it.get("chapter"))
                    except (TypeError, ValueError):
                        chapter_id = "unassigned"
                    if crop_annotation is not None:
                        renderer_id, renderer_version, renderer_config_sha256 = (
                            _crop_renderer_contract(backend)
                        )
                        crop_spec = {
                            "item_id": str(it["id"]),
                            "chapter_id": chapter_id,
                            "side": side,
                            "role": role,
                            "content_scope": content_scope,
                            "isolation": (
                                "target_with_required_context"
                                if semantic_purity.get("required_context_ids")
                                else "target_item_only"
                            ),
                            "source_id": make_source_id(file),
                            "source_file": file,
                            "source_sha256": source_sha256,
                            "source_page": page,
                            "page_box_pdf_points": page_box,
                            "bbox_pdf_points": bbox,
                            "selection_method": selection_method,
                            "selection_evidence_sha256": selection_evidence_sha256,
                            "renderer_id": renderer_id,
                            "renderer_version": renderer_version,
                            "renderer_config_sha256": renderer_config_sha256,
                            "semantic_purity": semantic_purity,
                        }
                        crop_spec_sha256 = make_crop_spec_sha256(**crop_spec)
                except (CropContractError, OSError, ValueError) as exc:
                    crop_setup_error = str(exc)
                    report["skipped"].append({
                        "file": file,
                        "why": "crop_contract_failed p.%d: %s" % (page, exc),
                    })
            name = _safe_asset_name(
                file, page, it["id"], suffix, source_sha256=source_sha256,
                crop_spec_sha256=crop_spec_sha256,
            )
            rel_path = "references/assets/" + name
            if can_write and pdf is not None and source_sha256:
                try:
                    if bbox is None:
                        png = backend.render_page_png(pdf, page - 1)
                    elif crop_setup_error is not None:
                        png = None
                    else:
                        # ``render_crop_png`` only calls the clip renderer.  A
                        # missing/failed clip is never replaced by a whole page.
                        png, crop_width, crop_height = render_crop_png(
                            backend, pdf, page - 1, bbox
                        )
                        crop_dimensions = (crop_width, crop_height)
                except Exception as e:   # a single malformed/encrypted page must not crash the whole run
                    png = None
                    report["skipped"].append({"file": file, "why": "渲染失败 p.%d: %s" % (page, e)})
                if png:
                    if bbox is not None:
                        try:
                            if crop_dimensions is None:
                                raise CropContractError(
                                    "crop dimensions were not established"
                                )
                            candidate_sha256 = hashlib.sha256(bytes(png)).hexdigest()
                            if crop_spec is not None:
                                crop_receipt = CropReceipt.create(
                                    output_path=rel_path,
                                    output_sha256=candidate_sha256,
                                    output_width=crop_dimensions[0],
                                    output_height=crop_dimensions[1],
                                    supersedes=(),
                                    **crop_spec
                                )
                                crop_asset = compact_asset_from_receipt(crop_receipt)
                                if crop_receipt.side == "prompt":
                                    crop_asset["contains_full_prompt"] = (
                                        crop_receipt.content_scope == "full_prompt"
                                    )
                                validate_crop_asset_binding(crop_asset, crop_receipt)
                        except (CropContractError, ValueError) as exc:
                            png = None
                            crop_receipt = None
                            crop_asset = None
                            report["skipped"].append({
                                "file": file,
                                "why": "crop_receipt_failed p.%d: %s" % (page, exc),
                            })
                if png:
                    full = os.path.join(asset_root, name)
                    if not _under(asset_root, full):   # name is sanitized; defensive belt-and-braces
                        report["warnings"].append("unsafe_asset_target_skipped")
                    else:
                        try:
                            asset_sha256 = _write_png_atomic(asset_root, name, png)
                            if (crop_receipt is not None
                                    and asset_sha256 != crop_receipt.output_sha256):
                                raise ValueError(
                                    "published crop hash differs from its receipt"
                                )
                            wrote = True
                            rendered += 1
                        except (OSError, ValueError) as exc:
                            report["skipped"].append({
                                "file": file,
                                "why": "PNG 写入/格式校验失败 p.%d: %s" % (page, exc),
                            })
            if not wrote and bbox is not None:
                side = "prompt" if role == "question_context" else "answer"
                review_key = (str(it["id"]), side, file, page)
                if review_key not in crop_reviewed:
                    crop_reviewed.add(review_key)
                    detail = crop_setup_error or "clip render/receipt publication failed"
                    report["ai_review"].append({
                        "kind": "item_asset_crop_not_materialized",
                        "file": file,
                        "pages": [page],
                        "external_ids": [str(it["id"])],
                        "side": side,
                        "action": (
                            "Target-only crop was not materialized (%s). Inspect the "
                            "bound source page and provide a corrected --crop-annotations "
                            "record. Whole-page fallback is forbidden."
                        ) % detail,
                    })
            if not wrote and role in (
                    "answer_context", "worked_solution", "student_attempt"):
                # don't DECLARE a missing answer-side asset — it would fail-close an otherwise-valid
                # question whose own figure rendered fine (the text `answer` already covers it).
                report["warnings"].append("answer_image_unavailable: %s (p.%d)" % (it["id"], page))
                continue
            if not wrote:
                why = ("无渲染后端" if not (want_render and backend.can_render())
                       else "未指定 --asset-root" if not asset_root
                       else "该页非 PDF 来源（无法渲染）" if pdf is None
                       else "渲染返回空")
                report["warnings"].append(
                    "likely_asset_required_but_no_image: %s (%s, %s)" % (it["id"], role, why))
                # render=required fails when a needed QUESTION figure can't be produced — for a
                # requires_assets figure OR a marker-only image-prompt (both have _render; role here is
                # always question_context, since answer-side misses were already `continue`d above).
                if it.get("_render"):
                    missing_required.append("%s (%s, %s)" % (it["id"], role, why))
                # Fail closed: never declare a path that was not produced in this
                # run.  A same-named file left by an older workspace build cannot
                # silently satisfy the new question.
                continue
            if bbox is not None:
                if crop_receipt is None or crop_asset is None:
                    # The automatic layout crop is intentionally a readable
                    # legacy declaration, not authoring-grade evidence.  It
                    # remains available to normal tutoring and gives the
                    # backfill command exact bytes to audit later.
                    declared = {
                        "path": rel_path,
                        "role": role,
                        "type": "crop_image",
                        "sha256": asset_sha256,
                        "source_file": file,
                        "source_sha256": source_sha256,
                        "source_page": page,
                        "source_bbox_pdf_points": [float(value) for value in bbox],
                    }
                    report["warnings"].append(
                        "crop_semantic_review_required: %s (%s p.%d)"
                        % (it["id"], file, page)
                    )
                    report["ai_review"].append({
                        "kind": "item_asset_crop_semantic_review_required",
                        "file": file,
                        "pages": [page],
                        "external_ids": [str(it["id"])],
                        "side": (
                            "prompt" if role == "question_context" else "answer"
                        ),
                        "action": (
                            "Review this exact crop for target-item-only semantic "
                            "purity, then provide a schema-v2 crop annotation or "
                            "run incremental crop-receipt backfill."
                        ),
                    })
                else:
                    declared = dict(crop_asset)
                declared["caption"] = "%s p.%d (%s crop)" % (file, page, role)
                assets.append(declared)
                if crop_receipt is not None:
                    report["crop_receipts"].append(crop_receipt.to_dict())
            else:
                assets.append({
                    "path": rel_path,
                    "role": role,
                    "type": asset_type,
                    "caption": "%s p.%d (%s)" % (file, page, role),
                    "sha256": asset_sha256,
                    # An answer-side visual may legitimately come from the
                    # submission while answer_source_file points elsewhere.
                    "source_file": file,
                    "source_sha256": source_sha256,
                })
        it["assets"] = assets
    report["pages_rendered"] = rendered
    report["crop_receipts"] = sorted(
        report["crop_receipts"], key=lambda row: row["crop_receipt_id"]
    )
    report["crop_receipt_index_sha256"] = crop_canonical_sha256(
        report["crop_receipts"]
    )
    # Re-parse the exact report view before publication.  This catches duplicate
    # IDs/output paths, ordering drift, malformed full receipts, or a digest
    # mismatch inside the builder rather than deferring discovery to authoring.
    validate_crop_receipt_index(report)

    # --render-pages required must FAIL (not just warn) when a required figure couldn't be produced,
    # else we'd emit requires_assets=true items with missing images that the validator then rejects.
    if args.render_pages == "required" and missing_required:
        return 3, {"error": "render-pages=required 但有 %d 个必需页图未能渲染：%s。请确保对应源为可渲染的 "
                            "PDF、渲染后端可用（pymupdf 或 pypdfium2+Pillow）、并已指定 --asset-root。"
                            % (len(missing_required), "；".join(missing_required[:6]))}, report

    course = args.course_name or os.path.basename(os.path.abspath(materials)) or "未命名科目"
    # 作业相关文件（题面册、配对/未配对解答册）都不进章节 wiki——解答册整册是答案、
    # 作业册常带 inline Solution 块，混进 wiki 等于测验/复盘前泄题；题面与官方答案已完整
    # 进入 quiz_bank（含出处 source_file/answer_source_file），wiki 只保留学习材料
    sol_files = (set(report.get("homework_solution_files") or [])
                 | set(report.get("homework_files") or [])
                 | exam_rerouted)          # 归还讲义管线的讲义式试卷：题答进 quiz_bank、正文仍挡出 wiki
    # 解答目录（solutions/、hw1_solutions/）里配不上作业的文件（solutions/week1.pdf）会退出
    # 作业配对、交还讲义管线补配讲义答案——但整册仍是官方解答，照样挡在 wiki 外
    sol_dir_files = {pg["file"] for pg in pages
                     if any(_sol_dir_segment(seg) for seg in
                            os.path.dirname(pg["file"].replace(chr(92), "/")).split("/") if seg)}
    # 混合材料根里配不上作业的裸答案册（solutions.pdf / final_answers.pdf——文件名剥掉
    # 解答记号/描述词后一无所有）同样挡出 wiki：语料里有作业上下文时它就是答案册；
    # 纯讲义语料（无作业文件）保持原样，交给讲义答案配对
    if report.get("homework_files"):
        sol_dir_files |= {pg["file"] for pg in pages
                          if _pure_sol_stem(os.path.splitext(os.path.basename(pg["file"]))[0])}
    wiki_pages = [pg for pg in pages if pg["file"] not in sol_files
                  and pg["file"] not in sol_dir_files
                  and (pg["file"], pg["page"]) not in residue_page_keys]   # 残渣页不进 wiki
    _typed = {}
    for _it in (lecture_items + homework_items):
        _typed[_it.get("type", "subjective")] = _typed.get(_it.get("type", "subjective"), 0) + 1
    if _typed:
        report["warnings"].append(
            "type_heuristic: 题型由保守启发式判定——%s；启发式只认选择题/填空题两种形态，"
            "判断/编程等其他题型会落在主观题里，请 AI 复核 quiz_bank 的 type 字段"
            % "、".join("%s %d" % (k, v) for k, v in sorted(_typed.items())))
        if _typed.get("subjective"):
            # Type review is an item decision. Keep each hand-off scoped to one
            # external question identity so applying it cannot silently close
            # unrelated questions from another chapter in the same source file.
            unscoped = 0
            for review_item in lecture_items + homework_items:
                if (review_item.get("type") or "subjective") != "subjective":
                    continue
                external_id = review_item.get("id")
                if external_id in (None, ""):
                    unscoped += 1
                    continue
                report["ai_review"].append({
                    "kind": "type_defaulted",
                    "file": review_item.get("source_file") or "references/quiz_bank.json",
                    "pages": review_item.get("source_pages") or [],
                    "external_ids": [str(external_id)],
                    "action": (
                        "Review this one question's prompt and confirm or replace its type "
                        "with choice/subjective/diagram/fill_blank/true_false/code; add "
                        "options only when the prompt evidence contains them."
                    ),
                })
            if unscoped:
                # Compatibility fallback for malformed/legacy raw items without
                # an external ID. The structured pipeline deliberately keeps this
                # as an unscoped legacy candidate instead of inventing an ID.
                report["ai_review"].append({
                    "kind": "type_defaulted",
                    "file": "references/quiz_bank.json",
                    "action": (
                        "%d subjective questions have no external ID; review their types "
                        "after restoring stable question identities."
                    ) % unscoped,
                })
    _ch_notes = []
    sections = group_sections(wiki_pages, _ch_notes)
    for _f in _ch_notes:
        report["warnings"].append(
            "chapter_unassigned: %s（无任何章节线索，按上文/第 1 章并入——正确分章请重命名加 chNN "
            "或页首加章节标记，或由 AI 核对 wiki 分章）" % _f)
    if _ch_notes:
        report["ai_review"].append({
            "kind": "chapter_unassigned", "file": "；".join(_ch_notes[:20]),
            "action": "这些文件无章节线索，内容已并入上文/第 1 章（这是猜测）。请 AI 核对生成的 wiki "
                      "分章是否正确，不对则手工调整或让用户重命名后重建。"})
    if not sections:
        report["warnings"].append(
            "wiki_empty: 讲义类内容为零（材料全是作业/试卷/答案或未能提取）——将生成占位第 1 章，"
            "复习知识面为空")
        report["ai_review"].append({
            "kind": "wiki_empty", "file": "(all)",
            "action": "没有任何讲义内容进入 wiki。请确认材料里是否本应有讲义；若有，检查它们是否被"
                      "列入 skipped/接管清单并逐条处理；若确实只有题目材料，请告知学生 wiki 为空。"})
    try:
        final_hashes = {}
        changed_sources = [
            _rel(path, materials) for path in all_source_paths
            if _sha256_path(path, final_hashes) != source_snapshot_hashes[path]
        ]
    except OSError as exc:
        report["warnings"].append("source_snapshot_drift: %s" % exc)
        return 6, {"error": "提取期间材料消失或不可读：%s" % exc}, report
    if changed_sources:
        report["warnings"].append(
            "source_snapshot_drift: %s" % "、".join(changed_sources)
        )
        return 6, {
            "error": "提取期间材料字节发生变化，已拒绝把旧文本与新版本哈希绑定：%s"
                     % "、".join(changed_sources)
        }, report
    _annotate_ingestion_languages(ingestion_pages, report)
    raw_input = build_raw_input(course, sections, lecture_items, homework_items)
    structured_questions = _structured_question_union(
        raw_input["quiz_bank"], raw_input.get("teaching_examples") or ()
    )
    # The public quiz/teaching views deliberately replace an image-backed
    # prompt with a short page-reference sentence and strip private builder
    # fields.  The typed ingestion fact must nevertheless preserve the exact
    # source-language prompt text that was used for language classification.
    # Overlay only this private producer field on a copy passed to the IR
    # builder; it never leaks back into quiz_bank/teaching_examples.
    source_items_by_id = {
        str(item.get("id")): item
        for item in list(lecture_items or ()) + list(homework_items or ())
        if isinstance(item, dict) and item.get("id") not in (None, "")
    }
    ingestion_questions = []
    for item in structured_questions:
        current = dict(item)
        source_item = source_items_by_id.get(str(item.get("id")))
        source_prompt = (
            source_item.get("_prompt_text")
            if isinstance(source_item, dict) else None
        )
        if isinstance(source_prompt, str) and source_prompt.strip():
            current["_prompt_text"] = source_prompt
        ingestion_questions.append(current)
    try:
        raw_input["ingestion"] = build_ingestion_payload(
            materials,
            all_source_paths,
            ingestion_pages,
            sections=sections,
            quiz_items=ingestion_questions,
            report=report,
            parser_receipts=_merge_parser_receipts(
                _core_parser_receipts(
                    materials,
                    all_source_paths,
                    ingestion_pages,
                    source_snapshot_hashes,
                    backend,
                    report,
                ),
                optional_parser_receipts,
            ),
        )
    except Exception as exc:
        # The compatibility output must never claim success when its durable
        # provenance/review envelope could not be constructed.
        report["warnings"].append("ingestion_envelope_failed: %s" % exc)
        return 5, {"error": "结构化 ingestion envelope 构建失败：%s" % exc}, report
    return 0, raw_input, report


def _asset_role_map(*collections, workspace=None):
    roles = {}
    for rows in collections:
        for path, role in iter_asset_declarations(rows or ()):
            key = (workspace_asset_identity_key(path, workspace)
                   if workspace is not None else physical_asset_key(path))
            if key is not None and isinstance(role, str):
                roles.setdefault(key, set()).add(role)
    return roles


def _validate_pre_ingestion_generation_facts(workspace, ingest_path):
    """Validate the narrow generation files allowed before first compilation."""

    pending_relative = MATERIAL_BUILD_PENDING_PATH
    pending_path = os.path.join(workspace, *pending_relative.split("/"))
    if not os.path.isfile(pending_path) or _path_has_link_or_reparse(pending_path):
        raise ValueError("pre-ingestion material pending marker is missing or unsafe")
    pending = read_json(pending_path)
    validate_generation(pending, expected_status="pending")
    pending_payload, _pending_snapshot = stable_read_bytes(pending_path)
    pending_sha = hashlib.sha256(pending_payload).hexdigest()
    for binding in (pending["raw_input"], pending["parse_report"]):
        source = os.path.join(workspace, *binding["path"].split("/"))
        try:
            source_payload, _source_snapshot = stable_read_bytes(source)
            source_matches = (
                hashlib.sha256(source_payload).hexdigest() == binding["sha256"]
            )
        except (OSError, TypeError, ValueError):
            source_matches = False
        if not source_matches:
            # Blocker-first publication may expose the self-authenticating
            # marker before either source document.  The orchestrator permits
            # this fact set only to rebuild a candidate and then requires exact
            # generation equality (or an explicit supersede authorization)
            # before publication.  A standalone builder remains blocked by the
            # ordinary material-pending mutation gate.
            pass

    recovery_root = os.path.join(ingest_path, "material_build_recovery")
    recovery_logs = {}
    if os.path.lexists(recovery_root):
        if (not os.path.isdir(recovery_root)
                or _path_has_link_or_reparse(recovery_root)):
            raise ValueError("pre-ingestion material recovery directory is unsafe")
        for name in sorted(os.listdir(recovery_root)):
            path = os.path.join(recovery_root, name)
            generation_id, extension = os.path.splitext(name)
            if (extension != ".json" or not os.path.isfile(path)
                    or _path_has_link_or_reparse(path)):
                raise ValueError("pre-ingestion material recovery entry is unsafe")
            if material_recovery_path(generation_id).split("/")[-1] != name:
                raise ValueError("pre-ingestion material recovery filename is invalid")
            value = read_json(path)
            validate_runtime_recovery_log(value)
            if value["generation_id"] != generation_id:
                raise ValueError("pre-ingestion material recovery identity drifted")
            recovery_logs[generation_id] = value

    current_generation = pending["generation_id"]
    current = recovery_logs.get(current_generation)
    if current is not None:
        validate_runtime_recovery_log(
            current, pending=pending, pending_sha256=pending_sha
        )
        if current["records"][-1]["outcome"] is not None:
            raise ValueError("pre-ingestion pending recovery is already closed")

    expected_recovery_ids = {current_generation} if current is not None else set()
    seen = {current_generation}
    child_generation = current_generation
    predecessor_id = pending.get("supersedes_generation_id")
    while predecessor_id is not None:
        if predecessor_id in seen or len(seen) > 64:
            raise ValueError(
                "pre-ingestion recovery ancestry is cyclic or too deep"
            )
        seen.add(predecessor_id)
        predecessor = recovery_logs.get(predecessor_id)
        if predecessor is None:
            raise ValueError(
                "pre-ingestion successor lacks predecessor recovery audit"
            )
        latest = predecessor["records"][-1]
        authorization = latest["authorization"]
        outcome = latest["outcome"]
        binding = authorization["pending"]
        if (binding["generation_id"] != predecessor_id
                or authorization["action"] != "supersede"
                or (outcome is not None and (
                    outcome.get("status") != "abandoned"
                    or outcome.get("replacement_generation_id")
                    != child_generation))):
            raise ValueError("pre-ingestion predecessor recovery audit is invalid")
        expected_recovery_ids.add(predecessor_id)
        child_generation = predecessor_id
        predecessor_id = binding.get("supersedes_generation_id")
    if set(recovery_logs) != expected_recovery_ids:
        raise ValueError("pre-ingestion recovery audit is unrelated to pending generation")

    transactions = os.path.join(ingest_path, "transactions")
    if os.path.lexists(transactions) and (
            not os.path.isdir(transactions)
            or _path_has_link_or_reparse(transactions)
            or os.listdir(transactions)):
        raise ValueError("pre-ingestion recovery transaction directory is unsafe")


def _validate_empty_pre_ingestion_transactions(ingest_path):
    """Accept the empty journal directory created by first-run locking only."""

    transactions = os.path.join(ingest_path, "transactions")
    if not os.path.lexists(transactions):
        return
    if (not os.path.isdir(transactions)
            or _path_has_link_or_reparse(transactions)
            or os.listdir(transactions)):
        raise ValueError("pre-ingestion recovery transaction directory is unsafe")


def _existing_workspace_asset_policy(workspace):
    """Load every durable asset layer, while permitting a genuinely new workspace."""

    empty = {"quiz_rows": [], "teaching_rows": [], "content_units": [],
             "tainted_keys": set(), "tainted_identity_keys": set(),
             "conflicts": [], "unsafe_paths": [], "item_groups": {}}
    if not workspace:
        return empty
    quiz_path = os.path.join(workspace, "references", "quiz_bank.json")
    teaching_path = os.path.join(workspace, "references", "teaching_examples.json")
    ingest_path = os.path.join(workspace, ".ingest")
    content_path = os.path.join(ingest_path, "content_units.jsonl")
    if not any(os.path.lexists(path) for path in (
            quiz_path, teaching_path, content_path)):
        # IngestionStore creates locks before the first publication, and a failed/retried
        # builder may already have its three compatibility outputs.  None of those files is
        # compiled asset-policy truth.  Every other .ingest entry still proves this is not a
        # genuinely new evidence layer and must fail closed when content_units.jsonl is absent.
        pre_ingestion_names = {
            "mutation.lock", "publication.lock", "source_raw_input.json",
            "parse_report.json", "ai_review_manifest.json",
            "material_build_pending.json", "material_build_recovery",
            "transactions",
        }
        extra_ingest = []
        if os.path.isdir(ingest_path) and not _path_has_link_or_reparse(ingest_path):
            extra_ingest = [name for name in os.listdir(ingest_path)
                            if name not in pre_ingestion_names]
        if extra_ingest:
            raise ValueError(
                "existing .ingest is missing content_units.jsonl"
            )
        generation_names = {
            name for name in (
                "material_build_pending.json", "material_build_recovery"
            ) if os.path.lexists(os.path.join(ingest_path, name))
        }
        if generation_names:
            _validate_pre_ingestion_generation_facts(workspace, ingest_path)
        else:
            _validate_empty_pre_ingestion_transactions(ingest_path)
        return empty
    if not os.path.isfile(quiz_path) or _path_has_link_or_reparse(quiz_path):
        raise ValueError(
            "existing workspace asset policy is incomplete or link-backed: "
            "references/quiz_bank.json"
        )
    return workspace_asset_policy_snapshot(workspace)


def _staged_asset_files(stage_root):
    output = []
    for base, dirs, files in os.walk(stage_root):
        dirs.sort()
        files.sort()
        for filename in files:
            path = os.path.join(base, filename)
            if _path_has_link_or_reparse(path) or not os.path.isfile(path):
                raise ValueError("staged asset is non-regular or link-backed: %s" % path)
            relative = os.path.relpath(path, stage_root).replace(os.sep, "/")
            workspace_relative = "references/assets/" + relative
            key = physical_asset_key(workspace_relative)
            if key is None:
                raise ValueError("staged asset has an unsafe workspace path: %r" % relative)
            with open(path, "rb") as stream:
                payload = stream.read()
            output.append({
                "stage_path": path,
                "relative": relative,
                "workspace_path": workspace_relative,
                "key": key,
                "payload": payload,
            })
    return output


def _plan_staged_assets(stage_root, destination_root, raw_input, workspace):
    """Audit and freeze a staged asset publication without mutating the workspace.

    All parser/render writers target ``stage_root``.  Consequently an ingestion
    envelope or ownership-policy rejection cannot have already replaced a
    deterministic workspace file.  The returned plan owns immutable payload
    bytes, so callers may remove the staging directory before committing the
    complete JSON+asset transaction.  Different bytes never replace an existing
    target.
    """

    quiz_rows = list((raw_input or {}).get("quiz_bank") or ())
    teaching_rows = list((raw_input or {}).get("teaching_examples") or ())
    content_units = list(((raw_input or {}).get("ingestion") or {}).get(
        "content_units") or ())
    # Audit every candidate layer before looking at or publishing staged files.  Compatibility
    # quiz/teaching rows may deliberately remain chapter-unassigned while a typed review routes
    # their compiled unit, so waive only that one identity warning.  Path/role/schema errors,
    # student-attempt laundering, duplicate identities, and same-item prompt/answer collisions
    # remain hard failures across the complete candidate batch.  In particular, an empty existing
    # workspace must not make conflicts *within* the new batch invisible.
    identity_workspace = (
        workspace if workspace and os.path.isdir(workspace) else None
    )
    candidate_audit = audit_asset_policy(
        quiz_rows=quiz_rows,
        teaching_rows=teaching_rows,
        content_units=content_units,
        workspace=identity_workspace,
        allow_missing_workspace_assets=True,
    )

    def compatibility_unassigned_warning(message):
        return (
            isinstance(message, str)
            and message.startswith((
                "references/quiz_bank.json[",
                "references/teaching_examples.json[",
            ))
            and "has asset evidence but no stable chapter/phase locator" in message
        )

    problems = list(candidate_audit["invalid_declarations"])
    problems.extend(
        message for message in candidate_audit["conflicts"]
        if not compatibility_unassigned_warning(message)
    )
    if problems:
        raise ValueError("generated candidate asset policy is invalid: %s" % problems[0])

    existing = _existing_workspace_asset_policy(workspace)
    problems = list(existing.get("unsafe_paths") or ()) + list(
        existing.get("conflicts") or ())
    if problems:
        raise ValueError("existing workspace asset policy is invalid: %s" % problems[0])

    # Different lexical paths can be hard links to the same physical file.  The
    # exact-path ownership map below remains useful for all role transitions,
    # but it must not let a new official-looking declaration rename an existing
    # student submission and thereby shed its workspace-wide taint.
    if workspace:
        for rows in (quiz_rows, teaching_rows, content_units):
            for path, role in iter_asset_declarations(rows):
                if role == STUDENT_ATTEMPT:
                    continue
                try:
                    tainted_alias = workspace_asset_is_student_attempt(
                        path, workspace, existing
                    )
                except ValueError as exc:
                    raise ValueError(
                        "cannot verify candidate asset physical identity: %s" % exc
                    )
                if tainted_alias:
                    raise ValueError(
                        "generated candidate asset %r is a hardlink alias of existing "
                        "student_attempt evidence" % path
                    )

    staged = _staged_asset_files(stage_root)
    candidate_roles = _asset_role_map(
        quiz_rows, teaching_rows, content_units,
        workspace=identity_workspace,
    )
    existing_roles = _asset_role_map(
        existing.get("quiz_rows"), existing.get("teaching_rows"),
        existing.get("content_units"),
        workspace=identity_workspace,
    )
    candidate_policy = {
        "quiz_rows": quiz_rows, "teaching_rows": teaching_rows,
        "content_units": content_units,
        "item_groups": candidate_audit["item_groups"],
    }
    staged_keys = {
        (workspace_asset_identity_key(record["workspace_path"], identity_workspace)
         if identity_workspace is not None else record["key"])
        for record in staged
    }
    undeclared = [record["workspace_path"] for record in staged
                  if (workspace_asset_identity_key(
                          record["workspace_path"], identity_workspace)
                      if identity_workspace is not None else record["key"])
                  not in candidate_roles]
    if undeclared:
        raise ValueError("staged writer produced an undeclared asset: %s" % undeclared[0])
    declared_but_unstaged = [key for key in candidate_roles if key not in staged_keys]
    if declared_but_unstaged:
        raise ValueError(
            "generated candidate declares an asset that is absent from the safe staging area"
        )

    destination_root = os.path.abspath(destination_root)
    if _path_has_link_or_reparse(destination_root):
        raise ValueError("destination asset root is link/reparse-backed")
    publication = []
    promotion_requests = []
    for record in staged:
        comparison_key = (
            workspace_asset_identity_key(
                record["workspace_path"], identity_workspace
            ) if identity_workspace is not None else record["key"]
        )
        old_roles = existing_roles.get(comparison_key, set())
        new_roles = candidate_roles.get(comparison_key, set())
        role_changed = bool(old_roles and old_roles != new_roles)
        promotes_to_student_attempt = (
            role_changed
            and old_roles == {"answer_context"}
            and new_roles == {STUDENT_ATTEMPT}
        )
        if old_roles:
            # The policy audits above decide whether a role combination is legal.
            # Publication permits an idempotent rebuild or the one monotonic
            # promotion above.  Byte identity is still checked below before a
            # promotion becomes part of the immutable plan.
            if role_changed and not promotes_to_student_attempt:
                raise ValueError(
                    "staged target conflicts with existing workspace ownership: %s "
                    "(%s -> %s)" % (
                        record["workspace_path"], sorted(old_roles), sorted(new_roles)
                    )
                )
        destination = os.path.abspath(os.path.join(
            destination_root, *record["relative"].split("/")
        ))
        if not _under(destination_root, destination):
            raise ValueError("staged asset escapes destination root")
        if os.path.lexists(destination):
            if (_path_has_link_or_reparse(destination)
                    or not os.path.isfile(destination)):
                raise ValueError(
                    "existing asset target is non-regular or link-backed: %s"
                    % record["workspace_path"]
                )
            with open(destination, "rb") as stream:
                current = stream.read()
            if current != record["payload"]:
                raise ValueError(
                    "refusing to overwrite an existing asset target with different bytes: %s"
                    % record["workspace_path"]
                )
            if promotes_to_student_attempt:
                promotion_requests.append((
                    comparison_key,
                    record["workspace_path"],
                    hashlib.sha256(record["payload"]).hexdigest(),
                ))
            publication.append((record, destination, False))
        else:
            if old_roles:
                raise ValueError(
                    "refusing to materialize a stale target already owned by the workspace: %s"
                    % record["workspace_path"]
                )
            publication.append((record, destination, True))

    role_promotions = legacy_attempt_promotion_receipts(
        tuple((path, sha256) for _identity, path, sha256 in promotion_requests),
        existing,
        candidate_policy,
        identity_workspace,
    ) if promotion_requests else ()
    promotion_authorizations = tuple(
        (request[0], receipt)
        for request, receipt in zip(promotion_requests, role_promotions)
    )
    return {
        "destination_root": destination_root,
        "publication": tuple(publication),
        "role_promotions": tuple(role_promotions),
        "promotion_authorizations": promotion_authorizations,
        "promotion_workspace": identity_workspace,
        "promotion_candidate_policy": (
            copy.deepcopy(candidate_policy) if role_promotions else None
        ),
        "promotion_candidate_findings": (
            (tuple(candidate_audit["invalid_declarations"]),
             tuple(candidate_audit["conflicts"])) if role_promotions else None
        ),
    }


def _recheck_role_promotions(plan):
    authorizations = plan.get("promotion_authorizations") or ()
    if not authorizations:
        return
    workspace = plan.get("promotion_workspace")
    live = _existing_workspace_asset_policy(workspace)
    if (live.get("unsafe_paths") or live.get("conflicts")):
        raise ValueError("legacy asset promotion policy changed before publication")
    frozen = plan.get("promotion_candidate_policy") or {}
    candidate_audit = audit_asset_policy(
        quiz_rows=frozen.get("quiz_rows"),
        teaching_rows=frozen.get("teaching_rows"),
        content_units=frozen.get("content_units"),
        workspace=workspace,
        allow_missing_workspace_assets=True,
    )
    findings = (
        tuple(candidate_audit["invalid_declarations"]),
        tuple(candidate_audit["conflicts"]),
    )
    if findings != plan.get("promotion_candidate_findings"):
        raise ValueError("legacy candidate asset policy drifted before publication")
    candidate_policy = dict(frozen)
    candidate_policy["item_groups"] = candidate_audit["item_groups"]
    for planned_identity, receipt in authorizations:
        current_identity = workspace_asset_identity_key(receipt["path"], workspace)
        if current_identity != planned_identity:
            raise ValueError("legacy asset promotion authority drifted before publication")
    verified_receipts = legacy_attempt_promotion_receipts(
        tuple((receipt["path"], receipt["sha256"])
              for _identity, receipt in authorizations),
        live,
        candidate_policy,
        workspace,
    )
    for (_planned_identity, receipt), verified in zip(
            authorizations, verified_receipts):
        if verified != receipt:
            raise ValueError("legacy asset promotion provenance drifted before publication")


def _publish_asset_plan(plan, journal=None):
    """Publish one fully preflighted asset plan and return an exact rollback journal."""

    destination_root = plan["destination_root"]
    publication = plan["publication"]
    if journal is None:
        journal = {"created_files": [], "created_dirs": []}
    if (not isinstance(journal, dict)
            or not isinstance(journal.get("created_files"), list)
            or not isinstance(journal.get("created_dirs"), list)
            or journal["created_files"] or journal["created_dirs"]):
        raise ValueError("asset publication journal must be a fresh mutable journal")
    created_files = journal["created_files"]
    created_dirs = journal["created_dirs"]
    try:
        _recheck_role_promotions(plan)
        if _path_has_link_or_reparse(destination_root):
            raise ValueError("destination asset root changed after publication preflight")
        missing_root_dirs = []
        cursor = destination_root
        while cursor and not os.path.exists(cursor):
            missing_root_dirs.append(cursor)
            next_cursor = os.path.dirname(cursor)
            if next_cursor == cursor:
                break
            cursor = next_cursor
        # Register every absent directory before recursive creation.  If the
        # host creates only an ancestor and then raises, rollback still owns it.
        created_dirs.extend(reversed(missing_root_dirs))
        os.makedirs(destination_root, exist_ok=True)
        if (_path_has_link_or_reparse(destination_root)
                or not os.path.isdir(destination_root)):
            raise ValueError("destination asset root became unsafe during publication")
        for record, destination, should_write in publication:
            if not should_write:
                if (_path_has_link_or_reparse(destination)
                        or not os.path.isfile(destination)):
                    raise ValueError(
                        "preflighted byte-identical asset target became unsafe"
                    )
                with open(destination, "rb") as stream:
                    if stream.read() != record["payload"]:
                        raise ValueError(
                            "preflighted byte-identical asset target drifted"
                        )
                continue
            parent = os.path.dirname(destination)
            missing = []
            cursor = parent
            while cursor and not os.path.exists(cursor):
                missing.append(cursor)
                next_cursor = os.path.dirname(cursor)
                if next_cursor == cursor:
                    break
                cursor = next_cursor
            created_dirs.extend(reversed(missing))
            os.makedirs(parent, exist_ok=True)
            # Record the rollback intent before the replace boundary.  A wrapped
            # publisher may complete the replace and then raise; appending only
            # after return would leak that newly public asset from the journal.
            created_files.append({
                "path": destination,
                "sha256": hashlib.sha256(record["payload"]).hexdigest(),
            })
            _write_new_asset_atomic(
                parent, os.path.basename(destination), record["payload"]
            )
    except BaseException as exc:
        rollback_failures = []
        for entry in reversed(created_files):
            try:
                if not os.path.lexists(entry["path"]):
                    continue
                if (_path_has_link_or_reparse(entry["path"])
                        or not os.path.isfile(entry["path"])):
                    raise OSError("created asset became unsafe")
                with open(entry["path"], "rb") as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
                if digest != entry["sha256"]:
                    raise OSError("created asset drifted before rollback")
                os.unlink(entry["path"])
            except OSError as rollback_exc:
                rollback_failures.append(
                    "%s: %s" % (entry["path"], rollback_exc)
                )
        rollback_failures.extend(_remove_created_directories(created_dirs))
        if rollback_failures:
            raise OSError(
                "asset publication rollback failed: %s"
                % "; ".join(rollback_failures)
            ) from exc
        raise
    return journal


def _publish_staged_assets(stage_root, destination_root, raw_input, workspace):
    """Backward-compatible one-shot staged asset publication."""

    plan = _plan_staged_assets(stage_root, destination_root, raw_input, workspace)
    return _publish_asset_plan(plan)


def _publication_json_bytes(value):
    """Serialize one builder control document before any public mutation."""

    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("builder publication JSON is not serializable: %s" % exc)


def _publication_target_states(publications):
    """Preflight exact JSON targets and snapshot their byte/existence state."""

    states = {}
    paths = [os.path.abspath(str(path)) for path, _value in publications]
    for left_index, left in enumerate(paths):
        for right in paths[left_index + 1:]:
            if _publication_targets_alias(left, right):
                raise ValueError("builder JSON publication targets must be physically distinct")
    for path in paths:
        if _path_has_link_or_reparse(path):
            raise ValueError(
                "builder JSON publication target is link/reparse-backed: %s" % path
            )
        if os.path.lexists(path):
            if not os.path.isfile(path):
                raise ValueError(
                    "builder JSON publication target is not a regular file: %s" % path
                )
            with open(path, "rb") as stream:
                payload = stream.read()
            states[path] = {"existed": True, "payload": payload}
        else:
            states[path] = {"existed": False, "payload": None}
    return states


def _missing_parent_directories(path):
    missing = []
    cursor = os.path.dirname(os.path.abspath(path))
    while cursor and not os.path.exists(cursor):
        missing.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    return list(reversed(missing))


def _cleanup_staged_json(entries):
    failures = []
    for entry in entries:
        temporary = entry.get("temporary")
        if not temporary or not os.path.exists(temporary):
            continue
        try:
            os.unlink(temporary)
        except OSError as exc:
            failures.append("%s: %s" % (temporary, exc))
    return failures


def _remove_created_directories(paths):
    failures = []
    for path in sorted(set(paths), key=len, reverse=True):
        if not os.path.lexists(path):
            continue
        try:
            if _path_has_link_or_reparse(path) or not os.path.isdir(path):
                raise OSError("created directory became link-backed or non-directory")
            os.rmdir(path)
        except OSError as exc:
            failures.append("%s: %s" % (path, exc))
    return failures


def _stage_json_publications(publications, journal=None):
    """Create durable same-directory temp files for the complete JSON set."""

    normalized = [
        (os.path.abspath(str(path)), _publication_json_bytes(value))
        for path, value in publications
    ]
    states = _publication_target_states(normalized)
    if journal is None:
        journal = {"entries": [], "states": states, "created_dirs": []}
    if (not isinstance(journal, dict)
            or not isinstance(journal.get("entries"), list)
            or not isinstance(journal.get("created_dirs"), list)
            or journal["entries"] or journal["created_dirs"]):
        raise ValueError("JSON publication journal must be a fresh mutable journal")
    journal["states"] = states
    entries = journal["entries"]
    created_dirs = journal["created_dirs"]
    try:
        for path, payload in normalized:
            missing = _missing_parent_directories(path)
            parent = os.path.dirname(path)
            created_dirs.extend(missing)
            os.makedirs(parent, exist_ok=True)
            if (_path_has_link_or_reparse(parent)
                    or not os.path.isdir(parent)):
                raise ValueError(
                    "builder JSON publication parent became unsafe: %s" % parent
                )
            descriptor, temporary = tempfile.mkstemp(
                prefix=".%s.builder-publication." % os.path.basename(path),
                suffix=".tmp",
                dir=parent,
            )
            entry = {
                "path": path,
                "payload": payload,
                "temporary": temporary,
            }
            try:
                # Register the cleanup intent before writing any bytes.  If a
                # hostile/custom list cannot accept the entry, clean the one
                # still-local resource explicitly so no temp escapes the
                # transaction journal.
                entries.append(entry)
            except BaseException as exc:
                failures = []
                try:
                    os.close(descriptor)
                except OSError as close_exc:
                    failures.append("%s: %s" % (temporary, close_exc))
                try:
                    os.unlink(temporary)
                except OSError as unlink_exc:
                    failures.append("%s: %s" % (temporary, unlink_exc))
                if failures:
                    raise OSError(
                        "builder JSON staging registration rollback failed: %s"
                        % "; ".join(failures)
                    ) from exc
                raise
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
    except BaseException as exc:
        cleanup_failures = _cleanup_staged_json(entries)
        cleanup_failures.extend(_remove_created_directories(created_dirs))
        if cleanup_failures:
            raise OSError(
                "builder JSON staging rollback failed: %s"
                % "; ".join(cleanup_failures)
            ) from exc
        raise
    return journal


def _revalidate_publication_target(path, state):
    if state["existed"]:
        if (_path_has_link_or_reparse(path) or not os.path.isfile(path)):
            raise ValueError("builder JSON publication target became unsafe: %s" % path)
        with open(path, "rb") as stream:
            current = stream.read()
        if current != state["payload"]:
            raise ValueError("builder JSON publication target drifted: %s" % path)
    elif os.path.lexists(path):
        raise ValueError("builder JSON publication target appeared: %s" % path)


def _replace_publication_stage(temporary, destination):
    """Replace one JSON target; split out for deterministic failure injection."""

    if (_path_has_link_or_reparse(temporary)
            or not os.path.isfile(temporary)):
        raise OSError("builder publication stage is no longer a regular file")
    os.replace(temporary, destination)


def _atomic_restore_publication_bytes(path, payload):
    """Restore exact bytes without sharing the injectable forward replace hook."""

    parent = os.path.dirname(path)
    if _path_has_link_or_reparse(parent) or not os.path.isdir(parent):
        raise OSError("builder rollback parent is unsafe: %s" % parent)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s.builder-rollback." % os.path.basename(path),
        suffix=".tmp",
        dir=parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore_publication_targets(states, attempted_paths):
    failures = []
    for path in reversed(attempted_paths):
        state = states[path]
        try:
            if state["existed"]:
                _atomic_restore_publication_bytes(path, state["payload"])
            elif os.path.lexists(path):
                if (_path_has_link_or_reparse(path)
                        or not os.path.isfile(path)):
                    raise OSError("new publication target became unsafe")
                os.unlink(path)
        except OSError as exc:
            failures.append("%s: %s" % (path, exc))
    return failures


def _rollback_asset_journal(journal):
    failures = []
    for entry in reversed(journal.get("created_files") or ()):
        path = entry["path"]
        try:
            if not os.path.lexists(path):
                continue
            if (_path_has_link_or_reparse(path)
                    or not os.path.isfile(path)):
                raise OSError("created asset became unsafe")
            with open(path, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
            if digest != entry["sha256"]:
                raise OSError("created asset drifted before rollback")
            os.unlink(path)
        except OSError as exc:
            failures.append("%s: %s" % (path, exc))
    failures.extend(_remove_created_directories(journal.get("created_dirs") or ()))
    return failures


def _retain_publication_blockers(entries, blockers, attempted_paths):
    """Keep the current fail-closed marker after an incomplete rollback."""

    failures = []
    attempted = set(attempted_paths)
    payloads = {
        entry["path"]: entry["payload"] for entry in entries
        if entry["path"] in blockers
    }
    for path in sorted(blockers & attempted):
        try:
            _atomic_restore_publication_bytes(path, payloads[path])
        except OSError as exc:
            failures.append("%s: %s" % (path, exc))
    return failures


def _publish_builder_transaction(
        json_publications, asset_plans=(), blocker_paths=()):
    """Commit every builder JSON and asset as one rollback-protected batch."""

    stage_journal = {"entries": [], "states": {}, "created_dirs": []}
    entries = stage_journal["entries"]
    states = stage_journal["states"]
    json_created_dirs = stage_journal["created_dirs"]
    asset_journals = []
    attempted = []
    blockers = {os.path.abspath(str(path)) for path in blocker_paths or ()}
    try:
        _stage_json_publications(json_publications, journal=stage_journal)
        states = stage_journal["states"]
        staged_paths = {entry["path"] for entry in entries}
        if not blockers.issubset(staged_paths):
            raise ValueError("builder blocker path is not part of the JSON transaction")
        # Publish the fail-closed marker before the first asset or source fact.
        # A caught failure rolls it back with the rest of the batch; a hard
        # process interruption leaves it visible so validators cannot accept a
        # mixed builder/compiler generation.
        for entry in entries:
            path = entry["path"]
            if path not in blockers:
                continue
            _revalidate_publication_target(path, states[path])
            attempted.append(path)
            _replace_publication_stage(entry["temporary"], path)
            entry["temporary"] = None
        for plan in asset_plans or ():
            # Register the journal with the outer transaction before the first
            # asset mutation, so even a failure after the inner publisher's
            # replace/return boundary remains rollback-visible.
            journal = {"created_files": [], "created_dirs": []}
            asset_journals.append(journal)
            _publish_asset_plan(plan, journal=journal)
        # Rebind migration authority after all asset work and immediately
        # before the first JSON replacement.  This catches a hardlink/identity
        # change introduced inside the publication window.
        for plan in asset_plans or ():
            _recheck_role_promotions(plan)
        for entry in entries:
            path = entry["path"]
            if path in blockers:
                continue
            _revalidate_publication_target(path, states[path])
            attempted.append(path)
            _replace_publication_stage(entry["temporary"], path)
            entry["temporary"] = None
    except BaseException as exc:
        rollback_failures = _cleanup_staged_json(entries)
        for journal in reversed(asset_journals):
            rollback_failures.extend(_rollback_asset_journal(journal))
        non_blockers = [path for path in attempted if path not in blockers]
        blocker_attempts = [path for path in attempted if path in blockers]
        rollback_failures.extend(
            _restore_publication_targets(states, non_blockers)
        )
        if rollback_failures:
            # Never erase the only validator-visible blocker while another
            # asset/JSON rollback is incomplete.  Re-publish its staged bytes
            # even if an earlier rollback step happened to disturb it.
            rollback_failures.extend(
                _retain_publication_blockers(entries, blockers, attempted)
            )
        else:
            rollback_failures.extend(
                _restore_publication_targets(states, blocker_attempts)
            )
            rollback_failures.extend(
                _remove_created_directories(json_created_dirs)
            )
        if rollback_failures:
            raise OSError(
                "builder publication rollback failed: %s"
                % "; ".join(rollback_failures)
            ) from exc
        raise
    finally:
        _cleanup_staged_json(entries)


def _run_unlocked(
        args, backend=None, adapter_runner=None, deferred_asset_plans=None):
    """Build every asset in an isolated staging area before workspace publication."""

    original_root = getattr(args, "asset_root", None)
    if not original_root:
        return _run_unlocked_core(
            args, backend=backend, adapter_runner=adapter_runner
        )
    workspace = _publication_workspace(args)
    stage_base = tempfile.mkdtemp(prefix="exam-cram-asset-stage-")
    stage_root = os.path.join(stage_base, "references", "assets")
    try:
        # Enter the cleanup boundary immediately after mkdtemp.  A partially
        # successful recursive mkdir must not strand the staging root.
        os.makedirs(stage_root)
        args.asset_root = stage_root
        code, raw_input, report = _run_unlocked_core(
            args, backend=backend, adapter_runner=adapter_runner
        )
        if code != 0:
            return code, raw_input, report
        try:
            plan = _plan_staged_assets(
                stage_root, original_root, raw_input, workspace
            )
            if plan.get("role_promotions"):
                report.setdefault("asset_role_promotions", []).extend(
                    plan["role_promotions"]
                )
            if deferred_asset_plans is None:
                _publish_asset_plan(plan)
            else:
                deferred_asset_plans.append(plan)
        except (OSError, ValueError) as exc:
            if report is None:
                report = {"warnings": []}
            report.setdefault("warnings", []).append(
                "asset_publication_rejected: %s" % exc
            )
            report["_no_publish_on_failure"] = True
            return 5, {
                "error": "asset publication rejected before workspace mutation: %s" % exc
            }, report
        return code, raw_input, report
    finally:
        args.asset_root = original_root
        try:
            shutil.rmtree(stage_base)
        except OSError as exc:
            raise OSError(
                "cannot securely remove staged course assets at %s: %s"
                % (stage_base, exc)
            ) from exc


def run(args, backend=None, adapter_runner=None, *, _publication_locked=False,
        publication_workspace=None, _deferred_asset_plans=None):
    """Build in memory while serializing every validator-visible asset write."""

    workspace = _publication_workspace(args, explicit=publication_workspace)
    if _deferred_asset_plans is not None and not _publication_locked:
        raise ValueError(
            "deferred asset publication requires the caller-held workspace lock"
        )
    if workspace is not None and not _publication_locked:
        with workspace_publication_lock(workspace):
            return _run_unlocked(
                args, backend=backend, adapter_runner=adapter_runner
            )
    return _run_unlocked(
        args,
        backend=backend,
        adapter_runner=adapter_runner,
        deferred_asset_plans=_deferred_asset_plans,
    )


def _main_locked(args, backend=None, publication_workspace=None):
    """Run and publish while the caller owns the workspace publication lock."""

    asset_plans = []
    code, raw_input, report = run(
        args,
        backend=backend,
        _publication_locked=True,
        publication_workspace=publication_workspace,
        _deferred_asset_plans=asset_plans,
    )
    if code != 0:
        sys.stderr.write((raw_input or {}).get("error", "失败") + "\n")
        if report is not None and not report.get("_no_publish_on_failure"):
            publications = [(args.report, report)]
            if report.get("ai_review"):
                # 失败退出同样要留接管清单——exit 4（全是扫描件）正是最需要 AI 接管的场景
                mp = os.path.join(os.path.dirname(os.path.abspath(args.report)), "ai_review_manifest.json")
                publications.append((
                    mp,
                    {"note": "程序无法处理/不确定的材料清单——AI 必须逐条处理，绝不静默略过",
                     "entries": report["ai_review"]},
                ))
            _publish_builder_transaction(publications)
            if report.get("ai_review"):
                print("[!] AI 接管清单：%d 条未能自动处理的材料 → %s（请逐条处理）"
                      % (len(report["ai_review"]), mp))
        return code
    if any(plan.get("role_promotions") for plan in asset_plans):
        sys.stderr.write(
            "legacy asset-role migration requires scripts/ingest_course.py so "
            "the builder-to-compiler generation remains fail closed\n"
        )
        return 5
    manifest_path = os.path.join(os.path.dirname(os.path.abspath(args.report)), "ai_review_manifest.json")
    _publish_builder_transaction(
        (
            (args.out, raw_input),
            (args.report, report),
            (manifest_path, {
                "note": "程序无法处理/不确定的材料清单——AI 必须逐条处理，绝不静默略过",
                "entries": report.get("ai_review", []),
            }),
        ),
        asset_plans=asset_plans,
    )
    if report.get("ai_review"):
        print("[!] AI 接管清单：%d 条未能自动处理的材料 → %s（请逐条处理）"
              % (len(report["ai_review"]), manifest_path))
    print("[+] raw_input: %s（%d 阶段 / %d 题，其中讲义题 %d）"
          % (args.out, len(raw_input["phases"]), len(raw_input["quiz_bank"]),
             report["examples_detected"] + report["quizzes_detected"]))
    print("[+] report: %s（后端 %s，渲染 %d 页，警告 %d）"
          % (args.report, report["backend"], report["pages_rendered"], len(report["warnings"])))
    return 0


def main(argv=None, backend=None):
    # Reconfigure before parse_args so Chinese --help text prints on Windows.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_arg_parser().parse_args(argv)
    try:
        workspace = _publication_workspace(args)
        if workspace is None:
            return _main_locked(args, backend=backend)
        ingest_path = os.path.join(workspace, ".ingest")
        ingest_preexisting = os.path.lexists(ingest_path)
        with workspace_publication_lock(workspace):
            if ingest_preexisting and not os.path.lexists(ingest_path):
                raise ConflictError(".ingest changed while acquiring the publication lock")
            if (not ingest_preexisting
                    and _writes_ingestion_files(args, workspace)):
                # publication_lock holds state.  A new .ingest did not exist for
                # it to lock, so create/hold mutation.lock before the first JSON.
                with IngestionStore(workspace).mutation_lock():
                    return _main_locked(
                        args, backend=backend, publication_workspace=workspace
                    )
            return _main_locked(
                args, backend=backend, publication_workspace=workspace
            )
    except ConflictError as exc:
        sys.stderr.write("builder publication conflict: %s\n" % exc)
        return 7
    except (OSError, ValueError) as exc:
        sys.stderr.write("builder publication failed: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
