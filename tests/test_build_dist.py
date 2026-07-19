# -*- coding: utf-8 -*-
"""v4-P6 — dist manifest stays honest: every runtime-referenced script ships, no dev dirs leak,
the zip builds and preserves the ${CLAUDE_SKILL_DIR} layout. Stdlib only."""
import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_dist  # noqa: E402
import exam_start  # noqa: E402

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
        self.assertFalse(build_dist.is_runtime_path("docs/retrieval-evaluation.md"))
        self.assertFalse(build_dist.is_runtime_path("docs/skill-architecture.md"))
        self.assertFalse(build_dist.is_runtime_path("docs/localization.md"))
        self.assertNotIn("scripts/retrieval_evaluation.py", self.files)
        self.assertTrue(build_dist.is_runtime_path("docs/language-policy.md"))

    def test_source_readme_language_mirrors_do_not_inflate_runtime(self):
        reader_docs = (
            "docs/agent-portability.zh.md",
            "docs/exam-audit.zh.md",
            "docs/file-format.en.md",
            "docs/language-policy.zh.md",
            "docs/openai-study-guide-adapter.zh.md",
            "docs/pdf-capability-adapters.en.md",
            "docs/skill-architecture.en.md",
        )
        for rel in reader_docs:
            self.assertFalse(build_dist.is_runtime_path(rel), rel)
            self.assertNotIn(rel, self.files, rel)

    def test_every_skill_referenced_script_ships(self):
        # every scripts/<name>.py referenced from runtime skill texts must be in the manifest
        refs = set()
        pat = re.compile(r"scripts/((?:[\w.-]+/)*[\w.-]+\.py)")
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
                  "scripts/asset_policy.py", "scripts/image_validation.py",
                  "scripts/material_generation.py",
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

    def test_runtime_receipt_manifest_exactly_matches_distribution(self):
        self.assertEqual(
            self.files,
            exam_start._runtime_manifest(ROOT),
            "exam_runtime_receipt 与实际学生分发包不能使用不同文件边界",
        )

    def test_shipped_markdown_links_stay_inside_built_manifest(self):
        shipped = set(self.files)
        broken = []
        link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
        for rel in self.files:
            if not rel.endswith(".md"):
                continue
            with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as stream:
                text = re.sub(r"```.*?```", "", stream.read(), flags=re.DOTALL)
            for raw in link_re.findall(text):
                target = raw.strip().split()[0].strip("<>")
                if (not target or target.startswith(("#", "http://", "https://", "mailto:"))
                        or any(token in target for token in ("{{", "}}", "<", ">"))):
                    continue
                target = target.split("#", 1)[0].split("?", 1)[0]
                if not target:
                    continue
                normalized = os.path.normpath(
                    os.path.join(os.path.dirname(rel), target)
                ).replace("\\", "/")
                absolute = os.path.join(ROOT, *normalized.split("/"))
                if os.path.isfile(absolute):
                    if normalized not in shipped:
                        broken.append("%s -> %s (excluded from package)" % (rel, target))
                elif os.path.isdir(absolute):
                    prefix = normalized.rstrip("/") + "/"
                    if not any(path.startswith(prefix) for path in shipped):
                        broken.append("%s -> %s (empty/excluded directory)" % (rel, target))
                # Missing checkout paths can be intentional student-workspace examples
                # (for example mistakes/ch02.md). Repository dead links are covered by
                # the broader documentation-consistency test; this guard is specifically
                # for checkout targets that build_dist silently excludes.
        self.assertFalse(broken, "运行时 Markdown 含分发包外链接:\n" + "\n".join(broken))


