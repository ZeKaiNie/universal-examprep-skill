# -*- coding: utf-8 -*-
"""Textbook-style labels, messy solution file names and scanned homework sheets."""
import unittest

from coach.chapters import Chapter
from coach.extract import Page, Source
from coach.questions import extract_questions, pair_key


def src(rel, kind, pages, scan=()):
    s = Source("/m/" + rel, rel, kind)
    s.pages = [Page(i, t, scan=(i in scan)) for i, t in enumerate(pages, 1)]
    return s


def chapter(n, title, text):
    c = Chapter(n, title)
    c.blocks = [("ch%02d.pdf" % n, 1, text)]
    return c


CHAPTERS = [chapter(1, "Sets", "sets union intersection venn"), chapter(2, "Sequential", "tree diagram sequential")]


class LabelPairingTest(unittest.TestCase):
    def test_pair_key_normalizes_downloads_and_synonyms(self):
        self.assertEqual(pair_key("hw2 (4)(1).pdf"), "hw2")
        self.assertEqual(pair_key("homework2solutions.pdf"), "hw2")
        self.assertEqual(pair_key("hw5-solutions(1).pdf"), "hw5")
        self.assertEqual(pair_key("hw1solution.pdf"), "hw1")
        self.assertEqual(pair_key("Assignment 3 answers.pdf"), "hw3")

    def test_textbook_labels_pair_and_set_chapter(self):
        hw = src("hw1 (4)(1).pdf", "homework", [
            "EEC161 Homework 1\n1. Problem 1.1.2 (see the book)\n2. Problem 1.2.1\n3. Problem 2.1.3",
            "a No N and M are not mutually exclusive because",   # student's scanned sheet (OCR)
            "more handwriting",
        ], scan=(1, 2, 3))
        sol = src("hw1solution.pdf", "solution", [
            "Homework 1 Solutions\nYates and Goodman 3e Solution Set: 1.1.2, 1.2.1, and\n2.1.3\n"
            "Problem 1.1.2 Solution\nBased on the Venn diagram, N and M are not mutually exclusive.\n"
            "Problem 1.2.1 Solution\nThe sample space has four outcomes.\n"
            "Problem 2.1.3 Solution\nUse a tree diagram.",
        ])
        items = extract_questions([hw, sol], CHAPTERS)
        self.assertEqual([q["label"] for q in items], ["1.1.2", "1.2.1", "2.1.3"])
        self.assertEqual(items[0]["answer"], "Based on the Venn diagram, N and M are not mutually exclusive.")
        self.assertEqual(items[2]["answer"], "Use a tree diagram.")
        self.assertEqual([q["chapter"] for q in items], [1, 1, 2])
        self.assertFalse(any(q["chapter_guessed"] for q in items))
        # the handwritten OCR text never leaks into a question
        self.assertNotIn("handwriting", items[2]["question"])
        self.assertNotIn("mutually exclusive because", items[2]["question"])

    def test_dotted_label_is_not_a_list_item(self):
        sol = src("hw9solutions.pdf", "solution", ["7.3.6, 7.4.2, and 9.1.2\nProblem 7.3.6 Solution\nRandom variables X and Y have joint PDF."])
        items = extract_questions([sol], CHAPTERS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["label"], "7.3.6")
        self.assertEqual(items[0]["question"], "Random variables X and Y have joint PDF.")


if __name__ == "__main__":
    unittest.main()
