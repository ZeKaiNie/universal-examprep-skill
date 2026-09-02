# -*- coding: utf-8 -*-
"""Command line for Exam Cram Coach.

Every command prints short plain text that an agent can paste into its reply, and ends
with the command to run next, so even a small model never has to remember the CLI.
"""
import argparse
import json
import os
import re
import sys
import time

from . import __version__, chapters as chmod, extract, figures as figmod, index as idx, questions as qmod, state as st
from .text import is_mostly_cjk, pack, shorten

CHAPTERS_FILE = "chapters.json"
BANK_FILE = "quiz_bank.json"
FIGURES_FILE = "figures.json"
POINTER = os.path.join(os.path.expanduser("~"), ".exam-cram-coach", "last_workspace")
DEFAULT_SLICE = {"zh": 3000, "en": 5000}

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------ wording

_T = {
    "no_ws": ("找不到学习工作区。先运行：python coach.py setup <材料文件夹>",
              "No study workspace found. Run: python coach.py setup <materials folder>"),
    "setup_done": ("✅ 工作区已建好", "✅ Workspace ready"),
    "files": ("文件", "Files"),
    "chapters": ("章节", "Chapters"),
    "questions": ("题目", "Questions"),
    "with_answers": ("有参考答案", "with reference answers"),
    "figures_count": ("配图", "Figures"),
    "scan_note": ("页扫描/手写页已跳过（不当作题面或答案）", "scanned/handwritten pages skipped (never used as question or answer text)"),
    "warnings": ("⚠️ 注意", "⚠️ Notes"),
    "pdf_missing": ("个 PDF 无法读取文本：请运行 `pip install pypdfium2` 后重新 setup",
                    "PDF file(s) could not be read: run `pip install pypdfium2` and setup again"),
    "no_pdfium": ("未安装 pypdfium2：PDF 里的图无法裁剪。运行 `pip install pypdfium2` 后重新 setup 即可自动配图",
                  "pypdfium2 is not installed: figures inside PDFs cannot be cropped. Run `pip install pypdfium2` and setup again"),
    "no_text": ("没有可提取文本（扫描件/纯图片）。讲这一章时请直接打开文件查看",
                "has no extractable text (scan/pure images). Open the file directly when teaching it"),
    "read_error": ("读取失败", "could not be read"),
    "next_steps": ("下一步", "Next"),
    "course": ("课程", "Course"),
    "exam_in": ("距考试", "Exam in"),
    "days": ("天", "day(s)"),
    "lang": ("语言", "Language"),
    "progress": ("进度", "Progress"),
    "current": ("当前", "Current"),
    "part": ("段", "part"),
    "mistakes": ("错题", "Mistakes"),
    "open": ("待复习", "open"),
    "confusions": ("疑难点", "Confusions"),
    "all_done": ("🎉 所有章节都讲完了。可以复习错题（python coach.py mistakes）或生成小抄（python coach.py cheatsheet）。",
                 "🎉 All chapters covered. Review mistakes (python coach.py mistakes) or build the cheat sheet (python coach.py cheatsheet)."),
    "ch_end": ("本章正文已讲完。", "End of this chapter's text."),
    "ch_examples": ("本章相关题目", "Questions for this chapter"),
    "no_examples": ("材料里没有这一章的题目；如需练习，只能出 AI 自编题并注明 ⚠️ AI 生成。",
                    "The materials contain no questions for this chapter; any practice question must be labelled ⚠️ AI-generated."),
    "answer_yes": ("有参考答案", "reference answer available"),
    "answer_no": ("无参考答案", "no reference answer"),
    "src": ("来源", "Source"),
    "figures": ("本章配图文件（请直接查看）", "Figure files for this chapter (open them directly)"),
    "slice_figs": ("🖼 本段配图（讲到对应内容时展示给学生）", "🖼 Figures in this slice (show them when you reach that content)"),
    "q_fig": ("🖼 题面图（出题前先展示）", "🖼 Question figure (show it before asking)"),
    "a_fig": ("🖼 答案图（讲解答案时展示）", "🖼 Answer figure (show it while explaining the answer)"),
    "no_text_ch": ("这一章没有可提取的文本。请直接打开下面的文件逐页阅读后讲解：",
                   "This chapter has no extractable text. Open these files and read them page by page:"),
    "quiz_head": ("测验", "Quiz"),
    "no_quiz": ("没有可用的题目。", "No usable questions."),
    "ref_answer": ("参考答案", "Reference answer"),
    "no_ref": ("材料里没有这道题的答案。你的答案必须标注 ⚠️ AI 生成答案，非老师/教材提供。",
               "The materials do not contain an answer. Label yours: ⚠️ AI-generated answer — not from your teacher or textbook."),
    "stmt_missing": ("题干不在材料里（教材题号）。请根据参考答案中复述的已知条件说明题意，并标注 🟡。",
                     "The problem statement is not in the materials (textbook number). Restate the givens from the reference answer and label them 🟡."),
    "givens": ("已知条件（摘自参考答案开头，可复述给学生）", "Givens (from the start of the reference answer; safe to restate)"),
    "recorded": ("已记录", "Recorded"),
    "unknown_q": ("找不到题目", "Unknown question id"),
    "unknown_ch": ("没有这一章", "No such chapter"),
    "done_ok": ("已标记完成", "Marked done"),
    "verified": ("已验证（本章至少答对 1 道材料题）", "verified (at least one material question answered right)"),
    "covered": ("已讲完但未验证（没有答对本章的材料题）", "covered but unverified (no material question answered right)"),
    "note_ok": ("已记入笔记", "Note saved"),
    "no_mistakes": ("没有待复习的错题。", "No open mistakes."),
    "cheatsheet_ok": ("小抄已写入", "Cheat sheet written to"),
    "search_none": ("材料中没有找到相关内容。请告诉学生「资料里没有这部分」，不要编造。",
                    "Nothing relevant found in the materials. Tell the student the materials do not cover this; do not invent."),
    "hint_next": ("继续讲：python coach.py next", "Continue: python coach.py next"),
    "hint_quiz": ("测验：python coach.py quiz", "Quiz: python coach.py quiz"),
    "hint_done": ("讲完本章：python coach.py done", "Finish chapter: python coach.py done"),
    "hint_ask": ("查资料：python coach.py ask \"关键词\"", "Look up: python coach.py ask \"keywords\""),
    "hint_check": ("看答案：python coach.py check <题号>   记录：python coach.py answer <题号> right|wrong|skip",
                   "Answer key: python coach.py check <id>   Record: python coach.py answer <id> right|wrong|skip"),
    "hint_figure": ("整页/局部截图：python coach.py figure <文件> <页码> [--crop x0,y0,x1,y1]",
                    "Page or region shot: python coach.py figure <file> <page> [--crop x0,y0,x1,y1]"),
    "goto_ok": ("已切换到第", "Switched to chapter"),
    "material_label": ("🟢 以下为资料原文（讲解时请注明出处）", "🟢 Material text follows (cite the source when teaching)"),
    "guessed": ("章节为自动推测", "chapter guessed automatically"),
    "figure_saved": ("已保存", "Saved"),
    "no_figures": ("没有找到配图。", "No figures found."),
    "unknown_file": ("材料里没有这个文件", "No such file in the materials"),
}


