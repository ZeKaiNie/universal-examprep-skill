# -*- coding: utf-8 -*-
"""v4-P5 — scripts/cheatsheet_render.py: md-subset rendering, print-CSS invariants
(margin floor / columns / font), page-fit math monotonicity, PDF page-count parser,
and the no-browser degradation contract. No real browser is launched in CI
(EXAMPREP_NO_BROWSER=1)."""
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import cheatsheet_render as cr  # noqa: E402

PY = sys.executable
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)
SIGNATURE_PREFIXED_GARBAGE = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"0" * 40
)

MD = "\n".join([
    "# 《数据结构》考前小抄",
    "## 必背结论",
    "- 链表访问 **O(n)**，顺序表随机访问 `O(1)`",
    "- 归并排序稳定，*快排*不稳定",
    "| 结构 | 查找 | 插入 |",
    "|---|---|---|",
    "| 链表 | O(n) | O(1) |",
    "1. 第一步",
    "2. 第二步",
    "---",
    "见 [ch02 笔记](notebook/ch02.md#q13)。",
])


class MdSubset(unittest.TestCase):
    def test_blocks_render(self):
        h = cr.md_to_html_body(MD)
        for frag in ("<h1>", "<h2>", "<ul>", "<li>", "<strong>O(n)</strong>", "<em>快排</em>",
                     "<code>O(1)</code>", "<table>", "<th>结构</th>", "<td>O(n)</td>",
                     "<ol>", "<hr/>"):
            self.assertIn(frag, h)

    def test_links_flattened_for_print(self):
        h = cr.md_to_html_body(MD)
        self.assertNotIn("<a ", h, "打印面不留活链接")
        self.assertIn('<span class="lnk">ch02 笔记</span>', h)

    def test_html_escaped(self):
        h = cr.md_to_html_body("危险 <script>alert(1)</script> 内容")
        self.assertNotIn("<script>", h)


class LayoutMath(unittest.TestCase):
    def test_margin_floor_enforced(self):
        html = cr.render_html(MD, 9.0, 2, margin_mm=5)
        self.assertIn("margin: %dmm" % cr.MIN_MARGIN_MM, html,
                      "低于 12mm 的边距必须被抬回下限（打印机吞边）")

    def test_font_and_columns_in_css(self):
        html = cr.render_html(MD, 7.0, 3)
        self.assertIn("font: 7.0pt", html)
        self.assertIn("column-count: 3", html)

    def test_pick_font_monotonic(self):
        f1, _ = cr.pick_font(3000, 2)
        f2, _ = cr.pick_font(30000, 2)
        self.assertGreaterEqual(f1, f2, "内容越多字号只能更小")
        self.assertGreaterEqual(f1, cr.FONT_MIN)
        self.assertLessEqual(f1, cr.FONT_MAX)

    def test_pick_font_prefers_largest_fitting(self):
        font, cols = cr.pick_font(100, 2)
        self.assertEqual(font, cr.FONT_MAX, "内容极少时应取最大字号（可读优先）")
        self.assertEqual(cols, 2)

    def test_dense_content_switches_three_columns(self):
        font, cols = cr.pick_font(10 ** 6, 1)
        self.assertEqual(font, cr.FONT_MIN)
        self.assertEqual(cols, 3)


class PdfPageCount(unittest.TestCase):
    def test_counts_page_objects_not_pages_node(self):
        blob = (b"%PDF-1.7\n1 0 obj</Type /Pages /Kids [2 0 R 3 0 R]>\n"
                b"2 0 obj</Type /Page /Parent 1 0 R>\n3 0 obj</Type /Page /Parent 1 0 R>\n")
        p = tempfile.mktemp(suffix=".pdf")
        with open(p, "wb") as f:
            f.write(blob)
        try:
            self.assertEqual(cr.pdf_page_count(p), 2, "/Type /Pages 树节点不算页")
        finally:
            os.unlink(p)


