#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Official pre-ingest: scan a course-materials folder → raw_input.json (+ optional page-image
assets + a parse report) for scripts/ingest.py.

This is NOT an OCR project. It is a deterministic, honest, first official entrypoint that:
  - preserves page provenance (source_file / source_pages),
  - preserves full-page renders for figure-dependent lecture pages (so diagram questions keep context),
  - extracts obvious lecture Example/Quiz problem-solution pairs into quiz_bank items,
  - never pretends lossy text extraction is complete, and
  - fails / warns clearly when the OPTIONAL PDF backends are unavailable.

stdlib-only core + tests. PDF *text extraction* and *page rendering* are OPTIONAL backends:
  - text:   pypdf
  - render: PyMuPDF (`fitz`, native PNG, no extra deps) OR pypdfium2 + Pillow (its to_pil adapter)
Install only if you need them, e.g.:  pip install pypdf pymupdf   (or: pip install pypdf pypdfium2 Pillow)
Rendering also needs --asset-root <workspace>/references/assets (where the page PNGs are written).

Usage:
  python scripts/build_raw_input_from_workspace.py \\
      --materials ./course_materials --out raw_input.json \\
      --asset-root skill_workspace/references/assets \\
      --render-pages auto --extract-lecture-questions auto --report parse_report.json
  python scripts/ingest.py -i raw_input.json -o skill_workspace
  python scripts/validate_workspace.py skill_workspace
