# -*- coding: utf-8 -*-
"""B4 run_matrix.py 回归：通用 Tier-3 矩阵 runner 的 --mock 端到端 + config 校验 + 断点/确定性/诚实判分。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
SCRIPT = os.path.join(BENCH, "run_matrix.py")
FIXTURE_CFG = os.path.join(BENCH, "fixtures", "mini_course_matrix", "config.json")
sys.path.insert(0, BENCH)
import run_matrix as RM  # noqa: E402


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, encoding="utf-8")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class MockPipeline(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="b4mx_")
        self.addCleanup(shutil.rmtree, self.out, True)

    def _summary(self):
        with open(os.path.join(self.out, "summary.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_mock_end_to_end_produces_summary(self):
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("answers.jsonl", "scores.jsonl", "summary.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)
        s = self._summary()
        self.assertEqual(sorted(s["models"]), ["haiku", "opus"])
        self.assertEqual(s["arms"], ["closedbook", "rawfiles", "skill"])
        self.assertEqual(s["courses"], ["minios"])
        self.assertEqual(s["n_items"], 5)
        self.assertEqual(len(s["matrix"]), 6)          # 2 models × 3 arms

    def test_mock_honest_scoring(self):
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        s = self._summary()
        cell = s["matrix"]["opus|skill"]
        self.assertEqual(cell["n_answerable"], 4)
        self.assertEqual(cell["n_oos"], 1)
        self.assertEqual(cell["abstention_oos"], 1.0)   # 越界探针弃答 → 正确
        self.assertEqual(cell["n_infra_error"], 0)

    def test_answers_and_scores_row_shapes(self):
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        ans = _rows(os.path.join(self.out, "answers.jsonl"))
        sco = _rows(os.path.join(self.out, "scores.jsonl"))
        self.assertEqual(len(ans), 30)                  # 2×3×5
        for a in ans:
            self.assertLessEqual({"course", "model", "arm", "item_id", "status", "answer"}, set(a))
        for sc in sco:
            self.assertLessEqual({"course", "model", "arm", "item_id", "correct", "abstained",
                                  "answerable"}, set(sc))
        # 越界探针在每个 model×arm 下都弃答
        oos = [sc for sc in sco if sc["item_id"] == "mx_probe_oos"]
        self.assertEqual(len(oos), 6)
        self.assertTrue(all(sc["abstained"] and sc["correct"] for sc in oos))

    def test_resumable_second_run_skips(self):
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        r2 = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        self.assertIn("本次待处理 0", r2.stdout)

    def test_deterministic(self):
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        a1 = _read(os.path.join(self.out, "answers.jsonl"))
        out2 = tempfile.mkdtemp(prefix="b4mx2_")
        self.addCleanup(shutil.rmtree, out2, True)
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", out2)
        a2 = _read(os.path.join(out2, "answers.jsonl"))
        self.assertEqual(a1, a2)

    def test_limit(self):
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out, "--limit", "4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(_rows(os.path.join(self.out, "answers.jsonl"))), 4)

    def test_bare_mock_uses_fixture_config(self):
        # 不给 --config 也能跑（自带 fixture 课程），写入临时 results
        r = _run("--mock", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._summary()["courses"], ["minios"])

    def test_mock_never_calls_claude(self):
        # 猴补 gen.run_claude 抛错，--mock 仍完整跑通 → 证明 mock 路径不 shell claude
        import gen
        orig = gen.run_claude
        gen.run_claude = lambda *a, **k: (_ for _ in ()).throw(AssertionError("mock 不该 shell claude"))
        try:
            cfg = RM.load_config(FIXTURE_CFG)
            cfg["results_dir"] = self.out                # 别污染 fixture 目录
            RM.run(cfg, mock=True, limit=6)
        finally:
            gen.run_claude = orig


class ConfigValidation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="b4cfg_")
        self.addCleanup(shutil.rmtree, self.d, True)

    def _cfg(self, obj):
        p = os.path.join(self.d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        return p

    def test_missing_courses_exits_2(self):
        self.assertEqual(_run("--mock", "--config", self._cfg({"models": ["opus"]})).returncode, 2)

    def test_duplicate_course_name_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}, {"name": "a", "items": "i.jsonl"}]}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_bad_primary_course_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["closedbook"],
               "primary_course": "nope"}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_missing_items_file_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "does_not_exist.jsonl"}], "arms": ["closedbook"]}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_arm_missing_workspace_key_exits_2(self):
        # 选了 rawfiles/skill 臂但没声明 raw_ws/skill_ws → fail-loud
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["rawfiles"]}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_explicit_empty_arms_exits_2(self):
        # 显式 "arms":[] 不当"缺席"回落全默认矩阵 —— fail-loud
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": []}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_explicit_empty_models_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["closedbook"], "models": []}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_question_only_items_rejected(self):
        # 误指到只有 id+question 的盲测题面文件（*_q.jsonl）→ fail-loud（缺金标无法判分）
        with open(os.path.join(self.d, "q.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q"}) + "\n")
        r = _run("--mock", "--config", self._cfg({"courses": [{"name": "a", "items": "q.jsonl"}],
                                                  "arms": ["closedbook"]}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("answer_type", r.stderr)

    def test_answerable_without_gold_rejected(self):
        with open(os.path.join(self.d, "nogold.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q", "answer_type": "factual",
                                "answerable": True}) + "\n")
        r = _run("--mock", "--config", self._cfg({"courses": [{"name": "a", "items": "nogold.jsonl"}],
                                                  "arms": ["closedbook"]}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("gold_answer", r.stderr)

    def _items_cfg(self, item):
        with open(os.path.join(self.d, "i.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        return self._cfg({"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["closedbook"]})

    def test_answerable_non_bool_exits_2(self):
        # "answerable":"false"（字符串）会被 bool() 当 True → 拒绝
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "a", "answer_type": "factual",
             "answerable": "false"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("布尔", r.stderr)

    def test_bad_numeric_tolerance_exits_2(self):
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "5", "answer_type": "numeric",
             "answerable": True, "tolerance": "abc"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("tolerance", r.stderr)

    def test_negative_limit_exits_2(self):
        self.assertEqual(_run("--mock", "--config", FIXTURE_CFG, "--limit", "-1").returncode, 2)

    def test_mock_and_real_together_exits_2(self):
        self.assertEqual(_run("--mock", "--real", "--config", FIXTURE_CFG).returncode, 2)

    def test_non_bool_mock_config_exits_2(self):
        # "mock":"false"（字符串）会被 bool() 当 True → 拒绝
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["closedbook"], "mock": "false"}
        r = _run("--mock", "--config", self._cfg(cfg))
        self.assertEqual(r.returncode, 2)
        self.assertIn("mock", r.stderr)

    def test_empty_items_file_exits_2(self):
        with open(os.path.join(self.d, "empty.jsonl"), "w", encoding="utf-8") as f:
            f.write("# 只有注释\n\n")
        r = _run("--mock", "--config", self._cfg({"courses": [{"name": "a", "items": "empty.jsonl"}],
                                                  "arms": ["closedbook"]}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("空题集", r.stderr)

    def test_non_numeric_gold_exits_2(self):
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "abc", "answer_type": "numeric",
             "answerable": True}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("gold_answer 非数字", r.stderr)

    def test_negative_tolerance_exits_2(self):
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "5", "answer_type": "numeric",
             "answerable": True, "tolerance": -1}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("不能为负", r.stderr)

    def test_unknown_answer_type_exits_2(self):
        # 拼错 "numerci" → fail-loud（否则走非数值路、忽略 tolerance、静默污染）
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "a", "answer_type": "numerci",
             "answerable": True}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("answer_type", r.stderr)

    def test_relative_paths_resolved_to_config_dir(self):
        # config 里的相对 items 路径按 config 目录解析
        with open(os.path.join(self.d, "i.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q", "gold_answer": "a",
                                "answer_type": "factual", "answerable": True}) + "\n")
        cfg = RM.load_config(self._cfg({"courses": [{"name": "a", "items": "i.jsonl"}],
                                        "arms": ["closedbook"]}))
        self.assertEqual(os.path.normpath(cfg["_courses_by_name"]["a"]["items"]),
                         os.path.normpath(os.path.join(self.d, "i.jsonl")))

    def test_non_list_arms_exits_2(self):
        # "arms":"skill"（漏方括号）不再被逐字符迭代成假臂 —— fail-loud
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": "skill"}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_non_list_models_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "models": "opus"}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_unknown_arm_exits_2(self):
        cfg = {"courses": [{"name": "a", "items": "i.jsonl"}], "arms": ["skil"]}
        self.assertEqual(_run("--mock", "--config", self._cfg(cfg)).returncode, 2)

    def test_non_string_course_name_exits_2(self):
        # 整数 name 不再直落 TypeError 原生 traceback
        r = _run("--mock", "--config", self._cfg({"courses": [{"name": 5, "items": "i.jsonl"}]}))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_malformed_items_line_exits_2_with_lineno(self):
        with open(os.path.join(self.d, "bad.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q", "gold_answer": "a",
                                "answer_type": "factual", "answerable": True}) + "\n")
            f.write("{not valid json\n")
        r = _run("--mock", "--config", self._cfg({"courses": [{"name": "a", "items": "bad.jsonl"}],
                                                  "arms": ["closedbook"]}))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("第 2 行", r.stderr)


class FixesRegression(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="b4fix_")
        self.addCleanup(shutil.rmtree, self.out, True)

    def test_score_row_faithfulness_none_no_crash(self):
        # judge_error 判分 faithfulness=None 时 score_row 不崩（float(None) 曾是 blocker）
        import judge as J
        orig = J.judge_answer
        J.judge_answer = lambda item, ans, ask, judge_repeats=1: {
            "id": item["id"], "correct": False, "hallucinated": 0, "abstained": False,
            "judge_error": 1, "faithfulness": None}
        try:
            row, jf = RM.score_row("c", "m", "closedbook",
                                   {"id": "q", "answerable": True}, "ans", mock=True)
            self.assertIsNone(row["faithfulness"])
            self.assertEqual(row["judge_error"], 1)
        finally:
            J.judge_answer = orig

    def test_mock_forces_judge_model_label(self):
        # config judge_model=haiku + --mock → summary 标 mock（占位不冒充真判分）
        d = tempfile.mkdtemp(prefix="b4jm_")
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.dirname(FIXTURE_CFG)
        cfg = {"courses": [{"name": "minios", "items": os.path.join(src, "items.jsonl"),
                            "combined": os.path.join(src, "materials", "_combined.txt"),
                            "skill_ws": os.path.join(src, "skill_ws"),
                            "raw_ws": os.path.join(src, "raw_ws")}],
               "models": ["opus"], "arms": ["skill"], "judge_model": "haiku", "mock": True}
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        _run("--mock", "--config", cfgp, "--results-dir", self.out)
        with open(os.path.join(self.out, "summary.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["judge_model"], "mock")

    def test_published_results_dir_guard(self):
        published = os.path.join(BENCH, "results", "matrix")
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", published)
        self.assertEqual(r.returncode, 2)
        self.assertIn("已发布", r.stderr)

    def test_resume_dedupes_from_answers_without_cache(self):
        # 模拟崩溃后：answers.jsonl 有一行、但 gen_cache 缺失 → 续跑不重复该任务
        os.makedirs(self.out, exist_ok=True)
        # 先跑一遍拿到真实的一行 answer，再删掉 cache 模拟"写了 answer 没写 cache"
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out, "--limit", "1")
        cache = os.path.join(self.out, "gen_cache.jsonl")
        if os.path.isfile(cache):
            os.remove(cache)
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out, "--limit", "1")
        with open(os.path.join(self.out, "answers.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        keys = [(r["course"], r["model"], r["arm"], r["item_id"]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))       # 无重复行

    def test_classify_timeout_is_transient(self):
        self.assertEqual(RM._classify("TIMEOUT"), "transient")

    def test_cache_key_pipe_safe(self):
        # 课程名带 '|' 不与别的任务碰撞
        a = RM._cache_key("a|b", "m", "closedbook", "q")
        b = RM._cache_key("a", "b|m", "closedbook", "q")
        self.assertNotEqual(a, b)

    def test_mock_real_mode_mixing_refused(self):
        # 先 --mock 后 --real 同 results_dir → 拒绝混用（不静默把占位当真跑）
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        r = _run("--real", "--config", FIXTURE_CFG, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("混用", r.stderr)

    def test_changed_config_same_resultsdir_refused(self):
        # 同 results_dir 换了 config（不同 arms/models）→ 拒绝（旧行会和新配置混聚出错摘要）
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        d = tempfile.mkdtemp(prefix="b4fp_")
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.dirname(FIXTURE_CFG)
        cfg2 = {"courses": [{"name": "minios", "items": os.path.join(src, "items.jsonl"),
                             "combined": os.path.join(src, "materials", "_combined.txt"),
                             "skill_ws": os.path.join(src, "skill_ws"),
                             "raw_ws": os.path.join(src, "raw_ws")}],
                "models": ["opus"], "arms": ["closedbook"], "mock": True}   # 与 fixture 不同指纹
        cfg2p = os.path.join(d, "config.json")
        with open(cfg2p, "w", encoding="utf-8") as f:
            json.dump(cfg2, f)
        r = _run("--mock", "--config", cfg2p, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不同的 config", r.stderr)

    def test_items_content_edit_refused(self):
        # 就地编辑 items 内容（路径没变）→ 指纹变 → 拒绝复用旧 results_dir（旧 score 不当仍有效）
        d = tempfile.mkdtemp(prefix="b4ic_")
        self.addCleanup(shutil.rmtree, d, True)
        itemsp = os.path.join(d, "items.jsonl")

        def write_items(gold):
            with open(itemsp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "x", "question": "q", "gold_answer": gold,
                                    "answer_type": "factual", "answerable": True}) + "\n")
        write_items("a")
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "c", "items": itemsp}], "models": ["opus"],
                       "arms": ["closedbook"], "mock": True}, f)
        _run("--mock", "--config", cfgp, "--results-dir", self.out)
        write_items("b")                                  # 就地改 gold（路径不变，内容变）
        r = _run("--mock", "--config", cfgp, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不同的 config", r.stderr)

    def test_material_content_edit_refused(self):
        # 就地改 combined 材料内容（路径不变）且**选了 material 臂** → 指纹变 → 拒绝复用旧 results_dir。
        # （加固批语义收窄：没选 material 臂时判分不读 combined，改它不拒续跑——见 HardeningB4X。）
        d = tempfile.mkdtemp(prefix="b4mc_")
        self.addCleanup(shutil.rmtree, d, True)
        itemsp = os.path.join(d, "items.jsonl")
        with open(itemsp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q", "gold_answer": "a",
                                "answer_type": "factual", "answerable": True}) + "\n")
        combp = os.path.join(d, "combined.txt")
        with open(combp, "w", encoding="utf-8") as f:
            f.write("material version one")
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "c", "items": itemsp, "combined": combp}],
                       "models": ["opus"], "arms": ["closedbook", "material"], "mock": True}, f)
        _run("--mock", "--config", cfgp, "--results-dir", self.out)
        with open(combp, "w", encoding="utf-8") as f:
            f.write("material version TWO")                # 就地改材料内容
        r = _run("--mock", "--config", cfgp, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不同的 config", r.stderr)

    def test_bad_items_then_fixed_not_refused(self):
        # 首跑 items 坏 → 死在建任务前、不写 .run_meta；修好后同目录续跑不被误判成"不同 config"
        d = tempfile.mkdtemp(prefix="b4bf_")
        self.addCleanup(shutil.rmtree, d, True)
        itemsp = os.path.join(d, "items.jsonl")
        with open(itemsp, "w", encoding="utf-8") as f:
            f.write("# 空题集\n")
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "c", "items": itemsp}], "models": ["opus"],
                       "arms": ["closedbook"], "mock": True}, f)
        r1 = _run("--mock", "--config", cfgp, "--results-dir", self.out)
        self.assertEqual(r1.returncode, 2)                # 空题集死
        self.assertFalse(os.path.isfile(os.path.join(self.out, ".run_meta.json")))
        with open(itemsp, "w", encoding="utf-8") as f:    # 修好
            f.write(json.dumps({"id": "x", "question": "q", "gold_answer": "a",
                                "answer_type": "factual", "answerable": True}) + "\n")
        r2 = _run("--mock", "--config", cfgp, "--results-dir", self.out)
        self.assertEqual(r2.returncode, 0, r2.stderr)     # 不被误判成不同 config

    def test_judge_infra_failure_flagged(self):
        # 判分侧 claude 撞配额/超时（非 JSON 错误串）→ score_row 标 judge_infra_failed（不落盘、下次重判）
        import gen
        orig = gen.run_claude
        gen.run_claude = lambda prompt, model, **kw: ("hit your limit; resets later", None)
        try:
            item = {"id": "q", "question": "?", "gold_answer": "xyz",
                    "answer_type": "factual", "answerable": True}
            row, jf = RM.score_row("c", "m", "closedbook", item, "unrelated answer",
                                   mock=False, judge_model="haiku")
            self.assertTrue(jf)
        finally:
            gen.run_claude = orig

    def test_valid_judge_json_with_marker_word_not_infra(self):
        # 合法判分 JSON（内容恰好含 "resets" 等 gen.classify 词表词）不误判为 infra
        import gen
        orig = gen.run_claude
        gen.run_claude = lambda prompt, model, **kw: (
            '{"claims":[{"claim":"resets the cache","supported":1}],"correct":1,"abstained":0}', None)
        try:
            item = {"id": "q", "question": "?", "gold_answer": "xyz",
                    "answer_type": "factual", "answerable": True}
            row, jf = RM.score_row("c", "m", "closedbook", item, "unrelated answer",
                                   mock=False, judge_model="haiku")
            self.assertFalse(jf)                         # 判分成功 → 不是 infra（尽管含 resets）
        finally:
            gen.run_claude = orig

    def _seed_answers(self):
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, "answers.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"course": "minios", "model": "opus", "arm": "closedbook",
                                "item_id": "x", "status": "ok", "answer": "a"}) + "\n")

    def test_artifacts_without_meta_refused(self):
        # 有 answers 产物但无 .run_meta（如没带 dotfile 复制）→ 拒绝（无法核对隔离）
        self._seed_answers()
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn(".run_meta", r.stderr)

    def test_artifacts_with_corrupt_meta_refused(self):
        self._seed_answers()
        with open(os.path.join(self.out, ".run_meta.json"), "w", encoding="utf-8") as f:
            f.write("{corrupt json")
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn(".run_meta", r.stderr)

    def test_generate_real_marker_answer_not_dropped(self):
        # 合法答案含 "resets" 等词（ok=True）→ 不被当配额错丢弃
        orig = RM.real_answer
        RM.real_answer = lambda *a, **k: ("the cache resets and usage limit is 5", 0.01, True, "")
        try:
            ans, cost, kind = RM._generate_real({}, {}, "opus", "closedbook", {"question": "q"})
            self.assertEqual(kind, "ok")
            self.assertIn("resets", ans)
        finally:
            RM.real_answer = orig

    def test_generate_real_infra_error_classified_hard(self):
        # 真错误文本（ok=False）才走 classify → hard
        orig = RM.real_answer
        RM.real_answer = lambda *a, **k: ("", None, False, "hit your limit; resets later")
        try:
            ans, cost, kind = RM._generate_real({}, {}, "opus", "closedbook", {"question": "q"})
            self.assertEqual(kind, "hard")
        finally:
            RM.real_answer = orig

    def test_reordered_models_refused(self):
        # 同课程同模型集但顺序不同 → 指纹不同 → 拒绝复用（--limit 切片顺序会变）
        d = tempfile.mkdtemp(prefix="b4ord_")
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.dirname(FIXTURE_CFG)
        course = {"name": "minios", "items": os.path.join(src, "items.jsonl"),
                  "combined": os.path.join(src, "materials", "_combined.txt"),
                  "skill_ws": os.path.join(src, "skill_ws"), "raw_ws": os.path.join(src, "raw_ws")}

        def write_cfg(models):
            cfgp = os.path.join(d, "config.json")
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({"courses": [course], "models": models, "arms": ["closedbook"],
                           "mock": True}, f)
            return cfgp
        _run("--mock", "--config", write_cfg(["opus", "haiku"]), "--results-dir", self.out)
        r = _run("--mock", "--config", write_cfg(["haiku", "opus"]), "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不同的 config", r.stderr)

    def test_resume_rescores_answer_without_score(self):
        # 崩溃后"有答案没判分"：删掉 scores 模拟 → 续跑重判该题（不重生成、不重复 answer）
        _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out, "--limit", "1")
        os.remove(os.path.join(self.out, "scores.jsonl"))
        r = _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out, "--limit", "1")
        self.assertIn("重判 1", r.stdout)
        with open(os.path.join(self.out, "answers.jsonl"), encoding="utf-8") as f:
            self.assertEqual(len([l for l in f if l.strip()]), 1)     # answer 不重复
        with open(os.path.join(self.out, "scores.jsonl"), encoding="utf-8") as f:
            self.assertEqual(len([l for l in f if l.strip()]), 1)     # 重判补上了 score

    def test_secondary_course_absent_not_demanded(self):
        # 2 课程配置，--limit 只覆盖课程1 → 聚合不因课程2 缺席而硬失败（finding 5）
        d = tempfile.mkdtemp(prefix="b4sec_")
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.dirname(FIXTURE_CFG)
        course = lambda nm: {"name": nm, "items": os.path.join(src, "items.jsonl"),
                             "combined": os.path.join(src, "materials", "_combined.txt"),
                             "skill_ws": os.path.join(src, "skill_ws"),
                             "raw_ws": os.path.join(src, "raw_ws")}
        cfg = {"courses": [course("c1"), course("c2")], "models": ["opus"], "arms": ["closedbook"],
               "primary_course": "c1", "secondary_course": "c2", "mock": True}
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        # c1 有 5 题×1模型×1臂=5 个任务，排在 c2 之前 → --limit 5 只覆盖 c1
        r = _run("--mock", "--config", cfgp, "--results-dir", self.out, "--limit", "5")
        self.assertEqual(r.returncode, 0, r.stderr)               # 不因 c2 缺席失败
        with open(os.path.join(self.out, "summary.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["courses"], ["c1"])


class HardeningB4X(unittest.TestCase):
    """加固批（对抗审计 9 条）：账本坏行死锁、崩溃残段自愈、meta 缺键、指纹盲点、NaN/Inf 金标、陈旧 summary。"""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="b4hx_")
        self.addCleanup(shutil.rmtree, self.out, True)

    def _mock_run(self):
        return _run("--mock", "--config", FIXTURE_CFG, "--results-dir", self.out)

    # ---- NaN / Infinity 金标与容差 ----
    def _items_cfg(self, item):
        d = tempfile.mkdtemp(prefix="b4hi_")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "i.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        p = os.path.join(d, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "a", "items": os.path.join(d, "i.jsonl")}],
                       "arms": ["closedbook"]}, f)
        return p

    def test_nan_tolerance_rejected(self):
        # json.loads 接受裸 NaN 字面量、float() 不报错——tol=NaN 任何比较 False、每答案判错，须显式拦
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "5", "answer_type": "numeric",
             "answerable": True, "tolerance": float("nan")}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("有限数", r.stderr)

    def test_infinity_tolerance_rejected(self):
        # tol=Infinity → 任何答案都在容差内 → 全判对
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": "5", "answer_type": "numeric",
             "answerable": True, "tolerance": float("inf")}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("有限数", r.stderr)

    def test_nan_gold_rejected(self):
        r = _run("--mock", "--config", self._items_cfg(
            {"id": "x", "question": "q", "gold_answer": float("nan"), "answer_type": "numeric",
             "answerable": True}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("有限数", r.stderr)

    # ---- 账本坏行：中间坏行 fail-loud（不再和 aggregate 形成死锁），崩溃残段自愈 ----
    def test_interior_malformed_score_row_fails_loud(self):
        self._mock_run()
        sp = os.path.join(self.out, "scores.jsonl")
        with open(sp, encoding="utf-8") as f:
            lines = f.read().splitlines(True)
        lines[0] = '{"course": "minios", "model": "opus", "arm": "closedb\n'   # 截断的半行 + 换行
        with open(sp, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        r = self._mock_run()
        self.assertEqual(r.returncode, 2)                # 以前：静默跳过→重判→aggregate 死→每次续跑白做
        self.assertIn("第 1 行", r.stderr)

    def test_missing_key_score_row_fails_loud(self):
        self._mock_run()
        sp = os.path.join(self.out, "scores.jsonl")
        with open(sp, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        del rows[0]["item_id"]                           # 合法 JSON 但缺必备键
        with open(sp, "w", encoding="utf-8") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        r = self._mock_run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("KeyError", r.stderr)

    def test_partial_tail_self_heals(self):
        # 崩溃只写了半行（末行无换行且非法）→ 视作未写入：截掉 + 续跑补齐，无重复、聚合成功
        self._mock_run()
        sp = os.path.join(self.out, "scores.jsonl")
        with open(sp, encoding="utf-8") as f:
            lines = f.read().splitlines(True)
        with open(sp, "w", encoding="utf-8") as f:
            f.write("".join(lines[:-1]))
            f.write(lines[-1].rstrip("\n")[:30])         # 半行、无换行
        r = self._mock_run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("残段", r.stderr)
        rows = _rows(sp)
        keys = [(d["course"], d["model"], d["arm"], d["item_id"]) for d in rows]
        self.assertEqual(len(keys), len(set(keys)))      # 无重复
        self.assertEqual(len(rows), 30)

    def test_complete_tail_without_newline_not_duplicated(self):
        # 末行完整只是缺换行 → 行有效：补换行再续写，绝不黏行、绝不重打
        self._mock_run()
        ap = os.path.join(self.out, "answers.jsonl")
        with open(ap, encoding="utf-8") as f:
            content = f.read()
        with open(ap, "w", encoding="utf-8") as f:
            f.write(content.rstrip("\n"))                # 去掉末尾换行
        r = self._mock_run()
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = _rows(ap)                                 # 若黏行，这里 json.loads 直接炸
        self.assertEqual(len(rows), 30)
        keys = [(d["course"], d["model"], d["arm"], d["item_id"]) for d in rows]
        self.assertEqual(len(keys), len(set(keys)))

    # ---- meta 缺键拒绝 ----
    def test_meta_missing_fingerprint_refused(self):
        self._mock_run()
        with open(os.path.join(self.out, ".run_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "mock"}, f)               # 缺 fingerprint
        r = self._mock_run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("fingerprint", r.stderr)

    def test_meta_missing_mode_refused(self):
        self._mock_run()
        with open(os.path.join(self.out, ".run_meta.json"), encoding="utf-8") as f:
            fp = json.load(f)["fingerprint"]
        with open(os.path.join(self.out, ".run_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fp}, f)            # 缺 mode
        r = self._mock_run()
        self.assertEqual(r.returncode, 2)

    # ---- 指纹盲点 ----
    def _fp_cfg(self, arms, combined_text):
        d = tempfile.mkdtemp(prefix="b4fp_")
        self.addCleanup(shutil.rmtree, d, True)
        items = os.path.join(d, "i.jsonl")
        with open(items, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "x", "question": "q", "gold_answer": "a",
                                "answer_type": "factual", "answerable": True}) + "\n")
        comb = os.path.join(d, "c.txt")
        with open(comb, "w", encoding="utf-8") as f:
            f.write(combined_text)
        return {"courses": [{"name": "a", "items": items, "combined": comb}],
                "models": ["opus"], "arms": arms, "primary_course": "a", "secondary_course": None}

    def test_fingerprint_ignores_unused_material(self):
        # 没选 material 臂时，改 combined 不该变指纹（判分不读它）——否则合法续跑被误拒
        cfg = self._fp_cfg(["closedbook"], "v1")
        fp1 = RM._config_fingerprint(cfg)
        with open(cfg["courses"][0]["combined"], "w", encoding="utf-8") as f:
            f.write("v2 changed")
        self.assertEqual(RM._config_fingerprint(cfg), fp1)
        # 选了 material 臂 → 改 combined 必须变指纹
        cfg2 = self._fp_cfg(["closedbook", "material"], "v1")
        fp2 = RM._config_fingerprint(cfg2)
        with open(cfg2["courses"][0]["combined"], "w", encoding="utf-8") as f:
            f.write("v2 changed")
        self.assertNotEqual(RM._config_fingerprint(cfg2), fp2)

    def test_fingerprint_judge_default_explicit_equivalent(self):
        # 缺省裁判 = 显式 haiku（判分实际用的解析值）——补写默认值不该被当"换裁判"拒续跑
        cfg = self._fp_cfg(["closedbook"], "v1")
        fp_default = RM._config_fingerprint(cfg)
        cfg["judge_model"] = "haiku"
        self.assertEqual(RM._config_fingerprint(cfg), fp_default)
        cfg["judge_model"] = "sonnet"
        self.assertNotEqual(RM._config_fingerprint(cfg), fp_default)

    # ---- 聚合子进程的警告透传 ----
    def test_ragged_warning_forwarded_through_runner(self):
        # aggregate 的「答题集不齐平」警告写在子进程 stderr——runner capture 后必须转发，不能吞。
        # 制造不齐平：给某一格追加一条**不在题集里的**已答+已判行（不进任务表 → resume 不会补平）。
        self._mock_run()
        extra = {"course": "minios", "model": "opus", "arm": "skill", "item_id": "extra_x"}
        with open(os.path.join(self.out, "answers.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(extra, answerable=True, status="ok",
                                    answer="a", cost_usd=0.0)) + "\n")
        with open(os.path.join(self.out, "scores.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(extra, answerable=True, correct=True, hallucinated=0,
                                    abstained=0, judge_error=0)) + "\n")
        r = self._mock_run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("不齐平", r.stderr)                # 警告透传到 runner 的 stderr
        with open(os.path.join(self.out, "summary.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["ragged_matrix"])

    # ---- 聚合失败也删陈旧 summary ----
    def test_aggregate_failure_drops_stale_summary(self):
        self._mock_run()
        self.assertTrue(os.path.isfile(os.path.join(self.out, "summary.json")))
        sp = os.path.join(self.out, "scores.jsonl")
        with open(sp, encoding="utf-8") as f:
            first = f.readline()
        with open(sp, "a", encoding="utf-8") as f:
            f.write(first)                               # 追加重复 key 行 → aggregate _die
        r = self._mock_run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("aggregate_matrix 失败", r.stderr)
        # 旧 summary 必须被删——否则 report_matrix 会读一份对不上的陈旧摘要
        self.assertFalse(os.path.isfile(os.path.join(self.out, "summary.json")))


if __name__ == "__main__":
    unittest.main()
