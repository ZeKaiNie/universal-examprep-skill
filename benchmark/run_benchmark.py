#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hallucination benchmark runner for the exam-cram skill.

Paired design: every gold item is answered by BOTH arms —
  * baseline : a vanilla `claude -p` told to answer strictly from the raw materials;
  * skill    : `claude -p` run inside the skill workspace, where the skill's file-locked
               references/wiki/ + quiz_bank.json are live (its anti-hallucination regime).
Then judge.py scores each answer (deterministic for numeric, LLM-judge for the rest) and
stats.py runs the paired tests. Output: results/raw.jsonl + results/report.md.

Run it WITHOUT spending any Claude quota first:
    python run_benchmark.py --mock
Then for real (uses your logged-in Claude Code subscription, no API key needed):
    python run_benchmark.py --config config.json

This is a SCAFFOLD: the --mock path proves the pipeline end-to-end; the real path shells
out to `claude -p`. Verify the exact `claude` flags against your installed version.
"""

import os
import sys
import json
import argparse
import subprocess

import judge as J
import report as R

# 在 Windows 默认 GBK 控制台上避免中文状态输出变成乱码
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

BASELINE_PROMPT = (
    "请只依据下面的【课程材料】回答问题；材料中没有的内容，请直接回答“材料中未涵盖”，不要编造。\n\n"
    "【课程材料】\n{material}\n\n【问题】{q}\n\n请直接给出简洁答案。"
)
SKILL_PROMPT = (
    "你是备考教练。请依据本工作区已建立的 references/wiki/ 知识库与 references/quiz_bank.json 题库回答问题；"
    "材料未涵盖的内容请回答“材料中未涵盖”，不要现场编造或重新推导。\n\n【问题】{q}\n\n请直接给出简洁答案。"
)

DEFAULT_CFG = {
    "items_path": "items/items.jsonl",
    "materials_text": "materials/_combined.txt",   # baseline arm reads this
    "skill_workspace": "skill_workspace",            # skill arm runs here
    "results_dir": "results",
    "generator_cmd": "claude", "generator_model": "",
    "judge_cmd": "claude", "judge_model": "",
    "allowed_tools": "Read Glob Grep",
    "judge_repeats": 3,
    "mock": False,
}


# ---------------- io ----------------
def load_jsonl(path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.exit(f"[-] {path}:{ln} 不是合法 JSON：{e}")
    if not items:
        sys.exit(f"[-] {path} 里没有任何题目。请先按 items/README.md 准备金标集。")
    return items


def read_text(path):
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# ---------------- generators ----------------
def run_claude(prompt, cwd, cmd, model, allowed_tools, timeout=600):
    args = [cmd, "-p", prompt, "--output-format", "json"]
    if model:
        args += ["--model", model]
    if allowed_tools:
        args += ["--allowedTools", *allowed_tools.split()]
    proc = subprocess.run(args, cwd=cwd or ".", capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)
    try:
        data = json.loads(proc.stdout)
        return data.get("result", proc.stdout) or "", data.get("total_cost_usd")
    except json.JSONDecodeError:
        return (proc.stdout or proc.stderr or "").strip(), None


def mock_generate(item, arm):
    """Deterministic fake answers so --mock exercises the whole pipeline."""
    if not item.get("answerable", True):
        return "材料中未涵盖该内容。" if arm == "skill" else "根据推测，答案大概是某个值。"
    if arm == "skill":
        return str(item.get("gold_answer", ""))            # skill reads the fixed answer
    # baseline: deterministically wrong on ~half the items
    if sum(ord(c) for c in item["id"]) % 2 == 0:
        return str(item.get("gold_answer", ""))
    if item.get("answer_type") == "numeric":
        try:
            return f"约等于 {float(item['gold_answer']) * 1.5:.2f}"
        except (TypeError, ValueError):
            return "无法确定具体数值"
    return "（基线模型自行推导，可能与材料不符的内容）"


def make_generator(cfg):
    if cfg["mock"]:
        return mock_generate
    materials = read_text(cfg["materials_text"])
    if not materials:
        print("[!] 警告：未找到 materials_text，基线臂将缺少课程材料；请先准备 materials/_combined.txt")

    def gen(item, arm):
        if arm == "skill":
            return run_claude(SKILL_PROMPT.format(q=item["question"]), cfg["skill_workspace"],
                              cfg["generator_cmd"], cfg["generator_model"], cfg["allowed_tools"])[0]
        return run_claude(BASELINE_PROMPT.format(material=materials, q=item["question"]), ".",
                          cfg["generator_cmd"], cfg["generator_model"], "")[0]
    return gen


def make_judge(cfg):
    if cfg["mock"]:
        return J.mock_judge
    return lambda prompt: run_claude(prompt, ".", cfg["judge_cmd"], cfg["judge_model"], "")[0]


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser(description="exam-cram skill 防幻觉 benchmark 运行器")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--mock", action="store_true", help="不调用 Claude，用占位答案跑通全流程")
    ap.add_argument("--items", default=None, help="覆盖 items_path")
    args = ap.parse_args()

    cfg = dict(DEFAULT_CFG)
    if os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    elif not args.mock:
        print(f"[!] 未找到 {args.config}，使用默认配置（可复制 config.example.json）")
    if args.mock:
        cfg["mock"] = True
    if args.items:
        cfg["items_path"] = args.items

    items = load_jsonl(cfg["items_path"])
    generate, ask_judge = make_generator(cfg), make_judge(cfg)
    os.makedirs(cfg["results_dir"], exist_ok=True)

    scored, raw = [], []
    for i, item in enumerate(items, 1):
        if "id" not in item or "question" not in item:
            sys.exit(f"[-] 第 {i} 题缺少 id 或 question 字段。")
        a_base, a_skill = generate(item, "baseline"), generate(item, "skill")
        j_base = J.judge_answer(item, a_base, ask_judge, cfg["judge_repeats"])
        j_skill = J.judge_answer(item, a_skill, ask_judge, cfg["judge_repeats"])
        scored.append({"id": item["id"], "baseline": j_base, "skill": j_skill})
        raw.append({"id": item["id"], "question": item["question"],
                    "baseline_answer": a_base, "skill_answer": a_skill,
                    "baseline_score": j_base, "skill_score": j_skill})
        print(f"[+] ({i}/{len(items)}) {item['id']} 已评分")

    with open(os.path.join(cfg["results_dir"], "raw.jsonl"), "w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    R.generate(scored, cfg, cfg["results_dir"])
    print(f"\n[+] 完成。结果目录: {cfg['results_dir']}/ "
          f"（report.html 直接用浏览器打开看图；report.md / raw.jsonl 为数据）")


if __name__ == "__main__":
    main()
