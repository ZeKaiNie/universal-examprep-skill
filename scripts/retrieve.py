#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight retrieval over structured course chunks — pure stdlib BM25, no LLM, no network.

Single definition point for the index format: ingest builds `references/retrieval_index.json`
by importing `build_index()` from HERE; answer-time lookup runs `search()` / this CLI. Results
are citation-shaped {text, score, source}, and an
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
import hashlib
import json
import math
import os
import re
import sys

try:
    import strict_json
except ImportError:  # imported as scripts.retrieve in unit tests
    from scripts import strict_json

try:
    from ingestion.identifiers import (
        is_link_or_reparse,
        normalize_workspace_path,
        safe_workspace_entry,
    )
except ImportError:  # imported as scripts.retrieve in unit tests
    from scripts.ingestion.identifiers import (
        is_link_or_reparse,
        normalize_workspace_path,
        safe_workspace_entry,
    )

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

INDEX_NAME = os.path.join("references", "retrieval_index.json")
TERMS_NAME = os.path.join("references", "terms.json")
TEACHING_EXAMPLES_NAME = "references/teaching_examples.json"
INDEX_VERSION = 2
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

def build_index(chunks, integrity=None):
    """chunks: [{"id": "ch02/s03", "file": "references/wiki/ch02/s03_xxx.md", "chapter": "2",
                "title": "...", "text": "..."}] → the persistable index dict.
    Each doc stores its OWN chunk text so snippets always come from the hit chunk — the file
    on disk holds the whole chapter, and windowing over it could show a sibling chunk's words
    under this chunk's id. The index is a gitignored workspace artifact; the size cost is fine.
    Old indexes without per-doc text still load (the snippet path falls back to the file)."""
    docs, vocab = [], {}
    seen_ids = set()
    for ci, c in enumerate(chunks):
        for k in ("id", "file", "text"):
            if not c.get(k):
                _die("build_index: chunk 缺必需字段 %r（id=%r）" % (k, c.get("id")))
        if c["id"] in seen_ids:
            _die("build_index: chunk id 重复: %s" % c["id"])
        seen_ids.add(c["id"])
        toks = tokenize(c["text"])
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        for t, n in tf.items():
            vocab.setdefault(t, []).append([ci, n])
        docs.append({
            "id": c["id"], "file": c["file"], "chapter": c.get("chapter"),
            "chapter_id": c.get("chapter_id"), "phase_id": c.get("phase_id"),
            "title": c.get("title") or "", "len": len(toks), "text": c["text"],
            "source_file": c.get("source_file"), "pages": list(c.get("pages") or ()),
            "unit_ids": list(c.get("unit_ids") or ()), "kind": c.get("kind") or "prose",
            "asset_paths": list(c.get("asset_paths") or ()),
            "asset_roles": list(c.get("asset_roles") or ()),
        })
    avgdl = (sum(d["len"] for d in docs) / len(docs)) if docs else 0.0
    return {"version": INDEX_VERSION, "k1": K1, "b": B, "avgdl": round(avgdl, 3),
            "n_docs": len(docs), "docs": docs, "vocab": vocab,
            "integrity": integrity if isinstance(integrity, dict) else {"wiki": []}}


# ---------------- query-time ----------------

