# -*- coding: utf-8 -*-
"""v4-P3 缺口8 — trace capture (gen.parse_stream_events) + chapter-routing recall
(benchmark/retrieval_eval.py). Offline: synthetic stream-json transcripts, no claude calls."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmark"))
import gen  # noqa: E402
import retrieval_eval as rev  # noqa: E402


def _ev(obj):
    return json.dumps(obj, ensure_ascii=False)


STREAM = "\n".join([
    _ev({"type": "system", "subtype": "init"}),
    _ev({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Glob", "input": {"pattern": "references/wiki/*.md"}}]}}),
    _ev({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ch01.md ch02.md"}]}}),
    _ev({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "references/wiki/ch02.md"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "references/wiki/ch02.md"}}]}}),
    _ev({"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking..."}]}}),
    _ev({"type": "result", "result": "The answer is X.", "total_cost_usd": 0.0123}),
])


class ParseStream(unittest.TestCase):
    def test_extracts_result_cost_and_files(self):
        result, cost, files = gen.parse_stream_events(STREAM)
        self.assertEqual(result, "The answer is X.")
        self.assertAlmostEqual(cost, 0.0123)
        self.assertEqual(files, ["references/wiki/*.md", "references/wiki/ch02.md"],
                         "按调用顺序去重记录 Glob pattern + Read file_path")

    def test_garbage_lines_ignored(self):
        result, cost, files = gen.parse_stream_events("not json\n\n" + STREAM + "\nbroken{")
        self.assertEqual(result, "The answer is X.")
        self.assertEqual(len(files), 2)

    def test_empty_input(self):
        self.assertEqual(gen.parse_stream_events(""), ("", None, []))


class ChapterMap(unittest.TestCase):
    def test_source_file_forms(self):
        for path, want in [("materials/x/lecture02.md", 2), ("lec7.pdf", 7),
                           ("references/wiki/ch14.md", 14), ("wiki/ch02/s03_x.md", 2),
                           ("Chapter 9 notes.txt", 9), ("ps04.md", 4), ("syllabus.md", None)]:
            self.assertEqual(rev.chapter_of(path), want, path)

    def test_opened_chapters_dedup(self):
        self.assertEqual(rev.opened_chapters(
            ["references/wiki/ch02.md", "ch02/s01_a.md", "notes.txt"]), {2})


class Evaluate(unittest.TestCase):
    ITEMS = [
        {"id": "q1", "source_file": "materials/lecture02.md", "answerable": True},
        {"id": "q2", "source_file": "materials/lecture05.md", "answerable": True},
        {"id": "q3", "source_file": "materials/syllabus.md", "answerable": True},   # unmappable
        {"id": "oos1", "source_file": None, "answerable": False},                    # probe: excluded
    ]
    ANSWERS = [
        {"id": "q1", "model": "haiku", "arm": "skill",
         "files_opened": ["references/wiki/ch02.md"], "answer": "..."},              # hit
        {"id": "q2", "model": "haiku", "arm": "skill",
         "files_opened": ["references/wiki/ch04.md"], "answer": "..."},              # miss
        {"id": "q1", "model": "haiku", "arm": "rawfiles", "answer": "no trace"},     # untraced
    ]

    def test_recall_and_loud_accounting(self):
        res = rev.evaluate(self.ANSWERS, self.ITEMS)
        cell = res["cells"]["haiku|skill"]
        self.assertEqual((cell["n_traced"], cell["n_hit"]), (2, 1))
        self.assertAlmostEqual(cell["recall"], 0.5)
        self.assertEqual(res["unmapped_items"], ["q3"], "提不出章号的题必须响亮列出")
        self.assertEqual(res["n_untraced_answers"], 1)
        self.assertEqual(res["n_gold"], 2, "越界探针与不可映射题不进分母")

    def test_arm_filter(self):
        res = rev.evaluate(self.ANSWERS, self.ITEMS, arm="skill")
        self.assertNotIn("haiku|rawfiles", res["cells"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