class W(object):
    """Wording in the workspace language."""

    def __init__(self, lang):
        self.zh = lang == "zh"

    def __call__(self, key):
        return _T[key][0 if self.zh else 1]


# ------------------------------------------------------------------ workspace io

def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_workspace(explicit):
    for cand in (explicit, os.environ.get("EXAM_CRAM_WORKSPACE")):
        if cand and os.path.exists(st.path(cand)):
            return os.path.abspath(cand)
    if os.path.exists(st.path("exam-cram")):
        return os.path.abspath("exam-cram")
    if os.path.exists(POINTER):
        cand = open(POINTER, encoding="utf-8").read().strip()
        if cand and os.path.exists(st.path(cand)):
            return cand
    return None


def remember_workspace(ws):
    try:
        os.makedirs(os.path.dirname(POINTER), exist_ok=True)
        with open(POINTER, "w", encoding="utf-8") as fh:
            fh.write(ws)
    except OSError:
        pass


def load_ws(args):
    ws = resolve_workspace(args.workspace)
    if ws is None:
        sys.stderr.write(_T["no_ws"][0] + "\n" + _T["no_ws"][1] + "\n")
        sys.exit(2)
    state = st.load(ws)
    return ws, state, W(state["language"])


def load_chapters(ws):
    return [chmod.Chapter.from_dict(d) for d in _read_json(os.path.join(ws, CHAPTERS_FILE), [])]


def load_bank(ws):
    return _read_json(os.path.join(ws, BANK_FILE), [])


def load_figures(ws):
    return _read_json(os.path.join(ws, FIGURES_FILE), [])


def chapter_parts(ch, slice_chars):
    """Stable split of a chapter into teachable pieces: [[(anchor, text), ...], ...]."""
    segments = []
    for rel, page, text in ch.blocks:
        for piece in pack(text, slice_chars):
            segments.append(("%s p.%s" % (rel, page), piece))
    parts, cur, size = [], [], 0
    for seg in segments:
        if cur and size + len(seg[1]) > slice_chars:
            parts.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += len(seg[1])
    if cur:
        parts.append(cur)
    return parts


def render_part(part):
    out, last_anchor = [], None
    for anchor, text in part:
        if anchor != last_anchor:
            out.append("[%s]" % anchor)
            last_anchor = anchor
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip()


def part_pages(part):
    pages = []
    for anchor, _ in part:
        m = re.match(r"(.+) p\.(\d+)$", anchor)
        if m and (m.group(1), int(m.group(2))) not in pages:
            pages.append((m.group(1), int(m.group(2))))
    return pages


def figures_for_pages(figs, pages):
    wanted = set(pages)
    return [f for f in figs if f.get("kind") == "figure" and (f["file"], f["page"]) in wanted]


# ------------------------------------------------------------------ question crops

