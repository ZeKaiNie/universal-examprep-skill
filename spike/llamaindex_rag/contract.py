# -*- coding: utf-8 -*-
"""Standalone stdlib helpers for the RAG spike — mirror the benchmark's config/item field NAMES
without importing anything from benchmark/ (the spike stays self-contained).

Nothing here imports a third-party package. The abstain marker + looks_abstained() are re-implemented
locally (NOT imported from benchmark/judge.py) so detection stays inside the spike.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# RAG 在检索太弱（含越界探针）时输出 ABSTAIN → looks_abstained 命中。
ABSTAIN = "材料中未涵盖"
# _ABSTAIN_MARKERS 与 looks_abstained 是 benchmark/judge.py 的 ABSTAIN_MARKERS + looks_abstained 的
# **逐字镜像**（含 5 个英文标记，归一只 .lower() 不去空格）——本地复制以保持 spike 自包含（不 import
# judge.py），改动其一必须同步另一处，否则真臂英文弃答会被漏计。
_ABSTAIN_MARKERS = (
    "材料中未涵盖", "材料未涵盖", "无法确定", "不确定", "未提及", "没有提到",
    "not covered", "cannot determine", "not in the material", "i don't know", "not sure",
)

# fixtures 作为默认 items/materials，使得裸 `python rag.py --mock` 无需任何外部参数即可跑通。
# 字段名沿用 benchmark/config.example.json；backend/top_k/min_score/chunk_* 为 spike 本地扩展。
DEFAULT_CFG = {
    "items_path": os.path.join(HERE, "fixtures", "mini_items.jsonl"),
    "materials_text": os.path.join(HERE, "fixtures", "mini_materials.txt"),
    "skill_workspace": "",
    "results_dir": os.path.join(HERE, "results"),
    "generator_cmd": "",
    "generator_model": "",
    "judge_cmd": "",
    "judge_model": "",
    "mock": True,
    "backend": None,               # None → 由 mock 标志推断；也可显式 "mock"/"llamaindex"
    "openai_api_key": "",
    "openai_api_base": "https://api.deepseek.com/v1",
    "top_k": 4,
    "min_score": 0.25,             # 针对 mock 的哈希-余弦调过（fixtures 可答项≥0.48、越界探针≤0.09）；换真 embed_model 必须重调（README 有说明）
    "chunk_size": 512,
    "chunk_overlap": 64,
    "context_window": 32768,
    "embed_model": "BAAI/bge-small-en-v1.5",
}


def load_config(args):
    """DEFAULT_CFG ← 可选 config 文件 ← CLI 覆盖。优先级：CLI --backend > CLI --mock/--real > config > 默认。"""
    cfg = dict(DEFAULT_CFG)
    path = getattr(args, "config", None)
    if path:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise SystemExit("[-] config 顶层必须是对象：%s" % path)
        cfg.update(loaded)
        # config 里的相对路径按 **config 文件所在目录** 解析（不是 cwd）——否则从仓库根跑真跑，
        # results_dir="results" 会写到未被 gitignore 的仓库根 results/，破坏隔离。
        cfg_dir = os.path.dirname(os.path.abspath(path))
        for k in ("items_path", "materials_text", "results_dir"):
            v = cfg.get(k)
            if isinstance(v, str) and v and not os.path.isabs(v):
                cfg[k] = os.path.join(cfg_dir, v)
    # CLI 覆盖（路径按 cwd，标准）——CLI 显式给的胜过 config。
    for arg_name, cfg_key in (("items", "items_path"), ("materials", "materials_text"),
                              ("results_dir", "results_dir"), ("backend", "backend")):
        v = getattr(args, arg_name, None)
        if v is not None:
            cfg[cfg_key] = v
    # --mock/--real 必须能盖过 config 里的 backend——否则 config backend:llamaindex + --mock 仍走真跑、
    # config backend:mock + --real 静默跑 mock 却打印 real。显式 --backend 最高优先则保留。
    backend_from_cli = getattr(args, "backend", None) is not None
    if getattr(args, "real", False):
        cfg["mock"] = False
        if not backend_from_cli:
            cfg["backend"] = None
    if getattr(args, "mock", False):
        cfg["mock"] = True
        if not backend_from_cli:
            cfg["backend"] = None
    return cfg


def load_jsonl(path):
    """读金标集：跳过空行与 # 注释行（与 run_benchmark 同口径）。"""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(json.loads(s))
    return out


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def looks_abstained(answer):
    """本地弃答检测（不 import judge.py，但与其 looks_abstained 逐字等价：.lower() 后子串命中）。"""
    a = (answer or "").lower()
    return any(m.lower() in a for m in _ABSTAIN_MARKERS)
