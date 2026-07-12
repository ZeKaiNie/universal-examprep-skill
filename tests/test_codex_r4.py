# -*- coding: utf-8 -*-
"""Codex r4 回归钉（A/B 组）：tmp 符号链接、输入符号链接、无浏览器旧 PDF、v3bak 悬空链接。
（C 组 notebook 语义钉在 tests/test_notebook.py：replace_keyed_on_id_and_type / duplicate_slug。）"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable


def _ws_with_sheet():
    d = tempfile.mkdtemp(prefix="r4-")
    with open(os.path.join(d, "cheatsheet.md"), "w", encoding="utf-8") as f:
        f.write("# 小抄\n\n- 要点 x\n")
    return d


def _render(d, *extra):
    return subprocess.run([PY, os.path.join(SCRIPTS, "cheatsheet_render.py"),
                           "--workspace", d, "--pages", "1"] + list(extra),
                          capture_output=True, text=True, encoding="utf-8")


class RendererPathSafety(unittest.TestCase):
    def setUp(self):
        self.d = _ws_with_sheet()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_tmp_symlink_cleared_not_followed(self):
        outside = os.path.join(self.d, "..", "r4_out_%s.html" % os.path.basename(self.d))
        open(outside, "w").close()
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        try:
            os.symlink(outside, os.path.join(self.d, "cheatsheet.html.tmp"))
        except OSError:
            self.skipTest("symlink privilege unavailable")
        r = _render(self.d, "--html-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 预埋的 tmp 链接必须被清掉而不是被跟随写入——外部文件保持空
        with open(outside, encoding="utf-8") as f:
            self.assertEqual(f.read(), "", "写入跟随了预埋 tmp 符号链接（逃出工作区）")
        self.assertTrue(os.path.isfile(os.path.join(self.d, "cheatsheet.html")))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_symlinked_input_md_refused(self):
        outside_md = os.path.join(self.d, "..", "r4_in_%s.md" % os.path.basename(self.d))
        with open(outside_md, "w", encoding="utf-8") as f:
            f.write("# 外部内容\n")
        self.addCleanup(lambda: os.path.exists(outside_md) and os.remove(outside_md))
        os.remove(os.path.join(self.d, "cheatsheet.md"))
        try:
            os.symlink(outside_md, os.path.join(self.d, "cheatsheet.md"))
        except OSError:
            self.skipTest("symlink privilege unavailable")
        r = _render(self.d, "--html-only")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("符号链接", r.stderr + r.stdout)

    def test_no_browser_removes_stale_pdf(self):
        with open(os.path.join(self.d, "cheatsheet.pdf"), "wb") as f:
            f.write(b"%PDF stale")
        env = dict(os.environ, EXAMPREP_NO_BROWSER="1", PATH="")  # 藏起浏览器
        r = subprocess.run([PY, os.path.join(SCRIPTS, "cheatsheet_render.py"),
                            "--workspace", self.d, "--pages", "1"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        if r.returncode != 3:
            self.skipTest("browser still discoverable in this environment")
        self.assertFalse(os.path.exists(os.path.join(self.d, "cheatsheet.pdf")),
                         "无浏览器降级路径必须移除旧 PDF，防止打印过期成品")


class BackupSymlinkGuard(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_dangling_v3bak_symlink_not_followed(self):
        d = tempfile.mkdtemp(prefix="r4bak-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # 旧词汇 state 触发迁移 → 会写 study_state.json.v3bak 备份
        legacy = {"version": 1, "current_phase": 1, "scope": None, "mode": "查缺补漏",
                  "time_budget": "1-3天", "language": "中文", "preferences": {},
                  "mistake_archive": [], "confusion_log": [], "knowledge_window": [],
                  "phase_checklist": [], "last_updated": None}
        with open(os.path.join(d, "study_state.json"), "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        with open(os.path.join(d, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("阶段 1\n")
        outside = os.path.join(d, "..", "r4_leak_%s.json" % os.path.basename(d))
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        try:
            os.symlink(outside, os.path.join(d, "study_state.json.v3bak"))  # 悬空链接
        except OSError:
            self.skipTest("symlink privilege unavailable")
        r = subprocess.run([PY, os.path.join(SCRIPTS, "update_progress.py"),
                            "--workspace", d, "show"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertFalse(os.path.exists(outside),
                         "备份写入跟随了悬空符号链接（学生状态泄到工作区外）: " + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