def _segments_for(infos, heads, i):
    """Page regions (page, top, bottom) covered by block i, given all block heads in order."""
    page, head = heads[i][0], heads[i][1]
    info = infos.get(page)
    top = figmod.find_text_top(info, head) if info else None
    if top is None:
        return []
    nxt = heads[i + 1] if i + 1 < len(heads) else None
    if nxt and nxt[0] == page:
        nt = figmod.find_text_top(infos[page], nxt[1])
        return [(page, top, nt if nt is not None else 24)]
    segs = [(page, top, 24)]
    if info.scan:
        return segs  # pages after a scanned list are the student's own sheets, never the question
    last = nxt[0] if nxt else min(page + 3, max(infos))
    for p in range(page + 1, last):
        if p in infos:
            segs.append((p, infos[p].height - 24, 24))
    if nxt and nxt[0] in infos:
        nt = figmod.find_text_top(infos[nxt[0]], nxt[1])
        if nt is not None:
            segs.append((nxt[0], infos[nxt[0]].height - 24, nt))
    return segs


def attach_question_figures(materials, ws, bank, pdf_infos, sources):
    """Crop the printed region of each question/answer that contains a picture."""
    by_rel = {s.rel: s for s in sources}
    head_cache = {}

    def heads_of(rel):
        if rel not in head_cache:
            head_cache[rel] = qmod.heads(by_rel[rel]) if rel in by_rel else []
        return head_cache[rel]

    records = []
    for it in bank:
        for side, key in (("source", "figures"), ("answer_source", "answer_figures")):
            ref = it.get(side)
            it[key] = []
            if not ref or ref["file"] not in pdf_infos:
                continue
            heads = heads_of(ref["file"])
            pos = next((k for k, h in enumerate(heads) if h[0] == ref["page"] and h[1] == ref.get("head")), None)
            if pos is None:
                continue
            segs = _segments_for(pdf_infos[ref["file"]], heads, pos)
            label = it["id"] if side == "source" else it["id"] + "_ans"
            paths = figmod.crop_blocks(materials, ws, ref["file"], pdf_infos[ref["file"]], segs, figmod.safe_stem(ref["file"]), label)
            it[key] = paths
            for p in paths:
                records.append({"file": ref["file"], "page": ref["page"], "path": p, "kind": "question" if side == "source" else "answer", "qid": it["id"], "box": None})
    return records


# ------------------------------------------------------------------ commands

def cmd_setup(args):
    t0 = time.time()
    materials = os.path.abspath(args.materials)
    if not os.path.isdir(materials):
        sys.stderr.write("materials folder not found: %s\n" % materials)
        return 2
    ws = os.path.abspath(args.workspace or os.path.join(materials, "exam-cram"))
    os.makedirs(os.path.join(ws, "chapters"), exist_ok=True)

    sources = extract.scan(materials)
    fig_records, scan_pages, pdf_infos = figmod.extract_figures(materials, sources, ws)
    n_scan = 0
    for src in sources:
        scans = scan_pages.get(src.rel, set())
        for p in src.pages:
            p.scan = p.number in scans
        n_scan += len(scans)
    chapters = chmod.build_chapters(sources)
    all_text = "\n".join(c.text for c in chapters)
    lang = args.lang or ("zh" if is_mostly_cjk(all_text) else "en")
    w = W(lang)
    slice_chars = args.slice or DEFAULT_SLICE[lang]
    bank = qmod.extract_questions(sources, chapters)
    fig_records += attach_question_figures(materials, ws, bank, pdf_infos, sources)
    chunks = idx.build_chunks(chapters)

    # readable chapter files (short names: deep Windows paths hit the 260-char limit)
    for old in os.listdir(os.path.join(ws, "chapters")):
        if old.startswith("ch") and old.endswith(".md"):
            os.remove(os.path.join(ws, "chapters", old))
    for ch in chapters:
        safe = re.sub(r"[^\w\-]+", "_", ch.title)[:24].strip("_")
        body = "# %d. %s\n\n" % (ch.number, ch.title)
        body += "\n\n".join(render_part(p) for p in chapter_parts(ch, slice_chars)) + "\n"
        for fname in ("ch%02d_%s.md" % (ch.number, safe) if safe else "", "ch%02d.md" % ch.number):
            if not fname:
                continue
            try:
                with open(os.path.join(ws, "chapters", fname), "w", encoding="utf-8") as fh:
                    fh.write(body)
                break
            except OSError:
                continue
    _write_json(os.path.join(ws, CHAPTERS_FILE), [c.to_dict() for c in chapters])
    _write_json(os.path.join(ws, BANK_FILE), bank)
    _write_json(os.path.join(ws, FIGURES_FILE), fig_records)
    idx.save_index(ws, chunks)

    course = args.name or os.path.basename(materials.rstrip("\\/")) or "course"
    old = st.load(ws)
    ch_summaries = []
    for ch in chapters:
        ch_summaries.append({
            "n": ch.number, "title": ch.title, "sources": ch.sources,
            "parts": len(chapter_parts(ch, slice_chars)),
            "questions": sum(1 for q in bank if q["chapter"] == ch.number),
        })
    state = st.new_state(course, lang, args.days, materials, ch_summaries, slice_chars)
    if old and old.get("version") == 5 and not args.fresh:
        # keep progress on re-setup
        keep = {c["n"]: c for c in old["chapters"]}
        for c in state["chapters"]:
            if c["n"] in keep:
                c["status"] = keep[c["n"]]["status"]
                c["part"] = min(keep[c["n"]]["part"], c["parts"])
        state["history"], state["mistakes"], state["notes"] = old["history"], old["mistakes"], old["notes"]
        state["current"] = old["current"] if st.chapter(state, old["current"]) else state["current"]
        state["created"] = old["created"]
        if args.days is None:
            state["exam_days"] = old.get("exam_days")
    if args.start:
        state["current"] = args.start
    st.save(ws, state)
    remember_workspace(ws)

    # report
    kinds = {}
    for s in sources:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    print("%s: %s  (%.1fs)" % (w("setup_done"), ws, time.time() - t0))
    print("%s: %s" % (w("files"), ", ".join("%s×%d" % kv for kv in sorted(kinds.items())) or "0"))
    print("%s (%d):" % (w("chapters"), len(chapters)))
    for c in state["chapters"]:
        print("  %2d. %s  [%s; %d %s]" % (c["n"], c["title"], ", ".join(c["sources"]), c["questions"], w("questions")))
    answered = sum(1 for q in bank if q["answer"])
    print("%s: %d (%d %s)" % (w("questions"), len(bank), answered, w("with_answers")))
    n_fig = sum(1 for f in fig_records if f["kind"] == "figure")
    n_q = sum(1 for f in fig_records if f["kind"] in ("question", "answer"))
    print("%s: %d + %d question/answer crops%s" % (w("figures_count"), n_fig, n_q, ("; %d %s" % (n_scan, w("scan_note"))) if n_scan else ""))
    notes = []
    pdf_missing = [s.rel for s in sources if s.error == "pdf_support_missing"]
    if pdf_missing:
        notes.append("%d %s: %s" % (len(pdf_missing), w("pdf_missing"), ", ".join(pdf_missing)))
    elif any(s.rel.lower().endswith(".pdf") for s in sources) and not figmod.has_pdfium():
        notes.append(w("no_pdfium"))
    for s in sources:
        if s.error and s.error != "pdf_support_missing":
            notes.append("%s %s (%s)" % (s.rel, w("read_error"), s.error))
        elif "no_text" in s.warnings:
            notes.append("%s %s" % (s.rel, w("no_text")))
        for wn in s.warnings:
            if wn.startswith("figures_failed"):
                notes.append("%s: %s" % (s.rel, wn))
    if notes:
        print(w("warnings") + ":")
        for n in notes:
            print("  - " + n)
    print(w("next_steps") + ": python coach.py next")
    return 0