def load_terms(ws):
    """zh↔en glossary → symmetric expansion map token→set(tokens). Missing file = no expansion."""
    path = os.path.join(ws, TERMS_NAME)
    # 与 retrieval_index.json 同一读入纪律（Codex r5）：符号链接/越界的术语表会把外部词汇
    # 注进查询扩展（并经弃答 payload 泄出）——拒读，不静默当无术语表
    if is_link_or_reparse(path):
        _die("terms.json 不得为符号链接（可能指向工作区外）——拒绝读取")
    if not os.path.isfile(path):
        return {}
    _assert_contained(ws, path, "terms.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = strict_json.load(f)
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
        if is_link_or_reparse(path):
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
        out.append({
            "id": d["id"], "file": d["file"], "chapter": d.get("chapter"),
            "chapter_id": d.get("chapter_id"), "phase_id": d.get("phase_id"),
            "source_file": d.get("source_file"), "pages": d.get("pages") or [],
            "unit_ids": d.get("unit_ids") or [], "kind": d.get("kind") or "prose",
            "asset_paths": d.get("asset_paths") or [],
            "asset_roles": d.get("asset_roles") or [],
            "title": d.get("title") or "", "score": round(sc, 4),
            "text": _snippet(ws, d, q_tokens),
        })
    return out, q_tokens


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_integrity(ws, integrity):
    if not isinstance(integrity, dict):
        _die("retrieval_index.json integrity 必须是对象——请重跑 ingest 重建")
    wiki = integrity.get("wiki", [])
    if not isinstance(wiki, list):
        _die("retrieval_index.json integrity.wiki 必须是数组——请重跑 ingest 重建")
    fixed_files = {
        "source_manifest": ".ingest/source_manifest.json",
        "content_units": ".ingest/content_units.jsonl",
        "canonical_groups": ".ingest/canonical_groups.jsonl",
        "source_conflicts": ".ingest/source_conflicts.jsonl",
        "quiz_bank": "references/quiz_bank.json",
        "teaching_examples": TEACHING_EXAMPLES_NAME,
    }
    rows = list(wiki)
    for key, fixed_file in fixed_files.items():
        if integrity.get(key) is not None:
            row = integrity[key]
            if not isinstance(row, dict) or row.get("file") != fixed_file:
                _die(
                    "retrieval_index.json integrity.%s must bind fixed file %s"
                    "——请重跑 ingest 重建" % (key, fixed_file)
                )
            rows.append(row)

    # Legacy indexes remain usable only while no teaching layer exists.  Once
    # that layer is present it participates in retrieval policy, so an index
    # that does not bind its exact bytes is stale even when every indexed wiki
    # and quiz-bank byte is unchanged.
    try:
        teaching_path = str(safe_workspace_entry(ws, TEACHING_EXAMPLES_NAME))
    except (TypeError, ValueError) as exc:
        _die("teaching layer path is unsafe: %s" % exc)
    if os.path.lexists(teaching_path) and integrity.get("teaching_examples") is None:
        _die(
            "stale_index: references/teaching_examples.json exists but the index "
            "does not bind it——请重跑 ingest 重建"
        )
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"file", "sha256"}:
            _die("retrieval_index.json integrity 条目损坏——请重跑 ingest 重建")
        relative = row["file"]
        expected = row["sha256"]
        if not isinstance(relative, str) or not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            _die("retrieval_index.json integrity 条目字段无效——请重跑 ingest 重建")
        try:
            canonical = normalize_workspace_path(relative)
            path = str(safe_workspace_entry(ws, canonical))
        except (TypeError, ValueError) as exc:
            _die("索引依赖文件路径不安全: %s (%s)" % (relative, exc))
        if is_link_or_reparse(path):
            _die("索引依赖文件不得为符号链接或重解析点: %s" % relative)
        if not os.path.isfile(path):
            _die("索引依赖文件缺失: %s——请重跑 ingest 重建" % relative)
        if _sha256_file(path) != expected:
            _die("stale_index: %s 已变更——拒绝旧索引，请重跑 ingest 重建" % relative)