class CliContract(unittest.TestCase):
    def _ws(self):
        ws = tempfile.mkdtemp(prefix="cs_")
        with open(os.path.join(ws, "cheatsheet.md"), "w", encoding="utf-8") as f:
            f.write(MD)
        references = os.path.join(ws, "references")
        os.makedirs(references, exist_ok=True)
        with open(os.path.join(references, "quiz_bank.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        return ws

    def _write_json(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)

    def _write_image(self, ws, name="official.png"):
        path = os.path.join(ws, "references", "assets", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(PNG)
        return path

    def _write_md(self, ws, value):
        with open(os.path.join(ws, "cheatsheet.md"), "w", encoding="utf-8") as f:
            f.write(value)

    def _write_content_units(self, ws, rows):
        path = os.path.join(ws, ".ingest", "content_units.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def _run(self, *extra):
        env = dict(os.environ, EXAMPREP_NO_BROWSER="1")
        return subprocess.run([PY, os.path.join(SCRIPTS, "cheatsheet_render.py")] + list(extra),
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def test_html_only_exit_0(self):
        ws = self._ws()
        try:
            r = self._run("--workspace", ws, "--pages", "2", "--html-only")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(ws, "cheatsheet.html")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_canonical_official_image_is_embedded(self):
        ws = self._ws()
        try:
            self._write_image(ws)
            self._write_json(os.path.join(ws, "references", "quiz_bank.json"), [{
                "id": "official-figure", "chapter": 1,
                "assets": [{"path": "references/assets/official.png",
                            "role": "question_context"}],
            }])
            self._write_md(ws, "# Sheet\n\n![official](references/assets/official.png)\n")
            r = self._run("--workspace", ws, "--pages", "1", "--html-only")
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(ws, "cheatsheet.html"), "r", encoding="utf-8") as f:
                rendered = f.read()
            self.assertIn('src="data:image/png;base64,', rendered)
            self.assertNotIn('src="references/assets/official.png"', rendered)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_signature_prefixed_corrupt_png_fails_without_publication(self):
        ws = self._ws()
        try:
            image_path = self._write_image(ws)
            with open(image_path, "wb") as stream:
                stream.write(SIGNATURE_PREFIXED_GARBAGE)
            self._write_md(ws, "![broken](references/assets/official.png)\n")
            html_path = os.path.join(ws, "cheatsheet.html")
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            for path, payload in ((html_path, b"OLD-HTML"), (pdf_path, b"OLD-PDF")):
                with open(path, "wb") as stream:
                    stream.write(payload)
            r = self._run("--workspace", ws, "--pages", "1", "--html-only")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
            self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_undeclared_hardlink_alias_of_attempt_is_rejected(self):
        ws = self._ws()
        try:
            attempt = self._write_image(ws, "attempt.png")
            alias = os.path.join(ws, "references", "assets", "official-alias.png")
            try:
                os.link(attempt, alias)
            except (OSError, NotImplementedError):
                self.skipTest("hard links unavailable")
            self._write_content_units(ws, [{
                "unit_id": "attempt", "chapter_id": "ch02", "kind": "figure",
                "asset_path": "references/assets/attempt.png",
                "asset_role": "student_attempt", "metadata": {},
            }])
            self._write_md(ws, "![alias](references/assets/official-alias.png)\n")
            r = self._run("--workspace", ws, "--pages", "1", "--html-only")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("student_attempt", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(ws, "cheatsheet.html")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_foreign_chapter_attempt_and_alias_fail_without_publication(self):
        ws = self._ws()
        try:
            self._write_image(ws, "attempt.png")
            self._write_content_units(ws, [{
                "unit_id": "foreign-attempt", "chapter_id": "ch02", "kind": "figure",
                "asset_path": "references\\assets\\attempt.png",
                "asset_role": "student_attempt", "metadata": {},
            }])
            html_path = os.path.join(ws, "cheatsheet.html")
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            for path, payload in ((html_path, b"OLD-HTML"), (pdf_path, b"OLD-PDF")):
                with open(path, "wb") as f:
                    f.write(payload)
            for markdown_path in (
                    "references/assets/attempt.png",
                    "references/assets/./attempt.png"):
                with self.subTest(markdown_path=markdown_path):
                    self._write_md(ws, "![attempt](%s)\n" % markdown_path)
                    r = self._run("--workspace", ws, "--pages", "1", "--html-only")
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
                    self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
                    self.assertFalse(os.path.lexists(os.path.join(
                        ws, ".cheatsheet.rendering.html")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_image_alias_spellings_fail_without_replacing_old_artifacts(self):
        ws = self._ws()
        try:
            self._write_image(ws)
            html_path = os.path.join(ws, "cheatsheet.html")
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            aliases = (
                "references/assets/./official.png",
                "references//assets/official.png",
                "references\\assets\\official.png",
                "references/assets/official.png.",
            )
            for alias in aliases:
                with self.subTest(alias=alias):
                    with open(html_path, "wb") as f:
                        f.write(b"OLD-HTML")
                    with open(pdf_path, "wb") as f:
                        f.write(b"OLD-PDF")
                    self._write_md(ws, "![official](%s)\n" % alias)
                    r = self._run("--workspace", ws, "--pages", "1", "--html-only")
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
                    self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_unsafe_or_conflicting_workspace_policy_blocks_before_publication(self):
        cases = (
            ([{
                "id": "unsafe", "chapter": 1,
                "assets": [{"path": "references/assets/./official.png",
                            "role": "question_context"}],
            }], []),
            ([{
                "id": "official", "chapter": 1,
                "assets": [{"path": "references/assets/official.png",
                            "role": "question_context"}],
            }], [{
                "unit_id": "attempt", "chapter_id": "ch02", "kind": "figure",
                "asset_path": "references\\assets\\official.png",
                "asset_role": "student_attempt", "metadata": {},
            }]),
        )
        for quiz_rows, units in cases:
            with self.subTest(quiz_rows=quiz_rows, units=units):
                ws = self._ws()
                try:
                    self._write_image(ws)
                    self._write_json(
                        os.path.join(ws, "references", "quiz_bank.json"), quiz_rows)
                    if units:
                        self._write_content_units(ws, units)
                    html_path = os.path.join(ws, "cheatsheet.html")
                    pdf_path = os.path.join(ws, "cheatsheet.pdf")
                    with open(html_path, "wb") as f:
                        f.write(b"OLD-HTML")
                    with open(pdf_path, "wb") as f:
                        f.write(b"OLD-PDF")
                    r = self._run("--workspace", ws, "--pages", "1", "--html-only")
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
                    self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
                    self.assertFalse(os.path.lexists(os.path.join(
                        ws, ".cheatsheet.rendering.html")))
                finally:
                    shutil.rmtree(ws, ignore_errors=True)

    def test_asset_policy_drift_blocks_publish_and_preserves_old_outputs(self):
        ws = self._ws()
        try:
            html_path = os.path.join(ws, "cheatsheet.html")
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            with open(html_path, "wb") as f:
                f.write(b"OLD-HTML")
            with open(pdf_path, "wb") as f:
                f.write(b"OLD-PDF")
            initial = cr.workspace_asset_policy_snapshot(ws)
            changed = dict(initial)
            changed["quiz_rows"] = [{"id": "late-change", "chapter": 1}]
            with mock.patch.object(
                    cr, "workspace_asset_policy_snapshot",
                    side_effect=[initial, changed]):
                with self.assertRaisesRegex(SystemExit, "1"):
                    cr.main(["--workspace", ws, "--pages", "1", "--html-only"],
                            _state_locked=True)
            self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
            self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
            self.assertFalse(os.path.lexists(os.path.join(ws, ".cheatsheet.rendering.html")))
            self.assertFalse(os.path.lexists(os.path.join(ws, ".cheatsheet.rendering.pdf")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_public_helpers_reject_caller_supplied_empty_policy(self):
        ws = self._ws()
        try:
            self._write_image(ws, "attempt.png")
            self._write_content_units(ws, [{
                "unit_id": "attempt", "chapter_id": "ch02", "kind": "figure",
                "asset_path": "references/assets/attempt.png",
                "asset_role": "student_attempt", "metadata": {},
            }])
            markdown = "![attempt](references/assets/attempt.png)\n"
            for helper in (
                    lambda: cr.md_to_html_body(
                        markdown, ws, asset_policy={"tainted_keys": ()}),
                    lambda: cr.render_html(
                        markdown, 9.0, 2, ws=ws,
                        asset_policy={"tainted_keys": ()})):
                with self.subTest(helper=helper):
                    with self.assertRaisesRegex(SystemExit, "1"):
                        helper()
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_no_browser_degrades_exit_3_with_html(self):
        ws = self._ws()
        try:
            r = self._run("--workspace", ws, "--pages", "1")
            self.assertEqual(r.returncode, 3, "无浏览器必须走降级码并保留 HTML 产物")
            self.assertIn("no_browser", r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(ws, "cheatsheet.html")))
            self.assertFalse(os.path.isfile(os.path.join(ws, "cheatsheet.pdf")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_html_only_removes_stale_pdf(self):
        ws = self._ws()
        try:
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            with open(pdf_path, "wb") as stream:
                stream.write(b"STALE-PDF")
            r = self._run("--workspace", ws, "--pages", "1", "--html-only")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(ws, "cheatsheet.html")))
            self.assertFalse(os.path.lexists(pdf_path))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_second_artifact_replace_failure_rolls_back_html_and_pdf(self):
        ws = self._ws()
        try:
            html_path = os.path.join(ws, "cheatsheet.html")
            pdf_path = os.path.join(ws, "cheatsheet.pdf")
            with open(html_path, "wb") as stream:
                stream.write(b"OLD-HTML")
            with open(pdf_path, "wb") as stream:
                stream.write(b"OLD-PDF")
            pdf_stage = os.path.join(ws, ".cheatsheet.rendering.pdf")
            real_replace = os.replace

            def fake_print(_browser, _html, output, timeout=120):
                with open(output, "wb") as stream:
                    stream.write(b"%PDF-1.4\n1 0 obj</Type /Page>\n")

            def fail_pdf_publication(source, destination):
                if os.path.abspath(source) == os.path.abspath(pdf_stage):
                    result = real_replace(source, destination)
                    raise MemoryError("injected post-replace failure")
                return real_replace(source, destination)

            with mock.patch.object(cr, "find_browser", return_value="fake-browser"), \
                    mock.patch.object(cr, "print_to_pdf", side_effect=fake_print), \
                    mock.patch.object(cr, "pdf_page_count", return_value=1), \
                    mock.patch.object(cr.os, "replace", side_effect=fail_pdf_publication):
                with self.assertRaisesRegex(SystemExit, "1"):
                    cr.main(["--workspace", ws, "--pages", "1"], _state_locked=True)
            self.assertEqual(open(html_path, "rb").read(), b"OLD-HTML")
            self.assertEqual(open(pdf_path, "rb").read(), b"OLD-PDF")
            self.assertFalse(os.path.lexists(os.path.join(
                ws, ".cheatsheet.rendering.html")))
            self.assertFalse(os.path.lexists(pdf_stage))
            self.assertFalse(any(name.startswith(".cheatsheet.rollback-")
                                 for name in os.listdir(ws)))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_missing_md_dies(self):
        ws = tempfile.mkdtemp(prefix="cs_")
        try:
            r = self._run("--workspace", ws, "--pages", "1")
            self.assertEqual(r.returncode, 2)
            self.assertIn("cheatsheet.md", r.stderr)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_bad_usage(self):
        ws = self._ws()
        try:
            self.assertEqual(self._run("--workspace", ws, "--pages", "0").returncode, 2)
            self.assertEqual(self._run("--workspace", ws, "--pages", "1",
                                       "--font-size", "20").returncode, 2)
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
