# -*- coding: utf-8 -*-
"""v4-P6 — dist manifest stays honest: every runtime-referenced script ships, no dev dirs leak,
the zip builds and preserves the ${CLAUDE_SKILL_DIR} layout. Stdlib only."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_dist  # noqa: E402

PY = sys.executable


class Manifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = build_dist.manifest()

    def test_no_dev_surface_leaks(self):
        for f in self.files:
            top = f.split("/")[0]
            self.assertNotIn(top, ("benchmark", "tests", "spike", "assets", ".github", "dist"),
                             f"开发面文件泄进分发包: {f}")
        self.assertNotIn("scripts/build_dist.py", self.files, "打包器自身是开发工具，不进包")

    def test_maintainer_history_does_not_ship(self):
        excluded = ("docs/plans/", "docs/history/", "docs/releases/")
        leaked = [f for f in self.files if f.startswith(excluded)]
        self.assertEqual(leaked, [], "维护者计划/历史/发布文档泄进运行时包: %s" % leaked)
        self.assertFalse(build_dist.is_runtime_path("docs/plans/example.md"))
        self.assertFalse(build_dist.is_runtime_path("docs\\history\\plans\\example.md"))
        self.assertTrue(build_dist.is_runtime_path("docs/language-policy.md"))

    def test_every_skill_referenced_script_ships(self):
        # every scripts/<name>.py referenced from runtime skill texts must be in the manifest
        refs = set()
        pat = re.compile(r"scripts/([\w_]+\.py)")
        scan = ["SKILL.md", "AGENTS.md"]
        for d in ("skills", "locales", "prompts", "docs"):
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, d)):
                for fn in files:
                    if fn.endswith(".md"):
                        rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
                        if build_dist.is_runtime_path(rel):
                            scan.append(rel)
        for rel in scan:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                refs.update(pat.findall(fh.read()))
        refs.discard("build_dist.py")   # dev tool, mentioned only in dev docs if at all
        missing = ["scripts/" + r for r in sorted(refs) if "scripts/" + r not in self.files]
        self.assertEqual(missing, [], "运行时文本引用了清单外的脚本（清单漂移）: %s" % missing)

    def test_core_runtime_files_present(self):
        for f in ("SKILL.md", "AGENTS.md", "LICENSE",
                  "skills/exam-cram/SKILL.md", "locales/zh/SKILL.md", "locales/en/SKILL.md",
                  "locales/zh/messages.json", "locales/en/messages.json",
                  "scripts/update_progress.py", "scripts/notebook.py", "scripts/retrieve.py",
                  "scripts/cheatsheet_render.py", "prompts/web_prompt.md",
                  "docs/language-policy.md", "docs/pdf-capability-adapters.json",
                  "skills/exam-study-guide/SKILL.md", "scripts/exam_start.py",
                  "scripts/readiness.py", "scripts/study_guide_content.py",
                  "scripts/study_guide_document.py", "scripts/study_guide_render.py",
                  "scripts/study_guide_qa.py"):
            self.assertIn(f, self.files, f"核心运行时文件不在清单: {f}")

    def test_manifest_files_exist_on_disk(self):
        gone = [f for f in self.files if not os.path.isfile(os.path.join(ROOT, *f.split("/")))]
        self.assertEqual(gone, [], "清单里有磁盘上不存在的文件: %s" % gone)


class Build(unittest.TestCase):
    def test_zip_builds_and_preserves_layout(self):
        with tempfile.TemporaryDirectory(prefix="dist-") as d:
            out = os.path.join(d, "pkg.zip")
            r = subprocess.run([PY, os.path.join(ROOT, "scripts", "build_dist.py"), "--out", out],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.isfile(out))
            self.assertLess(os.path.getsize(out), 600 * 1024, "运行时包应远小于全仓库源码包")
            with zipfile.ZipFile(out) as z:
                names = set(z.namelist())
                # ${CLAUDE_SKILL_DIR} layout contract: root entry + two-level skills + root dirs
                self.assertIn("SKILL.md", names)
                self.assertIn("skills/exam-tutor/SKILL.md", names)
                self.assertIn("scripts/update_progress.py", names)
                self.assertIn("locales/zh/skills/exam-tutor.md", names)
                bad = z.testzip()
                self.assertIsNone(bad, f"zip 损坏成员: {bad}")

    def test_print_manifest_mode(self):
        r = subprocess.run([PY, os.path.join(ROOT, "scripts", "build_dist.py"), "--print-manifest"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertGreater(len(lines), 40)
        self.assertIn("SKILL.md", lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
