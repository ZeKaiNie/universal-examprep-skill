# -*- coding: utf-8 -*-
"""Codex r3 回归钉：渲染器输出安全（符号链接/旧 PDF/属性注入/字号锁定）+ 溯源链接穿越拒绝。"""
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
import cheatsheet_render   # noqa: E402
import validate_workspace as V  # noqa: E402
PY = sys.executable
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


def _safe_image_workspace(testcase):
    ws = tempfile.mkdtemp(prefix="r3img-")
    testcase.addCleanup(shutil.rmtree, ws, ignore_errors=True)
    assets = os.path.join(ws, "references", "assets")
    os.makedirs(assets)
    with open(os.path.join(assets, "f.png"), "wb") as stream:
        stream.write(PNG)
    with open(os.path.join(ws, "references", "quiz_bank.json"),
              "w", encoding="utf-8") as stream:
        json.dump([], stream)
    return ws


class ImgAttributeEscaping(unittest.TestCase):
    def test_quote_in_alt_cannot_escape_attribute(self):
        ws = _safe_image_workspace(self)
        body = cheatsheet_render.md_to_html_body(
            '![x" onerror="alert(1)](references/assets/f.png)\n', ws)
        self.assertNotIn('onerror="alert', body, "alt 引号必须被转义，不得逃出属性")
        self.assertIn("&quot;", body)

    def test_ampersand_not_double_escaped(self):
        ws = _safe_image_workspace(self)
        body = cheatsheet_render.md_to_html_body(
            '![A & B](references/assets/f.png)\n', ws)
        self.assertIn('alt="A &amp; B"', body)
        self.assertNotIn("&amp;amp;", body, "整行已 escape 过一次，属性不得二次转义 &")


class OutputPathSafety(unittest.TestCase):
    def _ws_with_sheet(self):
        d = tempfile.mkdtemp(prefix="r3out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "cheatsheet.md"), "w", encoding="utf-8") as f:
            f.write("# 小抄\n\n- 要点 x\n")
        os.makedirs(os.path.join(d, "references"))
        with open(os.path.join(d, "references", "quiz_bank.json"),
                  "w", encoding="utf-8") as f:
            json.dump([], f)
        return d

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_symlinked_html_output_refused(self):
        d = self._ws_with_sheet()
        outside = os.path.join(d, "..", "outside_%s.html" % os.path.basename(d))
        open(outside, "w").close()
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        try:
            os.symlink(outside, os.path.join(d, "cheatsheet.html"))
        except OSError:
            self.skipTest("symlink privilege unavailable")
        r = subprocess.run([PY, os.path.join(SCRIPTS, "cheatsheet_render.py"),
                            "--workspace", d, "--pages", "1", "--html-only"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("符号链接", r.stderr)

    def test_html_only_writes_atomically(self):
        d = self._ws_with_sheet()
        r = subprocess.run([PY, os.path.join(SCRIPTS, "cheatsheet_render.py"),
                            "--workspace", d, "--pages", "1", "--html-only"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(d, "cheatsheet.html")))
        self.assertFalse(os.path.exists(os.path.join(d, ".cheatsheet.rendering.html")),
                         "临时文件必须被 os.replace 收走")


class StalePdfAndFontLock(unittest.TestCase):
    def test_print_to_pdf_removes_stale_and_fails_loud(self):
        d = tempfile.mkdtemp(prefix="r3pdf-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        html = os.path.join(d, "s.html"); pdf = os.path.join(d, "s.pdf")
        open(html, "w").close()
        with open(pdf, "wb") as f:
            f.write(b"%PDF stale /Type /Page x")
        fake = os.path.join(d, "fakebrowser.py")
        with open(fake, "w", encoding="utf-8") as f:
            f.write("import sys; sys.exit(9)\n")
        # 用 python 冒充失败的浏览器：旧 PDF 已存在——修复前这里会静默拿旧文件当成功
        orig_run = cheatsheet_render.subprocess.run
        def fake_run(args, **kw):
            return orig_run([PY, fake], capture_output=True)
        cheatsheet_render.subprocess.run = fake_run
        self.addCleanup(setattr, cheatsheet_render.subprocess, "run", orig_run)
        with self.assertRaises(SystemExit) as cm:
            cheatsheet_render.print_to_pdf("fakebrowser", html, pdf)
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(os.path.exists(pdf), "失败时不得留下旧 PDF 冒充新产出")

    def test_explicit_font_size_pins_font_in_source(self):
        # 行为锁在源码逻辑上：显式 --font-size 时 fit-loop 一轮即 break（font_locked）
        import inspect
        src = inspect.getsource(cheatsheet_render.main)
        self.assertIn("font_locked", src)
        self.assertIn("or font_locked", src, "显式字号必须短路拟合环的 nudge 分支")


class TraversalLinksRejected(unittest.TestCase):
    def _ws(self, link):
        d = tempfile.mkdtemp(prefix="r3trav-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "references", "wiki"))
        open(os.path.join(d, "references", "wiki", "ch1.md"), "w", encoding="utf-8").write("# c")
        with open(os.path.join(d, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "q1", "chapter": 1, "type": "subjective", "question": "q",
                        "keywords": ["a"], "answer": "a", "source": "teacher"}], f)
        open(os.path.join(d, "study_plan.md"), "w", encoding="utf-8").write("阶段 1\n")
        open(os.path.join(d, "study_progress.md"), "w", encoding="utf-8").write(
            "## 当前复习断点\n阶段 1\n\n## 💡 概念疑难点记录\n")
        # 工作区外造一个带锚的 md，穿越链接指向它
        outside_dir = os.path.dirname(d)
        outside = os.path.join(outside_dir, "evil_%s.md" % os.path.basename(d))
        with open(outside, "w", encoding="utf-8") as f:
            f.write("## [#q1] evil\n")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        with open(os.path.join(d, "cheatsheet.md"), "w", encoding="utf-8") as f:
            f.write("# 小抄\n- 要点（[→](%s)）\n" % link)
        return d, os.path.basename(outside)

    def test_dotdot_traversal_rejected(self):
        d, evil = self._ws("notebook/../../%s" % "PLACEHOLDER")
        # 重写 cheatsheet 用真实文件名
        with open(os.path.join(d, "cheatsheet.md"), "w", encoding="utf-8") as f:
            f.write("# 小抄\n- 要点（[→](notebook/../../%s#q1-evil)）\n" % evil)
        errors, _, _ = V.validate(d)
        self.assertTrue(any("穿越" in e["msg"] or "逃出" in e["msg"] for e in errors),
                        [e["msg"] for e in errors])


if __name__ == "__main__":
    unittest.main(verbosity=2)