class Build(unittest.TestCase):
    def test_python_comment_stripping_preserves_runtime_syntax_metadata_and_strings(self):
        source = (
            b"#!/usr/bin/env python\n"
            b"# -*- coding: utf-8 -*-\n"
            b'VALUE = "# string data"  # ordinary comment\n'
            b"# another ordinary comment\n"
            b"print(VALUE)\n"
        )
        compact = build_dist._strip_python_comments(source)
        self.assertTrue(compact.startswith(
            b"#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"))
        self.assertIn(b'"# string data"', compact)
        self.assertNotIn(b"ordinary comment", compact)
        compile(compact, "fixture.py", "exec")

    def test_python_compaction_shrinks_docstrings_without_changing_runtime_structure(self):
        source = (
            b'"""module documentation"""\n'
            b"from __future__ import annotations\n"
            b'class Example:\n'
            b'    """class documentation\n    second line"""\n'
            b'    def method(self):\n'
            b'        """method documentation"""; return "runtime string"\n'
        )
        compact = build_dist._strip_python_comments(source)
        self.assertEqual(source.count(b"\n"), compact.count(b"\n"))
        self.assertNotIn(b"module documentation", compact)
        self.assertNotIn(b"class documentation", compact)
        self.assertNotIn(b"method documentation", compact)
        self.assertIn(b"runtime string", compact)
        namespace = {}
        exec(compile(compact, "fixture.py", "exec"), namespace)
        self.assertEqual("", namespace["__doc__"])
        self.assertEqual("", namespace["Example"].__doc__)
        self.assertEqual("", namespace["Example"].method.__doc__)

    def test_python_compaction_preserves_multiline_docstring_closing_line_suffix(self):
        source = (
            b"def value():\n"
            b'    """documentation first line\n'
            b'    documentation second line"""; return 7\n'
        )
        compile(source, "fixture.py", "exec")
        compact = build_dist._strip_python_comments(source)
        self.assertEqual(source.count(b"\n"), compact.count(b"\n"))
        self.assertNotIn(b"documentation", compact)
        namespace = {}
        exec(compile(compact, "fixture.py", "exec"), namespace)
        self.assertEqual("", namespace["value"].__doc__)
        self.assertEqual(7, namespace["value"]())

    def test_python_layout_compaction_preserves_significant_tokens_and_ast(self):
        source = (
            b"#!/usr/bin/env python\n"
            b"# -*- coding: utf-8 -*-\n"
            b"VALUE = (\n"
            b"    1\n"
            b"    + 2\n"
            b")\n"
            b"\n"
            b'def text():\n    return "runtime\\nstring"\n'
        )
        stripped = build_dist._strip_python_comments(source)
        compact = build_dist._compact_python_layout(stripped)
        self.assertLess(len(compact), len(stripped))
        self.assertTrue(compact.startswith(
            b"#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"))

        def significant(data):
            return [
                (token.type, token.string)
                for token in tokenize.tokenize(io.BytesIO(data).readline)
                if token.type != tokenize.NL
            ]

        self.assertEqual(significant(stripped), significant(compact))
        self.assertEqual(
            ast.dump(ast.parse(stripped), include_attributes=False),
            ast.dump(ast.parse(compact), include_attributes=False),
        )

    def test_runtime_bytes_are_cross_checkout_line_ending_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="dist-eol-") as d:
            fixture = os.path.join(d, "fixture.py")
            with open(fixture, "wb") as stream:
                stream.write(b"#!/usr/bin/env python\r\nvalue = 7\r\n")
            original_root = build_dist.ROOT
            try:
                build_dist.ROOT = d
                packed = build_dist._runtime_bytes("fixture.py")
            finally:
                build_dist.ROOT = original_root
        self.assertNotIn(b"\r", packed)
        self.assertTrue(packed.startswith(b"#!/usr/bin/env python\n"))
        namespace = {}
        exec(compile(packed, "fixture.py", "exec"), namespace)
        self.assertEqual(7, namespace["value"])

    def test_zip_builds_and_preserves_layout(self):
        with tempfile.TemporaryDirectory(prefix="dist-") as d:
            out = os.path.join(d, "pkg.zip")
            r = subprocess.run([PY, os.path.join(ROOT, "scripts", "build_dist.py"), "--out", out],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.isfile(out))
            self.assertLessEqual(
                os.path.getsize(out), build_dist.MAX_RUNTIME_ZIP_BYTES,
                "运行时包超过 v4.3 的 850,000 B 硬上限；请排除开发面或重新审计新增运行时能力",
            )
            with zipfile.ZipFile(out) as z:
                names = set(z.namelist())
                # ${CLAUDE_SKILL_DIR} layout contract: root entry + two-level skills + root dirs
                self.assertIn("SKILL.md", names)
                self.assertIn("skills/exam-tutor/SKILL.md", names)
                self.assertIn("scripts/update_progress.py", names)
                self.assertIn("locales/zh/skills/exam-tutor.md", names)
                self.assertIn("docs/file-format.md", names)
                self.assertNotIn("docs/runtime-file-contract.md", names)
                compact_contract = z.read("docs/file-format.md").decode("utf-8")
                self.assertIn("Runtime workspace contract", compact_contract)
                for name in sorted(n for n in names if n.endswith(".py")):
                    compile(z.read(name), name, "exec")
                bad = z.testzip()
                self.assertIsNone(bad, f"zip 损坏成员: {bad}")
                z.extractall(d)
            for script in ("update_progress.py", "validate_workspace.py"):
                run = subprocess.run(
                    [PY, os.path.join(d, "scripts", script), "--help"],
                    cwd=d, capture_output=True, text=True, encoding="utf-8",
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_build_is_byte_deterministic_with_fixed_metadata(self):
        with tempfile.TemporaryDirectory(prefix="dist-deterministic-") as d:
            first = os.path.join(d, "first.zip")
            second = os.path.join(d, "second.zip")
            self.assertEqual(0, build_dist.build(first))
            self.assertEqual(0, build_dist.build(second))
            with open(first, "rb") as stream:
                first_bytes = stream.read()
            with open(second, "rb") as stream:
                self.assertEqual(first_bytes, stream.read())
            with zipfile.ZipFile(first) as archive:
                for info in archive.infolist():
                    self.assertEqual(build_dist._ZIP_TIMESTAMP, info.date_time)
                    self.assertEqual(0o644, (info.external_attr >> 16) & 0o777)

    def test_print_manifest_mode(self):
        r = subprocess.run([PY, os.path.join(ROOT, "scripts", "build_dist.py"), "--print-manifest"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertGreater(len(lines), 40)
        self.assertIn("SKILL.md", lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