def cmd_status(args):
    ws, state, w = load_ws(args)
    done = sum(1 for c in state["chapters"] if c["status"] in ("done", "verified"))
    total = len(state["chapters"])
    cur = st.chapter(state)
    line = ["%s: %s" % (w("course"), state["course"]), "%s: %s" % (w("lang"), state["language"])]
    if state.get("exam_days") is not None:
        line.append("%s %s %s" % (w("exam_in"), state["exam_days"], w("days")))
    print(" | ".join(line))
    print("%s: %s %d/%d" % (w("progress"), st.bar(done, total), done, total))
    for c in state["chapters"]:
        mark = "→" if cur and c["n"] == cur["n"] else " "
        right, asked = st.chapter_results(state, c["n"])
        print(" %s %2d. %-40s %-9s %s %d/%d  quiz %d/%d" % (
            mark, c["n"], shorten(c["title"], 40), c["status"], w("part"), min(c["part"], c["parts"]), c["parts"], right, asked))
    om = st.open_mistakes(state)
    conf = [n for n in state["notes"] if n["type"] == "confusion"]
    print("%s: %d %s | %s: %d" % (w("mistakes"), len(om), w("open"), w("confusions"), len(conf)))
    print("%s: %s" % (w("next_steps"), "python coach.py next"))
    return 0


def _advance_to_next_todo(state):
    for c in state["chapters"]:
        if c["status"] == "todo":
            state["current"] = c["n"]
            return c
    return None


def _print_examples(w, ws, bank, n, limit=8):
    items = [q for q in bank if q["chapter"] == n]
    print("--- %s (%d) ---" % (w("ch_examples"), len(items)))
    if not items:
        print(w("no_examples"))
        return
    for q in items[:limit]:
        flag = w("answer_yes") if q["answer"] else w("answer_no")
        guess = " (%s)" % w("guessed") if q.get("chapter_guessed") else ""
        fig = " 🖼" if q.get("figures") or q.get("answer_figures") else ""
        print("[%s] %s | %s p.%s | %s%s%s" % (q["id"], shorten(q["question"] or q["source"].get("head", ""), 100), q["source"]["file"], q["source"]["page"], flag, guess, fig))
    if len(items) > limit:
        print("… +%d" % (len(items) - limit))


def _print_figs(ws, label, paths):
    if not paths:
        return
    print(label + ":")
    for p in paths:
        print("  " + os.path.join(ws, p.replace("/", os.sep)))


