# -*- coding: utf-8 -*-
"""Tests for scripts/build_raw_input_from_workspace.py — the official course-material builder.

All tests are stdlib-only and NEVER import pypdf/pypdfium2/PyMuPDF: the parser core runs on
synthetic page text, and the PDF backend is a fake object injected into run(). This mirrors CI,
where the optional PDF dependencies are not installed.
"""
import copy
import contextlib
import importlib.util
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import build_raw_input_from_workspace as B  # noqa: E402
import exam_start  # noqa: E402


_RUNTIME_IDENTITY = None
_CONFIRMED_FULL_PAIRS = set()
_B_RUN = B.run
_B_MAIN = B.main


def _fixture_workspace(args, explicit=None):
    """Infer a canonical test workspace without bypassing product validation."""
    if explicit is not None:
        return os.path.abspath(str(explicit))
    asset_root = getattr(args, "asset_root", None)
    if asset_root:
        references = os.path.dirname(os.path.abspath(asset_root))
        if (os.path.basename(asset_root) == "assets"
                and os.path.basename(references) == "references"):
            return os.path.dirname(references)
    candidates = []
    for value in (getattr(args, "out", None), getattr(args, "report", None)):
        if value:
            parent = os.path.dirname(os.path.abspath(value))
            if os.path.basename(parent) == ".ingest":
                candidates.append(os.path.dirname(parent))
    if candidates and all(
            os.path.normcase(os.path.normpath(value))
            == os.path.normcase(os.path.normpath(candidates[0]))
            for value in candidates):
        return candidates[0]
    return None


def _fixture_home(workspace):
    return os.path.join(
        os.path.dirname(os.path.abspath(workspace)),
        ".%s-examprep-home" % os.path.basename(os.path.abspath(workspace)),
    )


def _cached_runtime_identity():
    global _RUNTIME_IDENTITY
    if _RUNTIME_IDENTITY is None:
        _RUNTIME_IDENTITY = exam_start._capture_runtime_identity()
    return _RUNTIME_IDENTITY


