# LlamaIndex RAG 实验 spike（阶段：增强·探索）

> **一句话**：一个**独立实验骨架**，用来把「本仓库确定性的 wiki + 聚焦检索」和「真·向量 RAG（LlamaIndex）」放到**同一套金标**上做同题对照。当前只交付**规格 + 可跑骨架 + `--mock` 干跑**——真索引/检索/生成是 opt-in，留给你显式开。

---

## 这是什么，不是什么

- **是**：`spike/llamaindex_rag/` 下一个自包含目录：规格（本文件）+ 一条 backend 无关的管线骨架 + 一个纯 stdlib 的 `--mock` 干跑 + 自测。
- **不是**：它**没有**接入 skill（`skills/`、`scripts/ingest.py`），**也没有**接入 benchmark 主线（`benchmark/run_benchmark.py`、`gen.py`、`judge.py`、report/矩阵管线）。它是探索件，不是已交付特性。
- **诚实口径**：`--mock` 是**确定性 stand-in**（哈希词袋嵌入 + 余弦检索 + 抽取式「生成」），**不测量任何正确率**——它的计数是占位，和 `benchmark/run_benchmark.py --mock` / `judge.py mock_judge` 同一种"只验管线通不通"的姿态。

## 依赖姿态（为什么这样隔离）

本仓库的宪法是**纯 Python 标准库、零 pip 依赖、不联网、不调 LLM/API**。LlamaIndex 是重型第三方依赖，通常要联网 + embedding/LLM。因此：

- **LlamaIndex 是 opt-in，不是仓库依赖**。它只出现在 `llamaindex_backend.py` 的**方法内部**（惰性 import），并有一份**独立的** `requirements-real.txt`。repo / skill / benchmark 的任何 requirements 都**不**加它。
- **干净检出上只有两件事会跑**：`--mock` 干跑 和 `tests/` 自测——纯 stdlib、无网络、无密钥。其余（真嵌入、建索引、检索、生成）都需要你 opt-in：`pip install` + `--real` + 自己的 API key + 自己的材料。
- **真跑产物全部 gitignore**（索引、嵌入缓存、`results/`、`config.json`、材料副本），与 benchmark 真跑产物同口径。

## 怎么跑

```bash
# 1) 干跑（默认，纯 stdlib，无 pip/网络/密钥）——用自带 fixtures，零参数即可
python spike/llamaindex_rag/rag.py --mock          # 或直接 python spike/llamaindex_rag/rag.py

# 2) 自测（干净检出即绿）
cd spike/llamaindex_rag && python -m unittest discover -s tests
#   或： python spike/llamaindex_rag/rag.py --self-test

# 3) 真跑（opt-in——需装依赖 + 配密钥；不进 CI，产物 gitignore）
pip install -r spike/llamaindex_rag/requirements-real.txt
cp spike/llamaindex_rag/config.example.json spike/llamaindex_rag/config.json   # 填 openai_api_key
python spike/llamaindex_rag/rag.py --real --config spike/llamaindex_rag/config.json \
       --items <你的 items.jsonl> --materials <你的材料.txt>
```

常用参数：`--backend {mock,llamaindex}`（显式选后端）、`--limit N`（只跑前 N 题）、`--results-dir DIR`。

## 与三臂 benchmark 的关系（不要过度声称）

现有三臂 = 闭卷 / 给全材料无 skill / skill。本 spike **被设计成契约兼容**，因此**将来**可以成为第 4 个「framework RAG」对照臂：

- 读同样的 `config.json` 字段与 `items.jsonl` 金标 schema（`{id, question, gold_answer, supporting_span, source_file, answer_type∈(numeric|definition|factual), answerable, tolerance}`）；
- 每题输出一条**纯答案字符串**（`judge.judge_answer` 消费的形态、`looks_abstained` 可检测），写 `raw.jsonl` 为 `{id, question, rag}`。

但它**尚未接入、尚未真跑、不产出任何经过验证的数字**——**请勿**把它当作已存在的 benchmark 臂，也**不要**引用它的任何准确率/幻觉率。

## 越界弃答（设计目标，非实测结论）

对 `answerable=false` 的越界探针，正确的 RAG 行为是**弃答**：检索最高分低于 `min_score` 时返回 `材料中未涵盖`（`judge.looks_abstained` 命中），对应 benchmark 的"负向拒答"指标。mock 与 real **共享**同一道弃答门（`backend.Backend.answer_for_item`），所以两后端对越界探针的策略一致，真后端在弃答门通过前**绝不触发 LLM**。这是骨架的设计目标，不是实测结论——真值要等你 opt-in 真跑。

## 真跑的几个坑（已在代码里编码）

- **`api_base` 不是 `base_url`**：`OpenAILike(api_base=cfg['openai_api_base'], ...)`。
- **`is_chat_model=True`**：默认 `False` 会打 `/completions` 而 404。
- **deepseek 的 OpenAI 兼容面通常没有 `/embeddings`**：所以嵌入**默认走本地 HF BGE**（`HuggingFaceEmbedding('BAAI/bge-small-en-v1.5')`，无密钥、下载后查询期零网络），别让 starter 默认悄悄回落到需要 `OPENAI_API_KEY` 的 `OpenAIEmbedding`。
- **`min_score` 是 embed-model 相关的**：当前 `0.25` 是针对 mock 的哈希-余弦调的（fixtures 可答项 ≥0.48、越界探针 ≤0.09）。**换成真 BGE / 别的嵌入必须重新调**这个阈值，否则弃答门会误伤或漏放。

## 文件

| 文件 | 作用 |
| :-- | :-- |
| `rag.py` | 纯 stdlib CLI + backend 无关编排器（唯一入口；不 import 任何第三方包） |
| `backend.py` | mock/real 唯一接缝：`Backend` 抽象 + `Chunk` + `make_backend` 工厂 + 共享弃答门 |
| `mock_backend.py` | 纯 stdlib 确定性 stand-in（哈希词袋嵌入 + 余弦检索 + 抽取式生成；停用词过滤） |
| `llamaindex_backend.py` | 惰性真接缝（重依赖只在方法内 import，缺失时明确提示 pip） |
| `contract.py` | stdlib 助手：`DEFAULT_CFG` / `load_config` / `load_jsonl` / `looks_abstained`（不 import benchmark） |
| `fixtures/mini_materials.txt`、`fixtures/mini_items.jsonl` | 自撰迷你语料 + 金标（含 1 道越界探针），使 `--mock` 零参数可跑 |
| `config.example.json` | 配置模板（密钥留空；真 `config.json` gitignore） |
| `requirements-real.txt` | **仅** `--real` 用的 opt-in 依赖，绝不进任何 repo requirements |
| `tests/test_mock.py` | 纯 stdlib 自测：锁 mock 端到端、越界弃答、确定性、以及"mock 路径不 import 重依赖"的隔离守卫 |

---

作者：Siyun Chen。fixtures 为自撰虚构内容，不含任何受版权课程文本。
