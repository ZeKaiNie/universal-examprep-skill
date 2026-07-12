# -*- coding: utf-8 -*-
"""依赖预检清单（check_deps.py）：清单结构、材料感知的 needed 判定、退出码契约、分发包收录。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import check_deps  # noqa: E402
PY = sys.executable


class ManifestShape(unittest.TestCase):
    def test_groups_cover_the_three_capabilities(self):
        ids = {g["id"] for g in check_deps.GROUPS}
        self.assertEqual(ids, {"pdf_text", "pdf_render", "browser"})

    def test_every_pip_group_has_install_command(self):
        rep = check_deps.build_report()
        for r in rep["groups"]:
            self.assertTrue(r["install"], r["id"])
            if r["id"] != "browser" and not r["ok"]:
                self.assertIn("pip install", r["install"])

    def test_json_mode_is_machine_readable(self):
        r = subprocess.run([PY, os.path.join(SCRIPTS, "check_deps.py"), "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        rep = json.loads(r.stdout)
        self.assertIn("groups", rep)
        self.assertIn("missing_needed", rep)


class MaterialsAwareness(unittest.TestCase):
    def test_no_materials_means_unknown_needed(self):
        rep = check_deps.build_report(None)
        self.assertEqual(rep["materials_have_pdf"], None)
        self.assertEqual([r for r in rep["groups"] if r["id"] == "pdf_text"][0]["needed"],
                         "unknown")
        self.assertEqual(rep["missing_needed"], [], "不可判定时绝不硬报缺失")

    def test_pdf_materials_flip_needed_true(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "lec01.pdf"), "wb").write(b"%PDF")
            rep = check_deps.build_report(d)
            self.assertTrue(rep["materials_have_pdf"])
            row = [r for r in rep["groups"] if r["id"] == "pdf_text"][0]
            self.assertIs(row["needed"], True)

    def test_text_only_materials_not_needed(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "notes.md"), "w").write("x")
            rep = check_deps.build_report(d)
            self.assertIs(rep["materials_have_pdf"], False)
            row = [r for r in rep["groups"] if r["id"] == "pdf_text"][0]
            self.assertIs(row["needed"], False)
            self.assertEqual(rep["missing_needed"], [])

    def test_browser_group_never_hard_missing(self):
        # 小抄 PDF 有降级路径（HTML+手动打印）——browser 永远是 optional，不进 missing_needed
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "lec01.pdf"), "wb").write(b"%PDF")
            rep = check_deps.build_report(d)
            self.assertNotIn("browser", rep["missing_needed"])

    def test_exit_code_contract(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "notes.txt"), "w").write("x")
            r = subprocess.run([PY, os.path.join(SCRIPTS, "check_deps.py"),
                                "--materials", d], capture_output=True, text=True,
                               encoding="utf-8")
            self.assertEqual(r.returncode, 0, "无 PDF 材料时任何缺失都不算 NEEDED")


class ShipsInDist(unittest.TestCase):
    def test_check_deps_in_runtime_manifest(self):
        import build_dist
        self.assertIn("scripts/check_deps.py", build_dist.manifest(),
                      "预检工具必须随运行时包分发——它就是给安装现场用的")


if __name__ == "__main__":
    unittest.main(verbosity=2)
