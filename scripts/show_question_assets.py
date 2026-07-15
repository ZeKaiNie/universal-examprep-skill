#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Print the EXACT prompt-side asset Markdown for one question (P0-V2 official tool).

The visual-first contract (P0-V1) says: a requires/maybe_requires_assets item must SHOW its
prompt-side image(s) before asking/explaining, with renderable relative-POSIX paths — and if that is
impossible the item must be skipped, fail-closed. This tool makes that step deterministic instead of
hand-written: it emits the Markdown lines to paste BEFORE the question, verifies the files actually
exist/are safe (same rules as validate_workspace), and refuses (exit 1) when the contract can't be met.
Answer-side assets are only printed with --with-answer, AFTER a separator — never before the prompt.

    python scripts/show_question_assets.py --workspace <ws> --id <qid> [--with-answer] [--lang zh|en]

Exit codes: 0 printed · 1 fail-closed (visual item without a displayable prompt asset) · 2 bad input.
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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import validate_workspace as V   # noqa: E402 — reuse the validator's safety rules verbatim

QUESTION_SIDE = V.QUESTION_SIDE_ROLES
ANSWER_SIDE = {"answer_context", "worked_solution"}


def _die(msg, code=2):
    sys.stderr.write("show_question_assets: " + msg + "\n")
    raise SystemExit(code)


def _usable(ws, a):
    full, unsafe = V._asset_safety(ws, a.get("path"))
    return (not unsafe) and full and os.path.isfile(full) and os.access(full, os.R_OK)


def run(argv=None):
    ap = argparse.ArgumentParser(description="Print the question-side asset Markdown that must be shown first for an item (fail-closed).")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--id", required=True, help="question id")
    ap.add_argument("--with-answer", action="store_true", help="append answer-side assets afterwards (hidden by default)")
    ap.add_argument("--lang", default="zh",
                    help="reply-language mode for the visible asset label. Accepts canonical "
                         "zh/en/bilingual plus legacy/display aliases `中文`/`English`/`双语` (`中文` and `双语` "
                         "map to zh labels `题面图`/`答案图`; `English` maps to en labels "
                         "Question-side/Answer-side asset). The `双语` caller emits the zh labels and "
                         "adds its own `> EN:` mirror.")
    args = ap.parse_args(argv)
    # v4：语言词表同源 i18n（不再私藏映射副本）；bilingual 的调用方发 zh 标签 + 自己补 `> EN:` 镜像
    import i18n                                  # 同目录
    code, _w = i18n.canon_language(str(args.lang))
    if code not in i18n.LANGS:
        _die("--lang 只接受规范值 zh/en/bilingual 或显示别名 中文/English/双语，收到: %r" % args.lang)
    lang = "en" if code == "en" else "zh"
    q_label = "题面图" if lang == "zh" else "Question-side asset"
    a_label = "答案图" if lang == "zh" else "Answer-side asset"

    bank_path = os.path.join(args.workspace, "references", "quiz_bank.json")
    if not os.path.isfile(bank_path):
        _die("找不到 quiz_bank.json: %s" % bank_path)
    try:
        bank = json.load(open(bank_path, encoding="utf-8"))
    except ValueError as e:
        _die("quiz_bank.json 不是合法 JSON: %s" % e)
    q = next((x for x in bank if isinstance(x, dict) and str(x.get("id")) == args.id), None)
    if q is None:
        _die("题库里没有 id=%s 的题" % args.id)

    # runtime visual contract covers requires/maybe AND stub/page_reference (their text isn't standalone —
    # the original page/prompt asset must be shown first; see exam-tutor SKILL)
    qts = q.get("question_text_status")
    why = ("requires" if q.get("requires_assets") is True
           else "maybe" if q.get("maybe_requires_assets") is True
           else qts if qts in ("stub", "page_reference") else None)
    visual = why is not None
    assets = [a for a in (q.get("assets") or []) if isinstance(a, dict)]
    prompt_all = [a for a in assets if a.get("role") in QUESTION_SIDE]
    prompt = [a for a in prompt_all if _usable(args.workspace, a)]
    broken = [a for a in prompt_all if not _usable(args.workspace, a)]
    answer = [a for a in assets if a.get("role") in ANSWER_SIDE and _usable(args.workspace, a)]

    if visual and (not prompt or broken):
        # strict-ALL: a visual item's prompt is complete only when EVERY question-side asset displays —
        # a question needing both a figure and a table must not be asked with one silently missing.
        pointer = ""
        if q.get("source_file") and q.get("source_pages"):
            pointer = "；原页出处 %s p.%s" % (q["source_file"], ",".join(str(p) for p in q["source_pages"]))
        sys.stderr.write("show_question_assets: %s 的题面不完整（%s）——%s%s。"
                         "按 fail-closed 契约必须跳过此题，不得按完整题面出题/讲解\n"
                         % (args.id, why,
                            ("缺失/不可用的题面侧 asset: " + ", ".join(str(a.get("path")) for a in broken))
                            if broken else "没有任何可展示的题面侧 asset", pointer))
        raise SystemExit(1)

    def _cap(a, idx, kind):
        # zh keeps the raw caption; en must stay ASCII-safe (captions AND ids built from Chinese
        # material stems are commonly CJK), so fall back id → ASCII index placeholder.
        cap = a.get("caption") or args.id
        if lang == "en" and not str(cap).isascii():
            cap = args.id if str(args.id).isascii() else "%s %d" % (kind, idx)
        return cap

    for i, a in enumerate(prompt, 1):                  # POSIX relative paths → renderable Markdown,
        rel = str(a["path"]).replace("\\", "/")        # label per reply language (docs/file-format.md §4)
        print("![%s: %s](%s)" % (q_label, _cap(a, i, "question-side asset"), rel))
    if not prompt:
        print("（该题不依赖图片，无题面 asset）" if lang == "zh"
              else "(this item needs no figure — no question-side asset)")
    if args.with_answer and answer:
        sep = ("（以下为答案/解析侧图片，讲解或复盘时才展示）" if lang == "zh"
               else "(answer/solution-side images below — shown only during solution or review)")
        print("\n--- %s ---" % sep)
        for i, a in enumerate(answer, 1):
            print("![%s: %s](%s)" % (a_label, _cap(a, i, "answer-side asset"),
                                     str(a["path"]).replace("\\", "/")))
    return 0


if __name__ == "__main__":
    sys.exit(run())
