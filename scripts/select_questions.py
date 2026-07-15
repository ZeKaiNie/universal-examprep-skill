#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Official question selector (A2) — filter the quiz bank by tags instead of ad-hoc scripts.

quiz_bank.json is the SINGLE SOURCE OF TRUTH; every invocation reads it fresh (no stale cache reads).
Filters compose with AND semantics:

    python scripts/select_questions.py --workspace <ws> --source-type homework
    python scripts/select_questions.py --workspace <ws> --chapter 2 --requires-assets yes
    python scripts/select_questions.py --workspace <ws> --knowledge-point 条件概率 --difficulty-min 3
    python scripts/select_questions.py --workspace <ws> --source-type homework,exam --json

`--export-sqlite <path>` additionally writes a sqlite3 query cache (stdlib; a GENERATED artifact for
external ad-hoc SQL, never committed, never read back by this tool). Honest scope: selection is
deterministic tag filtering — no relevance ranking, no LLM.

Exit codes: 0 ok (even if 0 matches — the count is printed) · 2 bad input/usage.
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

SOURCE_TYPES = {"homework", "lecture_quiz", "example", "practice_exam", "exam", "other"}


def _die(msg):
    sys.stderr.write("select_questions: " + msg + "\n")
    raise SystemExit(2)


def load_bank(ws):
    path = os.path.join(ws, "references", "quiz_bank.json")
    if not os.path.isfile(path):
        _die("找不到题库: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            bank = json.load(f)
    except ValueError as e:
        _die("quiz_bank.json 不是合法 JSON: %s" % e)
    if not isinstance(bank, list):
        _die("quiz_bank.json 顶层必须是数组")
    return [q for q in bank if isinstance(q, dict) and q.get("id") is not None]


def _chapter_of(q):
    c = q.get("chapter") if q.get("chapter") is not None else q.get("phase")
    return str(c) if c is not None else None


def match(q, args):
    # Backward compatibility for banks produced before worked examples became
    # teaching-only: an explicit false flag is a hard assessment exclusion.
    if q.get("gradable") is False:
        return False
    if args.source_type:
        # missing source_type NEVER silently matches a scope filter — untagged items are excluded and
        # counted separately, so a homework-only session can't quietly serve untagged lecture items
        if q.get("source_type") not in args.source_type:
            return False
    if args.chapter is not None:
        keys = {str(q.get("chapter")) if q.get("chapter") is not None else None,
                str(q.get("phase")) if q.get("phase") is not None else None} - {None}
        if str(args.chapter) not in keys:              # chapter OR phase（题可同时带原章号与复习阶段）
            return False
    if args.knowledge_point:
        kps = q.get("knowledge_points") or []
        if not any(args.knowledge_point in k for k in kps if isinstance(k, str)):
            return False
    d = q.get("difficulty")
    if args.difficulty_min is not None and not (isinstance(d, int) and not isinstance(d, bool)
                                                and d >= args.difficulty_min):
        return False
    if args.difficulty_max is not None and not (isinstance(d, int) and not isinstance(d, bool)
                                                and d <= args.difficulty_max):
        return False
    if args.requires_assets != "any":
        req = q.get("requires_assets") is True
        maybe = q.get("maybe_requires_assets") is True
        want = {"yes": req, "no": not (req or maybe), "maybe": maybe}[args.requires_assets]
        if not want:
            return False
    return True


def export_sqlite(bank, path):
    import sqlite3
    if os.path.exists(path):
        # NEVER overwrite something that isn't a previous sqlite export（误指到 quiz_bank.json 等
        # 工作区文件会把它删掉——按魔数校验，非 SQLite 文件一律拒绝）
        with open(path, "rb") as f:
            magic = f.read(16)
        if not magic.startswith(b"SQLite format 3"):
            _die("--export-sqlite 目标已存在且不是 SQLite 缓存文件，拒绝覆盖: %s" % path)
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE questions (id TEXT PRIMARY KEY, type TEXT, chapter TEXT, phase TEXT, "
                    "source_type TEXT, difficulty INTEGER, difficulty_reason TEXT, "
                    "requires_assets INTEGER, maybe_requires_assets INTEGER, "
                    "has_official_answer INTEGER, question TEXT)")
        con.execute("CREATE TABLE knowledge_points (question_id TEXT, knowledge_point TEXT)")
        for q in bank:
            if q.get("gradable") is False:
                continue
            # official = 教材/老师来源的答案；mixed/unknown/缺 source 都不算（与视觉索引同口径）
            official_src = q.get("source") in ("teacher", "material") and q.get("ai_generated") is not True

            def _nonblank(v):
                if isinstance(v, str):
                    return bool(v.strip())             # 空白-only 答案不算有答案
                if isinstance(v, (list, tuple)):
                    return any(isinstance(x, str) and x.strip() for x in v)
                if isinstance(v, dict):
                    return bool(v)                     # {} 与校验器口径一致：不算有答案
                return v is not None
            has_ans = official_src and (_nonblank(q.get("answer")) or _nonblank(q.get("answer_keywords")))
            con.execute("INSERT OR REPLACE INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (str(q["id"]), q.get("type"),
                         str(q.get("chapter")) if q.get("chapter") is not None else None,
                         str(q.get("phase")) if q.get("phase") is not None else None,
                         q.get("source_type"),
                         q.get("difficulty") if isinstance(q.get("difficulty"), int)
                         and not isinstance(q.get("difficulty"), bool) else None,
                         q.get("difficulty_reason"),
                         int(q.get("requires_assets") is True), int(q.get("maybe_requires_assets") is True),
                         int(has_ans), str(q.get("question", ""))[:500]))
            seen_kp = set()
            for k in (q.get("knowledge_points") or []):
                if isinstance(k, str) and k not in seen_kp:       # 重复标签只插一行
                    seen_kp.add(k)
                    con.execute("INSERT INTO knowledge_points VALUES (?,?)", (str(q["id"]), k))
        con.commit()
    finally:
        con.close()


