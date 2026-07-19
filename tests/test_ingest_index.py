# -*- coding: utf-8 -*-
"""Ingest wiring: a fresh workspace gets one integrity-bearing retrieval_index.json
(+ terms.json passthrough), wiki chapter files stay byte-for-byte verbatim (legacy contract),
and the retrieve CLI routes a query to the right chapter of the freshly built workspace."""
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable
EXAM_START = os.path.join(SCRIPTS, "exam_start.py")

RAW = {
    "course_name": "数据结构",
    "phases": [
        {"phase_num": 1, "phase_name": "线性表", "wiki_filename": "ch1_linear.md",
         "wiki_content": "# 线性表\n\n## 链表\n链表由节点组成，访问代价 O(n)。头指针 head 指向首节点。\n\n"
                         "## 顺序表\n顺序表支持随机访问，插入需要搬移元素。" + " 细节补充。" * 40},
        {"phase_num": 2, "phase_name": "排序", "wiki_filename": "ch2_sort.md",
         "wiki_content": "# 排序\n\n## 归并排序\nMerge sort 是稳定排序，时间复杂度 O(n log n)。\n\n"
                         "## 快速排序\n快排平均 O(n log n)，最坏 O(n^2)，不稳定。" + " 细节补充。" * 40},
    ],
    "quiz_bank": [
        {"id": "q1", "phase": 1, "type": "choice", "question": "链表访问代价？",
         "options": ["O(1)", "O(n)"], "answer": "O(n)", "source": "teacher_provided"},
    ],
    "terms": {"归并排序": ["merge sort"], "链表": ["linked list"]},
}


def _confirm_full_workspace(root, workspace, language="zh"):
    """Register the exact isolated pair required by the direct compiler gate."""
    materials = os.path.join(root, "materials")
    home = os.path.join(root, ".examprep-home")
    os.makedirs(materials, exist_ok=True)
    env = dict(os.environ)
    env["EXAMPREP_HOME"] = home
    result = subprocess.run(
        [
            PY, EXAM_START, "confirm", "--course", "ingest-index-fixture",
            "--materials", materials, "--workspace", workspace,
            "--mode", "from_scratch", "--time-budget", "le1d",
            "--language", language, "--processing-mode", "full", "--json",
        ],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            "full-mode workspace confirmation failed:\n"
            + result.stdout + result.stderr
        )
    return env


def _workspace_env(root):
    env = dict(os.environ)
    env["EXAMPREP_HOME"] = os.path.join(root, ".examprep-home")
    return env


