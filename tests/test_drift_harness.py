# -*- coding: utf-8 -*-
"""Root-level, CI-reachable tests for the Tier 4 long-horizon drift harness (benchmark/drift/run_drift.py).

CI only discovers the repo-root tests/, so the T4 harness is covered HERE. Pure stdlib; no network / LLM /
API keys / deps / paid run. Verifies: the good long-session transcript passes all thresholds; each bad
transcript fails for its intended reason; malformed input exits 2; token/cost accounting; overread and
row-loss detection; the --llm skeleton never returns success; and the fixture is self-authored text."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIFT = os.path.join(ROOT, "benchmark", "drift")
RUN = os.path.join(DRIFT, "run_drift.py")
SCEN = os.path.join(DRIFT, "scenarios", "long_session_basic.json")
TR = os.path.join(DRIFT, "transcripts")
FIX = os.path.join(DRIFT, "fixtures", "mini_course_long")

sys.path.insert(0, DRIFT)
import run_drift as D   # noqa: E402


def _tr(name):
    return os.path.join(TR, name)


def _eval(transcript):
    sc = D.load_scenario(SCEN)
    return D.evaluate(sc, _tr(transcript))


def _eval_turns(turns, scenario=SCEN):
    """Write a synthetic transcript and evaluate it against a scenario — for targeted regression probes."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
    return D.evaluate(D.load_scenario(scenario), p)["metrics"]


def _cli(args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, RUN] + args, capture_output=True, text=True, encoding="utf-8", env=e)


def _fail_thresholds(result):
    return {f["threshold"] for f in result["failures"]}


