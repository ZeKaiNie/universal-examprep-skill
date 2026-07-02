# -*- coding: utf-8 -*-
"""Root-level, CI-reachable tests for the Tier 4 long-horizon drift harness (benchmark/drift/run_drift.py).

CI only discovers the repo-root tests/, so the T4 harness is covered HERE. Pure stdlib; no network / LLM /
API keys / deps / paid run. Verifies: the good long-session transcript passes all thresholds; each bad
transcript fails for its intended reason; malformed input exits 2; token/cost accounting; overread and
row-loss detection; the --llm skeleton never returns success; and the fixture is self-authored text."""
import json
import os
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

    # 6) missing / malformed transcript exits 2
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
        self.assertEqual(names, ["live_smoke_basic", "long_session_basic"])   # every committed scenario ran

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
                       "urllib.request", "http.client", "import socket", "import subprocess", "claude -p"):
            self.assertNotIn(banned, src)

    def test_llm_skeleton_never_succeeds(self):
        r = _cli(["--llm"])                                      # not opted in
        self.assertEqual(r.returncode, 2)
        r2 = _cli(["--llm"], env={"RUN_SKILL_DRIFT_LLM": "1"})   # opted in, but unimplemented
        self.assertEqual(r2.returncode, 3)                      # never 0 — a skeleton must not report success

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
