# -*- coding: utf-8 -*-
"""v4-P3 R-slice — scripts/chunk.py: cleaning is conservative, chunks never exceed HARD_MAX,
offsets locate back verbatim, gold-span-in-exactly-one-chunk acceptance on synthetic degenerate
text (the real-PSYC acceptance runs only when the local gitignored workspace exists)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import chunk  # noqa: E402


def degenerate_transcript(n_sent=400):
    """Single-line, no-heading lecture-ish text with scraper CSS at the head (the PSYC shape)."""
    head = "PSYC 110 p { font-size: 14px; } PSYC 110 Introduction: Lecture 3 Transcript "
    sents = []
    for i in range(n_sent):
        lead = "Okay. " if i % 17 == 0 else ""
        sents.append("%sThis is sentence %d about memory encoding and retrieval in lecture three." % (lead, i))
    return head + " ".join(sents)


class Cleaning(unittest.TestCase):
    def test_css_residue_stripped_from_head(self):
        text, _ = chunk.chunk_text(degenerate_transcript(40))
        self.assertNotIn("font-size", text, "头部 CSS 残留必须被清洗")
        self.assertIn("Lecture 3 Transcript", text, "真实内容不得被误删")

    def test_braces_in_course_prose_survive(self):
        src = "## Sets\n" + ("Define the set {x | x > 0} of positive reals. " * 40) + \
              "\n## More\n" + ("Another section about {a, b, c} tuples. " * 40)
        text, chunks = chunk.chunk_text(src)
        self.assertIn("{x | x > 0}", text, "正文里的集合记号绝不能被当 CSS 清掉")
        self.assertTrue(chunks)

    def test_html_tags_stripped(self):
        text, _ = chunk.chunk_text("<div class='x'>Hello</div> <p>world</p> real content here. " * 20)
        self.assertNotIn("<div", text)
        self.assertIn("real content", text)


class Chunking(unittest.TestCase):
    def test_headings_become_chunk_boundaries(self):
        src = "# Lec 6\n\n## Terminology\n" + ("Terms explained. " * 30) + \
              "\n## Tree Navigation\n" + ("Walking the tree. " * 30)
        _, chunks = chunk.chunk_text(src)
        titles = [c["title"] for c in chunks]
        self.assertIn("Terminology", titles)
        self.assertIn("Tree Navigation", titles)

    def test_degenerate_text_chunks_under_hard_max(self):
        _, chunks = chunk.chunk_text(degenerate_transcript())
        self.assertGreater(len(chunks), 5, "长退化文本必须被切成多块")
        for c in chunks:
            self.assertLessEqual(len(c["text"]), chunk.HARD_MAX,
                                 "任何块不得超过 HARD_MAX（计划验收）")

    def test_offsets_locate_back_verbatim(self):
        text, chunks = chunk.chunk_text(degenerate_transcript())
        for c in chunks:
            self.assertEqual(text[c["start"]:c["end"]], c["text"],
                             "偏移必须能在清洗后文本里逐字定位回块内容")

    def test_chunks_cover_text_without_overlap(self):
        text, chunks = chunk.chunk_text(degenerate_transcript())
        pos = 0
        for c in chunks:
            self.assertGreaterEqual(c["start"], pos, "块不得重叠")
            pos = c["end"]
        covered = sum(c["end"] - c["start"] for c in chunks)
        self.assertGreaterEqual(covered / len(text), 0.98, "覆盖率必须≈全文（不静默丢内容）")

    def test_gold_span_lands_in_exactly_one_chunk(self):
        # the plan's acceptance shape: a verbatim quote must be findable inside ONE chunk
        src = degenerate_transcript(200)
        gold = "This is sentence 123 about memory encoding and retrieval in lecture three."
        text, chunks = chunk.chunk_text(src)
        holders = [c for c in chunks if gold in c["text"]]
        self.assertEqual(len(holders), 1, "逐字金标 span 必须完整落在唯一块内（不跨块截断）")

    def test_no_punctuation_run_hard_splits(self):
        _, chunks = chunk.chunk_text("词" * 7000)   # zero sentence enders
        self.assertTrue(all(len(c["text"]) <= chunk.HARD_MAX for c in chunks))
        self.assertGreaterEqual(len(chunks), 3)

    def test_empty_input(self):
        text, chunks = chunk.chunk_text("")
        self.assertEqual((text, chunks), ("", []))


class RealPsycAcceptance(unittest.TestCase):
    """Plan acceptance on the real (gitignored, local-only) PSYC wiki — skips elsewhere/CI."""
    WIKI = os.path.join(ROOT, "benchmark", "skill_workspace", "psyc110_full", "references", "wiki")

    def test_all_20_chapters_slice_under_hard_max(self):
        if not os.path.isdir(self.WIKI):
            self.skipTest("本地无 psyc110_full 工作区（gitignored）——真实验收仅在持有材料的机器上跑")
        import glob
        files = sorted(glob.glob(os.path.join(self.WIKI, "ch*.md")))
        self.assertGreaterEqual(len(files), 20)
        for fp in files:
            with open(fp, encoding="utf-8", errors="replace") as f:
                text, chunks = chunk.chunk_text(f.read())
            self.assertTrue(chunks, fp)
            for c in chunks:
                self.assertLessEqual(len(c["text"]), chunk.HARD_MAX, fp)
            self.assertNotIn("font-size", text, fp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
