# -*- coding: utf-8 -*-
"""Tests for scripts/build_raw_input_from_workspace.py — the official course-material builder.

All tests are stdlib-only and NEVER import pypdf/pypdfium2/PyMuPDF: the parser core runs on
synthetic page text, and the PDF backend is a fake object injected into run(). This mirrors CI,
where the optional PDF dependencies are not installed.
"""
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import build_raw_input_from_workspace as B  # noqa: E402


# --------------------------------------------------------------------------- helpers

class FakeBackend(object):
    """Stands in for a real pypdf/pypdfium2 backend. `pages_by_file` maps a pdf basename to a
    list of per-page text strings; `can_render` toggles whether page rendering is available."""

    def __init__(self, pages_by_file, can_render=True):
        self._pages = pages_by_file
        self._can_render = can_render
        self.name = "fake"

    def can_text(self):
        return True

    def can_render(self):
        return self._can_render

    def page_texts(self, pdf_path):
        return self._pages.get(os.path.basename(pdf_path), [])

    def render_page_png(self, pdf_path, page_index):
        if not self._can_render:
            return None
        return b"\x89PNG\r\n\x1a\n" + bytes([page_index & 0xFF])  # tiny non-empty fake PNG


def _pages(file, *texts):
    return [{"file": file, "page": i + 1, "text": t} for i, t in enumerate(texts)]


def _materials_with_pdf(basename="ch01.pdf"):
    """A temp materials dir holding one empty .pdf on disk (the fake backend supplies its text)."""
    d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
    os.makedirs(d)
    with open(os.path.join(d, basename), "wb") as f:
        f.write(b"%PDF-1.4 fake")
    return d


def _args(materials, **over):
    out = over.pop("out", os.path.join(materials, "raw_input.json"))
    rep = over.pop("report", os.path.join(materials, "parse_report.json"))
    aroot = over.pop("asset_root", os.path.join(materials, "ws", "references", "assets"))
    argv = ["--materials", materials, "--out", out, "--report", rep]
    if aroot is not None:                       # pass asset_root=None to OMIT --asset-root
        argv += ["--asset-root", aroot]
    for k, v in over.items():
        argv += ["--" + k.replace("_", "-"), v]
    return B.build_arg_parser().parse_args(argv)


# --------------------------------------------------------------------------- pure-core tests

