#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Drive a real coding agent through a short study session and score the transcript.

Backends:
  antigravity  Google Antigravity's local agent API (the IDE must be running). Models: flash_lite | flash | pro
  claude       Claude Code headless (`claude -p`). Models: e.g. claude-haiku-4-5-20251001, claude-sonnet-5

Examples:
  python eval/agent_smoke.py antigravity --model flash_lite --materials "D:/EEC 160" --install-skill
  python eval/agent_smoke.py claude --model claude-haiku-4-5-20251001 --materials "D:/EEC 160"

Antigravity connection: pass --ls-address / --csrf-token / --project-id or set ANTIGRAVITY_LS_ADDRESS,
ANTIGRAVITY_CSRF_TOKEN, ANTIGRAVITY_PROJECT_ID (see the running language_server.exe command line and
~/.gemini/config/projects/*.json). --install-skill copies SKILL.md + coach into
~/.gemini/antigravity/skills/exam-cram-coach first, the way a student would install it.
Transcripts and scores are written to eval/results/ (git-ignored: they contain course text).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")
SKILL_PATH = os.path.join(ROOT, "SKILL.md")
COMMANDS = {"setup", "status", "next", "chapter", "goto", "ask", "quiz", "check", "answer", "done", "note",
            "mistakes", "cheatsheet", "figures", "figure", "doctor", "help"}
AGY_SKILLS = os.path.expanduser("~/.gemini/antigravity/skills/exam-cram-coach")
AGY_EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\resources\bin\language_server.exe")

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass


def install_skill(dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    shutil.copy(SKILL_PATH, dest)
    shutil.copy(os.path.join(ROOT, "coach.py"), dest)
    shutil.copytree(os.path.join(ROOT, "coach"), os.path.join(dest, "coach"), ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def turns_for(materials, skill_dir, lang):
    coach = os.path.join(skill_dir, "coach.py")
    skill = os.path.join(skill_dir, "SKILL.md")
    if lang == "zh":
        return [
            "我安装了一个技能：请先完整读取 %s ，然后严格按它工作。我的课程资料在 %s 。三天后考试，我零基础，"
            "请用中文从第 1 章开始讲。coach.py 在 %s（python、pypdfium2 已装好）。" % (skill, materials, coach),
            "继续，下一段。",
            "考我第 1 章的第一道作业题（quiz --chapter 1 -n 1）。",
            "我不会做。请直接看参考答案给我讲解，并把答案里的图给我看。",
        ]
    return [
        "I installed a skill: read %s completely first, then follow it exactly. My course materials are in %s . "
        "The exam is in 3 days, I am starting from zero, teach me in English from chapter 1. coach.py is at %s "
        "(python and pypdfium2 are installed)." % (skill, materials, coach),
        "Next slice please.",
        "Quiz me on the first chapter 1 homework question (quiz --chapter 1 -n 1).",
        "I can't do it. Show me the reference answer, explain it, and show me the answer figure.",
    ]


# ---------------------------------------------------------------- antigravity backend

class Antigravity(object):
    def __init__(self, args):
        self.env = dict(os.environ)
        for key, val in (("ANTIGRAVITY_LS_ADDRESS", args.ls_address), ("ANTIGRAVITY_CSRF_TOKEN", args.csrf_token),
                         ("ANTIGRAVITY_PROJECT_ID", args.project_id)):
            if val:
                self.env[key] = val
        self.model = args.model
        self.conv = None

    def _api(self, *argv):
        # call the executable directly: the agentapi.bat wrapper truncates arguments at the first newline
        proc = subprocess.run([AGY_EXE, "agentapi"] + list(argv), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.env, timeout=120)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return {"raw": proc.stdout, "stderr": proc.stderr}

    def transcript_path(self):
        return os.path.expanduser("~/.gemini/antigravity/brain/%s/.system_generated/logs/transcript.jsonl" % self.conv)

    def read_transcript(self):
        p = self.transcript_path()
        if not os.path.exists(p):
            return []
        rows = []
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return rows

    def _wait(self, after_index, timeout):
        t0, stable, last_n = time.time(), 0, -1
        while time.time() - t0 < timeout:
            rows = self.read_transcript()
            n = len(rows)
            done = [r for r in rows if r.get("step_index", -1) > after_index and r.get("source") == "MODEL"
                    and r.get("type") == "PLANNER_RESPONSE" and r.get("status") == "DONE"]
            if done and n == last_n:
                stable += 1
                if stable >= 4:  # ~12 quiet seconds after the last reply
                    return rows
            else:
                stable = 0
            last_n = n
            time.sleep(3)
        return self.read_transcript()

    def send(self, text, first):
        before = len(self.read_transcript()) if self.conv else 0
        if first:
            r = self._api("new-conversation", "--model=%s" % self.model, "--title=exam-cram-smoke", text)
            self.conv = r.get("response", {}).get("newConversation", {}).get("conversationId")
            if not self.conv:
                raise SystemExit("antigravity: could not start conversation: %s" % json.dumps(r)[:600])
            print("conversation", self.conv)
        else:
            r = self._api("send-message", self.conv, text)
            if "error" in r:
                print("send-message:", json.dumps(r)[:300])
        return self._wait(before - 1 if before else -1, timeout=600)

    def to_turns(self, rows):
        turns = []
        for r in rows:
            src, typ, content = r.get("source"), r.get("type"), r.get("content") or ""
            if src == "USER_EXPLICIT":
                turns.append({"role": "user", "text": content})
            elif src == "MODEL" and typ == "PLANNER_RESPONSE":
                turns.append({"role": "assistant", "text": content})
            elif src == "SYSTEM":
                continue
            else:
                turns.append({"role": "tool", "type": typ, "text": content})
        return turns


# ---------------------------------------------------------------- claude backend

class Claude(object):
    def __init__(self, args):
        self.model = args.model
        self.session = None
        self.cwd = args.cwd or ROOT
        self.exe = shutil.which("claude")
        if not self.exe:
            raise SystemExit("claude CLI not found on PATH")

    def send(self, text, first):
        cmd = [self.exe, "-p", text, "--model", self.model, "--output-format", "stream-json", "--verbose",
               "--allowedTools", "Bash(python *),Bash(python3 *),Read,Bash(cd *),Bash(dir *),Bash(ls *)"]
        if first:
            cmd += ["--append-system-prompt", "You are a coding agent with a shell. Run commands instead of describing them."]
        else:
            cmd += ["--resume", self.session]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=self.cwd, timeout=1200)
        events = []
        for line in proc.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        if not events:
            print("claude produced no events; stderr:", proc.stderr[:800])
        for e in events:
            if e.get("session_id"):
                self.session = e["session_id"]
        return events

    def to_turns(self, events):
        turns = []
        for e in events:
            if e.get("type") == "assistant":
                for block in e.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        turns.append({"role": "assistant", "text": block["text"]})
                    elif block.get("type") == "tool_use":
                        turns.append({"role": "tool", "type": block.get("name"), "text": json.dumps(block.get("input"), ensure_ascii=False)})
            elif e.get("type") == "user":
                for block in e.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        c = block.get("content")
                        turns.append({"role": "tool_result", "text": c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:4000]})
        return turns


# ---------------------------------------------------------------- scoring

def score(all_turns):
    cmds, unknown = [], []
    for t in all_turns:
        if t["role"] == "tool":
            for m in re.finditer(r"coach\.py(?:\\+\"|\")?\s+(?:(?:-w|--workspace)\s+(?:\\?\"[^\"]*\\?\"|\S+)\s+)?([a-z]+)", t["text"]):
                cmds.append(m.group(1))
                if m.group(1) not in COMMANDS:
                    unknown.append(m.group(1))
    assistant = "\n".join(t["text"] for t in all_turns if t["role"] == "assistant")
    tool_text = "\n".join(t["text"] for t in all_turns if t["role"] in ("tool", "tool_result"))
    # some hosts log only a command's output, not its text: recognise commands by their output banner
    for name, sig in (("setup", r"工作区已建好|Workspace ready"), ("next", r"=== (?:第 \d+ 章|Chapter \d+)"),
                      ("quiz", r"=== (?:测验|Quiz):"), ("check", r"--- (?:参考答案|Reference answer) ---"),
                      ("answer", r"(?:已记录|Recorded): q\d+")):
        if name not in cmds and re.search(sig, tool_text):
            cmds.append(name)
    cites = re.findall(r"[\w\-() ]+\.(?:pdf|pptx|docx|md|txt)\s*,?\s*(?:p\.?|page|第)\s*\d+", assistant, re.I)
    cjk = len(re.findall(r"[一-鿿]", assistant))
    return {
        "commands_run": cmds,
        "unknown_commands": unknown,
        "ran_setup": "setup" in cmds, "ran_next": "next" in cmds, "ran_quiz": "quiz" in cmds,
        "ran_check": "check" in cmds, "ran_answer": "answer" in cmds,
        "citations": len(cites),
        "labels": {"green": assistant.count("🟢"), "yellow": assistant.count("🟡"), "warn": assistant.count("⚠️")},
        "images_embedded_in_reply": len(re.findall(r"!\[[^\]]*\]\([^)]*\.png\)", assistant)),
        "image_files_opened_by_tools": len(set(re.findall(r"[\w\-]+\.png", tool_text))),
        "assistant_chars": len(assistant),
        "reply_is_chinese": cjk > len(assistant) * 0.2,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", choices=["antigravity", "claude"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--materials", required=True)
    ap.add_argument("--lang", choices=["zh", "en"], default="zh")
    ap.add_argument("--skill-dir", default=ROOT, help="folder holding SKILL.md and coach.py the agent should use")
    ap.add_argument("--install-skill", action="store_true", help="copy the skill into Antigravity's skills folder and use it from there")
    ap.add_argument("--ls-address")
    ap.add_argument("--csrf-token")
    ap.add_argument("--project-id")
    ap.add_argument("--cwd")
    ap.add_argument("--turns", type=int, default=4)
    args = ap.parse_args()

    skill_dir = install_skill(AGY_SKILLS) if args.install_skill else os.path.abspath(args.skill_dir)
    backend = Antigravity(args) if args.backend == "antigravity" else Claude(args)
    os.makedirs(RESULTS, exist_ok=True)
    tag = "%s_%s_%s" % (args.backend, args.model, time.strftime("%Y%m%d_%H%M%S"))
    all_turns = []
    for i, text in enumerate(turns_for(os.path.abspath(args.materials), skill_dir, args.lang)[: args.turns]):
        print("== turn %d: %s" % (i + 1, text[:90].replace("\n", " ")))
        raw = backend.send(text, first=(i == 0))
        turns = backend.to_turns(raw)
        if args.backend == "antigravity":
            all_turns = turns  # the transcript already holds every turn
        else:
            all_turns += turns
        last = [t for t in turns if t["role"] == "assistant"]
        print((last[-1]["text"] if last else "(no reply)")[:1500])
        with open(os.path.join(RESULTS, tag + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"backend": args.backend, "model": args.model, "turns": all_turns, "score": score(all_turns)}, fh, ensure_ascii=False, indent=1)
    print(json.dumps(score(all_turns), ensure_ascii=False, indent=1))
    print("saved", os.path.join(RESULTS, tag + ".json"))


if __name__ == "__main__":
    main()
