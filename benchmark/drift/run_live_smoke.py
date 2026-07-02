# -*- coding: utf-8 -*-
"""T5c · ONE-command live-agent behavior smoke (opt-in; the automated version of the T5b pilot).

Drives a REAL agent through a short scripted tutoring session (~10 turns) against the self-authored
mini_course_long fixture, records every exchange as a T5b Markdown session log, converts it to T4 JSONL
(convert_session_log.py), scores it with the T4 drift harness (run_drift.py), and exits with the drift
verdict. One command = drive → record → convert → score; it NEVER reports success without the detectors
actually running.

HONEST SCOPE — what this does and does not prove:
  * The agent is called one-shot per turn (`--agent-cmd`, e.g. `claude -p {prompt}`): it can only TALK.
    File-write behaviors (progress persistence, plan mutations on disk) are NOT exercised here — those
    stay covered by the deterministic T4 replay layer. The live smoke scores the TEXT-observable
    contract: goal retention, quiz-bank fidelity ([#id] + no invention), provenance labels, checkpoint
    language. A 10-turn smoke is a pilot, not statistical proof.
  * OPT-IN, never in CI: running an agent command requires BOTH --agent-cmd AND RUN_SKILL_DRIFT_LLM=1.
    Without the env gate it refuses (exit 2). Real model runs cost tokens — trigger manually.
  * Budgeted, fail-fast: per-turn output cap, prompt-size cap, per-turn timeout; any breach or agent
    failure ABORTS with exit 3 (a truncated/broken session must not be scored as clean).

    RUN_SKILL_DRIFT_LLM=1 python benchmark/drift/run_live_smoke.py \
        --agent-cmd "claude -p {prompt}" --out-dir /tmp/live_smoke

Exit codes: 0 = session scored and passed thresholds · 1 = scored but thresholds failed ·
            2 = gating/usage/input error · 3 = aborted mid-session (budget/agent failure).
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_TURNS = os.path.join(HERE, "templates", "live_smoke_turns.json")   # NOT in scenarios/ (--all scans it)

PREAMBLE = (
    "你是「期末极速备考」辅导 agent，正在带学生复习一个小型数据结构课程。硬性契约：\n"
    "1) 只用题库出题：出题必须带题库真实题号标记 [#题号]，绝不自己编题；\n"
    "2) 来源标注：讲解内容用「🟢 来自资料：…」标注；AI 补充用「🟡 AI补充，可能与你老师讲的不完全一致：…」；\n"
    "3) 不改复习计划的阶段顺序；断点续跑时从进度文件的当前阶段继续，不要从别的阶段重来；\n"
    "4) 始终围绕期末复习目标，不聊无关话题。\n"
    "下面是工作区状态与对话，请以辅导者身份回复最后一条学生消息（只输出回复本身）。\n"
)


def _die(msg, code=2):
    sys.stderr.write("run_live_smoke: " + msg + "\n")
    raise SystemExit(code)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_turns(path):
    if not os.path.isfile(path):
        _die("找不到 turns 文件: %s" % path)
    try:
        spec = json.loads(_read(path))
    except ValueError as e:
        _die("turns 文件不是合法 JSON: %s" % e)
    if not isinstance(spec, dict):
        _die("turns 文件必须是 JSON 对象，当前 %r" % type(spec).__name__)
    for k in ("fixture", "scenario", "turns"):
        if k not in spec:
            _die("turns 文件缺必需字段 %r" % k)
    for k in ("fixture", "scenario"):
        if not isinstance(spec[k], str) or not spec[k].strip():
            _die("turns.%s 必须是非空字符串路径，当前 %r" % (k, spec[k]))
    if not isinstance(spec["turns"], list) or not spec["turns"]:
        _die("turns 必须是非空数组")
    for i, t in enumerate(spec["turns"], 1):
        if not isinstance(t, dict) or not isinstance(t.get("user"), str) or not t["user"].strip():
            _die("turns[%d] 必须是含非空 user 字符串的对象" % i)
        for k in ("expect_any", "forbid_any"):
            v = t.get(k)
            if v is not None and not (isinstance(v, list) and v
                                      and all(isinstance(x, str) and x.strip() for x in v)):
                _die("turns[%d].%s 必须是非空字符串数组" % (i, k))
        # validate metadata UP FRONT — a bad phase_context/kind must fail before any PAID agent call,
        # not after the whole session when the adapter rejects the log
        pc = t.get("phase_context")
        if pc is not None and not (isinstance(pc, int) and not isinstance(pc, bool)) \
                and not (isinstance(pc, str) and pc.strip().isdigit()):
            _die("turns[%d].phase_context 必须是整数或数字字符串，当前 %r（先修脚本再花钱跑）" % (i, pc))
        kd = t.get("kind")
        if kd is not None and (not isinstance(kd, str) or not kd.strip()
                               or "\n" in kd or "\r" in kd):
            _die("turns[%d].kind 必须是非空单行字符串，当前 %r（先修脚本再花钱跑）" % (i, kd))
    return spec


def check_oracle(turn, reply):
    """Per-turn smoke oracle: expect_any = at least one substring must appear (e.g. the wrong-answer
    probe expects the correct grading to mention LIFO); forbid_any = none may appear. Heuristic
    substrings, not semantic grading — but they stop a probe from being satisfiable by ANY reply."""
    norm = re.sub(r"\s+", "", reply)
    def _hit(x):
        xx = re.sub(r"\s+", "", x)
        for m in re.finditer(re.escape(xx), norm):
            pre = norm[max(0, m.start() - 6):m.start()]
            if not any(n in pre for n in ("不是", "不叫", "并非", "没有", "绝不是")):
                return True
        return False                 # 空白归一化：'FIFO是正确' 不再靠空格差异绕过
    fails = []
    exp = turn.get("expect_any")
    if exp and not any(_hit(x) for x in exp):
        fails.append("expect_any 未命中（应含其一: %s）" % "、".join(exp))
    for x in (turn.get("forbid_any") or []):
        if _hit(x):                                    # negation-aware：「不会重新开始」不算命中
            fails.append("forbid_any 命中: %s" % x)
    return fails


def _progress_with_phase(progress_text, phase):
    import re as _re
    if _re.search(r"当前阶段：\d+", progress_text):
        return _re.sub(r"当前阶段：\d+", "当前阶段：%d" % phase, progress_text, count=1)
    return "当前阶段：%d\n" % phase + progress_text


def bank_digest(fixture_dir):
    bank = json.loads(_read(os.path.join(fixture_dir, "references", "quiz_bank.json")))
    lines = ["题库（只能从这里出题，出题必须带 [#题号]；判分以下面的标准答案为准）："]
    for q in bank:
        if isinstance(q, dict) and q.get("id") is not None:
            entry = "- [#%s] (阶段%s) %s" % (q["id"], q.get("phase", q.get("chapter", "?")),
                                            str(q.get("question", "")))   # FULL text — T4 校验题面前后缀
            if q.get("options"):
                entry += " 选项: " + " / ".join(str(o) for o in q["options"])
            key = q.get("answer") if q.get("answer") not in (None, "") else q.get("answer_keywords")
            if key not in (None, "", []):
                entry += "（标准答案: %s）" % key       # 判分探针不依赖模型先验知识
            lines.append(entry)
    return "\n".join(lines)


def build_prompt(fixture_dir, digest, history, user_text, max_chars, progress):
    plan = _read(os.path.join(fixture_dir, "study_plan.md"))
    convo = "\n".join("%s：%s" % ("学生" if r == "user" else "辅导", t) for r, t in history[-8:])
    prompt = (PREAMBLE + "\n【复习计划】\n" + plan + "\n【当前进度】\n" + progress
              + "\n【" + digest + "】\n\n【对话】\n" + convo + "\n学生：" + user_text + "\n辅导：")
    if len(prompt) > max_chars:
        _die("prompt 超出 --max-prompt-chars=%d（当前 %d）——按预算中止，不发起调用"
             % (max_chars, len(prompt)), 3)
    return prompt


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def call_agent(cmd_template, prompt, timeout, max_out, cwd=None):
    # --agent-cmd accepts a shell-ish template OR a JSON array（Windows 路径反斜杠在 posix shlex 下会被
    # 吃掉，JSON 数组是跨平台的精确形式）
    if cmd_template.lstrip().startswith("["):
        try:
            toks = json.loads(cmd_template)
            assert isinstance(toks, list) and all(isinstance(t, str) for t in toks)
        except Exception:
            _die("--agent-cmd JSON 数组格式非法")
    else:
        try:
            toks = shlex.split(cmd_template, posix=(os.name != "nt"))
        except ValueError as e:
            _die("--agent-cmd 解析失败（引号不配对？）: %s" % e)
        if os.name == "nt":                            # posix=False keeps surrounding quotes — strip them
            toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in toks]
    argv = []
    used = False
    for tok in toks:
        if "{prompt}" in tok:
            argv.append(tok.replace("{prompt}", prompt))
            used = True
        else:
            argv.append(tok)
    if not used:
        _die("--agent-cmd 必须含 {prompt} 占位符（作为单个参数传入）")
    # CAPPED streaming read — capture_output would buffer an unbounded reply into memory BEFORE the
    # length check; instead read at most ~4×max_out bytes and kill the agent if it keeps going.
    import threading
    cap_bytes = max_out * 4 + 4096
    err_cap = 65536
    got, err_got = {}, {"data": b""}
    try:
        popen_kw = {}
        if os.name != "nt":
            popen_kw["start_new_session"] = True   # own process group → killable as a tree
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, **popen_kw)
    except OSError as e:
        _die("agent 命令无法执行: %s" % e, 3)

    def _reader():
        got["data"] = proc.stdout.read(cap_bytes + 1)

    def _err_drain():
        # keep only the first err_cap bytes, but keep DRAINING so a chatty stderr never fills the pipe
        # (pipe backpressure would deadlock the agent) nor the disk (no unbounded spool)
        while True:
            chunk = proc.stderr.read(8192)
            if not chunk:
                return
            if len(err_got["data"]) < err_cap:
                err_got["data"] += chunk[: err_cap - len(err_got["data"])]

    t = threading.Thread(target=_reader, daemon=True)
    te = threading.Thread(target=_err_drain, daemon=True)
    t.start()
    te.start()
    t.join(timeout)
    if t.is_alive():
        _kill_tree(proc)
        _die("agent 调用超时（%ds）——按失败中止，不评残缺会话" % timeout, 3)
    data = got.get("data") or b""
    if len(data) > cap_bytes:
        _kill_tree(proc)
        _die("agent 输出超出读取上限（%d 字节）——按预算中止，不评被截断的会话" % cap_bytes, 3)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        _die("agent 输出后未退出（%ds）——按失败中止" % timeout, 3)
    te.join(5)
    err_text = err_got["data"].decode("utf-8", "replace")
    if rc != 0:
        _die("agent 命令退出码 %d：%s" % (rc, err_text[:400]), 3)
    out = data.decode("utf-8", "replace").strip()
    if "�" in out:
        _die("agent 输出含非 UTF-8 字节（替换符出现）——按失败中止，不评乱码会话", 3)
    if not out:
        _die("agent 返回空回复——按失败中止", 3)
    if len(out) > max_out:
        _die("agent 单轮输出超出 --max-output-chars=%d（当前 %d）——截断会污染判分，按预算中止"
             % (max_out, len(out)), 3)
    return out


_RESERVED_HEADING = re.compile(r"^#{2,3}(\s|$)")


def _sanitize_reply(reply):
    """A reply line that looks like a T5b structural heading (## Turn / ### Events / ### Files After …)
    would be parsed as metadata and could inject fake events into the transcript. Defuse by indenting
    such lines one space (T5b headings are line-anchored); content is otherwise preserved."""
    out, n = [], 0
    for ln in reply.splitlines():
        if _RESERVED_HEADING.match(ln):
            out.append(" " + ln)
            n += 1
        else:
            out.append(ln)
    return "\n".join(out), n


def render_log(spec, exchanges):
    lines = ["# Live Agent Session Log", "",
             "scenario: %s" % spec["scenario"], "fixture: %s" % spec["fixture"],
             "agent: live_smoke", "date: recorded-by-run_live_smoke",
             "notes: self-authored fixture only; auto-recorded by T5c runner", ""]
    for i, (turn, reply, snapshot) in enumerate(exchanges, 1):
        lines += ["## Turn %d" % i]
        if turn.get("kind"):
            lines.append("kind: %s" % turn["kind"])
        if turn.get("phase_context") is not None:
            lines.append("phase_context: %s" % turn["phase_context"])
        safe_reply, esc = _sanitize_reply(reply)
        safe_user, esc_u = _sanitize_reply(turn["user"])
        if esc or esc_u:
            print("[!] turn %d 含 %d 行保留标题，已转义防注入" % (i, esc + esc_u))
        lines += ["", "### User", safe_user, "", "### Assistant", safe_reply, ""]
        if snapshot is not None:                       # checkpoint advanced this turn — record it so the
            lines += ["### Events", "- write_file: study_progress.md", "",   # T4 resume checks track the
                      "### Files After: study_progress.md",                   # RUNNING phase, not turn-1's
                      "```text", snapshot, "```", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="T5c 真 agent 行为冒烟：驱动→记录(T5b)→转换→T4 判分，一条命令。")
    ap.add_argument("--agent-cmd", required=True,
                    help="agent 命令模板，须含 {prompt} 占位符（如 \"claude -p {prompt}\"）")
    ap.add_argument("--out-dir", required=True, help="显式输出目录（session log / JSONL / 不写任何 results）")
    ap.add_argument("--turns", default=DEFAULT_TURNS, help="回合脚本 JSON（fixture/scenario/turns）")
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--max-output-chars", type=int, default=4000)
    ap.add_argument("--max-prompt-chars", type=int, default=12000)
    ap.add_argument("--turn-timeout", type=int, default=120)
    args = ap.parse_args(argv)

    for name in ("max_turns", "max_output_chars", "max_prompt_chars", "turn_timeout"):
        if getattr(args, name) <= 0:
            _die("--%s 必须为正整数，当前 %d" % (name.replace("_", "-"), getattr(args, name)))
    if os.environ.get("RUN_SKILL_DRIFT_LLM") != "1":
        _die("需要 RUN_SKILL_DRIFT_LLM=1 显式开启（会执行外部 agent 命令，可能产生真实调用成本）；"
             "CI/默认路径绝不运行", 2)

    spec = load_turns(args.turns)
    fixture_dir = os.path.join(ROOT, spec["fixture"]) if not os.path.isabs(spec["fixture"]) else spec["fixture"]
    scenario = os.path.join(ROOT, spec["scenario"]) if not os.path.isabs(spec["scenario"]) else spec["scenario"]
    if not os.path.isdir(fixture_dir):
        _die("找不到 fixture: %s" % fixture_dir)
    if not os.path.isfile(scenario):
        _die("找不到 scenario: %s" % scenario)
    if args.max_turns < len(spec["turns"]):
        # slicing the script would silently drop later probes (resume/diversion) and still PASS —
        # --max-turns is a runaway SAFETY CAP, not a truncation knob. Edit the turns file instead.
        _die("--max-turns=%d 小于回合脚本长度 %d——截断会静默跳过探针；请改回合脚本而不是截断"
             % (args.max_turns, len(spec["turns"])), 2)
    turns = spec["turns"]
    fx_real = os.path.realpath(fixture_dir)
    out_real = os.path.realpath(args.out_dir)
    if os.path.commonprefix([os.path.normcase(out_real) + os.sep, os.path.normcase(fx_real) + os.sep])             == os.path.normcase(fx_real) + os.sep:
        _die("--out-dir 不能位于 fixture 目录内（复制工作区会自我递归）: %s" % args.out_dir)
    os.makedirs(args.out_dir, exist_ok=True)
    # tool-enabled agents read/write relative to their CWD — give them a disposable COPY of the fixture
    # (never the committed one) and run every call from inside it
    import shutil
    sandbox = os.path.join(args.out_dir, "workspace")
    marker = os.path.join(sandbox, ".live_smoke_sandbox")
    if os.path.isdir(sandbox):
        if not os.path.isfile(marker):
            # NEVER delete a directory we didn't create — a broad --out-dir (e.g. /tmp) could contain an
            # unrelated 'workspace' dir belonging to the user
            _die("--out-dir 下已存在非本工具创建的 workspace/ 目录，拒绝删除：%s（换个 --out-dir 或手动清理）"
                 % sandbox)
        shutil.rmtree(sandbox)
    shutil.copytree(fixture_dir, sandbox)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("created by run_live_smoke.py; safe to delete\n")

    digest = bank_digest(sandbox)
    _bank = json.loads(_read(os.path.join(sandbox, "references", "quiz_bank.json")))
    bank_answers = {}
    for _q in _bank:
        if not (isinstance(_q, dict) and _q.get("id") is not None):
            continue
        if _q.get("type") == "choice":
            continue      # choice 的答案就是某个选项，出题必然展示选项——不算泄露（文档已注明该边界）
        keys = []
        if _q.get("answer") not in (None, ""):
            keys.append(str(_q["answer"]))
        keys += [str(k) for k in (_q.get("answer_keywords") or [])]
        bank_answers[str(_q["id"])] = [re.sub(r"\s+", "", k) for k in keys if re.sub(r"\s+", "", k)]
    progress_path = os.path.join(sandbox, "study_progress.initial.md")
    progress = _read(progress_path) if os.path.isfile(progress_path) else "当前阶段：1\n"
    canonical = os.path.join(sandbox, "study_progress.md")   # skill contract reads THIS file on disk
    with open(canonical, "w", encoding="utf-8", newline="\n") as f:
        f.write(progress)
    import re as _re
    m = _re.search(r"当前阶段：(\d+)", progress)
    cur_phase = int(m.group(1)) if m else 1
    history, exchanges, oracle_failures = [], [], []
    for i, turn in enumerate(turns, 1):
        prompt = build_prompt(sandbox, digest, history, turn["user"], args.max_prompt_chars, progress)
        reply = call_agent(args.agent_cmd, prompt, args.turn_timeout, args.max_output_chars, cwd=sandbox)
        history += [("user", turn["user"]), ("assistant", reply)]
        for f in check_oracle(turn, reply):
            oracle_failures.append("turn %d: %s" % (i, f))
        if turn.get("kind") == "quiz":                 # a quiz prompt must not LEAK the standard answer
            rnorm = re.sub(r"\s+", "", reply)
            for qid in re.findall(r"\[#([^\]\s]+)\]", reply):
                for key in bank_answers.get(qid, []):
                    # 单字符答案（如 "0"）任意出现会误报——要求出现在「答案」语境附近才算泄露
                    hit = (key in rnorm) if len(key) >= 2 else bool(re.search("答案.{0,6}" + re.escape(key), rnorm))
                    if hit:
                        oracle_failures.append("turn %d: quiz 泄露标准答案（[#%s] 含 %r）" % (i, qid, key))
                        break
        snapshot = None
        tp = turn.get("phase_context")
        if isinstance(tp, str) and tp.strip().isdigit():
            tp = int(tp.strip())                       # T4/adapter 都接受数字字符串，这里同口径
        if isinstance(tp, int) and not isinstance(tp, bool) and tp != cur_phase:    # the script advanced the phase — the harness (as
            cur_phase = tp                             # the 'environment') persists the checkpoint the
            progress = _progress_with_phase(progress, cur_phase)   # agent reads on later turns
            with open(canonical, "w", encoding="utf-8", newline="\n") as f:
                f.write(progress)                       # disk copy stays in sync with the prompt state
            snapshot = progress
        exchanges.append((turn, reply, snapshot))
        print("[+] turn %d/%d 完成（回复 %d 字）" % (i, len(turns), len(reply)))

    log_path = os.path.join(args.out_dir, "live_session.md")
    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_log(spec, exchanges))
    jsonl_path = os.path.join(args.out_dir, "live_session.jsonl")
    conv = subprocess.run([sys.executable, os.path.join(HERE, "convert_session_log.py"),
                           "--in", log_path, "--out", jsonl_path],
                          capture_output=True, text=True, encoding="utf-8")
    if conv.returncode != 0:
        _die("T5b 转换失败（exit %d）：%s" % (conv.returncode, (conv.stderr or "")[:400]), 3)

    score = subprocess.run([sys.executable, os.path.join(HERE, "run_drift.py"),
                            "--scenario", scenario, "--transcript", jsonl_path],
                           capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(score.stdout)
    sys.stderr.write(score.stderr)
    print("[+] session log: %s\n[+] jsonl: %s" % (log_path, jsonl_path))
    if score.returncode not in (0, 1):
        _die("T4 判分器异常退出（%d）——不产生任何通过结论" % score.returncode, 3)
    if oracle_failures:                          # per-turn oracles gate the verdict alongside T4 metrics —
        for f in oracle_failures:                # a probe answered wrongly must not PASS on metrics alone
            print("[oracle-fail] " + f)
        print("[!] %d 个回合级 oracle 未通过（探针答复不符合脚本期望）" % len(oracle_failures))
        return 1
    return score.returncode                      # 0 = 达标；1 = 检出漂移（判分真实跑过才可能返回 0）


if __name__ == "__main__":
    sys.exit(main())