def cmd_next(args):
    ws, state, w = load_ws(args)
    cur = st.chapter(state)
    if cur is None or cur["status"] != "todo":
        cur = _advance_to_next_todo(state)
    if cur is None:
        print(w("all_done"))
        st.save(ws, state)
        return 0
    chs = {c.number: c for c in load_chapters(ws)}
    ch = chs[cur["n"]]
    slice_chars = args.chars or state["slice_chars"]
    parts = chapter_parts(ch, slice_chars)
    cur["parts"] = len(parts)
    if args.back:
        cur["part"] = max(0, cur["part"] - 2)
    elif args.repeat:
        cur["part"] = max(0, cur["part"] - 1)
    k = cur["part"]
    head = ("第 %d 章：%s" if w.zh else "Chapter %d: %s") % (cur["n"], cur["title"])
    bank = load_bank(ws)
    if not parts:
        print("=== %s ===" % head)
        print(w("no_text_ch"))
        for rel in cur["sources"]:
            print("  - " + os.path.join(state["materials"], rel))
        if ch.figures:
            print(w("figures") + ":")
            for f in ch.figures:
                print("  - " + os.path.join(state["materials"], f))
        cur["part"] = 0
        _print_examples(w, ws, bank, cur["n"])
        print("%s | %s" % (w("hint_quiz"), w("hint_done")))
        st.save(ws, state)
        return 0
    if k >= len(parts):
        print("=== %s (%s %d/%d) ===" % (head, w("part"), len(parts), len(parts)))
        print(w("ch_end"))
        _print_examples(w, ws, bank, cur["n"])
        print("%s | %s" % (w("hint_quiz"), w("hint_done")))
        st.save(ws, state)
        return 0
    print("=== %s (%s %d/%d) ===" % (head, w("part"), k + 1, len(parts)))
    print(w("material_label"))
    print(render_part(parts[k]))
    figs = figures_for_pages(load_figures(ws), part_pages(parts[k]))
    if figs:
        print("")
        print(w("slice_figs") + ":")
        for f in figs:
            print("  [%s p.%s] %s" % (f["file"], f["page"], os.path.join(ws, f["path"].replace("/", os.sep))))
    if k == 0 and ch.figures:
        print("\n" + w("figures") + ":")
        for f in ch.figures:
            print("  - " + os.path.join(state["materials"], f))
    cur["part"] = k + 1
    if cur["part"] >= len(parts):
        print("")
        _print_examples(w, ws, bank, cur["n"])
        print("%s | %s" % (w("hint_quiz"), w("hint_done")))
    else:
        print("\n%s | %s" % (w("hint_next"), w("hint_ask")))
    st.save(ws, state)
    return 0


def cmd_chapter(args):
    ws, state, w = load_ws(args)
    chs = {c.number: c for c in load_chapters(ws)}
    if args.n not in chs:
        print(w("unknown_ch"))
        return 2
    parts = chapter_parts(chs[args.n], args.chars or state["slice_chars"])
    if args.part is None:
        print("=== %d. %s (%d %s) ===" % (args.n, chs[args.n].title, len(parts), w("part")))
        for i, p in enumerate(parts, 1):
            print(" %d/%d: %s" % (i, len(parts), shorten(p[0][1], 70)))
        return 0
    k = max(1, min(args.part, len(parts)))
    print("=== %d. %s (%s %d/%d) ===" % (args.n, chs[args.n].title, w("part"), k, len(parts)))
    print(render_part(parts[k - 1]))
    figs = figures_for_pages(load_figures(ws), part_pages(parts[k - 1]))
    if figs:
        print("\n" + w("slice_figs") + ":")
        for f in figs:
            print("  [%s p.%s] %s" % (f["file"], f["page"], os.path.join(ws, f["path"].replace("/", os.sep))))
    return 0


def cmd_goto(args):
    ws, state, w = load_ws(args)
    c = st.chapter(state, args.n)
    if c is None:
        print(w("unknown_ch"))
        return 2
    state["current"] = args.n
    if c["status"] != "todo":
        c["status"] = "todo"
    if args.restart:
        c["part"] = 0
    st.save(ws, state)
    print("%s %d: %s" % (w("goto_ok"), args.n, c["title"]))
    print(w("hint_next"))
    return 0


def cmd_ask(args):
    ws, state, w = load_ws(args)
    hits = idx.search(idx.load_index(ws), args.query, k=args.k, chapter=args.chapter)
    if not hits:
        print(w("search_none"))
        return 4
    figs = load_figures(ws)
    for c, score in hits:
        print("🟢 [ch%s | %s p.%s | score %.1f]" % (c["chapter"], c["file"], c["page"], score))
        print(shorten(c["text"], args.chars))
        for f in figures_for_pages(figs, [(c["file"], c["page"])]):
            print("  🖼 " + os.path.join(ws, f["path"].replace("/", os.sep)))
        print("")
    return 0


def _pick_quiz(state, bank, n, count, include_all):
    pool = [q for q in bank if include_all or q["chapter"] == n]
    if not pool:
        return []
    asked = {}
    for h in state["history"]:
        asked[h["qid"]] = h["result"]
    open_ids = {m["qid"] for m in st.open_mistakes(state)}

    def rank(q):
        if q["id"] in open_ids:
            return (0, q["id"])
        if q["id"] not in asked:
            return (1, q["id"])
        return (2, q["id"])

    pool.sort(key=rank)
    return pool[:count]