class CoreExtraction(unittest.TestCase):
    def test_ingestion_language_annotation_keeps_mixed_and_formula_only_unknown(self):
        pages = [{
            "file": "mixed.txt", "page": 1,
            "text": "Explain the result，并说明中文条件。",
            "elements": [{
                "kind": "text", "text": "Explain the result，并说明中文条件。"
            }],
        }, {
            "file": "formula.txt", "page": 1, "text": "V=IR",
            "elements": [{"kind": "formula", "text": "V=IR", "latex": "V=IR"}],
        }, {
            "file": "english.txt", "page": 1,
            "text": "The theorem is used in this example.",
            "elements": [{"kind": "heading", "text": "Overview"}],
        }]
        report = {}
        B._annotate_ingestion_languages(pages, report)
        self.assertNotIn("source_language", pages[0])
        self.assertNotIn("source_language", pages[0]["elements"][0])
        self.assertNotIn("source_language", pages[1])
        self.assertNotIn("source_language", pages[1]["elements"][0])
        self.assertEqual("en", pages[2]["source_language"])
        self.assertEqual("en", pages[2]["elements"][0]["source_language"])
        self.assertEqual(2, report["source_language_annotations"]["pages_unresolved"])
        self.assertEqual(2, report["source_language_annotations"]["elements_unresolved"])

    def test_source_location_labels_do_not_claim_every_record_is_a_page(self):
        self.assertEqual(
            "DOCX explicit-break logical segment 2",
            B._source_location_label("notes.docx", 2),
        )
        self.assertEqual("PPTX slide 3", B._source_location_label("deck.pptx", 3))
        self.assertEqual("PDF page 4", B._source_location_label("paper.pdf", 4))
        self.assertEqual("source location 5", B._source_location_label("table.xlsx", 5))

    def test_source_language_classification_is_conservative_and_auditable(self):
        self.assertEqual("en", B._classify_source_language(
            "Determine the current using the circuit shown below."))
        self.assertEqual("zh", B._classify_source_language("计算电路中的电流 I。"))
        self.assertIsNone(B._classify_source_language("V=IR"))
        self.assertIsNone(B._classify_source_language("a=1"))
        self.assertIsNone(B._classify_source_language(
            "Explain the result，并说明中文条件。"))

        items = [{
            "question": "Explain the result.",
            "answer": "The answer is 4.",
        }, {
            "question": "V=IR",
            "answer": "4",
        }]
        report = {"warnings": []}
        B._annotate_source_languages(items, report)
        self.assertEqual("en", items[0]["source_language"])
        self.assertEqual("en", items[0]["answer_source_language"])
        self.assertNotIn("source_language", items[1])
        self.assertTrue(any(w.startswith("source_language_inferred: 2")
                            for w in report["warnings"]))
        self.assertTrue(any(w.startswith("source_language_review_required: 2")
                            for w in report["warnings"]))

    def test_requires_assets_heuristic(self):
        self.assertTrue(B.requires_assets_heuristic("Shade the Venn diagram at right."))
        self.assertTrue(B.requires_assets_heuristic("see the figure / table below"))
        self.assertFalse(B.requires_assets_heuristic("Compute 2 + 2 and simplify."))

    def test_detect_example_problem_and_solution(self):
        pages = _pages("ch01.pdf",
                       "Example 1.1 Problem  Prove the identity.",
                       "Example 1.1 Solution  By induction ...")
        items = B.extract_lecture_items(pages)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["id"], "lecture_example_1_1")
        self.assertEqual(it["source_type"], "example")
        self.assertEqual(it["source_pages"], [1])
        self.assertEqual(it["answer_source_pages"], [2])

    def test_detect_quiz_and_solution(self):
        pages = _pages("ch01.pdf", "Quiz 1.1  State the theorem.", "Quiz 1.1 Solution  It says ...")
        items = B.extract_lecture_items(pages)
        self.assertEqual([it["id"] for it in items], ["lecture_quiz_1_1"])
        self.assertEqual(items[0]["source_type"], "lecture_quiz")
        self.assertEqual(items[0]["answer_source_pages"], [2])

    def test_ungradable_worked_examples_are_teaching_only(self):
        pages = _pages(
            "ch01.pdf",
            "Example 1.1 Problem  Compute the value.",
            "Example 1.1 Solution  The value is 4.",
            "Example 1.2  A completed worked demonstration with result 7.",
            "Quiz 1.1  State the theorem.",
        )
        lecture = B.extract_lecture_items(pages)
        sections = B.group_sections(pages)
        raw = B.build_raw_input("C", sections, lecture)

        # Only assessable problems remain in the canonical bank.  The completed
        # demonstration has no independent solution/key and is teaching-only.
        self.assertEqual(
            [q["id"] for q in raw["quiz_bank"]],
            ["lecture_example_1_1", "lecture_quiz_1_1"],
        )
        # Teaching reachability is an independent snapshot of every Example, including overlap.
        teaching = {e["id"]: e for e in raw["teaching_examples"]}
        self.assertEqual(set(teaching), {"lecture_example_1_1", "lecture_example_1_2"})
        self.assertEqual(teaching["lecture_example_1_1"]["teaching_role"], "paired_problem")
        self.assertEqual(teaching["lecture_example_1_2"]["teaching_role"], "worked_example")
        self.assertEqual(teaching["lecture_example_1_1"]["answer_source_pages"], [2])
        self.assertEqual(teaching["lecture_example_1_2"]["source_pages"], [3])
        self.assertNotIn("gradable", teaching["lecture_example_1_1"])
        self.assertIs(teaching["lecture_example_1_2"]["gradable"], False)
        self.assertNotIn("lecture_quiz_1_1", teaching)

    def test_default_worked_example_role_is_also_teaching_only(self):
        # Compatibility for callers that provide a lecture Example snapshot
        # without the builder's private _teaching_role marker.
        item = {
            "id": "lecture_example_1_8", "chapter": 1, "type": "subjective",
            "question": "Completed demonstration", "answer_status": "unknown",
            "source_file": "ch01.pdf", "source_pages": [8],
        }
        raw = B.build_raw_input("C", [], [item])
        self.assertEqual([], raw["quiz_bank"])
        self.assertEqual("worked_example", raw["teaching_examples"][0]["teaching_role"])
        self.assertIs(raw["teaching_examples"][0]["gradable"], False)

    def test_empty_text_pdf_page_keeps_wiki_anchor_for_visual_repair(self):
        pages = _pages("ch01.pdf", "Chapter 1 prose", "")
        raw = B.build_raw_input("C", B.group_sections(pages), [])
        wiki = raw["phases"][0]["wiki_content"]
        self.assertIn("<!-- ch01.pdf p.2 -->", wiki)
        self.assertIn("保留原页锚点供视觉覆盖核对", wiki)

    def test_explicit_unpaired_example_problem_is_still_a_paired_problem_teaching_role(self):
        pages = _pages("ch01.pdf", "Example 1.9 Problem  Compute x, but the solution page is missing.")
        lecture = B.extract_lecture_items(pages)
        raw = B.build_raw_input("C", B.group_sections(pages), lecture)
        self.assertEqual(raw["teaching_examples"][0]["teaching_role"], "paired_problem")
        self.assertEqual(raw["quiz_bank"][0]["answer_status"], "unknown")

    def test_merges_continued_solution_pages(self):
        pages = _pages("ch01.pdf",
                       "Quiz 1.4  Long one.",
                       "Quiz 1.4 Solution  part 1",
                       "Quiz 1.4 Solution (Continued 2)  part 2")
        items = B.extract_lecture_items(pages)
        self.assertEqual(items[0]["answer_source_pages"], [2, 3])

    def test_stable_ids_and_dedup(self):
        pages = _pages("ch01.pdf",
                       "Quiz 1.1  v1.",
                       "Quiz 1.1  duplicated heading v2.",
                       "Quiz 1.1 Solution  s.")
        ids = [it["id"] for it in B.extract_lecture_items(pages)]
        self.assertEqual(ids, ["lecture_quiz_1_1"])  # deduped by (kind, chapter, num)
        # determinism: same input -> identical output
        again = [it["id"] for it in B.extract_lecture_items(pages)]
        self.assertEqual(ids, again)

    def test_requires_assets_flag_on_venn(self):
        pages = _pages("ch01.pdf",
                       "Quiz 1.1  Shade the corresponding region in the Venn diagram at right.",
                       "Quiz 1.1 Solution  A∩B.")
        it = B.extract_lecture_items(pages)[0]
        self.assertTrue(it["requires_assets"])
        self.assertEqual(it["question_text_status"], "page_reference")
        self.assertEqual(it["type"], "diagram")

    def test_plain_question_is_not_asset_required(self):
        pages = _pages("ch01.pdf", "Example 2.3 Problem  Compute the sum 1+...+n.",
                       "Example 2.3 Solution  n(n+1)/2.")
        it = B.extract_lecture_items(pages)[0]
        self.assertFalse(it["requires_assets"])
        self.assertEqual(it["question_text_status"], "full")

    def test_problem_text_containing_solution_not_misclassified(self):
        # P1 regression: a problem whose text mentions "solution" must stay a problem (not dropped)
        cases = [
            ("Example 4.4 Problem  Find the solution set of the inequality.", "Example 4.4 Solution  x>2."),
            ("Quiz 6.1  Sketch the solution curve.", "Quiz 6.1 Solution  see plot."),
            ("Example 5.2 Problem: write the general solution.", "Example 5.2 Solution  y=Ce^x."),
        ]
        for prob, sol in cases:
            items = B.extract_lecture_items([{"file": "ch.pdf", "page": 1, "text": prob},
                                             {"file": "ch.pdf", "page": 2, "text": sol}])
            self.assertEqual(len(items), 1, "dropped pair for: %r -> %r" % (prob, items))
            self.assertEqual(items[0]["answer_source_pages"], [2])

    def test_orphan_solution_keys_detected(self):
        pages = _pages("ch.pdf", "Example 9.9 Solution  answer with no problem page.")
        self.assertIn(("example", 9, 9), B.orphan_solution_keys(pages))

    def test_solution_before_problem_still_paired(self):
        # P2 regression: solution page preceding its problem must still be claimed
        pages = _pages("ch.pdf", "Example 1.1 Solution  ans here.", "Example 1.1 Problem  the question.")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["answer_source_pages"], [1])
        self.assertNotIn("answer_status", it)

    def test_continued_solution_after_intervening_problem(self):
        # P2 regression: a continued solution page after a different problem is not lost
        pages = _pages("ch.pdf",
                       "Example 1.1 Problem  q1.",
                       "Example 1.1 Solution  part1.",
                       "Example 1.2 Problem  q2.",
                       "Example 1.1 Solution (Continued)  part2.")
        items = {it["id"]: it for it in B.extract_lecture_items(pages)}
        self.assertEqual(items["lecture_example_1_1"]["answer_source_pages"], [2, 4])

    def test_heuristic_excludes_known_false_positives(self):
        for t in ("See the table of contents on page 2.", "Just figure it out yourself.",
                  "The graph theory chapter is hard."):
            self.assertFalse(B.requires_assets_heuristic(t), "false positive: %r" % t)

    def test_heuristic_scoped_to_problem_slice(self):
        # round-3 P2: a Venn mention in a LATER problem on the same page must not flag THIS plain problem.
        # (markers are line-anchored, so each Quiz heading starts its own line — as in real slide text)
        pages = [{"file": "ch01.pdf", "page": 1,
                  "text": "Quiz 1.1  Compute 2+2.\nQuiz 1.2  Shade the Venn diagram at right."},
                 {"file": "ch01.pdf", "page": 2,
                  "text": "Quiz 1.1 Solution  4.\nQuiz 1.2 Solution  the region."}]
        items = {it["id"]: it for it in B.extract_lecture_items(pages)}
        self.assertFalse(items["lecture_quiz_1_1"]["requires_assets"])  # plain → not asset-required
        self.assertTrue(items["lecture_quiz_1_2"]["requires_assets"])   # Venn slice → asset-required

    def test_subdir_asset_names_distinct(self):
        # round-3 P2: same-named files in different subdirs must not collide on the same page
        a = B._safe_asset_name("lecture/ch01.pdf", 12, "lecture_quiz_1_1")
        b = B._safe_asset_name("solutions/ch01.pdf", 12, "lecture_quiz_1_1")
        self.assertNotEqual(a, b)
        self.assertIn("lecture", a)
        self.assertIn("solutions", b)

    # ---- round-4 hardening ----
    def test_inline_mention_is_not_a_marker(self):
        # round-4 P2: prose "See Example 1.1" / a TOC entry must not be mistaken for a lecture heading
        pages = _pages("ch01.pdf", "Please review the proof. See Example 1.1 in the textbook for details.")
        self.assertEqual(B.extract_lecture_items(pages), [])

    def test_problem_statement_picks_problem_not_solution(self):
        # round-4 P2: solution-before-problem on one page → slice the PROBLEM, not the earlier solution
        text = "Example 1.1 Solution  the answer is 42.\nExample 1.1 Problem  what is the answer?"
        stmt = B._problem_statement(text, "example", 1, 1)
        self.assertIn("what is the answer", stmt)
        self.assertNotIn("answer is 42", stmt)

    def test_same_marker_in_two_files_namespaced(self):
        # round-4 P2: Quiz 1.1 in two files → two distinct items, each paired with its OWN solution
        pages = [{"file": "lecture/ch01.pdf", "page": 1, "text": "Quiz 1.1  Compute A."},
                 {"file": "lecture/ch01.pdf", "page": 2, "text": "Quiz 1.1 Solution  A is 1."},
                 {"file": "homework/ch01.pdf", "page": 1, "text": "Quiz 1.1  Compute B."},
                 {"file": "homework/ch01.pdf", "page": 2, "text": "Quiz 1.1 Solution  B is 2."}]
        items = B.extract_lecture_items(pages)
        self.assertEqual(len(items), 2)                      # both kept (not deduped away)
        self.assertEqual(len({it["id"] for it in items}), 2)  # distinct namespaced ids
        by_file = {it["source_file"]: it for it in items}
        self.assertIn("A is 1", by_file["lecture/ch01.pdf"]["answer"])   # paired with own file's solution
        self.assertIn("B is 2", by_file["homework/ch01.pdf"]["answer"])

    def test_marker_only_question_is_page_reference(self):
        # round-4 P2: only a heading extracted (prompt is in an image) → page_reference, not an
        # unanswerable "full" title
        pages = _pages("ch01.pdf", "Quiz 1.1", "Quiz 1.1 Solution  see the figure.")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["question_text_status"], "page_reference")
        self.assertFalse(it.get("requires_assets"))

    # ---- round-4 (P0B r4) hardening ----
    def test_continued_problem_pages_merged(self):
        pages = _pages("ch01.pdf", "Example 1.1 Problem  Prove part one.",
                       "Example 1.1 Problem (Continued)  and also part two.",
                       "Example 1.1 Solution  done.")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["source_pages"], [1, 2])         # both problem pages kept, not just the first

    def test_ambiguous_ids_are_injective(self):
        # a/b.pdf and a_b.pdf sanitize to the same stem → ids must still be distinct
        pages = [{"file": "a/b.pdf", "page": 1, "text": "Quiz 1.1  q1."},
                 {"file": "a_b.pdf", "page": 1, "text": "Quiz 1.1  q2."}]
        ids = [it["id"] for it in B.extract_lecture_items(pages)]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))            # no duplicate quiz_bank ids

    def test_section_uses_first_marker_on_mixed_page(self):
        # boundary page: Quiz 1.9 appears before Example 2.1 → chapter 1, not 2
        pages = [{"file": "ch.pdf", "page": 1,
                  "text": "Quiz 1.9  last of ch1.\nExample 2.1 Problem  first of ch2."}]
        self.assertEqual(B.group_sections(pages)[0]["chapter"], 1)

    def test_pre_problem_solution_kept_with_continuation(self):
        # solution part-1 BEFORE the problem + a later (Continued) part → BOTH kept
        pages = _pages("ch.pdf", "Example 1.1 Solution  part one.",
                       "Example 1.1 Problem  the question.",
                       "Example 1.1 Solution (Continued)  part two.")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["answer_source_pages"], [1, 3])  # not just the continuation page

    # ---- round-5 (P0B r5) hardening ----
    def test_markdown_heading_marker_detected(self):
        # '## Quiz 1.1' (a Markdown heading in .md materials) must match the anchored prefix
        pages = _pages("ch01.md", "## Quiz 1.1 Problem  State it.", "## Quiz 1.1 Solution  Answer.")
        ids = [it["id"] for it in B.extract_lecture_items(pages)]
        self.assertIn("lecture_quiz_1_1", ids)

    def test_continued_before_solution_is_solution(self):
        # 'Example 1.1 (Continued) Solution ...' is a SOLUTION continuation, not a problem
        ms = B.detect_lecture_markers("Example 1.1 (Continued) Solution  more steps.")
        self.assertEqual(ms[0]["role"], "solution")
        self.assertTrue(ms[0]["continued"])

    def test_shown_below_and_tree_are_asset_cues(self):
        self.assertTrue(B.requires_assets_heuristic("Given the tree shown below, find the leaves."))
        self.assertTrue(B.requires_assets_heuristic("Draw the circuit."))
        self.assertFalse(B.requires_assets_heuristic("Compute 2 + 2."))  # still no false positive

    # ---- P0D: prune leftover workspace dirs from the materials scan ----
    def test_scan_prunes_leftover_workspace_dirs(self):
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        os.makedirs(os.path.join(d, "references", "wiki"))
        os.makedirs(os.path.join(d, "scratch", "extracted"))
        with open(os.path.join(d, "references", "wiki", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("## Quiz 1.1 Problem leftover\n## Quiz 1.1 Solution x")
        with open(os.path.join(d, "scratch", "extracted", "ch01.txt"), "w", encoding="utf-8") as f:
            f.write("Quiz 9.9 leftover scratch")
        with open(os.path.join(d, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        self.assertEqual([os.path.basename(p) for p in pdfs], ["ch01.pdf"])  # only the real PDF
        self.assertEqual(texts, [])                                          # leftover .md/.txt skipped
        self.assertIn("references", pruned)
        self.assertIn("scratch", pruned)

    def test_leftover_workspace_not_ingested(self):
        # P0D end-to-end: a prior workspace's markers must not enter the bank; the real PDF's do
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        os.makedirs(os.path.join(d, "references", "wiki"))
        with open(os.path.join(d, "references", "wiki", "ch01.md"), "w", encoding="utf-8") as f:
            f.write("## Quiz 9.9 Problem leftover\n## Quiz 9.9 Solution x")
        with open(os.path.join(d, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Real question.", "Quiz 1.1 Solution  real."]})
        code, ri, report = B.run(_args(d), backend=be)
        self.assertEqual(code, 0)
        ids = [q["id"] for q in ri["quiz_bank"]]
        self.assertIn("lecture_quiz_1_1", ids)        # from the real PDF
        self.assertNotIn("lecture_quiz_9_9", ids)     # leftover .md item NOT ingested
        self.assertTrue(any("pruned_non_material" in w for w in report["warnings"]))

    # ---- round-6 (P0B r6) hardening ----
    def test_toc_entry_not_extracted(self):
        # 'Example 1.1 Counting subsets ....... 12' is a table-of-contents line, not a heading
        self.assertEqual(B.extract_lecture_items(_pages("ch01.pdf", "Example 1.1 Counting subsets ........ 12")), [])
        # ...but a 3-dot ellipsis in a REAL prompt must NOT be mistaken for TOC dot-leaders
        self.assertEqual(len(B.detect_lecture_markers("Example 2.3 Problem  Compute 1+2+...+n.")), 1)

    def test_problem_statement_skips_continued_solution_marker(self):
        text = "Example 1.1 (Continued) Solution  ans part two.\nExample 1.1 Problem  the real question?"
        stmt = B._problem_statement(text, "example", 1, 1)
        self.assertIn("the real question", stmt)
        self.assertNotIn("ans part two", stmt)

    def test_solution_statement_handles_continued_before_solution(self):
        sol = B._solution_statement("Example 1.1 (Continued) Solution  the worked answer.", "example", 1, 1)
        self.assertIn("worked answer", sol)

    def test_cjk_short_prompt_not_marker_only(self):
        pages = _pages("ch01.pdf", "Example 1.1 求导", "Example 1.1 Solution  答案")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["question_text_status"], "full")   # 求导 is a real (terse) prompt
        self.assertIn("求导", it["question"])

    def test_ambiguous_key_does_not_claim_shared_solution(self):
        # Quiz 1.1 problem in two files + a separate solutions-only file → don't mis-assign the solution
        pages = [{"file": "a.pdf", "page": 1, "text": "Quiz 1.1  q in a."},
                 {"file": "b.pdf", "page": 1, "text": "Quiz 1.1  q in b."},
                 {"file": "sol.pdf", "page": 1, "text": "Quiz 1.1 Solution  shared sol."}]
        items = B.extract_lecture_items(pages)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertNotIn("answer_source_pages", it)     # neither claims the ambiguous shared solution
            self.assertEqual(it.get("answer_status"), "unknown")

    def test_legitimate_references_dir_not_pruned(self):
        # a course 'references/' of real PDFs (no wiki/assets signature) must NOT be pruned
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        os.makedirs(os.path.join(d, "references"))
        with open(os.path.join(d, "references", "ch02.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        with open(os.path.join(d, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        self.assertEqual(sorted(os.path.basename(p) for p in pdfs), ["ch01.pdf", "ch02.pdf"])
        self.assertEqual(pruned, [])

    # ---- round-7 (P0B r7) hardening ----
    def test_unparenthesized_continued_solution_is_solution(self):
        # 'Continued Solution' / 'Continued: Solution' (no parens) must still classify as a solution
        for tail in ("Example 1.1 Continued Solution  ans.", "Example 1.1 Continued: Solution  ans."):
            ms = B.detect_lecture_markers(tail)
            self.assertEqual(ms[0]["role"], "solution", tail)
            self.assertTrue(ms[0]["continued"])

    def test_references_assets_pdfs_not_pruned(self):
        # a course storing PDFs under references/assets/ (no references/wiki) must NOT be pruned
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        os.makedirs(os.path.join(d, "references", "assets"))
        with open(os.path.join(d, "references", "assets", "fig.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        self.assertIn("fig.pdf", [os.path.basename(p) for p in pdfs])
        self.assertEqual(pruned, [])

    def test_generated_progress_files_skipped(self):
        # study_plan.md / study_progress.md at the materials root are workspace files, not material
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        for fn in ("study_plan.md", "study_progress.md", "lecture_notes.md"):
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write("Quiz 1.1  x\nQuiz 1.1 Solution  y")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        names = sorted(os.path.basename(p) for p in texts)
        self.assertEqual(names, ["lecture_notes.md"])   # real notes kept, generated files skipped

    def test_generated_e2e_audit_is_not_ingested_as_course_material(self):
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        names = (
            "universal-examprep-e2e-audit-2026-07-14.md",
            "universal-examprep-e2e-audit.md",
            "audit.md",
            "lecture_notes.md",
        )
        for name in names:
            with open(os.path.join(d, name), "w", encoding="utf-8") as stream:
                stream.write("course-like text")
        _pdfs, texts, pruned, _others = B._scan_materials(d)
        kept = sorted(os.path.basename(path) for path in texts)
        self.assertEqual(kept, ["audit.md", "lecture_notes.md"])
        self.assertEqual(
            sorted(pruned),
            ["universal-examprep-e2e-audit-2026-07-14.md",
             "universal-examprep-e2e-audit.md"],
        )

    # ---- round-8 (P0B r8) hardening ----
    def test_txt_drawing_prompt_not_requires_assets(self):
        # a .txt source can't hide an image → 'Draw the graph' stays text-complete (full), not asset-required
        it = B.extract_lecture_items(_pages("notes.txt", "Quiz 1.1  Draw the graph of y=x^2.",
                                            "Quiz 1.1 Solution  parabola."))[0]
        self.assertFalse(it["requires_assets"])
        self.assertEqual(it["question_text_status"], "full")
        # the SAME prompt from a .pdf IS asset-flagged (renderable)
        it2 = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  Draw the graph of y=x^2.",
                                             "Quiz 1.1 Solution  parabola."))[0]
        self.assertTrue(it2["requires_assets"])

    def test_long_title_toc_entry_skipped(self):
        # TOC dot-leaders may sit far past the 48-char tail (long title) → scan the whole heading line
        long = "Example 1.1 A very long descriptive section title that exceeds forty eight characters ...... 12"
        self.assertEqual(B.detect_lecture_markers(long), [])

    def test_continued_problem_page_sliced_at_next_marker(self):
        # a continued problem page that also starts the next item must not append the next item's text
        pages = _pages("ch01.pdf", "Quiz 1.1 Problem  first part.",
                       "Quiz 1.1 Problem (Continued)  second part.\nQuiz 1.2 Problem  unrelated next.")
        it = next(q for q in B.extract_lecture_items(pages) if q["id"] == "lecture_quiz_1_1")
        self.assertIn("second part", it["question"])
        self.assertNotIn("unrelated next", it["question"])

    def test_skip_files_only_at_root(self):
        # study_plan.md skipped at the materials ROOT but KEPT in a subfolder (could be a real file)
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        os.makedirs(os.path.join(d, "lectures"))
        with open(os.path.join(d, "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(d, "lectures", "study_plan.md"), "w", encoding="utf-8") as f:
            f.write("y")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        rels = sorted(os.path.relpath(t, d).replace(os.sep, "/") for t in texts)
        self.assertEqual(rels, ["lectures/study_plan.md"])   # root skipped, subfolder kept

    # ---- round-9 (P0B r9) hardening ----
    def test_plural_solution_heading_recognized(self):
        # 'Quiz 1.1 Solutions' (plural) is a solution → the preceding problem isn't lost/mis-paired
        self.assertEqual(B.detect_lecture_markers("Quiz 1.1 Solutions  x")[0]["role"], "solution")
        it = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  the question.",
                                            "Quiz 1.1 Solutions  the answers."))[0]
        self.assertEqual(it["answer_source_pages"], [2])

    def test_chapter_carried_forward_for_unmarked_pages(self):
        # a deck with no chNN filename: an unmarked page after 'Example 2.1' stays in chapter 2, not 1
        pages = [{"file": "lecture.pdf", "page": 1, "text": "Example 2.1 Problem  q."},
                 {"file": "lecture.pdf", "page": 2, "text": "ordinary chapter-2 prose, no marker."}]
        secs = {s["chapter"]: s for s in B.group_sections(pages)}
        self.assertIn(2, secs)
        self.assertEqual(secs[2]["pages"], [1, 2])   # both pages in ch 2
        self.assertNotIn(1, secs)                     # page 2 NOT dumped into ch 1

    def test_problem_statement_skips_toc_line(self):
        # a TOC 'Quiz 1.1 ...... 12' before the real 'Quiz 1.1 Problem' → slice the REAL one
        text = "Quiz 1.1 Intro to sets ........ 12\nQuiz 1.1 Problem  the real prompt here."
        self.assertIn("the real prompt", B._problem_statement(text, "quiz", 1, 1))

    def test_same_page_solution_continuation_captured(self):
        # two solution slices on one page → BOTH captured (not just the first)
        text = "Quiz 1.1 Solution  part one.\nQuiz 1.1 Solution (Continued)  part two."
        sol = B._solution_statement(text, "quiz", 1, 1)
        self.assertIn("part one", sol)
        self.assertIn("part two", sol)

    # ---- round-10 (P0B r10) hardening ----
    def test_nested_workspace_dir_fully_pruned(self):
        # a prior run's output (skill_workspace/ with references/wiki + study_progress.md) nested under
        # --materials must be pruned WHOLE — its study_progress.md must not leak in as a .md material
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        ws = os.path.join(d, "skill_workspace")
        os.makedirs(os.path.join(ws, "references", "wiki"))
        for p in (os.path.join(ws, "study_progress.md"), os.path.join(ws, "references", "wiki", "ch1.md")):
            with open(p, "w", encoding="utf-8") as f:
                f.write("Quiz 9.9  leftover")
        with open(os.path.join(d, "ch01.pdf"), "wb") as f:
            f.write(b"%PDF fake")
        pdfs, texts, pruned, _others = B._scan_materials(d)
        self.assertEqual(texts, [])                              # nothing from skill_workspace/
        self.assertEqual([os.path.basename(p) for p in pdfs], ["ch01.pdf"])
        self.assertIn("skill_workspace", pruned)

    def test_same_page_continued_problem_captured(self):
        text = "Quiz 1.1 Problem  part one.\nQuiz 1.1 Problem (Continued)  part two of the prompt."
        stmt = B._problem_statement(text, "quiz", 1, 1)
        self.assertIn("part one", stmt)
        self.assertIn("part two", stmt)

    def test_numeric_only_footer_is_marker_only(self):
        # 'Quiz 1.1\n12' (heading + slide number) → marker_only/page_reference, not an unanswerable full title
        it = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1\n12", "Quiz 1.1 Solution  ans"))[0]
        self.assertEqual(it["question_text_status"], "page_reference")
        # ...but a heading + a real letter/CJK prompt stays full
        it2 = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  Define a set.",
                                             "Quiz 1.1 Solution  ans"))[0]
        self.assertEqual(it2["question_text_status"], "full")

    # ---- round-11 (P0B r11) hardening: figure-shown vs figure-noun cue split ----
    def test_txt_figure_shown_cue_is_asset_required(self):
        # a .txt 'Shade the Venn at right' references a SHOWN figure → fail-closed (STRONG cue, any source)
        it = B.extract_lecture_items(_pages("notes.txt", "Quiz 1.1  Shade the Venn diagram at right.",
                                            "Quiz 1.1 Solution  region A."))[0]
        self.assertTrue(it["requires_assets"])
        # ...but a .txt 'Draw the graph' (WEAK figure-noun, possibly produce-prompt) stays text-complete
        it2 = B.extract_lecture_items(_pages("notes.txt", "Quiz 1.2  Draw the graph of y=x^2.",
                                             "Quiz 1.2 Solution  parabola."))[0]
        self.assertFalse(it2["requires_assets"])

    def test_image_chart_below_cues(self):
        for t in ("Use the image below.", "Read the chart below.", "See the picture below."):
            self.assertTrue(B.requires_assets_heuristic(t), t)

    def test_symbolic_prompt_not_marker_only(self):
        it = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  2+2=?", "Quiz 1.1 Solution  4"))[0]
        self.assertEqual(it["question_text_status"], "full")     # operators = a real prompt

    def test_figure_item_keeps_extracted_answer(self):
        # a figure-dependent item with an extracted text solution keeps the TEXT answer (for grading)
        it = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  Shade the Venn at right.",
                                            "Quiz 1.1 Solution  the shaded region is A and B."))[0]
        self.assertTrue(it["requires_assets"])
        self.assertIn("shaded region", it["answer"])             # not just a page-reference string

    # ---- round-12 (P0B r12) hardening ----
    def test_answer_heading_recognized_as_solution(self):
        # a worked page labeled 'Answer'/'Answers' (not 'Solution') is a solution, not a new problem
        self.assertEqual(B.detect_lecture_markers("Quiz 1.1 Answers  the worked answer")[0]["role"], "solution")
        it = B.extract_lecture_items(_pages("ch01.pdf", "Quiz 1.1  the question.",
                                            "Quiz 1.1 Answer  the answer."))[0]
        self.assertEqual(it["answer_source_pages"], [2])
        self.assertNotEqual(it.get("answer_status"), "unknown")

    def test_continued_page_asset_scoped_to_this_item(self):
        # a continued page that also starts the next item must not lend THAT item's Venn to this one
        pages = _pages("ch01.pdf", "Quiz 1.1 Problem  plain text part one.",
                       "Quiz 1.1 Problem (Continued)  still plain.\nQuiz 1.2 Problem  Shade the Venn at right.")
        items = {it["id"]: it for it in B.extract_lecture_items(pages)}
        self.assertFalse(items["lecture_quiz_1_1"]["requires_assets"])  # Quiz 1.1 stays plain
        self.assertTrue(items["lecture_quiz_1_2"]["requires_assets"])   # Quiz 1.2 owns the Venn

    def test_section_grouping_from_headings(self):
        pages = (_pages("a.pdf", "Quiz 1.1  x") + _pages("b.pdf", "Example 2.1 Problem  y"))
        secs = B.group_sections(pages)
        self.assertEqual([s["chapter"] for s in secs], [1, 2])

    def test_section_grouping_from_filename_when_no_heading(self):
        pages = _pages("ch03_notes.pdf", "no markers here, just prose")
        self.assertEqual(B.group_sections(pages)[0]["chapter"], 3)

    def test_raw_phase_order_is_separate_from_only_chapter_five(self):
        sections = B.group_sections(_pages("ch05_notes.pdf", "chapter five prose"))
        phase = B.build_raw_input("C", sections, [])["phases"][0]
        self.assertEqual(phase["phase_num"], 1)
        self.assertEqual(phase["phase_id"], "phase01")
        self.assertEqual(phase["chapter"], 5)
        self.assertEqual(phase["chapter_id"], "ch05")
        self.assertEqual(phase["wiki_filename"], "ch05.md")

    def test_raw_noncontiguous_chapters_keep_true_identity(self):
        sections = B.group_sections(
            _pages("ch02_notes.pdf", "chapter two prose")
            + _pages("ch07_notes.pdf", "chapter seven prose")
        )
        phases = B.build_raw_input("C", sections, [])["phases"]
        self.assertEqual([p["phase_num"] for p in phases], [1, 2])
        self.assertEqual([p["phase_id"] for p in phases], ["phase01", "phase02"])
        self.assertEqual([p["chapter"] for p in phases], [2, 7])
        self.assertEqual([p["chapter_id"] for p in phases], ["ch02", "ch07"])

    def test_homework_items_not_dropped(self):
        hw = [{"id": "hw_1", "type": "subjective", "question": "q", "answer": "a", "source": "material"}]
        lec = [{"id": "lecture_quiz_1_1", "type": "diagram", "question": "q", "source": "material"}]
        ri = B.build_raw_input("C", [{"chapter": 1, "files": ["ch01.pdf"], "pages": [1], "text_blocks": ["t"]}],
                               lec, homework_items=hw)
        ids = [q["id"] for q in ri["quiz_bank"]]
        self.assertIn("hw_1", ids)
        self.assertIn("lecture_quiz_1_1", ids)

    # ---- Codex round-1 hardening (P1 + P2) ----
    def test_full_item_carries_real_problem_text(self):
        # P1: a text-complete item's question is the ACTUAL problem text, not a "see the page" pointer
        pages = _pages("ch01.pdf", "Example 2.3 Problem  Compute the sum 1+2+...+n.",
                       "Example 2.3 Solution  n(n+1)/2.")
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["question_text_status"], "full")
        self.assertIn("Compute the sum", it["question"])
        self.assertNotIn("见原始讲义", it["question"])

    def test_full_item_answer_carries_real_solution_text(self):
        # round-2 P1: a text-complete item's answer is the EXTRACTED solution, not a page pointer
        pages = _pages("ch01.pdf", "Example 2.3 Problem  Compute the sum.",
                       "Example 2.3 Solution  The result is n(n+1)/2.")
        it = B.extract_lecture_items(pages)[0]
        self.assertIn("n(n+1)/2", it["answer"])
        self.assertNotIn("见原始讲义", it["answer"])

    def test_multi_file_answer_source_pages_only_first_file(self):
        # round-2 P2: don't claim another file's page number under answer_source_file
        pages = [{"file": "a.pdf", "page": 1, "text": "Quiz 1.1  q."},
                 {"file": "a.pdf", "page": 2, "text": "Quiz 1.1 Solution  part1."},
                 {"file": "b.pdf", "page": 1, "text": "Quiz 1.1 Solution (Continued)  part2."}]
        it = B.extract_lecture_items(pages)[0]
        self.assertEqual(it["answer_source_file"], "b.pdf")     # first by (page,file): (b.pdf,1)
        self.assertEqual(it["answer_source_pages"], [1])        # ONLY b.pdf's page, not [1,2]
        self.assertEqual({f for f, p in it["_answer_pages"]}, {"a.pdf", "b.pdf"})  # both still rendered

    def test_rel_keeps_subdir_same_named_files_distinct(self):
        # P2: same-named PDFs in different subdirs get distinct file ids (no page_pdf collision)
        base = os.path.join("x", "mats")
        a = B._rel(os.path.join(base, "lecture", "ch01.pdf"), base)
        b = B._rel(os.path.join(base, "homework", "ch01.pdf"), base)
        self.assertEqual(a, "lecture/ch01.pdf")
        self.assertNotEqual(a, b)

    def test_internal_render_keys_stripped_from_bank(self):
        ri = B.build_raw_input("C", [{"chapter": 1, "files": ["a"], "pages": [1], "text_blocks": ["t"]}],
                               [{"id": "lecture_quiz_1_1", "type": "diagram", "question": "q",
                                 "_question_pages": [("a", 1)], "_answer_pages": [("a", 2)]}])
        item = ri["quiz_bank"][0]
        self.assertNotIn("_question_pages", item)
        self.assertNotIn("_answer_pages", item)

    def test_pypdfium2_render_requires_pillow(self):
        from pdf_capabilities import PDF_RENDER_CANDIDATES
        pdfium = next(row for row in PDF_RENDER_CANDIDATES if row[0] == "pypdfium2")
        self.assertEqual(pdfium[1], ("pypdfium2", "PIL"))

    def test_pymupdf_backend_extracts_text_pages(self):
        closed = []

        class Page(object):
            def __init__(self, text):
                self.text = text

            def get_text(self, mode):
                self.assert_mode = mode
                return self.text

        class Document(list):
            def close(self):
                closed.append(True)

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.open = lambda _path: Document([Page("page one"), Page("page two")])
        with mock.patch.dict(sys.modules, {"fitz": fake_fitz}):
            texts = B.RealBackend(text_lib="pymupdf").page_texts("fake.pdf")
        self.assertEqual(texts, ["page one", "page two"])
        self.assertEqual(closed, [True])

    def test_pdf_text_backend_falls_back_per_document(self):
        fake_pypdf = types.ModuleType("pypdf")
        fake_pypdf.PdfReader = mock.Mock(side_effect=ValueError("pypdf cannot parse this file"))

        class Page(object):
            def get_text(self, _mode):
                return "Recovered by PyMuPDF"

        class Document(list):
            def close(self):
                pass

        fake_fitz = types.ModuleType("fitz")
        fake_fitz.open = lambda _path: Document([Page()])
        with mock.patch.dict(sys.modules, {"pypdf": fake_pypdf, "fitz": fake_fitz}):
            backend = B.RealBackend(text_lib=["pypdf", "pymupdf"])
            texts = backend.page_texts("mixed.pdf")
        self.assertEqual(["Recovered by PyMuPDF"], texts)
        self.assertEqual(["pymupdf"], backend.last_text_methods)

    def test_detect_backend_uses_fitz_for_text_when_pypdf_is_missing(self):
        fake_fitz = types.ModuleType("fitz")
        with mock.patch.dict(sys.modules, {"pypdf": None, "fitz": fake_fitz}):
            backend = B.detect_backend()
        self.assertEqual(backend.text_lib, "pymupdf")
        self.assertEqual(backend.render_lib, "pymupdf")
        self.assertTrue(backend.can_text())
        self.assertTrue(backend.can_render())


# --------------------------------------------------------------------------- CLI / run() tests

class CliAndRun(unittest.TestCase):
    def test_txt_docx_and_pdf_prose_units_keep_source_language(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="language-prose-"), "materials")
        os.makedirs(materials)
        with open(os.path.join(materials, "ch01_notes.txt"), "w", encoding="utf-8") as stream:
            stream.write("Chapter 1\nThe theorem is used in this text example.")
        with open(os.path.join(materials, "ch01_notes.docx"), "wb") as stream:
            stream.write(b"synthetic package; extractor is injected")
        with open(os.path.join(materials, "ch01_notes.pdf"), "wb") as stream:
            stream.write(b"%PDF-1.4 synthetic")
        docx = [{
            "file": "ch01_notes.docx", "page": 1,
            "text": "Chapter 1\nThe theorem is used in this document example.",
            "elements": [{
                "kind": "heading", "text": "Chapter 1", "ordinal": 0, "bbox": None,
            }, {
                "kind": "text", "text": "The theorem is used in this document example.",
                "ordinal": 1, "bbox": None,
            }],
            "embedded_assets": [], "review_signals": [],
        }]
        backend = FakeBackend({
            "ch01_notes.pdf": ["Chapter 1\nThe theorem is used in this PDF example."],
        }, can_render=False)
        with mock.patch.object(B, "extract_ooxml", return_value=docx):
            code, payload, unused_report = B.run(
                _args(materials, render_pages="never"), backend=backend)
        self.assertEqual(0, code)
        units = payload["ingestion"]["content_units"]
        for source_file in ("ch01_notes.txt", "ch01_notes.docx", "ch01_notes.pdf"):
            prose = [row for row in units
                     if row["source_file"] == source_file and row["kind"] == "text"]
            self.assertTrue(prose, source_file)
            self.assertTrue(all(
                row["metadata"].get("source_language") == "en" for row in prose
            ), source_file)

    def test_programmatic_optional_adapter_runner_remains_injectable(self):
        materials = _materials_with_pdf()

        def runner(request):
            self.assertEqual("ch01.pdf", request.source_file)
            return {
                "pages": [{
                    "file": "ch01.pdf",
                    "page": 1,
                    "text": "Chapter 1\nGrounded adapter content.",
                    "elements": [{
                        "kind": "text",
                        "text": "Chapter 1\nGrounded adapter content.",
                        "ordinal": 0,
                        "bbox": None,
                        "method": "native",
                        "confidence": 1.0,
                    }],
                    "embedded_assets": [],
                    "review_signals": [],
                }],
                "discovered_page_count": 1,
                "warnings": [],
            }

        code, payload, unused_report = B.run(
            _args(materials, ingest_adapter="docling", render_pages="never"),
            backend=B.NoBackend(),
            adapter_runner=runner,
        )
        self.assertEqual(0, code)
        receipt = payload["ingestion"]["parser_receipts"][0]
        self.assertEqual("docling", receipt["adapter"])
        self.assertEqual(1, receipt["discovered_page_count"])

    def test_docx_review_signal_uses_explicit_break_segment_locator(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="docx-location-"), "materials")
        os.makedirs(materials)
        with open(os.path.join(materials, "notes.docx"), "wb") as stream:
            stream.write(b"synthetic package; extractor is injected")
        extracted = [{
            "file": "notes.docx",
            "page": 1,
            "text": "Chapter 1\nGrounded content",
            "elements": [],
            "embedded_assets": [],
            "review_signals": [{
                "reason_code": "docx_visual_check",
                "detail": "floating object may need review",
            }],
        }]
        with mock.patch.object(B, "extract_ooxml", return_value=extracted):
            code, unused_payload, report = B.run(_args(materials), backend=B.NoBackend())
        self.assertEqual(0, code)
        warning = next(value for value in report["warnings"]
                       if value.startswith("docx_visual_check:"))
        review = next(value for value in report["ai_review"]
                      if value.get("kind") == "docx_visual_check")
        self.assertIn("DOCX explicit-break logical segment 1", warning)
        self.assertIn("DOCX explicit-break logical segment 1", review["action"])
        self.assertNotIn("notes.docx p.1", warning)

    def test_gold_xlsx_and_standalone_raster_use_dedicated_ingestion_paths(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="gold-materials-"), "materials")
        os.makedirs(materials)
        fixtures = os.path.join(ROOT, "tests", "fixtures", "ingestion_gold")
        for name in ("workbook.xlsx", "scan.png"):
            shutil.copyfile(os.path.join(fixtures, name), os.path.join(materials, name))

        args = _args(materials)
        code, payload, report = B.run(args, backend=B.NoBackend())
        self.assertEqual(0, code)
        units = payload["ingestion"]["content_units"]

        workbook_units = [row for row in units if row["source_file"] == "workbook.xlsx"]
        formula = next(row for row in workbook_units if row["kind"] == "formula")
        self.assertEqual("=2+3", formula["text"])
        self.assertEqual("B2", formula["metadata"]["parser_metadata"]["coordinate"])
        table = next(row for row in workbook_units
                     if row["kind"] == "table" and "Cell\tValue" in row["text"])
        self.assertIn("A2\tLayout", table["text"])

        raster = next(row for row in units
                      if row["source_file"] == "scan.png" and row["kind"] == "figure")
        self.assertEqual("source_page", raster["asset_role"])
        self.assertEqual([0.0, 0.0, 16.0, 12.0], raster["bbox"])
        self.assertTrue(os.path.isfile(os.path.join(
            materials, "ws", *raster["asset_path"].split("/"))))
        self.assertTrue(any(row.get("kind") == "standalone_raster_needs_ocr"
                            for row in report["ai_review"]))

        receipts = {row["source_file"]: row
                    for row in payload["ingestion"]["parser_receipts"]}
        self.assertEqual("stdlib:xlsx", receipts["workbook.xlsx"]["module"])
        self.assertEqual("stdlib:raster", receipts["scan.png"]["module"])
        self.assertEqual(1, receipts["workbook.xlsx"]["discovered_page_count"])
        self.assertEqual(1, receipts["scan.png"]["discovered_page_count"])
        self.assertEqual({"network": False, "upload": False, "install": False},
                         receipts["scan.png"]["policy"])

        from ingestion.pipeline import persist_payload
        compiled = os.path.join(materials, "compiled")
        os.makedirs(compiled)
        manifest = persist_payload(
            compiled, payload["ingestion"]
        )
        self.assertEqual(2, manifest["source_count"])
        chapter_issues = [
            row for row in payload["ingestion"]["review_candidates"]
            if row["reason_codes"] == ["chapter_unassigned"]
        ]
        self.assertEqual(
            len({row["source_file"] for row in chapter_issues}),
            len(chapter_issues),
        )
        self.assertEqual(2, len(chapter_issues))
        raster_ocr_issues = [
            row for row in payload["ingestion"]["review_candidates"]
            if "standalone_raster_needs_ocr" in row["reason_codes"]
        ]
        self.assertEqual(1, len(raster_ocr_issues))
        self.assertEqual("scan.png", raster_ocr_issues[0]["source_file"])

    def test_raster_sidecar_is_linked_as_first_class_source_not_image_text(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="sidecar-materials-"), "materials")
        os.makedirs(materials)
        fixtures = os.path.join(ROOT, "tests", "fixtures", "ingestion_gold")
        image = os.path.join(materials, "ch01_diagram.png")
        shutil.copyfile(os.path.join(fixtures, "scan.png"), image)
        with open(os.path.join(materials, "ch01_diagram.ocr.txt"), "w", encoding="utf-8") as stream:
            stream.write("Exact OCR transcript from explicit sidecar.")
        with open(os.path.join(materials, "ch01_diagram.md"), "w", encoding="utf-8") as stream:
            stream.write("Unrelated same-stem lecture note.")

        code, payload, unused_report = B.run(_args(materials), backend=B.NoBackend())
        self.assertEqual(0, code)
        units = payload["ingestion"]["content_units"]
        anchor = next(
            row for row in units
            if row["source_file"] == "ch01_diagram.png" and row["kind"] == "page_anchor"
        )
        self.assertEqual(
            "ch01_diagram.ocr.txt",
            anchor["metadata"]["parser_metadata"]["sidecar"]["source_file"],
        )
        image_units = [row for row in units if row["source_file"] == "ch01_diagram.png"]
        self.assertFalse(any("Exact OCR transcript" in row["text"] for row in image_units))
        self.assertTrue(any(
            row["source_file"] == "ch01_diagram.ocr.txt"
            and "Exact OCR transcript" in row["text"]
            for row in units
        ))
        self.assertTrue(any(
            row["source_file"] == "ch01_diagram.md"
            and "Unrelated same-stem" in row["text"]
            for row in units
        ))
        receipts = {
            row["source_file"]: row for row in payload["ingestion"]["parser_receipts"]
        }
        self.assertIn("ch01_diagram.ocr.txt", receipts)
        self.assertIn("ch01_diagram.md", receipts)

    def test_builder_binds_xlsx_parse_to_initial_source_snapshot(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="xlsx-aba-"), "materials")
        os.makedirs(materials)
        fixture = os.path.join(ROOT, "tests", "fixtures", "ingestion_gold", "workbook.xlsx")
        workbook = os.path.join(materials, "ch01_workbook.xlsx")
        shutil.copyfile(fixture, workbook)
        with open(workbook, "rb") as stream:
            original = stream.read()
        expected = hashlib.sha256(original).hexdigest()
        real_extract = B.extract_xlsx
        observed = {}

        def swap_then_extract(path, source_file, **kwargs):
            observed["expected_sha256"] = kwargs.get("expected_sha256")
            try:
                with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("custom/revision-b.txt", b"revision B")
                return real_extract(path, source_file, **kwargs)
            finally:
                with open(path, "wb") as stream:
                    stream.write(original)

        with mock.patch.object(B, "extract_xlsx", side_effect=swap_then_extract):
            code, unused_payload, report = B.run(_args(materials), backend=B.NoBackend())
        self.assertEqual(expected, observed["expected_sha256"])
        self.assertEqual(0, code)
        self.assertTrue(any(
            row.get("file") == "ch01_workbook.xlsx"
            and "expected_sha256" in row.get("why", "")
            for row in report["skipped"]
        ))

    def test_failed_supported_source_persists_as_inspectable_blocked_workspace(self):
        materials = os.path.join(tempfile.mkdtemp(prefix="failed-raster-"), "materials")
        os.makedirs(materials)
        source = os.path.join(materials, "ch01_bad.png")
        with open(source, "wb") as stream:
            stream.write(b"not a raster image")

        code, payload, report = B.run(_args(materials), backend=B.NoBackend())
        self.assertEqual(0, code)
        self.assertTrue(any(
            row.get("file") == "ch01_bad.png" for row in report["skipped"]
        ))
        receipt = next(
            row for row in payload["ingestion"]["parser_receipts"]
            if row["source_file"] == "ch01_bad.png"
        )
        source_record = next(
            row for row in payload["ingestion"]["sources"]
            if row["path"] == "ch01_bad.png"
        )
        self.assertEqual("failed", receipt["status"])
        self.assertEqual([], receipt["produced_pages"])
        self.assertEqual("failed", source_record["status"])
        self.assertTrue(any(
            row["source_file"] == "ch01_bad.png" and row["severity"] == "blocking"
            for row in payload["ingestion"]["review_candidates"]
        ))

        from ingestion.pipeline import persist_payload
        workspace = os.path.join(materials, "compiled")
        os.makedirs(workspace)
        manifest = persist_payload(workspace, payload["ingestion"])
        self.assertEqual(1, manifest["source_count"])
        self.assertTrue(os.path.isfile(os.path.join(
            workspace, ".ingest", "parser_receipts.json"
        )))

    def test_multiframe_rasters_persist_only_as_failed_blocked_sources(self):
        from tests.test_raster_adapter import (
            _animated_gif, _animated_webp, _apng, _multi_page_tiff,
        )

        materials = os.path.join(tempfile.mkdtemp(prefix="multiframe-raster-"), "materials")
        os.makedirs(materials)
        fixtures = {
            "ch01_animated.gif": _animated_gif(),
            "ch01_animated.webp": _animated_webp(),
            "ch01_animated.png": _apng(),
            "ch01_multipage.tiff": _multi_page_tiff(),
        }
        for filename, raster_payload in fixtures.items():
            with open(os.path.join(materials, filename), "wb") as stream:
                stream.write(raster_payload)

        code, payload, report = B.run(_args(materials), backend=B.NoBackend())
        self.assertEqual(0, code)
        ingestion = payload["ingestion"]
        self.assertEqual([], ingestion["content_units"])
        self.assertEqual(set(fixtures), {
            row["source_file"] for row in ingestion["parser_receipts"]
            if row["status"] == "failed" and not row["produced_pages"]
        })
        self.assertEqual(set(fixtures), {
            row["path"] for row in ingestion["sources"] if row["status"] == "failed"
        })
        self.assertEqual(set(fixtures), {
            row["source_file"] for row in ingestion["review_candidates"]
            if row["severity"] == "blocking"
        })
        self.assertEqual(set(fixtures), {
            row["file"] for row in report["skipped"]
        })

        from ingestion.pipeline import persist_payload
        workspace = os.path.join(materials, "compiled")
        os.makedirs(workspace)
        manifest = persist_payload(workspace, ingestion)
        self.assertEqual(len(fixtures), manifest["source_count"])
        self.assertEqual(0, manifest["unit_count"])

    def test_cli_help_without_pdf_deps(self):
        # the script emits UTF-8 (it reconfigures stdout); decode UTF-8 explicitly so the test is
        # independent of the OS console locale (cp1252 on CI / gbk on a zh Windows box).
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "build_raw_input_from_workspace.py"), "--help"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0)
        self.assertIn("materials", r.stdout)
        self.assertNotIn("--ingest-adapter", r.stdout)

    def test_missing_pdf_backend_clear_error(self):
        d = _materials_with_pdf()
        code, payload, report = B.run(_args(d), backend=B.NoBackend())
        self.assertEqual(code, 3)
        self.assertIn("pypdf", payload["error"])
        self.assertIn("no_pdf_text_backend", report["warnings"])

    def test_unrenderable_answer_does_not_block_question(self):
        # round-8: a valid PDF question figure + an unrenderable answer page → the answer asset is NOT
        # declared (so the otherwise-valid question isn't fail-closed); only the question figure is kept
        d = _materials_with_pdf()

        class QOnly(FakeBackend):
            def render_page_png(self, pdf_path, page_index):   # page 2 (index 1, the solution) can't render
                return None if page_index == 1 else FakeBackend.render_page_png(self, pdf_path, page_index)

        be = QOnly({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.", "Quiz 1.1 Solution  region A."]})
        code, ri, report = B.run(_args(d), backend=be)
        it = next(q for q in ri["quiz_bank"] if q["id"] == "lecture_quiz_1_1")
        roles = [a["role"] for a in it["assets"]]
        self.assertIn("question_context", roles)       # question figure declared (it rendered)
        self.assertNotIn("answer_context", roles)       # answer figure NOT declared (it couldn't render)
        self.assertTrue(any("answer_image_unavailable" in w for w in report["warnings"]))

    def test_txt_materials_work_without_backend(self):
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        with open(os.path.join(d, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("Example 1.1 Problem  hi\n")
        code, ri, report = B.run(_args(d), backend=B.NoBackend())  # no PDFs -> stdlib path works
        self.assertEqual(code, 0)
        self.assertGreaterEqual(report["pages_extracted"], 1)

    def test_render_required_without_backend_errors(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s"]},
                         can_render=False)
        code, payload, _ = B.run(_args(d, render_pages="required"), backend=be)
        self.assertEqual(code, 3)
        self.assertIn("pypdfium2", payload["error"])

    def test_emits_asset_metadata_when_rendered(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.",
                                       "Quiz 1.1 Solution  A∩B."]})
        args = _args(d)
        code, ri, report = B.run(args, backend=be)
        self.assertEqual(code, 0)
        item = next(q for q in ri["quiz_bank"] if q["id"] == "lecture_quiz_1_1")
        self.assertTrue(item["requires_assets"])
        qside = [a for a in item["assets"] if a["role"] == "question_context"]
        self.assertTrue(qside)
        # the rendered PNG actually exists on disk under the asset root
        png = os.path.join(args.asset_root, os.path.basename(qside[0]["path"]))
        self.assertTrue(os.path.isfile(png))
        self.assertGreaterEqual(report["pages_rendered"], 1)

    def test_teaching_snapshot_keeps_rendered_assets_and_report_metrics(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": [
            "Example 1.1 Problem  Shade the Venn diagram at right.",
            "Example 1.1 Solution  Shade region A.",
            "Example 1.2  This table demonstrates the completed calculation.",
        ]})
        args = _args(d)
        code, raw, report = B.run(args, backend=be)
        self.assertEqual(code, 0)
        bank = {q["id"]: q for q in raw["quiz_bank"]}
        teaching = {e["id"]: e for e in raw["teaching_examples"]}
        self.assertEqual(set(teaching), {"lecture_example_1_1", "lecture_example_1_2"})
        self.assertNotIn("lecture_example_1_2", bank)
        self.assertIs(teaching["lecture_example_1_2"]["gradable"], False)
        self.assertEqual(teaching["lecture_example_1_1"]["assets"],
                         bank["lecture_example_1_1"]["assets"])
        self.assertEqual(report["teaching_examples_detected"], 2)
        self.assertEqual(report["teaching_example_roles"],
                         {"paired_problem": 1, "worked_example": 1})

    def test_warns_when_asset_required_but_no_render(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s"]},
                         can_render=False)
        code, ri, report = B.run(_args(d, render_pages="auto"), backend=be)
        self.assertEqual(code, 0)
        self.assertTrue(any("likely_asset_required_but_no_image" in w for w in report["warnings"]))

    def test_empty_materials_fails(self):
        d = tempfile.mkdtemp(prefix="mat-")                  # no parseable files at all
        code, payload, report = B.run(_args(d), backend=B.NoBackend())
        self.assertEqual(code, 4)
        self.assertIn("no_material_files", report["warnings"])

    def test_scanned_pdf_blank_pages_build_blocked_review_payload(self):
        # Process success is separate from readiness: image-only pages remain accounted for,
        # rendered as review evidence when possible, and carry blocking typed review work.
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["", "", ""]})         # 3 blank pages
        code, payload, report = B.run(_args(d), backend=be)
        self.assertEqual(code, 0)
        self.assertIn("no_text_extracted", report["warnings"])
        self.assertTrue(any("pdf_no_text" in w for w in report["warnings"]))
        anchors = [row for row in payload["ingestion"]["content_units"]
                   if row["kind"] == "page_anchor"]
        self.assertEqual([1, 2, 3], sorted(row["page"] for row in anchors))
        source_assets = [row for row in payload["ingestion"]["content_units"]
                         if row["asset_role"] == "source_page"]
        self.assertEqual(3, len(source_assets))
        self.assertTrue(any(row["severity"] == "blocking"
                            for row in payload["ingestion"]["review_candidates"]))

    def test_render_required_fails_for_marker_only_image_prompt(self):
        # round-11: a marker-only image prompt (heading + no text) whose page can't render also fails
        # `--render-pages required` (its prompt is in an image we can't produce)
        d = _materials_with_pdf()

        class NullRender(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                return None

        be = NullRender({"ch01.pdf": ["Quiz 1.1", "Quiz 1.1 Solution  ans"]})   # heading only → marker_only
        code, payload, report = B.run(_args(d, render_pages="required"), backend=be)
        self.assertEqual(code, 3)

    def test_render_required_without_asset_root_errors(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        code, payload, _ = B.run(_args(d, render_pages="required", asset_root=None), backend=be)
        self.assertEqual(code, 2)
        self.assertIn("asset-root", payload["error"])

    def test_render_auto_without_asset_root_warns_and_skips(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        code, ri, report = B.run(_args(d, asset_root=None), backend=be)   # render auto, no --asset-root
        self.assertEqual(code, 0)
        self.assertTrue(any("asset_root_not_set" in w for w in report["warnings"]))
        self.assertEqual(report["pages_rendered"], 0)                     # nothing written to a wrong place

    def test_render_required_fails_when_asset_page_unrenderable(self):
        # round-2 P2: render=required must ERROR (not just warn) if a required figure can't be produced.
        # The asset-required item must come from a PDF (round-8: .txt items are never requires_assets);
        # here the render backend returns None for the page so the figure can't be produced.
        d = _materials_with_pdf()

        class NullRender(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                return None

        be = NullRender({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        code, payload, report = B.run(_args(d, render_pages="required"), backend=be)
        self.assertEqual(code, 3)
        self.assertIn("必需页图未能渲染", payload["error"])

    def test_render_failure_on_one_page_does_not_crash(self):
        # round-3 P2: a backend that throws on a page must be caught + reported, not crash the CLI
        d = _materials_with_pdf()

        class Boom(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                raise RuntimeError("bad page")

        be = Boom({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        code, ri, report = B.run(_args(d), backend=be)            # render auto
        self.assertEqual(code, 0)                                 # did not crash
        self.assertTrue(any("渲染失败" in s.get("why", "") for s in report["skipped"]))

    def test_marker_only_renders_page_when_backend_available(self):
        # round-4 P2: a marker-only item (prompt in image) should still get its page rendered so it's
        # displayable — even though requires_assets stays false
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1", "Quiz 1.1 Solution  see fig."]})  # heading only
        args = _args(d)
        code, ri, report = B.run(args, backend=be)
        it = next(q for q in ri["quiz_bank"] if q["id"] == "lecture_quiz_1_1")
        self.assertEqual(it["question_text_status"], "page_reference")
        self.assertFalse(it["requires_assets"])                   # soft, not hard-required
        qside = [a for a in it["assets"] if a["role"] == "question_context"]
        self.assertTrue(qside)                                    # but the page WAS rendered
        self.assertTrue(os.path.isfile(os.path.join(args.asset_root, os.path.basename(qside[0]["path"]))))

    def test_renders_all_continued_answer_pages(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.4  Shade the Venn diagram at right.",
                                       "Quiz 1.4 Solution  part1.",
                                       "Quiz 1.4 Solution (Continued)  part2."]})
        args = _args(d)
        code, ri, report = B.run(args, backend=be)
        item = next(q for q in ri["quiz_bank"] if q["id"] == "lecture_quiz_1_4")
        sol = [a for a in item["assets"] if a["role"] == "answer_context"]
        self.assertEqual(len(sol), 2)                        # BOTH solution pages get an asset...
        for a in sol:                                        # ...and both are rendered to disk
            self.assertTrue(os.path.isfile(os.path.join(args.asset_root, os.path.basename(a["path"]))))

    def test_answer_spanning_multiple_files_warns(self):
        d = os.path.join(tempfile.mkdtemp(prefix="mat-"), "materials")
        os.makedirs(d)
        for fn in ("a.pdf", "b.pdf"):
            with open(os.path.join(d, fn), "wb") as f:
                f.write(b"%PDF fake")
        be = FakeBackend({"a.pdf": ["Quiz 1.1  q.", "Quiz 1.1 Solution  part1."],
                          "b.pdf": ["Quiz 1.1 Solution (Continued)  part2."]})
        code, ri, report = B.run(_args(d), backend=be)
        self.assertTrue(any("answer_spans_multiple_files" in w for w in report["warnings"]))


# --------------------------------------------------------------------------- ingest integration

class PublicationLocking(unittest.TestCase):
    def test_official_workspace_main_publishes_under_new_ingestion_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.abspath(temp)
            materials = os.path.join(root, "materials")
            workspace = os.path.join(root, "workspace")
            os.makedirs(materials)
            os.makedirs(workspace)
            with open(os.path.join(materials, "ch01.txt"), "w", encoding="utf-8") as stream:
                stream.write("Chapter 1\nA source-backed concept.\n")
            out = os.path.join(workspace, ".ingest", "source_raw_input.json")
            report = os.path.join(workspace, ".ingest", "parse_report.json")
            assets = os.path.join(workspace, "references", "assets")

            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", assets,
                "--render-pages", "never",
            ], backend=B.NoBackend())

            self.assertEqual(0, code)
            with open(out, encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)
            with open(report, encoding="utf-8") as stream:
                self.assertIsInstance(json.load(stream), dict)
            self.assertTrue(os.path.isfile(
                os.path.join(workspace, ".ingest", "mutation.lock")
            ))

    def test_official_workspace_conflict_publishes_no_assets_or_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.abspath(temp)
            materials = os.path.join(root, "materials")
            workspace = os.path.join(root, "workspace")
            os.makedirs(materials)
            os.makedirs(workspace)
            with open(os.path.join(materials, "ch01.txt"), "w", encoding="utf-8") as stream:
                stream.write("Chapter 1\nA source-backed concept.\n")
            out = os.path.join(workspace, ".ingest", "source_raw_input.json")
            report = os.path.join(workspace, ".ingest", "parse_report.json")
            assets = os.path.join(workspace, "references", "assets")

            with B.workspace_publication_lock(workspace):
                code = B.main([
                    "--materials", materials,
                    "--out", out,
                    "--report", report,
                    "--asset-root", assets,
                    "--render-pages", "never",
                ], backend=B.NoBackend())

            self.assertEqual(7, code)
            self.assertFalse(os.path.exists(out))
            self.assertFalse(os.path.exists(report))
            self.assertFalse(os.path.exists(
                os.path.join(workspace, ".ingest", "ai_review_manifest.json")
            ))
            self.assertFalse(os.path.exists(assets))


def _ingest(raw_input_path, out_dir):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "ingest.py"),
                           "-i", raw_input_path, "-o", out_dir],
                          capture_output=True, text=True, encoding="utf-8")


def _validate(ws):
    spec = importlib.util.spec_from_file_location("vw", os.path.join(SCRIPTS, "validate_workspace.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class IngestIntegration(unittest.TestCase):
    def test_generated_raw_input_accepted_by_ingest(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Example 1.1 Problem  compute.", "Example 1.1 Solution  ok."]})
        args = _args(d)
        code, ri, _ = B.run(args, backend=be)
        self.assertEqual(code, 0)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.join(d, "ws")
        r = _ingest(args.out, ws)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(ws, "references", "quiz_bank.json")))

    def test_asset_fields_survive_into_quiz_bank(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        args = _args(d)  # asset-root points into ws/references/assets so renders land in the workspace
        args.asset_root = os.path.join(d, "ws", "references", "assets")
        code, ri, _ = B.run(args, backend=be)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.join(d, "ws")
        self.assertEqual(_ingest(args.out, ws).returncode, 0)
        with open(os.path.join(ws, "references", "quiz_bank.json"), encoding="utf-8") as f:
            qb = json.load(f)
        item = next(q for q in qb if q["id"] == "lecture_quiz_1_1")
        for k in ("source_file", "source_pages", "assets", "requires_assets", "question_text_status"):
            self.assertIn(k, item)
        # and the generated workspace validates clean (the rendered asset exists on disk)
        V = _validate(ws)
        self.assertEqual(V._exit_code(V.validate(ws)[0]), 0)

    def test_validator_catches_missing_asset_from_generated_item(self):
        # render unavailable -> requires_assets item has an asset path but no file -> validator errors
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s."]},
                         can_render=False)
        args = _args(d, render_pages="auto")
        args.asset_root = os.path.join(d, "ws", "references", "assets")
        code, ri, _ = B.run(args, backend=be)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.join(d, "ws")
        self.assertEqual(_ingest(args.out, ws).returncode, 0)
        V = _validate(ws)
        errors = V.validate(ws)[0]
        self.assertEqual(V._exit_code(errors), 1)  # missing required asset -> fail-closed

    def test_old_handauthored_raw_input_still_works(self):
        d = tempfile.mkdtemp(prefix="old-")
        ri = {"course_name": "Old", "phases": [{"phase_num": 1, "phase_name": "P1",
              "wiki_filename": "ch1.md", "wiki_content": "# c"}],
              "quiz_bank": [{"id": "q1", "chapter": 1, "type": "choice", "question": "?",
                             "options": ["A", "B"], "answer": "A", "source": "material"}]}
        p = os.path.join(d, "raw_input.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ri, f)
        ws = os.path.join(d, "ws")
        self.assertEqual(_ingest(p, ws).returncode, 0)


class Hygiene(unittest.TestCase):
    def test_no_required_pdf_dependencies(self):
        with open(os.path.join(SCRIPTS, "build_raw_input_from_workspace.py"), encoding="utf-8") as f:
            src = f.read()
        # optional backends must only be imported lazily (inside functions), never at module top level
        head = src[:src.index("def ")]
        for dep in ("import pypdf", "import pypdfium2", "import fitz", "import requests"):
            self.assertNotIn(dep, head)

    def test_no_committed_course_pdfs_or_images(self):
        # the repo must not carry real course PDFs or slide images.
        # Scan TRACKED files only (git ls-files) — the test name says "committed"; local benchmark
        # artifacts (cheatsheet.pdf, rendered page PNGs under gitignored results/ or skill_workspace/)
        # are NOT committed and must not trip this. Falls back to a working-tree walk outside a git
        # checkout (CI tarball) so the guard still runs.
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8")
            tracked = [f for f in out.stdout.splitlines() if f.strip()] if out.returncode == 0 else None
        except (OSError, ValueError):
            tracked = None
        if tracked is None:                                    # not a git checkout — walk the tree
            tracked = []
            for dirpath, _dirs, files in os.walk(ROOT):
                if ".git" in dirpath:
                    continue
                for fn in files:
                    tracked.append(os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/"))
        for rel in tracked:
            low = rel.lower()
            if low.endswith(".pdf"):
                if low in {
                    "tests/fixtures/ingestion_gold/layout.pdf",
                    "tests/fixtures/ingestion_gold/scan.pdf",
                    "tests/fixtures/ingestion_gold/shared_prompt_answer.pdf",
                }:
                    # Project-authored, reproducible CC0 parser-regression fixtures.  Keep
                    # this allowlist exact so real course PDFs can never hide under fixtures/.
                    continue
                self.fail("committed PDF found: %s" % rel)
            if low.endswith((".png", ".jpg", ".jpeg")):
                if rel.split("/")[0] == "assets":              # project branding (mascot/hero), intentional
                    continue
                p = os.path.join(ROOT, *rel.split("/"))
                if os.path.isfile(p):
                    self.assertLess(os.path.getsize(p), 4096,
                                    "suspiciously large image (real slide?): %s" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