def load_index(ws):
    path = os.path.join(ws, INDEX_NAME)
    if is_link_or_reparse(path):
        _die("retrieval_index.json 不得为符号链接或重解析点——拒绝读取")
    if not os.path.isfile(path):
        return None
    _assert_contained(ws, path, "retrieval_index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            idx = strict_json.load(f)
    except ValueError as e:
        _die("retrieval_index.json 不是合法 JSON: %s" % e)
    for k in ("version", "docs", "vocab", "n_docs", "avgdl", "integrity"):
        if k not in idx:
            _die("retrieval_index.json 缺字段 %r——索引损坏，请重跑 ingest 重建" % k)
    if idx["version"] != INDEX_VERSION:
        _die("retrieval_index.json version=%r 与本工具 (%d) 不符——请重跑 ingest 重建"
             % (idx["version"], INDEX_VERSION))
    expected_top = {"version", "k1", "b", "avgdl", "n_docs", "docs", "vocab", "integrity"}
    if not isinstance(idx, dict) or set(idx) != expected_top:
        _die("retrieval_index.json 顶层 schema 无效——请重跑 ingest 重建")
    if (type(idx["n_docs"]) is not int or idx["n_docs"] < 0
            or not isinstance(idx["docs"], list) or len(idx["docs"]) != idx["n_docs"]
            or not isinstance(idx["vocab"], dict)
            or not isinstance(idx["avgdl"], (int, float))
            or isinstance(idx["avgdl"], bool) or not math.isfinite(idx["avgdl"])
            or idx["avgdl"] < 0
            or not isinstance(idx["k1"], (int, float)) or isinstance(idx["k1"], bool)
            or not math.isfinite(idx["k1"]) or idx["k1"] <= 0
            or not isinstance(idx["b"], (int, float)) or isinstance(idx["b"], bool)
            or not math.isfinite(idx["b"]) or not 0 <= idx["b"] <= 1):
        _die("retrieval_index.json 统计字段无效——请重跑 ingest 重建")
    integrity_files = set()
    if isinstance(idx["integrity"], dict):
        integrity_rows = list(idx["integrity"].get("wiki") or ())
        for key in (
            "source_manifest", "content_units", "canonical_groups",
            "source_conflicts", "quiz_bank", "teaching_examples",
        ):
            if idx["integrity"].get(key) is not None:
                integrity_rows.append(idx["integrity"][key])
        for row in integrity_rows:
            if isinstance(row, dict) and isinstance(row.get("file"), str):
                try:
                    integrity_files.add(normalize_workspace_path(row["file"]))
                except (TypeError, ValueError):
                    pass
    expected_doc = {
        "id", "file", "chapter", "chapter_id", "phase_id", "title", "len", "text",
        "source_file", "pages", "unit_ids", "kind", "asset_paths", "asset_roles",
    }
    doc_ids = set()
    for position, doc in enumerate(idx["docs"]):
        # Index v2 existed briefly before per-chunk text was embedded.  Accept
        # exactly that one historical shape so old student workspaces retain the
        # documented contained file-scan fallback; all other missing/unknown
        # fields remain fail-closed.
        legacy_doc = expected_doc - {"text"}
        if not isinstance(doc, dict) or set(doc) not in (expected_doc, legacy_doc):
            _die("retrieval_index.json docs[%d] schema 无效——请重跑 ingest" % position)
        if (not isinstance(doc["id"], str) or not doc["id"] or doc["id"] in doc_ids
                or not isinstance(doc["file"], str) or not doc["file"]
                or type(doc["len"]) is not int or doc["len"] < 0
                or ("text" in doc and not isinstance(doc["text"], str))
                or not all(isinstance(doc[key], list)
                           for key in ("pages", "unit_ids", "asset_paths", "asset_roles"))):
            _die("retrieval_index.json docs[%d] 字段无效——请重跑 ingest" % position)
        try:
            doc_file = normalize_workspace_path(doc["file"])
            safe_workspace_entry(ws, doc_file)
            asset_paths = [normalize_workspace_path(value) for value in doc["asset_paths"]]
            for asset_path in asset_paths:
                safe_workspace_entry(ws, asset_path)
        except (TypeError, ValueError) as exc:
            _die("retrieval_index.json docs[%d] 路径不安全: %s" % (position, exc))
        if doc_file not in integrity_files:
            _die("retrieval_index.json docs[%d].file 未绑定到完整性清单" % position)
        if (not all(type(page) is int and page >= 1 for page in doc["pages"])
                or not all(isinstance(value, str) and value for value in doc["unit_ids"])
                or not all(isinstance(value, str) and value for value in doc["asset_paths"])
                or not all(isinstance(value, str) and value for value in doc["asset_roles"])
                or not isinstance(doc["title"], str)
                or not isinstance(doc["kind"], str) or not doc["kind"]
                or (doc["chapter"] is not None
                    and (isinstance(doc["chapter"], bool)
                         or not isinstance(doc["chapter"], (str, int))))
                or any(doc[key] is not None and not isinstance(doc[key], str)
                       for key in ("chapter_id", "phase_id", "source_file"))):
            _die("retrieval_index.json docs[%d] metadata 无效——请重跑 ingest" % position)
        doc_ids.add(doc["id"])
    for token, postings in idx["vocab"].items():
        if not isinstance(token, str) or not token or not isinstance(postings, list):
            _die("retrieval_index.json vocab schema 无效——请重跑 ingest")
        seen_docs = set()
        for posting in postings:
            if (not isinstance(posting, list) or len(posting) != 2
                    or type(posting[0]) is not int or not 0 <= posting[0] < idx["n_docs"]
                    or type(posting[1]) is not int or posting[1] <= 0
                    or posting[0] in seen_docs):
                _die("retrieval_index.json posting 无效——请重跑 ingest")
            seen_docs.add(posting[0])
    _verify_integrity(ws, idx["integrity"])
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
