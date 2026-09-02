# -*- coding: utf-8 -*-
import unittest

from coach.chapters import build_chapters, number_from_name
from coach.extract import Page, Source


def src(rel, kind, pages):
    s = Source("/m/" + rel, rel, kind)
    s.pages = [Page(i, t) for i, t in enumerate(pages, 1)]
    return s


class NumberFromNameTest(unittest.TestCase):
    def test_variants(self):
        cases = {
            "MIT6_006S20_lec3.pdf": 3, "Lecture 12 - Graphs.pptx": 12, "ch05_hashing.docx": 5,
            "第三章_树.md": 3, "第10讲.pdf": 10, "03-sorting.pdf": 3, "week4.pdf": 4,
            "psyc110_lecture02.md": 2, "march-2024-notes.pdf": None, "intro.pdf": None,
        }
        for name, want in cases.items():
            self.assertEqual(number_from_name(name), want, name)


class BuildChaptersTest(unittest.TestCase):
    def test_one_file_per_chapter_with_titles_and_merge(self):
        sources = [
            src("lec2.pdf", "lecture", ["Instructors: X Lecture 2: Data Structures", "more"]),
            src("lec1.pdf", "lecture", ["Lecture 1: Introduction", "p2"]),
            src("ch2_notes.md", "notes", ["# Extra notes on chapter two"]),
            src("hw1.pdf", "homework", ["Problem 1. x"]),
            src("fig2.png", "figure", []),
        ]
        chs = build_chapters(sources)
        self.assertEqual([c.number for c in chs], [1, 2])
        self.assertEqual(chs[0].title, "Introduction")
        self.assertEqual(chs[1].title, "Data Structures")
        self.assertEqual(chs[1].sources, ["lec2.pdf", "ch2_notes.md"])
        self.assertEqual(chs[1].figures, ["fig2.png"])
        self.assertEqual([b[1] for b in chs[0].blocks], [1, 2])

    def test_split_single_file_on_internal_headings(self):
        text = "教材\n\n第一章 绪论\n\n内容一\n\n第二章 线性表\n\n内容二\n\n第三章 栈\n\n内容三"
        chs = build_chapters([src("数据结构教材.md", "lecture", [text])])
        self.assertEqual([(c.number, c.title) for c in chs], [(1, "绪论"), (2, "线性表"), (3, "栈")])
        self.assertIn("内容二", chs[1].text)
        self.assertNotIn("内容三", chs[1].text)

    def test_numbered_file_is_not_split_on_subsections(self):
        text = "# Lecture 2 - Brain\n\n## Chapter 1. Dualism\n\ntext\n\n## Chapter 2. Materialism\n\nmore"
        chs = build_chapters([src("psyc110_lecture02.md", "lecture", [text])])
        self.assertEqual(len(chs), 1)
        self.assertEqual(chs[0].number, 2)
        self.assertEqual(chs[0].title, "Brain")

    def test_unnumbered_files_appended_in_order(self):
        chs = build_chapters([
            src("zeta.md", "lecture", ["# Zeta topic\n\nbody"]),
            src("lec1.md", "lecture", ["# Lecture 1: A\n\nbody"]),
            src("alpha.md", "lecture", ["Alpha Notes\n\nbody body."]),
        ])
        self.assertEqual([(c.number, c.title) for c in chs], [(1, "A"), (2, "Zeta topic"), (3, "Alpha Notes")])

    def test_empty_and_failed_sources_ignored(self):
        bad = src("lec9.pdf", "lecture", [])
        bad.error = "pdf_support_missing"
        self.assertEqual(build_chapters([bad]), [])


if __name__ == "__main__":
    unittest.main()
