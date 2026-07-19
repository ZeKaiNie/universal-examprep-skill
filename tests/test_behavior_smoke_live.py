# -*- coding: utf-8 -*-
"""B2 — behavior_smoke LIVE single-turn smoke is WIRED (no longer a skeleton).

The live path (`run_behavior_smoke.py --llm`) drives a real agent per scenario and applies the SAME
deterministic detectors as `--mock` to each reply. These tests exercise the wiring WITHOUT a paid LLM:
  (1) live_reply_check reuses the mock detectors — the committed golden passes, the negative fails;
  (2) the full run_llm pipe runs end-to-end against a STUB agent (a tiny local script that returns each
      scenario's golden reply), so every reply-verifiable scenario PASSES and transcripts are written.
Opt-in gating stays: no env + no --agent-cmd → refuses (returns 2), never runs `claude` in CI."""
import io
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = os.path.join(ROOT, "benchmark", "behavior_smoke")
sys.path.insert(0, BS)
import run_behavior_smoke as B  # noqa: E402

SCEN = json.loads(io.open(os.path.join(BS, "scenarios.json"), encoding="utf-8").read())
FIXTURE = os.path.join(BS, SCEN.get("fixture", "fixtures/mini_course"))
_BY_NAME = {s["name"]: s for s in SCEN["scenarios"]}
REPLY_VERIFIABLE = ["quiz_bank_only", "scope_override", "provenance_labels", "zero_basic_key_question",
                    "teaching_template", "time_budget_no_questions", "knowledge_window_recheck",
                    "language_first_ask", "visual_first_assets", "checkpoint_recovery",
                    "artifact_mode_routing"]


def _read(rel):
    with io.open(os.path.join(BS, rel), encoding="utf-8") as f:
        return f.read()


class LiveReplyCheckReusesMockDetectors(unittest.TestCase):
    """live_reply_check runs the SAME positive detectors --mock uses (no logic drift)."""

    def test_golden_reply_passes_each_scenario(self):
        for name in REPLY_VERIFIABLE:
            sc = _BY_NAME[name]
            res = B.live_reply_check(name, sc, _read(sc["mock_output"]), FIXTURE)
            self.assertIsNotNone(res, name)
            ok, detail = res
            self.assertTrue(ok, f"{name}: golden reply should pass live detector — {detail}")

    def test_negative_reply_fails_where_a_negative_exists(self):
        for name in REPLY_VERIFIABLE:
            sc = _BY_NAME[name]
            if not sc.get("mock_negative"):
                continue
            ok, _detail = B.live_reply_check(name, sc, _read(sc["mock_negative"]), FIXTURE)
            self.assertFalse(ok, f"{name}: negative reply must fail the live detector")

    def test_state_mutation_scenarios_are_skipped_not_faked(self):
        # a one-shot `claude -p` can only TALK — file-mutation scenarios must return None (SKIP), never
        # PASS. checkpoint_recovery is NOT here: its resume message is reply-verifiable (R6 U4).
        for name in ("hint_skip_mistake_archive", "confusion_tracking", "no_python_fallback"):
            self.assertIsNone(B.live_reply_check(name, _BY_NAME[name], "anything", FIXTURE), name)


class RunLlmGating(unittest.TestCase):
    def test_optin_refused_without_env_or_agent_cmd(self):
        old = os.environ.pop("RUN_SKILL_BEHAVIOR_LLM", None)
        try:
            self.assertEqual(B.run_llm(["--llm"]), 2)
        finally:
            if old is not None:
                os.environ["RUN_SKILL_BEHAVIOR_LLM"] = old


