# -*- coding: utf-8 -*-
"""The single mock/real SEAM. stdlib-only import surface — heavy deps live inside the real backend's
methods, never here. Both backends inherit ONE out-of-scope policy (the abstain gate) so mock and real
behave identically on answerable=false probes; subclasses override only index/retrieve/generate.
"""
import abc
from dataclasses import dataclass

ABSTAIN = "材料中未涵盖"          # 与 contract.ABSTAIN / judge.py 弃答标记一致


@dataclass(frozen=True)
class Chunk:
    text: str
    score: float
    source: str = ""


class Backend(abc.ABC):
    """检索增强的抽象后端。answer_for_item 是共享的确定性外壳：建索引(幂等)→检索→弃答门→生成。"""

    def __init__(self, config):
        self.config = config or {}
        self._indexed = False

    @abc.abstractmethod
    def index(self, materials_text):
        """把语料切块/嵌入/建索引，每个语料只建一次（子类内部也应自带重建守卫）。"""

    @abc.abstractmethod
    def retrieve(self, question):
        """返回按 score 降序的 top-k Chunk。"""

    @abc.abstractmethod
    def generate(self, question, chunks):
        """由检索到的 chunks 生成一条答案字符串（仅在弃答门通过后调用）。"""

    def answer_for_item(self, item, materials_text):
        """返回一条纯答案字符串（judge.judge_answer 消费的形态）。

        弃答门 = 好 RAG 对越界探针的正确行为：没有 chunk 或 top score < min_score → 返回 ABSTAIN，
        且**在**调用 generate（真后端会打 LLM）**之前**就短路，越界探针绝不触发 LLM。"""
        if not self._indexed:
            self.index(materials_text)
            self._indexed = True
        chunks = self.retrieve(item.get("question", ""))
        min_score = float(self.config.get("min_score", 0.25))   # 与 DEFAULT_CFG/config/README 的默认一致
        top = max((c.score for c in chunks), default=0.0)
        if not chunks or top < min_score:
            return ABSTAIN
        return self.generate(item.get("question", ""), chunks)


def resolve_backend_name(config):
    """把 config 解析成实际会用的后端名（mock / llamaindex）——单一事实源。
    summary/tag/密钥校验都用它，而非零散的 mock 标志，避免 --backend mock + --real 这类组合被错标。"""
    name = config.get("backend") or ("mock" if config.get("mock", True) else "llamaindex")
    return "llamaindex" if name == "real" else name


def make_backend(config):
    """唯一的分支点。mock 在任何重依赖 import 之前短路；真后端惰性 import（其模块顶仍是 stdlib）。"""
    raw = config.get("backend") or ("mock" if config.get("mock", True) else "llamaindex")
    if raw not in ("mock", "llamaindex", "real"):
        raise SystemExit("[-] 未知 backend：%r（应为 mock / llamaindex）" % raw)
    name = resolve_backend_name(config)
    if name == "mock":
        from mock_backend import MockBackend        # stdlib-only
        return MockBackend(config)
    from llamaindex_backend import LlamaIndexBackend   # 模块顶为 stdlib；重依赖在其方法内
    return LlamaIndexBackend(config)
