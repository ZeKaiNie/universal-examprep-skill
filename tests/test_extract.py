# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest

from coach import extract
from coach.extract import Page
from tests.helpers import make_docx, make_pdf, make_pptx

try:
    import pypdf  # noqa: F401
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_text_form_feed_pages_and_gbk(self):
        with open(self.path("a.txt"), "wb") as fh:
            fh.write("第一页 内容\f第二页".encode("gb18030"))
        pages = extract.read_text(self.path("a.txt"))
        self.assertEqual([p.number for p in pages], [1, 2])
        self.assertEqual(pages[1].text, "第二页")

    def test_docx_headings_pagebreak_table(self):
        make_docx(self.path("n.docx"), [("h1", "Chapter 1 Intro"), "Hello world", "PAGEBREAK",
                                        "Second page", ("table", [["a", "b"], ["c", "d"]])])
        pages = extract.read_docx(self.path("n.docx"))
        self.assertEqual(len(pages), 2)
        self.assertIn("# Chapter 1 Intro", pages[0].text)
        self.assertIn("Hello world", pages[0].text)
        self.assertIn("a | b", pages[1].text)

    def test_pptx_slides_and_notes(self):
        make_pptx(self.path("s.pptx"), [["Title", "point one"], ["Slide two"]], notes={2: "speaker note"})
        pages = extract.read_pptx(self.path("s.pptx"))
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].text, "Title\npoint one")
        self.assertIn("[notes] speaker note", pages[1].text)

    def test_html(self):
        with open(self.path("p.html"), "w", encoding="utf-8") as fh:
            fh.write("<html><head><style>x{}</style></head><body><h2>Topic</h2><p>Body &amp; text</p><script>1</script></body></html>")
        pages = extract.read_html(self.path("p.html"))
        self.assertIn("## Topic", pages[0].text)
        self.assertIn("Body & text", pages[0].text)
        self.assertNotIn("x{}", pages[0].text)

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_pdf_with_pypdf(self):
        make_pdf(self.path("l.pdf"), ["Lecture 2: Sorting", "Merge sort is stable"])
        pages = extract.read_pdf(self.path("l.pdf"))
        self.assertEqual(len(pages), 2)
        self.assertIn("Lecture 2: Sorting", pages[0].text)
        self.assertIn("Merge sort", pages[1].text)

    @unittest.skipIf(HAS_PYPDF, "pypdf installed")
    def test_pdf_without_pypdf_is_reported_not_fatal(self):
        make_pdf(self.path("l.pdf"), ["x"])
        src = extract.extract_source(self.dir, self.path("l.pdf"))
        self.assertEqual(src.error, "pdf_support_missing")

    def test_classify(self):
        self.assertEqual(extract.classify("slides/lec3.pdf"), "lecture")
        self.assertEqual(extract.classify("hw/hw2.pdf"), "homework")
        self.assertEqual(extract.classify("hw2_solutions.pdf"), "solution")
        self.assertEqual(extract.classify("2024 期末试卷.docx"), "exam")
        self.assertEqual(extract.classify("作业3答案.txt"), "solution")
        self.assertEqual(extract.classify("MIT6_006S20_q1.pdf"), "exam")
        self.assertEqual(extract.classify("random.txt", "Problem 1. Prove that"), "homework")
        self.assertEqual(extract.classify("random.txt", "Definitions"), "lecture")

    def test_strip_repeated_headers(self):
        words = ["alpha", "beta", "gamma", "delta"]
        pages = [Page(i, "%d 6.006 Quiz 1 Name\n%s body\nStep 1\n%s tail\n%d" % (i, words[i - 1], words[i - 1], i))
                 for i in range(1, 5)]
        out = extract.strip_repeated_lines(pages)
        # header and page number go; a repeated line in the middle of the page ("Step 1") stays
        self.assertEqual(out[0].text, "alpha body\nStep 1\nalpha tail")
        self.assertEqual(out[3].text, "delta body\nStep 1\ndelta tail")

    def test_scan_skips_workspace_and_hidden(self):
        os.makedirs(self.path("exam-cram"))
        os.makedirs(self.path("sub"))
        with open(self.path("exam-cram/x.md"), "w") as fh:
            fh.write("ignored")
        with open(self.path("sub/lec1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Lecture 1\ncontent")
        with open(self.path("fig2.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        with open(self.path("README.md"), "w") as fh:
            fh.write("about this folder")
        srcs = extract.scan(self.dir)
        self.assertEqual([s.rel for s in srcs], ["fig2.png", "sub/lec1.md"])
        self.assertEqual(srcs[0].kind, "figure")
        self.assertEqual(srcs[1].kind, "lecture")


if __name__ == "__main__":
    unittest.main()