"""
import argparse
import zlib
import json
import os
import re
import sys

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
_TOC_RE = re.compile(r"\.{4,}")   # 4+ dot-leaders → a table-of-contents line, not a heading


def _role_of_tail(tail):
    """Role of a marker from the text right after its number. A leading "(Continued)" may precede the
    role word ("Example 1.1 (Continued) Solution …"); strip it before matching. Used everywhere so
    detect_lecture_markers and the text-slicers agree."""
    # strip ONLY a leading "continued" token (+ optional number/parens/separators) — not the words
    # after it, so "Continued Solution" / "Continued: Solution" (no parens) still leaves "Solution".
    tail_role = re.sub(r"^\s*\(?\s*continued\b\s*\d*\s*\)?[\s:.\-]*", "", tail, flags=re.I)
    if _ROLE_PROBLEM_RE.match(tail) or _ROLE_PROBLEM_RE.match(tail_role):
        return "problem"
    if _ROLE_SOLUTION_RE.match(tail) or _ROLE_SOLUTION_RE.match(tail_role):
        return "solution"
    return "problem"   # bare "Quiz 1.1" with no keyword → a problem


def _iter_markers(text):
    """Every NON-TOC lecture marker in TEXT-POSITION order — the single source of truth shared by
    detect_lecture_markers AND the text-slicers, so TOC-skip / role / plural never diverge between
    them. Returns dicts: {start, kind, chapter, num, role, continued}."""
    text = text or ""
    out = []
    for kind, rx in (("example", _EXAMPLE_RE), ("quiz", _QUIZ_RE)):
        for m in rx.finditer(text):
            nl = text.find("\n", m.end())
            line = text[m.start():(nl if nl >= 0 else len(text))][:300]   # the whole heading line
            if _TOC_RE.search(line):   # dot-leaders anywhere on the line → TOC entry (even long titles), skip
                continue
            tail = text[m.end():m.end() + 48]
            out.append({"start": m.start(), "kind": kind, "chapter": int(m.group(1)), "num": int(m.group(2)),
                        "role": _role_of_tail(tail), "continued": bool(re.search(r"\bContinued\b", tail, re.I))})
    out.sort(key=lambda d: d["start"])
    return out


def detect_lecture_markers(text):
    """Find lecture Example/Quiz markers on one page (TEXT-POSITION order). Returns a list of
    {kind: 'example'|'quiz', chapter: int, num: int, role: 'problem'|'solution', continued: bool}."""
    return [{k: d[k] for k in ("kind", "chapter", "num", "role", "continued")} for d in _iter_markers(text)]


def orphan_solution_keys(pages):
    """Solution markers whose (kind,chapter,num) never had a detected problem — surfaced as a
    warning so a mis-detected pair is fail-loud, not silently dropped."""
    marked = _markers_with_pages(pages)
    probs = {_key(mk) for _, mk in marked if mk["role"] == "problem"}
    sols = {_key(mk) for _, mk in marked if mk["role"] == "solution"}
    return sorted(sols - probs)


def _markers_with_pages(pages):
    marked = []
    for i, pg in enumerate(pages):
        for mk in detect_lecture_markers(pg.get("text", "")):
            marked.append((i, mk))
    return marked


def _key(mk):
    return (mk["kind"], mk["chapter"], mk["num"])


def _problem_statement(page_text, kind, chapter, num):
    """Extract the problem text for `<kind> X.Y` on a page — concatenating EVERY problem-role slice for
    that key (so a same-page `Problem …` + `Problem (Continued) …` are both captured), each cut at the
    next marker. Skips TOC lines and `Solution` markers of the same number (solution-before-problem)."""
    text = page_text or ""
    mks = _iter_markers(text)
    starts = [d["start"] for d in mks]
    parts = []
    for d in mks:
        if d["kind"] == kind and d["chapter"] == chapter and d["num"] == num and d["role"] != "solution":
            after = [st for st in starts if st > d["start"]]
            e = min(after) if after else len(text)
            parts.append(" ".join(text[d["start"]:e].split()).strip())
    return " ".join(parts).strip()


def _body_after_marker(stmt, kind, chapter, num):
    """The text of `stmt` after stripping the leading `<kind> X.Y [Problem]` heading — used to tell a
    real prompt from a marker-only title (a slide whose prompt is in an image pypdf couldn't read)."""
    rx = _EXAMPLE_RE if kind == "example" else _QUIZ_RE
    m = rx.search(stmt or "")
    if not m:
        return (stmt or "").strip()
    rest = stmt[m.end():]
    rest = re.sub(r"^\s*[\):.\-]?\s*\(?\s*problems?\b\)?", "", rest, flags=re.I)  # drop a trailing "Problem(s)"
    return rest.strip(" .:：、)）-—\t\n")


def _solution_statement(page_text, kind, chapter, num):
    """Extract the solution text for `<kind> X.Y` on a page — concatenating EVERY solution slice for
    that key (so a same-page `Solution …` + `Solution (Continued) …` are both captured), each cut at
    the next marker. The real `answer` for text-complete items so grading has something to compare to."""
    text = page_text or ""
    mks = _iter_markers(text)
    starts = [d["start"] for d in mks]
    parts = []
    for d in mks:
        if d["kind"] == kind and d["chapter"] == chapter and d["num"] == num and d["role"] == "solution":
            after = [st for st in starts if st > d["start"]]
            e = min(after) if after else len(text)
            parts.append(" ".join(text[d["start"]:e].split()).strip())
    return " ".join(parts).strip()


def extract_lecture_items(pages):
    """Pair each `<kind> X.Y` problem with its matching `Solution` pages (incl. `(Continued)`), assign
    stable IDs, and flag asset dependence. De-dups problems by (kind, chapter, num, source_file) — a
    marker reused across files (lecture/ch01.pdf + homework/ch01.pdf both `Quiz 1.1`) yields two
    distinct, file-namespaced items. Solutions are claimed same-file-first (a continuation in a file
    with no competing problem still merges), surviving intervening problems and solution-before-problem."""
    marked = _markers_with_pages(pages)
    sol_by_key = {}
    for mj, (pj, mk2) in enumerate(marked):
        if mk2["role"] == "solution":
            sol_by_key.setdefault(_key(mk2), []).append((mj, pj))
    prob_files = {}    # key -> set of files that contain a PROBLEM marker for it
    for (pj, mk2) in marked:
        if mk2["role"] == "problem":
            prob_files.setdefault(_key(mk2), set()).add(pages[pj]["file"])
    ambiguous = {k for k, fs in prob_files.items() if len(fs) > 1}   # same marker in >1 file → namespace id
    file_idx = {}      # injective per-file index within an ambiguous key (sanitized stems can collide)
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

        # a problem may span pages: gather later `Problem (Continued)` pages of the same key+file.
        prob_idxs = sorted({i} | {pj2 for (pj2, mk2) in marked
                                  if _key(mk2) == key and mk2["role"] == "problem"
                                  and pages[pj2]["file"] == pf and mk2.get("continued")})
        q_pages = sorted({(pages[k]["file"], pages[k]["page"]) for k in prob_idxs}, key=lambda fp: (fp[1], fp[0]))

        # take ALL usable solutions (both before AND after the problem). For a key that is a problem in
        # >1 file (ambiguous), only SAME-FILE solutions are usable — a separate solutions-only file's
        # `Quiz X.Y Solution` can't be assigned to one of the competing problems, so don't claim it.
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
        # scope the asset heuristic to THIS problem's slice on the anchor page; continued pages (which
        # wholly belong to this problem) are scanned whole.
        stmt = _problem_statement(prob_text, kind, key[1], key[2])
        # STRONG figure-shown cues fire on any source (a .txt "shade the Venn at right" is fail-closed);
        # WEAK figure-noun cues fire only for a renderable PDF (a .txt "draw the graph" stays text-complete).
        renderable = pf.lower().endswith(".pdf")
        # scope the heuristic to THIS problem's sliced text on every page (anchor + continued) — a
        # continued page that also starts the next item must not lend that item's "Venn" to this one.
        needs = (requires_assets_heuristic(stmt or prob_text, renderable) or any(
            requires_assets_heuristic(_problem_statement(pages[k].get("text", ""), kind, key[1], key[2]),
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
        # 完整原始题面（锚页 + 续页切片）——needs/marker_only 的 question 会被替换成指引句，
        # 跨页图线索（见下页图 在续页上）要靠它检查
        _cont_parts = [_problem_statement(pages[k].get("text", ""), kind, key[1], key[2])
                       or " ".join((pages[k].get("text") or "").split()) for k in prob_idxs if k != i]
        orig_question = " ".join([stmt] + _cont_parts).strip()
        if needs:
            qts = "page_reference"
            question = ("（%s %d.%d）本题依赖原始讲义 %s 第 %d 页的图/表，须配合所附 asset 作答。"
                        % (label, key[1], key[2], pf, prob_page["page"]))
        elif marker_only:
            qts = "page_reference"
            question = ("（%s %d.%d）题面未能从文本提取（可能在图片中），见原始讲义 %s 第 %d 页。"
                        % (label, key[1], key[2], pf, prob_page["page"]))
        else:
            qts = "full"
            # continued 切片已在 orig_question 内按题裁好（Quiz 1.1 (Continued) 页不吞 Quiz 1.2）
            question = orig_question
        item_id = "lecture_%s_%d_%d" % (kind, key[1], key[2])
        if key in ambiguous:   # readable stem + injective index (so a/b.pdf vs a_b.pdf don't collide)
            item_id += "__%s_%d" % (re.sub(r"[^\w]", "_", os.path.splitext(pf)[0]), file_idx[(key, pf)])
        _qt, _opts = _classify_question_type(question)
        item = {
            "id": item_id,
            "chapter": key[1],
            "type": "diagram" if needs else _qt,
            "question": question,
            "source": "material",
            "source_file": pf,
            "source_pages": [p for (f, p) in q_pages],
            "_question_pages": q_pages,                     # stripped from the emitted bank
            "_prompt_text": orig_question,                  # 原始题面含续页（needs 项 question 被替换成指引句）
            "_render": bool(needs or marker_only),          # render the page for figure- AND image-prompt items
            "requires_assets": bool(needs),
            "question_text_status": qts,
        }
        if not needs and _qt == "choice" and _opts:
            item["options"] = _opts
        if not needs:
            item["keywords"] = []  # subjective recommended field; left for the tutor/teacher to fill
        if ans_idx:
            ans = sorted({(pages[j]["file"], pages[j]["page"]) for j in ans_idx}, key=lambda fp: (fp[1], fp[0]))
            first_file = ans[0][0]
            item["answer_source_file"] = first_file
            item["answer_source_pages"] = [p for (f, p) in ans if f == first_file]
            item["_answer_pages"] = ans
            ref = "见原始讲义 %s 第 %s 页的解答。" % (
                first_file, "、".join(str(p) for (f, p) in ans if f == first_file))
            # keep the EXTRACTED solution text whenever there is one (grading needs it) — even for a
            # figure-dependent item; only fall back to the page-reference when no text was extracted.
            sol = " ".join(t for t in (_solution_statement(pages[j].get("text", ""), kind, key[1], key[2])
                                       for j in ans_idx) if t).strip()
            if sol:
                item["answer"] = sol + ("（解答可能依赖图，须看原页/asset）" if needs else "")
                _apply_typed_answer(item)
            else:
                item["answer"] = ref + ("（依赖图，须看原页/asset）" if needs else "")
                _apply_typed_answer(item)
        else:
            item["answer_status"] = "unknown"   # honest: no solution page detected
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


def _hw_num(s):
    """Problem number as int, or a normalized string for textbook decimals / lettered subparts
    （1.1 ≠ 1；1(a) 与 1a 同号，规范成 '1a'）."""
    if s is None:
        return None
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


def extract_homework_items(pages, root_name="", exclude=frozenset()):
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
              "homework_problems": 0, "homework_answered": 0, "warnings": _note_msgs}
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
            if ans is None:
                ans = inline_keys.get(mk["num"])       # 同文件 answer-key 段（不相邻也配）
            if ans is None:
                ans = tail_answers.get(mk["num"])      # 同文件尾部「解答区」重复标题段
            if ans is None:
                got = sol_answers.get((hf, mk["num"]))
                ans = got[:3] if got else None
            q_pages = _pages_for_span(bounds, mk["start"], nxt_q)
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
            marker_only = (len(re.findall(r"[0-9A-Za-z一-鿿]", _mo_body)) == 0
                           or not re.search(r"[A-Za-z一-鿿+\-*/=^%<>?？()（）]", _mo_body))
            # chapter：只在题文/文件名明说时才标（第N章 / Chapter N / chNN）——作业号 ≠ 章节号，不硬编
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
                    item["_answer_pages"] = [(ans[0], p) for p in (ans[2] or [])]
            elif ans and requires_assets_heuristic(ans[1], renderable=is_pdf.get(ans[0], False)):
                # 题面纯文本、官方解答依赖图（see the graph below）——渲染答案侧原页作 answer_context，
                # 复盘讲解不至于指着看不见的图；不设 requires_assets（题面本身完整可问）
                item["_render"] = True
                item["_answer_pages"] = [(ans[0], p) for p in (ans[2] or [])]
            items.append(item)
            report["homework_problems"] += 1
        if dup_counts:
            report["warnings"].append("hw_duplicate_problem: %s（%s——每题保留第一处标记，重现多为页眉/"
                                      "解答区重复）" % (hf, "、".join("Problem %s×%d" % (n, c)
                                                                      for n, c in sorted(dup_counts.items(),
                                                                                         key=lambda kv: str(kv[0])))))
        # chapter 只在题文/文件名明说时才标（作业号≠章节号，绝不猜）——但没章节的题 --chapter 过滤
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
        if (pg.get("text") or "").strip():
            sec["text_blocks"].append("<!-- %s p.%d -->\n%s" % (pg.get("file"), pg.get("page"),
                                                                 pg.get("text", "").strip()))
    if notes is not None:
        notes.extend(f for f in all_files if f not in clue_files)
    return [by_ch[c] for c in sorted(order)]


def _safe_asset_name(file, page, item_id, suffix=""):
    # keep subdirs (sanitized) so lecture/ch01.pdf and solutions/ch01.pdf don't collide on the same page
    stem = re.sub(r"[^\w.\-]", "_", os.path.splitext(file or "src")[0])
    if re.fullmatch(r"[.\-_]*", stem):         # all-dots/dashes/underscores (e.g. a ".." name) → a token
        stem = "src"
    sid = re.sub(r"[^\w.\-]", "_", str(item_id))
    return "%s_p%03d_%s%s.png" % (stem, int(page), sid, suffix)


def build_raw_input(course_name, sections, lecture_items, homework_items=None):
    """Assemble a raw_input.json compatible with scripts/ingest.py.
    `quiz_items` mirrors the bank for downstream tools; ingest reads `quiz_bank`."""
    phases = []
    for n, sec in enumerate(sections, 1):
        body = "\n\n".join(sec["text_blocks"]) or "（本章未提取到文本，请结合原始页/asset 复习）"
        phases.append({
            "phase_num": n,
            "phase_name": "第 %d 章" % sec["chapter"],
            "wiki_filename": "ch%02d.md" % sec["chapter"],
            "wiki_content": "# 第 %d 章\n\n来源文件：%s\n\n%s" % (
                sec["chapter"], "、".join(sec["files"]), body),
            "source_pages": sorted(set(p for p in sec["pages"] if p)),
        })
    if not phases:
        phases = [{"phase_num": 1, "phase_name": "第 1 章", "wiki_filename": "ch01.md",
                   "wiki_content": "# 第 1 章\n\n（未提取到内容）"}]
    # strip internal render-only keys (e.g. _answer_pages) so they don't leak into the bank
    def _clean(it):
        return {k: v for (k, v) in it.items() if not k.startswith("_")}
    bank = [_clean(it) for it in (list(lecture_items) + list(homework_items or []))]
    return {"course_name": course_name, "phases": phases, "quiz_bank": bank,
            "quiz_items": bank}   # optional mirror field (documented); ingest ignores unknown keys


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
            "没有可用的 PDF 文本后端。请安装可选依赖 `pypdf`（pip install pypdf）后重试——"
            "PDF 文本提取需要它（.txt/.md 材料无需任何后端）。")

    def render_page_png(self, pdf_path, page_index):
        return None


class RealBackend(object):
    def __init__(self, text_lib=None, render_lib=None):
        self.text_lib, self.render_lib = text_lib, render_lib
        self.name = "+".join(x for x in (text_lib, render_lib) if x) or "none"

    def can_text(self):
        return bool(self.text_lib)

    def can_render(self):
        return bool(self.render_lib)

    def page_texts(self, pdf_path):
        if self.text_lib != "pypdf":
            return NoBackend().page_texts(pdf_path)
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        return [(pg.extract_text() or "") for pg in reader.pages]

    def render_page_png(self, pdf_path, page_index):
        if self.render_lib == "pypdfium2":
            import io
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_path)
            bitmap = doc[page_index].render(scale=1.5)
            buf = io.BytesIO()
            bitmap.to_pil().save(buf, format="PNG")   # PIL adapter — Pillow verified at detect time
            return buf.getvalue()
        if self.render_lib == "pymupdf":
            import fitz
            doc = fitz.open(pdf_path)
            return doc[page_index].get_pixmap().tobytes("png")   # native PNG, no Pillow needed
        return None


def detect_backend():
    text_lib = render_lib = None
    try:
        import pypdf  # noqa: F401
        text_lib = "pypdf"
    except Exception:
        pass
    # PyMuPDF renders to PNG natively; pypdfium2 needs Pillow for its .to_pil() adapter, so only
    # claim pypdfium2 as a render backend when Pillow is ALSO importable (else can_render() lies).
    try:
        import fitz  # noqa: F401  (PyMuPDF) — preferred: no extra deps
        render_lib = "pymupdf"
    except Exception:
        try:
            import pypdfium2  # noqa: F401
            import PIL  # noqa: F401  (Pillow — required by pypdfium2's to_pil adapter)
            render_lib = "pypdfium2"
        except Exception:
            pass
    return RealBackend(text_lib, render_lib) if (text_lib or render_lib) else NoBackend()


# ---------------------------------------------------------------------------
# Path safety + filesystem
# ---------------------------------------------------------------------------

def _under(root, child):
    root_r = os.path.normcase(os.path.realpath(root))
    child_r = os.path.normcase(os.path.realpath(child))
    return child_r == root_r or child_r.startswith(root_r + os.sep)


# Tooling/VCS dirs that NEVER hold course material → always pruned from the materials scan.
ALWAYS_PRUNE = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
                ".idea", ".vscode", ".pytest_cache", ".ipynb_checkpoints"}