def _givens(answer):
    """The setup a solution restates before it starts solving: text before the first sub-part or 'Solution'."""
    if not answer:
        return None
    cut = re.search(r"(?:^|\n)\s*(?:\([a-h1-9]\)|[（(][a-h1-9][)）]|solution\s*[:：]|解[:：])", answer, re.I)
    head = answer[: cut.start()] if cut else answer
    head = " ".join(head.split())
    if not head:
        return None  # the solution starts solving at once: nothing safe to restate
    return (head[:300] + "…") if len(head) > 300 else head


def _print_question(q, w, ws, with_answer=False):
    guess = "  (%s)" % w("guessed") if q.get("chapter_guessed") else ""
    pts = " [%s]" % q["points"] if q.get("points") else ""
    print("[%s] ch%s %s%s | %s: %s p.%s%s" % (q["id"], q["chapter"], q["type"], pts, w("src"), q["source"]["file"], q["source"]["page"], guess))
    label_only = re.fullmatch(r"(?:problem|exercise|question|q)\s*[\d.]+\s*(?:\(.*\))?", q["question"] or "", re.I | re.S)
    if q["question"] and not label_only:
        print(q["question"])
    else:
        print(q["question"] or q["source"].get("head", ""))
        print(w("stmt_missing"))
        givens = _givens(q["answer"])
        if givens and not with_answer:
            print("🟡 %s: %s" % (w("givens"), givens))
    for o in q.get("options") or []:
        print("  " + o)
    _print_figs(ws, w("q_fig"), q.get("figures"))
    if with_answer:
        print("--- %s ---" % w("ref_answer"))
        if q["answer"]:
            src = q.get("answer_source") or q["source"]
            print("🟢 (%s p.%s)" % (src["file"], src["page"]))
            print(q["answer"])
            _print_figs(ws, w("a_fig"), q.get("answer_figures"))
        else:
            print(w("no_ref"))


def cmd_quiz(args):
    ws, state, w = load_ws(args)
    n = args.chapter or state["current"]
    picked = _pick_quiz(state, load_bank(ws), n, args.n, args.all)
    print("=== %s: ch%s ===" % (w("quiz_head"), "*" if args.all else n))
    if not picked:
        print(w("no_quiz"))
        print(w("no_examples"))
        return 3
    for q in picked:
        _print_question(q, w, ws)
        print("")
    print(w("hint_check"))
    return 0


def _find_q(bank, qid):
    return next((q for q in bank if q["id"] == qid), None)


def cmd_check(args):
    ws, state, w = load_ws(args)
    q = _find_q(load_bank(ws), args.qid)
    if q is None:
        print("%s: %s" % (w("unknown_q"), args.qid))
        return 2
    _print_question(q, w, ws, with_answer=True)
    return 0


def cmd_answer(args):
    ws, state, w = load_ws(args)
    q = _find_q(load_bank(ws), args.qid)
    if q is None:
        print("%s: %s" % (w("unknown_q"), args.qid))
        return 2
    st.record_result(state, args.qid, q["chapter"], args.result, args.note or "")
    st.save(ws, state)
    right, asked = st.chapter_results(state, q["chapter"])
    print("%s: %s %s | ch%s quiz %d/%d | %s %d" % (w("recorded"), args.qid, args.result, q["chapter"], right, asked, w("mistakes"), len(st.open_mistakes(state))))
    return 0


def cmd_done(args):
    ws, state, w = load_ws(args)
    c = st.chapter(state, args.chapter) if args.chapter else st.chapter(state)
    if c is None:
        print(w("unknown_ch"))
        return 2
    right, _ = st.chapter_results(state, c["n"])
    c["status"] = "verified" if right > 0 else "done"
    c["part"] = c["parts"]
    nxt = _advance_to_next_todo(state)
    st.save(ws, state)
    print("%s: ch%d %s → %s" % (w("done_ok"), c["n"], c["title"], w("verified") if right > 0 else w("covered")))
    if nxt:
        print("%s: ch%d %s" % (w("current"), nxt["n"], nxt["title"]))
        print(w("hint_next"))
    else:
        print(w("all_done"))
    return 0


def cmd_note(args):
    ws, state, w = load_ws(args)
    n = args.chapter or state["current"]
    state["notes"].append({"chapter": n, "type": args.type, "text": args.text.strip(), "ts": st.now()})
    st.save(ws, state)
    print("%s (ch%s, %s)" % (w("note_ok"), n, args.type))
    return 0


def cmd_mistakes(args):
    ws, state, w = load_ws(args)
    bank = load_bank(ws)
    om = st.open_mistakes(state, args.chapter)
    if not om:
        print(w("no_mistakes"))
        return 0
    for m in om:
        q = _find_q(bank, m["qid"])
        print("=== %s ×%d %s ===" % (m["qid"], m["count"], m.get("note", "")))
        if q:
            _print_question(q, w, ws, with_answer=args.answers)
        print("")
    print(w("hint_check"))
    return 0


