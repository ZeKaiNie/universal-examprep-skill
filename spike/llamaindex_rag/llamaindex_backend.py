# -*- coding: utf-8 -*-
"""LAZY real seam — LlamaIndexBackend. Module top imports ONLY stdlib (via backend), so
`from llamaindex_backend import LlamaIndexBackend` is safe on a clean checkout with no deps.

ALL third-party imports live INSIDE index() behind a try/except ImportError that names the exact
pip install. This backend is only constructed on the explicit --real / --backend llamaindex path
(mock short-circuits in the factory first), so --mock never reaches any guarded import.

API verified against llama-index 0.14.x (2026-06). Traps encoded here:
  · framework symbols live in llama_index.CORE; integrations are split packages.
  · Settings (global) REPLACES the deprecated ServiceContext.
  · OpenAILike param is `api_base` (NOT base_url); is_chat_model=True (default False → /completions 404).
  · embed_model set EXPLICITLY to local HF BGE — the deepseek OpenAI-compat endpoint has no /embeddings
    route, and the starter default OpenAIEmbedding would silently demand OPENAI_API_KEY.
"""
from backend import Backend, Chunk

_PIP_HINT = ("[-] 需要 LlamaIndex 才能真跑：pip install -r spike/llamaindex_rag/requirements-real.txt"
             "（--mock 不需要任何依赖 / 网络 / 密钥）")


class LlamaIndexBackend(Backend):
    def __init__(self, config):
        super().__init__(config)
        self._retriever = None
        self._qe = None

    def index(self, materials_text):
        if self._retriever is not None:                 # 每个语料只建一次
            return
        try:
            from llama_index.core import (Document, VectorStoreIndex, Settings,
                                          get_response_synthesizer)
            from llama_index.core.node_parser import SentenceSplitter
            from llama_index.core.retrievers import VectorIndexRetriever
            from llama_index.core.query_engine import RetrieverQueryEngine
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding
            from llama_index.llms.openai_like import OpenAILike
        except ImportError as e:
            raise SystemExit(_PIP_HINT + "\n  缺失：%s" % e)

        cfg = self.config
        key = cfg.get("openai_api_key")
        if not key:
            raise SystemExit("[-] 真跑生成需要 openai_api_key（写进 config.json；--mock 不需要）")

        # 本地 BGE 嵌入：无密钥、模型下载后查询期零网络。显式设置，避免 starter 默认走 OpenAIEmbedding。
        Settings.embed_model = HuggingFaceEmbedding(
            model_name=cfg.get("embed_model") or "BAAI/bge-small-en-v1.5")
        # 生成：OpenAI 兼容端点（匹配本仓库既有的 deepseek openai_api_base）。
        Settings.llm = OpenAILike(
            model=cfg.get("generator_model") or "deepseek-chat",
            api_base=cfg.get("openai_api_base"),         # 参数名是 api_base，不是 base_url
            api_key=key,
            is_chat_model=True,                          # 默认 False 会打 /completions 而 404
            context_window=int(cfg.get("context_window", 32768)),
            temperature=0.1, timeout=60.0, max_retries=3,
        )
        Settings.node_parser = SentenceSplitter(
            chunk_size=int(cfg.get("chunk_size", 512)),
            chunk_overlap=int(cfg.get("chunk_overlap", 64)))

        index = VectorStoreIndex.from_documents([Document(text=materials_text or "")])
        # 用低层 retriever 以便读到 similarity score，喂给继承来的弃答门（在任何 LLM 调用之前）。
        self._retriever = VectorIndexRetriever(index=index,
                                               similarity_top_k=int(cfg.get("top_k", 4)))
        self._qe = RetrieverQueryEngine(
            retriever=self._retriever,
            response_synthesizer=get_response_synthesizer(response_mode="compact"))

    def retrieve(self, question):
        nodes = self._retriever.retrieve(question)
        return [Chunk(text=n.node.get_content(), score=float(n.score or 0.0),
                      source=(getattr(n.node, "ref_doc_id", "") or ""))
                for n in nodes]

    def generate(self, question, chunks):
        # 仅在弃答门通过后到达——LLM 绝不对弱检索/越界探针触发。
        return str(self._qe.query(question))
