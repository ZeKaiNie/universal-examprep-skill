# -*- coding: utf-8 -*-
"""A7 score_difficulty.py 回归：确定性多信号难度评分 + 原子回写 quiz_bank。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "score_difficulty.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import score_difficulty as sd  # noqa: E402


def _item(**kw):
    base = {"id": "x", "type": "choice", "question": "问？", "answer": "A"}
    base.update(kw)
    return base


class ScoreItemUnit(unittest.TestCase):
    def test_bare_choice_is_easiest(self):
        d, r = sd.score_item(_item(id="a", chapter=1), None)
        self.assertEqual(d, 1)
        self.assertIn("无高难信号", r)

    def test_kp_count_signal(self):
        d1, _ = sd.score_item(_item(knowledge_points=["a"]), None)
        d2, r2 = sd.score_item(_item(knowledge_points=["a", "b"]), None)
        d3, r3 = sd.score_item(_item(knowledge_points=["a", "b", "c"]), None)
        self.assertLessEqual(d1, d2)
        self.assertLessEqual(d2, d3)
        self.assertIn("跨2知识点", r2)
        self.assertIn("跨3知识点", r3)

    def test_missing_kp_contributes_zero_no_crash(self):
        # knowledge_points 缺字段/非 list 都算 0，不抛
        self.assertEqual(sd._kp_count(_item()), 0)
        self.assertEqual(sd._kp_count(_item(knowledge_points="notalist")), 0)
        self.assertEqual(sd._kp_count(_item(knowledge_points=["  ", ""])), 0)

    def test_structural_families(self):
        self.assertEqual(sd._struct_families("计算积分 ∫ f dx"), ["积分"])
        self.assertEqual(set(sd._struct_families("分段函数的求和 Σ")), {"分段", "求和"})
        self.assertIn("证明", sd._struct_families("prove that x>0"))
        self.assertIn("条件化", sd._struct_families("conditional probability of A"))
        self.assertEqual(sd._struct_families("简单的选择题"), [])

    def test_structural_contribution_capped_at_two(self):
        # 触发 5 个结构族也只 +2（避免把题过度抬难）
        text = "分段函数 积分∫ 求和Σ 证明 换元"
        fams = sd._struct_families(text)
        self.assertGreaterEqual(len(fams), 4)
        d, _ = sd.score_item(_item(question=text, id="s"), None)
        # 仅结构（+2）→ points 2 → d3；确认没有因 5 族飙到 5
        self.assertEqual(d, 3)

    def test_requires_assets_signal(self):
        d0, _ = sd.score_item(_item(), None)
        d1, r1 = sd.score_item(_item(requires_assets=True), None)
        self.assertGreater(d1, d0)
        self.assertIn("需读图", r1)
        # maybe_requires_assets 不计（只有确定需读图才 +1）
        d2, _ = sd.score_item(_item(maybe_requires_assets=True), None)
        self.assertEqual(d2, d0)

    def test_multipage_reads_answer_pages_not_prompt_pages(self):
        # 多页解答信号读 answer_source_pages（解答页），不是 source_pages（题面页）
        self.assertTrue(sd._multipage(_item(answer_source_pages="3-5")))
        self.assertTrue(sd._multipage(_item(answer_source_pages=[3, 4])))
        self.assertTrue(sd._multipage(_item(answer_source_pages="3,4")))
        self.assertTrue(sd._multipage(_item(answer_source_pages={"start": 2, "end": 4})))
        self.assertFalse(sd._multipage(_item(answer_source_pages="3")))
        self.assertFalse(sd._multipage(_item(answer_source_pages=[7])))
        self.assertFalse(sd._multipage(_item()))
        # 两页题面（source_pages）不是难度信号，绝不误判为多页解答
        self.assertFalse(sd._multipage(_item(source_pages="2-3")))
        self.assertFalse(sd._multipage(_item(source_pages=[2, 3])))
        # 一页题面 + 多页解答 → 命中
        self.assertTrue(sd._multipage(_item(source_pages=[1], answer_source_pages=[2, 3])))

    def test_late_chapter_cutoff_needs_three_numeric_chapters(self):
        self.assertIsNone(sd._late_chapter_cutoff([_item(chapter=1), _item(chapter=2)]))
        bank = [_item(chapter=c) for c in (1, 2, 3, 4, 5, 6)]
        cut = sd._late_chapter_cutoff(bank)
        self.assertIsNotNone(cut)
        # ch6 应算靠后，ch1 不算
        self.assertTrue(cut <= 6 and cut > 1)

    def test_open_type_signal(self):
        d_choice, _ = sd.score_item(_item(type="choice"), None)
        d_subj, r = sd.score_item(_item(type="subjective"), None)
        self.assertGreaterEqual(d_subj, d_choice)
        self.assertIn("开放题型", r)

    def test_difficulty_clamped_1_5(self):
        for q in (_item(), _item(knowledge_points=list("abcdef"), requires_assets=True,
                                 source_pages="1-9", type="subjective",
                                 question="分段 积分∫ 求和Σ 证明 换元 矩阵 递归 极限", chapter=99)):
            d, _ = sd.score_item(q, 1)
            self.assertIn(d, (1, 2, 3, 4, 5))

    def test_deterministic(self):
        q = _item(knowledge_points=["a", "b"], question="积分∫", requires_assets=True)
        self.assertEqual(sd.score_item(q, 3), sd.score_item(dict(q), 3))


class ScoreCliIO(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="a7score_")
        os.makedirs(os.path.join(self.ws, "references"))
        self.bank = [
            _item(id="easy", chapter=1),
            _item(id="hard", chapter=5, type="subjective",
                  question="计算分段函数积分 ∫", knowledge_points=["a", "b", "c"],
                  requires_assets=True, answer_source_pages="2-4"),
        ]
        self.path = os.path.join(self.ws, "references", "quiz_bank.json")
        self._write(self.bank)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, obj):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "--workspace", self.ws, *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_writes_difficulty_and_reason(self):
        r = self._run("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        bank = self._read()
        by = {q["id"]: q for q in bank}
        self.assertEqual(by["easy"]["difficulty"], 1)
        self.assertEqual(by["hard"]["difficulty"], 5)
        self.assertIn("difficulty_reason", by["hard"])
        self.assertTrue(by["hard"]["difficulty_reason"].startswith("启发式下界"))

    def test_dry_run_does_not_write(self):
        before = self._read()
        r = self._run("--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._read(), before)
        self.assertIn('"written": false', r.stdout)

    def test_idempotent_second_run_no_change(self):
        self._run()
        r2 = self._run("--json")
        self.assertIn('"changed": 0', r2.stdout)

    def test_preserves_other_fields(self):
        self._run()
        by = {q["id"]: q for q in self._read()}
        self.assertEqual(by["hard"]["question"], "计算分段函数积分 ∫")
        self.assertEqual(by["hard"]["knowledge_points"], ["a", "b", "c"])

    def test_bad_json_exits_2(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(self._run().returncode, 2)

    def test_non_list_bank_exits_2(self):
        self._write({"quiz_bank": []})
        self.assertEqual(self._run().returncode, 2)

    def test_leftover_tmp_directory_exits_1_not_traceback(self):
        # quiz_bank.json.tmp 是遗留目录 → 文档承诺的 exit 1，而非原生 traceback；原文件不动（finding P3）
        os.mkdir(self.path + ".tmp")
        self.addCleanup(shutil.rmtree, self.path + ".tmp", True)
        before = self._read()
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self._read(), before)          # 原题库未被破坏

    def test_rejects_symlink_escaped_references(self):
        # references/ 是指向工作区外目录的符号链接 → 写前 realpath 归属校验拒绝，外部文件不被改（finding D）
        outside = tempfile.mkdtemp(prefix="a7out_")
        self.addCleanup(shutil.rmtree, outside, True)
        outbank = os.path.join(outside, "quiz_bank.json")
        with open(outbank, "w", encoding="utf-8") as f:
            json.dump([_item(id="ext", chapter=1)], f, ensure_ascii=False)
        ws2 = tempfile.mkdtemp(prefix="a7ws2_")
        self.addCleanup(shutil.rmtree, ws2, True)
        try:
            os.symlink(outside, os.path.join(ws2, "references"), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("平台不支持 symlink")
        before = open(outbank, encoding="utf-8").read()
        r = subprocess.run([sys.executable, SCRIPT, "--workspace", ws2],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertIn("逃出工作区", r.stderr)
        self.assertEqual(open(outbank, encoding="utf-8").read(), before)   # 外部文件未被改

    def test_rejects_symlink_tmp(self):
        # 预建同名 .tmp 符号链接 → 拒绝写、不改原文件
        tmp = self.path + ".tmp"
        target = os.path.join(self.ws, "outside.json")
        open(target, "w").close()
        try:
            os.symlink(target, tmp)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("平台不支持 symlink")
        before = self._read()
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._read(), before)


if __name__ == "__main__":
    unittest.main()
