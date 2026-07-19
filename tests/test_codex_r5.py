# -*- coding: utf-8 -*-
"""Codex r5 回归钉：list 实际锚 / 围栏字符跟踪 / 正文标题参与 slug 计数 / img 包含性 /
ingest 语言持久化 / terms.json 读入纪律。"""
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
import cheatsheet_render  # noqa: E402
import notebook as nb     # noqa: E402
PY = sys.executable
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


def _prepare_image_workspace(ws, image=False):
    assets = os.path.join(ws, "references", "assets")
    os.makedirs(assets)
    with open(os.path.join(ws, "references", "quiz_bank.json"),
              "w", encoding="utf-8") as stream:
        json.dump([], stream)
    if image:
        with open(os.path.join(assets, "f.png"), "wb") as stream:
            stream.write(PNG)


def run_nb(ws, *args, stdin=None):
    return subprocess.run([PY, os.path.join(SCRIPTS, "notebook.py"), "--workspace", ws] + list(args),
                          capture_output=True, text=True, encoding="utf-8", input=stdin)


class ListAnchorsSuffixed(unittest.TestCase):
    def test_list_json_reports_actual_anchor(self):
        with tempfile.TemporaryDirectory() as ws:
            run_nb(ws, "add-entry", "--chapter", "1", "--type", "walkthrough",
                   "--id", "q1", "--title", "同名", stdin="a")
            run_nb(ws, "add-entry", "--chapter", "1", "--type", "feedback",
                   "--id", "q1", "--title", "同名", stdin="b")
            lst = json.loads(run_nb(ws, "list", "--json").stdout)
            anchors = [e["anchor"] for e in lst["entries"]]
            self.assertEqual(sorted(anchors), ["q1-同名", "q1-同名-1"],
                             "list 必须给重复后缀调整后的实际锚: %r" % anchors)


class FenceCharTracking(unittest.TestCase):
    def test_tilde_inside_backtick_fence_is_content(self):
        # CommonMark：反引号栏只有反引号能关——~~~ 在其中是内容；其后的 ## [#x] 仍在栏内
        body = "\n".join(["```", "~~~", "## [#fake] 假条目", "```"])
        with tempfile.TemporaryDirectory() as ws:
            r = run_nb(ws, "add-entry", "--chapter", "1", "--type", "walkthrough",
                       "--id", "real", "--title", "真条目", stdin=body)
            self.assertEqual(r.returncode, 0, r.stderr)
            lst = json.loads(run_nb(ws, "list", "--json").stdout)
            self.assertEqual([e["id"] for e in lst["entries"]], ["real"],
                             "反引号栏内的 ~~~ 不得被当成关栏，假条目不得成块")

    def test_fence_step_semantics(self):
        s, m = nb._fence_step(None, "```py")
        self.assertEqual((s, m), (("`", 3), True))
        s2, m2 = nb._fence_step(s, "~~~")
        self.assertEqual((s2, m2), (s, False), "异字符围栏行是内容")
        s3, m3 = nb._fence_step(s, "``")
        self.assertEqual((s3, m3), (s, False))
        s4, m4 = nb._fence_step(s, "````")
        self.assertEqual((s4, m4), (None, True), "同字符更长可关栏")


class BodyHeadingsCountForSlugs(unittest.TestCase):
    def test_body_heading_shifts_later_entry_suffix(self):
        # 条目正文里出现与后续条目同 slug 的标题时，后续条目的实际锚带 -1（GitHub 全文件计数）
        with tempfile.TemporaryDirectory() as ws:
            run_nb(ws, "add-entry", "--chapter", "2", "--type", "walkthrough",
                   "--id", "q1", "--title", "第一条", stdin="正文里引用：\n### [#q2] 第二条\n以上是示例标题")
            r2 = run_nb(ws, "add-entry", "--chapter", "2", "--type", "walkthrough",
                        "--id", "q2", "--title", "第二条", stdin="真正的第二条")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("#q2-第二条-1", r2.stdout,
                          "正文标题先占了 slug，真条目的实际锚必须带 -1: " + r2.stdout)
            idx = open(os.path.join(ws, "notebook", "index.md"), encoding="utf-8").read()
            self.assertIn("(ch02.md#q2-第二条-1)", idx)