# generated skill-workspace files (not course material) → skipped even if they sit at the materials root.
SKIP_FILES = {"study_plan.md", "study_progress.md", "walkthrough.md", "raw_input.json",
              "parse_report.json", "ingest_report.json", "ai_review_manifest.json"}


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
    """Return sorted (pdf_paths, text_paths, pruned_dirs). Prunes tooling/VCS dirs unconditionally, and
    a `references/`+`scratch/` dir ONLY when it carries a generated-workspace signature — so a prior
    workspace inside the course folder isn't re-ingested, but a real course `references/` of PDFs is kept.
    (Real case: D:\\EEC 160 held a previous ad-hoc workspace → without pruning every lecture marker was
    triplicated across the pdf + extracted .txt + wiki .md, blowing up the bank with broken items.)"""
    pdfs, texts, pruned, others = [], [], [], []
    for dirpath, dirs, files in os.walk(materials_dir):
        keep = []
        for d in dirs:
            full = os.path.join(dirpath, d)
            if d.lower() in ALWAYS_PRUNE or _is_leftover_workspace(full, d) or _is_workspace_root(full):
                pruned.append(os.path.relpath(full, materials_dir).replace(os.sep, "/"))
            else:
                keep.append(d)
        dirs[:] = keep   # os.walk: prune in place
        at_root = os.path.realpath(dirpath) == os.path.realpath(materials_dir)
        for fn in sorted(files):
            low = fn.lower()
            if at_root and low in SKIP_FILES:   # generated workspace file at the ROOT (study_plan/progress/…)
                continue                          # a same-named file in a subfolder is kept (could be real)
            full = os.path.join(dirpath, fn)
            if low.endswith(".pdf"):
                pdfs.append(full)
            elif low.endswith((".txt", ".md")):
                texts.append(full)
            elif not fn.startswith((".", "~$")) and low not in ("thumbs.db", "desktop.ini"):
                others.append(full)                    # 不支持的格式也要留痕，绝不零痕迹丢弃
                                                       #（只豁免已知 OS 垃圾名，不按扩展名类猜）
    return sorted(pdfs), sorted(texts), sorted(pruned), sorted(others)


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
            item.setdefault("keywords", [])
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
        description="官方课程材料 → raw_input.json（+ 可选页面图 assets + 解析报告），供 ingest.py 使用。",
        epilog="可选依赖：文本 pip install pypdf；渲染 pip install pymupdf（自带 PNG）或 pypdfium2 Pillow。"
               "（.txt/.md 材料无需任何依赖。）")
    p.add_argument("--materials", required=True, help="课程材料文件夹（含 PDF / txt / md）")
    p.add_argument("--out", default="raw_input.json", help="输出 raw_input.json 路径")
    p.add_argument("--report", default="parse_report.json", help="解析报告 JSON 路径")
    p.add_argument("--asset-root", default=None,
                   help="渲染页图写入目录，应指向 <workspace>/references/assets。"
                        "渲染开启而未指定时：auto 跳过渲染并告警，required 报错")
    p.add_argument("--render-pages", choices=["never", "auto", "required"], default="auto",
                   help="渲染依赖图的页面：never/auto/required（required 时无渲染后端/无 --asset-root 则报错）")
    p.add_argument("--extract-lecture-questions", choices=["never", "auto"], default="auto",
                   help="是否抽取讲义 Example/Quiz 题：never/auto")
    p.add_argument("--extract-homework", choices=["never", "auto"], default="auto",
                   help="是否抽取作业题（含题答分离 PDF 的自动配对与 inline Solution）：never/auto")
    p.add_argument("--course-name", default=None, help="科目名称（默认取材料目录名）")
    return p


