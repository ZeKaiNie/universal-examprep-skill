# -*- coding: utf-8 -*-
"""Tiny BM25 retrieval over chapter chunks. No dependencies, rebuilt in memory on load."""
import json
import math
import os
from collections import Counter

from .text import pack, tokenize

INDEX_FILE = "index.json"
CHUNK_CHARS = 600
K1, B = 1.5, 0.75


class BM25(object):
    def __init__(self, docs_tokens):
        self.n = len(docs_tokens)
        self.tf = [Counter(t) for t in docs_tokens]
        self.dl = [len(t) for t in docs_tokens]
        self.avgdl = (sum(self.dl) / self.n) if self.n else 1.0
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query_tokens):
        out = [0.0] * self.n
        for q in set(query_tokens):
            idf = self.idf.get(q)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(q)
                if not f:
                    continue
                out[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self.dl[i] / self.avgdl))
        return out

    def best(self, query_tokens, k=5):
        sc = self.scores(query_tokens)
        order = sorted(range(self.n), key=lambda i: -sc[i])
        return [(i, sc[i]) for i in order[:k] if sc[i] > 0]


def build_chunks(chapters):
    chunks = []
    for ch in chapters:
        for rel, page, text in ch.blocks:
            for piece in pack(text, CHUNK_CHARS):
                chunks.append({"chapter": ch.number, "file": rel, "page": page, "text": piece})
    return chunks


def save_index(ws, chunks):
    path = os.path.join(ws, INDEX_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 5, "chunks": chunks}, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load_index(ws):
    path = os.path.join(ws, INDEX_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("chunks", [])


def search(chunks, query, k=5, chapter=None):
    """Return [(chunk, score)] ranked by BM25; empty list means the materials have no match."""
    pool = [c for c in chunks if chapter is None or c["chapter"] == chapter]
    if not pool:
        return []
    bm = BM25([tokenize(c["text"]) for c in pool])
    q = tokenize(query)
    return [(pool[i], s) for i, s in bm.best(q, k)]
