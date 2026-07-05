# -*- coding: utf-8 -*-
"""B7 · Unified run ledger — every REAL benchmark/agent run leaves one auditable JSONL row.

Why: live smokes (T5c), drift replays, rejudge exports and future matrix runs each produce artifacts
in different places; without a ledger the "which model / which prompt / which workspace / what cost /
where is the transcript" story scatters and reproducibility dies. One row per run, append-only:

    {"run_id": "...", "kind": "live_smoke", "model": "claude-...", "prompt_hash": "sha256:...",
     "workspace_hash": "sha256:...", "transcript_path": "...", "summary_path": "...",
     "cost_usd": 0.01, "tokens_in": 1200, "tokens_out": 400, "exit_code": 0,
     "notes": "...", "created_at": "2026-07-02T01:00:00"}

HONEST BOUNDARIES:
  * the ledger FILE (benchmark/runs/ledger.jsonl) is a LOCAL artifact — gitignored, never committed
    (real run data may embed private course paths); the committed pieces are this module, the schema,
    and a self-authored sample for tests.
  * hashes are for REPRODUCIBILITY BOOKKEEPING (did the prompt/workspace change between runs), not
    security; cost/tokens are caller-reported, the ledger does not measure them itself.
  * recording never breaks a run: integrations treat ledger-write failure as a WARNING.

CLI:
    python benchmark/runs/ledger.py record --kind live_smoke --model claude-x \
        --transcript /tmp/live.jsonl --summary /tmp/report.txt --cost 0.02 --exit-code 0
    python benchmark/runs/ledger.py show --last 5
    python benchmark/runs/ledger.py verify

Exit codes: 0 ok · 1 verify found bad rows · 2 bad input/usage.
"""
import argparse
import datetime
import hashlib
import itertools
import json
import math
import os
import sys
import time

_RUN_SEQ = itertools.count()   # 进程内计数器——同秒同内容的 record 也必须拿到不同 run_id

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "ledger.jsonl")

KINDS = {"live_smoke", "drift_replay", "behavior_smoke_llm", "matrix_gen", "rejudge_export",
         "judge_calibration", "other"}
REQUIRED = ("run_id", "kind", "created_at")
STR_FIELDS = ("run_id", "kind", "model", "prompt_hash", "workspace_hash", "transcript_path",
              "summary_path", "notes", "created_at")
INT_FIELDS = ("tokens_in", "tokens_out")   # token 数只能是非负整数；cost_usd 单独按非负有限浮点校验


def _die(msg, code=2):
    sys.stderr.write("ledger: " + msg + "\n")
    raise SystemExit(code)


def hash_text(text):
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:24]


def workspace_hash(ws):
    """Stable fingerprint of the workspace INPUTS that define a run: quiz_bank / study_plan /
    study_state (when present). File list is fixed so the hash is comparable across runs."""
    h = hashlib.sha256()
    for rel in ("references/quiz_bank.json", "study_plan.md", "study_state.json", "study_progress.md"):
        p = os.path.join(ws, rel)
        h.update(rel.encode("utf-8"))
        if os.path.isfile(p):
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        else:
            h.update(b"<absent>")
    return "sha256:" + h.hexdigest()[:24]


def validate_entry(e):
    """Return a list of problems (empty = valid)."""
    probs = []
    if not isinstance(e, dict):
        return ["row 不是 JSON 对象"]
    for k in REQUIRED:
        if not e.get(k):
            probs.append("缺必需字段 %s" % k)
    kd = e.get("kind")
    # 数组/对象 kind 对 set 做 in 会 TypeError——verify/show 是诊断坏行的工具，必须先验类型再验成员
    if kd is not None and (not isinstance(kd, str) or kd not in KINDS):
        probs.append("kind 非法: %r（应为 %s）" % (kd, sorted(KINDS)))
    for k in STR_FIELDS:
        if e.get(k) is not None and not isinstance(e[k], str):
            probs.append("%s 必须是字符串" % k)
    v = e.get("cost_usd")
    # NaN 的 v<0 为 False、inf 也是 float——不拦会把 NaN/Infinity 写进 JSONL（非可移植 JSON），
    # 且 verify 用同一校验器会把坏行报成有效
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                          or not math.isfinite(v) or v < 0):
        probs.append("cost_usd 必须是非负有限数值")
    for k in INT_FIELDS:
        v = e.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            probs.append("%s 必须是非负整数" % k)   # 1.5 个 token 不存在——与 CLI 口径一致
    ec = e.get("exit_code")
    if ec is not None and (isinstance(ec, bool) or not isinstance(ec, int)):
        probs.append("exit_code 必须是整数")
    return probs