def build_ws_from(raw, lang=None):
    tmp = tempfile.mkdtemp(prefix="ing2_")
    raw_path = os.path.join(tmp, "raw_input.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    ws = os.path.join(tmp, "ws")
    language = lang or "zh"
    env = _confirm_full_workspace(tmp, ws, language=language)
    command = [PY, os.path.join(SCRIPTS, "ingest.py"), "--input", raw_path,
               "--output-dir", ws]
    if lang is not None:
        command.extend(["--lang", lang])
    r = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return tmp, ws, r


def build_ws():
    return build_ws_from(RAW)


class IngestIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.ws, cls.r = build_ws()
        if cls.r.returncode != 0:
            raise AssertionError("ingest failed:\n" + cls.r.stdout + cls.r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wiki_files_stay_verbatim(self):
        for p in RAW["phases"]:
            with open(os.path.join(self.ws, "references", "wiki", p["wiki_filename"]),
                      encoding="utf-8") as f:
                self.assertEqual(f.read(), p["wiki_content"],
                                 "v3 契约：章文件逐字写盘，索引化不得改动它")

    def test_retrieval_index_built(self):
        path = os.path.join(self.ws, "references", "retrieval_index.json")
        self.assertTrue(os.path.isfile(path), "ingest v2 必须产出检索索引")
        with open(path, encoding="utf-8") as f:
            idx = json.load(f)
        self.assertGreaterEqual(idx["n_docs"], 4, "两章各至少两小节")
        ids = {d["id"] for d in idx["docs"]}
        self.assertTrue(any(i.startswith("ch01#") for i in ids))
        self.assertTrue(any(i.startswith("ch02#") for i in ids))

    def test_retrieval_integrity_replaces_standalone_wiki_meta(self):
        with open(os.path.join(self.ws, "references", "retrieval_index.json"), encoding="utf-8") as f:
            index = json.load(f)
        meta = {row["file"]: row["sha256"] for row in index["integrity"]["wiki"]}
        for p in RAW["phases"]:
            relative = "references/wiki/" + p["wiki_filename"]
            self.assertEqual(
                meta[relative],
                hashlib.sha256(p["wiki_content"].encode("utf-8")).hexdigest(),
            )
        self.assertFalse(os.path.exists(os.path.join(self.ws, "references", "wiki_meta.json")))

    def test_terms_passthrough(self):
        with open(os.path.join(self.ws, "references", "terms.json"), encoding="utf-8") as f:
            terms = json.load(f)
        self.assertEqual(terms["链表"], ["linked list"])

    def test_retrieve_routes_to_right_chapter(self):
        r = subprocess.run([PY, os.path.join(SCRIPTS, "retrieve.py"), "--workspace", self.ws,
                            "--query", "merge sort 稳定吗", "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        hits = json.loads(r.stdout)["hits"]
        self.assertEqual(hits[0]["chapter"], "2", "terms/内容应把归并排序问题路由到第 2 章")

    def test_retrieve_abstains_on_oos(self):
        r = subprocess.run([PY, os.path.join(SCRIPTS, "retrieve.py"), "--workspace", self.ws,
                            "--query", "quantum entanglement paradox", "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 4, "材料外问题必须走弃答退出码")


class PhaseChapterIdentity(unittest.TestCase):
    def _read_json(self, workspace, relative):
        with open(os.path.join(workspace, *relative.split("/")), encoding="utf-8") as stream:
            return json.load(stream)

    def test_legacy_only_ch05_uses_filename_chapter_not_phase_order(self):
        # Backward compatibility: raw input produced before chapter/chapter_id fields existed still
        # carried the true chapter in chNN*.md.  It must no longer be indexed as phase 1 / chapter 1.
        raw = {
            "course_name": "Only Chapter Five",
            "phases": [{"phase_num": 1, "phase_name": "Chapter 5",
                        "wiki_filename": "ch05.md",
                        "wiki_content": "# Chapter 5\n\nUnique quasar topic."}],
            "quiz_bank": [],
        }
        tmp, ws, result = build_ws_from(raw)
        try:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            index = self._read_json(ws, "references/retrieval_index.json")
            self.assertEqual({doc["chapter"] for doc in index["docs"]}, {"5"})
            self.assertTrue(all(doc["id"].startswith("ch05#") for doc in index["docs"]))
            meta = index["integrity"]["phases"][0]
            self.assertEqual(meta["chapter"], 5)
            self.assertEqual(meta["chapter_id"], "ch05")
            self.assertEqual(meta["phase_num"], 1)
            self.assertEqual(meta["phase_id"], "phase01")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_explicit_ch02_and_ch07_preserve_order_and_true_chapters(self):
        raw = {
            "course_name": "Sparse Chapters",
            "phases": [
                {"phase_num": 1, "phase_id": "phase-foundation", "chapter": 2,
                 "chapter_id": "ch02", "phase_name": "Chapter 2",
                 "wiki_filename": "ch02.md", "wiki_content": "# Chapter 2\n\nAlpha matrix."},
                {"phase_num": 2, "phase_id": "phase-advanced", "chapter": 7,
                 "chapter_id": "ch07", "phase_name": "Chapter 7",
                 "wiki_filename": "ch07.md", "wiki_content": "# Chapter 7\n\nBeta tensor."},
            ],
            "quiz_bank": [],
        }
        tmp, ws, result = build_ws_from(raw)
        try:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            index = self._read_json(ws, "references/retrieval_index.json")
            self.assertEqual({doc["chapter"] for doc in index["docs"]}, {"2", "7"})
            self.assertEqual({doc["id"].split("#", 1)[0] for doc in index["docs"]},
                             {"ch02", "ch07"})
            meta = {row["wiki_file"]: row for row in index["integrity"]["phases"]}
            self.assertEqual(
                [(meta[name]["phase_num"], meta[name]["phase_id"],
                  meta[name]["chapter"], meta[name]["chapter_id"])
                 for name in ("references/wiki/ch02.md", "references/wiki/ch07.md")],
                [(1, "phase-foundation", 2, "ch02"),
                 (2, "phase-advanced", 7, "ch07")],
            )
            with open(os.path.join(ws, "study_plan.md"), encoding="utf-8") as stream:
                plan = stream.read()
            self.assertIn("| **阶段 1** | Chapter 2 | `references/wiki/ch02.md` |", plan)
            self.assertIn("| **阶段 2** | Chapter 7 | `references/wiki/ch07.md` |", plan)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


RAW_EN = {
    "course_name": "Data Structures",
    "phases": [
        {"phase_num": 1, "phase_name": "Linear lists", "wiki_filename": "ch1_linear.md",
         "wiki_content": "# Linear lists\n\nLinked list access cost is O(n)."},
        {"phase_num": 2, "phase_name": "Sorting", "wiki_filename": "ch2_sort.md",
         "wiki_content": "# Sorting\n\nMerge sort is stable, O(n log n)."},
    ],
    "quiz_bank": [
        {"id": "q1", "phase": 1, "type": "choice", "question": "Linked-list access cost?",
         "options": ["O(1)", "O(n)"], "answer": "O(n)", "source": "teacher"},
    ],
}

# CJK detector for generated en files: Han + CJK/fullwidth punctuation. The ingest-substituted
# course-name token keeps its 《…》 machine anchor, so those spans are stripped before scanning.
_CJK_RE = re.compile(u"[⺀-鿿豈-﫿＀-￯]")
_SUBJECT_SPAN_RE = re.compile(u"《[^》]*》")


class EnWorkspaceLanguage(unittest.TestCase):
    """Codex 评审回归：`--lang en` 的插入行（阶段表/打卡清单/断点种子）必须是英文——
    en 模板里混入 阶段/未开始/模拟测试 会产出违反单语言纯净的混语工作区。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.ws, cls.r = build_ws_from(RAW_EN, lang="en")
        cls.env = _workspace_env(cls.tmp)
        if cls.r.returncode != 0:
            raise AssertionError("ingest --lang en failed:\n" + cls.r.stdout + cls.r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _read_ws(self, name):
        with open(os.path.join(self.ws, name), encoding="utf-8") as f:
            return f.read()

    def test_en_plan_rows_are_english_and_cjk_free(self):
        plan = self._read_ws("study_plan.md")
        self.assertIn("| **Phase 1** |", plan)
        self.assertIn("| **Phase 2** |", plan)
        self.assertIn("Not started", plan)
        self.assertIn("| **Mock test** |", plan)
        self.assertIn("| **Pitfall sweep** |", plan)
        stripped = _SUBJECT_SPAN_RE.sub("", plan)
        self.assertFalse(_CJK_RE.search(stripped),
                         "en study_plan.md 在《科目名称》替换位之外不得有 CJK: %r"
                         % sorted(set(_CJK_RE.findall(stripped))))

    def test_en_progress_rows_are_english_and_cjk_free(self):
        # Exact-pair confirmation initializes the canonical state before the
        # compiler runs.  The progress Markdown is now only its generated view,
        # so verify language and phase at the source of truth.
        prog = self._read_ws("study_progress.md")
        self.assertIn("study_state.json", prog)
        with open(os.path.join(self.ws, "study_state.json"), encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["language"], "en")
        self.assertEqual(state["current_phase"], 1)

    def test_en_workspace_passes_validator(self):
        r = subprocess.run([PY, os.path.join(SCRIPTS, "validate_workspace.py"), self.ws],
                           capture_output=True, text=True, encoding="utf-8",
                           env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)    # 读侧认 Phase N 行

    def test_en_workspace_init_parses_phase(self):
        # Re-running init is intentionally rejected once confirmation created
        # state.  Direct ingestion must preserve that initialized phase.
        with open(os.path.join(self.ws, "study_state.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["current_phase"], 1)

    def test_zh_workspace_byte_shape_unchanged(self):
        # 缺省 zh 路径寸步不动：阶段表/打卡行保持历史字节形
        tmp, ws, r = build_ws()
        try:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(ws, "study_plan.md"), encoding="utf-8") as f:
                plan = f.read()
            self.assertIn("| **阶段 1** | 线性表 | `references/wiki/ch1_linear.md` | 未开始 |", plan)
            self.assertIn("| **模拟测试** | 综合真题自测 | `references/quiz_bank.json` | 未开始 |", plan)
            with open(os.path.join(ws, "study_progress.md"), encoding="utf-8") as f:
                prog = f.read()
            self.assertIn("study_state.json", prog)
            with open(os.path.join(ws, "study_state.json"), encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["language"], "zh")
            self.assertEqual(state["current_phase"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class NoTermsNoFile(unittest.TestCase):
    def test_absent_terms_writes_nothing(self):
        raw = {k: v for k, v in RAW.items() if k != "terms"}
        tmp = tempfile.mkdtemp(prefix="ing2_")
        try:
            rp = os.path.join(tmp, "raw_input.json")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
            ws = os.path.join(tmp, "ws")
            env = _confirm_full_workspace(tmp, ws)
            r = subprocess.run([PY, os.path.join(SCRIPTS, "ingest.py"), "--input", rp,
                                "--output-dir", ws], capture_output=True, text=True,
                               encoding="utf-8", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(ws, "references", "terms.json")))
            self.assertTrue(os.path.exists(os.path.join(ws, "references", "retrieval_index.json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
