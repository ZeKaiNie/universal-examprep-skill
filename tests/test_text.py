# -*- coding: utf-8 -*-
import unittest

from coach.text import chinese_numeral, clean, is_mostly_cjk, pack, tokenize


class TokenizeTest(unittest.TestCase):
    def test_ascii_words_lowercased_and_stopwords_dropped(self):
        self.assertEqual(tokenize("The Quick brown fox's tail"), ["quick", "brown", "fox", "tail"])

    def test_cjk_bigrams(self):
        self.assertEqual(tokenize("循环队列"), ["循环", "环队", "队列"])
        self.assertEqual(tokenize("栈"), ["栈"])

    def test_mixed(self):
        self.assertEqual(tokenize("BM25 检索"), ["bm25", "检索"])


class HelpersTest(unittest.TestCase):
    def test_clean_ligatures_and_whitespace(self):
        self.assertEqual(clean("eﬃcient  ﬁle\r\n\r\n\r\n\r\nx"), "efficient file\n\nx")

    def test_is_mostly_cjk(self):
        self.assertTrue(is_mostly_cjk("这是一段中文笔记，讲栈和队列 with LIFO"))
        self.assertFalse(is_mostly_cjk("Mostly English text about stacks 中"))

    def test_chinese_numeral(self):
        self.assertEqual(chinese_numeral("三"), 3)
        self.assertEqual(chinese_numeral("十二"), 12)
        self.assertEqual(chinese_numeral("二十"), 20)
        self.assertEqual(chinese_numeral("7"), 7)
        self.assertIsNone(chinese_numeral("abc"))

    def test_pack_respects_target(self):
        text = "\n\n".join("paragraph %d " % i + "x" * 100 for i in range(20))
        pieces = pack(text, 500)
        self.assertGreater(len(pieces), 3)
        self.assertTrue(all(len(p) <= 700 for p in pieces))
        self.assertEqual("\n\n".join(pieces).count("paragraph"), 20)


if __name__ == "__main__":
    unittest.main()
