#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight retrieval over the chunked wiki (v4-P3) — pure stdlib BM25, no LLM, no network.

Single definition point for the index format: ingest builds `references/retrieval_index.json`
by importing `build_index()` from HERE; answer-time lookup runs `search()` / this CLI. The spike
(spike/llamaindex_rag) contract is honored: results are Chunk-shaped {text, score, source}, and an
ABSTAIN GATE runs before any generation — zero term overlap (or top score below --min-score) means
「材料中未涵盖」, never a fabricated answer.

    python scripts/retrieve.py --workspace <ws> --query "旁观者效应 实验" [-k 4] [--json]

Tokenization: ASCII words are lowercased word tokens; CJK runs become character BIGRAMS (standard
zero-dep zh tokenization; single CJK char queries fall back to unigram). `references/terms.json`
(zh↔en course glossary, built at ingest) expands the query BOTH ways so a zh question retrieves
en course material and vice versa.

Degradation contract (old workspaces): a workspace without retrieval_index.json is NOT an error —
exit code 3 with a one-line notice; the agent falls back to whole-chapter reading (v3 behavior)
and may suggest re-running ingest to build the index. Exit: 0 hits · 3 no-index · 4 abstain
(no hit above the gate) · 2 bad input.
"""
import argparse
import json
import math
import os
import re
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

INDEX_NAME = os.path.join("references", "retrieval_index.json")
TERMS_NAME = os.path.join("references", "terms.json")
INDEX_VERSION = 1
K1, B = 1.5, 0.75          # standard BM25 constants; persisted in the index for provenance
DEFAULT_TOP_K = 4          # spike contract default
_CJK = r"぀-ヿ㐀-䶿一-鿿"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']*|[%s]+" % _CJK)
_CJK_RUN_RE = re.compile(r"^[%s]+$" % _CJK)


def _die(msg, code=2):
    sys.stderr.write("retrieve: " + msg + "\n")
    raise SystemExit(code)


# 查询侧停用词（Codex r2 P1）：纯英文功能词的重合不算「材料覆盖」——否则
# "What is the capital of France?" 靠 is/the/of 就能在任何英文材料上凑出正分，弃答门限形同虚设。
# 只滤查询侧：旧/新索引都零迁移；真实内容词照常检索。zh 走字 bigram（双字组合本身携带内容），
# 不设 zh 停用词——zh 越界问题由「信息词无命中 → 零命中 → 弃答」兜底。
_QUERY_STOPWORDS = frozenset(
    "a an the is are was were be been being am do does did doing have has had having will would "
    "shall should can could may might must of in on at by for with about against between into "
    "through to from up down out off over under again and or but not no nor so than too very "
    "what which who whom whose when where why how this that these those it its he she they them "
    "him his her hers their theirs you your yours we our ours i me my mine us if as then there "
    "here also just please tell say says said give gives show shows explain define describe".split())


def informative_terms(tokens):
    """查询 token 中的信息词（滤英文功能词；CJK bigram 与技术词原样保留）。"""
    return [t for t in tokens if t not in _QUERY_STOPWORDS]


def tokenize(text):
    """ASCII → lowercased word tokens; each CJK run → character bigrams (unigram if length 1).
    Deterministic and language-mixed-safe: '什么是Word-RAM模型' → ['什么','么是',...,'word-ram',...]."""
    out = []
    for m in _TOKEN_RE.finditer((text or "").lower()):
        tok = m.group(0)
        if _CJK_RUN_RE.match(tok):
            if len(tok) == 1:
                out.append(tok)
            else:
                out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            out.append(tok)
    return out


# ---------------- index build (imported by ingest) ----------------

def build_index(chunks):
    """chunks: [{"id": "ch02/s03", "file": "references/wiki/ch02/s03_xxx.md", "chapter": "2",
                "title": "...", "text": "..."}] → the persistable index dict.
    Each doc stores its OWN chunk text so snippets always come from the hit chunk — the file
    on disk holds the whole chapter, and windowing over it could show a sibling chunk's words
    under this chunk's id. The index is a gitignored workspace artifact; the size cost is fine.
    Old indexes without per-doc text still load (the snippet path falls back to the file)."""
    docs, vocab = [], {}
    for ci, c in enumerate(chunks):
        for k in ("id", "file", "text"):
            if not c.get(k):
                _die("build_index: chunk 缺必需字段 %r（id=%r）" % (k, c.get("id")))
        toks = tokenize(c["text"])
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        for t, n in tf.items():
            vocab.setdefault(t, []).append([ci, n])
        docs.append({"id": c["id"], "file": c["file"], "chapter": c.get("chapter"),
                     "title": c.get("title") or "", "len": len(toks), "text": c["text"]})
    avgdl = (sum(d["len"] for d in docs) / len(docs)) if docs else 0.0
    return {"version": INDEX_VERSION, "k1": K1, "b": B, "avgdl": round(avgdl, 3),
            "n_docs": len(docs), "docs": docs, "vocab": vocab}


# ---------------- query-time ----------------

def load_terms(ws):
    """zh↔en glossary → symmetric expansion map token→set(tokens). Missing file = no expansion."""
    path = os.path.join(ws, TERMS_NAME)
    # 与 retrieval_index.json 同一读入纪律（Codex r5）：符号链接/越界的术语表会把外部词汇
    # 注进查询扩展（并经弃答 payload 泄出）——拒读，不静默当无术语表
    if os.path.islink(path):
        _die("terms.json 不得为符号链接（可能指向工作区外）——拒绝读取")
    if not os.path.isfile(path):
        return {}
    _assert_contained(ws, path, "terms.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except ValueError as e:
        _die("terms.json 不是合法 JSON: %s" % e)
    if not isinstance(raw, dict):
        _die("terms.json 顶层必须是 {术语: [对应术语,…]} 对象")
    exp = {}
    for k, vals in raw.items():
        if not isinstance(vals, list):
            continue
        group = [k] + [v for v in vals if isinstance(v, str)]
        toks_per_term = [tokenize(t) for t in group]
        for i, src in enumerate(toks_per_term):
            for st in src:
                for j, dst in enumerate(toks_per_term):
                    if i != j:
                        exp.setdefault(st, set()).update(dst)
    return exp


def expand_query(q_tokens, exp):
    out = list(q_tokens)
    seen = set(q_tokens)
    for t in q_tokens:
        for e in exp.get(t, ()):
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out


def bm25_scores(index, q_tokens):
    n, avgdl = index["n_docs"], index["avgdl"] or 1.0
    k1, b = index.get("k1", K1), index.get("b", B)
    docs, vocab = index["docs"], index["vocab"]
    scores = {}
    for t in set(q_tokens):
        postings = vocab.get(t)
        if not postings:
            continue
        idf = math.log(1.0 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
        for di, tf in postings:
            dl = docs[di]["len"] or 1
            s = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
            scores[di] = scores.get(di, 0.0) + s
    return scores


def _assert_contained(ws, path, name):
    ws_real = os.path.normcase(os.path.realpath(ws))
    real = os.path.normcase(os.path.realpath(path))
    if real != ws_real and not real.startswith(ws_real + os.sep):
        _die("%s 经符号链接 / 父目录逃出工作区——拒绝读取" % name)


def _snippet(ws, doc, q_tokens, width=240):
    """Window around the first query-token hit inside THIS chunk's own text (fallback: head).
    The doc's stored text is authoritative — scanning the whole chapter file could window
    around a DIFFERENT chunk's occurrence and display the wrong section under this id.
    Old indexes without per-doc text keep the pre-v4 file-scan path (read-only, contained)."""
    text = doc.get("text")
    if not text:
        path = os.path.join(ws, doc["file"])
        if os.path.islink(path):
            return ""
        _assert_contained(ws, path, doc["file"])
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    low = text.lower()
    pos = -1
    for t in q_tokens:
        pos = low.find(t)
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 4)
    return re.sub(r"\s+", " ", text[start:start + width]).strip()


def search(ws, index, query, top_k=DEFAULT_TOP_K, min_score=0.0):
    exp = load_terms(ws)
    q_tokens = expand_query(informative_terms(tokenize(query)), exp)
    if not q_tokens:
        # 查询里没有任何信息词（全是英文功能词）——不允许功能词重合冒充「材料覆盖」，走弃答
        return [], q_tokens
    scores = bm25_scores(index, q_tokens)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, top_k)]
    docs = index["docs"]
    out = []
    for di, sc in ranked:
        if sc < min_score:
            continue
        d = docs[di]
        out.append({"id": d["id"], "file": d["file"], "chapter": d.get("chapter"),
                    "title": d.get("title") or "", "score": round(sc, 4),
                    "text": _snippet(ws, d, q_tokens)})
    return out, q_tokens


def load_index(ws):
    path = os.path.join(ws, INDEX_NAME)
    if os.path.islink(path):
        _die("retrieval_index.json 不得为符号链接——拒绝读取")
    if not os.path.isfile(path):
        return None
    _assert_contained(ws, path, "retrieval_index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except ValueError as e:
        _die("retrieval_index.json 不是合法 JSON: %s" % e)
    for k in ("version", "docs", "vocab", "n_docs", "avgdl"):
        if k not in idx:
            _die("retrieval_index.json 缺字段 %r——索引损坏，请重跑 ingest 重建" % k)
    if idx["version"] != INDEX_VERSION:
        _die("retrieval_index.json version=%r 与本工具 (%d) 不符——请重跑 ingest 重建"
             % (idx["version"], INDEX_VERSION))
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser(description="BM25 retrieval over the chunked wiki (stdlib only; "
                                             "abstain gate before any generation)")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("-k", "--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="absolute BM25 gate; 0 disables (zero-hit abstain still applies)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    ws = args.workspace
    if not os.path.isdir(ws):
        _die("工作区不存在: %s" % ws)
    if args.top_k <= 0:
        _die("--top-k 必须为正整数")
    if args.min_score < 0:
        _die("--min-score 不能为负")

    index = load_index(ws)
    if index is None:
        # 老工作区无索引：不是错误——降级提示 + 独立退出码，agent 回落整章直读（v3 行为）
        sys.stderr.write("retrieve: no_index: 本工作区没有 retrieval_index.json——按旧行为直读章节文件；"
                         "可重跑 scripts/ingest.py 升级出索引后再用检索\n")
        raise SystemExit(3)

    hits, q_tokens = search(ws, index, args.query, args.top_k, args.min_score)
    if not hits:
        # 弃答门限：零命中/全部低于门限——按 spike 契约先于任何生成宣布「材料中未涵盖」
        payload = {"abstain": True, "reason": "no_hit_above_gate", "query_tokens": q_tokens[:32]}
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("[!] abstain: 检索零命中（或全部低于 --min-score 门限）——材料中未涵盖，不要编造")
        raise SystemExit(4)

    if args.as_json:
        print(json.dumps({"abstain": False, "hits": hits}, ensure_ascii=False))
    else:
        for h in hits:
            print("%-14s score=%-8s %s" % (h["id"], h["score"], h["file"]))
            if h["title"]:
                print("    # %s" % h["title"])
            if h["text"]:
                print("    %s" % h["text"][:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