@contextlib.contextmanager
def _confirmed_full_pair(materials, workspace):
    """Create and expose the exact receipt/state/registry required by full mode."""
    materials = os.path.abspath(materials)
    workspace = os.path.abspath(workspace)
    home = _fixture_home(workspace)
    key = (os.path.normcase(workspace), os.path.normcase(materials))
    runtime_identity = _cached_runtime_identity()
    with mock.patch.dict(os.environ, {"EXAMPREP_HOME": home}), \
            mock.patch.object(
                exam_start, "_capture_runtime_identity",
                return_value=runtime_identity):
        receipt = os.path.join(workspace, exam_start.RUNTIME_RECEIPT_NAME)
        registry = os.path.join(home, "workspaces.json")
        if (key not in _CONFIRMED_FULL_PAIRS
                or not os.path.isfile(receipt)
                or not os.path.isfile(registry)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = exam_start.run([
                    "confirm", "--course", "material-builder-fixture",
                    "--materials", materials, "--workspace", workspace,
                    "--mode", "from_scratch", "--time-budget", "le1d",
                    "--language", "en", "--processing-mode", "full", "--json",
                ])
            if code != 0:
                raise AssertionError(output.getvalue())
            _CONFIRMED_FULL_PAIRS.add(key)
        yield home


def _confirmed_builder_run(args, *positional, **kwargs):
    """Give publication-mode builder fixtures an exact full-mode receipt."""
    workspace = _fixture_workspace(
        args, explicit=kwargs.get("publication_workspace")
    )
    if workspace is None:
        return _B_RUN(args, *positional, **kwargs)
    with _confirmed_full_pair(getattr(args, "materials"), workspace):
        return _B_RUN(args, *positional, **kwargs)


def _confirmed_builder_main(argv=None, backend=None):
    """Confirm canonical B.main fixtures before its early workspace gate."""
    args = B.build_arg_parser().parse_args(argv)
    workspace = _fixture_workspace(args)
    if workspace is None:
        return _B_MAIN(argv, backend=backend)
    expected_out = os.path.join(workspace, ".ingest", "source_raw_input.json")
    expected_report = os.path.join(workspace, ".ingest", "parse_report.json")
    if not (
            os.path.normcase(os.path.normpath(os.path.abspath(args.out)))
            == os.path.normcase(os.path.normpath(expected_out))
            and os.path.normcase(os.path.normpath(os.path.abspath(args.report)))
            == os.path.normcase(os.path.normpath(expected_report))):
        return _B_MAIN(argv, backend=backend)
    with _confirmed_full_pair(args.materials, workspace):
        return _B_MAIN(argv, backend=backend)


def setUpModule():
    """Install builder fixture wrappers only while this module is running."""
    B.run = _confirmed_builder_run
    B.main = _confirmed_builder_main


def tearDownModule():
    B.run = _B_RUN
    B.main = _B_MAIN


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


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
        return PNG

    def page_layout(self, pdf_path, page_index):
        return {
            "page_bbox": [0.0, 0.0, 612.0, 792.0],
            "images": [],
            "text_boxes": [],
            "text_blocks": [],
        }

    def render_page_clip_png(self, pdf_path, page_index, bbox):
        # Delegate so specialised failure backends that override the historical
        # full-page method exercise the same failure on the strict clip route.
        return self.render_page_png(pdf_path, page_index)


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
    out_supplied = "out" in over
    report_supplied = "report" in over
    out = over.pop("out", os.path.join(materials, "raw_input.json"))
    rep = over.pop("report", os.path.join(materials, "parse_report.json"))
    aroot = over.pop(
        "asset_root",
        os.path.join(
            os.path.abspath(materials) + "-workspace", "references", "assets"
        ),
    )
    if aroot is not None:
        workspace = os.path.dirname(os.path.dirname(os.path.abspath(aroot)))
        ingest = os.path.join(workspace, ".ingest")
        if not out_supplied:
            out = os.path.join(ingest, "source_raw_input.json")
        if not report_supplied:
            rep = os.path.join(ingest, "parse_report.json")
    argv = ["--materials", materials, "--out", out, "--report", rep]
    if aroot is not None:                       # pass asset_root=None to OMIT --asset-root
        argv += ["--asset-root", aroot]
    for k, v in over.items():
        argv += ["--" + k.replace("_", "-"), v]
    return B.build_arg_parser().parse_args(argv)


# --------------------------------------------------------------------------- pure-core tests

class CoreExtraction(unittest.TestCase):
    def test_ingestion_language_annotation_keeps_zxx_unit_only(self):
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
            "elements": [{"kind": "text", "text": "The theorem is used here."}],
        }, {
            "file": "page-language.txt", "page": 1, "source_language": "en",
            "text": "The theorem is used in this example.",
            "elements": [{"kind": "heading", "text": "Topology"}],
        }]
        report = {}
        B._annotate_ingestion_languages(pages, report)
        self.assertNotIn("source_language", pages[0])
        self.assertNotIn("source_language", pages[0]["elements"][0])
        self.assertNotIn("source_language", pages[1])
        self.assertEqual("zxx", pages[1]["elements"][0]["source_language"])
        self.assertEqual("en", pages[2]["source_language"])
        self.assertEqual("en", pages[2]["elements"][0]["source_language"])
        self.assertEqual("en", pages[3]["source_language"])
        self.assertNotIn("source_language", pages[3]["elements"][0])
        counters = report["source_language_annotations"]
        self.assertEqual(1, counters["pages_unresolved"])
        self.assertEqual(1, counters["pages_language_neutral"])
        self.assertEqual(2, counters["elements_unresolved"])
        self.assertEqual(1, counters["elements_language_neutral"])

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
        self.assertEqual("en", B._classify_source_language("Chapter 1"))
        self.assertEqual("zh", B._classify_source_language("计算电路中的电流 I。"))
        self.assertEqual("zh", B._classify_source_language("计算 a 的值。"))
        self.assertEqual("zh", B._classify_source_language(
            "（Quiz 1.1）本题依赖原始讲义中的图。"))
        self.assertEqual("zxx", B._classify_source_language("V=IR", kind="formula"))
        self.assertEqual("zxx", B._classify_source_language("a=1", kind="text"))
        self.assertEqual("zxx", B._classify_source_language("4", kind="answer"))
        self.assertEqual("zxx", B._classify_source_language(
            r"S=\{bbb,bbn,bnb,bnn\}", kind="formula"))
        self.assertEqual("zxx", B._classify_source_language(
            r"B_1=\{ttth,ttht,thtt,httt\}", kind="formula"))
        self.assertEqual("zxx", B._classify_source_language(
            r"A=\{tttt,httt,thtt,ttht,ttth\}", kind="formula"))
        self.assertEqual("zxx", B._classify_source_language("(ma,ea)", kind="formula"))
        self.assertIsNone(B._classify_source_language(
            "P=1 otherwise 0", kind="formula"))
        self.assertIsNone(B._classify_source_language(
            "", kind="formula", latex=r"P=1\;\text{for a valid result}"))
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
        self.assertEqual("zxx", items[1]["source_language"])
        self.assertEqual("zxx", items[1]["answer_source_language"])
        self.assertTrue(any(w.startswith("source_language_inferred: 4")
                            for w in report["warnings"]))
        self.assertFalse(any(w.startswith("source_language_review_required:")
                             for w in report["warnings"]))

    def test_requires_assets_heuristic(self):
        self.assertTrue(B.requires_assets_heuristic("Shade the Venn diagram at right."))
        self.assertTrue(B.requires_assets_heuristic("see the figure / table below"))
        self.assertFalse(B.requires_assets_heuristic("Compute 2 + 2 and simplify."))

    def test_page_reference_language_uses_original_prompt_not_generated_instruction(self):
        item = B.extract_lecture_items(_pages(
            "ch01.pdf",
            "Example 1.6 Determine the probability shown in the figure below.",
            "Example 1.6 Solution  The probability is one half.",
        ))[0]
        self.assertEqual("page_reference", item["question_text_status"])
        self.assertEqual("en", B._classify_source_language(
            item["_prompt_text"], kind="question"
        ))
        self.assertEqual("zh", B._classify_source_language(
            item["question"], kind="question"
        ))
        chinese = {"question": "计算该事件的概率。", "answer": "答案为二分之一。"}
        report = {"warnings": []}
        B._annotate_source_languages([item, chinese], report)
        self.assertEqual("en", item["source_language"])
        self.assertEqual("zh", chinese["source_language"])

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

    def test_lettered_quiz_solutions_pair_for_all_eec_missing_answer_regressions(self):
        # EEC 160 uses ``Quiz N.N(A) Solution``.  The parenthesized subpart is
        # between the numeric marker and role word; it must not turn the answer
        # page into another bare problem marker.
        affected = ((5, 6), (5, 8), (6, 4), (7, 2),
                    (7, 3), (7, 4), (7, 5), (9, 3))
        for chapter, number in affected:
            heading = "Quiz %d.%d(A)" % (chapter, number)
            pages = _pages(
                "ch%02d.pdf" % chapter,
                heading + "  Compute the requested quantity.",
                heading + " Solution  official worked answer.",
            )
            item = B.extract_lecture_items(pages)[0]
            self.assertEqual(item["id"], "lecture_quiz_%d_%d_a" % (chapter, number))
            self.assertEqual(item["variant"], "a")
            self.assertEqual(item["source_pages"], [1])
            self.assertEqual(item["answer_source_pages"], [2])
            self.assertNotIn("official worked answer", item["question"])
            self.assertIn("official worked answer", item["answer"])

    def test_lettered_solution_continuations_never_join_the_question(self):
        pages = _pages(
            "ch07.pdf",
            "Quiz 7.3(A)  Find the conditional PMF.",
            "Quiz 7.3(A) Solution  first answer page.",
            "Quiz 7.3(A) Solution (Continued 2)  second answer page.",
        )
        item = B.extract_lecture_items(pages)[0]
        self.assertEqual(item["id"], "lecture_quiz_7_3_a")
        self.assertEqual(item["source_pages"], [1])
        self.assertEqual(item["answer_source_pages"], [2, 3])
        self.assertNotIn("answer page", item["question"])
        self.assertIn("first answer page", item["answer"])
        self.assertIn("second answer page", item["answer"])

    def test_lettered_variants_are_distinct_and_never_share_answers(self):
        pages = _pages(
            "ch05.pdf",
            "Quiz 5.6(A)  Compute answer A.",
            "Quiz 5.6(A) Solution  official A only.",
            "Quiz 5.6(B)  Compute answer B.",
            "Quiz 5.6(B) Solution  official B only.",
        )
        items = {item["id"]: item for item in B.extract_lecture_items(pages)}
        self.assertEqual(set(items), {"lecture_quiz_5_6_a", "lecture_quiz_5_6_b"})

        part_a = items["lecture_quiz_5_6_a"]
        self.assertEqual(part_a["source_pages"], [1])
        self.assertEqual(part_a["answer_source_pages"], [2])
        self.assertIn("official A only", part_a["answer"])
        self.assertNotIn("official B only", part_a["answer"])

        part_b = items["lecture_quiz_5_6_b"]
        self.assertEqual(part_b["source_pages"], [3])
        self.assertEqual(part_b["answer_source_pages"], [4])
        self.assertIn("official B only", part_b["answer"])
        self.assertNotIn("official A only", part_b["answer"])

        markers = B.detect_lecture_markers("\n".join(page["text"] for page in pages))
        self.assertEqual([marker["variant"] for marker in markers], ["a", "a", "b", "b"])

    def test_lettered_marker_only_title_keeps_variant_in_page_reference(self):
        item = B.extract_lecture_items(_pages("ch05.pdf", "Quiz 5.6(A)"))[0]
        self.assertEqual(item["id"], "lecture_quiz_5_6_a")
        self.assertEqual(item["question_text_status"], "page_reference")
        self.assertIn("Quiz 5.6(A)", item["question"])

    def test_lettered_orphan_solution_reports_its_variant(self):
        pages = _pages("ch05.pdf", "Quiz 5.6(B) Solution  answer without prompt.")
        self.assertEqual(B.orphan_solution_keys(pages), [("quiz", 5, 6, "b")])

    def test_lettered_example_and_problem_titles_keep_adjacent_real_prompts(self):
        example_markers = B.detect_lecture_markers(
            "Example 4.2(B) Solution  done.\n"
            "Example 4.3(B) Problem  prove it.\n"
            "Example 4.4(B) Calculate the expectation."
        )
        self.assertEqual(
            [(m["num"], m["variant"], m["role"]) for m in example_markers],
            [(2, "b", "solution"), (3, "b", "problem"), (4, "b", "problem")],
        )
        bare = B.extract_lecture_items(
            _pages("ch04.pdf", "Example 4.4(B) Calculate the expectation."))[0]
        self.assertEqual(bare["id"], "lecture_example_4_4_b")
        self.assertEqual(bare["_teaching_title"], "Example 4.4(B)")
        self.assertEqual(bare["_teaching_role"], "worked_example")

        # Homework Problem numbering already consumes the lettered subpart as
        # part of its number; keep that neighboring parser behavior intact.
        homework_markers = B._hw_markers(
            "Problem 5.6(A)  Determine independence.\n"
            "Problem 5.6(A) Solution\nThey are dependent."
        )
        self.assertEqual(
            [(m["num"], m["role"]) for m in homework_markers],
            [("5.6a", "problem"), ("5.6a", "solution")],
        )

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

    def test_crop_spec_digest_changes_asset_name(self):
        first = B._safe_asset_name(
            "quiz.pdf", 12, "q1", "_qcrop1",
            source_sha256="1" * 64, crop_spec_sha256="2" * 64,
        )
        second = B._safe_asset_name(
            "quiz.pdf", 12, "q1", "_qcrop1",
            source_sha256="1" * 64, crop_spec_sha256="3" * 64,
        )
        self.assertNotEqual(first, second)
        self.assertIn("crop_%s" % ("2" * 12), first)

    # ---- round-4 hardening ----
    def test_inline_mention_is_not_a_marker(self):
        # round-4 P2: prose "See Example 1.1" / a TOC entry must not be mistaken for a lecture heading
        pages = _pages("ch01.pdf", "Please review the proof. See Example 1.1 in the textbook for details.")
        self.assertEqual(B.extract_lecture_items(pages), [])

    def test_wrapped_example_reference_does_not_cut_real_eec_prompt(self):
        # Real EEC-160 p.77 extraction: the PDF line wrap puts an inline Example reference at column
        # zero. It is still prose, not the next title/boundary.
        text = (
            "Example 1.21 Problem\n"
            "Suppose that for the experiment monitoring three purchasing decisions in\n"
            "Example 1.9, each outcome (a sequence of three decisions, each either\n"
            "buy or not buy) is equally likely.\n"
            "Are the events B2 that the second customer purchases a phone and N2 that the second\n"
            "customer does not purchase a phone independent? Are the events B1 and B2 independent?"
        )
        items = B.extract_lecture_items(_pages("ch01.pdf", text))
        self.assertEqual([item["id"] for item in items], ["lecture_example_1_21"])
        self.assertIn("Example 1.9, each outcome", items[0]["question"])
        self.assertTrue(items[0]["question"].endswith("Are the events B1 and B2 independent?"))

    def test_wrapped_quiz_reference_does_not_cut_real_eec_prompt(self):
        # Real EEC-160 p.91 extraction: Quiz 1.3 is the object of "described in", not a new Quiz.
        text = (
            "Example 1.25 Problem\n"
            "Use Matlab to generate 12 random student test scores T as described in\n"
            "Quiz 1.3."
        )
        items = B.extract_lecture_items(_pages("ch01.pdf", text))
        self.assertEqual([item["id"] for item in items], ["lecture_example_1_25"])
        self.assertEqual(
            items[0]["question"],
            "Example 1.25 Problem Use Matlab to generate 12 random student test scores T "
            "as described in Quiz 1.3.",
        )

    def test_wrapped_unpunctuated_example_reference_is_not_a_boundary(self):
        # Real EEC-160 ch.2 p.20: this inline reference has no comma/period after its number, so the
        # preceding continuation line supplies the decisive structure signal.
        text = (
            "Example 2.9\n"
            "The number of seven-card combinations is 133,784,560.\n"
            "By contrast, we found in\n"
            "Example 2.5 674,274,182,400 seven-permutations of 52 objects.\n"
            "The ratio is 7! = 5040."
        )
        items = B.extract_lecture_items(_pages("ch02.pdf", text))
        self.assertEqual([item["id"] for item in items], ["lecture_example_2_9"])
        self.assertIn("Example 2.5 674,274,182,400", items[0]["question"])
        self.assertTrue(items[0]["question"].endswith("The ratio is 7! = 5040."))

    def test_line_start_example_reference_inside_solution_is_not_bare_example(self):
        text = (
            "Example 6.13 Solution\n"
            "function x=uniformrv(a,b,m)\n"
            "x=a+(b-a)*rand(m,1);\n"
            "Example 6.6 says that Y = a + (b - a)U is a uniform random variable.\n"
            "Example 6.14 Problem\n"
            "Write a function that generates samples of Y."
        )
        markers = B.detect_lecture_markers(text)
        self.assertEqual(
            [(m["chapter"], m["num"], m["role"]) for m in markers],
            [(6, 13, "solution"), (6, 14, "problem")],
        )
        ids = [it["id"] for it in B.extract_lecture_items(_pages("ch06.pdf", text))]
        self.assertEqual(ids, ["lecture_example_6_14"])

    def test_genuine_bare_example_after_solution_is_still_detected(self):
        text = (
            "Example 6.13 Solution  The previous result.\n"
            "Example 6.14 Calculate the expected value of Y."
        )
        markers = B.detect_lecture_markers(text)
        self.assertEqual(
            [(m["chapter"], m["num"], m["role"]) for m in markers],
            [(6, 13, "solution"), (6, 14, "problem")],
        )
        item = B.extract_lecture_items(_pages("ch06.pdf", text))[0]
        self.assertEqual(item["id"], "lecture_example_6_14")
        self.assertEqual(item["_teaching_role"], "worked_example")

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
        self.assertEqual(
            "Quiz 1.1 Solution the shaded region is A and B.", it["answer"])
        self.assertNotIn("须看原页", it["answer"])

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

    def test_programmatic_heavy_adapter_runner_is_rejected_locally(self):
        materials = _materials_with_pdf()

        runner = mock.Mock(side_effect=AssertionError(
            "the local builder must never invoke a heavy parser runner"
        ))

        code, payload, report = B.run(
            _args(materials, ingest_adapter="docling", render_pages="never"),
            backend=B.NoBackend(),
            adapter_runner=runner,
        )
        self.assertEqual(3, code)
        runner.assert_not_called()
        self.assertIn("remote/cloud-host-only", payload["error"])
        self.assertEqual([], report["files_scanned"])

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
        workspace = os.path.dirname(os.path.dirname(args.asset_root))
        self.assertTrue(os.path.isfile(os.path.join(
            workspace, *raster["asset_path"].split("/"))))
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
            "Example 1.2  This worked demonstration completes the calculation.",
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

        question_units = [
            row for row in raw["ingestion"]["content_units"]
            if row["kind"] == "question"
        ]
        by_external_id = {}
        for row in question_units:
            by_external_id.setdefault(row.get("external_id"), []).append(row)
        self.assertEqual(1, len(by_external_id["lecture_example_1_1"]))
        self.assertEqual(1, len(by_external_id["lecture_example_1_2"]))
        teaching_only_unit = by_external_id["lecture_example_1_2"][0]
        self.assertEqual("ch01", teaching_only_unit["chapter_id"])
        self.assertIs(teaching_only_unit["metadata"]["gradable"], False)
        self.assertEqual("example", teaching_only_unit["metadata"]["source_type"])
        self.assertEqual(
            teaching["lecture_example_1_2"]["source_language"],
            teaching_only_unit["metadata"]["source_language"],
        )
        self.assertFalse(any(
            row["kind"] == "answer"
            and row.get("external_id") == "lecture_example_1_2"
            for row in raw["ingestion"]["content_units"]
        ))
        self.assertFalse(any(
            row["reason_codes"] == ["missing_answer"]
            and teaching_only_unit["unit_id"] in row.get("target_unit_ids", [])
            for row in raw["ingestion"]["review_candidates"]
        ))
        type_reviews = [
            row for row in raw["ingestion"]["review_candidates"]
            if row["reason_codes"] == ["type_defaulted"]
            and teaching_only_unit["unit_id"] in row.get("target_unit_ids", [])
        ]
        # Non-gradable worked examples still need a type for visual teaching and Guide authoring.
        self.assertEqual(1, len(type_reviews))

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
        workspace = os.path.abspath(d) + "-workspace"
        args = _args(
            d, render_pages="required", asset_root=None,
            out=os.path.join(workspace, ".ingest", "source_raw_input.json"),
            report=os.path.join(workspace, ".ingest", "parse_report.json"),
        )
        code, payload, _ = B.run(
            args, backend=be, publication_workspace=workspace
        )
        self.assertEqual(code, 2)
        self.assertIn("asset-root", payload["error"])

    def test_render_auto_without_asset_root_warns_and_skips(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        workspace = os.path.abspath(d) + "-workspace"
        args = _args(
            d, asset_root=None,
            out=os.path.join(workspace, ".ingest", "source_raw_input.json"),
            report=os.path.join(workspace, ".ingest", "parse_report.json"),
        )
        code, ri, report = B.run(
            args, backend=be, publication_workspace=workspace
        )  # render auto, no --asset-root
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

    def test_signature_prefixed_garbage_png_is_never_published(self):
        materials = _materials_with_pdf()

        class CorruptPng(FakeBackend):
            def render_page_png(self, pdf_path, page_index):
                return b"\x89PNG\r\n\x1a\nnot-a-real-png"

        backend = CorruptPng({
            "ch01.pdf": [
                "Quiz 1.1  Shade the diagram at right.",
                "Quiz 1.1 Solution  region A.",
            ],
        })
        args = _args(materials, render_pages="required")
        code, _payload, report = B.run(args, backend=backend)

        self.assertEqual(3, code)
        self.assertTrue(any(
            "invalid PNG" in row.get("why", "")
            for row in report["skipped"]
        ))
        self.assertFalse(os.path.exists(args.asset_root))

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

    def test_continued_answer_page_without_exact_crop_stays_in_review(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.4  Shade the Venn diagram at right.",
                                       "Quiz 1.4 Solution  part1.",
                                       "Quiz 1.4 Solution (Continued)  part2."]})
        args = _args(d)
        code, ri, report = B.run(args, backend=be)
        item = next(q for q in ri["quiz_bank"] if q["id"] == "lecture_quiz_1_4")
        sol = [a for a in item["assets"] if a["role"] == "answer_context"]
        # The first answer page has a unique solution marker and is isolated.
        # A continuation marker alone does not prove that its whole page contains
        # no unrelated material, so it remains explicit review work until an
        # exact layout/vision crop is supplied; no unsafe whole-page fallback.
        self.assertEqual(len(sol), 1)
        self.assertTrue(os.path.isfile(os.path.join(
            args.asset_root, os.path.basename(sol[0]["path"])
        )))
        self.assertTrue(any(
            row.get("kind") == "item_asset_crop_not_materialized"
            and row.get("pages") == [3]
            and row.get("side") == "answer"
            for row in report["ai_review"]
        ))

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
    def _visual_publication_fixture(self):
        materials = _materials_with_pdf()
        workspace = os.path.join(os.path.dirname(materials), "workspace")
        assets = os.path.join(workspace, "references", "assets")
        os.makedirs(assets, exist_ok=True)
        ingest = os.path.join(workspace, ".ingest")
        os.makedirs(ingest, exist_ok=True)
        out = os.path.join(ingest, "source_raw_input.json")
        report = os.path.join(ingest, "parse_report.json")
        backend = FakeBackend({
            "ch01.pdf": [
                "Quiz 1.1  Shade the Venn diagram at right.",
                "Quiz 1.1 Solution  region A.",
            ],
        })
        with open(os.path.join(materials, "ch01.pdf"), "rb") as stream:
            source_sha256 = hashlib.sha256(stream.read()).hexdigest()
        name = B._safe_asset_name(
            "ch01.pdf", 1, "lecture_quiz_1_1", "_isolated",
            source_sha256=source_sha256,
        )
        return materials, workspace, assets, out, report, backend, name

    def _attempt_promotion_fixture(
            self, temp, *, old_role="answer_context",
            new_role="student_attempt", old_id="hw-1", new_id="hw-1",
            old_chapter=1, new_chapter=1, old_source="homework/hw1.pdf",
            new_source="homework/hw1.pdf", old_source_type="homework",
            new_source_type="homework", old_source_sha256=None,
            new_source_sha256=None, omit_old=(), omit_new=(),
            staged_bytes=PNG):
        workspace = os.path.join(temp, "workspace")
        destination = os.path.join(workspace, "references", "assets")
        stage = os.path.join(temp, "stage")
        ingest = os.path.join(workspace, ".ingest")
        os.makedirs(destination)
        os.makedirs(stage)
        os.makedirs(ingest)
        relative = "references/assets/legacy-answer.png"
        with open(os.path.join(destination, "legacy-answer.png"), "wb") as stream:
            stream.write(PNG)
        with open(os.path.join(stage, "legacy-answer.png"), "wb") as stream:
            stream.write(staged_bytes)
        asset_sha256 = hashlib.sha256(PNG).hexdigest()
        old_source_sha256 = old_source_sha256 or ("1" * 64)
        new_source_sha256 = new_source_sha256 or old_source_sha256

        def asset(role, source_file, source_sha256, omitted):
            value = {
                "path": relative,
                "role": role,
                "sha256": asset_sha256,
                "source_file": source_file,
                "source_sha256": source_sha256,
            }
            for name in omitted:
                value.pop(name, None)
            return value

        old_item = {
            "id": old_id, "chapter": old_chapter,
            "source_type": old_source_type, "source_file": old_source,
            "assets": [asset(
                old_role, old_source, old_source_sha256, set(omit_old)
            )],
        }
        new_item = {
            "id": new_id, "chapter": new_chapter,
            "source_type": new_source_type, "source_file": new_source,
            "assets": [asset(
                new_role, new_source, new_source_sha256, set(omit_new)
            )],
        }
        references = os.path.dirname(destination)
        with open(os.path.join(references, "quiz_bank.json"), "w",
                  encoding="utf-8") as stream:
            json.dump([old_item], stream)
        with open(os.path.join(references, "teaching_examples.json"), "w",
                  encoding="utf-8") as stream:
            json.dump([], stream)
        with open(os.path.join(ingest, "content_units.jsonl"), "w",
                  encoding="utf-8"):
            pass
        candidate = {
            "quiz_bank": [new_item],
            "teaching_examples": [],
            "ingestion": {"content_units": []},
        }
        return workspace, destination, stage, relative, candidate

    def test_staged_builder_never_overwrites_unowned_target_or_json(self):
        (materials, unused_workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        target = os.path.join(assets, name)
        sentinel_asset = b"UNOWNED-ASSET"
        sentinel_out = b"OLD-RAW-INPUT"
        sentinel_report = b"OLD-REPORT"
        with open(target, "wb") as stream:
            stream.write(sentinel_asset)
        with open(out, "wb") as stream:
            stream.write(sentinel_out)
        with open(report, "wb") as stream:
            stream.write(sentinel_report)

        code = B.main([
            "--materials", materials, "--out", out, "--report", report,
            "--asset-root", assets,
        ], backend=backend)

        self.assertEqual(5, code)
        self.assertEqual(sentinel_asset, open(target, "rb").read())
        self.assertEqual(sentinel_out, open(out, "rb").read())
        self.assertEqual(sentinel_report, open(report, "rb").read())

    def test_staged_builder_live_attempt_policy_rejects_even_identical_bytes(self):
        (materials, workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        target = os.path.join(assets, name)
        candidate_bytes = backend.render_page_png(
            os.path.join(materials, "ch01.pdf"), 0
        )
        with open(target, "wb") as stream:
            stream.write(candidate_bytes)
        references = os.path.join(workspace, "references")
        os.makedirs(references, exist_ok=True)
        bank_path = os.path.join(references, "quiz_bank.json")
        target_rel = "references/assets/" + name
        with open(bank_path, "w", encoding="utf-8") as stream:
            json.dump([{
                "id": "legacy-attempt", "chapter": 9,
                "assets": [{"path": target_rel, "role": "student_attempt"}],
            }], stream, ensure_ascii=False, indent=2)
        with open(os.path.join(workspace, ".ingest", "content_units.jsonl"),
                  "w", encoding="utf-8"):
            pass
        sentinel_out = b"OLD-RAW-INPUT"
        sentinel_report = b"OLD-REPORT"
        with open(out, "wb") as stream:
            stream.write(sentinel_out)
        with open(report, "wb") as stream:
            stream.write(sentinel_report)

        code = B.main([
            "--materials", materials, "--out", out, "--report", report,
            "--asset-root", assets,
        ], backend=backend)

        self.assertEqual(5, code)
        self.assertEqual(candidate_bytes, open(target, "rb").read())
        self.assertEqual(sentinel_out, open(out, "rb").read())
        self.assertEqual(sentinel_report, open(report, "rb").read())
        self.assertIn("student_attempt",
                      open(bank_path, encoding="utf-8").read())

    def test_staged_candidate_batch_rejects_attempt_laundering_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            stage = os.path.join(temp, "stage")
            destination = os.path.join(workspace, "references", "assets")
            os.makedirs(stage)
            with open(os.path.join(stage, "shared.png"), "wb") as stream:
                stream.write(b"candidate")
            relative = "references/assets/shared.png"
            candidate = {
                "quiz_bank": [{
                    "id": "official-q", "chapter": 1,
                    "assets": [{"path": relative, "role": "question_context"}],
                }],
                "teaching_examples": [{
                    "id": "foreign-attempt", "chapter": 2,
                    "assets": [{"path": relative, "role": "student_attempt"}],
                }],
                "ingestion": {"content_units": []},
            }

            with self.assertRaisesRegex(ValueError, "student_attempt"):
                B._publish_staged_assets(stage, destination, candidate, workspace)

            self.assertFalse(os.path.lexists(destination))
            self.assertEqual(b"candidate", open(
                os.path.join(stage, "shared.png"), "rb").read())

    def test_staged_candidate_rejects_hardlink_alias_of_existing_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            destination = os.path.join(workspace, "references", "assets")
            stage = os.path.join(temp, "stage")
            ingest = os.path.join(workspace, ".ingest")
            os.makedirs(destination)
            os.makedirs(stage)
            os.makedirs(ingest)
            attempt = os.path.join(destination, "attempt.png")
            official = os.path.join(destination, "official.png")
            with open(attempt, "wb") as stream:
                stream.write(PNG)
            try:
                os.link(attempt, official)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")
            with open(os.path.join(stage, "official.png"), "wb") as stream:
                stream.write(PNG)
            references = os.path.dirname(destination)
            with open(os.path.join(references, "quiz_bank.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([{
                    "id": "old-student-attempt", "chapter": 2,
                    "assets": [{
                        "path": "references/assets/attempt.png",
                        "role": "student_attempt",
                    }],
                }], stream)
            with open(os.path.join(references, "teaching_examples.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([], stream)
            with open(os.path.join(ingest, "content_units.jsonl"), "w",
                      encoding="utf-8"):
                pass
            candidate = {
                "quiz_bank": [{
                    "id": "new-official", "chapter": 1,
                    "assets": [{
                        "path": "references/assets/official.png",
                        "role": "question_context",
                    }],
                }],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }

            with self.assertRaisesRegex(ValueError, "hardlink alias.*student_attempt"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )
            self.assertTrue(os.path.samefile(attempt, official))
            self.assertEqual(PNG, open(official, "rb").read())

    def test_staged_candidate_rejects_prompt_alias_of_existing_answer_hardlink(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            destination = os.path.join(workspace, "references", "assets")
            stage = os.path.join(temp, "stage")
            ingest = os.path.join(workspace, ".ingest")
            os.makedirs(destination)
            os.makedirs(stage)
            os.makedirs(ingest)
            answer = os.path.join(destination, "answer.png")
            prompt = os.path.join(destination, "prompt.png")
            with open(answer, "wb") as stream:
                stream.write(PNG)
            try:
                os.link(answer, prompt)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")
            with open(os.path.join(stage, "prompt.png"), "wb") as stream:
                stream.write(PNG)
            references = os.path.dirname(destination)
            with open(os.path.join(references, "quiz_bank.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([{
                    "id": "q1", "chapter": 1,
                    "assets": [{
                        "path": "references/assets/answer.png",
                        "role": "answer_context",
                    }],
                }], stream)
            with open(os.path.join(references, "teaching_examples.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([], stream)
            with open(os.path.join(ingest, "content_units.jsonl"), "w",
                      encoding="utf-8"):
                pass
            candidate = {
                "quiz_bank": [{
                    "id": "q1", "chapter": 1,
                    "assets": [{
                        "path": "references/assets/prompt.png",
                        "role": "question_context",
                    }],
                }],
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }

            with self.assertRaisesRegex(
                    ValueError, "conflicts with existing workspace ownership"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_staged_candidate_allows_exact_legacy_answer_attempt_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage,
             relative, candidate) = self._attempt_promotion_fixture(temp)

            plan = B._plan_staged_assets(
                stage, destination, candidate, workspace
            )

            self.assertEqual(1, len(plan["publication"]))
            self.assertFalse(plan["publication"][0][2])
            self.assertEqual(({
                "path": relative,
                "from_roles": ["answer_context"],
                "to_roles": ["student_attempt"],
                "sha256": hashlib.sha256(PNG).hexdigest(),
                "source_file": "homework/hw1.pdf",
                "source_sha256": "1" * 64,
                "item_chapter": "1",
                "item_id": "hw-1",
                "reason": "legacy_answer_context_to_student_attempt",
            },), plan["role_promotions"])

    def test_staged_candidate_attempt_promotion_still_requires_identical_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(
                 temp, staged_bytes=PNG + b"different-revision"
             )

            with self.assertRaisesRegex(ValueError, "different bytes"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_attempt_promotion_rejects_every_other_old_role(self):
        for old_role in ("question_context", "figure", "worked_solution"):
            with self.subTest(old_role=old_role), tempfile.TemporaryDirectory() as temp:
                (workspace, destination, stage, _relative,
                 candidate) = self._attempt_promotion_fixture(
                     temp, old_role=old_role
                 )
                with self.assertRaisesRegex(
                        ValueError, "conflicts with existing workspace ownership"):
                    B._plan_staged_assets(
                        stage, destination, candidate, workspace
                    )

    def test_attempt_promotion_rejects_cross_item_or_chapter_owner(self):
        cases = (
            {"new_id": "hw-2"},
            {"new_chapter": 2},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                (workspace, destination, stage, _relative,
                 candidate) = self._attempt_promotion_fixture(temp, **changes)
                with self.assertRaisesRegex(
                        ValueError, "conflicts with workspace ownership"):
                    B._plan_staged_assets(
                        stage, destination, candidate, workspace
                    )

    def test_attempt_promotion_rejects_multiple_candidate_item_owners(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            second = copy.deepcopy(candidate["quiz_bank"][0])
            second["id"] = "hw-2"
            candidate["quiz_bank"].append(second)

            with self.assertRaisesRegex(
                    ValueError, "conflicts with workspace ownership"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_attempt_promotion_rejects_missing_or_changed_provenance(self):
        cases = (
            {"omit_old": ("sha256",)},
            {"omit_new": ("source_file",)},
            {"omit_new": ("source_sha256",)},
            {"new_source_sha256": "2" * 64},
            {"new_source": "homework/hw2.pdf"},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                (workspace, destination, stage, _relative,
                 candidate) = self._attempt_promotion_fixture(temp, **changes)
                with self.assertRaises(ValueError):
                    B._plan_staged_assets(
                        stage, destination, candidate, workspace
                    )
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            candidate["quiz_bank"][0]["assets"][0]["sha256"] = "2" * 64
            with self.assertRaises(ValueError):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_attempt_promotion_rejects_non_homework_or_mismatched_owner_source(self):
        cases = (
            {"old_source_type": "lecture_quiz"},
            {"new_source_type": "lecture_quiz"},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                (workspace, destination, stage, _relative,
                 candidate) = self._attempt_promotion_fixture(temp, **changes)
                with self.assertRaises(ValueError):
                    B._plan_staged_assets(
                        stage, destination, candidate, workspace
                    )
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            candidate["quiz_bank"][0]["source_file"] = "homework/other.pdf"
            with self.assertRaisesRegex(ValueError, "same homework source"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_attempt_promotion_rejects_reverse_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(
                 temp, old_role="student_attempt", new_role="answer_context"
             )
            with self.assertRaisesRegex(ValueError, "student_attempt"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )

    def test_attempt_promotion_rejects_hardlink_alias_path(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, relative,
             candidate) = self._attempt_promotion_fixture(temp)
            old_path = os.path.join(destination, "legacy-answer.png")
            alias_path = os.path.join(destination, "alias.png")
            try:
                os.link(old_path, alias_path)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")
            os.unlink(os.path.join(stage, "legacy-answer.png"))
            with open(os.path.join(stage, "alias.png"), "wb") as stream:
                stream.write(PNG)
            alias_relative = "references/assets/alias.png"
            candidate["quiz_bank"][0]["assets"][0]["path"] = alias_relative

            with self.assertRaisesRegex(
                    ValueError, "conflicts with workspace ownership"):
                B._plan_staged_assets(
                    stage, destination, candidate, workspace
                )
            self.assertTrue(os.path.samefile(
                os.path.join(workspace, *relative.split("/")), alias_path
            ))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_attempt_promotion_rejects_asset_path_case_only_change(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            os.unlink(os.path.join(stage, "legacy-answer.png"))
            with open(os.path.join(stage, "Legacy-answer.png"), "wb") as stream:
                stream.write(PNG)
            candidate["quiz_bank"][0]["assets"][0]["path"] = (
                "references/assets/Legacy-answer.png"
            )
            with self.assertRaisesRegex(ValueError, "workspace ownership"):
                B._plan_staged_assets(stage, destination, candidate, workspace)

    def test_attempt_promotion_rejects_source_path_case_only_change(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(
                 temp, new_source="homework/HW1.pdf"
             )
            with self.assertRaisesRegex(ValueError, "provenance"):
                B._plan_staged_assets(stage, destination, candidate, workspace)

    def test_attempt_promotion_publish_rejects_same_byte_hardlink_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            plan = B._plan_staged_assets(stage, destination, candidate, workspace)
            target = os.path.join(destination, "legacy-answer.png")
            replacement = os.path.join(destination, "same-bytes.png")
            with open(replacement, "wb") as stream:
                stream.write(PNG)
            os.unlink(target)
            try:
                os.link(replacement, target)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")

            with self.assertRaisesRegex(ValueError, "authority drifted"):
                B._publish_asset_plan(plan)
            self.assertTrue(os.path.samefile(target, replacement))

    def test_attempt_promotion_rechecks_full_candidate_alias_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            (workspace, destination, stage, _relative,
             candidate) = self._attempt_promotion_fixture(temp)
            target = os.path.join(destination, "legacy-answer.png")
            alias = os.path.join(destination, "candidate-prompt.png")
            with open(alias, "wb") as stream:
                stream.write(PNG)
            with open(os.path.join(stage, "candidate-prompt.png"), "wb") as stream:
                stream.write(PNG)
            prompt = copy.deepcopy(candidate["quiz_bank"][0])
            prompt.update({
                "id": "hw-2", "chapter": 2,
                "source_file": "homework/hw2.pdf",
            })
            prompt["assets"][0].update({
                "path": "references/assets/candidate-prompt.png",
                "role": "question_context",
                "source_file": "homework/hw2.pdf",
                "source_sha256": "2" * 64,
            })
            candidate["quiz_bank"].append(prompt)
            plan = B._plan_staged_assets(stage, destination, candidate, workspace)
            os.unlink(alias)
            try:
                os.link(target, alias)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")

            with self.assertRaisesRegex(ValueError, "candidate asset policy drifted"):
                B._publish_asset_plan(plan)
            self.assertTrue(os.path.samefile(target, alias))

    def test_staged_candidate_allows_idempotent_cross_item_prompt_answer_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            destination = os.path.join(workspace, "references", "assets")
            stage = os.path.join(temp, "stage")
            ingest = os.path.join(workspace, ".ingest")
            os.makedirs(destination)
            os.makedirs(stage)
            os.makedirs(ingest)
            shared = os.path.join(destination, "shared.png")
            with open(shared, "wb") as stream:
                stream.write(PNG)
            with open(os.path.join(stage, "shared.png"), "wb") as stream:
                stream.write(PNG)
            records = [{
                "id": "prompt-item", "chapter": 1,
                "assets": [{
                    "path": "references/assets/shared.png",
                    "role": "question_context",
                }],
            }, {
                "id": "answer-item", "chapter": 1,
                "assets": [{
                    "path": "references/assets/shared.png",
                    "role": "answer_context",
                }],
            }]
            references = os.path.dirname(destination)
            with open(os.path.join(references, "quiz_bank.json"), "w",
                      encoding="utf-8") as stream:
                json.dump(records, stream)
            with open(os.path.join(references, "teaching_examples.json"), "w",
                      encoding="utf-8") as stream:
                json.dump([], stream)
            with open(os.path.join(ingest, "content_units.jsonl"), "w",
                      encoding="utf-8"):
                pass
            candidate = {
                "quiz_bank": records,
                "teaching_examples": [],
                "ingestion": {"content_units": []},
            }

            plan = B._plan_staged_assets(
                stage, destination, candidate, workspace
            )

            self.assertEqual(1, len(plan["publication"]))
            self.assertFalse(plan["publication"][0][2])

    def test_staged_candidate_rejects_invalid_compatibility_asset_declaration(self):
        cases = (
            {"path": "references/assets/NUL.png", "role": "question_context"},
            {"path": "references/assets/prompt.png", "role": {"side": "prompt"}},
        )
        for asset in cases:
            with self.subTest(asset=asset), tempfile.TemporaryDirectory() as temp:
                workspace = os.path.join(temp, "workspace")
                stage = os.path.join(temp, "stage")
                destination = os.path.join(workspace, "references", "assets")
                os.makedirs(stage)
                candidate = {
                    "quiz_bank": [{
                        "id": "bad-q", "chapter": 1, "assets": [asset],
                    }],
                    "teaching_examples": [],
                    "ingestion": {"content_units": []},
                }

                with self.assertRaisesRegex(ValueError, "invalid"):
                    B._publish_staged_assets(stage, destination, candidate, workspace)

                self.assertFalse(os.path.lexists(destination))

    def test_workspace_inference_rejects_outside_asset_root_before_mutation(self):
        materials = _materials_with_pdf()
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            ingest = os.path.join(workspace, ".ingest")
            outside_assets = os.path.join(temp, "other", "references", "assets")
            os.makedirs(ingest)
            os.makedirs(outside_assets)
            out = os.path.join(ingest, "source_raw_input.json")
            report = os.path.join(ingest, "parse_report.json")
            sentinel_out = b"OLD-RAW-INPUT"
            sentinel_report = b"OLD-REPORT"
            sentinel_asset = b"OUTSIDE-ASSET"
            with open(out, "wb") as stream:
                stream.write(sentinel_out)
            with open(report, "wb") as stream:
                stream.write(sentinel_report)
            outside_sentinel = os.path.join(outside_assets, "sentinel.bin")
            with open(outside_sentinel, "wb") as stream:
                stream.write(sentinel_asset)

            backend = FakeBackend({
                "ch01.pdf": ["Quiz 1.1  Shade the diagram at right."],
            })
            backend.render_page_png = mock.Mock(
                side_effect=AssertionError("render must not run across workspace roots")
            )
            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", outside_assets,
            ], backend=backend)

            self.assertEqual(2, code)
            backend.render_page_png.assert_not_called()
            self.assertEqual(sentinel_out, open(out, "rb").read())
            self.assertEqual(sentinel_report, open(report, "rb").read())
            self.assertEqual(sentinel_asset, open(outside_sentinel, "rb").read())
            self.assertFalse(os.path.lexists(
                os.path.join(workspace, "references", "assets")
            ))

    def test_workspace_publication_paths_require_canonical_component_spelling(self):
        materials = _materials_with_pdf()
        with tempfile.TemporaryDirectory() as temp:
            workspace = os.path.join(temp, "workspace")
            cases = (
                (
                    os.path.join(workspace, ".INGEST", "source_raw_input.json"),
                    os.path.join(workspace, ".INGEST", "parse_report.json"),
                    None,
                    "canonical .ingest",
                ),
                (
                    os.path.join(temp, "raw_input.json"),
                    os.path.join(temp, "parse_report.json"),
                    os.path.join(workspace, "References", "Assets"),
                    "references/assets",
                ),
            )
            for out, report, asset_root, error in cases:
                with self.subTest(out=out, asset_root=asset_root):
                    argv = [
                        "--materials", materials,
                        "--out", out,
                        "--report", report,
                    ]
                    if asset_root is not None:
                        argv.extend(["--asset-root", asset_root])
                    args = B.build_arg_parser().parse_args(argv)
                    with self.assertRaisesRegex(ValueError, error):
                        B._publication_workspace(args)
                    self.assertFalse(os.path.lexists(out))
                    self.assertFalse(os.path.lexists(report))
                    if asset_root is not None:
                        self.assertFalse(os.path.lexists(asset_root))

    def test_workspace_mode_rejects_every_noncanonical_builder_json_target(self):
        target_relatives = (
            ".ingest/content_units.jsonl",
            ".ingest/build_manifest.json",
            ".ingest/review_queue.jsonl",
            "references/quiz_bank.json",
            "study_state.json",
            "references/assets/candidate.png",
        )
        for relative in target_relatives:
            with self.subTest(relative), tempfile.TemporaryDirectory() as temp:
                materials = _materials_with_pdf()
                workspace = os.path.join(temp, "workspace")
                assets = os.path.join(workspace, "references", "assets")
                ingest = os.path.join(workspace, ".ingest")
                os.makedirs(assets)
                os.makedirs(ingest)
                target = os.path.join(workspace, *relative.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                sentinel = ("OLD:" + relative).encode("utf-8")
                with open(target, "wb") as stream:
                    stream.write(sentinel)
                report = os.path.join(ingest, "parse_report.json")
                backend = FakeBackend({"ch01.pdf": ["Quiz 1.1 plain prompt"]})
                backend.page_texts = mock.Mock(
                    side_effect=AssertionError("validation must precede parsing")
                )

                code = B.main([
                    "--materials", materials,
                    "--out", target,
                    "--report", report,
                    "--asset-root", assets,
                ], backend=backend)

                self.assertEqual(2, code)
                backend.page_texts.assert_not_called()
                self.assertEqual(sentinel, open(target, "rb").read())
                self.assertFalse(os.path.lexists(report))

    def test_publication_outputs_and_review_manifest_must_be_physically_distinct(self):
        cases = ("same-out-report", "out-is-review-manifest")
        for case in cases:
            with self.subTest(case), tempfile.TemporaryDirectory() as temp:
                materials = _materials_with_pdf()
                report = os.path.join(temp, "parse_report.json")
                out = report if case == "same-out-report" else os.path.join(
                    temp, "ai_review_manifest.json"
                )
                sentinel = b"DO-NOT-REPLACE"
                with open(out, "wb") as stream:
                    stream.write(sentinel)
                backend = FakeBackend({"ch01.pdf": ["Quiz 1.1 plain prompt"]})
                backend.page_texts = mock.Mock(
                    side_effect=AssertionError("validation must precede parsing")
                )

                code = B.main([
                    "--materials", materials, "--out", out, "--report", report,
                ], backend=backend)

                self.assertEqual(2, code)
                backend.page_texts.assert_not_called()
                self.assertEqual(sentinel, open(out, "rb").read())

    def test_official_visual_publication_commits_deferred_assets_with_all_json(self):
        (materials, workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )

        code = B.main([
            "--materials", materials,
            "--out", out,
            "--report", report,
            "--asset-root", assets,
        ], backend=backend)

        self.assertEqual(0, code)
        self.assertTrue(os.path.isfile(os.path.join(assets, name)))
        with open(out, encoding="utf-8") as stream:
            self.assertIsInstance(json.load(stream), dict)
        with open(report, encoding="utf-8") as stream:
            self.assertIsInstance(json.load(stream), dict)
        manifest = os.path.join(
            workspace, ".ingest", "ai_review_manifest.json"
        )
        with open(manifest, encoding="utf-8") as stream:
            self.assertIsInstance(json.load(stream), dict)

    def test_official_visual_publication_rolls_back_every_late_json_failure(self):
        for fail_at in (1, 2, 3):
            with self.subTest(fail_at=fail_at):
                (materials, workspace, assets, out, report,
                 backend, name) = self._visual_publication_fixture()
                root = os.path.dirname(materials)
                try:
                    manifest = os.path.join(
                        workspace, ".ingest", "ai_review_manifest.json"
                    )
                    sentinels = {
                        out: b"OLD-RAW-INPUT",
                        report: b"OLD-PARSE-REPORT",
                        manifest: b"OLD-AI-REVIEW-MANIFEST",
                    }
                    for path, payload in sentinels.items():
                        with open(path, "wb") as stream:
                            stream.write(payload)
                    target = os.path.join(assets, name)
                    self.assertFalse(os.path.lexists(target))

                    real_replace = B._replace_publication_stage
                    call_count = [0]

                    def fail_selected(temporary, destination):
                        call_count[0] += 1
                        if call_count[0] == fail_at:
                            raise OSError(
                                "injected JSON replace failure %d" % fail_at
                            )
                        return real_replace(temporary, destination)

                    with mock.patch.object(
                            B, "_replace_publication_stage",
                            side_effect=fail_selected):
                        code = B.main([
                            "--materials", materials,
                            "--out", out,
                            "--report", report,
                            "--asset-root", assets,
                        ], backend=backend)

                    self.assertEqual(2, code)
                    self.assertEqual(fail_at, call_count[0])
                    for path, payload in sentinels.items():
                        with open(path, "rb") as stream:
                            self.assertEqual(payload, stream.read())
                    self.assertFalse(os.path.lexists(target))
                    leftovers = []
                    for base, _dirs, files in os.walk(workspace):
                        leftovers.extend(
                            os.path.join(base, filename)
                            for filename in files
                            if (".builder-publication." in filename
                                or ".builder-rollback." in filename
                                or filename.endswith(".tmp"))
                        )
                    self.assertEqual([], leftovers)
                finally:
                    shutil.rmtree(root, ignore_errors=True)

    def test_fail_closed_blocker_is_public_before_assets_and_source_json(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "material_build_pending.json")
            raw = os.path.join(temp, "source_raw_input.json")
            report = os.path.join(temp, "parse_report.json")
            old = {
                pending: b"OLD-PENDING",
                raw: b"OLD-RAW",
                report: b"OLD-REPORT",
            }
            for path, payload in old.items():
                with open(path, "wb") as stream:
                    stream.write(payload)
            new_pending = {"status": "pending"}
            new_raw = {"raw": "new"}
            new_report = {"report": "new"}

            def inspect_asset_boundary(_plan, journal=None):
                self.assertEqual(
                    B._publication_json_bytes(new_pending),
                    Path(pending).read_bytes(),
                )
                self.assertEqual(old[raw], Path(raw).read_bytes())
                self.assertEqual(old[report], Path(report).read_bytes())
                return journal

            with mock.patch.object(
                    B, "_publish_asset_plan",
                    side_effect=inspect_asset_boundary):
                B._publish_builder_transaction(
                    ((report, new_report), (raw, new_raw),
                     (pending, new_pending)),
                    asset_plans=({"sentinel": True},),
                    blocker_paths=(pending,),
                )

            self.assertEqual(
                B._publication_json_bytes(new_raw), Path(raw).read_bytes()
            )
            self.assertEqual(
                B._publication_json_bytes(new_report), Path(report).read_bytes()
            )

    def test_failed_non_blocker_rollback_retains_current_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "material_build_pending.json")
            raw = os.path.join(temp, "source_raw_input.json")
            report = os.path.join(temp, "parse_report.json")
            for path, payload in (
                    (pending, b"OLD-PENDING"),
                    (raw, b"OLD-RAW"),
                    (report, b"OLD-REPORT")):
                Path(path).write_bytes(payload)
            new_pending = {"status": "pending", "generation": "new"}
            new_raw = {"raw": "new"}
            new_report = {"report": "new"}
            real_replace = B._replace_publication_stage
            replace_count = [0]

            def fail_after_report(temporary, destination):
                replace_count[0] += 1
                if replace_count[0] == 3:
                    raise OSError("injected forward failure")
                return real_replace(temporary, destination)

            real_restore = B._atomic_restore_publication_bytes

            def fail_report_restore(path, payload):
                if path == report:
                    raise OSError("injected report rollback failure")
                return real_restore(path, payload)

            with mock.patch.object(
                    B, "_replace_publication_stage",
                    side_effect=fail_after_report), mock.patch.object(
                    B, "_atomic_restore_publication_bytes",
                    side_effect=fail_report_restore), self.assertRaisesRegex(
                    OSError, "builder publication rollback failed"):
                B._publish_builder_transaction(
                    ((report, new_report), (raw, new_raw),
                     (pending, new_pending)),
                    blocker_paths=(pending,),
                )

            self.assertEqual(b"OLD-RAW", Path(raw).read_bytes())
            self.assertEqual(
                B._publication_json_bytes(new_report), Path(report).read_bytes()
            )
            self.assertEqual(
                B._publication_json_bytes(new_pending), Path(pending).read_bytes()
            )

    def test_unknown_blocker_path_rejects_without_replacing_any_json(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = os.path.join(temp, "source_raw_input.json")
            missing = os.path.join(temp, "material_build_pending.json")
            with open(raw, "wb") as stream:
                stream.write(b"OLD-RAW")
            with self.assertRaisesRegex(ValueError, "blocker path"):
                B._publish_builder_transaction(
                    ((raw, {"raw": "new"}),),
                    blocker_paths=(missing,),
                )
            self.assertEqual(b"OLD-RAW", Path(raw).read_bytes())
            self.assertFalse(os.path.lexists(missing))

    def test_standalone_builder_refuses_role_migration_without_orchestrator(self):
        with tempfile.TemporaryDirectory() as temp:
            args = types.SimpleNamespace(
                out=os.path.join(temp, "source_raw_input.json"),
                report=os.path.join(temp, "parse_report.json"),
            )

            def fake_run(_args, **kwargs):
                kwargs["_deferred_asset_plans"].append({
                    "role_promotions": ({"path": "legacy.png"},),
                })
                return 0, {"phases": [], "quiz_bank": []}, {
                    "warnings": [], "ai_review": [],
                }

            with mock.patch.object(B, "run", side_effect=fake_run), \
                    mock.patch.object(B, "_publish_builder_transaction") as publish:
                code = B._main_locked(args)

            self.assertEqual(5, code)
            publish.assert_not_called()
            self.assertFalse(os.path.lexists(args.out))
            self.assertFalse(os.path.lexists(args.report))

    def test_asset_publish_raise_after_replace_still_rolls_back_everything(self):
        (materials, workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )
        manifest = os.path.join(
            workspace, ".ingest", "ai_review_manifest.json"
        )
        sentinels = {
            out: b"OLD-RAW-INPUT",
            report: b"OLD-PARSE-REPORT",
            manifest: b"OLD-AI-REVIEW-MANIFEST",
        }
        for path, payload in sentinels.items():
            with open(path, "wb") as stream:
                stream.write(payload)
        target = os.path.join(assets, name)
        real_publish = B._write_new_asset_atomic

        def publish_then_raise(asset_root, asset_name, payload):
            real_publish(asset_root, asset_name, payload)
            raise OSError("injected failure after asset replace")

        with mock.patch.object(
                B, "_write_new_asset_atomic", side_effect=publish_then_raise):
            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", assets,
            ], backend=backend)

        self.assertEqual(2, code)
        self.assertFalse(os.path.lexists(target))
        for path, payload in sentinels.items():
            with open(path, "rb") as stream:
                self.assertEqual(payload, stream.read())

    def test_asset_publisher_raise_after_return_uses_outer_registered_journal(self):
        (materials, workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )
        manifest = os.path.join(
            workspace, ".ingest", "ai_review_manifest.json"
        )
        sentinels = {
            out: b"OLD-RAW-INPUT",
            report: b"OLD-PARSE-REPORT",
            manifest: b"OLD-AI-REVIEW-MANIFEST",
        }
        for path, payload in sentinels.items():
            with open(path, "wb") as stream:
                stream.write(payload)
        real_publish = B._publish_asset_plan

        def publish_then_raise(plan, journal=None):
            real_publish(plan, journal=journal)
            raise OSError("injected failure after asset publisher return")

        with mock.patch.object(
                B, "_publish_asset_plan", side_effect=publish_then_raise):
            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", assets,
            ], backend=backend)

        self.assertEqual(2, code)
        self.assertFalse(os.path.lexists(os.path.join(assets, name)))
        for path, payload in sentinels.items():
            with open(path, "rb") as stream:
                self.assertEqual(payload, stream.read())

    def test_json_stager_raise_after_return_cleans_registered_stages(self):
        (materials, workspace, assets, out, report,
         backend, name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )
        real_stage = B._stage_json_publications

        def stage_then_raise(publications, journal=None):
            real_stage(publications, journal=journal)
            raise OSError("injected failure after JSON stager return")

        with mock.patch.object(
                B, "_stage_json_publications", side_effect=stage_then_raise):
            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", assets,
            ], backend=backend)

        self.assertEqual(2, code)
        self.assertFalse(os.path.lexists(os.path.join(assets, name)))

    def test_json_stager_append_failure_cleans_unregistered_temp(self):
        class BoomList(list):
            def append(self, value):
                raise MemoryError("injected journal append failure")

        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, "out.json")
            journal = {
                "entries": BoomList(),
                "states": {},
                "created_dirs": [],
            }

            with self.assertRaisesRegex(MemoryError, "journal append"):
                B._stage_json_publications(
                    [(target, {"ok": True})], journal=journal
                )

            self.assertEqual([], [
                name for name in os.listdir(temp)
                if ".builder-publication." in name
            ])

    def test_partial_json_parent_creation_is_rollback_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            ancestor = os.path.join(temp, "new-parent")
            target = os.path.join(ancestor, "nested", "out.json")

            def partial_makedirs(_path, exist_ok=False):
                os.mkdir(ancestor)
                raise OSError("injected partial recursive mkdir")

            with mock.patch.object(B.os, "makedirs", side_effect=partial_makedirs):
                with self.assertRaisesRegex(OSError, "partial recursive mkdir"):
                    B._stage_json_publications([(target, {"ok": True})])
            self.assertFalse(os.path.lexists(ancestor))

    def test_stage_cleanup_failure_is_an_explicit_operation_failure(self):
        (materials, _workspace, assets, out, report,
         backend, _name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )
        staged_paths = []

        def fail_cleanup(path, *args, **kwargs):
            staged_paths.append(path)
            raise OSError("injected secure cleanup failure")

        try:
            with mock.patch.object(B.shutil, "rmtree", side_effect=fail_cleanup):
                code = B.main([
                    "--materials", materials,
                    "--out", out,
                    "--report", report,
                    "--asset-root", assets,
                ], backend=backend)
        finally:
            for path in staged_paths:
                shutil.rmtree(path, ignore_errors=True)

        self.assertEqual(2, code)
        self.assertTrue(staged_paths)
        self.assertTrue(any(
            os.path.basename(path).startswith("exam-cram-asset-stage-")
            for path in staged_paths
        ))

    def test_partial_asset_stage_mkdir_failure_cleans_stage_root(self):
        (materials, _workspace, assets, out, report,
         backend, _name) = self._visual_publication_fixture()
        self.addCleanup(
            shutil.rmtree, os.path.dirname(materials), ignore_errors=True
        )
        controlled_parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, controlled_parent, ignore_errors=True)
        stage_base = os.path.join(controlled_parent, "controlled-stage")
        os.mkdir(stage_base)
        stage_root = os.path.join(stage_base, "references", "assets")
        real_makedirs = os.makedirs

        def partial_makedirs(path, exist_ok=False):
            if os.path.abspath(path) == os.path.abspath(stage_root):
                real_makedirs(os.path.dirname(path), exist_ok=True)
                raise OSError("injected partial asset-stage mkdir")
            return real_makedirs(path, exist_ok=exist_ok)

        with mock.patch.object(B.tempfile, "mkdtemp", return_value=stage_base), \
                mock.patch.object(B.os, "makedirs", side_effect=partial_makedirs):
            code = B.main([
                "--materials", materials,
                "--out", out,
                "--report", report,
                "--asset-root", assets,
            ], backend=backend)

        self.assertEqual(2, code)
        self.assertFalse(os.path.lexists(stage_base))

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

            # Establish the full-mode receipt before deliberately holding the
            # publication lock; confirmation itself is a state mutation and is
            # correctly unable to run inside the conflict being tested.
            with _confirmed_full_pair(materials, workspace):
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


def _ingest(raw_input_path, out_dir, materials):
    with _confirmed_full_pair(materials, out_dir):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "ingest.py"),
             "-i", raw_input_path, "-o", out_dir],
            capture_output=True, text=True, encoding="utf-8",
            env=os.environ.copy(),
        )


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
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.dirname(os.path.dirname(args.out))
        r = _ingest(args.out, ws, d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(ws, "references", "quiz_bank.json")))

    def test_asset_fields_survive_into_quiz_bank(self):
        d = _materials_with_pdf()
        be = FakeBackend({"ch01.pdf": ["Quiz 1.1  Shade the Venn diagram at right.", "Quiz 1.1 Solution  s."]})
        args = _args(d)
        code, ri, _ = B.run(args, backend=be)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.dirname(os.path.dirname(args.out))
        self.assertEqual(_ingest(args.out, ws, d).returncode, 0)
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
        code, ri, _ = B.run(args, backend=be)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ri, f, ensure_ascii=False)
        ws = os.path.dirname(os.path.dirname(args.out))
        self.assertEqual(_ingest(args.out, ws, d).returncode, 0)
        V = _validate(ws)
        errors = V.validate(ws)[0]
        self.assertEqual(V._exit_code(errors), 1)  # missing required asset -> fail-closed

    def test_old_handauthored_raw_input_still_works(self):
        d = tempfile.mkdtemp(prefix="old-")
        materials = os.path.join(d, "materials")
        os.makedirs(materials)
        ri = {"course_name": "Old", "phases": [{"phase_num": 1, "phase_name": "P1",
              "wiki_filename": "ch1.md", "wiki_content": "# c"}],
              "quiz_bank": [{"id": "q1", "chapter": 1, "type": "choice", "question": "?",
                             "options": ["A", "B"], "answer": "A", "source": "material"}]}
        p = os.path.join(materials, "raw_input.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ri, f)
        ws = os.path.join(d, "workspace")
        self.assertEqual(_ingest(p, ws, materials).returncode, 0)


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
