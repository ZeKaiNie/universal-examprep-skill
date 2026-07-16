# -*- coding: utf-8 -*-
"""Codex r2 回归钉：phase_num 校验期规范化 / 停用词弃答门限 / Grep 正则不进轨迹 / 图片进打印页。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(ROOT, "benchmark"))
import cheatsheet_render  # noqa: E402
import gen                # noqa: E402
import retrieve           # noqa: E402
PY = sys.executable
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)

RAW_OK = {
    "course_name": "数据结构",
    "phases": [{"phase_num": "1", "phase_name": "线性表", "wiki_filename": "ch1.md",
                "wiki_content": "# 线性表\n\n## 链表\n链表由节点组成，访问代价 O(n)。" + " 补充。" * 30}],
    "quiz_bank": [],
}


def _run_ingest(raw, tmp):
    rp = os.path.join(tmp, "raw_input.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    ws = os.path.join(tmp, "ws")
    r = subprocess.run([PY, os.path.join(SCRIPTS, "ingest.py"), "--input", rp,
                        "--output-dir", ws], capture_output=True, text=True, encoding="utf-8")
    return ws, r


class PhaseNumValidation(unittest.TestCase):
    def test_digit_string_phase_num_coerced(self):
        tmp = tempfile.mkdtemp(prefix="r2pn-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws, r = _run_ingest(RAW_OK, tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(ws, "references", "retrieval_index.json"), encoding="utf-8") as f:
            ids = {d["id"] for d in json.load(f)["docs"]}
        self.assertTrue(all(i.startswith("ch01#") for i in ids), ids)

    def test_bad_phase_num_fails_clean_before_writing(self):
        bad = dict(RAW_OK)
        bad["phases"] = [dict(RAW_OK["phases"][0], phase_num="一")]
        tmp = tempfile.mkdtemp(prefix="r2pn-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws, r = _run_ingest(bad, tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("phase_num", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr, "校验期拒绝，不许裸 TypeError")
        self.assertFalse(os.path.exists(os.path.join(ws, "references", "quiz_bank.json")),
                         "校验失败不得留下半个工作区")


class StopwordAbstainGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r2sw-")
        cls.ws, r = _run_ingest(RAW_OK, cls.tmp)
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_function_word_only_english_query_abstains(self):
        # 英文功能词与任何英文材料都重合——不滤停用词时这里会拿到正分 hits（Codex r2 P1）
        r = subprocess.run([PY, os.path.join(SCRIPTS, "retrieve.py"), "--workspace", self.ws,
                            "--query", "What is the capital of France?", "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 4, "纯功能词/材料外问题必须走弃答退出码: " + r.stdout)
        self.assertTrue(json.loads(r.stdout)["abstain"])

    def test_informative_terms_helper(self):
        toks = retrieve.informative_terms(
            ["what", "is", "the", "linked", "list", "of", "链表"])
        self.assertEqual(toks, ["linked", "list", "链表"])

    def test_real_query_still_hits(self):
        r = subprocess.run([PY, os.path.join(SCRIPTS, "retrieve.py"), "--workspace", self.ws,
                            "--query", "链表 访问代价", "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class GrepPatternNotAFile(unittest.TestCase):
    def _events(self, name, inp):
        return "\n".join([
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}),
            json.dumps({"type": "result", "result": "ans", "total_cost_usd": 0.01}),
        ])

    def test_grep_search_regex_excluded(self):
        out, cost, files = gen.parse_stream_events(
            self._events("Grep", {"pattern": "lecture02", "path": "references/wiki"}))
        self.assertNotIn("lecture02", files, "Grep 搜索正则不是打开的文件，不得虚标 recall")
        self.assertIn("references/wiki", files)

    def test_glob_pattern_still_recorded(self):
        out, cost, files = gen.parse_stream_events(
            self._events("Glob", {"pattern": "references/wiki/ch0*.md"}))
        self.assertIn("references/wiki/ch0*.md", files)

    def test_read_file_path_recorded(self):
        out, cost, files = gen.parse_stream_events(
            self._events("Read", {"file_path": "references/wiki/ch02.md"}))
        self.assertEqual(files, ["references/wiki/ch02.md"])


class ImageSurvivesRendering(unittest.TestCase):
    def test_image_md_becomes_img_tag(self):
        with tempfile.TemporaryDirectory() as ws:
            assets = os.path.join(ws, "references", "assets")
            os.makedirs(assets)
            with open(os.path.join(assets, "ch02_p3_fig.png"), "wb") as stream:
                stream.write(PNG)
            with open(os.path.join(ws, "references", "quiz_bank.json"),
                      "w", encoding="utf-8") as stream:
                json.dump([], stream)
            body = cheatsheet_render.md_to_html_body(
                "## 例题\n\n![题面图](references/assets/ch02_p3_fig.png)\n\n"
                "- 要点（[→](references/wiki/ch1.md)）\n", ws)
            self.assertIn('<img src="data:image/png;base64,', body,
                          "打印页必须自包含题面图，不得被链接压平规则吃掉")
            self.assertIn('alt="题面图"', body)
            self.assertIn('<span class="lnk">→</span>', body, "普通链接仍应压平为纯文本")
            self.assertNotIn("<img src=\"references/wiki", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
