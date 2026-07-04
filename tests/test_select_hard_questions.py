# -*- coding: utf-8 -*-
"""A7 select_hard_questions.py 回归：难度 × 掌握状态 × A6 模式的确定性出题排序。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "select_hard_questions.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import select_hard_questions as shq  # noqa: E402


def _q(qid, chapter=1, difficulty=3, **kw):
    d = {"id": qid, "chapter": chapter, "type": "subjective",
         "question": "q", "answer": "a", "difficulty": difficulty}
    d.update(kw)
    return d


class ClassifyUnit(unittest.TestCase):
    def test_mistake_id_is_weak(self):
        idx = shq.build_mastery({"mistake_archive": [{"id": "q1", "chapter": 9}]})
        self.assertEqual(shq.classify(_q("q1"), idx)[0], "weak")

    def test_trouble_chapter_is_weak(self):
        idx = shq.build_mastery({"mistake_archive": [{"id": None, "chapter": 2}]})
        self.assertEqual(shq.classify(_q("qx", chapter=2), idx)[0], "weak")
        idx2 = shq.build_mastery({"confusion_log": [{"id": None, "chapter": 3}]})
        self.assertEqual(shq.classify(_q("qy", chapter=3), idx2)[0], "weak")

    def test_window_out_is_weak_by_point_not_whole_chapter(self):
        # 窗口外只让**覆盖该点**的题薄弱；同章其它无关点的题不被牵连（点级，非章级）
        idx = shq.build_mastery({"knowledge_window": [
            {"point": "积分", "chapter": 5, "status": "窗口外"}]})
        self.assertEqual(shq.classify(_q("b", chapter=99, knowledge_points=["定积分"]), idx)[0], "weak")   # 点子串命中
        self.assertEqual(shq.classify(_q("a", chapter=5), idx)[0], "neutral")     # 同章但无该点 → 不牵连
        self.assertEqual(shq.classify(_q("c", chapter=5, knowledge_points=["矩阵"]), idx)[0], "neutral")  # 同章不同点

    def test_in_window_is_mastered_by_point(self):
        idx = shq.build_mastery({"knowledge_window": [
            {"point": "极限", "chapter": 4, "status": "在窗口"},
            {"point": "连续", "chapter": 6, "status": "已实测"}]})
        self.assertEqual(shq.classify(_q("a", chapter=4, knowledge_points=["极限"]), idx)[0], "mastered")
        self.assertEqual(shq.classify(_q("b", chapter=6, knowledge_points=["连续性"]), idx)[0], "mastered")
        self.assertEqual(shq.classify(_q("c", chapter=4), idx)[0], "neutral")    # 同章无该点 → 不算已掌握

    def test_weak_beats_mastered_when_both(self):
        # 同点既在窗口又有错题 → weak 优先（有错题就还没掌握）
        idx = shq.build_mastery({"mistake_archive": [{"id": None, "chapter": 4}],
                                 "knowledge_window": [{"point": "p", "chapter": 4, "status": "在窗口"}]})
        self.assertEqual(shq.classify(_q("a", chapter=4, knowledge_points=["p"]), idx)[0], "weak")

    def test_weak_matches_via_phase_not_only_chapter(self):
        # 只带 phase 的题：trouble/窗口按 chapter-OR-phase 命中（与 A2 一致）
        idx = shq.build_mastery({"mistake_archive": [{"id": None, "chapter": 2}]})
        q = {"id": "p", "phase": 2, "type": "subjective", "question": "q", "answer": "a", "difficulty": 3}
        self.assertEqual(shq.classify(q, idx)[0], "weak")

    def test_neutral_default(self):
        idx = shq.build_mastery({})
        self.assertEqual(shq.classify(_q("a", chapter=1), idx)[0], "neutral")

    def test_none_state_all_neutral(self):
        idx = shq.build_mastery(None)
        self.assertEqual(shq.classify(_q("a"), idx)[0], "neutral")

    def test_resolved_mistake_not_weak(self):
        # 已订正/已复盘/已解决 的错题不再算薄弱；待复盘 仍算
        for done in ("已订正", "已复盘", "已解决"):
            idx = shq.build_mastery({"mistake_archive": [{"id": "q1", "chapter": 2, "status": done}]})
            self.assertNotIn("q1", idx["mistake_ids"], done)
            self.assertNotIn("2", idx["trouble_ch"], done)
        idx2 = shq.build_mastery({"mistake_archive": [{"id": "q2", "chapter": 3, "status": "待复盘"}]})
        self.assertIn("q2", idx2["mistake_ids"])
        self.assertIn("3", idx2["trouble_ch"])

    def test_resolved_confusion_not_weak(self):
        # 已回顾 与 已解决 的疑难都不再算薄弱；待回顾 仍算
        for done in ("已回顾", "已解决"):
            idx = shq.build_mastery({"confusion_log": [{"chapter": 5, "status": done}]})
            self.assertNotIn("5", idx["trouble_ch"], done)
        idx2 = shq.build_mastery({"confusion_log": [{"chapter": 6, "status": "待回顾"}]})
        self.assertIn("6", idx2["trouble_ch"])


class OrderUnit(unittest.TestCase):
    def _mk(self, cls, diff, i):
        return {"id": "i%d" % i, "difficulty": diff, "cls": cls, "trigger": "t",
                "chapter": "1", "orig_idx": i}

    def test_review_mode_weak_asc_then_neutral_mastered_desc(self):
        items = [self._mk("weak", 4, 0), self._mk("weak", 1, 1),
                 self._mk("neutral", 2, 2), self._mk("neutral", 5, 3),
                 self._mk("mastered", 3, 4), self._mk("mastered", 5, 5)]
        out = [it["id"] for it in shq.order_items(items, "查缺补漏")]
        # weak 先易后难：i1(d1),i0(d4)；neutral 先难：i3(d5),i2(d2)；mastered 先难：i5(d5),i4(d3)
        self.assertEqual(out, ["i1", "i0", "i3", "i2", "i5", "i4"])

    def test_beginner_mode_global_ascending(self):
        items = [self._mk("weak", 4, 0), self._mk("neutral", 2, 1),
                 self._mk("mastered", 5, 2), self._mk("mastered", 1, 3)]
        out = [it["id"] for it in shq.order_items(items, "零基础从头讲")]
        # 零基础=难度优先（全局先易后难），掌握类别仅同难度内 tiebreak：
        # d1(i3) → d2(i1) → d4(i0) → d5(i2)。绝不把 weak 难题(i0 d4)排到简单题前。
        self.assertEqual(out, ["i3", "i1", "i0", "i2"])

    def test_beginner_never_serves_hard_weak_before_easy(self):
        # 回归 finding：weak d5 绝不排在 neutral d1 之前（零基础模式）
        items = [self._mk("weak", 5, 0), self._mk("neutral", 1, 1)]
        out = [it["id"] for it in shq.order_items(items, "零基础从头讲")]
        self.assertEqual(out, ["i1", "i0"])
        # 对照：查缺补漏模式下 weak 仍先行（巩固优先）
        out2 = [it["id"] for it in shq.order_items(items, "查缺补漏")]
        self.assertEqual(out2, ["i0", "i1"])


class CliIO(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="a7sel_")
        os.makedirs(os.path.join(self.ws, "references"))
        self.bank = [
            _q("easy", chapter=1, difficulty=1),
            _q("mid", chapter=3, difficulty=3),
            _q("hard", chapter=5, difficulty=5),
        ]
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _state(self, st):
        with open(os.path.join(self.ws, "study_state.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)

    def _run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "--workspace", self.ws, "--json", *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_json_shape_and_state_flag(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["mode"], "查缺补漏")
        self.assertFalse(obj["state_loaded"])
        self.assertEqual({it["id"] for it in obj["items"]}, {"easy", "mid", "hard"})

    def test_reads_mode_from_state(self):
        self._state({"mode": "零基础从头讲"})
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["mode"], "零基础从头讲")

    def test_legacy_panic_mode_migrates_to_beginner(self):
        # 旧 panic → 零基础从头讲（与 update_progress 迁移同口径，不再误当查缺补漏）
        self._state({"mode": "panic"})
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["mode"], "零基础从头讲")

    def test_legacy_sprint_mode_migrates_to_review(self):
        self._state({"mode": "sprint"})            # 旧 sprint → 查缺补漏
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["mode"], "查缺补漏")

    def test_truly_unknown_mode_falls_back(self):
        self._state({"mode": "乱写的模式"})        # 非标准串 → 回落默认，不炸
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["mode"], "查缺补漏")

    def test_cli_mode_overrides_state(self):
        self._state({"mode": "查缺补漏"})
        obj = json.loads(self._run("--mode", "零基础从头讲").stdout)
        self.assertEqual(obj["mode"], "零基础从头讲")

    def test_mistake_makes_weak_first(self):
        self._state({"mode": "查缺补漏", "mistake_archive": [{"id": "hard", "chapter": 5}]})
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["items"][0]["id"], "hard")     # 错题→weak→排最前
        self.assertEqual(obj["items"][0]["class"], "weak")

    def test_chapter_exact_filter(self):
        obj = json.loads(self._run("--chapter", "3").stdout)
        self.assertEqual([it["id"] for it in obj["items"]], ["mid"])

    def test_chapter_filter_matches_phase(self):
        # 追加一个只带 phase 的题，--chapter 应按 chapter-OR-phase 命中它
        self.bank.append({"id": "ph2", "phase": 7, "type": "subjective",
                          "question": "q", "answer": "a", "difficulty": 2})
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        obj = json.loads(self._run("--chapter", "7").stdout)
        self.assertEqual([it["id"] for it in obj["items"]], ["ph2"])

    def test_from_chapter_matches_phase_tag(self):
        # 双标 {chapter:1, phase:3} 的题在 --from-chapter 3 时应保留（chapter 与 phase 都算）
        self.bank.append({"id": "dual", "chapter": 1, "phase": 3, "type": "subjective",
                          "question": "q", "answer": "a", "difficulty": 2})
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        obj = json.loads(self._run("--from-chapter", "3").stdout)
        self.assertIn("dual", [it["id"] for it in obj["items"]])       # phase=3 命中，不因 chapter=1 被剔

    def test_from_chapter_numeric(self):
        obj = json.loads(self._run("--from-chapter", "3").stdout)
        self.assertEqual({it["id"] for it in obj["items"]}, {"mid", "hard"})

    def test_num_limit(self):
        obj = json.loads(self._run("-n", "1").stdout)
        self.assertEqual(obj["count"], 1)

    # ---- finding 1: 存档 scope 必须被应用（A2 契约，未标签项排除）----
    def test_stored_scope_excludes_out_of_scope_and_untagged(self):
        # homework-only 存档范围：只应返回 source_type=homework，exam/untagged 排除
        self.bank = [
            {"id": "hw1", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 2, "source_type": "homework"},
            {"id": "ex1", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 4, "source_type": "exam"},
            {"id": "untagged", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 3},
        ]
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        self._state({"mode": "查缺补漏", "scope": "homework-only"})
        obj = json.loads(self._run().stdout)
        self.assertEqual([it["id"] for it in obj["items"]], ["hw1"])
        self.assertEqual(obj["source_types"], ["homework"])

    def test_cli_source_type_overrides_scope(self):
        self.bank[0]["source_type"] = "homework"
        self.bank[1]["source_type"] = "exam"
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        self._state({"mode": "查缺补漏", "scope": "homework-only"})
        obj = json.loads(self._run("--source-type", "exam").stdout)
        self.assertEqual([it["id"] for it in obj["items"]], ["mid"])   # mid 是 exam

    def test_source_type_all_overrides_scope_to_mixed(self):
        # 存档 homework-only + --source-type all → 一次性覆盖为混合池（返回所有题，含 exam/untagged）
        self.bank[0]["source_type"] = "homework"      # easy
        self.bank[1]["source_type"] = "exam"          # mid（hard 无 source_type=untagged）
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        self._state({"mode": "查缺补漏", "scope": "homework-only"})
        obj = json.loads(self._run("--source-type", "all").stdout)
        self.assertIsNone(obj["source_types"])                       # 混合池
        self.assertEqual({it["id"] for it in obj["items"]}, {"easy", "mid", "hard"})
        self.assertTrue(any("覆盖存档范围为混合池" in n for n in obj["notes"]))

    def test_unmappable_scope_fails_loud(self):
        self._state({"mode": "查缺补漏", "scope": "某个自定义范围"})
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("无法自动映射", r.stderr)

    def test_mixed_scope_no_filter(self):
        self._state({"mode": "查缺补漏", "scope": "混合题池"})
        obj = json.loads(self._run().stdout)
        self.assertIsNone(obj["source_types"])
        self.assertEqual(obj["count"], 3)

    def test_bad_source_type_value_exits_2(self):
        self.assertEqual(self._run("--source-type", "nonsense").returncode, 2)

    def test_empty_source_type_exits_2(self):
        # '' 或 ',' 的显式空过滤是用法错误，绝不静默退混合池（finding C）
        self.assertEqual(self._run("--source-type", "").returncode, 2)
        self.assertEqual(self._run("--source-type", ",").returncode, 2)

    def test_untagged_exclusion_reported(self):
        # 范围过滤下未标签题被排除必须计数上报（finding B / A2 契约）
        self.bank = [
            {"id": "hw", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 2, "source_type": "homework"},
            {"id": "untag", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 3},
        ]
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        self._state({"mode": "查缺补漏", "scope": "homework-only"})
        obj = json.loads(self._run().stdout)
        self.assertEqual(obj["untagged_excluded"], 1)
        self.assertEqual([it["id"] for it in obj["items"]], ["hw"])
        self.assertTrue(any("未标签" in n for n in obj["notes"]))

    def test_symlinked_state_fails_loud(self):
        # study_state.json 为符号链接 → fail loud（finding E）
        ext = os.path.join(self.ws, "external_state.json")
        with open(ext, "w", encoding="utf-8") as f:
            json.dump({"mode": "查缺补漏"}, f, ensure_ascii=False)
        link = os.path.join(self.ws, "study_state.json")
        try:
            os.symlink(ext, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("平台不支持 symlink")
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("符号链接", r.stderr)

    def test_md_only_scope_fallback(self):
        # 无 study_state.json 但 study_progress.md 记了 homework-only → 仍按范围过滤（不静默放宽）
        self.bank = [
            {"id": "hw", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 2, "source_type": "homework"},
            {"id": "ex", "chapter": 1, "type": "subjective", "question": "q", "answer": "a",
             "difficulty": 4, "source_type": "exam"},
        ]
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        with open(os.path.join(self.ws, "study_progress.md"), "w", encoding="utf-8") as f:
            f.write("# 学习进度\n\n* **范围/模式**：homework-only ｜ 查缺补漏 ｜ 时间预算 未设定\n")
        obj = json.loads(self._run().stdout)
        self.assertTrue(obj["state_loaded"])                 # md 回落也算已读 state
        self.assertEqual(obj["source_types"], ["homework"])   # 范围行被尊重
        self.assertEqual([it["id"] for it in obj["items"]], ["hw"])

    # ---- 某章起步补弱：必须显式 --from-chapter，绝不从 current_phase 猜（阶段号≠章号）----
    def test_weak_start_mode_requires_explicit_scope(self):
        # 既无 --chapter 也无 --from-chapter，即便 state 带 current_phase 也 fail-loud——不拿阶段号当章号猜
        self._state({"mode": "某章起步补弱", "current_phase": 3})
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("章范围", r.stderr)
        self.assertIn("阶段号未必等于章号", r.stderr)

    def test_weak_start_mode_cli_from_chapter_works(self):
        self._state({"mode": "某章起步补弱", "current_phase": 1})
        obj = json.loads(self._run("--from-chapter", "5").stdout)
        self.assertEqual(obj["from_chapter"], 5)
        self.assertEqual([it["id"] for it in obj["items"]], ["hard"])

    def test_weak_start_mode_chapter_alone_satisfies(self):
        # 显式 --chapter 即算显式章范围（不再报 usage error）——检查点抽题路径可用
        self._state({"mode": "某章起步补弱", "current_phase": 1})
        obj = json.loads(self._run("--chapter", "3").stdout)
        self.assertEqual([it["id"] for it in obj["items"]], ["mid"])   # 只出第 3 章

    def test_on_the_fly_difficulty_when_unscored(self):
        # 题库无 difficulty 字段 → 即时补算，不落盘
        for q in self.bank:
            q.pop("difficulty", None)
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump(self.bank, f, ensure_ascii=False, indent=2)
        obj = json.loads(self._run().stdout)
        self.assertTrue(all(1 <= it["difficulty"] <= 5 for it in obj["items"]))
        # 未落盘：原文件仍无 difficulty
        with open(os.path.join(self.ws, "references", "quiz_bank.json"), encoding="utf-8") as f:
            self.assertFalse(any("difficulty" in q for q in json.load(f)))


if __name__ == "__main__":
    unittest.main()