@unittest.skipUnless(os.path.isdir(DRIFT), "drift harness not present")
class DriftHarness(unittest.TestCase):
    # 1) good transcript passes ALL thresholds
    def test_good_transcript_passes(self):
        r = _eval("good_session.jsonl")
        self.assertTrue(r["passed"], r["failures"])
        m = r["metrics"]
        self.assertEqual(m["goal_retention"], 1.0)
        self.assertEqual(m["plan_mutations"], 0)
        self.assertEqual(m["invention_rate"], 0.0)
        self.assertEqual(m["wrong_phase_quiz"], 0)
        self.assertEqual(m["reset_detected"], 0)
        self.assertEqual(m["provenance_fidelity"], 1.0)
        self.assertEqual(m["progress_rows_lost"], 0)
        self.assertGreaterEqual(m["turns"], 12)      # a genuinely long-horizon session

    # 2) bad plan-drift fails plan adherence (and only that)
    def test_bad_plan_drift_fails_plan(self):
        r = _eval("bad_plan_drift.jsonl")
        self.assertFalse(r["passed"])
        self.assertIn("plan_mutations_max", _fail_thresholds(r))
        self.assertGreater(r["metrics"]["plan_mutations"], 0)
        self.assertLess(r["metrics"]["plan_adherence"], 1.0)
        self.assertEqual(_fail_thresholds(r), {"plan_mutations_max"})   # fails ONLY for the intended reason

    # 3) bad quiz-invention fails invention rate
    def test_bad_quiz_invention_fails_invention(self):
        r = _eval("bad_quiz_invention.jsonl")
        self.assertFalse(r["passed"])
        self.assertIn("quiz_invention_rate_max", _fail_thresholds(r))
        self.assertGreater(r["metrics"]["invented"], 0)
        self.assertGreater(r["metrics"]["invention_rate"], 0.0)
        self.assertEqual(_fail_thresholds(r), {"quiz_invention_rate_max"})

    # 4) bad checkpoint-reset fails the checkpoint metric
    def test_bad_checkpoint_reset_fails_checkpoint(self):
        r = _eval("bad_checkpoint_reset.jsonl")
        self.assertFalse(r["passed"])
        self.assertIn("checkpoint_reset_max", _fail_thresholds(r))
        m = r["metrics"]
        self.assertEqual(m["reset_detected"], 1)
        self.assertEqual(m["expected_phase"], 2)     # progress was at phase 2
        self.assertEqual(m["resumed_phase"], 1)      # …but it restarted at phase 1
        self.assertEqual(_fail_thresholds(r), {"checkpoint_reset_max"})

    # 5) bad provenance-drift fails provenance fidelity
    def test_bad_provenance_drift_fails_provenance(self):
        r = _eval("bad_provenance_drift.jsonl")
        self.assertFalse(r["passed"])
        self.assertIn("provenance_fidelity_min", _fail_thresholds(r))
        self.assertLess(r["metrics"]["provenance_fidelity"], 0.8)
        self.assertGreater(r["metrics"]["explanation_turns"], 0)
        self.assertEqual(_fail_thresholds(r), {"provenance_fidelity_min"})

    # 5b) A6 mode drift — ≤1天档向学生提问 = goal-drift 的具体化
    def test_a6_urgent_mode_good_passes(self):
        sc = os.path.join(DRIFT, "scenarios", "mode_urgent_no_questions.json")
        r = D.evaluate(D.load_scenario(sc), _tr("good_session_urgent_1day.jsonl"))
        self.assertTrue(r["passed"], r["failures"])
        self.assertEqual(r["metrics"]["urgent_mode_questions"], 0)
        # A6-YI0：好转写必须真的把推断出的 零基础从头讲+≤1天 持久化进 study_state.json 快照
        self.assertEqual(r["metrics"]["urgent_mode_persisted"], 1)

    def test_a6_urgent_persist_required(self):
        # 只喊「按默认开讲」却没把 mode/time 落盘 → urgent_mode_persisted=0 → 挂在 persist 门槛上（Codex R3-YI0）
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "mode_urgent_no_questions.json"))
        turns = D.load_jsonl(_tr("good_session_urgent_1day.jsonl"), "g")
        for t in turns:                                          # 抹掉状态快照 = 没持久化
            t.pop("files_after", None)
            t["events"] = [e for e in t.get("events", []) if "study_state.json" not in e.get("path", "")]
        d = tempfile.mkdtemp()
        p = os.path.join(d, "no_persist.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertEqual(r["metrics"]["urgent_mode_persisted"], 0)
        self.assertIn("urgent_mode_persisted_min", _fail_thresholds(r))

    def test_a6_urgent_alias_time_budget_counts(self):
        # update_progress 认「明天考」为 ≤1天，drift 侧也必须（复用 canonical 归一），别名场景仍施加紧迫约束
        self.assertTrue(D._tier_is_urgent("明天考"))
        self.assertTrue(D._tier_is_urgent("考前一天"))
        self.assertFalse(D._tier_is_urgent("3-7天"))
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "mode_urgent_no_questions.json"))
        sc["time_budget"] = "明天考"
        r = D.evaluate(sc, _tr("bad_urgent_1day_questions.jsonl"))
        self.assertGreater(r["metrics"]["urgent_mode_questions"], 0)   # 别名档也照数提问，不再漏判为非紧迫
        self.assertIn("urgent_mode_questions_max", _fail_thresholds(r))

    def test_a6_urgent_mode_questions_fail(self):
        sc = os.path.join(DRIFT, "scenarios", "mode_urgent_no_questions.json")
        r = D.evaluate(D.load_scenario(sc), _tr("bad_urgent_1day_questions.jsonl"))
        self.assertFalse(r["passed"])
        self.assertIn("urgent_mode_questions_max", _fail_thresholds(r))
        self.assertEqual(r["metrics"]["urgent_mode_questions"], 3)   # 每轮都在问学生偏好

    def test_a6_urgent_metric_zero_when_budget_not_urgent(self):
        # 非 ≤1天档：即使转写里有学生问句，urgent_mode_questions 也恒为 0（指标不适用）
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "mode_urgent_no_questions.json"))
        sc["time_budget"] = "3-7天"
        r = D.evaluate(sc, _tr("bad_urgent_1day_questions.jsonl"))
        self.assertEqual(r["metrics"]["urgent_mode_questions"], 0)

    def test_b3_window_persist_good_passes(self):
        # B3：A6 知识点窗口长会话持久化——窗口条目随讲解登记（≥2）、进出用状态迁移不丢行（lost=0）
        sc = os.path.join(DRIFT, "scenarios", "window_persist.json")
        r = D.evaluate(D.load_scenario(sc), _tr("window_persist_session.jsonl"))
        self.assertTrue(r["passed"], r["failures"])
        self.assertGreaterEqual(r["metrics"]["window_rows_added"], 2)
        self.assertEqual(r["metrics"]["window_rows_lost"], 0)

    def test_b3_window_row_silently_dropped_fails(self):
        # 某回合把已登记的窗口条目从 knowledge_window 里静默删掉 → window_rows_lost>0 → 挂门槛
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        last = turns[-1]
        st = json.loads(last["files_after"]["study_state.json"])
        st["knowledge_window"] = [w for w in st["knowledge_window"] if w["point"] != "队列FIFO"]
        last["files_after"]["study_state.json"] = json.dumps(st, ensure_ascii=False)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "dropped.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertFalse(r["passed"])
        self.assertIn("window_rows_lost_max", _fail_thresholds(r))
        self.assertEqual(r["metrics"]["window_rows_lost"], 1)

    def test_b3_window_status_transition_not_a_loss(self):
        # 窗口进出（在窗口→窗口外→已实测）是状态迁移、键不变 → 不算丢行
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        r = D.evaluate(sc, _tr("window_persist_session.jsonl"))
        self.assertEqual(r["metrics"]["window_rows_lost"], 0)

    def test_b3_chapter_backfill_not_a_loss(self):
        # 补章节（point@ → point@N）是同一行的回填，不算 丢+加（Codex R5LA + 方向性 SGGq）
        self.assertTrue(D._window_same_row("红黑树@", "红黑树@7"))
        self.assertEqual(D._window_diff(["红黑树@"], ["红黑树@7"]), (0, 0))

    def test_b3_window_diff_one_to_one_and_directional(self):
        # Codex R_Xa 一对一 + SGGq 方向性：抹章不是 backfill、是丢失
        self.assertFalse(D._window_same_row("点@7", "点@"))        # 抹掉章节身份 ≠ 同一行
        self.assertEqual(D._window_diff(["栈@1"], ["栈@"]), (1, 1))   # 栈@1 → 栈@ 抹章 = 丢一行 + 新一行
        self.assertEqual(D._window_diff(["模板@2", "模板@5"], ["模板@"]), (1, 2))   # 两条塌成一条：真丢 2
        self.assertEqual(D._window_diff(["栈@1"], ["栈@1", "队列@1"]), (1, 0))       # 讲了新点 = added
        self.assertEqual(D._window_diff(["栈@1"], ["栈@1"]), (0, 0))                # 状态迁移不改键

    def test_b3_omitted_window_status_defaults_in_window(self):
        # Codex SGGn：省略 status 的合法窗口行归一到渲染默认（v4 代号 in_window——state 侧三代词汇
        # 与 md 侧显示词都经 canon 收敛到代号后再比对），不误触 md/state 不一致
        snap = D.parse_state_json(json.dumps(
            {"current_phase": 1, "mistake_archive": [], "confusion_log": [],
             "knowledge_window": [{"point": "栈", "chapter": "1"}]}), [1])
        self.assertEqual(snap["window_status"], ["in_window"])
        # 中文显示词照样被收敛到同一代号（旧 state 快照兼容）
        snap_zh = D.parse_state_json(json.dumps(
            {"current_phase": 1, "mistake_archive": [], "confusion_log": [],
             "knowledge_window": [{"point": "栈", "chapter": "1", "status": "在窗口"}]}), [1])
        self.assertEqual(snap_zh["window_status"], ["in_window"])

    def test_b3_non_canonical_window_status_fails_loud(self):
        # Codex R_Xd：非 canonical 窗口状态（typo/任意串）是坏写入 → 畸形输入 exit 2，不让乱码状态骗过迁移门槛
        bad = json.dumps({"current_phase": 1, "mistake_archive": [], "confusion_log": [],
                          "knowledge_window": [{"point": "栈", "chapter": "1", "status": "在窗户"}]})
        with self.assertRaises(D.DriftError):
            D.parse_state_json(bad, [1])

    def test_b3_md_window_status_stale_flagged(self):
        # Codex R_XY：state 迁移了窗口状态、生成视图 md 保持旧状态（行数没变）→ 陈旧面板计入 md_write_after_state
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        for t in turns:
            fa = t.get("files_after") or {}
            if "study_progress.md" in fa:
                fa["study_progress.md"] = re.sub(r"(\| 栈的LIFO \| 1 \| )(窗口外|已实测)", r"\1在窗口",
                                                 fa["study_progress.md"])
        d = tempfile.mkdtemp()
        p = os.path.join(d, "stalestatus.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertGreater(r["metrics"]["md_write_after_state"], 0)
        self.assertFalse(r["passed"])

    def test_b3_llm_requires_turns(self):
        # Codex R_Xg：--llm 委托前必须显式 --turns，否则 live runner 会默认跑短 smoke 而非长会话漂移
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", tempfile.mkdtemp()],
                 env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("必须显式", r.stderr)

    def test_b3_llm_rejects_state_scenario(self):
        # Codex OSL85：--turns 指向依赖 state 的 scenario（window_persist）时 --llm 显式拒绝——
        # run_live_smoke 不录 study_state.json，会看不到 state/窗口写入而误判
        d = tempfile.mkdtemp()
        spec = {"fixture": "benchmark/drift/fixtures/mini_course_long_state",
                "scenario": "benchmark/drift/scenarios/window_persist.json", "turns": [{"user": "hi"}]}
        sp = os.path.join(d, "turns.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", d, "--turns", sp],
                 env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("暂不支持", r.stderr)

    def test_b3_duplicate_window_row_fails_loud(self):
        # Codex OSL86：同一快照里同 point@chapter 重复（追加而非更新）= 坏写入 → exit 2
        bad = json.dumps({"current_phase": 1, "mistake_archive": [], "confusion_log": [],
                          "knowledge_window": [{"point": "栈", "chapter": "1", "status": "在窗口"},
                                               {"point": "栈", "chapter": "1", "status": "窗口外"}]})
        with self.assertRaises(D.DriftError):
            D.parse_state_json(bad, [1])

    def test_b3_llm_allows_textonly_scenario(self):
        # Codex OSTZM：只有 checkpoint/md 阈值（无 requires_state/窗口/urgent）的 live_smoke_basic 不该被拒——
        # 这些指标由 live runner 的合成 md 快照支持，默认 live 路径必须能跑
        d = tempfile.mkdtemp()
        spec = {"fixture": "benchmark/drift/fixtures/mini_course_long",
                "scenario": "benchmark/drift/scenarios/live_smoke_basic.json", "turns": [{"user": "hi"}]}
        sp = os.path.join(d, "t.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        # --no-ledger：这是唯一会真委托进 run_live_smoke 的 --llm 用例，否则 live runner 会把默认账本
        # 写进 repo 的 benchmark/runs/ledger.jsonl，污染本地/CI 工作区（Codex OSlMB）
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", os.path.join(d, "o"), "--turns", sp,
                  "--no-ledger"], env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertNotIn("暂不支持", r.stderr)                    # 未被 state 门挡下（应委托给 live runner）

    def test_b3_llm_bad_scenario_type_no_traceback(self):
        # Codex OSTZR：--turns 里 scenario 是非字符串（123）→ 别抛 TypeError traceback，交给 live runner 校验
        d = tempfile.mkdtemp()
        sp = os.path.join(d, "bad.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"fixture": "x", "scenario": 123, "turns": [{"user": "hi"}]}, f)
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", os.path.join(d, "o"), "--turns", sp],
                 env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertNotIn("Traceback", r.stderr)

    def test_b3_window_key_normalized_like_renderer(self):
        # Codex OSTZP：含 | 的 point 名，state 键按 _md_cell 归一（| → /），与 render_md 写的 md 单元格一致
        snap = D.parse_state_json(json.dumps(
            {"current_phase": 1, "mistake_archive": [], "confusion_log": [],
             "knowledge_window": [{"point": "DFS|BFS", "chapter": "1", "status": "在窗口"}]}), [1])
        self.assertEqual(snap["window_rows"], ["DFS/BFS@1"])

    def test_b3_unknown_threshold_key_rejected_at_load(self):
        # Codex OSlL7：thresholds 里的未知 key（typo）在 load_scenario 即报——确定性路径与 --llm 预检都能
        # 在判分/付费之前拦下
        d = tempfile.mkdtemp()
        scj = os.path.join(d, "sc.json")
        with open(scj, "w", encoding="utf-8") as f:
            f.write('{"name":"x","fixture":"benchmark/drift/fixtures/mini_course_long",'
                    '"thresholds":{"nonsense_max":0}}')
        with self.assertRaises(D.DriftError):
            D.load_scenario(scj)

    def test_b3_window_before_archive_sections(self):
        # Codex OSlL9：🪟 窗口区排在 错题/疑难 之前时，进归档区必须清 in_window，否则归档行被窗口解析器吞掉
        md = ("# 进度\n## 🪟 知识点窗口\n| 知识点 | 关联章节 | 状态 | 备注 |\n| :--- | :--- | :--- | :--- |\n"
              "| 栈 | 1 | 在窗口 | |\n## ❌ 错题档案记录\n| 错题ID | 关联章节 | 错误原因分析 | 状态 |\n"
              "| :--- | :--- | :--- | :--- |\n| [#q1] | 1 | 记反了 | 待复盘 |\n")
        r = D.parse_progress(md)
        self.assertEqual(len(r["mistake_rows"]), 1)               # 归档行没被窗口解析器吞掉
        self.assertEqual(len(r["window_rows"]), 1)

    def test_b3_window_point_with_at_sign(self):
        # Codex OSZRp：point 名含 @（C@语言）时用 rpartition 从右切，backfill 不被误判为 丢+加
        self.assertTrue(D._window_same_row("C@语言@", "C@语言@1"))       # 补章节 = 同一行
        self.assertFalse(D._window_same_row("C@语言@1", "C@语言@"))      # 抹章 = 不同行
        self.assertEqual(D._window_diff(["C@语言@"], ["C@语言@1"]), (0, 0))
        self.assertEqual(D._window_diff(["C@语言@1"], ["C@语言@"]), (1, 1))

    def test_b3_llm_rejects_state_backed_fixture(self):
        # Codex OSZRj：scenario 没 requires_state/state 阈值，但 fixture 自带 study_state.json 也要拒——
        # 否则 live 的 md-only 快照会被当陈旧，付费跑后 false-fail
        d = tempfile.mkdtemp()
        scj = os.path.join(d, "sc.json")
        with open(scj, "w", encoding="utf-8") as f:
            json.dump({"name": "txt_over_state_fx", "fixture": "benchmark/drift/fixtures/mini_course_long_state",
                       "thresholds": {"goal_retention_min": 0.9}}, f, ensure_ascii=False)
        sp = os.path.join(d, "t.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"fixture": "benchmark/drift/fixtures/mini_course_long_state", "scenario": scj,
                       "turns": [{"user": "hi"}]}, f, ensure_ascii=False)
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", os.path.join(d, "o"), "--turns", sp],
                 env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("暂不支持", r.stderr)

    def test_b3_llm_cwd_relative_turns_still_rejected(self):
        # Codex OSfRR：从别的 cwd 用相对 --turns 指向 state scenario，绝对化后预检仍能拒（不漏过付费跑）
        d = tempfile.mkdtemp()
        spec = {"fixture": "benchmark/drift/fixtures/mini_course_long_state",
                "scenario": "benchmark/drift/scenarios/window_persist.json", "turns": [{"user": "hi"}]}
        with open(os.path.join(d, "state_turns.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        e = dict(os.environ)
        e["RUN_SKILL_DRIFT_LLM"] = "1"
        r = subprocess.run([sys.executable, RUN, "--llm", "--agent-cmd", "echo {prompt}",
                            "--out-dir", "out", "--turns", "state_turns.json"],
                           cwd=d, capture_output=True, text=True, encoding="utf-8", env=e)
        self.assertEqual(r.returncode, 2)
        self.assertIn("暂不支持", r.stderr)

    def test_b3_llm_malformed_scenario_rejected_preflight(self):
        # Codex OSfRP：存在但畸形的 scenario（thresholds 非对象）在委托前就报错，不烧 token
        d = tempfile.mkdtemp()
        scj = os.path.join(d, "bad_sc.json")
        with open(scj, "w", encoding="utf-8") as f:
            f.write('{"name":"x","fixture":"benchmark/drift/fixtures/mini_course_long",'
                    '"thresholds":"not-an-object"}')
        sp = os.path.join(d, "t.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"fixture": "benchmark/drift/fixtures/mini_course_long", "scenario": scj,
                       "turns": [{"user": "hi"}]}, f)
        r = _cli(["--llm", "--agent-cmd", "echo {prompt}", "--out-dir", os.path.join(d, "o"), "--turns", sp],
                 env={"RUN_SKILL_DRIFT_LLM": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("无法解析", r.stderr)

    def test_b3_window_note_only_stale_md_flagged(self):
        # Codex OSZRm：只改 note 的窗口更新，md 备注列没跟上（陈旧）也要抓——比对带上 note
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        for t in turns:
            fa = t.get("files_after") or {}
            if "study_state.json" in fa:
                st = json.loads(fa["study_state.json"])
                for w in st["knowledge_window"]:
                    if w["point"] == "队列FIFO":
                        w["note"] = "新增备注XYZ"                      # state 加 note，md 保持旧（空）备注
                fa["study_state.json"] = json.dumps(st, ensure_ascii=False)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "noteonly.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertGreater(r["metrics"]["md_write_after_state"], 0)
        self.assertFalse(r["passed"])

    def test_b3_md_duplicate_window_row_flagged(self):
        # Codex OSTZO：md 里同一窗口行重复（陈旧/手改）不能被 dict 折叠——保留重数，md 与 state 不一致要抓
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        for t in turns:
            fa = t.get("files_after") or {}
            md = fa.get("study_progress.md")
            if md and "栈的LIFO" in md:
                fa["study_progress.md"] = re.sub(r"(\| 栈的LIFO \| 1 \| \S+ \|[^\n]*\n)", r"\1\1", md, count=1)
                break
        d = tempfile.mkdtemp()
        p = os.path.join(d, "dupmd.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertGreater(r["metrics"]["md_write_after_state"], 0)
        self.assertFalse(r["passed"])

    def test_b3_status_migration_required(self):
        # Codex R5LB：只保留窗口行、状态全程不迁移（在窗口→窗口外→已实测 没发生）应挂 migrations 门槛
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        for t in turns:
            fa = t.get("files_after") or {}
            if "study_state.json" in fa:
                st = json.loads(fa["study_state.json"])
                for w in st["knowledge_window"]:
                    w["status"] = "在窗口"                          # 冻结状态，从不迁移
                fa["study_state.json"] = json.dumps(st, ensure_ascii=False)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "nomig.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertEqual(r["metrics"]["window_status_migrations"], 0)
        self.assertFalse(r["passed"])
        self.assertIn("window_status_migrations_min", _fail_thresholds(r))

    def test_b3_md_missing_window_section_flagged(self):
        # Codex R5LE：state 有窗口条目、生成视图 md 漏了 🪟 区 → 双写不一致 md_write_after_state>0
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        for t in turns:
            fa = t.get("files_after") or {}
            if "study_progress.md" in fa:
                fa["study_progress.md"] = re.sub(r"## 🪟[\s\S]*$", "", fa["study_progress.md"])
        d = tempfile.mkdtemp()
        p = os.path.join(d, "stalemd.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        r = D.evaluate(sc, p)
        self.assertGreater(r["metrics"]["md_write_after_state"], 0)
        self.assertFalse(r["passed"])

    def test_b3_bad_window_row_fails_loud(self):
        # knowledge_window 里出现缺 point 的坏行 → 畸形输入 fail-loud（exit 2），不静默当 0 行通过
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "window_persist.json"))
        turns = D.load_jsonl(_tr("window_persist_session.jsonl"), "w")
        st = json.loads(turns[-1]["files_after"]["study_state.json"])
        st["knowledge_window"].append({"chapter": "1", "status": "在窗口"})   # 缺 point
        turns[-1]["files_after"]["study_state.json"] = json.dumps(st, ensure_ascii=False)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        rc = _cli(["--scenario", os.path.join(DRIFT, "scenarios", "window_persist.json"),
                   "--transcript", p]).returncode
        self.assertEqual(rc, 2)                                  # 畸形输入 = exit 2

    def test_a6_detector_parity_drift_vs_behavior_smoke(self):
        # drift 的 _asks_student_question 是 behavior_smoke.asks_student_question 的逐字等价副本——
        # 用一组含反问/自答/中英/跨行/缺 cue 的样本锁二者一致，任一处漂了就红
        bs_dir = os.path.join(ROOT, "benchmark", "behavior_smoke")
        sys.path.insert(0, bs_dir)
        import run_behavior_smoke as BS   # noqa: E402
        cases = [
            "你好？", "你想先复习哪一章？ 告诉我。", "请问你复习到第几章了？请回复。",
            "Which chapter do you want to start with?", "Do you remember big-O notation?",
            "你打算从哪\n章开始？", "从哪一章开始？", "开始吧。从哪里开始最有把握？",
            "你可能会问：这道题为什么选 B？因为它满足性质。", "您也许好奇：栈和队列有何区别？其实差在存取顺序。",
            "为什么顺序表随机访问更快？因为地址可直接算出。", "接下来我给你讲栈的三个操作。",
            "要不要我先讲栈？", "纯讲解，无任何问句。",
            # R1-XX 新增的选择疑问 / should-I 形态也要在两份副本里判定一致
            "先讲栈还是队列？", "需要先讲栈吗？", "Should I start with stacks?",
            "用不用我先过一遍公式？", "先复习哪个？栈还是队列？", "接下来我先讲栈，再讲队列。",
            # R2-IAO 通用面向用户问句 + 收尾问句 + 反问自答，两份副本判定一致
            "还有问题吗？", "接下来怎么安排？", "我先讲第1章，可以吗？", "我们开始吧，好吗？",
            "Any questions?", "栈是后进先出，对吧？其实就是这样。", "你可能会问：为什么？因为如此。",
        ]
        for c in cases:
            self.assertEqual(BS.asks_student_question(c), D._asks_student_question(c),
                             "drift 与 behavior_smoke 的学生问句判定漂了：%r" % c)

    # 6) missing / malformed transcript exits 2
    def test_state_scenario_rejects_md_only_transcript(self):
        # requires_state 场景：从未写 study_state.json 的纯 md 转写必须挂在 md_write_after_state 上
        sc = D.load_scenario(os.path.join(DRIFT, "scenarios", "long_session_state.json"))
        r = D.evaluate(sc, _tr("good_session.jsonl"))
        self.assertIn("md_write_after_state_max", _fail_thresholds(r))

    def test_md_rows_beyond_state_counted_as_hand_edit(self):
        # 双写但只有生成视图多了行、state 空转——手改 md 不能靠捎带 no-op state 写洗白
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        md1 = "当前阶段：2\n## 错题本\n- [#q1] 误答\n"
        md2 = "当前阶段：2\n## 错题本\n- [#q1] 误答\n- [#q2] 手改新增的行\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "再记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md2}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)            # 只有 turn2 的行数背离计违规

    def test_non_utf8_state_fixture_raises_drifterror(self):
        import shutil
        sc = dict(D.load_scenario(os.path.join(DRIFT, "scenarios", "long_session_state.json")))
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        shutil.copytree(os.path.join(DRIFT, "fixtures", "mini_course_long"), fx)
        with open(os.path.join(fx, "study_state.json"), "wb") as f:
            f.write('{"current_phase": 2}'.encode("utf-16"))      # 带 BOM 的非 UTF-8 字节
        sc["fixture"] = fx
        with self.assertRaises(D.DriftError):                     # 坏编码走畸形输入路径，
            D.evaluate(sc, _tr("good_session_state.jsonl"))       # 不是 UnicodeDecodeError 崩栈

    def test_md_row_replacement_with_noop_state_flagged(self):
        # 双写但 md 同数替换了行、state 空转——行数不变也要计手改
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        md1 = "当前阶段：2\n## 错题本\n- [#q1] 误答\n"
        md2 = "当前阶段：2\n## 错题本\n- [#q9] 手改替换的行\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "替换。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md2}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)

    def test_state_event_without_snapshot_beside_md_rejected(self):
        # md 快照 + 只有 state 写事件——证据缺失不许豁免手改计数，按畸形输入拒收
        md2 = "当前阶段：2\n## 错题本\n- [#q9] 手改的行\n"
        with self.assertRaises(D.DriftError):
            _eval_turns([{"turn": 1, "assistant": "x", "phase_context": 2,
                          "files_after": {"study_progress.md": md2},
                          "events": [{"type": "write_file", "path": "study_state.json"}]}])

    def test_stale_md_missing_new_state_row_flagged(self):
        # 双写但 state 进了新行、生成视图没跟上——反向背离同样计违规
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        st2 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"},
                                              {"id": "q2", "note": "新错题"}],
                          "confusion_log": []}, ensure_ascii=False)
        md1 = "当前阶段：2\n## 错题本\n- [#q1] 误答\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "再记录。", "phase_context": 2,
             "files_after": {"study_state.json": st2, "study_progress.md": md1}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)

    def test_stale_md_phase_in_dual_write_flagged(self):
        # 双写但生成视图的断点还停在旧阶段——面板陈旧同样计违规
        st2 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        md_ok = "当前阶段：2\n## 错题本\n- [#q1] 误答\n"
        md_stale = "当前阶段：1\n## 错题本\n- [#q1] 误答\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "推进。", "phase_context": 2,
             "files_after": {"study_state.json": st2, "study_progress.md": md_ok}},
            {"turn": 2, "assistant": "又写。", "phase_context": 2,
             "files_after": {"study_state.json": st2, "study_progress.md": md_stale}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)

    def test_idless_generated_row_hand_edit_flagged(self):
        # idless 生成表行（首列 '-'）的同数手改也要抓；官方 set-*-status 只改状态列不误报
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"chapter": "1", "note": "无id笔记一"}],
                          "confusion_log": []}, ensure_ascii=False)
        hdr = ("| 错题ID | 关联章节 | 错误原因分析 | 状态 |" + chr(10)
               + "| :--- | :--- | :--- | :--- |" + chr(10))
        md1 = "当前阶段：2" + chr(10) + "## 错题档案记录" + chr(10) + hdr + "| - | 1 | 无id笔记一 | 待复盘 |" + chr(10)
        md2 = "当前阶段：2" + chr(10) + "## 错题档案记录" + chr(10) + hdr + "| - | 1 | 手改替换的内容 | 待复盘 |" + chr(10)
        md3 = "当前阶段：2" + chr(10) + "## 错题档案记录" + chr(10) + hdr + "| - | 1 | 手改替换的内容 | 已订正 |" + chr(10)
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "替换。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md2}},
            {"turn": 3, "assistant": "只改状态。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md3}},
        ])
        # turn2 手改现形；turn3 的 note 仍与事实源背离（跨源包含检查）——持续陈旧每回合都计
        self.assertEqual(m["md_write_after_state"], 2)

    def test_first_turn_same_count_md_hand_edit_flagged(self):
        # fixture 给了 state+初始 md 双基线：首个双写快照的同数手改（state 与基线一致、
        # md 行被替换）也要计违规
        import shutil
        sc = dict(D.load_scenario(os.path.join(DRIFT, "scenarios", "long_session_state.json")))
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        shutil.copytree(os.path.join(DRIFT, "fixtures", "mini_course_long_state"), fx)
        st0 = json.dumps({"version": 1, "current_phase": 1,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        with open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8") as f:
            f.write(st0)
        with open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8") as f:
            f.write("当前阶段：1" + chr(10) + "## 错题本" + chr(10) + "- [#q1] 误答" + chr(10))
        sc["fixture"] = fx
        t = os.path.join(d, "t.jsonl")
        md_edit = "当前阶段：1" + chr(10) + "## 错题本" + chr(10) + "- [#q9] 首回合手改的行" + chr(10)
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "首回合。", "phase_context": 1,
                                "files_after": {"study_state.json": st0,
                                                "study_progress.md": md_edit}},
                               ensure_ascii=False) + chr(10))
        r = D.evaluate(sc, t)
        self.assertGreaterEqual(r["metrics"]["md_write_after_state"], 1)

    def test_status_only_stale_md_flagged(self):
        # set-mistake-status 后 state 状态已改、生成视图还挂旧状态——状态背离也计手改；
        # 官方同步更新（turn3）不误报
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答", "status": "待复盘"}],
                          "confusion_log": []}, ensure_ascii=False)
        st2 = st1.replace("待复盘", "已订正")
        hdr = ("| 错题ID | 关联章节 | 错误原因分析 | 状态 |" + chr(10)
               + "| :--- | :--- | :--- | :--- |" + chr(10))
        md1 = "当前阶段：2" + chr(10) + "## 错题档案记录" + chr(10) + hdr + "| [#q1] | 1 | 误答 | 待复盘 |" + chr(10)
        md2 = md1.replace("待复盘", "已订正")
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "改状态。", "phase_context": 2,
             "files_after": {"study_state.json": st2, "study_progress.md": md1}},
            {"turn": 3, "assistant": "官方同步。", "phase_context": 2,
             "files_after": {"study_state.json": st2, "study_progress.md": md2}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)            # 只有 turn2 的状态背离计违规

    def test_same_count_id_divergence_in_dual_write_flagged(self):
        # 同数双写、state 与 md 都在变但 id 序列不同（state 进 q1、面板显示 q9）——id 键跨源可比
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        md_wrong = "当前阶段：2\n## 错题本\n- [#q9] 面板上是别的行\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md_wrong}},
        ])
        self.assertGreaterEqual(m["md_write_after_state"], 1)

    def test_md_event_without_snapshot_beside_state_rejected(self):
        # state 快照 + md 裸写事件（无 md 快照）——生成视图无从核对，按畸形输入拒收
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [], "confusion_log": []}, ensure_ascii=False)
        with self.assertRaises(D.DriftError):
            _eval_turns([{"turn": 1, "assistant": "x", "phase_context": 2,
                          "files_after": {"study_state.json": st1},
                          "events": [{"type": "write_file", "path": "study_progress.md"}]}])

    def test_nonwhitelist_status_divergence_flagged(self):
        # 手改状态列为词表外的合法词（已解决）——状态列取最后一格，不再靠白名单
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "mistake_archive": [{"id": "q1", "note": "误答", "status": "待复盘"}],
                          "confusion_log": []}, ensure_ascii=False)
        hdr = ("| 错题ID | 关联章节 | 错误原因分析 | 状态 |" + chr(10)
               + "| :--- | :--- | :--- | :--- |" + chr(10))
        md1 = "当前阶段：2" + chr(10) + "## 错题档案记录" + chr(10) + hdr + "| [#q1] | 1 | 误答 | 待复盘 |" + chr(10)
        md2 = md1.replace("待复盘", "已解决")
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md1}},
            {"turn": 2, "assistant": "手改状态。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md2}},
        ])
        self.assertEqual(m["md_write_after_state"], 1)

    def test_official_render_repair_of_stale_baseline_not_flagged(self):
        # fixture 初始 md 本就陈旧（与 fixture state 不一致）——首回合官方 render 修复
        # （md 追平 state、state 不动）不许被误判成手改
        import shutil
        sc = dict(D.load_scenario(os.path.join(DRIFT, "scenarios", "long_session_state.json")))
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        shutil.copytree(os.path.join(DRIFT, "fixtures", "mini_course_long_state"), fx)
        st0 = json.dumps({"version": 1, "current_phase": 1,
                          "mistake_archive": [{"id": "q1", "note": "误答"}],
                          "confusion_log": []}, ensure_ascii=False)
        with open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8") as f:
            f.write(st0)
        with open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8") as f:
            f.write("当前阶段：1" + chr(10) + "## 错题本" + chr(10) + "- [#q0] 陈旧的旧行" + chr(10))
        sc["fixture"] = fx
        t = os.path.join(d, "t.jsonl")
        md_fixed = "当前阶段：1" + chr(10) + "## 错题本" + chr(10) + "- [#q1] 误答" + chr(10)
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "render 修复。", "phase_context": 1,
                                "files_after": {"study_state.json": st0,
                                                "study_progress.md": md_fixed}},
                               ensure_ascii=False) + chr(10))
        r = D.evaluate(sc, t)
        self.assertEqual(r["metrics"]["md_write_after_state"], 0)

    def test_idless_note_divergence_in_dual_write_flagged(self):
        # 无 id 行同数同状态但 note 背离——state 的 note 必须原文出现在同节的生成行里
        st1 = json.dumps({"version": 1, "current_phase": 2,
                          "confusion_log": [{"chapter": "1", "note": "原始疑难笔记"}],
                          "mistake_archive": []}, ensure_ascii=False)
        hdr = ("| 疑难ID | 关联章节 | 疑难点 | 状态 |" + chr(10)
               + "| :--- | :--- | :--- | :--- |" + chr(10))
        md_wrong = ("当前阶段：2" + chr(10) + "## 💡 概念疑难点记录" + chr(10) + hdr
                    + "| - | 1 | 手改过的疑难 | 待回顾 |" + chr(10))
        m = _eval_turns([
            {"turn": 1, "assistant": "记录。", "phase_context": 2,
             "files_after": {"study_state.json": st1, "study_progress.md": md_wrong}},
        ])
        self.assertGreaterEqual(m["md_write_after_state"], 1)

    def test_stale_idless_baseline_not_seeded(self):
        # 基线行数/id 序列一致但无 id 行的 note 背离——不做种子，首回合官方 render 修复不误判
        import shutil
        sc = dict(D.load_scenario(os.path.join(DRIFT, "scenarios", "long_session_state.json")))
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        shutil.copytree(os.path.join(DRIFT, "fixtures", "mini_course_long_state"), fx)
        st0 = json.dumps({"version": 1, "current_phase": 1, "mistake_archive": [],
                          "confusion_log": [{"chapter": "1", "note": "事实源里的疑难"}]},
                         ensure_ascii=False)
        with open(os.path.join(fx, "study_state.json"), "w", encoding="utf-8") as f:
            f.write(st0)
        with open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8") as f:
            f.write("当前阶段：1" + chr(10) + "## 疑难点" + chr(10) + "- 陈旧基线里的旧疑难" + chr(10))
        sc["fixture"] = fx
        t = os.path.join(d, "t.jsonl")
        md_fixed = "当前阶段：1" + chr(10) + "## 疑难点" + chr(10) + "- 事实源里的疑难" + chr(10)
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "assistant": "render 修复。", "phase_context": 1,
                                "files_after": {"study_state.json": st0,
                                                "study_progress.md": md_fixed}},
                               ensure_ascii=False) + chr(10))
        r = D.evaluate(sc, t)
        self.assertEqual(r["metrics"]["md_write_after_state"], 0)

    def test_missing_transcript_exits_2(self):
        r = _cli(["--scenario", SCEN, "--transcript", os.path.join(TR, "does_not_exist.jsonl")])
        self.assertEqual(r.returncode, 2)

    def test_malformed_transcript_exits_2(self):
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "bad.jsonl")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not valid json }\n")
        r = _cli(["--scenario", SCEN, "--transcript", bad])
        self.assertEqual(r.returncode, 2)
        self.assertIn("JSON", r.stderr)

    def test_unknown_threshold_key_exits_2(self):
        d = tempfile.mkdtemp()
        sc = json.load(open(SCEN, encoding="utf-8"))
        sc["thresholds"] = {"nonsense_max": 0}
        scf = os.path.join(d, "s.json")
        json.dump(sc, open(scf, "w", encoding="utf-8"))
        r = _cli(["--scenario", scf, "--transcript", _tr("good_session.jsonl")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("未知阈值", r.stderr)

    # 7) token / cost fields → totals computed
    def test_token_cost_accounting(self):
        m = _eval("good_session.jsonl")["metrics"]["cost"]
        self.assertTrue(m["has_token_accounting"])
        self.assertEqual(m["total_tokens_in"], 15550)
        self.assertGreater(m["total_tokens_out"], 0)
        self.assertAlmostEqual(m["total_cost_usd"], 0.018, places=4)
        self.assertIsNotNone(m["context_growth_ratio"])

    # 8) transcript WITHOUT token/cost fields still works
    def test_without_token_fields_still_works(self):
        m = _eval("bad_plan_drift.jsonl")["metrics"]["cost"]      # this transcript carries no token fields
        self.assertFalse(m["has_token_accounting"])
        self.assertEqual(m["total_tokens_in"], 0)
        self.assertEqual(m["total_cost_usd"], 0.0)
        self.assertIsNone(m["context_growth_ratio"])

    # 9) wiki overread event detected
    def test_wiki_overread_detected(self):
        r = _eval("bad_wiki_overread.jsonl")
        self.assertEqual(r["metrics"]["overread_flag"], 1)
        self.assertIn("overread_max", _fail_thresholds(r))
        # good session (phase-scoped reads) does NOT flag overread
        self.assertEqual(_eval("good_session.jsonl")["metrics"]["overread_flag"], 0)

    # 10) mistake/confusion rows lost detected; and additions counted in the good session
    def test_progress_rows_lost_detected(self):
        r = _eval("bad_progress_loss.jsonl")
        self.assertGreater(r["metrics"]["progress_rows_lost"], 0)
        self.assertIn("progress_rows_lost_max", _fail_thresholds(r))
        good = _eval("good_session.jsonl")["metrics"]
        self.assertEqual(good["mistake_rows_added"], 2)          # two wrong answers archived
        self.assertEqual(good["confusion_rows_added"], 1)        # one confusion tracked
        self.assertEqual(good["progress_rows_lost"], 0)          # …and nothing ever silently dropped

    # 11) CLI --all runs committed scenarios and exits 0 for the good set
    def test_cli_all_exits_0(self):
        r = _cli(["--all"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertIn("long_session_basic", r.stdout)

    def test_cli_bad_transcript_exits_1(self):
        r = _cli(["--scenario", SCEN, "--transcript", _tr("bad_quiz_invention.jsonl")])
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)

    def test_json_out_written_to_explicit_path_only(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "summary.json")
        r = _cli(["--all", "--json-out", out])
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["all_passed"])
        names = sorted(r["scenario"] for r in data["results"])
        self.assertEqual(names, ["live_smoke_basic", "long_session_basic", "long_session_state",
                                 "mode_urgent_no_questions", "window_persist"])   # every committed scenario ran

    # extra coverage: wrong-phase and untagged detection via small synthetic transcripts
    def test_wrong_phase_quiz_detected(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "wp.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            # a phase-1 quiz turn that draws a phase-2 bank id → wrong_phase
            f.write(json.dumps({"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                                "assistant": "题目 [#tree_height_1] 一棵只有根节点的树，高度是多少？"},
                               ensure_ascii=False) + "\n")
        m = D.evaluate(D.load_scenario(SCEN), t)["metrics"]
        self.assertEqual(m["wrong_phase_quiz"], 1)
        self.assertEqual(m["invented"], 0)                       # it IS a bank id with its real question, just wrong phase

    def test_untagged_question_detected(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "ut.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"turn": 1, "user": "考我", "kind": "quiz",
                                "assistant": "1. 栈的访问顺序是什么？"}, ensure_ascii=False) + "\n")
        m = D.evaluate(D.load_scenario(SCEN), t)["metrics"]
        self.assertGreaterEqual(m["untagged_questions"], 1)

    # ---- regression guards for evasions caught by the pre-open adversarial review ----

    def test_bad_goal_drift_fails_goal_retention(self):
        r = _eval("bad_goal_drift.jsonl")
        self.assertFalse(r["passed"])
        self.assertIn("goal_retention_min", _fail_thresholds(r))
        self.assertLess(r["metrics"]["goal_retention"], 0.9)

    def test_plan_authorization_is_scoped_not_a_session_latch(self):
        # one early '改计划' must NOT authorize an unrelated, non-adjacent later plan mutation
        PLAN1 = "# 计划\n## 阶段1：栈\n"
        m = _eval_turns([
            {"turn": 1, "user": "帮我改计划里的错别字，别动顺序", "assistant": "错别字修好了。"},
            {"turn": 2, "user": "从阶段1考我", "assistant": "题目 [#stack_lifo_1] 栈?", "kind": "quiz", "phase_context": 1},
            {"turn": 3, "user": "继续", "assistant": "顺手删了阶段2。", "files_after": {"study_plan.md": PLAN1}},
        ])
        self.assertGreater(m["plan_mutations"], 0)

    def test_checkpoint_reset_checked_on_every_resume_not_just_first(self):
        # first resume correct, a LATER resume restarts an earlier phase → still detected
        p2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "进入阶段2。", "phase_context": 2, "files_after": {"study_progress.md": p2}},
            {"turn": 2, "user": "我回来了", "assistant": "继续阶段2。", "kind": "resume"},
            {"turn": 3, "user": "我又回来了", "assistant": "我们从阶段1重新开始吧。", "kind": "resume"},
        ])
        self.assertGreaterEqual(m["reset_detected"], 1)

    def test_explanation_detection_not_escapable_by_kind(self):
        # a turn whose user asked to explain is an explanation turn even if it mislabels kind
        m = _eval_turns([{"turn": 1, "user": "解释一下红黑树", "assistant": "红黑树就是这样，我瞎编的没依据。", "kind": "note"}])
        self.assertEqual(m["explanation_turns"], 1)
        self.assertEqual(m["provenance_fidelity"], 0.0)

    def test_quiz_with_no_tag_is_flagged_untagged(self):
        # asked to quiz but produced NO bank-tagged item (prose invention) → untagged, not silently clean
        m = _eval_turns([{"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "我给你出道题：跳表的期望复杂度是多少呢"}])
        self.assertGreaterEqual(m["untagged_questions"], 1)

    def test_overread_and_wrongphase_use_running_phase_without_phase_context(self):
        # NO phase_context on the turn → the running phase (init=1) is used, so the checks still fire
        over = _eval_turns([{"turn": 1, "user": "考我一道", "assistant": "题目 [#stack_lifo_1] 栈?",
                             "events": [{"type": "read_file", "path": "references/wiki/ch2_trees.md"}]}])
        self.assertEqual(over["overread_flag"], 1)
        wp = _eval_turns([{"turn": 1, "user": "考我", "kind": "quiz", "assistant": "题目 [#tree_height_1] 一棵只有根节点的树，高度是多少？"}])
        self.assertEqual(wp["wrong_phase_quiz"], 1)   # phase-2 id while running phase is 1

    def test_row_reword_keeping_id_is_not_a_false_loss(self):
        # editing a mistake row's prose while keeping its [#id] must NOT count as a lost row
        p1 = "当前阶段：1\n## 错题本\n- [#stack_lifo_1] 栈应为 LIFO\n## 疑难点\n（暂无）\n"
        p2 = "当前阶段：1\n## 错题本\n- [#stack_lifo_1] 栈应为 LIFO（后进先出）补充\n## 疑难点\n（暂无）\n"
        m = _eval_turns([{"turn": 1, "assistant": "记下了。", "files_after": {"study_progress.md": p1}},
                         {"turn": 2, "assistant": "补充了一下。", "files_after": {"study_progress.md": p2}}])
        self.assertEqual(m["progress_rows_lost"], 0)

    def test_goal_marker_min_positive_signal(self):
        # optional threshold: a session whose ASSISTANT never references the exam goal fails goal_marker_min
        d = tempfile.mkdtemp()
        sc = json.load(open(SCEN, encoding="utf-8"))
        sc["thresholds"] = {"goal_marker_min": 1}
        scf = os.path.join(d, "s.json")
        json.dump(sc, open(scf, "w", encoding="utf-8"))
        m = _eval_turns([{"turn": 1, "user": "考我", "assistant": "题目 [#stack_lifo_1] 栈?"}], scenario=scf)
        self.assertEqual(m["goal_marker_seen"], 0)
        r = D.evaluate(D.load_scenario(scf), _tr("bad_quiz_invention.jsonl"))
        self.assertIn("goal_marker_min", _fail_thresholds(r))   # assistant never says 期末/复习 → fails

    # ---- regression guards for the 8 P2s from Codex round-1 (real ingest-template + correctness) ----

    def test_parses_real_ingest_plan_table_and_checklist(self):
        plan = ("| 阶段 | 核心任务 | Wiki | 状态 |\n| :-- | :-- | :-- | :-- |\n"
                "| **阶段 1** | 栈 | `references/wiki/ch1.md` | 未开始 |\n"
                "| **阶段 2** | 树 | `references/wiki/ch2.md` | 未开始 |\n"
                "- [ ] **阶段 1**：栈\n- [ ] **阶段 2**：树\n")
        self.assertEqual(D.parse_plan_phases(plan), [1, 2])         # table+checklist deduped, order kept

    def test_parses_real_ingest_progress_table_and_checkpoint(self):
        prog = ("## ⏱️ 当前复习断点\n* **当前进行阶段**：阶段 2：树\n"
                "## ❌ 错题档案记录\n| 错题ID | 关联章节 | 题目内容简述 | 错误原因分析 | 状态 |\n"
                "| :--- | :--- | :--- | :--- | :--- |\n| [#stack_lifo_1] | 第1章 | 栈顺序 | 混淆LIFO | 未复习 |\n"
                "## 💡 概念疑难点记录\n| 序号 | 章节 | 疑难点 | 解答要点 | 状态 |\n"
                "| :--- | :--- | :--- | :--- | :--- |\n| 1 | 第1章 | 循环队列 | 取模 | 待复习 |\n")
        p = D.parse_progress(prog)
        self.assertEqual(p["phase"], 2)                            # '当前进行阶段：阶段 2' parsed
        self.assertEqual(len(p["mistake_rows"]), 1)                # table DATA row counted (header/sep excluded)
        self.assertEqual(len(p["confusion_rows"]), 1)

    def test_non_quiz_turn_with_archived_id_not_scored_as_quiz(self):
        # a progress/mistake summary that mentions an archived [#id] must NOT be scored as a quiz item
        m = _eval_turns([{"turn": 1, "user": "看看我的错题", "assistant": "已保留错题 [#tree_height_1]，复习一下。"}])
        self.assertEqual(m["wrong_phase_quiz"], 0)
        self.assertEqual(m["quiz_items"], 0)

    def test_followup_quiz_triggers_recognized(self):
        for u in ("再来一道阶段1的", "再来一题", "下一题", "来一道题"):
            m = _eval_turns([{"turn": 1, "user": u, "assistant": "跳表的期望复杂度是多少呢"}])
            self.assertGreaterEqual(m["untagged_questions"], 1, u)  # detected as a quiz → untagged flagged

    def test_goal_drift_matched_case_insensitively(self):
        m = _eval_turns([{"turn": 1, "user": "x", "assistant": "Switch Course to CS101, forget the exam."}])
        self.assertLess(m["goal_retention"], 1.0)

    def test_string_phase_value_normalized(self):
        self.assertEqual(D._as_phase("2"), 2)
        self.assertEqual(D._as_phase(2), 2)
        self.assertIsNone(D._as_phase("later"))
        # integration: a bank entry with "phase":"1" must not be flagged wrong-phase during phase 1
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write("## 阶段1：栈\n")
        open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8").write("当前阶段：1\n")
        json.dump([{"id": "q_str", "phase": "1"}],
                  open(os.path.join(fx, "references", "quiz_bank.json"), "w", encoding="utf-8"))
        sc = {"name": "s", "fixture": fx, "thresholds": {"wrong_phase_quiz_max": 0}}
        scf = os.path.join(d, "s.json")
        json.dump(sc, open(scf, "w", encoding="utf-8"))
        m = _eval_turns([{"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#q_str] ?"}], scenario=scf)
        self.assertEqual(m["wrong_phase_quiz"], 0)                 # "1" == 1, not a wrong-phase

    def test_vacuous_user_only_transcript_rejected(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "useronly.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write('{"turn":1,"user":"hi"}\n{"turn":2,"user":"anyone there?"}\n')
        r = _cli(["--scenario", SCEN, "--transcript", t])
        self.assertEqual(r.returncode, 2)                          # exercises nothing measurable → rejected

    # ---- regression guards for the 7 P2s from Codex round-2 ----

    def test_laundered_question_through_valid_id_is_invention(self):
        # a real [#id] with a FABRICATED question (not the bank item's) must count as invention
        m = _eval_turns([{"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#stack_lifo_1] 跳表的期望复杂度是多少？"}])
        self.assertEqual(m["invented"], 1)
        self.assertEqual(m["bank_backed"], 0)

    def test_untagged_extra_in_mixed_quiz_turn(self):
        # one valid tagged item + an extra untagged invented question in the SAME turn → untagged counted
        m = _eval_turns([{"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#stack_lifo_1] 栈遵循什么访问顺序？\n3. 跳表的复杂度是多少？"}])
        self.assertGreaterEqual(m["untagged_questions"], 1)

    def test_reset_detected_when_saved_phase_mentioned_before_restart(self):
        p2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"
        m = _eval_turns([
            {"turn": 1, "assistant": "到阶段2。", "phase_context": 2, "files_after": {"study_progress.md": p2}},
            {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "当前在阶段2，但我们先从阶段1开始复习。"},
        ])
        self.assertGreaterEqual(m["reset_detected"], 1)   # min phase mentioned (1) < expected (2)

    def test_phase_from_chapter_and_plan_map_not_naive_chnn(self):
        # official bank uses `chapter` (no `phase`), and phase 1 legitimately points at ch03 — the plan map
        # (not chNN==phase) must resolve scope, so reading/quizzing the phase's own chapter is NOT a violation
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write(
            "| 阶段 | 任务 | Wiki | 状态 |\n| :- | :- | :- | :- |\n"
            "| **阶段 1** | 图 | `references/wiki/ch03_graphs.md` | 未开始 |\n")
        open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8").write("当前阶段：1\n")
        json.dump([{"id": "g1", "chapter": "ch03_graphs", "question": "什么是邻接表？"}],
                  open(os.path.join(fx, "references", "quiz_bank.json"), "w", encoding="utf-8"), ensure_ascii=False)
        scf = os.path.join(d, "s.json")
        json.dump({"name": "s", "fixture": fx, "thresholds": {"wrong_phase_quiz_max": 0, "overread_max": 0}},
                  open(scf, "w", encoding="utf-8"))
        m = _eval_turns([{"turn": 1, "user": "从阶段1考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#g1] 什么是邻接表？",
                          "events": [{"type": "read_file", "path": "references/wiki/ch03_graphs.md"}]}], scenario=scf)
        self.assertEqual(m["wrong_phase_quiz"], 0)
        self.assertEqual(m["overread_flag"], 0)

    def test_no_assistant_turns_rejected(self):
        # a replay with only files_after/events but NO assistant output is malformed (dropped assistant text)
        d = tempfile.mkdtemp()
        t = os.path.join(d, "noassist.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write('{"turn":1,"events":[{"type":"read_file","path":"references/wiki/ch1_stack_queue.md"}]}\n')
        r = _cli(["--scenario", SCEN, "--transcript", t])
        self.assertEqual(r.returncode, 2)

    def test_malformed_fixture_json_is_exit_2_not_crash(self):
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write("## 阶段1\n")
        open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8").write("当前阶段：1\n")
        open(os.path.join(fx, "references", "quiz_bank.json"), "w", encoding="utf-8").write("{ not json }")
        scf = os.path.join(d, "s.json")
        json.dump({"name": "s", "fixture": fx, "thresholds": {}}, open(scf, "w", encoding="utf-8"))
        r = _cli(["--scenario", scf, "--transcript", _tr("good_session.jsonl")])
        self.assertEqual(r.returncode, 2)              # documented malformed-input code, not a traceback/exit 1
        self.assertNotIn("Traceback", r.stderr)

    # ---- regression guards for the 7 P2s from Codex round-3 ----
    _P2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"

    def test_completed_phase_mention_is_not_a_reset(self):
        # '阶段1已完成，继续阶段2' names an earlier phase but continues phase 2 → NOT a reset…
        ok = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._P2}},
                          {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "阶段1已经完成，我们继续阶段2。"}])
        self.assertEqual(ok["reset_detected"], 0)
        # …but a genuine '从阶段1重新开始' still is
        bad = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._P2}},
                           {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "我们从阶段1重新开始吧。"}])
        self.assertGreaterEqual(bad["reset_detected"], 1)

    def test_numeric_string_phase_context_normalized(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "kind": "quiz", "phase_context": "1",
                          "assistant": "题目 [#tree_height_1] 一棵只有根节点的树，高度是多少？"}])
        self.assertEqual(m["wrong_phase_quiz"], 1)     # "1" → phase 1; tree_height is phase 2 → wrong

    def test_dot_prefixed_wiki_read_path_counted(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "assistant": "题目 [#stack_lifo_1] 栈遵循什么访问顺序？",
                          "events": [{"type": "read_file", "path": "./references/wiki/ch2_trees.md"}]}])
        self.assertEqual(m["wiki_reads"], 1)
        self.assertEqual(m["overread_flag"], 1)        # ./-prefixed cross-phase read still caught

    def test_short_resume_trigger_and_case(self):
        m = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._P2}},
                         {"turn": 2, "user": "回来了", "assistant": "我们从阶段1重新开始。"}])   # no kind=resume
        self.assertGreaterEqual(m["reset_detected"], 1)

    def test_english_plan_change_authorization_case_insensitive(self):
        m = _eval_turns([{"turn": 1, "user": "Change the plan: drop phase 2", "assistant": "done, dropped 阶段2。",
                          "files_after": {"study_plan.md": "# plan\n## 阶段1：栈\n"}}])
        self.assertEqual(m["plan_mutations"], 0)       # authorized → not a mutation

    def test_non_numeric_threshold_value_exits_2(self):
        d = tempfile.mkdtemp()
        sc = json.load(open(SCEN, encoding="utf-8"))
        sc["thresholds"] = {"checkpoint_reset_max": "0"}
        scf = os.path.join(d, "s.json")
        json.dump(sc, open(scf, "w", encoding="utf-8"))
        r = _cli(["--scenario", scf, "--transcript", _tr("good_session.jsonl")])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_dotted_wiki_filename_in_plan_map(self):
        m = D.parse_plan_map("| **阶段 1** | 图 | `references/wiki/ch03.graphs.md` | x |\n")
        self.assertEqual(m, {1: {"ch03.graphs"}})

    # ---- regression guards for the 7 findings from Codex round-4 ----

    def test_in_place_phase_rename_is_a_mutation(self):
        # same phase NUMBERS but a renamed topic (阶段2：树 → 阶段2：职业规划) is an unauthorized plan change
        m = _eval_turns([{"turn": 1, "user": "继续", "assistant": "改了阶段2主题。",
                          "files_after": {"study_plan.md": "# p\n## 阶段1：栈与队列（Stack & Queue）\n## 阶段2：职业规划\n"}}])
        self.assertGreater(m["plan_mutations"], 0)

    def test_di_n_jieduan_reset_recognized(self):
        p2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"
        m = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": p2}},
                         {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "我们从第1阶段开始复习。"}])
        self.assertGreaterEqual(m["reset_detected"], 1)   # '第1阶段' order recognized

    def test_absolute_wiki_read_path_counted(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "assistant": "题目 [#stack_lifo_1] 栈遵循什么访问顺序？",
                          "events": [{"type": "read_file", "path": "/workspace/x/references/wiki/ch2_trees.md"}]}])
        self.assertEqual(m["wiki_reads"], 1)
        self.assertEqual(m["overread_flag"], 1)

    def test_same_line_appended_untagged_question_counted(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#stack_lifo_1] 栈遵循什么访问顺序？另外，跳表的期望复杂度是多少？"}])
        self.assertGreaterEqual(m["untagged_questions"], 1)

    def test_goal_marker_matched_case_insensitively(self):
        d = tempfile.mkdtemp()
        sc = json.load(open(SCEN, encoding="utf-8"))
        sc["thresholds"] = {"goal_marker_min": 1}
        scf = os.path.join(d, "s.json")
        json.dump(sc, open(scf, "w", encoding="utf-8"))
        m = _eval_turns([{"turn": 1, "user": "x", "assistant": "Exam review starts now."}], scenario=scf)
        self.assertEqual(m["goal_marker_seen"], 1)

    def test_numeric_chapter_matched_by_number_not_substring(self):
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write(
            "| 阶段 | 任务 | Wiki | 状态 |\n| :- | :- | :- | :- |\n"
            "| **阶段 1** | dp | `references/wiki/ch10_dp.md` | x |\n"
            "| **阶段 2** | basics | `references/wiki/ch01_basics.md` | x |\n")
        pm = D.parse_plan_map(open(os.path.join(fx, "study_plan.md"), encoding="utf-8").read())
        self.assertEqual(D.phase_of_chapter(pm, 1), 2)     # chapter 1 → ch01 (phase 2), NOT ch10 (phase 1)

    def test_non_dict_files_after_exits_2(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "b.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write('{"turn":1,"assistant":"x","files_after":"oops"}\n')
        r = _cli(["--scenario", SCEN, "--transcript", t])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    # ---- regression guards for the 9 findings from Codex round-5 ----
    _PP2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"

    def test_english_explanation_trigger_case_insensitive(self):
        m = _eval_turns([{"turn": 1, "user": "Explain stacks", "assistant": "stacks are LIFO, no label here."}])
        self.assertEqual(m["provenance_fidelity"], 0.0)   # detected as explanation, unlabeled → fails

    def test_injected_duplicate_phase_is_a_mutation(self):
        m = _eval_turns([{"turn": 1, "user": "x", "assistant": "y", "files_after": {"study_plan.md":
            "## 阶段1：栈与队列（Stack & Queue）\n## 阶段2：树（Trees）\n## 阶段2：职业规划\n"}}])
        self.assertGreater(m["plan_mutations"], 0)

    def test_negated_restart_is_not_a_reset(self):
        m = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._PP2}},
                         {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "不会从阶段1重新开始，我们继续阶段2。"}])
        self.assertEqual(m["reset_detected"], 0)

    def test_resume_skipping_ahead_is_a_reset(self):
        m = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._PP2}},
                         {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "我们从阶段3开始复习。"}])
        self.assertGreaterEqual(m["reset_detected"], 1)   # jumped ahead of the saved phase

    def test_prose_question_before_first_tag_counted(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "另外，跳表的期望复杂度是多少？\n题目 [#stack_lifo_1] 栈遵循什么访问顺序？"}])
        self.assertGreaterEqual(m["untagged_questions"], 1)

    def test_off_plan_wiki_read_flagged(self):
        m = _eval_turns([{"turn": 1, "user": "考我", "phase_context": 1,
                          "assistant": "题目 [#stack_lifo_1] 栈遵循什么访问顺序？",
                          "events": [{"type": "read_file", "path": "references/wiki/summary.md"}]}])
        self.assertEqual(m["overread_flag"], 1)           # a wiki scoped to no phase, read during phase 1

    def test_scenario_not_object_exits_2(self):
        d = tempfile.mkdtemp()
        scf = os.path.join(d, "s.json")
        open(scf, "w", encoding="utf-8").write("42")
        r = _cli(["--scenario", scf, "--transcript", _tr("good_session.jsonl")])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_non_object_event_element_exits_2(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "b.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write('{"turn":1,"assistant":"x","events":["oops"]}\n')
        r = _cli(["--scenario", SCEN, "--transcript", t])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_unscoped_bank_item_is_wrong_phase(self):
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write("## 阶段1：栈\n")
        open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8").write("当前阶段：1\n")
        json.dump([{"id": "u1", "question": "泛题？"}],       # no phase, no chapter
                  open(os.path.join(fx, "references", "quiz_bank.json"), "w", encoding="utf-8"), ensure_ascii=False)
        scf = os.path.join(d, "s.json")
        json.dump({"name": "s", "fixture": fx, "thresholds": {"wrong_phase_quiz_max": 0}},
                  open(scf, "w", encoding="utf-8"))
        m = _eval_turns([{"turn": 1, "user": "考我", "kind": "quiz", "phase_context": 1,
                          "assistant": "题目 [#u1] 泛题？"}], scenario=scf)
        self.assertEqual(m["wrong_phase_quiz"], 1)

    # ---- regression guards for the 6 findings from Codex round-6 (final) ----
    _PPP2 = "当前阶段：2\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n"

    def test_plan_map_preferred_over_chn_fallback(self):
        d = tempfile.mkdtemp()
        fx = os.path.join(d, "fx")
        os.makedirs(os.path.join(fx, "references"))
        open(os.path.join(fx, "study_plan.md"), "w", encoding="utf-8").write(
            "| 阶段 | t | Wiki | s |\n| :- | :- | :- | :- |\n| **阶段 1** | x | `references/wiki/ch03_graphs.md` | x |\n")
        open(os.path.join(fx, "study_progress.initial.md"), "w", encoding="utf-8").write("当前阶段：1\n")
        json.dump([{"id": "g1", "chapter": "ch03_graphs", "question": "q?"}],
                  open(os.path.join(fx, "references", "quiz_bank.json"), "w", encoding="utf-8"), ensure_ascii=False)
        scf = os.path.join(d, "s.json")
        json.dump({"name": "s", "fixture": fx, "thresholds": {"overread_max": 0}}, open(scf, "w", encoding="utf-8"))
        # phase 1 is mapped to ch03; reading ch01 (chNN=1) during phase 1 is off-plan → overread
        off = _eval_turns([{"turn": 1, "user": "x", "phase_context": 1, "assistant": "读",
                            "events": [{"type": "read_file", "path": "references/wiki/ch01_basics.md"}]}], scenario=scf)
        self.assertEqual(off["overread_flag"], 1)
        own = _eval_turns([{"turn": 1, "user": "x", "phase_context": 1, "assistant": "读",
                            "events": [{"type": "read_file", "path": "references/wiki/ch03_graphs.md"}]}], scenario=scf)
        self.assertEqual(own["overread_flag"], 0)

    def test_non_string_fixture_field_exits_2(self):
        d = tempfile.mkdtemp()
        scf = os.path.join(d, "s.json")
        json.dump({"name": "s", "fixture": 123, "thresholds": {}}, open(scf, "w"))
        r = _cli(["--scenario", scf, "--transcript", _tr("good_session.jsonl")])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_non_string_assistant_field_exits_2(self):
        d = tempfile.mkdtemp()
        t = os.path.join(d, "b.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write('{"turn":1,"assistant":123}\n')
        r = _cli(["--scenario", SCEN, "--transcript", t])
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_confusion_table_status_update_is_not_a_loss(self):
        pa = ("当前阶段：1\n## 疑难点\n| 序号 | 疑难点 | 状态 |\n| :- | :- | :- |\n| 1 | 循环队列 | 待回顾 |\n")
        pb = ("当前阶段：1\n## 疑难点\n| 序号 | 疑难点 | 状态 |\n| :- | :- | :- |\n| 1 | 循环队列 | 已回顾 |\n")
        m = _eval_turns([{"turn": 1, "assistant": "记", "files_after": {"study_progress.md": pa}},
                         {"turn": 2, "assistant": "改状态", "files_after": {"study_progress.md": pb}}])
        self.assertEqual(m["progress_rows_lost"], 0)   # keyed by 序号, status change isn't a lost row

    def test_restarting_the_saved_phase_is_not_a_reset(self):
        ok = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._PPP2}},
                          {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "我们重新开始阶段2的树复习。"}])
        self.assertEqual(ok["reset_detected"], 0)
        bad = _eval_turns([{"turn": 1, "assistant": "到2", "phase_context": 2, "files_after": {"study_progress.md": self._PPP2}},
                           {"turn": 2, "user": "回来了", "kind": "resume", "assistant": "咱们从头开始吧。"}])
        self.assertGreaterEqual(bad["reset_detected"], 1)   # 从头开始 (no phase named) still resets

    def test_human_readable_chapter_string_resolved(self):
        pm = D.parse_plan_map("| **阶段 1** | x | `references/wiki/ch01_basics.md` | x |\n")
        self.assertEqual(D.phase_of_chapter(pm, "第1章"), 1)   # '第1章' → chapter 1 → ch01 (phase 1)

    # 12) no network / LLM / API key / deps; --llm skeleton never returns success
    def test_no_network_llm_or_dep_in_source(self):
        with open(RUN, encoding="utf-8") as f:
            src = f.read()
        for banned in ("import requests", "import anthropic", "import openai", "import numpy",
                       "urllib.request", "http.client", "import socket", "claude -p"):
            self.assertNotIn(banned, src)
        # B3：subprocess 现在合法用于 opt-in 的 --llm 委托（转正给 run_live_smoke），但确定性 replay
        # 路径必须仍纯净——所有 subprocess.run/Popen/call 调用的**位置**都只能落在 run_llm 函数体内。
        # 用 span（字符偏移区间）比对而非字面量：字面量 "subprocess.run" 到处都一样，比它没意义（Codex R5LD）。
        import re as _re
        m = _re.search(r"\ndef run_llm\(.*?(?=\ndef )", src, _re.S)
        self.assertIsNotNone(m, "找不到 run_llm 函数体")
        lo, hi = m.start(), m.end()
        calls = list(_re.finditer(r"subprocess\.(?:run|Popen|call)\b", src))
        self.assertTrue(calls, "预期 run_llm 里有 subprocess 委托调用")
        for c in calls:
            self.assertTrue(lo <= c.start() < hi,
                            "subprocess 调用在偏移 %d 逃出了 run_llm 体 [%d,%d)（确定性 replay 必须无子进程）"
                            % (c.start(), lo, hi))

    def test_llm_opt_in_delegates_never_succeeds_without_agent(self):
        # B3：--llm 转正——委托给 run_live_smoke 真管线，但仍绝不无 agent 就报成功
        r = _cli(["--llm"])                                      # 未 opt-in → 门控拒绝
        self.assertEqual(r.returncode, 2)
        r2 = _cli(["--llm"], env={"RUN_SKILL_DRIFT_LLM": "1"})   # opt-in 但没给 --agent-cmd/--turns
        self.assertNotEqual(r2.returncode, 0)                   # 委托的 live runner 缺必需参数 → 非 0，绝不 0

    # 13) fixture is self-authored plain text; no copyrighted / binary materials committed
    def test_fixture_is_self_authored_text(self):
        exts = set()
        total = 0
        for base, dirs, files in os.walk(DRIFT):
            dirs[:] = [d for d in dirs if d != "__pycache__"]    # skip transient, gitignored bytecode
            for fn in files:
                exts.add(os.path.splitext(fn)[1].lower())
                total += os.path.getsize(os.path.join(base, fn))
        self.assertTrue(exts <= {".py", ".md", ".json", ".jsonl"}, "unexpected file types: %s" % exts)
        self.assertLess(total, 200 * 1024, "drift/ unexpectedly large — no big/copyrighted blobs allowed")
        # the wiki is our own short CS common-knowledge, not course material
        self.assertTrue(os.path.isfile(os.path.join(FIX, "references", "wiki", "ch1_stack_queue.md")))
        bank = json.load(open(os.path.join(FIX, "references", "quiz_bank.json"), encoding="utf-8"))
        self.assertEqual({q["id"] for q in bank},
                         {"stack_lifo_1", "queue_fifo_1", "tree_height_1", "bst_property_1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
