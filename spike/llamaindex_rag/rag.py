# -*- coding: utf-8 -*-
"""LlamaIndex RAG 实验 spike —— standalone CLI + backend-agnostic orchestrator (stdlib-ONLY file).

裸 `python spike/llamaindex_rag/rag.py`（== --mock）在自带 fixtures 上端到端跑通 MockBackend，
无需 pip / 网络 / 密钥、无需任何外部参数。真跑（--real / --backend llamaindex）为 opt-in。

诚实边界：这是一个实验骨架，未接入 skill 也未接入 benchmark 主线；--mock 是确定性 stand-in，
只验管线通不通，**不测量任何正确率**。详见同目录 README.md。

NO third-party import anywhere in this file. 不 import run_benchmark/judge/report；
不写入 benchmark/results 或 skill_workspace。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

import contract                     # noqa: E402  同目录 stdlib 助手
import backend as B                 # noqa: E402  mock/real 分支点


def build_argparser():
    ap = argparse.ArgumentParser(
        description="LlamaIndex RAG 实验 spike（standalone；--mock 纯 stdlib 离线干跑）")
    ap.add_argument("--mock", action="store_true",
                    help="默认。纯 stdlib 离线干跑（无 pip / 网络 / 密钥）")
    ap.add_argument("--real", action="store_true",
                    help="opt-in 真跑（需 requirements-real.txt + config 里的 openai_api_key）")
    ap.add_argument("--backend", choices=["mock", "llamaindex"], default=None,
                    help="显式指定后端（覆盖 --mock/--real 推断）")
    ap.add_argument("--config", default=None, help="config.json（字段名同 benchmark）")
    ap.add_argument("--items", default=None, help="items.jsonl（默认自带 fixtures，只读）")
    ap.add_argument("--materials", default=None, help="材料文本（默认自带 fixtures，只读）")
    ap.add_argument("--results-dir", dest="results_dir", default=None,
                    help="输出目录（默认 spike 本地 results/，已 gitignore）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 题（快速干跑）")
    ap.add_argument("--self-test", action="store_true", dest="self_test",
                    help="mock 跑 fixtures 并断言越界探针弃答，失败非零退出")
    return ap


def run(cfg, limit=None):
    items = contract.load_jsonl(cfg["items_path"])
    materials = contract.read_text(cfg["materials_text"])
    bk = B.make_backend(cfg)                     # mock 在任何重依赖 import 之前短路
    if limit is not None:
        items = items[: max(limit, 0)]
    records = []
    for it in items:
        ans = bk.answer_for_item(it, materials)
        records.append({"id": it.get("id"), "question": it.get("question"), "rag": ans})
    return records


def _self_test(cfg):
    cfg = dict(cfg)
    cfg["mock"], cfg["backend"] = True, "mock"
    records = run(cfg)
    items = {it["id"]: it for it in contract.load_jsonl(cfg["items_path"])}
    ok = True
    for r in records:
        it = items.get(r["id"], {})
        abst = contract.looks_abstained(r["rag"])
        if it.get("answerable") is False and not abst:
            print("[-] self-test 失败：越界探针未弃答 id=%s → %r" % (r["id"], r["rag"]))
            ok = False
        if it.get("answerable") is not False and abst:
            print("[-] self-test 失败：可答题被弃答 id=%s" % r["id"])
            ok = False
    print("[+] self-test 通过（mock 纯 stdlib，越界探针弃答）" if ok else "[-] self-test 未通过")
    return 0 if ok else 1


def main(argv=None):
    args = build_argparser().parse_args(argv)
    cfg = contract.load_config(args)
    if args.self_test:
        return _self_test(cfg)
    # 一切从**实际解析出的后端名**推导（而非零散的 mock 标志）——否则 --backend mock + --real
    # 会跑 mock 却把 summary/tag 标成 real。
    backend_name = B.resolve_backend_name(cfg)
    is_mock = (backend_name == "mock")
    # 真跑前置：密钥缺失就明确报错（mock 永远不读密钥）。
    if not is_mock and not cfg.get("openai_api_key"):
        raise SystemExit("[-] 真跑需要 openai_api_key（写进 config.json；--mock 不需要）")

    records = run(cfg, limit=args.limit)

    results_dir = cfg["results_dir"]
    os.makedirs(results_dir, exist_ok=True)
    contract.write_jsonl(os.path.join(results_dir, "raw.jsonl"), records)
    contract.write_jsonl(os.path.join(results_dir, "answers.jsonl"),
                         [{"id": r["id"], "rag": r["rag"]} for r in records])
    abstained = sum(1 for r in records if contract.looks_abstained(r["rag"]))
    summary = {
        "n": len(records),
        "abstained": abstained,
        "answered": len(records) - abstained,
        "backend": backend_name,
        "mock": is_mock,
        "note": "占位提示：未测量正确率 / placeholder, no correctness measured",
    }
    with open(os.path.join(results_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    tag = "mock 占位" if is_mock else "real"
    print("[+] %s 运行完成：%d 题，其中 %d 题弃答（材料未涵盖），结果写入 %s（未测量正确率）"
          % (tag, summary["n"], summary["abstained"], results_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