class RunLlmEndToEndWithStubAgent(unittest.TestCase):
    """The full pipe drives an agent per scenario and applies detectors — proven with a stub agent
    (returns each scenario's golden reply) so it's deterministic and paid-LLM-free."""

    def _stub_path(self, tmp):
        # a tiny agent: given the prompt (argv[1]), find the scenario whose student turn it contains,
        # print that scenario's golden mock_output. Emulates a perfectly-compliant agent.
        p = os.path.join(tmp, "stub_agent.py")
        src = (
            "import sys, json, io, os\n"
            "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "BS = %r\n"
            "d = json.load(io.open(os.path.join(BS, 'scenarios.json'), encoding='utf-8'))\n"
            "for sc in d['scenarios']:\n"
            "    if sc.get('prompt') and sc['prompt'] in prompt and sc.get('mock_output'):\n"
            "        sys.stdout.reconfigure(encoding='utf-8')\n"
            "        sys.stdout.write(io.open(os.path.join(BS, sc['mock_output']), encoding='utf-8').read())\n"
            "        break\n"
        ) % BS
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(src)
        return p

    def test_pipe_runs_and_all_reply_scenarios_pass(self):
        tmp = tempfile.mkdtemp()
        stub = self._stub_path(tmp)
        out = os.path.join(tmp, "out")
        # JSON-array agent-cmd is the cross-platform exact form call_agent accepts (no shell parsing);
        # {prompt} is substituted as a single argv element even though it is huge/multiline.
        agent_cmd = json.dumps([sys.executable, stub, "{prompt}"])
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = B.run_llm(["--llm", "--agent-cmd", agent_cmd, "--out-dir", out, "--timeout", "60"])
        finally:
            sys.stdout = old
        report = buf.getvalue()
        self.assertEqual(rc, 0, "all reply-verifiable scenarios should PASS against a compliant stub\n" + report)
        # every reply-verifiable scenario ran and wrote a transcript; state ones were SKIPped
        for name in REPLY_VERIFIABLE:
            self.assertTrue(os.path.isfile(os.path.join(out, "live_%s.md" % name)), name)
        self.assertIn("passed,", report)
        self.assertEqual(report.count("[FAIL]"), 0, report)

    def test_noncompliant_stub_makes_scenarios_fail(self):
        # a stub that returns an empty/irrelevant reply → detectors fail → run_llm returns 1 (not a
        # silent pass): proves the pipe actually applies the detectors to the live reply.
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "empty_agent.py")
        # ASCII-only reply so call_agent accepts it as valid UTF-8; it is simply non-compliant, so the
        # DETECTORS (not call_agent's byte guard) are what must reject it.
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("import sys\nsys.stdout.write('irrelevant reply, not compliant')\n")
        agent_cmd = json.dumps([sys.executable, p, "{prompt}"])
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = B.run_llm(["--llm", "--agent-cmd", agent_cmd, "--out-dir", os.path.join(tmp, "o"),
                            "--timeout", "60"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 1, "a non-compliant agent must make the live smoke FAIL, not pass")


class LiveRoundOneFixes(unittest.TestCase):
    """Regressions for the Codex round-1 findings on the live wiring."""

    def test_T1_agent_runs_in_throwaway_copy_pristine_fixture_untouched(self):
        # a MUTATING stub agent (appends an invented item to the bank in its cwd) must neither dirty
        # the tracked fixture nor make quiz_bank_only pass off the altered bank.
        tmp = tempfile.mkdtemp()
        agent = os.path.join(tmp, "mutating_agent.py")
        with io.open(agent, "w", encoding="utf-8") as f:
            f.write("import sys, json, io, os\n"
                    "sys.stdout.reconfigure(encoding='utf-8')\n"
                    "bank = os.path.join(os.getcwd(), 'references', 'quiz_bank.json')\n"
                    "with io.open(bank, encoding='utf-8') as _f:\n"
                    "    d = json.load(_f)\n"
                    "d.append({'id': 'INVENTED_999', 'question': 'x', 'chapter': 1})\n"
                    "with io.open(bank, 'w', encoding='utf-8') as _f:\n"
                    "    json.dump(d, _f)\n"
                    "sys.stdout.write('题目 [#INVENTED_999] x？')\n")
        fixture_bank = os.path.join(FIXTURE, "references", "quiz_bank.json")
        with io.open(fixture_bank, encoding="utf-8") as f:
            before = f.read()
        agent_cmd = json.dumps([sys.executable, agent, "{prompt}"])
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            B.run_llm(["--llm", "--agent-cmd", agent_cmd, "--out-dir", os.path.join(tmp, "o"),
                       "--timeout", "60"])
        finally:
            sys.stdout = old
        # the tracked fixture bank is byte-identical after the run (agent mutated only its sandbox copy)
        with io.open(fixture_bank, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(before, after, "live agent must NOT mutate the tracked fixture")
        self.assertNotIn("INVENTED_999", after)
        # quiz_bank_only reports FAIL against the pristine oracle (INVENTED_999 not in the real bank)
        self.assertIn("[FAIL] quiz_bank_only", buf.getvalue())

    def test_T2_live_teaching_template_catches_unsolicited_closers(self):
        # golden passes; the golden + an appended closer block must FAIL the live check
        sc = _BY_NAME["teaching_template"]
        good = _read(sc["mock_output"])
        self.assertTrue(B.live_reply_check("teaching_template", sc, good, FIXTURE)[0])
        with_closer = good + "\n\n【易错点】：注意别漏条件。\n【3分钟速记】：口诀……"
        self.assertFalse(B.live_reply_check("teaching_template", sc, with_closer, FIXTURE)[0],
                         "unsolicited 收尾块 after the source block must fail live too")

    def test_T3_unknown_flag_outside_llm_is_rejected(self):
        with self.assertRaises(SystemExit):     # argparse .error() raises SystemExit(2)
            B.main(["--mock", "--out-dir", "x"])   # --out-dir is an llm-only flag; must not silently pass

    def test_T4_live_prompt_exposes_visual_asset_paths(self):
        sc = _BY_NAME["visual_first_assets"]
        prompt = B._live_prompt(FIXTURE, sc)
        bank = json.loads(_read(os.path.join(os.path.relpath(FIXTURE, BS), "references", "quiz_bank.json")))
        vis = [q for q in bank if isinstance(q, dict)
               and (q.get("requires_assets") or q.get("maybe_requires_assets")
                    or q.get("question_text_status") in ("stub", "page_reference"))]
        self.assertTrue(vis, "fixture should have a visual-required item")
        for q in vis:
            for a in (q.get("assets") or []):
                if isinstance(a, dict) and a.get("path"):
                    self.assertIn(str(a["path"]).replace("\\", "/"), prompt,
                                  "the live prompt must expose each visual item's asset path")


class LiveRoundTwoFixes(unittest.TestCase):
    """Regressions for the Codex round-2 findings."""

    def test_T1_run_llm_rejects_typo_subflag(self):
        # a typo'd live sub-flag must fail loudly (a paid run must not proceed on the default timeout)
        with self.assertRaises(SystemExit):
            B.run_llm(["--llm", "--agent-cmd", "echo {prompt}", "--timeot", "5"])

    def test_T2_scope_override_requires_a_served_item(self):
        sc = _BY_NAME["scope_override"]
        # a reply that prints ONLY the override warning and serves no question must FAIL live
        only_warn = "⚠️ 临时覆盖你的 homework-only 范围偏好。"
        self.assertFalse(B.live_reply_check("scope_override", sc, only_warn, FIXTURE)[0])
        # the committed golden (override + a served [#id]) still passes
        self.assertTrue(B.live_reply_check("scope_override", sc, _read(sc["mock_output"]), FIXTURE)[0])

    def test_T3_live_prompt_omits_answer_side_asset_paths(self):
        # a synthetic fixture item carrying BOTH a question-side figure and an answer-side worked_solution:
        # the prompt must expose the question-side path but NEVER the answer-side one (no answer leak).
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "references"))
        bank = [{"id": "vq", "question": "看图求解", "chapter": 1, "requires_assets": True,
                 "assets": [{"role": "figure", "path": "references/assets/prompt_fig.png"},
                            {"role": "worked_solution", "path": "references/assets/answer_sol.png"}]}]
        with io.open(os.path.join(tmp, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(bank, f, ensure_ascii=False)
        prompt = B._live_prompt(tmp, {"prompt": "出这题"})
        self.assertIn("references/assets/prompt_fig.png", prompt)
        self.assertNotIn("answer_sol.png", prompt, "answer-side asset path must NOT leak into the prompt")


class LiveRoundThreeFixes(unittest.TestCase):
    """Regressions for the Codex round-3 findings."""

    def test_T1_scope_override_rejects_invented_id(self):
        sc = _BY_NAME["scope_override"]
        # override declared + an INVENTED id (not in the bank) must FAIL live (bank-only violation)
        invented = "⚠️ 临时覆盖你的 homework-only 范围偏好\n\n题目 [#INVENTED_999] 看图？"
        self.assertFalse(B.live_reply_check("scope_override", sc, invented, FIXTURE)[0])

    def test_T2_negative_budget_flag_rejected(self):
        with self.assertRaises(SystemExit):
            B.run_llm(["--llm", "--agent-cmd", "echo {prompt}", "--max-out", "-2000"])
        with self.assertRaises(SystemExit):
            B.run_llm(["--llm", "--agent-cmd", "echo {prompt}", "--timeout", "0"])

    def test_T4_ai_answer_title_warning_enforced_live(self):
        # a teaching reply whose source block uses the ⚠️ AI-generated label but whose ⑤ title lacks the
        # full warning must FAIL live (ai_answer mode), same as --mock's ai variant.
        sc = _BY_NAME["teaching_template"]
        ai_good = _read(sc["mock_ai_answer"]) if sc.get("mock_ai_answer") else None
        if ai_good:
            self.assertTrue(B.live_reply_check("teaching_template", sc, ai_good, FIXTURE)[0],
                            "the AI-answer golden (⚠️ in both ⑤ title and source label) should pass")
        warn_missing = _read(sc["mock_negative_warn_title"]) if sc.get("mock_negative_warn_title") else None
        if warn_missing:
            self.assertFalse(B.live_reply_check("teaching_template", sc, warn_missing, FIXTURE)[0],
                             "an AI answer missing the ⚠️ title warning must fail live too")


class LiveRoundFourFixes(unittest.TestCase):
    def test_T2_live_prompt_includes_standard_answers(self):
        # answer-dependent scenarios (teaching_template / zero_basic) must hand the agent the bank's
        # standard answers as hidden context, so it grades against the bank instead of prior knowledge.
        # Check the ACTUAL embedding token「（标准答案:」— the guard header for serve-only scenarios also
        # contains the bare word「标准答案」, so a substring check would false-pass (R5 T1 regression).
        prompt = B._live_prompt(FIXTURE, {"name": "teaching_template", "prompt": "精讲这题"})
        bank = json.loads(_read(os.path.join(os.path.relpath(FIXTURE, BS), "references", "quiz_bank.json")))
        keyed = [q for q in bank if isinstance(q, dict)
                 and (q.get("answer") not in (None, "", []) or q.get("answer_keywords") not in (None, "", []))]
        self.assertTrue(keyed, "fixture should have items with answers/keywords")
        self.assertIn("（标准答案:", prompt,
                      "an answer-dependent live prompt must EMBED the bank's standard answers")


class LiveRoundFiveFixes(unittest.TestCase):
    """Regressions for the Codex round-5 findings: (T1) serve-only quiz turns must not be handed / must
    not leak the answer key; (T2) scope_override must content-match, not accept a valid-ID sticker."""

    def test_T1_serve_only_prompt_omits_answer_key(self):
        # a serve-only quiz scenario must NOT receive the standard answers in its prompt — the agent
        # cannot leak a key it was never given (the leak source is removed at the prompt layer).
        p = B._live_prompt(FIXTURE, {"name": "quiz_bank_only", "prompt": "出题", "chapter": 1})
        self.assertNotIn("（标准答案:", p,
                         "serve-only quiz prompt must not embed the answer key")
        self.assertIn("不要泄露", p, "serve-only prompt should instruct the agent not to reveal answers")

    def test_T1_quiz_serve_answer_leak_is_caught(self):
        sc = _BY_NAME["quiz_bank_only"]
        qmap = B.load_quiz_bank_map(FIXTURE)
        ch = sc.get("chapter")
        ch1 = [(i, v) for i, v in qmap.items()
               if ch is None or str(v.get("chapter")) == str(ch) or str(v.get("phase")) == str(ch)]
        need = max(sc.get("min_questions", 1), 3)
        served = "\n".join("题目 [#%s] %s" % (i, v["question"]) for i, v in ch1[:need])
        # a clean serve (questions only, no key) PASSES
        self.assertTrue(B.live_reply_check("quiz_bank_only", sc, served, FIXTURE)[0],
                        "a clean serve of >= min_questions bank items must pass")
        # the SAME serve that also dumps a distinctive answer key to the student FAILS (answer_leak)
        leak = next(str(v["answer"]) for i, v in ch1
                    if v.get("answer") and len(re.sub(r"\s+", "", str(v["answer"]))) >= 8)
        leaked = served + "\n参考答案：" + leak
        ok, detail = B.live_reply_check("quiz_bank_only", sc, leaked, FIXTURE)
        self.assertFalse(ok, "dumping a served item's answer key must fail the quiz-serve smoke")
        self.assertIn("answer_leak=True", detail)

    def test_T1_short_answer_key_with_marker_is_caught(self):
        # a dumped answer key of SHORT keys ('A' / 'FIFO') next to a reveal marker must be caught,
        # even though each key on its own is < 8 chars (the earlier len>=8-only guard missed this).
        qmap = B.load_quiz_bank_map(FIXTURE)
        ch1_ans = [v.get("answer") for i, v in qmap.items() if str(v.get("chapter")) == "1"]
        leak = "答案速查：mc_q1 选 A；mc_q3 填 FIFO。"
        self.assertTrue(B._reply_leaks_answer_key(leak, ch1_ans),
                        "short-key answer dump next to a reveal marker must be flagged")

    def test_T1_markdown_mangled_answer_is_caught(self):
        # a long answer mangled with **bold** must not dodge the verbatim check (normalization strips md)
        qmap = B.load_quiz_bank_map(FIXTURE)
        mc_q2 = qmap["mc_q2"]["answer"]
        mangled = "参考答案：" + mc_q2.replace("先进先出", "先进**先出**", 1)
        self.assertNotEqual(mangled, "参考答案：" + mc_q2)  # it really is mangled
        self.assertTrue(B._reply_leaks_answer_key(mangled, [mc_q2]),
                        "a markdown-mangled long answer must still be caught")

    def test_T1_option_labels_and_instruction_not_flagged(self):
        # a legit serve that shows mc_q1's options (which contain 'FIFO'/'LIFO', == mc_q3's answer token)
        # plus a benign 「回复答案」 instruction must NOT be flagged (marker requirement kills the collision)
        qmap = B.load_quiz_bank_map(FIXTURE)
        ch1_ans = [v.get("answer") for i, v in qmap.items() if str(v.get("chapter")) == "1"]
        legit = ("1. [#mc_q1] 栈的存取顺序？\n"
                 "   A. 先进后出（LIFO） B. 先进先出（FIFO） C. 随机存取 D. 优先级最高先出\n"
                 "3. [#mc_q3] 队列的存取顺序简称是____（英文缩写）。\n"
                 "请直接回复你的答案。")
        self.assertFalse(B._reply_leaks_answer_key(legit, ch1_ans),
                         "option labels / question body / a 「回复答案」 instruction must not false-positive")

    def test_T2_scope_override_rejects_valid_id_wrong_body(self):
        sc = _BY_NAME["scope_override"]
        # a VALID bank id (mc_q1 = a stack MCQ) stuck on a MISMATCHED body (a Venn-figure prompt):
        # content-match must reject it even though the id + override line are present.
        wrong = ("⚠️ 临时覆盖你的 homework-only 范围偏好\n\n"
                 "题目 [#mc_q1] 请根据题面图判断阴影区域表示哪一个集合关系。")
        self.assertFalse(B.live_reply_check("scope_override", sc, wrong, FIXTURE)[0],
                         "a valid ID on a mismatched question body must fail content-match")
        # the corrected golden (real [#vis_q1] + its own body) still passes
        self.assertTrue(B.live_reply_check("scope_override", sc, _read(sc["mock_output"]), FIXTURE)[0])

    def test_T2_mock_scope_override_content_matches_bank(self):
        # the strengthened --mock check now content-matches the served item too (mock↔live parity)
        sc = _BY_NAME["scope_override"]
        ok, detail = B.check_scenario_mock("scope_override", sc, FIXTURE)
        self.assertTrue(ok, "strengthened mock scope_override must pass on the corrected golden: " + detail)
        self.assertIn("visualmatch", detail)


class LiveRoundSixFixes(unittest.TestCase):
    """Regressions for the Codex round-6 findings — the paid live path must not pass vacuously."""

    def test_U1_sandbox_exposes_skill_contract_and_workspace(self):
        # the live sandbox must contain BOTH the fixture workspace AND the repo's skill contract, so a
        # paid run exercises THIS skill instead of a generic agent guessing from the prompt stub.
        sandbox, cwd = B._prepare_live_sandbox(FIXTURE)
        try:
            self.assertTrue(os.path.isfile(os.path.join(cwd, "SKILL.md")), "SKILL.md must be in the sandbox")
            self.assertTrue(os.path.isdir(os.path.join(cwd, "skills")), "skills/ must be in the sandbox")
            # v4-P2: the root SKILL.md is a router into locales/ — the full-entry packs must ship too,
            # or the live agent reads a router that dispatches into nothing (SKILL.en.md is retired).
            self.assertTrue(os.path.isfile(os.path.join(cwd, "locales", "zh", "SKILL.md")),
                            "locales/zh/SKILL.md (full-entry pack) must be in the sandbox")
            self.assertTrue(os.path.isfile(os.path.join(cwd, "locales", "en", "SKILL.md")),
                            "locales/en/SKILL.md (full-entry pack) must be in the sandbox")
            self.assertFalse(os.path.exists(os.path.join(cwd, "SKILL.en.md")),
                             "retired SKILL.en.md must not reappear in the sandbox")
            self.assertTrue(os.path.isfile(os.path.join(cwd, "references", "quiz_bank.json")),
                            "the fixture workspace must still be present")
        finally:
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_U2_scope_override_rejects_nonvisual_bank_item(self):
        sc = _BY_NAME["scope_override"]
        # a VALID, content-matching bank item that is NOT a lecture visual (mc_q1 = the stack MCQ) must
        # FAIL: the scenario asked for figure items, so serving a non-visual item ignores the request.
        bank = B.load_quiz_bank_map(FIXTURE)
        nonvis = ("⚠️ 临时覆盖你的 homework-only 范围偏好\n\n题目 [#mc_q1] " + bank["mc_q1"]["question"])
        self.assertFalse(B.live_reply_check("scope_override", sc, nonvis, FIXTURE)[0],
                         "serving a non-visual bank item after the override must fail (visual-only map)")
        # the corrected golden (visual [#vis_q1]) still passes
        self.assertTrue(B.live_reply_check("scope_override", sc, _read(sc["mock_output"]), FIXTURE)[0])

    def test_U3_template_scenarios_reject_invented_ids(self):
        # a seven-step/source-structured reply that tags a FABRICATED id must FAIL both template scenarios
        for name in ("teaching_template", "zero_basic_key_question"):
            sc = _BY_NAME[name]
            good = _read(sc["mock_output"])
            tag_id = B.extract_question_ids(good)[0]
            faked = good.replace("[#" + tag_id + "]", "[#FAKE_999]")
            self.assertNotEqual(faked, good, name + ": fixture golden should tag a bank id to rewrite")
            self.assertFalse(B.live_reply_check(name, sc, faked, FIXTURE)[0],
                             name + ": a fabricated [#id] must fail the live template check")
            self.assertTrue(B.live_reply_check(name, sc, good, FIXTURE)[0],
                            name + ": the real-id golden must still pass")

    def test_U4_checkpoint_recovery_is_live_verifiable(self):
        sc = _BY_NAME["checkpoint_recovery"]
        # it must NOT be skipped, the resume golden passes, and a restart-at-phase-1 reply fails
        res = B.live_reply_check("checkpoint_recovery", sc, _read(sc["mock_output"]), FIXTURE)
        self.assertIsNotNone(res, "checkpoint_recovery must be reply-verifiable, not skipped")
        self.assertTrue(res[0], "the resume golden (refers to current phase) must pass")
        restart = "欢迎回来！我们从阶段 1 重新开始复习吧。"
        self.assertFalse(B.live_reply_check("checkpoint_recovery", sc, restart, FIXTURE)[0],
                         "a reply that restarts at phase 1 must fail")

    # a fully self-contained seven-step reply tagged with a REAL teacher id (mc_q1 = stack) but teaching a
    # WHOLLY fabricated off-bank topic (Dijkstra) — a real citation on invented content.
    _FABRICATED_ON_REAL_ID = (
        "【重点题精讲】[#mc_q1] Dijkstra 最短路径\n\n"
        "① 题面图：\n本题无图。\n\n"
        "② 这题在问什么：\n求二叉堆实现的 Dijkstra 单源最短路径的时间复杂度。\n\n"
        "③ 图里要读的量：\n顶点数 V、边数 E。\n\n"
        "④ 核心公式：\n每次取最小 O(logV)，松弛 E 次 → O((V+E)logV)。\n\n"
        "⑤ 逐步演算：\n1. 初始化距离。\n2. 反复取堆顶最小并松弛邻边。\n3. 得 O((V+E)logV)。\n\n"
        "⑥ 为什么这个答案成立：\n每条边的松弛和每次取最小值共同决定复杂度，因此得到上述数量级。\n\n"
        "⑦ 知识点溯源：\n第 7 章《图》 · references/wiki/ch07_graph.md · 原文 [lecture12.pdf 第 3 页](../lecture12.pdf#page=3)\n\n"
        "题目来源：hw07.pdf 第 1 页（homework）｜答案来源：hw07_sol.pdf 第 1 页｜🟢 来自资料\n")

    def test_U3_real_id_on_fabricated_question_fails(self):
        # the sharper hole: a real bank id on a WHOLLY fabricated off-bank question must FAIL.
        bank = B.load_quiz_bank_map(FIXTURE)
        self.assertFalse(B._reply_teaches_bank_topic(self._FABRICATED_ON_REAL_ID, bank),
                         "a real id ([#mc_q1]=stack) on a Dijkstra body must fail the topic anchor")
        sc = _BY_NAME["teaching_template"]
        self.assertFalse(B.live_reply_check("teaching_template", sc, self._FABRICATED_ON_REAL_ID, FIXTURE)[0],
                         "teaching_template must reject a real id on a fabricated question")
        self.assertTrue(B.live_reply_check("teaching_template", sc, _read(sc["mock_output"]), FIXTURE)[0],
                        "the on-topic golden must still pass")

    def test_U5_default_out_dir_is_outside_repo(self):
        # a naive --llm run (no --out-dir) must not write transcripts (which embed answer keys) into the
        # worktree — the default must resolve to a dir OUTSIDE the repo tree. Behavioral, not a source grep.
        tmp = tempfile.mkdtemp()
        stub = os.path.join(tmp, "noop_agent.py")
        with io.open(stub, "w", encoding="utf-8") as f:
            f.write("import sys\nsys.stdout.write('noop')\n")  # ASCII, non-compliant is fine here
        agent_cmd = json.dumps([sys.executable, stub, "{prompt}"])
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            B.run_llm(["--llm", "--agent-cmd", agent_cmd, "--timeout", "60"])  # NO --out-dir
        finally:
            sys.stdout = old
        report = buf.getvalue()
        m = re.search(r"transcripts → (.+?)/live_\*\.md", report)
        self.assertTrue(m, "run_llm must report the chosen transcript dir\n" + report)
        out_dir = os.path.abspath(m.group(1))
        try:
            inside = os.path.commonpath([out_dir, B.ROOT]) == B.ROOT
        except ValueError:
            inside = False  # different drive on Windows → definitely outside the repo
        self.assertFalse(inside, "default transcript dir %r must be OUTSIDE the repo %r" % (out_dir, B.ROOT))

    def test_U6_bank_ai_item_labeled_green_fails(self):
        # a served item the BANK marks ai_generated (mc_q8) must be forced into ⚠️ mode: relabeling it 🟢
        # must FAIL even though the reply's own label now says 🟢 (bank metadata overrides the self-label).
        sc = _BY_NAME["teaching_template"]
        bank = B.load_quiz_bank_map(FIXTURE)
        self.assertEqual(bank["mc_q8"]["source"], "ai_generated", "precondition: mc_q8 is ai_generated")
        ai_good = _read(sc["mock_ai_answer"])  # a compliant [#mc_q8] reply WITH the ⚠️ AI-answer label
        self.assertTrue(B.live_reply_check("teaching_template", sc, ai_good, FIXTURE)[0],
                        "the ⚠️-labeled ai_generated golden must pass")
        # strip the ⚠️ warning to a 🟢 label; on-topic + template stay intact, so the ONLY change is the
        # provenance mode — which the bank now forces to ⚠️, so this must FAIL (U6 closes this false pass).
        greened = ai_good.replace("⚠️ AI生成答案，非老师/教材提供", "🟢 来自资料")
        self.assertNotIn("⚠️", greened)
        self.assertFalse(B._reply_has_ai_generated_label(greened), "reply no longer self-labels AI")
        self.assertFalse(B.live_reply_check("teaching_template", sc, greened, FIXTURE)[0],
                         "a bank ai_generated item relabeled 🟢 must fail (bank forces the ⚠️ contract)")


if __name__ == "__main__":
    unittest.main()
