# -*- coding: utf-8 -*-
import unittest

from coach.chapters import Chapter
from coach.extract import Page, Source
from coach.questions import extract_questions, pair_key


def src(rel, kind, pages):
    s = Source("/m/" + rel, rel, kind)
    s.pages = [Page(i, t) for i, t in enumerate(pages, 1)]
    return s


def chapter(n, title, text):
    c = Chapter(n, title)
    c.blocks = [("lec%d.md" % n, 1, text)]
    return c


CHAPTERS = [
    chapter(1, "Stacks", "A stack is last in first out (LIFO). push pop top overflow underflow. 栈 后进先出 入栈 出栈 栈顶"),
    chapter(2, "Queues", "A queue is first in first out (FIFO). enqueue dequeue front rear circular. 队列 先进先出 循环队列 front rear"),
]


class QuestionExtractionTest(unittest.TestCase):
    def test_inline_answers_sections_and_types(self):
        text = (
            "一、选择题\n\n1. 栈的特点是\nA. 先进先出\nB. 后进先出\n答案：B\n解析：LIFO\n\n"
            "2. 判断题：队列先进先出\n答案：正确\n\n"
            "二、填空题\n\n1. 循环队列判空条件是____。\n答案：front == rear\n\n"
            "2. 简述 dequeue 的过程。\n"
        )
        items = extract_questions([src("作业1.txt", "homework", [text])], CHAPTERS)
        self.assertEqual([q["id"] for q in items], ["q001", "q002", "q003", "q004"])
        self.assertEqual(items[0]["type"], "choice")
        self.assertEqual(items[0]["options"], ["A. 先进先出", "B. 后进先出"])
        self.assertEqual(items[0]["answer"], "B\n解析：LIFO")
        self.assertEqual(items[1]["type"], "true_false")
        self.assertEqual(items[2]["type"], "fill_blank")
        self.assertEqual(items[2]["answer"], "front == rear")
        self.assertNotIn("二、填空题", items[1]["answer"])
        self.assertIsNone(items[3]["answer"])
        self.assertEqual(items[3]["type"], "subjective")
        self.assertEqual(items[0]["chapter"], 1)
        self.assertEqual(items[2]["chapter"], 2)
        self.assertTrue(items[2]["chapter_guessed"])

    def test_paired_solution_file(self):
        q = src("hw1.txt", "homework", ["Problem 1. [5 points] Is a stack LIFO?\n(a) explain\n\nProblem 2. Define a queue."])
        s = src("hw1_solutions.txt", "solution", ["Problem 1. [5 points] Is a stack LIFO?\n(a) explain\nSolution: Yes, last in first out.\n\nProblem 2. Define a queue.\nSolution: FIFO structure."])
        items = extract_questions([q, s], CHAPTERS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["answer"], "Solution: Yes, last in first out.")
        self.assertEqual(items[0]["answer_source"]["file"], "hw1_solutions.txt")
        self.assertEqual(items[0]["answer_source"]["page"], 1)
        self.assertEqual(items[0]["points"], 5)
        self.assertEqual(items[1]["answer"], "Solution: FIFO structure.")

    def test_pair_key(self):
        self.assertEqual(pair_key("MIT6_006S20_q1_sol.pdf"), pair_key("MIT6_006S20_q1.pdf"))
        self.assertEqual(pair_key("作业2答案.txt"), pair_key("作业2.txt"))
        self.assertNotEqual(pair_key("hw1.pdf"), pair_key("hw2.pdf"))

    def test_sequence_rule_ignores_numbered_lists_inside_answers(self):
        text = "Problem 1. Sort the array.\nSolution: steps\n1. split\n2. merge\n\nProblem 2. Hash the keys."
        items = extract_questions([src("exam.txt", "exam", [text])], CHAPTERS)
        self.assertEqual([q["number"] for q in items], [1, 2])

    def test_explicit_chapter_hint_wins(self):
        text = "1. (Chapter 2) What is a stack?\n答案：LIFO"
        items = extract_questions([src("hw.txt", "homework", [text])], CHAPTERS)
        self.assertEqual(items[0]["chapter"], 2)
        self.assertFalse(items[0]["chapter_guessed"])


if __name__ == "__main__":
    unittest.main()
