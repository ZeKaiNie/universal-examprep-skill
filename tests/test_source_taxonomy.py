# -*- coding: utf-8 -*-
"""A2 tests — source taxonomy schema, official selector, knowledge index, scope-override contract."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
       b"\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _mk_ws(tmp, extra=None):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "references", "wiki"))
    open(os.path.join(ws, "references", "wiki", "ch1.md"), "w", encoding="utf-8").write("# ch1\n内容")
    open(os.path.join(ws, "references", "wiki", "ch2.md"), "w", encoding="utf-8").write("# ch2\n内容")
    open(os.path.join(ws, "study_plan.md"), "w", encoding="utf-8").write(
        "# 计划\n## 阶段1：栈（references/wiki/ch1.md）\n- 刷题\n## 阶段2：树\n- references/wiki/ch2.md\n")
    open(os.path.join(ws, "study_progress.md"), "w", encoding="utf-8").write(
        "当前阶段：1\n## 错题本\n（暂无）\n## 疑难点\n（暂无）\n")
    bank = [
        {"id": "hw1", "chapter": 1, "type": "subjective", "question": "作业题一？", "answer": "A",
         "source": "material", "ai_generated": False, "source_type": "homework",
         "knowledge_points": ["栈", "LIFO"], "difficulty": 2, "difficulty_reason": "单概念直推"},
        {"id": "lq1", "chapter": 1, "type": "choice", "question": "讲义 quiz？",
         "options": ["a", "b", "c", "d"], "answer": "a", "source": "material", "ai_generated": False,
         "source_type": "lecture_quiz", "knowledge_points": ["栈"], "difficulty": 4,
         "difficulty_reason": "多步推理"},
        {"id": "ex1", "phase": 2, "type": "subjective", "question": "例题？", "answer": "B",
         "source": "material", "ai_generated": False, "source_type": "example",
         "knowledge_points": ["树", "遍历"], "requires_assets": True,
         "assets": [{"path": "references/assets/p.png", "role": "figure", "type": "page_image"}]},
        {"id": "untagged1", "chapter": 1, "type": "subjective", "question": "没打标签的题？",
         "answer": "C", "source": "material", "ai_generated": False},
    ]
    if extra:
        bank += extra
    os.makedirs(os.path.join(ws, "references", "assets"))
    with open(os.path.join(ws, "references", "assets", "p.png"), "wb") as f:
        f.write(PNG)
    json.dump(bank, open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return ws


def _validate(ws):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "validate_workspace.py"), ws],
                          capture_output=True, text=True, encoding="utf-8")


class ValidatorTaxonomy(unittest.TestCase):
    def test_tagged_bank_passes(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = _validate(ws)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bad_source_type_fails(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "b1", "chapter": 1, "type": "subjective",
                                          "question": "x?", "answer": "y", "source": "material",
                                          "ai_generated": False, "source_type": "lecture"}])
        r = _validate(ws)
        self.assertEqual(r.returncode, 1)
        self.assertIn("source_type", r.stdout + r.stderr)

    def test_bad_difficulty_fails(self):
        for bad in (0, 6, "3", True):
            ws = _mk_ws(tempfile.mkdtemp(), [{"id": "b2", "chapter": 1, "type": "subjective",
                                              "question": "x?", "answer": "y", "source": "material",
                                              "ai_generated": False, "difficulty": bad}])
            r = _validate(ws)
            self.assertEqual(r.returncode, 1, "difficulty=%r 应报错" % (bad,))

    def test_bad_knowledge_points_fail(self):
        for bad in ([], ["", "x"], "栈", [1]):
            ws = _mk_ws(tempfile.mkdtemp(), [{"id": "b3", "chapter": 1, "type": "subjective",
                                              "question": "x?", "answer": "y", "source": "material",
                                              "ai_generated": False, "knowledge_points": bad}])
            r = _validate(ws)
            self.assertEqual(r.returncode, 1, "knowledge_points=%r 应报错" % (bad,))


class Selector(unittest.TestCase):
    def _run(self, ws, args):
        import importlib
        m = importlib.import_module("select_questions")
        from io import StringIO
        old = sys.stdout
        sys.stdout = buf = StringIO()
        try:
            rc = m.run(["--workspace", ws] + args)
        finally:
            sys.stdout = old
        return rc, buf.getvalue()

    def test_filter_by_source_type_excludes_untagged_loudly(self):
        ws = _mk_ws(tempfile.mkdtemp())
        rc, out = self._run(ws, ["--source-type", "homework", "--json"])
        data = json.loads(out)
        self.assertEqual([i["id"] for i in data["items"]], ["hw1"])
        self.assertEqual(data["untagged_excluded"], 1)            # 未标签题不静默混入范围

    def test_filter_dimensions_compose(self):
        ws = _mk_ws(tempfile.mkdtemp())
        rc, out = self._run(ws, ["--chapter", "1", "--difficulty-min", "3", "--json"])
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["lq1"])
        rc, out = self._run(ws, ["--knowledge-point", "遍历", "--json"])
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["ex1"])
        rc, out = self._run(ws, ["--requires-assets", "yes", "--json"])
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["ex1"])
        rc, out = self._run(ws, ["--chapter", "2", "--json"])     # phase 回退
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["ex1"])

    def test_selector_excludes_legacy_non_gradable_items(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{
            "id": "worked-only", "chapter": 1, "type": "subjective",
            "question": "Completed demonstration", "gradable": False,
            "source_type": "example",
        }])
        rc, out = self._run(ws, ["--json"])
        self.assertEqual(rc, 0)
        self.assertNotIn("worked-only", [i["id"] for i in json.loads(out)["items"]])

    def test_bad_source_type_filter_exits_2(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "select_questions.py"),
                            "--workspace", ws, "--source-type", "lecture"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)

    def test_sqlite_export_optional_generated(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "mx1", "chapter": 1, "type": "subjective",
                                          "question": "mixed 来源？", "answer": "Z", "source": "mixed",
                                          "ai_generated": False}])
        db = os.path.join(tempfile.mkdtemp(), "cache.db")
        rc, out = self._run(ws, ["--export-sqlite", db, "--json"])
        self.assertTrue(os.path.isfile(db))
        import sqlite3
        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        kp = con.execute("SELECT COUNT(*) FROM knowledge_points WHERE knowledge_point='栈'").fetchone()[0]
        mixed = con.execute("SELECT has_official_answer FROM questions WHERE id='mx1'").fetchone()[0]
        hw = con.execute("SELECT has_official_answer FROM questions WHERE id='hw1'").fetchone()[0]
        con.close()
        self.assertEqual(n, 5)
        self.assertEqual(kp, 2)
        self.assertEqual(mixed, 0)     # mixed/unknown 来源答案 ≠ 官方答案（与视觉索引同口径）
        self.assertEqual(hw, 1)


    # ---- regression guards for Codex round-1 (3 findings) ----

    def test_json_with_sqlite_export_stays_parseable(self):
        ws = _mk_ws(tempfile.mkdtemp())
        db = os.path.join(tempfile.mkdtemp(), "c.db")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "select_questions.py"),
                            "--workspace", ws, "--export-sqlite", db, "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)                                      # stdout 纯 JSON，状态行在 stderr
        self.assertIn("sqlite", r.stderr)

    def test_untagged_count_respects_other_filters(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "untagged_ch2", "phase": 2, "type": "subjective",
                                          "question": "第二章未标签？", "answer": "D",
                                          "source": "material", "ai_generated": False}])
        rc, out = self._run(ws, ["--source-type", "homework", "--chapter", "2", "--json"])
        data = json.loads(out)
        self.assertEqual(data["untagged_excluded"], 1)            # 只数 ch2 的未标签题（untagged1 在 ch1）
        rc, out = self._run(ws, ["--source-type", "homework", "--chapter", "1", "--json"])
        self.assertEqual(json.loads(out)["untagged_excluded"], 1) # ch1 的 untagged1

    # ---- regression guards for Codex round-3 ----

    def test_chapter_or_phase_matching(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "dual1", "chapter": 3, "phase": 1, "type": "subjective",
                                          "question": "双标题？", "answer": "E", "source": "material",
                                          "ai_generated": False, "source_type": "homework"}])
        rc, out = self._run(ws, ["--chapter", "3", "--json"])
        self.assertIn("dual1", [i["id"] for i in json.loads(out)["items"]])   # 原章号可命中
        rc, out = self._run(ws, ["--chapter", "1", "--json"])
        self.assertIn("dual1", [i["id"] for i in json.loads(out)["items"]])   # 复习阶段也可命中

    def test_sqlite_keeps_phase_and_dedupes_kp(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "dual3", "chapter": 3, "phase": 1, "type": "subjective",
                                          "question": "双标？", "answer": "E", "source": "material",
                                          "ai_generated": False, "knowledge_points": ["重", "重", "另"]}])
        db = os.path.join(tempfile.mkdtemp(), "c.db")
        self._run(ws, ["--export-sqlite", db, "--json"])
        import sqlite3
        con = sqlite3.connect(db)
        ch, ph = con.execute("SELECT chapter, phase FROM questions WHERE id='dual3'").fetchone()
        kp = con.execute("SELECT COUNT(*) FROM knowledge_points WHERE question_id='dual3'").fetchone()[0]
        con.close()
        self.assertEqual((ch, ph), ("3", "1"))                    # phase 不再被折叠丢失
        self.assertEqual(kp, 2)                                   # 重复标签只插一行

    def test_export_sqlite_never_overwrites_non_sqlite_file(self):
        ws = _mk_ws(tempfile.mkdtemp())
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "select_questions.py"),
                            "--workspace", ws, "--export-sqlite", bank_path],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)                         # 拒绝覆盖
        json.load(open(bank_path, encoding="utf-8"))              # 题库完好
        # 已有 sqlite 缓存可以安全重建
        db = os.path.join(tempfile.mkdtemp(), "c.db")
        self._run(ws, ["--export-sqlite", db, "--json"])
        rc, out = self._run(ws, ["--export-sqlite", db, "--json"])
        self.assertEqual(rc, 0)

    def test_empty_dict_answer_not_official(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "edict1", "chapter": 1, "type": "subjective",
                                          "question": "空对象答案？", "answer": {},
                                          "source": "material", "ai_generated": False}])
        db = os.path.join(tempfile.mkdtemp(), "c.db")
        self._run(ws, ["--export-sqlite", db, "--json"])
        import sqlite3
        con = sqlite3.connect(db)
        v = con.execute("SELECT has_official_answer FROM questions WHERE id='edict1'").fetchone()[0]
        con.close()
        self.assertEqual(v, 0)                                    # {} 与校验器口径一致

    def test_empty_string_source_type_rejected(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "select_questions.py"),
                            "--workspace", ws, "--source-type", ""],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)                         # "" 不静默回混合池

    def test_items_expose_both_chapter_and_phase(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "dual2", "chapter": 3, "phase": 1, "type": "subjective",
                                          "question": "双标？", "answer": "E", "source": "material",
                                          "ai_generated": False, "source_type": "homework"}])
        rc, out = self._run(ws, ["--chapter", "1", "--json"])
        it = next(i for i in json.loads(out)["items"] if i["id"] == "dual2")
        self.assertEqual(it["chapter"], 3)                        # 原章号保留
        self.assertEqual(it["phase"], 1)                          # 复习阶段不被折叠丢失

    def test_empty_source_type_filter_rejected(self):
        ws = _mk_ws(tempfile.mkdtemp())
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "select_questions.py"),
                            "--workspace", ws, "--source-type", ","],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)                         # 空过滤器 ≠ 不过滤

    def test_blank_answer_not_official_in_cache(self):
        ws = _mk_ws(tempfile.mkdtemp(), [{"id": "blank1", "chapter": 1, "type": "subjective",
                                          "question": "空白答案？", "answer": "   ",
                                          "source": "material", "ai_generated": False}])
        db = os.path.join(tempfile.mkdtemp(), "c.db")
        self._run(ws, ["--export-sqlite", db, "--json"])
        import sqlite3
        con = sqlite3.connect(db)
        v = con.execute("SELECT has_official_answer FROM questions WHERE id='blank1'").fetchone()[0]
        con.close()
        self.assertEqual(v, 0)                                    # 空白-only 答案不算官方答案

class KnowledgePostings(unittest.TestCase):
    def test_ingest_folds_knowledge_points_into_retrieval_index(self):
        temp = tempfile.mkdtemp()
        raw = {
            "course_name": "Knowledge postings",
            "phases": [{
                "phase_num": 1,
                "phase_name": "Stack",
                "wiki_filename": "ch1.md",
                "wiki_content": "# Stack\n\nLIFO structure.",
            }],
            "quiz_bank": [{
                "id": "lq1",
                "chapter": 1,
                "type": "subjective",
                "question": "Explain stack order.",
                "answer": "LIFO",
                "source": "material",
                "knowledge_points": ["栈", "LIFO"],
            }],
        }
        raw_path = os.path.join(temp, "raw.json")
        with open(raw_path, "w", encoding="utf-8") as stream:
            json.dump(raw, stream, ensure_ascii=False)
        workspace = os.path.join(temp, "workspace")
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "ingest.py"),
             "--input", raw_path, "--output-dir", workspace],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        with open(os.path.join(workspace, "references", "retrieval_index.json"),
                  encoding="utf-8") as stream:
            index = json.load(stream)
        concept = next(doc for doc in index["docs"] if doc["id"] == "concept:lq1")
        self.assertEqual("1", concept["chapter"])
        self.assertEqual("ch01", concept["chapter_id"])
        self.assertIn("栈", concept["text"])
        self.assertFalse(os.path.exists(
            os.path.join(workspace, "references", "knowledge_index.json")
        ))


class ScopeContract(unittest.TestCase):
    # v4-P2: the root zh manual lives at locales/zh/SKILL.md, the en manual at
    # locales/en/SKILL.md (SKILL.en.md retired); the control-layer skills files
    # still carry the override marker in both languages (zh form inside 「…」).
    ENTRY_POINTS = ["locales/zh/SKILL.md", "locales/en/SKILL.md", "AGENTS.md",
                    "prompts/web_prompt.md", "prompts/web_prompt.en.md",
                    "skills/exam-quiz/SKILL.md", "skills/exam-cram/SKILL.md",
                    "skills/exam-tutor/SKILL.md", "skills/exam-review/SKILL.md"]

    @staticmethod
    def _is_en_surface(p):
        return p.endswith(".en.md") or p.replace("\\", "/").startswith("locales/en/")

    def test_all_entry_points_carry_override_marker(self):
        for p in self.ENTRY_POINTS:
            txt = open(os.path.join(ROOT, p), encoding="utf-8").read()
            if self._is_en_surface(p):
                self.assertIn("Temporarily overriding", txt, p)   # C2b：en 面英文声明
                self.assertIn("scope preference", txt, p)
            else:
                self.assertIn("临时覆盖", txt, p)
                self.assertIn("范围偏好", txt, p)

    def test_smoke_detector_positive_negative(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "behavior_smoke"))
        import run_behavior_smoke as B
        good = "⚠️ 临时覆盖你的 homework-only 范围偏好：本轮改从 lecture 选题。\n\n题目 [#mc_q1] xx？"
        self.assertTrue(B.scope_override_declared(good))
        self.assertFalse(B.scope_override_declared("好的，来做图片题。\n\n题目 [#mc_q1] xx？"))
        untagged_first = "1. 这是未标号的题？\n\n⚠️ 临时覆盖你的 homework-only 范围偏好\n\n题目 [#mc_q1] xx？"
        self.assertFalse(B.scope_override_declared(untagged_first))   # 未标号题先出现也算违规
        numbered_cn = "题目一：看图作答？\n\n⚠️ 临时覆盖你的 homework-only 范围偏好"
        self.assertFalse(B.scope_override_declared(numbered_cn))      # 题目一：/题目 1： 也算第一道题
        late = "题目 [#mc_q1] xx？\n\n⚠️ 临时覆盖你的 homework-only 范围偏好"
        self.assertFalse(B.scope_override_declared(late))         # 声明必须在第一道题之前

    def test_no_new_deps_in_scripts(self):
        for p in ("select_questions.py", "ingest.py", "retrieve.py", "ingest_review.py"):
            src = open(os.path.join(SCRIPTS, p), encoding="utf-8").read()
            for banned in ("import requests", "import anthropic", "urllib.request", "import socket"):
                self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