def run(args, backend=None):
    """Core run. `backend` injectable for tests (a fake with page_texts/render_page_png)."""
    backend = backend or detect_backend()
    report = {"materials": args.materials, "backend": getattr(backend, "name", "none"),
              "files_scanned": [], "pages_extracted": 0, "pages_rendered": 0,
              "examples_detected": 0, "quizzes_detected": 0, "pairs_detected": 0,
              "skipped": [], "warnings": [], "ai_review": []}

    materials = args.materials
    if not os.path.isdir(materials):
        return 2, {"error": "materials 目录不存在: %s" % materials}, None

    pdfs, texts, pruned, others = _scan_materials(materials)
    for op in others:
        rel_o = _rel(op, materials)
        report["skipped"].append({"file": rel_o, "why": "不支持的格式（仅解析 PDF/.txt/.md）"})
        report["warnings"].append("unsupported_format: %s（内容不会进 wiki/题库——请 AI 接管）" % rel_o)
        report["ai_review"].append({
            "kind": "unsupported_format", "file": rel_o,
            "action": "该文件格式本工具不解析、内容未导入。请 AI 直接读取该文件（多模态可读 docx/pptx/图片），"
                      "把知识点/题目手工补进工作区，或转成 PDF/.txt 后重新运行构建。"})
    report["files_scanned"] = [os.path.relpath(p, materials) for p in (texts + pdfs)]
    report["pruned_dirs"] = pruned
    if pruned:   # fail-loud: a prior workspace/tooling dir was skipped, so the user knows why it's ignored
        report["warnings"].append("pruned_non_material_dirs: %s（不当作课程材料扫描）" % "、".join(pruned[:8]))

    # Honest dependency failure: PDFs present but no text backend → stop with a clear, actionable error.
    if pdfs and not backend.can_text():
        report["warnings"].append("no_pdf_text_backend")
        return 3, {"error": "发现 %d 个 PDF，但没有可用的 PDF 文本后端。请安装可选依赖："
                            "`pip install pypdf`（PDF 文本提取需要它；把页面渲染成图还需 "
                            "`pip install pymupdf` 或 `pypdfium2 Pillow`——只装 pypdfium2 而无 Pillow 不会启用渲染）。"
                            "纯 .txt/.md 材料无需任何依赖。" % len(pdfs)}, report

    pages = []
    residue_files = {}
    residue_page_keys = set()
    page_pdf_all_raw = {}
    for tp in texts:
        pages.extend(_read_text_file_pages(tp, _rel(tp, materials), report))
    for pdf in pdfs:
        rel = _rel(pdf, materials)   # subdir-qualified identifier, not bare basename (avoids collisions)
        try:
            nonblank, no_content, total = 0, [], 0
            for i, txt in enumerate(backend.page_texts(pdf)):
                pages.append({"file": rel, "page": i + 1, "text": txt, "_pdf": pdf})
                page_pdf_all_raw[(rel, i + 1)] = pdf
                total += 1
                # 残渣感知判定：每页只剩页码「12」的扫描件不能算有文本（审计实测骗过精确空判定）
                if _page_has_content(txt):
                    nonblank += 1
                else:
                    no_content.append(i + 1)
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

    # require some ACTUAL text, not just blank pages from a scanned PDF (else we'd emit an empty wiki and exit 0)
    if not any(_page_has_content(p.get("text")) for p in pages):
        _flush_residue(set())
        report["warnings"].append("no_text_extracted")
        return 4, {"error": "未从 --materials 提取到任何文本内容（页面为空或全是扫描件/图片）。请确认有可解析的 "
                            "PDF/.txt/.md（PDF 文本需 pypdf；图片/扫描件需 OCR，本工具不做）。"}, report

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
        lecture_items = extract_lecture_items(lecture_pages)
        report["examples_detected"] = sum(1 for it in lecture_items if it["id"].startswith("lecture_example"))
        report["quizzes_detected"] = sum(1 for it in lecture_items if it["id"].startswith("lecture_quiz"))
        report["pairs_detected"] = sum(1 for it in lecture_items if it.get("answer_source_pages"))
        # fail-loud: a solution detected with no matching problem (mis-detected pair) → surface it
        for k in orphan_solution_keys(lecture_pages):
            report["warnings"].append("solution_without_problem: %s %d.%d" % k)
        if hw_related:
            overlap = sum(len(detect_lecture_markers(pg.get("text", "")))
                          for pg in pages if pg["file"] in hw_related)
            if overlap:
                report["warnings"].append("hw_lecture_overlap: 作业/解答文件里发现 %d 个讲义型标记，"
                                          "未按讲义题导入（该内容属于作业管线）" % overlap)

    homework_items = []
    if getattr(args, "extract_homework", "auto") != "never":
        homework_items, hw_rep = extract_homework_items(pages, _mat_root_name,
                                                        exclude=exam_rerouted)
        report["warnings"].extend(hw_rep.pop("warnings"))
        report.update(hw_rep)
    # ---- render assets for figure-dependent items ----
    asset_root = args.asset_root
    page_pdf = {(pg["file"], pg["page"]): pg["_pdf"] for pg in pages if pg.get("_pdf")}
    page_pdf_all = dict(page_pdf_all_raw)   # 含残渣页——「见下页图」的图页往往正是无文本页
    page_pdf_all.update(page_pdf)

    want_render = args.render_pages in ("auto", "required")
    if want_render and not backend.can_render():
        if args.render_pages == "required":
            return 3, {"error": "render-pages=required 但没有渲染后端。请安装 PyMuPDF（pip install pymupdf）"
                                "或 pypdfium2+Pillow（pip install pypdfium2 Pillow）。"}, report
        report["warnings"].append("render_unavailable")
    if want_render and not asset_root:
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

    can_write = bool(asset_root) and want_render and backend.can_render()
    rendered, missing_required = 0, []
    for it in list(lecture_items) + list(homework_items):
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
        plan = ([("question_context", f, p, "") for (f, p) in it.get("_question_pages", [])]
                + [("question_context", f, p, "_adj") for (f, p) in _adj]
                + [("answer_context", f, p, "_sol") for (f, p) in it.get("_answer_pages", [])])
        for role, file, page, suffix in plan:
            name = _safe_asset_name(file, page, it["id"], suffix)
            rel_path = "references/assets/" + name
            wrote = False
            pdf = page_pdf_all.get((file, page))
            if can_write and pdf is not None:
                try:
                    png = backend.render_page_png(pdf, page - 1)
                except Exception as e:   # a single malformed/encrypted page must not crash the whole run
                    png = None
                    report["skipped"].append({"file": file, "why": "渲染失败 p.%d: %s" % (page, e)})
                if png:
                    full = os.path.join(asset_root, name)
                    if not _under(asset_root, full):   # name is sanitized; defensive belt-and-braces
                        report["warnings"].append("unsafe_asset_target_skipped")
                    else:
                        os.makedirs(asset_root, exist_ok=True)
                        with open(full, "wb") as f:
                            f.write(png)
                        wrote = True
                        rendered += 1
            if not wrote and role == "answer_context":
                # don't DECLARE a missing answer-side asset — it would fail-close an otherwise-valid
                # question whose own figure rendered fine (the text `answer` already covers it).
                report["warnings"].append("answer_image_unavailable: %s (p.%d)" % (it["id"], page))
                continue
            assets.append({"path": rel_path, "role": role, "type": "page_image",
                           "caption": "%s p.%d (%s)" % (file, page, role)})
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
        it["assets"] = assets
    report["pages_rendered"] = rendered

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
            report["ai_review"].append({
                "kind": "type_defaulted", "file": "references/quiz_bank.json",
                "action": "有 %d 道题按主观题默认定型（启发式判不准绝不硬猜）。请 AI 抽查这些题：若"
                          "实为判断/编程/选择题（选项在图里）等，改写 type（合法值 choice/subjective/"
                          "diagram/fill_blank/true_false/code）并补 options；同时抽查已判为 "
                          "choice/fill_blank 的题是否属实。" % _typed["subjective"]})
    # D5: wiki 配图——含图/表标题（Figure N / Table N / 图N / 表N）的讲义页渲染成 PNG，
    # 注入章节末尾「本章图示页」区；渲染不可用时警告降级（纯文字 wiki 照常完整）
    _WIKI_CAP_RE = re.compile(
        r"(?m)^\s*(?:(?:Figure|Fig\.?|Table)\s*\d+|[图表]\s*[\d一二三四五六七八九十]+)")
    wiki_fig_assets = {}
    _cap_pages = [(pg["file"], pg["page"]) for pg in wiki_pages
                  if pg.get("_pdf") and _WIKI_CAP_RE.search(pg.get("text") or "")]   # cap 只数可渲染页
    if _cap_pages and can_write:
        if len(_cap_pages) > 30:
            report["warnings"].append("wiki_figures_capped: 图示页 %d 张只渲染前 30 张（控制体积）——"
                                      "未渲染页仍在原 PDF，可让 AI 用多模态直接查看对应页，"
                                      "或按章节拆分材料分次重建" % len(_cap_pages))
            _cap_pages = _cap_pages[:30]
        for _f, _p in _cap_pages:
            _pdf = page_pdf.get((_f, _p))
            if _pdf is None:
                continue
            try:
                _png = backend.render_page_png(_pdf, _p - 1)
            except Exception as e:
                report["skipped"].append({"file": _f, "why": "wiki 图示页渲染失败 p.%d: %s" % (_p, e)})
                continue
            if not _png:
                continue
            _name = _safe_asset_name(_f, _p, "wiki%08x" % (zlib.crc32(_f.encode("utf-8")) & 0xffffffff),
                                      "_fig")   # 源路径 CRC 防 a/b.pdf 与 a_b.pdf 同名互撞
            _full = os.path.join(asset_root, _name)
            if not _under(asset_root, _full):
                report["warnings"].append("unsafe_asset_target_skipped")
                continue
            os.makedirs(asset_root, exist_ok=True)
            with open(_full, "wb") as _fh:
                _fh.write(_png)
            rendered += 1
            wiki_fig_assets[(_f, _p)] = "../assets/" + _name
        report["pages_rendered"] = rendered
    elif _cap_pages and want_render:
        report["warnings"].append(
            "wiki_figures_skipped: 检测到 %d 个图/表标题页但渲染不可用（缺后端或 --asset-root）——"
            "wiki 纯文字仍完整；需要配图请补齐后重建" % len(_cap_pages))

    _ch_notes = []
    sections = group_sections(wiki_pages, _ch_notes)
    if wiki_fig_assets:
        for sec in sections:
            gal = ["![%s 第 %d 页图示](%s)" % (f0, p0, wiki_fig_assets[(f0, p0)])
                   for (f0, p0) in sec.get("page_keys", []) if (f0, p0) in wiki_fig_assets]
            if gal:
                sec["text_blocks"].append("### 本章图示页（构建时自动渲染）\n\n" + "\n\n".join(gal))
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
    raw_input = build_raw_input(course, sections, lecture_items, homework_items)
    return 0, raw_input, report


