# -*- coding: utf-8 -*-
"""study_state.json: the one file that remembers progress. progress.md and notebook.md are views."""
import datetime as _dt
import json
import os

STATE_FILE = "study_state.json"
PROGRESS_FILE = "progress.md"
NOTEBOOK_FILE = "notebook.md"


def now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def new_state(course, language, days, materials, chapters, slice_chars):
    return {
        "version": 5,
        "course": course,
        "language": language,
        "exam_days": days,
        "materials": materials,
        "created": now(),
        "updated": now(),
        "slice_chars": slice_chars,
        "current": chapters[0]["n"] if chapters else None,
        "chapters": [
            {"n": c["n"], "title": c["title"], "status": "todo", "part": 0, "parts": c["parts"],
             "sources": c["sources"], "questions": c["questions"]}
            for c in chapters
        ],
        "history": [],   # {"qid","result","ts","chapter"}
        "mistakes": [],  # {"qid","chapter","note","status": open|fixed,"count"}
        "notes": [],     # {"chapter","type": summary|confusion|note,"text","ts"}
    }


def path(ws):
    return os.path.join(ws, STATE_FILE)


def load(ws):
    p = path(ws)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(p, text):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, p)


def save(ws, state):
    state["updated"] = now()
    _atomic_write(path(ws), json.dumps(state, ensure_ascii=False, indent=1))
    _atomic_write(os.path.join(ws, PROGRESS_FILE), render_progress(state))
    _atomic_write(os.path.join(ws, NOTEBOOK_FILE), render_notebook(state))


def chapter(state, n=None):
    n = state["current"] if n is None else n
    for c in state["chapters"]:
        if c["n"] == n:
            return c
    return None


def open_mistakes(state, n=None):
    return [m for m in state["mistakes"] if m["status"] == "open" and (n is None or m["chapter"] == n)]


def record_result(state, qid, chapter_n, result, note=""):
    state["history"].append({"qid": qid, "result": result, "ts": now(), "chapter": chapter_n})
    existing = next((m for m in state["mistakes"] if m["qid"] == qid), None)
    if result in ("wrong", "skip"):
        if existing:
            existing["count"] += 1
            existing["status"] = "open"
            if note:
                existing["note"] = note
        else:
            state["mistakes"].append({"qid": qid, "chapter": chapter_n, "note": note, "status": "open", "count": 1})
    elif result == "right" and existing and existing["status"] == "open":
        existing["status"] = "fixed"


def chapter_results(state, n):
    right = sum(1 for h in state["history"] if h["chapter"] == n and h["result"] == "right")
    asked = sum(1 for h in state["history"] if h["chapter"] == n)
    return right, asked


def bar(done, total, width=10):
    filled = int(round(width * done / total)) if total else 0
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def render_progress(state):
    zh = state["language"] == "zh"
    done = sum(1 for c in state["chapters"] if c["status"] in ("done", "verified"))
    total = len(state["chapters"])
    lines = ["# %s" % ("复习进度" if zh else "Study progress"), ""]
    lines.append("- %s: %s" % ("课程" if zh else "Course", state["course"]))
    lines.append("- %s: %s" % ("更新" if zh else "Updated", state["updated"]))
    lines.append("- %s: %s %d/%d" % ("章节" if zh else "Chapters", bar(done, total), done, total))
    lines.append("")
    lines.append("| # | %s | %s | %s |" % (("标题", "状态", "测验") if zh else ("Title", "Status", "Quiz")))
    lines.append("| --- | --- | --- | --- |")
    for c in state["chapters"]:
        right, asked = chapter_results(state, c["n"])
        mark = "→ " if c["n"] == state["current"] else ""
        lines.append("| %s%d | %s | %s | %d/%d |" % (mark, c["n"], c["title"], c["status"], right, asked))
    lines.append("")
    lines.append("## %s" % ("错题" if zh else "Mistakes"))
    om = [m for m in state["mistakes"] if m["status"] == "open"]
    if not om:
        lines.append("- (%s)" % ("暂无" if zh else "none"))
    for m in om:
        lines.append("- %s (ch%d, ×%d) %s" % (m["qid"], m["chapter"], m["count"], m.get("note", "")))
    lines.append("")
    conf = [n for n in state["notes"] if n["type"] == "confusion"]
    lines.append("## %s" % ("疑难点" if zh else "Confusions"))
    if not conf:
        lines.append("- (%s)" % ("暂无" if zh else "none"))
    for n in conf:
        lines.append("- ch%s: %s" % (n["chapter"], n["text"]))
    return "\n".join(lines) + "\n"


def render_notebook(state):
    zh = state["language"] == "zh"
    lines = ["# %s" % ("学习笔记" if zh else "Notebook"), ""]
    for c in state["chapters"]:
        notes = [n for n in state["notes"] if n["chapter"] == c["n"]]
        if not notes:
            continue
        lines.append("## %s %d: %s" % ("第" if zh else "Chapter", c["n"], c["title"]) if not zh
                     else "## 第 %d 章：%s" % (c["n"], c["title"]))
        for n in notes:
            lines.append("- [%s %s] %s" % (n["type"], n["ts"], n["text"]))
        lines.append("")
    return "\n".join(lines) + "\n"
