# -*- coding: utf-8 -*-
"""B7 tests — unified run ledger: record/show/verify, schema rejection, hash stability,
live-smoke integration (offline fake agent), warning-only failure semantics."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "benchmark", "runs")
sys.path.insert(0, RUNS)
import ledger as L   # noqa: E402


class LedgerCore(unittest.TestCase):
    def test_record_show_verify_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
        e = L.record({"kind": "live_smoke", "model": "m", "exit_code": 0}, path)
        self.assertTrue(e["run_id"])
        r = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"), "--ledger", path,
                            "show", "--last", "5"], capture_output=True, text=True, encoding="utf-8")
        self.assertIn(e["run_id"], r.stdout)
        v = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"), "--ledger", path, "verify"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(v.returncode, 0)
        self.assertIn("全部有效", v.stdout)

    def test_invalid_entries_rejected(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        for bad in ({"kind": "nonsense"}, {"kind": "live_smoke", "cost_usd": -1},
                    {"kind": "live_smoke", "exit_code": "0"}, {"kind": "live_smoke", "model": 3}):
            with self.assertRaises(SystemExit):
                L.record(bad, path)
        self.assertFalse(os.path.isfile(path))        # 无效行绝不落盘

    def test_verify_flags_bad_rows(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        L.record({"kind": "other"}, path)
        with open(path, "a", encoding="utf-8") as f:
            f.write("{broken json\n")
            f.write(json.dumps({"kind": "nonsense", "run_id": "x", "created_at": "t"}) + "\n")
        r = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"), "--ledger", path, "verify"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 行无效", r.stdout)

    def test_workspace_hash_stable_and_sensitive(self):
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "references"))
        with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            f.write("[]")
        h1 = L.workspace_hash(ws)
        self.assertEqual(h1, L.workspace_hash(ws))    # 稳定
        with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            f.write('[{"id":"q1"}]')
        self.assertNotEqual(h1, L.workspace_hash(ws))  # 输入变则指纹变

    def test_try_record_never_raises(self):
        e, warn = L.try_record({"kind": "nonsense"}, os.path.join(tempfile.mkdtemp(), "l.jsonl"))
        self.assertIsNone(e)
        self.assertIn("不受影响", warn)

    def test_record_rejects_non_string_created_at(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        with self.assertRaises(SystemExit):           # 拒绝而非 AttributeError（run_id 派生要 .replace）
            L.record({"kind": "other", "created_at": 123}, path)
        self.assertFalse(os.path.isfile(path))

    def test_try_record_malformed_created_at_warns_not_raises(self):
        e, warn = L.try_record({"kind": "other", "created_at": 123},
                               os.path.join(tempfile.mkdtemp(), "l.jsonl"))
        self.assertIsNone(e)
        self.assertIn("不受影响", warn)               # never-raises 契约：畸形字段也只降级为警告

    def test_show_handles_valid_json_non_dict_row(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        L.record({"kind": "other"}, path)
        with open(path, "a", encoding="utf-8") as f:
            f.write("[]\n")                           # 合法 JSON 但不是对象
        r = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"), "--ledger", path,
                            "show", "--last", "5"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("坏行", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_run_ids_unique_same_second(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        ids = {L.record({"kind": "other", "created_at": "2026-07-01T00:00:00"}, path)["run_id"]
               for _ in range(5)}
        self.assertEqual(len(ids), 5)                             # 同秒同内容也不撞 run_id

    def test_offline_smoke_suite_opts_out_of_ledger(self):
        src = open(os.path.join(ROOT, "tests", "test_live_smoke.py"), encoding="utf-8").read()
        self.assertIn("--no-ledger", src)                         # 离线套件绝不污染默认审计账本

    def test_non_finite_numeric_rejected(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(SystemExit):       # NaN/Infinity 不是可移植 JSON，绝不落盘
                L.record({"kind": "other", "cost_usd": bad}, path)
        self.assertFalse(os.path.isfile(path))
        e, warn = L.try_record({"kind": "other", "cost_usd": float("nan")}, path)
        self.assertIsNone(e)
        self.assertIn("不受影响", warn)

    def test_non_string_kind_reported_not_crash(self):
        probs = L.validate_entry({"kind": ["live_smoke"], "run_id": "r", "created_at": "t"})
        self.assertTrue(any("kind" in p for p in probs))          # 数组 kind 报坏行，不 TypeError
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        L.record({"kind": "other"}, path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": {"x": 1}, "run_id": "r", "created_at": "t"}) + "\n")
        r = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"), "--ledger", path, "verify"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_fractional_tokens_rejected(self):
        path = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        with self.assertRaises(SystemExit):
            L.record({"kind": "other", "tokens_in": 1.5}, path)   # 1.5 个 token 不存在
        e = L.record({"kind": "other", "tokens_in": 2, "tokens_out": 0, "cost_usd": 0.5}, path)
        self.assertEqual(e["tokens_in"], 2)                       # 整数 token + 小数成本合法

    def test_committed_sample_is_valid(self):
        r = subprocess.run([sys.executable, os.path.join(RUNS, "ledger.py"),
                            "--ledger", os.path.join(RUNS, "ledger.sample.jsonl"), "verify"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_real_ledger_gitignored(self):
        gi = open(os.path.join(ROOT, "benchmark", ".gitignore"), encoding="utf-8").read()
        self.assertIn("runs/ledger.jsonl", gi)         # 真实账本绝不进仓库


class LiveSmokeIntegration(unittest.TestCase):
    def test_live_smoke_writes_ledger_row(self):
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led, "--model", "fake-agent"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()]
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual(e["kind"], "live_smoke")
        self.assertEqual(e["model"], "fake-agent")
        self.assertEqual(e["exit_code"], 0)
        self.assertTrue(e["prompt_hash"].startswith("sha256:"))
        self.assertTrue(e["workspace_hash"].startswith("sha256:"))
        self.assertTrue(os.path.isfile(e["transcript_path"]))

    def test_no_ledger_flag_skips(self):
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led, "--no-ledger"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.isfile(led))

    def test_ledger_records_oracle_gated_exit_code(self):
        # T4 判分通过但回合 oracle 未过 → 进程退出 1，账本必须记 1（而不是判分器的 0）
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        spec = json.load(open(os.path.join(ROOT, "benchmark", "drift", "templates",
                                           "live_smoke_turns.json"), encoding="utf-8"))
        spec["turns"].append({"user": "随便聊两句今天的进度。", "phase_context": 2,
                              "expect_any": ["ZZZ_不可能出现的探针串_QQQ"]})
        turns_path = os.path.join(out, "turns.json")
        with open(turns_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led,
                            "--turns", turns_path],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        rows = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()]
        self.assertEqual(rows[0]["exit_code"], 1)

    def test_ledger_workspace_hash_is_pre_run_fingerprint(self):
        # 默认脚本会把阶段推进到 2（改写沙盒 study_progress.md）——账本指纹必须代表运行「输入」，
        # 事后从原始 fixture 复算要能对上
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()][0]
        rebuilt = os.path.join(tempfile.mkdtemp(), "ws")
        shutil.copytree(os.path.join(ROOT, "benchmark", "drift", "fixtures", "mini_course_long"), rebuilt)
        prog = open(os.path.join(rebuilt, "study_progress.initial.md"), encoding="utf-8").read()
        with open(os.path.join(rebuilt, "study_progress.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(prog)
        self.assertEqual(row["workspace_hash"], L.workspace_hash(rebuilt))      # 可从输入复算
        self.assertNotEqual(row["workspace_hash"],
                            L.workspace_hash(os.path.join(out, "workspace")))   # ≠ 被改写后的沙盒

    def test_prompt_hash_reflects_actual_prompts(self):
        # 多轮 prompt 含前轮回复——同一脚本、不同 agent 行为必须得到不同 prompt_hash
        #（旧口径只哈希静态脚本，两次运行会同哈希，审计字段失义）
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        hashes = []
        for drift in ("0", "1"):
            out = tempfile.mkdtemp()
            led = os.path.join(out, "ledger.jsonl")
            env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1", FAKE_DRIFT=drift)
            subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led],
                           capture_output=True, text=True, encoding="utf-8", env=env)
            rows = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()]
            self.assertEqual(len(rows), 1)
            hashes.append(rows[0]["prompt_hash"])
        self.assertNotEqual(hashes[0], hashes[1])

    def test_partial_transcript_preserved_on_mid_run_abort(self):
        # 第 1 回合成功、第 2 回合 agent 失败——已付费的部分会话必须落盘，账本行给出可核查 artifact
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        agent_code = ("import os,sys\n"
                      "p='cnt.txt'\n"
                      "n=int(open(p).read()) if os.path.exists(p) else 0\n"
                      "open(p,'w').write(str(n+1))\n"
                      "sys.stdout.reconfigure(encoding='utf-8')\n"
                      "print('好的，我们接着复习。') if n < 1 else sys.exit(9)\n")
        cmd = json.dumps([sys.executable, "-c", agent_code, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        rows = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()]
        self.assertEqual(rows[0]["exit_code"], 3)
        self.assertIn("turns_done=1", rows[0]["notes"])
        partial = rows[0]["summary_path"]
        self.assertTrue(partial and os.path.isfile(partial))      # 部分 T5b 日志真实存在
        body = open(partial, encoding="utf-8").read()
        self.assertIn("## Turn 1", body)
        self.assertIn("接着复习", body)

    def test_conversion_failure_row_has_no_phantom_transcript(self):
        # T5b 转换失败中止时，账本 transcript_path 必须是 null（不能指向不存在的文件）、
        # summary_path 指向已写出的 md
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        os.makedirs(os.path.join(out, "live_session.jsonl"))      # 让转换器的输出写入必然失败
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        row = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()][0]
        self.assertEqual(row["exit_code"], 3)
        self.assertIsNone(row.get("transcript_path"))             # 不发布未产出的 jsonl 路径
        self.assertTrue(row.get("summary_path") and os.path.isfile(row["summary_path"]))

    def test_ledger_records_aborted_run(self):
        # 付费回合烧掉后 agent 失败中止（_die exit 3）——账本必须留一行审计记录，而不是无痕
        out = tempfile.mkdtemp()
        led = os.path.join(out, "ledger.jsonl")
        cmd = json.dumps([sys.executable, "-c", "import sys; sys.exit(7)", "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", led, "--model", "fake-agent"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        rows = [json.loads(x) for x in open(led, encoding="utf-8") if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exit_code"], 3)
        self.assertIn("aborted", rows[0]["notes"])                # 记明是中止行
        self.assertTrue(rows[0]["workspace_hash"].startswith("sha256:"))

    def test_ledger_failure_does_not_break_run(self):
        out = tempfile.mkdtemp()
        bad_led = os.path.join(out, "no_dir_here", "x", "..", "..", "l.jsonl")   # still writable? use a dir path
        bad_led = out                                            # 目录当文件 → 写入必失败
        fake = os.path.join(ROOT, "tests", "fake_live_agent.py")
        cmd = json.dumps([sys.executable, fake, "{prompt}"])
        env = dict(os.environ, RUN_SKILL_DRIFT_LLM="1")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "benchmark", "drift", "run_live_smoke.py"),
                            "--agent-cmd", cmd, "--out-dir", out, "--ledger", bad_led],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)              # 记账失败绝不影响运行结果
        self.assertIn("ledger", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