def record(entry, ledger_path=None):
    """Validate + append one row. Returns the written entry. Raises SystemExit(2) on invalid input."""
    e = dict(entry)
    e.setdefault("created_at", datetime.datetime.now().isoformat(timespec="seconds"))
    if not isinstance(e["created_at"], str):
        # run_id 派生要对 created_at 做 .replace——非字符串必须在这里先拒绝，
        # 否则 AttributeError 会逃出 try_record 的 SystemExit/OSError 兜底
        _die("record 被拒：created_at 必须是字符串")
    if not e.get("run_id"):
        # created_at 只有秒级——哈希掺入 纳秒时钟+pid+进程内计数，快速连续 record 相同内容也不撞 id
        salt = "%d|%d|%d" % (time.time_ns(), os.getpid(), next(_RUN_SEQ))
        e["run_id"] = "%s-%s" % (e["created_at"].replace(":", "").replace("-", ""),
                                 hash_text(json.dumps(e, sort_keys=True, ensure_ascii=False)
                                           + "|" + salt)[7:15])
    probs = validate_entry(e)
    if probs:
        _die("record 被拒：" + "；".join(probs))
    path = ledger_path or DEFAULT_LEDGER
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def try_record(entry, ledger_path=None):
    """Integration-friendly: never raises — returns (entry|None, warning|None)."""
    try:
        return record(entry, ledger_path), None
    except SystemExit as e:
        return None, "ledger 记录失败（exit %s）——运行结果不受影响" % e.code
    except OSError as e:
        return None, "ledger 写入失败：%s——运行结果不受影响" % e
    except Exception as e:  # never-raises 契约兜底：任何意外（如畸形字段类型）都降级为警告
        return None, "ledger 记录异常：%s——运行结果不受影响" % e


def load(ledger_path=None):
    path = ledger_path or DEFAULT_LEDGER
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((ln, json.loads(line)))
            except ValueError:
                rows.append((ln, None))
    return rows


def run(argv=None):
    ap = argparse.ArgumentParser(description="统一运行账本（append-only JSONL；本地产物，不进仓库）。")
    ap.add_argument("--ledger", default=None, help="账本路径（默认 benchmark/runs/ledger.jsonl）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_rec = sub.add_parser("record")
    p_rec.add_argument("--kind", required=True, choices=sorted(KINDS))
    p_rec.add_argument("--model", default=None)
    p_rec.add_argument("--transcript", default=None)
    p_rec.add_argument("--summary", default=None)
    p_rec.add_argument("--workspace", default=None, help="给出则自动计算 workspace_hash")
    p_rec.add_argument("--prompt-file", default=None, help="给出则自动计算 prompt_hash")
    p_rec.add_argument("--cost", type=float, default=None)
    p_rec.add_argument("--tokens-in", type=int, default=None)
    p_rec.add_argument("--tokens-out", type=int, default=None)
    p_rec.add_argument("--exit-code", type=int, default=None)
    p_rec.add_argument("--note", default=None)
    p_show = sub.add_parser("show")
    p_show.add_argument("--last", type=int, default=10)
    sub.add_parser("verify")
    args = ap.parse_args(argv)

    if args.cmd == "record":
        entry = {"kind": args.kind, "model": args.model, "transcript_path": args.transcript,
                 "summary_path": args.summary, "cost_usd": args.cost, "tokens_in": args.tokens_in,
                 "tokens_out": args.tokens_out, "exit_code": args.exit_code, "notes": args.note}
        if args.workspace:
            if not os.path.isdir(args.workspace):
                _die("--workspace 不存在: %s" % args.workspace)
            entry["workspace_hash"] = workspace_hash(args.workspace)
        if args.prompt_file:
            if not os.path.isfile(args.prompt_file):
                _die("--prompt-file 不存在: %s" % args.prompt_file)
            entry["prompt_hash"] = hash_file(args.prompt_file)
        entry = {k: v for k, v in entry.items() if v is not None}
        e = record(entry, args.ledger)
        print("[+] 已记账 run_id=%s（%s）" % (e["run_id"], e["kind"]))
        return 0
    if args.cmd == "show":
        rows = load(args.ledger)
        for ln, e in rows[-args.last:]:
            # 与 verify 同一校验口径——合法 JSON 但非对象（如 []）不能直接 .get()
            probs = validate_entry(e) if e is not None else ["非法 JSON"]
            if probs:
                print("#%d <坏行：%s>" % (ln, "；".join(probs)))
            else:
                print("#%d %s %s model=%s cost=%s exit=%s %s"
                      % (ln, e.get("run_id"), e.get("kind"), e.get("model") or "-",
                         e.get("cost_usd", "-"), e.get("exit_code", "-"), e.get("transcript_path") or ""))
        print("（共 %d 行）" % len(rows))
        return 0
    # verify
    bad = 0
    for ln, e in load(args.ledger):
        probs = validate_entry(e) if e is not None else ["非法 JSON"]
        if probs:
            bad += 1
            print("#%d 无效：%s" % (ln, "；".join(probs)))
    print("verify: %s" % ("全部有效" if not bad else "%d 行无效" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run())
