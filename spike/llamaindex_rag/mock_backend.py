# -*- coding: utf-8 -*-
"""stdlib-ONLY deterministic MockBackend — an honest STAND-IN, not a real embedder/LLM.

Uses only re / hashlib / math. Hashing bag-of-words embedder + cosine retriever + extractive generator.
Same input + config → byte-identical output. It measures NOTHING; it exists to exercise the
chunk→embed→retrieve→abstain→generate pipeline end-to-end with zero pip/network/keys.
"""
import hashlib
import math
import re

from backend import Backend, Chunk

_DIM = 256          # 哈希桶维度

# 丢掉纯功能词/疑问词——否则 "the/of/in" 之类高频停用词会给毫不相干的段落刷出虚高余弦，
# 让越界探针骗过弃答门。停用词过滤让 mock 的词袋检索按**内容词**判别（仍是纯词法 stand-in）。
_STOP = frozenset("""
a an the of in on at to for and or but is are was were be been being it its this that these those
with as by from into than then so do does did done how what which who whom whose when where why
after before during their they them there here up down out only each every any some many much more
most will would shall should can could may might must not no nor if else while about over under
""".split())


class MockBackend(Backend):
    def __init__(self, config):
        super().__init__(config)
        self._chunks = None
        self._vecs = None

    # ---- 确定性哈希嵌入（词袋 → 定长向量 → L2 归一，dot == cosine）----
    @staticmethod
    def _tokens(text):
        return [t for t in re.findall(r"[A-Za-z0-9]+", (text or "").lower()) if t not in _STOP]

    def _embed(self, text):
        vec = [0.0] * _DIM
        for tok in self._tokens(text):
            bucket = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % _DIM
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec

    def _chunk_text(self, materials_text):
        """空行切段；超长段按 chunk_size 词加 overlap 滑窗（fixtures 段短 → 每段一块）。"""
        paras = [p.strip() for p in re.split(r"\n\s*\n", materials_text or "") if p.strip()]
        size = max(1, int(self.config.get("chunk_size", 512)))
        overlap = max(0, int(self.config.get("chunk_overlap", 64)))
        step = max(1, size - overlap)
        chunks = []
        for p in paras:
            words = p.split()
            if len(words) <= size:
                chunks.append(p)
                continue
            i = 0
            while i < len(words):
                chunks.append(" ".join(words[i:i + size]))
                if i + size >= len(words):
                    break
                i += step
        return chunks

    def index(self, materials_text):
        if self._chunks is not None:          # 重建守卫：每个语料只建一次
            return
        self._chunks = self._chunk_text(materials_text)
        self._vecs = [self._embed(c) for c in self._chunks]

    def retrieve(self, question):
        if not self._chunks:
            return []
        qv = self._embed(question)
        scored = []
        for i, (text, vec) in enumerate(zip(self._chunks, self._vecs)):
            score = sum(a * b for a, b in zip(qv, vec))     # 归一后点积即余弦
            scored.append((score, i, text))
        top_k = max(1, int(self.config.get("top_k", 4)))
        scored.sort(key=lambda t: (-t[0], t[1]))            # score 降序；同分按原序（确定性 tie-break）
        src = self.config.get("materials_text", "")
        return [Chunk(text=text, score=score, source=src) for (score, i, text) in scored[:top_k]]

    def generate(self, question, chunks):
        """抽取式 stand-in：返回 top chunk 的首句 / 前 200 字（非 LLM，仅占位，不谎称正确）。"""
        top = chunks[0].text
        first = re.split(r"(?<=[。.!?！？])\s", top, maxsplit=1)[0]
        return first[:200]