def cmd_cheatsheet(args):
    ws, state, w = load_ws(args)
    bank = load_bank(ws)
    out = [("# %s 考前小抄" if w.zh else "# %s cheat sheet") % state["course"], ""]
    for c in state["chapters"]:
        notes = [n for n in state["notes"] if n["chapter"] == c["n"]]
        mist = [m for m in state["mistakes"] if m["chapter"] == c["n"]]
        if not notes and not mist:
            continue
        out.append("## %d. %s" % (c["n"], c["title"]))
        for n in notes:
            if n["type"] == "summary":
                out.append(n["text"])
                out.append("")
        conf = [n for n in notes if n["type"] == "confusion"]
        if conf:
            out.append("**%s**" % ("疑难点" if w.zh else "Confusions"))
            for n in conf:
                out.append("- " + n["text"])
            out.append("")
        if mist:
            out.append("**%s**" % ("错题" if w.zh else "Mistakes"))
            for m in mist:
                q = _find_q(bank, m["qid"])
                if not q:
                    continue
                out.append("- [%s] %s" % (m["qid"], shorten(q["question"] or q["source"].get("head", ""), 200)))
                for p in q.get("figures") or []:
                    out.append("  ![](%s)" % p)
                if q["answer"]:
                    src = q.get("answer_source") or q["source"]
                    out.append("  - 🟢 %s: %s (%s p.%s)" % (w("ref_answer"), shorten(q["answer"], 300), src["file"], src["page"]))
                if m.get("note"):
                    out.append("  - " + m["note"])
            out.append("")
    dest = args.out or os.path.join(ws, "cheatsheet.md")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print("%s: %s" % (w("cheatsheet_ok"), dest))
    return 0


def cmd_figures(args):
    ws, state, w = load_ws(args)
    figs = load_figures(ws)
    if args.chapter:
        chs = {c.number: c for c in load_chapters(ws)}
        if args.chapter not in chs:
            print(w("unknown_ch"))
            return 2
        pages = {(rel, page) for rel, page, _ in chs[args.chapter].blocks}
        figs = [f for f in figs if (f["file"], f["page"]) in pages]
    if args.file:
        figs = [f for f in figs if f["file"] == args.file]
    if args.page:
        figs = [f for f in figs if f["page"] == args.page]
    if not figs:
        print(w("no_figures"))
        print(w("hint_figure"))
        return 3
    for f in figs:
        extra = " (%s %s)" % (f["kind"], f.get("qid", "")) if f["kind"] != "figure" else ""
        print("[%s p.%s]%s %s" % (f["file"], f["page"], extra, os.path.join(ws, f["path"].replace("/", os.sep))))
    return 0


def cmd_figure(args):
    ws, state, w = load_ws(args)
    if not figmod.has_pdfium():
        print(w("no_pdfium"))
        return 2
    path = os.path.join(state["materials"], args.file)
    if not os.path.exists(path):
        print("%s: %s" % (w("unknown_file"), args.file))
        return 2
    import pypdfium2 as pdfium
    page = pdfium.PdfDocument(path)[args.page - 1]
    pw, ph = page.get_size()
    box = None
    tag = "page"
    if args.crop:
        x0, y0, x1, y1 = [float(v) for v in args.crop.split(",")]
        if max(x0, y0, x1, y1) <= 1.0:  # fractions of the page, top-left origin
            x0, x1, y0, y1 = x0 * pw, x1 * pw, y0 * ph, y1 * ph
        box = (min(x0, x1), ph - max(y0, y1), max(x0, x1), ph - min(y0, y1))
        tag = "crop_%d_%d_%d_%d" % (box[0], ph - box[3], box[2], ph - box[1])
    out_dir = os.path.join(ws, figmod.FIG_DIR)
    os.makedirs(out_dir, exist_ok=True)
    dest = args.out or os.path.join(out_dir, "%s_p%d_%s.png" % (figmod.safe_stem(args.file), args.page, tag))
    wpx, hpx = figmod.render_region(path, args.page, box, dest, scale=args.scale)
    print("%s: %s (%dx%d px)" % (w("figure_saved"), dest, wpx, hpx))
    return 0


def cmd_doctor(args):
    print("Exam Cram Coach %s | python %s" % (__version__, sys.version.split()[0]))
    backend = extract.pdf_backend()
    if backend == "pypdfium2":
        print("pypdfium2: ok (PDF text + figure cropping)")
    elif backend == "pypdf":
        print("pypdf: ok (PDF text). pypdfium2 missing → no figure cropping from PDFs: pip install pypdfium2")
    else:
        print("PDF support: missing → pip install pypdfium2   (DOCX/PPTX/MD/TXT work without it)")
    ws = resolve_workspace(args.workspace)
    print("workspace: %s" % (ws or "(none)"))
    return 0