class ImgContainment(unittest.TestCase):
    def test_url_image_rejected(self):
        with tempfile.TemporaryDirectory() as ws:
            _prepare_image_workspace(ws)
            with self.assertRaises(SystemExit):
                cheatsheet_render.md_to_html_body("![x](https://evil.com/a.png)", ws)

    def test_traversal_image_rejected(self):
        with tempfile.TemporaryDirectory() as ws:
            _prepare_image_workspace(ws)
            with self.assertRaises(SystemExit):
                cheatsheet_render.md_to_html_body("![x](../outside.png)", ws)

    def test_missing_image_fails_closed_with_ws(self):
        with tempfile.TemporaryDirectory() as ws:
            _prepare_image_workspace(ws)
            with self.assertRaises(SystemExit):
                cheatsheet_render.md_to_html_body("![x](references/assets/gone.png)", ws)

    def test_contained_existing_image_renders(self):
        with tempfile.TemporaryDirectory() as ws:
            _prepare_image_workspace(ws, image=True)
            body = cheatsheet_render.md_to_html_body("![题面图](references/assets/f.png)", ws)
            self.assertIn('<img src="data:image/png;base64,', body)


class IngestLanguagePersistence(unittest.TestCase):
    RAW = {"course_name": "DS", "phases": [
        {"phase_num": 1, "phase_name": "Lists", "wiki_filename": "ch1.md",
         "wiki_content": "# Lists\ncontent"}], "quiz_bank": []}

    def _ingest(self, tmp, *extra):
        materials = os.path.join(tmp, "materials")
        os.makedirs(materials, exist_ok=True)
        rp = os.path.join(materials, "raw.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(self.RAW, f)
        ws = os.path.join(tmp, "ws")
        env = dict(os.environ)
        env["EXAMPREP_HOME"] = os.path.join(tmp, ".examprep-home")
        confirmed = subprocess.run(
            [
                PY, os.path.join(SCRIPTS, "exam_start.py"), "confirm",
                "--course", "codex-r5-fixture",
                "--materials", materials, "--workspace", ws,
                "--mode", "from_scratch", "--time-budget", "le1d",
                "--language", "en", "--processing-mode", "full", "--json",
            ],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
        r = subprocess.run([PY, os.path.join(SCRIPTS, "ingest.py"), "--input", rp,
                            "--output-dir", ws] + list(extra),
                           capture_output=True, text=True, encoding="utf-8", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        return ws

    def test_explicit_lang_preserves_confirmed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ingest(tmp, "--lang", "en")
            prog = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
            self.assertIn("语言偏好", prog)
            self.assertIn("English", prog)
            st = json.load(open(os.path.join(ws, "study_state.json"), encoding="utf-8"))
            self.assertEqual(st["language"], "en",
                             "显式 --lang en 不得覆盖已确认的 English 学习状态")

    def test_no_lang_flag_preserves_confirmed_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ingest(tmp)
            prog = open(os.path.join(ws, "study_progress.md"), encoding="utf-8").read()
            self.assertNotIn("<!-- LANGUAGE -->", prog)
            self.assertIn("语言偏好", prog)
            self.assertIn("English", prog)
            st = json.load(open(os.path.join(ws, "study_state.json"), encoding="utf-8"))
            self.assertEqual(st["language"], "en")


class TermsReadDiscipline(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_symlinked_terms_refused(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "references"))
            outside = os.path.join(ws, "..", "terms_%s.json" % os.path.basename(ws))
            with open(outside, "w", encoding="utf-8") as f:
                json.dump({"a": ["b"]}, f)
            try:
                os.symlink(outside, os.path.join(ws, "references", "terms.json"))
            except OSError:
                self.skipTest("symlink privilege unavailable")
            finally:
                pass
            try:
                import retrieve
                with self.assertRaises(SystemExit):
                    retrieve.load_terms(ws)
            finally:
                os.remove(outside)


if __name__ == "__main__":
    unittest.main(verbosity=2)