def run(argv=None):
    ap = argparse.ArgumentParser(description="Filter questions by tags (source/chapter/knowledge point/difficulty/figure-dependency); quiz_bank is the single source of truth.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--source-type", default=None,
                    help="comma-separated source filter (%s)" % "/".join(sorted(SOURCE_TYPES)))
    ap.add_argument("--chapter", default=None, help="chapter/phase")
    ap.add_argument("--knowledge-point", default=None, help="knowledge point (substring match)")
    ap.add_argument("--difficulty-min", type=int, default=None)
    ap.add_argument("--difficulty-max", type=int, default=None)
    ap.add_argument("--requires-assets", choices=["any", "yes", "no", "maybe"], default="any")
    ap.add_argument("--limit", type=int, default=0, help="0 = no limit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--export-sqlite", default=None,
                    help="optional: export the bank as a sqlite query cache (generated artifact; not committed, never read back by this tool)")
    args = ap.parse_args(argv)

    if args.source_type is not None:                   # ""（空字符串）也必须走校验，不能静默回混合池
        vals = [v.strip() for v in args.source_type.split(",") if v.strip()]
        if not vals:
            _die("--source-type 为空（如 ','）——空过滤器不等于不过滤，请给出至少一个来源")
        bad = [v for v in vals if v not in SOURCE_TYPES]
        if bad:
            _die("非法 source_type: %s（应为 %s）" % (bad, sorted(SOURCE_TYPES)))
        args.source_type = set(vals)
    for k in ("difficulty_min", "difficulty_max"):
        v = getattr(args, k)
        if v is not None and not 1 <= v <= 5:
            _die("--%s 必须在 1–5 内" % k.replace("_", "-"))

    bank = load_bank(args.workspace)
    if args.export_sqlite:
        export_sqlite(bank, args.export_sqlite)
        sys.stderr.write("[+] sqlite 缓存: %s（生成物，勿提交）\n" % args.export_sqlite)   # stdout 留给 --json

    hits = [q for q in bank if match(q, args)]
    untagged = 0
    if args.source_type:
        # 只统计「除范围外其余过滤都命中」的未标签题——它们才是被 scope 排除的真实候选
        import argparse as _ap
        rest = _ap.Namespace(**{**vars(args), "source_type": None})
        untagged = sum(1 for q in bank if q.get("source_type") is None and match(q, rest))
    total = len(hits)
    if args.limit and args.limit > 0:
        hits = hits[: args.limit]

    if args.json:
        print(json.dumps({"total_matched": total, "returned": len(hits),
                          "untagged_excluded": untagged,
                          "items": [{"id": str(q["id"]), "type": q.get("type"),
                                     "chapter": q.get("chapter"), "phase": q.get("phase"),
                                     "source_type": q.get("source_type"),
                                     "knowledge_points": q.get("knowledge_points"),
                                     "difficulty": q.get("difficulty"),
                                     "requires_assets": q.get("requires_assets") is True,
                                     "maybe_requires_assets": q.get("maybe_requires_assets") is True}
                                    for q in hits]}, ensure_ascii=False, indent=2))
        return 0
    print("匹配 %d 题（显示 %d）" % (total, len(hits)))
    for q in hits:
        print("- [#%s] ch%s %s%s%s %s" % (
            q["id"], _chapter_of(q) or "?",
            (q.get("source_type") or "未标来源"),
            (" 难度%d" % q["difficulty"]) if isinstance(q.get("difficulty"), int)
            and not isinstance(q.get("difficulty"), bool) else "",
            " 图依赖" if q.get("requires_assets") is True
            else (" 疑似图依赖" if q.get("maybe_requires_assets") is True else ""),
            str(q.get("question", ""))[:40]))
    if untagged:
        print("[!] 另有 %d 题未标 source_type，被范围过滤排除——跑 A3 homework ingest 或手工补标后重试" % untagged)
    return 0


if __name__ == "__main__":
    sys.exit(run())