def main(argv=None, backend=None):
    # reconfigure BEFORE parse_args so argparse's Chinese --help text prints on Windows consoles
    # (cp1252) without a UnicodeEncodeError that would make `--help` exit non-zero.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = build_arg_parser().parse_args(argv)
    code, raw_input, report = run(args, backend=backend)
    if code != 0:
        sys.stderr.write((raw_input or {}).get("error", "失败") + "\n")
        if report is not None:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            if report.get("ai_review"):
                # 失败退出同样要留接管清单——exit 4（全是扫描件）正是最需要 AI 接管的场景
                mp = os.path.join(os.path.dirname(os.path.abspath(args.report)), "ai_review_manifest.json")
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump({"note": "程序无法处理/不确定的材料清单——AI 必须逐条处理，绝不静默略过",
                               "entries": report["ai_review"]}, f, ensure_ascii=False, indent=2)
                print("[!] AI 接管清单：%d 条未能自动处理的材料 → %s（请逐条处理）"
                      % (len(report["ai_review"]), mp))
        return code
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(raw_input, f, ensure_ascii=False, indent=2)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    manifest_path = os.path.join(os.path.dirname(os.path.abspath(args.report)), "ai_review_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"note": "程序无法处理/不确定的材料清单——AI 必须逐条处理，绝不静默略过",
                   "entries": report.get("ai_review", [])}, f, ensure_ascii=False, indent=2)
    if report.get("ai_review"):
        print("[!] AI 接管清单：%d 条未能自动处理的材料 → %s（请逐条处理）"
              % (len(report["ai_review"]), manifest_path))
    print("[+] raw_input: %s（%d 阶段 / %d 题，其中讲义题 %d）"
          % (args.out, len(raw_input["phases"]), len(raw_input["quiz_bank"]),
             report["examples_detected"] + report["quizzes_detected"]))
    print("[+] report: %s（后端 %s，渲染 %d 页，警告 %d）"
          % (args.report, report["backend"], report["pages_rendered"], len(report["warnings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