HELP_ZH = """用法：python coach.py <命令> [--workspace 路径]
  setup <材料文件夹> [--lang zh|en] [--days N] [--name 课程名] [--start 章] [--slice 字数] [--fresh]
  status                 进度面板
  next [--repeat|--back] 打印当前章下一段资料原文 + 本段配图路径（讲课用）
  chapter N [--part K]   查看第 N 章（列出分段或打印第 K 段）
  goto N [--restart]     切换到第 N 章
  ask "关键词" [-k 5]    在材料里检索（用于回答学生提问）
  quiz [-n 3] [--chapter N] [--all]   抽题（错题优先），附题面图
  check <题号>           查看参考答案、出处与答案图
  answer <题号> right|wrong|skip [--note 说明]   记录作答
  done [--chapter N]     标记本章讲完，进入下一章
  note "内容" [--type summary|confusion|note] [--chapter N]   写笔记
  mistakes [--answers]   列出待复习错题
  cheatsheet [--out 文件] 由笔记+错题生成小抄
  figures [--chapter N] [--file F] [--page P]   列出已裁好的配图
  figure <文件> <页码> [--crop x0,y0,x1,y1] [--scale 2]   截整页或局部（坐标为 0-1 比例，左上角原点）
  doctor                 环境检查"""

HELP_EN = """Usage: python coach.py <command> [--workspace PATH]
  setup <materials folder> [--lang zh|en] [--days N] [--name COURSE] [--start N] [--slice CHARS] [--fresh]
  status                 progress panel
  next [--repeat|--back] print the next slice of the current chapter + its figure paths (teach from it)
  chapter N [--part K]   inspect chapter N (list parts or print part K)
  goto N [--restart]     switch to chapter N
  ask "keywords" [-k 5]  search the materials (to answer questions)
  quiz [-n 3] [--chapter N] [--all]   pick questions (open mistakes first), with question figures
  check <id>             show the reference answer, its source and answer figures
  answer <id> right|wrong|skip [--note TEXT]   record a result
  done [--chapter N]     mark the chapter finished and move on
  note "text" [--type summary|confusion|note] [--chapter N]   save a note
  mistakes [--answers]   list open mistakes
  cheatsheet [--out FILE] build a cheat sheet from notes + mistakes
  figures [--chapter N] [--file F] [--page P]   list cropped figures
  figure <file> <page> [--crop x0,y0,x1,y1] [--scale 2]   shoot a whole page or a region (0-1 fractions, top-left origin)
  doctor                 environment check"""


def cmd_help(args):
    ws = resolve_workspace(args.workspace)
    state = st.load(ws) if ws else None
    zh = (state or {}).get("language") == "zh"
    print(HELP_ZH if zh else HELP_EN)
    return 0


# ------------------------------------------------------------------ parser

def build_parser():
    p = argparse.ArgumentParser(prog="coach.py", description="Exam Cram Coach", add_help=True)
    p.add_argument("--workspace", "-w", help="study workspace (default: <materials>/exam-cram or the last one used)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("setup")
    s.add_argument("materials")
    s.add_argument("--lang", choices=["zh", "en"])
    s.add_argument("--days", type=int)
    s.add_argument("--name")
    s.add_argument("--start", type=int)
    s.add_argument("--slice", type=int, help="characters per teaching slice (smaller for small models)")
    s.add_argument("--fresh", action="store_true", help="discard previous progress")
    s.set_defaults(fn=cmd_setup)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    s = sub.add_parser("next")
    s.add_argument("--repeat", action="store_true")
    s.add_argument("--back", action="store_true")
    s.add_argument("--chars", type=int)
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("chapter")
    s.add_argument("n", type=int)
    s.add_argument("--part", type=int)
    s.add_argument("--chars", type=int)
    s.set_defaults(fn=cmd_chapter)

    s = sub.add_parser("goto")
    s.add_argument("n", type=int)
    s.add_argument("--restart", action="store_true")
    s.set_defaults(fn=cmd_goto)

    s = sub.add_parser("ask")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--chapter", type=int)
    s.add_argument("--chars", type=int, default=700)
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("quiz")
    s.add_argument("-n", type=int, default=3)
    s.add_argument("--chapter", type=int)
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_quiz)

    s = sub.add_parser("check")
    s.add_argument("qid")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("answer")
    s.add_argument("qid")
    s.add_argument("result", choices=["right", "wrong", "skip"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("done")
    s.add_argument("--chapter", type=int)
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("note")
    s.add_argument("text")
    s.add_argument("--type", choices=["summary", "confusion", "note"], default="note")
    s.add_argument("--chapter", type=int)
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("mistakes")
    s.add_argument("--chapter", type=int)
    s.add_argument("--answers", action="store_true")
    s.set_defaults(fn=cmd_mistakes)

    s = sub.add_parser("cheatsheet")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_cheatsheet)

    s = sub.add_parser("figures")
    s.add_argument("--chapter", type=int)
    s.add_argument("--file")
    s.add_argument("--page", type=int)
    s.set_defaults(fn=cmd_figures)

    s = sub.add_parser("figure")
    s.add_argument("file")
    s.add_argument("page", type=int)
    s.add_argument("--crop", help="x0,y0,x1,y1 as 0-1 fractions of the page (top-left origin) or PDF points")
    s.add_argument("--scale", type=float, default=2.0)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_figure)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("help").set_defaults(fn=cmd_help)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        return cmd_help(args)
    return args.fn(args)
