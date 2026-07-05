# -*- coding: utf-8 -*-
"""B5 calibrate_matrix.py 回归：从 run_matrix 输出抽分层校准样本 + Cohen's kappa。"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BENCH)
import calibrate_matrix as CM  # noqa: E402

FIX = os.path.join(BENCH, "fixtures", "mini_course_matrix", "config.json")


def _cm(*args):
    return subprocess.run([sys.executable, os.path.join(BENCH, "calibrate_matrix.py"), *args],
                          capture_output=True, text=True, encoding="utf-8")


def _run_matrix(out):
    subprocess.run([sys.executable, os.path.join(BENCH, "run_matrix.py"), "--mock",
                    "--config", FIX, "--results-dir", out],
                   capture_output=True, text=True, encoding="utf-8")


class SampleFromMatrix(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="b5cal_")
        self.addCleanup(shutil.rmtree, self.out, True)
        _run_matrix(self.out)

    def _sheet(self):
        return os.path.join(self.out, "calibration", "calibration_sheet.csv")

    def _rows(self):
        with open(self._sheet(), encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_sample_writes_hidden_sheet(self):
        r = _cm("sample", "--results-dir", self.out, "--config", FIX, "--n", "6")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self._rows()
        self.assertEqual(len(rows), 6)
        # 裁判判定不出现在待填表（human_correct 全空）——盲填
        self.assertTrue(all((row["human_correct"] or "").strip() == "" for row in rows))
        self.assertLessEqual({"ref_id", "question", "gold_answer", "reference_span", "model_answer"},
                             set(rows[0].keys()))
        # 隐藏 key 有对应判定
        keyp = os.path.join(self.out, "calibration", ".calibration_key.jsonl")
        with open(keyp, encoding="utf-8") as kf:
            keys = [json.loads(l) for l in kf if l.strip()]
        self.assertEqual(len(keys), 6)
        self.assertTrue(all("judge_correct" in k for k in keys))

    def test_sample_deterministic_by_seed(self):
        _cm("sample", "--results-dir", self.out, "--config", FIX, "--n", "6", "--seed", "3")
        a = [r["ref_id"] + r["question"] for r in self._rows()]
        _cm("sample", "--results-dir", self.out, "--config", FIX, "--n", "6", "--seed", "3")
        b = [r["ref_id"] + r["question"] for r in self._rows()]
        self.assertEqual(a, b)

    def test_no_answers_fails_loud(self):
        empty = tempfile.mkdtemp(prefix="b5empty_")
        self.addCleanup(shutil.rmtree, empty, True)
        r = _cm("sample", "--results-dir", empty, "--config", FIX, "--n", "6")
        self.assertEqual(r.returncode, 2)

    def test_malformed_jsonl_fails_loud(self):
        # answers.jsonl 有半行坏 JSON（中断写/手改）→ 不静默丢，sample 直接失败报行号
        with open(os.path.join(self.out, "answers.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"course": "c", "model": "opus"\n')   # 缺闭合括号
        r = _cm("sample", "--results-dir", self.out, "--config", FIX, "--n", "6")
        self.assertEqual(r.returncode, 2)
        self.assertIn("坏 JSONL", r.stderr)

    def test_config_mismatch_refused(self):
        # results_dir 的 .run_meta 记的是 fixture config；用不同 config（改了 models/arms）sample → 拒
        d = tempfile.mkdtemp(prefix="b5cm_")
        self.addCleanup(shutil.rmtree, d, True)
        src = os.path.dirname(FIX)
        cfg2 = {"courses": [{"name": "minios", "items": os.path.join(src, "items.jsonl"),
                             "combined": os.path.join(src, "materials", "_combined.txt"),
                             "skill_ws": os.path.join(src, "skill_ws"),
                             "raw_ws": os.path.join(src, "raw_ws")}],
                "models": ["opus"], "arms": ["closedbook"], "mock": True}   # 不同指纹
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(cfg2, f)
        r = _cm("sample", "--results-dir", self.out, "--config", cfgp, "--n", "3")
        self.assertEqual(r.returncode, 2)
        self.assertIn("不一致", r.stderr)


class KappaComputation(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="b5kap_")
        self.addCleanup(shutil.rmtree, self.out, True)
        self.cal = os.path.join(self.out, "calibration")
        os.makedirs(self.cal)

    def _write(self, pairs):
        # pairs: list of (human, judge)
        sheet = os.path.join(self.cal, "calibration_sheet.csv")
        keyp = os.path.join(self.cal, ".calibration_key.jsonl")
        with open(sheet, "w", encoding="utf-8-sig", newline="") as f, open(keyp, "w", encoding="utf-8") as kf:
            w = csv.DictWriter(f, fieldnames=CM._FIELDS)
            w.writeheader()
            for i, (h, j) in enumerate(pairs, 1):
                ref = "cal_%03d" % i
                w.writerow({"ref_id": ref, "course": "c", "answerable": 1, "question": "q",
                            "gold_answer": "g", "reference_span": "s", "model_answer": "a",
                            "human_correct": str(h)})
                kf.write(json.dumps({"ref_id": ref, "judge_correct": j}) + "\n")

    def test_perfect_agreement(self):
        self._write([(1, 1), (0, 0), (1, 1), (0, 0)])
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("一致率 agreement = 100.0%", r.stdout)
        self.assertIn("无分歧", r.stdout)

    def test_disagreement_listed(self):
        self._write([(1, 1), (1, 0), (0, 0), (0, 1)])   # 两条分歧
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("人机分歧 2 条", r.stdout)

    def test_all_blank_fails(self):
        self._write([("", 1), ("", 0)])
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 1)

    def test_degenerate_kappa_gated(self):
        # 裁判判定全同（都 1）+ 人全填 1 → kappa 退化，报"退化"而非"可信"
        self._write([(1, 1), (1, 1), (1, 1)])
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("退化", r.stdout)
        self.assertNotIn("可信", r.stdout)

    def test_invalid_label_fails_loud(self):
        # 填了 yes/2 这类非 0/1 → 不能当空格静默丢（会让 kappa 虚高）——fail loud 列出坏格
        self._write([(1, 1), ("yes", 0), (2, 1)])
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("不是 0/1", r.stderr)
        self.assertIn("cal_002", r.stderr)

    def test_excel_float_labels_accepted(self):
        # Excel 数字化的 1.0/0.0 是合法标注
        self._write([("1.0", 1), ("0.0", 0)])
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("一致率 agreement = 100.0%", r.stdout)

    def test_unmatched_ref_surfaced(self):
        # 填了但 ref_id 对不上 key（表被改/串）→ 不静默丢，报未匹配数 + stderr 警告
        self._write([(1, 1), (0, 0)])
        sheet = os.path.join(self.cal, "calibration_sheet.csv")
        with open(sheet, "a", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=CM._FIELDS).writerow(
                {"ref_id": "cal_999", "course": "c", "answerable": 1, "question": "q",
                 "gold_answer": "g", "reference_span": "s", "model_answer": "a", "human_correct": "1"})
        r = _cm("kappa", "--results-dir", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("未匹配 1", r.stdout)
        self.assertIn("对不上", r.stderr)


class CustomPool(unittest.TestCase):
    """手写 answers/scores/config，验 judge_error 排除、确定性排除、待填表隐藏 model/arm。"""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="b5cp_")
        self.addCleanup(shutil.rmtree, self.d, True)
        items = os.path.join(self.d, "items.jsonl")
        with open(items, "w", encoding="utf-8") as f:
            for iid, at, gold in (("f1", "factual", "g"), ("n1", "numeric", "5"), ("je1", "factual", "g")):
                f.write(json.dumps({"id": iid, "question": "q", "gold_answer": gold,
                                    "answer_type": at, "answerable": True}) + "\n")
            # 越界探针（answerable=false）——确定性弃答判定，应从校准池排除
            f.write(json.dumps({"id": "p1", "question": "q", "gold_answer": "",
                                "answer_type": "factual", "answerable": False}) + "\n")
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "c", "items": items}], "models": ["opus"],
                       "arms": ["closedbook"], "mock": True}, f)
        self.res = os.path.join(self.d, "res")
        os.makedirs(self.res)

        def base(iid):
            return {"course": "c", "model": "opus", "arm": "closedbook", "item_id": iid}
        with open(os.path.join(self.res, "answers.jsonl"), "w", encoding="utf-8") as f:
            for iid in ("f1", "n1", "je1", "p1"):
                f.write(json.dumps(dict(base(iid), answer="a")) + "\n")
        with open(os.path.join(self.res, "scores.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(dict(base("f1"), correct=True, scored_by="llm", judge_error=0)) + "\n")
            f.write(json.dumps(dict(base("n1"), correct=True, scored_by="mock", judge_error=0)) + "\n")
            f.write(json.dumps(dict(base("je1"), correct=False, scored_by="llm", judge_error=1)) + "\n")
            f.write(json.dumps(dict(base("p1"), correct=True, scored_by="llm", judge_error=0)) + "\n")

    def test_build_pool_skips_judge_error_carries_type(self):
        cfg = __import__("run_matrix").load_config(self.cfgp)
        pool = CM.build_pool(self.res, cfg)
        ids = {p["id"] for p in pool}
        self.assertEqual(ids, {"f1", "n1", "p1"})        # je1 (judge_error) 排除；p1 在池但校准时再排
        f1 = next(p for p in pool if p["id"] == "f1")
        self.assertEqual(f1["answer_type"], "factual")
        self.assertEqual(f1["scored_by"], "llm")

    def test_sample_excludes_deterministic_keeps_oos_hides_model_arm(self):
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已排除", r.stdout)                 # n1(numeric) 被排除的提示
        with open(os.path.join(self.res, "calibration", "calibration_sheet.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        # f1（llm 判、可答）+ p1（越界探针——弃答检测器的判定也要人工校准）；numeric 快路排除
        self.assertEqual(len(rows), 2)
        by_ans = {r_["answerable"]: r_ for r_ in rows}
        self.assertEqual(set(by_ans), {"0", "1"})
        self.assertIn("越界题", by_ans["0"]["question"])   # 越界行带「以是否老实弃答为准」标注
        self.assertNotIn("model", rows[0])               # 待填表不含 model/arm（不带偏标注）
        self.assertNotIn("arm", rows[0])

    def test_four_strata_composition(self):
        # 四层分层：池子四层充足时，n=24 抽成 可答判对/判错=8/8 + 越界弃答/未弃答=4/4（2:1 可答:越界）
        d = tempfile.mkdtemp(prefix="b5st_")
        self.addCleanup(shutil.rmtree, d, True)
        items = os.path.join(d, "items.jsonl")
        with open(items, "w", encoding="utf-8") as f:
            for i in range(20):
                f.write(json.dumps({"id": "a%02d" % i, "question": "q", "gold_answer": "g",
                                    "answer_type": "factual", "answerable": True}) + "\n")
            for i in range(10):
                f.write(json.dumps({"id": "o%02d" % i, "question": "q", "gold_answer": "",
                                    "answer_type": "factual", "answerable": False}) + "\n")
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"courses": [{"name": "c", "items": items}], "models": ["opus"],
                       "arms": ["closedbook"], "mock": True}, f)
        res = os.path.join(d, "res")
        os.makedirs(res)
        fa = open(os.path.join(res, "answers.jsonl"), "w", encoding="utf-8")
        fs = open(os.path.join(res, "scores.jsonl"), "w", encoding="utf-8")
        with fa, fs:
            for grp, cnt in (("a", 20), ("o", 10)):      # 可答/越界，各一半判对一半判错(=弃答/未弃答)
                for i in range(cnt):
                    base = {"course": "c", "model": "opus", "arm": "closedbook",
                            "item_id": "%s%02d" % (grp, i)}
                    fa.write(json.dumps(dict(base, answer="x")) + "\n")
                    fs.write(json.dumps(dict(base, correct=(i % 2 == 0), scored_by="llm",
                                             judge_error=0)) + "\n")
        r = _cm("sample", "--results-dir", res, "--config", cfgp, "--n", "24")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("可答判对=8", r.stdout)
        self.assertIn("可答判错=8", r.stdout)
        self.assertIn("越界弃答=4", r.stdout)
        self.assertIn("越界未弃答=4", r.stdout)
        with open(os.path.join(res, "calibration", "calibration_sheet.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 24)
        self.assertEqual(sum(1 for row in rows if row["answerable"] == "0"), 8)

    def test_out_dir_preserved_in_followup(self):
        od = os.path.join(self.d, "myout")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10", "--out-dir", od)
        self.assertIn("--out-dir %s" % od, r.stdout)     # 续跑命令带上自定义 out-dir

    def test_missing_meta_warns_loud(self):
        # 手拼目录（无 .run_meta.json）→ 放行但 stderr 响亮警告「指纹无从核对」
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("没有 .run_meta.json", r.stderr)

    def test_corrupt_meta_refused(self):
        # .run_meta.json 损坏 → 不能默认放行（假定不匹配比静默放行安全）
        with open(os.path.join(self.res, ".run_meta.json"), "w", encoding="utf-8") as f:
            f.write('{"mode": "mock", "fingerp')          # 截断的半个 JSON
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("损坏", r.stderr)

    def test_meta_without_fingerprint_refused(self):
        with open(os.path.join(self.res, ".run_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "mock"}, f)                # 缺 fingerprint 字段
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("fingerprint", r.stderr)

    def test_duplicate_answer_rows_refused(self):
        # answers.jsonl 同 key 重复行 → 人标的答案可能不是裁判判的那条——拒
        with open(os.path.join(self.res, "answers.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "opus", "arm": "closedbook",
                                "item_id": "f1", "answer": "another"}) + "\n")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("重复行", r.stderr)

    def test_duplicate_score_rows_refused(self):
        with open(os.path.join(self.res, "scores.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "opus", "arm": "closedbook",
                                "item_id": "f1", "correct": False, "scored_by": "llm",
                                "judge_error": 0}) + "\n")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("重复行", r.stderr)

    def test_score_without_answer_fails_loud(self):
        # scores 有判定但 answers 没对应答案（账本失同步）→ 拒，不静默缩样本偏 kappa
        with open(os.path.join(self.res, "scores.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"course": "c", "model": "opus", "arm": "closedbook",
                                "item_id": "ghost", "correct": True, "scored_by": "llm",
                                "judge_error": 0}) + "\n")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("失同步", r.stderr)

    def test_score_for_unknown_item_fails_loud(self):
        # 判定行的 item 不在 config 金标里（config 配错）→ 拒
        for name in ("answers.jsonl", "scores.jsonl"):
            with open(os.path.join(self.res, name), "a", encoding="utf-8") as f:
                row = {"course": "c", "model": "opus", "arm": "closedbook", "item_id": "not_in_items"}
                if name == "answers.jsonl":
                    row["answer"] = "a"
                else:
                    row.update(correct=True, scored_by="llm", judge_error=0)
                f.write(json.dumps(row) + "\n")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 2)
        self.assertIn("金标里找不到", r.stderr)

    def test_formula_injection_neutralized(self):
        # 模型答案以 = 开头（不可信文本）→ 加 ' 前缀，Excel 不再当公式执行
        ans = os.path.join(self.res, "answers.jsonl")
        with open(ans, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        for row in rows:
            if row["item_id"] == "f1":
                row["answer"] = "=HYPERLINK(evil)"
        with open(ans, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        r = _cm("sample", "--results-dir", self.res, "--config", self.cfgp, "--n", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.res, "calibration", "calibration_sheet.csv"),
                  encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        target = next(row for row in rows if "HYPERLINK" in row["model_answer"])
        self.assertTrue(target["model_answer"].startswith("'="), target["model_answer"])


class Units(unittest.TestCase):
    def test_model_family(self):
        self.assertEqual(CM._model_family("opus"), "claude")
        self.assertEqual(CM._model_family("claude-haiku-4-5"), "claude")
        self.assertEqual(CM._model_family("gemini-2.5"), "gemini")
        self.assertEqual(CM._model_family("gpt-4o"), "openai")
        self.assertEqual(CM._model_family("deepseek-chat"), "deepseek")

    def test_self_preference_falls_back_to_config(self):
        # summary.json 缺（infra 跳过的真跑会删过期 summary）→ 从 config 推裁判家族，警告照发
        out = tempfile.mkdtemp(prefix="b5spc_")
        self.addCleanup(shutil.rmtree, out, True)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            CM._warn_self_preference(out, [{"model": "opus"}], {"judge_model": "haiku"})
        self.assertIn("自我偏好", buf.getvalue())
        # mock config → 裁判是 mock 家族 → 不警告
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            CM._warn_self_preference(out, [{"model": "opus"}], {"mock": True})
        self.assertEqual(buf2.getvalue(), "")

    def test_self_preference_uses_run_meta_mode_over_cfg_mock(self):
        # config 写着 mock:true 但目录实际是 --real 跑的（.run_meta mode=real）→ 裁判按 real 推断（haiku），
        # 与 Claude 家族生成器重叠 → 警告照发，不被 cfg 的 mock 标记压掉
        out = tempfile.mkdtemp(prefix="b5spm_")
        self.addCleanup(shutil.rmtree, out, True)
        with open(os.path.join(out, ".run_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "real", "fingerprint": "x"}, f)
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            CM._warn_self_preference(out, [{"model": "opus"}], {"mock": True})
        self.assertIn("自我偏好", buf.getvalue())

    def test_load_jsonl_tolerates_bom(self):
        # 编辑器重存加了 BOM 的合法 .jsonl 不该被 fail-loud 误杀（utf-8-sig 读）
        d = tempfile.mkdtemp(prefix="b5bom_")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "answers.jsonl")
        with open(p, "w", encoding="utf-8-sig") as f:    # utf-8-sig 写 → 首行带 BOM
            f.write(json.dumps({"ref_id": "a", "judge_correct": 1}) + "\n")
            f.write(json.dumps({"ref_id": "b", "judge_correct": 0}) + "\n")
        rows = CM._load_jsonl(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ref_id"], "a")

    def test_load_jsonl_still_dies_on_real_garbage(self):
        # 真坏行仍 fail-loud（BOM 容忍不能顺带放过半行 JSON）
        d = tempfile.mkdtemp(prefix="b5bad_")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "scores.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"ref_id": "a"}\n{"broken\n')
        with self.assertRaises(SystemExit):
            CM._load_jsonl(p)

    def test_self_preference_warning(self):
        # summary.json judge_model=haiku + pool 有 opus → 同 claude 家族 → 警告
        out = tempfile.mkdtemp(prefix="b5sp_")
        self.addCleanup(shutil.rmtree, out, True)
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"judge_model": "haiku"}, f)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            CM._warn_self_preference(out, [{"model": "opus"}])
        self.assertIn("自我偏好", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
