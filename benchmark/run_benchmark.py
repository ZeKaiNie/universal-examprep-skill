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
import urllib.request
import urllib.error
import time

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
    "gemini_api_key": "",
    "openai_api_key": "",
    "openai_api_base": "https://api.deepseek.com/v1"
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


# ---------------- API Helpers ----------------
def run_gemini_api(prompt, api_key, model="gemini-2.5-flash", format_json=False):
    if not api_key:
        sys.exit("[-] 错误：选择 'gemini' 作为 generator 或 judge 时，必须在 config.json 中配置 'gemini_api_key'。")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    if format_json:
        payload["generationConfig"] = {
            "responseMimeType": "application/json"
        }
        
    data = json.dumps(payload).encode("utf-8")
    
    max_retries = 10
    backoff_factor = 2
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return text
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                err_msg = e.read().decode("utf-8")
                sleep_time = (backoff_factor ** attempt) + 5
                print(f"[!] 触发 API 限制/暂不可用 ({e.code}): {err_msg}\n等待 {sleep_time} 秒后重试...")
                time.sleep(sleep_time)
                continue
            else:
                err_msg = e.read().decode("utf-8")
                print(f"[-] Gemini API 错误 (HTTP {e.code}): {err_msg}")
                raise e
        except Exception as e:
            print(f"[-] API 请求异常: {e}")
            raise e
            
    sys.exit("[-] 达到最大重试次数，Gemini API 调用失败。")


def run_openai_api(prompt, api_key, api_base="https://api.deepseek.com/v1", model="deepseek-chat", format_json=False):
    if not api_key:
        sys.exit("[-] 错误：选择 'openai' 或 'deepseek' 作为 generator 或 judge 时，必须在 config.json 中配置 'openai_api_key'。")
        
    url = f"{api_base.rstrip('/')}/chat/completions"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0
    }
    
    if format_json:
        payload["response_format"] = {
            "type": "json_object"
        }
        
    data = json.dumps(payload).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    
    max_retries = 5
    backoff_factor = 2
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["choices"][0]["message"]["content"]
                return text
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504):
                sleep_time = (backoff_factor ** attempt) + 2
                print(f"[!] 触发 API 限制/错误 ({e.code})，等待 {sleep_time} 秒后重试...")
                time.sleep(sleep_time)
                continue
            else:
                err_msg = e.read().decode("utf-8")
                print(f"[-] API 错误 (HTTP {e.code}): {err_msg}")
                raise e
        except Exception as e:
            print(f"[-] API 请求异常: {e}")
            raise e
            
    sys.exit("[-] 达到最大重试次数，API 调用失败。")


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

    # Pre-load quiz bank lookup table to emulate skill's lazy-load retrieval
    quiz_bank_by_id = {}
    if cfg["generator_cmd"] in ("gemini", "openai", "deepseek"):
        qb_path = os.path.join(cfg["skill_workspace"], "references", "quiz_bank.json")
        if os.path.exists(qb_path):
            try:
                with open(qb_path, "r", encoding="utf-8") as f:
                    qb_list = json.load(f)
                    quiz_bank_by_id = {q["id"]: q for q in qb_list}
            except Exception as e:
                print(f"[!] 警告：加载 quiz_bank.json 失败: {e}")

    def gen(item, arm):
        if arm == "skill" and cfg["generator_cmd"] in ("gemini", "openai", "deepseek"):
            # Find matching chapter
            chapter = 1
            q_id = item["id"]
            if q_id in quiz_bank_by_id:
                chapter = quiz_bank_by_id[q_id].get("chapter", 1)
            
            # Search for wiki file `references/wiki/ch{chapter}_*.md`
            wiki_dir = os.path.join(cfg["skill_workspace"], "references", "wiki")
            wiki_content = ""
            if os.path.exists(wiki_dir):
                for f in os.listdir(wiki_dir):
                    if f.startswith(f"ch{chapter}_") and f.endswith(".md"):
                        with open(os.path.join(wiki_dir, f), "r", encoding="utf-8") as wf:
                            wiki_content = wf.read()
                        break
            
            # Construct the prompt imitating the skill behavior
            prompt = (
                "你是备考教练。请依据我们为你提供的相关 Wiki 知识库章节内容和题库解析回答问题；"
                "材料未涵盖的内容请直接回答“材料中未涵盖”，不要现场编造或重新推导。\n\n"
                f"【相关 Wiki 章节内容】\n{wiki_content}\n\n"
                f"【问题】{item['question']}\n\n请直接给出简洁答案。"
            )
            
            if cfg["generator_cmd"] == "gemini":
                # Add delay to avoid QPS issues
                time.sleep(13)
                return run_gemini_api(prompt, cfg["gemini_api_key"], cfg.get("generator_model", "gemini-2.5-flash"), format_json=False)
            else:
                time.sleep(0.5)
                return run_openai_api(
                    prompt,
                    cfg["openai_api_key"],
                    cfg.get("openai_api_base", "https://api.deepseek.com/v1"),
                    cfg.get("generator_model", "deepseek-chat"),
                    format_json=False
                )
        
        # Baseline arm or other commands
        if cfg["generator_cmd"] == "gemini":
            prompt = BASELINE_PROMPT.format(material=materials, q=item["question"])
            time.sleep(13)
            return run_gemini_api(prompt, cfg["gemini_api_key"], cfg.get("generator_model", "gemini-2.5-flash"), format_json=False)
        elif cfg["generator_cmd"] in ("openai", "deepseek"):
            prompt = BASELINE_PROMPT.format(material=materials, q=item["question"])
            time.sleep(0.5)
            return run_openai_api(
                prompt,
                cfg["openai_api_key"],
                cfg.get("openai_api_base", "https://api.deepseek.com/v1"),
                cfg.get("generator_model", "deepseek-chat"),
                format_json=False
            )
        else:
            if arm == "skill":
                return run_claude(SKILL_PROMPT.format(q=item["question"]), cfg["skill_workspace"],
                                  cfg["generator_cmd"], cfg["generator_model"], cfg["allowed_tools"])[0]
            return run_claude(BASELINE_PROMPT.format(material=materials, q=item["question"]), ".",
                              cfg["generator_cmd"], cfg["generator_model"], "")[0]
    return gen


def make_judge(cfg):
    if cfg["mock"]:
        return J.mock_judge
    if cfg["judge_cmd"] == "gemini":
        def judge(prompt):
            time.sleep(13)
            return run_gemini_api(prompt, cfg["gemini_api_key"], cfg.get("judge_model", "gemini-2.5-flash"), format_json=True)
        return judge
    elif cfg["judge_cmd"] in ("openai", "deepseek"):
        def judge(prompt):
            time.sleep(0.5)
            return run_openai_api(
                prompt,
                cfg["openai_api_key"],
                cfg.get("openai_api_base", "https://api.deepseek.com/v1"),
                cfg.get("judge_model", "deepseek-chat"),
                format_json=True
            )
        return judge
    else:
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
