# -*- coding: utf-8 -*-
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import study_guide_content as sgc  # noqa: E402


PY = sys.executable
SCRIPT = os.path.join(SCRIPTS, "study_guide_content.py")


def localized(zh, en):
    return {"zh": zh, "en": en}


class StudyGuideContentTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="study-guide-content-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, "references", "assets"))
        self._write_json("study_state.json", {"language": "bilingual"})
        self._write_json("references/teaching_examples.json", [
            {"id": "ex1", "chapter": 1, "teaching_role": "paired_problem",
             "source_type": "lecture", "assets": [{
                 "path": "references/assets/prompt.png", "role": "question_context",
                 "type": "page_image",
             }]},
            {"id": "teaching-ch2", "chapter": 2},
        ])
        self._write_json("references/quiz_bank.json", [
            {"id": "ex1", "chapter": 1, "type": "subjective", "question": "Overlap",
             "answer": "A", "source_type": "lecture"},
            {"id": "q1", "phase": 1, "type": "subjective", "question": "Question",
             "answer": "B", "source_type": "quiz", "assets": [{
                 "path": "references/assets/prompt.png", "role": "figure",
                 "type": "crop_image",
             }]},
            {"id": "legacy-demo", "chapter": 2, "type": "subjective", "question": "Demo",
             "gradable": False},
            {"id": "q-ch2", "chapter": 2, "type": "choice", "question": "Other",
             "answer": "A"},
        ])
        self.asset = "references/assets/prompt.png"
        with open(os.path.join(self.ws, *self.asset.split("/")), "wb") as stream:
            stream.write(b"local prompt asset")
        os.makedirs(os.path.join(self.ws, "notebook"))
        with open(os.path.join(self.ws, "notebook", "ch01.md"), "w", encoding="utf-8") as stream:
            stream.write(
                "# Personal notes\n\n"
                "## [#mine] Keep me\n\nHand-written content.\n\n"
                "## [#ex1] Example ex1\n\n> Walkthrough · 2026-07-14 10:00\n\n"
                "Seven-step walkthrough for ex1.\n\n---\n\n"
                "## [#q1] Example q1\n\n> Walkthrough · 2026-07-14 10:01\n\n"
                "Seven-step walkthrough for q1.\n\n---\n"
            )

    def _write_json(self, relative, value):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        return path

    def _write_jsonl(self, relative, rows):
        path = os.path.join(self.ws, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def _structured_units(self):
        prompt = {
            "path": self.asset, "role": "question_context", "type": "page_image",
            "contains_full_prompt": True,
        }
        return [
            {"unit_id": "sem-heading", "source_file": "course/ch01.pdf", "page": 1,
             "kind": "heading", "chapter_id": "ch01", "provenance": "material",
             "text": "Conditional probability"},
            {"unit_id": "sem-text", "source_file": "course/ch01.pdf", "page": 1,
             "kind": "text", "chapter_id": "ch01", "provenance": "material",
             "text": "Restrict the sample space to B."},
            {"unit_id": "sem-formula", "source_file": "course/ch01.pdf", "page": 2,
             "kind": "formula", "chapter_id": "ch01", "provenance": "material",
             "latex": r"P(A \mid B)=\frac{P(A\cap B)}{P(B)}"},
            {"unit_id": "sem-table", "source_file": "course/ch01.pdf", "page": 3,
             "kind": "table", "chapter_id": "ch01", "provenance": "material",
             "html": "<table><tr><td>A</td></tr></table>"},
            {"unit_id": "sem-code", "source_file": "course/ch01.pdf", "page": 4,
             "kind": "code", "chapter_id": "ch01", "provenance": "material",
             "text": "simulate(A, B)"},
            {"unit_id": "q-ex1", "source_file": "course/ch01.pdf", "page": 5,
             "kind": "question", "chapter_id": "ch01", "provenance": "material",
             "external_id": "ex1", "text": "Given P(A∩B)=0.2 and P(B)=0.5, find P(A|B).",
             "metadata": {"source_type": "lecture", "source_language": "en",
                          "assets": [prompt]}},
            {"unit_id": "a-ex1", "source_file": "course/ch01.pdf", "page": 6,
             "kind": "answer", "chapter_id": "ch01", "provenance": "material",
             "external_id": "ex1", "text": "The answer is 0.4.",
             "metadata": {"source_language": "en"}},
            {"unit_id": "q-q1", "source_file": "course/ch01.pdf", "page": 7,
             "kind": "question", "chapter_id": "ch01", "provenance": "material",
             "external_id": "q1", "text": "Given P(A∩B)=0.2 and P(B)=0.5, find P(A|B).",
             "metadata": {"source_type": "quiz", "source_language": "en"}},
            {"unit_id": "a-q1", "source_file": "course/ch01.pdf", "page": 8,
             "kind": "answer", "chapter_id": "ch01", "provenance": "material",
             "external_id": "q1", "text": "The answer is 0.4.",
             "metadata": {"source_language": "en"}},
            {"unit_id": "q-legacy", "source_file": "course/ch01.pdf", "page": 9,
             "kind": "question", "chapter_id": "ch01", "provenance": "material",
             "external_id": "legacy-demo", "text": "Given P(A∩B)=0.2 and P(B)=0.5, find P(A|B).",
             "metadata": {"source_type": "quiz", "source_language": "en"}},
            {"unit_id": "a-legacy", "source_file": "course/ch01.pdf", "page": 10,
             "kind": "answer", "chapter_id": "ch01", "provenance": "material",
             "external_id": "legacy-demo", "text": "The answer is 0.4.",
             "metadata": {"source_language": "en"}},
            {"unit_id": "q-unit-only", "source_file": "course/ch01.pdf", "page": 11,
             "kind": "question", "chapter_id": "ch01", "provenance": "material",
             "external_id": "unit-only", "text": "Given P(A∩B)=0.2 and P(B)=0.5, find P(A|B).",
             "metadata": {"source_type": "homework", "source_language": "en"}},
            {"unit_id": "a-unit-only", "source_file": "course/ch01.pdf", "page": 12,
             "kind": "answer", "chapter_id": "ch01", "provenance": "material",
             "external_id": "unit-only", "text": "The answer is 0.4.",
             "metadata": {"source_language": "en"}},
            {"unit_id": "other-chapter", "source_file": "course/ch02.pdf", "page": 1,
             "kind": "text", "chapter_id": "ch02", "provenance": "material",
             "text": "Not chapter one."},
        ]

    def _enable_structured(self, units=None):
        rows = copy.deepcopy(units if units is not None else self._structured_units())
        self._write_jsonl(".ingest/content_units.jsonl", rows)
        self._write_json(
            ".ingest/build_manifest.json",
            {"schema_version": 1, "pipeline_version": "ingestion-v1"},
        )
        with open(os.path.join(self.ws, "notebook", "ch01.md"), "a",
                  encoding="utf-8") as stream:
            stream.write(
                "\n## [#legacy-demo] Example legacy-demo\n\n"
                "> Walkthrough · 2026-07-14 10:02\n\n"
                "Seven-step walkthrough for legacy-demo.\n\n---\n\n"
                "## [#unit-only] Example unit-only\n\n"
                "> Walkthrough · 2026-07-14 10:03\n\n"
                "Seven-step walkthrough for unit-only.\n\n---\n"
            )
        return rows

    def _unit_source(self, unit_id, page, role, asset=False, full_prompt=False):
        source = {
            "source_file": "course/ch01.pdf", "pages": [page],
            "source_unit_id": unit_id, "role": role,
        }
        if asset:
            source["asset_path"] = self.asset
        if full_prompt:
            source["contains_full_prompt"] = True
        return source

    def _structured_manifest(self):
        self._enable_structured()
        manifest = self._manifest()
        manifest["semantic_exclusions"] = []
        kp = manifest["knowledge_points"][0]
        kp["source_unit_ids"] = [
            "sem-heading", "sem-text", "sem-formula", "sem-table", "sem-code"]
        kp["source_refs"] = [
            self._unit_source("sem-heading", 1, "concept"),
            self._unit_source("sem-text", 1, "concept"),
            self._unit_source("sem-table", 3, "concept"),
            self._unit_source("sem-code", 4, "concept"),
        ]
        kp["formulas"][0]["source_refs"] = [
            self._unit_source("sem-formula", 2, "formula")]
        kp["example_ids"] = ["ex1", "q1", "legacy-demo", "unit-only"]
        item_units = {
            "ex1": ("q-ex1", 5, "a-ex1", 6),
            "q1": ("q-q1", 7, "a-q1", 8),
            "legacy-demo": ("q-legacy", 9, "a-legacy", 10),
            "unit-only": ("q-unit-only", 11, "a-unit-only", 12),
        }
        walks = []
        for item_id in kp["example_ids"]:
            walk = self._walkthrough(item_id, full_prompt=(item_id == "ex1"))
            question_id, question_page, answer_id, answer_page = item_units[item_id]
            walk["source_trace"] = [
                self._unit_source(
                    question_id, question_page, "question", asset=(item_id == "ex1"),
                    full_prompt=(item_id == "ex1")),
                self._unit_source(answer_id, answer_page, "answer"),
            ]
            if item_id == "unit-only":
                walk["source_type"] = "homework"
            walks.append(walk)
        manifest["walkthroughs"] = walks
        return manifest

    def _source(self, role="question"):
        return {
            "source_file": "course/ch01.pdf",
            "pages": [2],
            "source_unit_id": "unit-ch01-p2",
            "role": role,
        }

    def _formula(self):
        return {
            "id": "f1",
            "latex": r"P(A \mid B)=\frac{P(A\cap B)}{P(B)}",
            "explanation": localized("条件概率公式", "Conditional-probability formula"),
            "variables": [
                {"symbol": "A", "meaning": localized("目标事件", "target event")},
                {"symbol": "B", "meaning": localized("已知条件", "given condition")},
            ],
            "applicability": localized("已知 B 已发生", "B is known to have occurred"),
            "source_refs": [self._source("formula")],
        }

    def _knowledge_point(self):
        return {
            "id": "kp1",
            "title": localized("条件概率", "Conditional probability"),
            "explanation": localized("在已知事件中缩小样本空间。",
                                     "Restrict the sample space to the known event."),
            "formulas": [self._formula()],
            "source_refs": [self._source("concept")],
            "example_ids": ["ex1", "q1"],
        }

    def _walkthrough(self, item_id, full_prompt=False):
        row = {
            "item_id": item_id,
            "answer_provenance": {"zh": "ai_supplemented", "en": "material"},
            "source_type": "lecture" if item_id == "ex1" else "quiz",
            "knowledge_point_ids": ["kp1"],
            "knowledge_point_uses": {
                "kp1": localized("识别条件事件并缩小样本空间。",
                                 "Identify the condition and restrict the sample space."),
            },
            "notebook_anchor": "%s-example-%s" % (item_id, item_id),
            "title": localized("例题 " + item_id, "Example " + item_id),
            "original_language": "en",
            "prompt_asset_mode": (
                "full_prompt" if full_prompt else "figure_only" if item_id == "q1" else "none"),
            "prompt_asset_paths": [self.asset] if (full_prompt or item_id == "q1") else [],
            "answer_asset_paths": [],
            # The English original is already visible. Bilingual mode requires only Chinese.
            "translation": {"zh": "求条件概率。"},
            "what_asked": localized("求给定 B 时 A 的概率。",
                                    "Find the probability of A given B."),
            "known_quantities": [{
                "label": localized("交集概率", "intersection probability"),
                "symbol": "P(A∩B)",
                "value": "0.2",
            }],
            "unknown_quantities": [{
                "label": localized("条件概率", "conditional probability"),
                "symbol": "P(A|B)",
            }],
            "solution_kind": "formula",
            "formula_uses": [{
                "formula_id": "f1",
                "why_applicable": localized("题目给出了条件 B。",
                                             "The prompt supplies condition B."),
                "variable_mapping": [
                    {"symbol": "A", "maps_to": localized("要求的事件", "the requested event")},
                    {"symbol": "B", "maps_to": localized("已发生的事件", "the given event")},
                ],
                "substitution": r"P(A\mid B)=0.2/0.5=0.4",
            }],
            "steps": [
                localized("先确认条件事件 B。", "First identify the conditioning event B."),
                localized("代入并计算 0.4。", "Substitute and calculate 0.4."),
            ],
            "answer": localized("答案是 0.4。", "The answer is 0.4."),
            "self_check": localized("结果在 0 与 1 之间。", "The result lies between 0 and 1."),
            "source_trace": [self._source("question"), self._source("answer")],
        }
        if not full_prompt:
            row["prompt_text"] = "Given P(A∩B)=0.2 and P(B)=0.5, find P(A|B)."
        return row

    def _manifest(self):
        return {
            "schema_version": 1,
            "chapter": 1,
            "language": "bilingual",
            "profile": "full",
            "knowledge_points": [self._knowledge_point()],
            "walkthroughs": [
                self._walkthrough("ex1", full_prompt=True),
                self._walkthrough("q1", full_prompt=False),
            ],
            "omissions": [],
        }

    def _draft(self, manifest=None, name="draft.json"):
        return self._write_json(name, manifest if manifest is not None else self._manifest())

    def assertInvalid(self, manifest, contains=None):
        with self.assertRaises(sgc.ContentError) as stopped:
            sgc.validate_manifest(self.ws, 1, manifest)
        if contains:
            self.assertIn(contains, str(stopped.exception))

    def test_expected_ids_are_teaching_plus_current_quiz_deduplicated(self):
        self.assertEqual(["ex1", "q1"], sgc.expected_item_ids(self.ws, 1))
        self.assertEqual({"ex1": "lecture", "q1": "quiz"},
                         sgc.expected_item_source_types(self.ws, 1))

    def test_legacy_ungradable_quiz_is_required_as_full_teaching_example(self):
        quizzes = [
            {"id": "ex1", "chapter": 1, "type": "subjective", "question": "Overlap",
             "answer": "A", "source_type": "lecture"},
            {"id": "q1", "phase": 1, "type": "subjective", "question": "Question",
             "answer": "B", "source_type": "quiz", "assets": [{
                 "path": self.asset, "role": "figure", "type": "crop_image",
             }]},
            {"id": "legacy-demo", "chapter": 1, "type": "subjective",
             "question": "Worked example without a grading rubric", "gradable": False,
             "source_type": "quiz"},
        ]
        self._write_json("references/quiz_bank.json", quizzes)
        manifest = self._manifest()
        manifest["knowledge_points"][0]["example_ids"].append("legacy-demo")
        legacy_walkthrough = self._walkthrough("legacy-demo")
        manifest["walkthroughs"].append(legacy_walkthrough)
        with open(os.path.join(self.ws, "notebook", "ch01.md"), "a",
                  encoding="utf-8") as stream:
            stream.write(
                "\n## [#legacy-demo] Example legacy-demo\n\n"
                "> Walkthrough · 2026-07-14 10:02\n\n"
                "Seven-step walkthrough for legacy-demo.\n")

        report = sgc.validate_manifest(self.ws, 1, manifest)

        self.assertEqual(["ex1", "q1", "legacy-demo"], report["expected_item_ids"])
        self.assertEqual(["ex1", "q1", "legacy-demo"], report["walkthrough_item_ids"])
        self.assertEqual({
            "teaching": 1, "quiz": 3, "content_unit_questions": 0, "unique": 3,
        }, report["expected_item_counts"])

    def test_structured_denominator_unions_all_quizzes_and_question_units(self):
        manifest = self._structured_manifest()
        report = sgc.validate_manifest(self.ws, 1, manifest)
        self.assertEqual(
            ["ex1", "q1", "legacy-demo", "unit-only"],
            report["expected_item_ids"])
        self.assertEqual({
            "teaching": 1, "quiz": 2, "content_unit_questions": 4, "unique": 4,
        }, report["expected_item_counts"])
        self.assertEqual({
            "ex1": "lecture", "q1": "quiz", "legacy-demo": "quiz",
            "unit-only": "homework",
        }, sgc.expected_item_source_types(self.ws, 1))
        self.assertTrue(report["structured_workspace"])

    def test_ingestion_v2_fails_closed_without_claim_sidecar_and_receipt(self):
        manifest = self._structured_manifest()
        self._write_json(
            ".ingest/build_manifest.json",
            {"schema_version": 1, "pipeline_version": "ingestion-v2"},
        )
        self._write_json(
            ".ingest/source_manifest.json",
            {"schema_version": 1, "sources": []},
        )
        self._write_jsonl(".ingest/canonical_groups.jsonl", [])
        self._write_jsonl(".ingest/source_conflicts.jsonl", [])
        self.assertInvalid(manifest, ".ingest/claim_records.jsonl")

    def test_structured_pipeline_version_cannot_be_deleted_to_bypass_claims(self):
        manifest = self._structured_manifest()
        build_manifest = os.path.join(self.ws, ".ingest", "build_manifest.json")
        os.remove(build_manifest)
        self.assertInvalid(manifest, "explicit pipeline_version")
        self._write_json(".ingest/build_manifest.json", {"schema_version": 1})
        self.assertInvalid(manifest, "pipeline_version is unsupported")

    def test_structured_question_units_require_unique_valid_external_ids(self):
        manifest = self._structured_manifest()

        missing = self._structured_units()
        del next(row for row in missing if row["unit_id"] == "q-unit-only")["external_id"]
        self._write_jsonl(".ingest/content_units.jsonl", missing)
        self.assertInvalid(manifest, "missing external_id")

        invalid = self._structured_units()
        next(row for row in invalid if row["unit_id"] == "q-unit-only")["external_id"] = 7
        self._write_jsonl(".ingest/content_units.jsonl", invalid)
        self.assertInvalid(manifest, "external_id must be a string")

        duplicate = self._structured_units()
        next(row for row in duplicate if row["unit_id"] == "q-unit-only")[
            "external_id"] = "q1"
        self._write_jsonl(".ingest/content_units.jsonl", duplicate)
        self.assertInvalid(manifest, "repeats current-chapter question external_id")

    def test_structured_workspace_requires_teaching_derivative(self):
        manifest = self._structured_manifest()
        os.remove(os.path.join(self.ws, "references", "teaching_examples.json"))
        self.assertInvalid(manifest, "references/teaching_examples.json is missing")

    def test_structured_prompt_text_and_original_language_bind_question_unit(self):
        manifest = self._structured_manifest()
        q1 = next(row for row in manifest["walkthroughs"] if row["item_id"] == "q1")
        q1["prompt_text"] = "A model-authored paraphrase of the source question."
        self.assertInvalid(manifest, "prompt_text must exactly match")

        manifest = self._structured_manifest()
        units = self._structured_units()
        del next(row for row in units if row["unit_id"] == "q-q1")["metadata"][
            "source_language"]
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(manifest, "must explicitly be zh or en")

        units = self._structured_units()
        next(row for row in units if row["unit_id"] == "q-q1")["metadata"][
            "source_language"] = "zh"
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(manifest, "original_language=en disagrees")

    def test_material_formula_latex_is_exactly_bound_to_formula_unit(self):
        manifest = self._structured_manifest()
        manifest["knowledge_points"][0]["formulas"][0]["latex"] = r"E=mc^2"
        self.assertInvalid(manifest, "formula unit latex does not exactly match")

    def test_all_teachable_content_unit_kinds_enter_semantic_denominator(self):
        manifest = self._structured_manifest()
        units = self._structured_units()
        extra_kinds = (
            "title", "list", "figure", "diagram", "caption", "speaker_notes", "other",
        )
        kp = manifest["knowledge_points"][0]
        for offset, kind in enumerate(extra_kinds, 20):
            unit_id = "sem-" + kind
            units.append({
                "unit_id": unit_id, "source_file": "course/ch01.pdf", "page": offset,
                "kind": kind, "chapter_id": "ch01", "provenance": "material",
            })
            kp["source_unit_ids"].append(unit_id)
            kp["source_refs"].append(self._unit_source(unit_id, offset, "concept"))
        self._write_jsonl(".ingest/content_units.jsonl", units)

        report = sgc.validate_manifest(self.ws, 1, manifest)

        self.assertEqual(12, report["semantic_unit_counts"]["expected"])
        for kind in extra_kinds:
            self.assertEqual(1, report["semantic_unit_counts"]["by_kind"][kind])

    def test_full_profile_cannot_exclude_formula_or_use_freeform_semantic_escape(self):
        manifest = self._structured_manifest()
        units = self._structured_units()
        units.append({
            "unit_id": "sem-formula-extra", "source_file": "course/ch01.pdf", "page": 30,
            "kind": "formula", "chapter_id": "ch01", "provenance": "material",
            "latex": r"x=1",
        })
        self._write_jsonl(".ingest/content_units.jsonl", units)
        manifest["semantic_exclusions"] = [{
            "source_unit_id": "sem-formula-extra",
            "reason_code": "outside_assessed_scope",
            "reason": localized("不纳入。", "Excluded."),
            "source_refs": [self._unit_source("sem-code", 4, "concept")],
        }]
        self.assertInvalid(manifest, "cannot exclude a material/ai_recovered formula")

        manifest = self._structured_manifest()
        units = self._structured_units()
        units.append({
            "unit_id": "sem-extra", "source_file": "course/ch01.pdf", "page": 31,
            "kind": "text", "chapter_id": "ch01", "provenance": "material",
            "text": "An additional teaching statement.",
        })
        self._write_jsonl(".ingest/content_units.jsonl", units)
        manifest["semantic_exclusions"] = [{
            "source_unit_id": "sem-extra",
            "reason": localized("随意省略。", "Arbitrarily excluded."),
        }]
        self.assertInvalid(manifest, "missing required keys")

    def test_full_profile_assets_are_an_exact_question_and_answer_side_denominator(self):
        manifest = self._structured_manifest()
        q1 = next(row for row in manifest["walkthroughs"] if row["item_id"] == "q1")
        q1["prompt_asset_mode"] = "none"
        q1["prompt_asset_paths"] = []
        self.assertInvalid(manifest, "must exactly cover every known question-side asset")

        manifest = self._structured_manifest()
        answer_asset = "references/assets/answer.png"
        with open(os.path.join(self.ws, *answer_asset.split("/")), "wb") as stream:
            stream.write(b"answer-side fixture")
        units = self._structured_units()
        next(row for row in units if row["unit_id"] == "a-q1")["metadata"]["assets"] = [{
            "path": answer_asset, "role": "worked_solution", "type": "crop_image",
        }]
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(manifest, "must exactly cover every known answer-side asset")

    def test_full_legacy_visual_dependency_flag_without_asset_blocks_for_ingest_review(self):
        for flag in ("requires_assets", "maybe_requires_assets"):
            with self.subTest(flag=flag):
                quizzes = [
                    {"id": "ex1", "chapter": 1, "question": "Overlap", "answer": "A",
                     "source_type": "lecture"},
                    {"id": "q1", "chapter": 1, "question": "Needs its missing figure",
                     "answer": "B", "source_type": "quiz", flag: True, "assets": []},
                ]
                self._write_json("references/quiz_bank.json", quizzes)
                manifest = self._manifest()
                q1 = next(row for row in manifest["walkthroughs"] if row["item_id"] == "q1")
                q1["prompt_asset_mode"] = "none"
                q1["prompt_asset_paths"] = []
                self.assertInvalid(manifest, "return to ingestion/review")

        quizzes = [
            {"id": "ex1", "chapter": 1, "question": "Overlap", "answer": "A",
             "source_type": "lecture"},
            {"id": "q1", "chapter": 1, "question": "Self-contained text",
             "answer": "B", "source_type": "quiz", "requires_assets": False,
             "maybe_requires_assets": False, "assets": []},
        ]
        self._write_json("references/quiz_bank.json", quizzes)
        allowed = self._manifest()
        q1 = next(row for row in allowed["walkthroughs"] if row["item_id"] == "q1")
        q1["prompt_asset_mode"] = "none"
        q1["prompt_asset_paths"] = []
        self.assertTrue(sgc.validate_manifest(self.ws, 1, allowed)["ok"])

    def test_full_structured_visual_dependency_flag_without_asset_blocks_review(self):
        for flag in ("requires_assets", "maybe_requires_assets"):
            with self.subTest(flag=flag):
                manifest = self._structured_manifest()
                quizzes = [
                    {"id": "ex1", "chapter": 1, "question": "Overlap", "answer": "A",
                     "source_type": "lecture"},
                    {"id": "q1", "chapter": 1, "question": "Needs its missing figure",
                     "answer": "B", "source_type": "quiz", "assets": []},
                ]
                self._write_json("references/quiz_bank.json", quizzes)
                units = self._structured_units()
                next(row for row in units if row["unit_id"] == "q-q1")["metadata"][flag] = True
                self._write_jsonl(".ingest/content_units.jsonl", units)
                q1 = next(row for row in manifest["walkthroughs"] if row["item_id"] == "q1")
                q1["prompt_asset_mode"] = "none"
                q1["prompt_asset_paths"] = []
                self.assertInvalid(manifest, "return to ingestion/review")

    def test_structured_semantic_units_require_exact_mapped_or_excluded_union(self):
        manifest = self._structured_manifest()
        report = sgc.validate_manifest(self.ws, 1, manifest)
        self.assertEqual({
            "expected": 5, "knowledge_point_mapped": 5, "excluded": 0,
            "by_kind": {"code": 1, "formula": 1, "heading": 1, "table": 1,
                        "text": 1},
        }, report["semantic_unit_counts"])

        missing = copy.deepcopy(manifest)
        missing["knowledge_points"][0]["source_unit_ids"].remove("sem-text")
        self.assertInvalid(missing, "missing=['sem-text']")

        overlap = copy.deepcopy(manifest)
        overlap["semantic_exclusions"] = [{
            "source_unit_id": "sem-code",
            "reason_code": "outside_assessed_scope",
            "reason": localized("资料明确标成不考。", "The material marks it as unassessed."),
            "source_refs": [self._unit_source("sem-code", 4, "concept")],
        }]
        self.assertInvalid(overlap, "both knowledge-point evidence and excluded")

        extra = copy.deepcopy(manifest)
        extra["knowledge_points"][0]["source_unit_ids"].append("q-ex1")
        self.assertInvalid(extra, "extra=['q-ex1']")

        not_localized = copy.deepcopy(manifest)
        not_localized["semantic_exclusions"] = [{
            "source_unit_id": "sem-code",
            "reason_code": "outside_assessed_scope",
            "reason": {"zh": "排除"},
            "source_refs": [self._unit_source("sem-code", 4, "concept")],
        }]
        self.assertInvalid(not_localized, "must contain")

    def test_structured_source_refs_bind_exact_unit_file_and_page(self):
        manifest = self._structured_manifest()
        missing_unit = copy.deepcopy(manifest)
        missing_unit["knowledge_points"][0]["source_refs"][0][
            "source_unit_id"] = "not-real"
        self.assertInvalid(missing_unit, "does not exist")

        wrong_file = copy.deepcopy(manifest)
        wrong_file["knowledge_points"][0]["source_refs"][0][
            "source_file"] = "course/other.pdf"
        self.assertInvalid(wrong_file, "source_file disagrees")

        wrong_page = copy.deepcopy(manifest)
        wrong_page["knowledge_points"][0]["source_refs"][0]["pages"] = [99]
        self.assertInvalid(wrong_page, "must exactly equal")

    def test_structured_kp_source_unit_ids_are_derived_from_typed_refs(self):
        manifest = self._structured_manifest()

        unreferenced = copy.deepcopy(manifest)
        unreferenced["knowledge_points"][0]["source_refs"] = unreferenced[
            "knowledge_points"][0]["source_refs"][1:]
        self.assertInvalid(unreferenced, "must exactly equal semantic current-chapter")

        wrong_concept_role = copy.deepcopy(manifest)
        wrong_concept_role["knowledge_points"][0]["source_refs"][0]["role"] = "question"
        self.assertInvalid(wrong_concept_role, "role must equal 'concept'")

        wrong_formula_role = copy.deepcopy(manifest)
        wrong_formula_role["knowledge_points"][0]["formulas"][0][
            "source_refs"][0]["role"] = "concept"
        self.assertInvalid(wrong_formula_role, "role must equal 'formula'")

        question_as_formula = copy.deepcopy(manifest)
        question_as_formula["knowledge_points"][0]["formulas"][0][
            "source_refs"] = [self._unit_source("q-ex1", 5, "formula")]
        self.assertInvalid(question_as_formula, "incompatible with content unit")

        other_chapter = copy.deepcopy(manifest)
        units = self._structured_units()
        units.append({
            "unit_id": "sem-ch2", "source_file": "course/ch02.pdf", "page": 2,
            "kind": "text", "chapter_id": "ch02", "provenance": "material",
            "text": "Other chapter concept.",
        })
        self._write_jsonl(".ingest/content_units.jsonl", units)
        other_chapter["knowledge_points"][0]["source_refs"][0] = {
            "source_file": "course/ch02.pdf", "pages": [2],
            "source_unit_id": "sem-ch2", "role": "concept",
        }
        self.assertInvalid(other_chapter, "must reference a current-chapter content unit")

        ai_supplemented = copy.deepcopy(manifest)
        units = self._structured_units()
        next(row for row in units if row["unit_id"] == "sem-heading")[
            "provenance"] = "ai_supplemented"
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(ai_supplemented, "material or ai_recovered evidence")

        ai_recovered = copy.deepcopy(manifest)
        units = self._structured_units()
        next(row for row in units if row["unit_id"] == "sem-heading")[
            "provenance"] = "ai_recovered"
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertTrue(sgc.validate_manifest(self.ws, 1, ai_recovered)["ok"])

    def test_structured_walkthrough_trace_is_role_and_item_bound(self):
        manifest = self._structured_manifest()

        wrong_question = copy.deepcopy(manifest)
        wrong_question["walkthroughs"][0]["source_trace"][0] = self._unit_source(
            "q-q1", 7, "question")
        self.assertInvalid(wrong_question, "not evidence for current-chapter item 'ex1'")

        concept_as_question = copy.deepcopy(manifest)
        concept_as_question["walkthroughs"][0]["source_trace"][0] = self._unit_source(
            "sem-text", 1, "question")
        self.assertInvalid(concept_as_question, "incompatible with content unit")

        other_items_answer = copy.deepcopy(manifest)
        other_items_answer["walkthroughs"][0]["source_trace"][1] = self._unit_source(
            "a-q1", 8, "answer")
        self.assertInvalid(other_items_answer, "not answer evidence for item 'ex1'")

        concept_as_answer = copy.deepcopy(manifest)
        concept_as_answer["walkthroughs"][0]["source_trace"][1] = self._unit_source(
            "sem-text", 1, "answer")
        self.assertInvalid(concept_as_answer, "incompatible with content unit")

        missing_role = copy.deepcopy(manifest)
        del missing_role["walkthroughs"][0]["source_trace"][0]["role"]
        self.assertInvalid(missing_role, "role is required")

    def test_structured_assets_must_bind_to_item_or_source_trace_side(self):
        manifest = self._structured_manifest()
        rogue_path = os.path.join(self.ws, "references", "assets", "rogue.png")
        with open(rogue_path, "wb") as stream:
            stream.write(b"rogue")
        rogue = copy.deepcopy(manifest)
        rogue["walkthroughs"][0]["prompt_asset_paths"] = [
            "references/assets/rogue.png"]
        self.assertInvalid(rogue, "not bound to a prompt-side source asset")

        wrong_side = copy.deepcopy(manifest)
        wrong_side["walkthroughs"][0]["answer_asset_paths"] = [self.asset]
        self.assertInvalid(wrong_side, "not bound to an answer-side source asset")

    def test_full_prompt_needs_whole_page_or_explicit_source_evidence(self):
        manifest = self._structured_manifest()
        units = self._structured_units()
        for unit in units:
            if unit["unit_id"] == "q-ex1":
                unit["metadata"]["assets"] = [{
                    "path": self.asset, "role": "figure", "type": "crop_image"}]
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self._write_json("references/teaching_examples.json", [
            {"id": "ex1", "chapter": 1, "source_type": "lecture", "assets": [{
                "path": self.asset, "role": "figure", "type": "crop_image"}]},
        ])

        no_proof = copy.deepcopy(manifest)
        question_ref = no_proof["walkthroughs"][0]["source_trace"][0]
        question_ref.pop("contains_full_prompt")
        self.assertInvalid(no_proof, "full_prompt requires")

        # The same crop is acceptable only because a question-bound source ref explicitly says
        # that this source image contains the entire prompt.
        self.assertTrue(sgc.validate_manifest(self.ws, 1, manifest)["ok"])

    def test_structured_solution_kind_and_formula_mapping_are_executable(self):
        manifest = self._structured_manifest()
        missing_kind = copy.deepcopy(manifest)
        del missing_kind["walkthroughs"][0]["solution_kind"]
        self.assertInvalid(missing_kind, "solution_kind")

        no_formula_use = copy.deepcopy(manifest)
        no_formula_use["walkthroughs"][0]["formula_uses"] = []
        self.assertInvalid(no_formula_use, "requires non-empty formula_uses")

        missing_symbol = copy.deepcopy(manifest)
        missing_symbol["walkthroughs"][0]["formula_uses"][0][
            "variable_mapping"].pop()
        self.assertInvalid(missing_symbol, "must exactly cover formula")

        non_formula = copy.deepcopy(manifest)
        walk = non_formula["walkthroughs"][0]
        walk["solution_kind"] = "procedure"
        walk["formula_uses"] = []
        walk["no_formula_reason"] = localized(
            "按定义逐步分类即可。", "Classify step by step from the definition.")
        self.assertTrue(sgc.validate_manifest(self.ws, 1, non_formula)["ok"])

        missing_reason = copy.deepcopy(non_formula)
        del missing_reason["walkthroughs"][0]["no_formula_reason"]
        self.assertInvalid(missing_reason, "no_formula_reason is required")

        missing_locale = copy.deepcopy(non_formula)
        missing_locale["walkthroughs"][0]["no_formula_reason"] = {"zh": "按定义"}
        self.assertInvalid(missing_locale, "must contain")

    def test_structured_answer_provenance_is_per_rendered_language(self):
        manifest = self._structured_manifest()
        as_string = copy.deepcopy(manifest)
        as_string["walkthroughs"][0]["answer_provenance"] = "material"
        self.assertInvalid(as_string, "per-language object")

        missing_en = copy.deepcopy(manifest)
        missing_en["walkthroughs"][0]["answer_provenance"] = {
            "zh": "ai_supplemented"}
        self.assertInvalid(missing_en, "missing=['en']")

        translated_as_material = copy.deepcopy(manifest)
        translated_as_material["walkthroughs"][0]["answer_provenance"] = {
            "zh": "material", "en": "material"}
        self.assertInvalid(translated_as_material, "material only for language")

        missing_answer_language = copy.deepcopy(manifest)
        units = self._structured_units()
        del next(row for row in units if row["unit_id"] == "a-ex1")["metadata"][
            "source_language"]
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(missing_answer_language, "unsupported=['en']")

        mismatched_answer = self._structured_manifest()
        mismatched_answer["walkthroughs"][0]["answer"]["en"] = "0.4"
        self.assertInvalid(mismatched_answer, "must exactly match")

    def test_answer_language_does_not_treat_latin_abbreviations_as_translation(self):
        manifest = self._structured_manifest()
        units = self._structured_units()
        question = next(row for row in units if row["unit_id"] == "q-ex1")
        question["metadata"]["source_language"] = "zh"
        answer = next(row for row in units if row["unit_id"] == "a-ex1")
        answer["text"] = "CPU 利用率为 40%。"
        answer["metadata"]["source_language"] = "zh"
        self._write_jsonl(".ingest/content_units.jsonl", units)
        walk = manifest["walkthroughs"][0]
        walk["original_language"] = "zh"
        walk["translation"] = {"en": "Find the CPU utilization."}
        walk["answer"]["zh"] = "CPU 利用率为 40%。"
        walk["answer_provenance"] = {"zh": "material", "en": "material"}
        self.assertInvalid(manifest, "unsupported=['en']")

        walk["answer_provenance"] = {"zh": "material", "en": "ai_supplemented"}
        self.assertTrue(sgc.validate_manifest(self.ws, 1, manifest)["ok"])

    def test_mixed_or_unknown_answer_needs_explicit_unit_source_language(self):
        manifest = self._structured_manifest()
        units = self._structured_units()
        del next(row for row in units if row["unit_id"] == "a-ex1")["metadata"][
            "source_language"]
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(manifest, "unsupported=['en']")

        units = self._structured_units()
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertTrue(sgc.validate_manifest(self.ws, 1, manifest)["ok"])

        invalid = self._structured_manifest()
        units = self._structured_units()
        next(row for row in units if row["unit_id"] == "a-ex1")["metadata"][
            "source_language"] = "mixed"
        self._write_jsonl(".ingest/content_units.jsonl", units)
        self.assertInvalid(invalid, "metadata.source_language must be zh or en")

    def test_import_requires_preexisting_official_walkthrough_evidence(self):
        draft = self._draft()
        notebook = os.path.join(self.ws, "notebook", "ch01.md")

        with open(notebook, "w", encoding="utf-8") as stream:
            stream.write("")
        with self.assertRaisesRegex(sgc.ContentError, "pre-existing notebook.py walkthrough"):
            sgc.import_manifest(self.ws, 1, draft)

        with open(notebook, "w", encoding="utf-8") as stream:
            stream.write(
                "## [#ex1] Example ex1\n\n"
                "Generated manifest prose without the official walkthrough metadata.\n\n"
                "## [#q1] Example q1\n\nGenerated prose.\n")
        with self.assertRaisesRegex(sgc.ContentError, "pre-existing notebook.py walkthrough"):
            sgc.import_manifest(self.ws, 1, draft)
        self.assertFalse(os.path.exists(os.path.join(
            self.ws, "notebook", "ch01.guide.json")))

    def test_legacy_validation_also_requires_preexisting_walkthrough_anchors(self):
        notebook = os.path.join(self.ws, "notebook", "ch01.md")
        with open(notebook, "w", encoding="utf-8") as stream:
            stream.write("")
        self.assertInvalid(self._manifest(), "pre-existing notebook.py walkthrough")

    def test_notebook_parent_symlink_or_reparse_is_rejected_when_supported(self):
        outside = tempfile.mkdtemp(prefix="outside-notebook-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        source = os.path.join(self.ws, "notebook", "ch01.md")
        shutil.copy2(source, os.path.join(outside, "ch01.md"))
        shutil.rmtree(os.path.join(self.ws, "notebook"))
        try:
            os.symlink(outside, os.path.join(self.ws, "notebook"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation is unavailable")
        self.assertInvalid(self._manifest(), "symlink/junction/reparse")

    def test_walkthrough_source_type_is_required_canonical_and_source_consistent(self):
        missing = self._manifest()
        del missing["walkthroughs"][0]["source_type"]
        self.assertInvalid(missing, "source_type")

        unknown = self._manifest()
        unknown["walkthroughs"][0]["source_type"] = "slides_or_something"
        self.assertInvalid(unknown, "must be one of")

        mismatch = self._manifest()
        mismatch["walkthroughs"][0]["source_type"] = "homework"
        self.assertInvalid(mismatch, "disagrees with source manifests")

        legacy_manifest_value = self._manifest()
        legacy_manifest_value["walkthroughs"][1]["source_type"] = "lecture_quiz"
        self.assertInvalid(legacy_manifest_value, "canonical value")

    def test_legacy_source_tags_normalize_but_duplicate_layer_conflicts_fail(self):
        teaching = [{
            "id": "ex1", "chapter": 1, "source_type": "example",
            "assets": [{"path": self.asset, "role": "question_context",
                        "type": "page_image"}],
        }]
        quizzes = [
            {"id": "ex1", "chapter": 1, "source_type": "example"},
            {"id": "q1", "chapter": 1, "source_type": "lecture_quiz", "assets": [{
                "path": self.asset, "role": "figure", "type": "crop_image",
            }]},
        ]
        self._write_json("references/teaching_examples.json", teaching)
        self._write_json("references/quiz_bank.json", quizzes)
        self.assertEqual({"ex1": "lecture", "q1": "quiz"},
                         sgc.expected_item_source_types(self.ws, 1))
        sgc.validate_manifest(self.ws, 1, self._manifest())

        teaching[0]["source_type"] = "homework"
        self._write_json("references/teaching_examples.json", teaching)
        with self.assertRaisesRegex(sgc.ContentError, "source_type conflict"):
            sgc.validate_manifest(self.ws, 1, self._manifest())

    def test_full_manifest_validates_exact_coverage_and_formula_links(self):
        report = sgc.validate_manifest(self.ws, 1, self._manifest())
        self.assertTrue(report["ok"])
        self.assertEqual("full", report["profile"])
        self.assertEqual(["ex1", "q1"], report["expected_item_ids"])
        self.assertEqual(["ex1", "q1"], report["walkthrough_item_ids"])
        self.assertEqual([], report["omitted_item_ids"])

    def test_full_rejects_missing_extra_or_omitted_items(self):
        missing = self._manifest()
        missing["walkthroughs"].pop()
        self.assertInvalid(missing, "exact walkthrough coverage")

        extra = self._manifest()
        extra["walkthroughs"][1]["item_id"] = "not-in-sources"
        extra["walkthroughs"][1]["prompt_asset_mode"] = "none"
        extra["walkthroughs"][1]["prompt_asset_paths"] = []
        self.assertInvalid(extra, "not a current-chapter")

        omitted = self._manifest()
        omitted["walkthroughs"].pop()
        omitted["omissions"] = [{
            "item_id": "q1",
            "knowledge_point_ids": ["kp1"],
            "reason": localized("省略", "Omitted"),
            "source_refs": [self._source()],
        }]
        self.assertInvalid(omitted, "profile=full")

    def test_abridged_requires_exact_reasoned_omission_partition(self):
        manifest = self._manifest()
        manifest["profile"] = "abridged"
        manifest["walkthroughs"] = [manifest["walkthroughs"][0]]
        manifest["omissions"] = [{
            "item_id": "q1",
            "knowledge_point_ids": ["kp1"],
            "reason": localized("一日速读版省略。", "Omitted from the one-day fast route."),
            "source_refs": [self._source()],
        }]
        report = sgc.validate_manifest(self.ws, 1, manifest)
        self.assertEqual(["q1"], report["omitted_item_ids"])

        no_ledger = copy.deepcopy(manifest)
        no_ledger["omissions"] = []
        self.assertInvalid(no_ledger, "requires at least one explicit omission")

        unaccounted = copy.deepcopy(manifest)
        unaccounted["omissions"][0]["item_id"] = "ex1"
        self.assertInvalid(unaccounted)

    def test_knowledge_point_links_and_formula_uses_are_bidirectional(self):
        bad_link = self._manifest()
        bad_link["walkthroughs"][0]["knowledge_point_ids"] = ["unknown-kp"]
        bad_link["walkthroughs"][0]["knowledge_point_uses"] = {
            "unknown-kp": localized("未知知识点", "Unknown knowledge point")}
        self.assertInvalid(bad_link, "disagree with knowledge point links")

        bad_formula = self._manifest()
        bad_formula["walkthroughs"][0]["formula_uses"][0]["formula_id"] = "f-other"
        self.assertInvalid(bad_formula, "outside its knowledge points")

        duplicate = self._manifest()
        duplicate["walkthroughs"][1]["item_id"] = "ex1"
        self.assertInvalid(duplicate, "not unique")

    def test_bilingual_agent_authored_blocks_require_both_languages(self):
        fields = [
            ("knowledge title", lambda m: m["knowledge_points"][0].__setitem__("title", {"zh": "条件概率"})),
            ("what asked", lambda m: m["walkthroughs"][0].__setitem__("what_asked", {"zh": "求概率"})),
            ("answer", lambda m: m["walkthroughs"][0].__setitem__("answer", {"zh": "0.4"})),
            ("step", lambda m: m["walkthroughs"][0]["steps"].__setitem__(0, {"zh": "第一步"})),
            ("mapping", lambda m: m["walkthroughs"][0]["formula_uses"][0]["variable_mapping"][0].__setitem__("maps_to", {"zh": "目标"})),
        ]
        for label, mutate in fields:
            with self.subTest(label=label):
                manifest = self._manifest()
                mutate(manifest)
                self.assertInvalid(manifest, "must contain")

    def test_explanation_provenance_exactly_labels_authored_languages(self):
        missing = self._manifest()
        missing["knowledge_points"][0]["explanation_provenance"] = {
            "en": "material"
        }
        self.assertInvalid(missing, "must label every authored explanation language")

        unknown = self._manifest()
        unknown["knowledge_points"][0]["explanation_provenance"] = {
            "zh": "ai_generated", "en": "material"
        }
        self.assertInvalid(unknown, "explanation_provenance.zh must be one of")

        labelled = self._manifest()
        labelled["knowledge_points"][0]["explanation_provenance"] = {
            "zh": "ai_translation", "en": "material"
        }
        self.assertTrue(sgc.validate_manifest(self.ws, 1, labelled)["ok"])

    def test_answer_provenance_is_required_and_material_needs_answer_evidence(self):
        missing = self._manifest()
        del missing["walkthroughs"][0]["answer_provenance"]
        self.assertInvalid(missing, "missing required keys")

        unknown = self._manifest()
        unknown["walkthroughs"][0]["answer_provenance"] = {
            "zh": "official-ish", "en": "material"}
        self.assertInvalid(unknown, "answer_provenance")

        no_answer_ref = self._manifest()
        no_answer_ref["walkthroughs"][0]["source_trace"] = [self._source("question")]
        self.assertInvalid(no_answer_ref, "requires an answer/solution source ref")

        ai = self._manifest()
        ai["walkthroughs"][0]["answer_provenance"] = {
            "zh": "ai_generated", "en": "ai_generated"}
        ai["walkthroughs"][0]["source_trace"] = [self._source("question")]
        self.assertTrue(sgc.validate_manifest(self.ws, 1, ai)["ok"])

    def test_full_prompt_uses_image_plus_only_missing_language_translation(self):
        manifest = self._manifest()
        # Valid fixture has no repeated English prompt and exactly the missing Chinese translation.
        sgc.validate_manifest(self.ws, 1, manifest)

        repeated_prompt = self._manifest()
        repeated_prompt["walkthroughs"][0]["prompt_text"] = "Repeated English original"
        self.assertInvalid(repeated_prompt, "must be omitted")

        missing_translation = self._manifest()
        missing_translation["walkthroughs"][0]["translation"] = {}
        self.assertInvalid(missing_translation, "missing target language")

        repeated_original_language = self._manifest()
        repeated_original_language["walkthroughs"][0]["translation"]["en"] = "Duplicate"
        self.assertInvalid(repeated_original_language, "already-visible")

    def test_relocalize_is_reversible_and_notebook_view_follows_state_language(self):
        draft = self._draft()
        sgc.import_manifest(self.ws, 1, draft)

        guide = os.path.join(self.ws, "study_guide")
        qa = os.path.join(guide, "qa")
        os.makedirs(qa)
        stale = (
            "ch01.html", "ch01.pdf", "ch01.receipt.json",
            "qa/ch01_p001.png", "qa/ch01_p017.png",
        )
        keep = ("ch02.html", "ch02.pdf", "ch02.receipt.json", "qa/ch02_p001.png",
                "qa/notes.png")
        for relative in stale + keep:
            path = os.path.join(guide, *relative.split("/"))
            with open(path, "wb") as stream:
                stream.write(b"stale artifact")

        self._write_json("study_state.json", {"language": "en"})
        report = sgc.relocalize_manifest(self.ws, 1, "en")
        self.assertEqual("bilingual", report["relocalized_from"])
        self.assertEqual(
            {"study_guide/" + relative for relative in stale},
            set(report["invalidated_artifacts"]))
        for relative in stale:
            self.assertFalse(os.path.exists(os.path.join(guide, *relative.split("/"))))
        for relative in keep:
            self.assertTrue(os.path.isfile(os.path.join(guide, *relative.split("/"))))
        with open(os.path.join(self.ws, "notebook", "ch01.guide.json"),
                  encoding="utf-8") as stream:
            manifest = json.load(stream)
        self.assertEqual("en", manifest["language"])
        self.assertEqual({"zh": "求条件概率。"},
                         manifest["walkthroughs"][0]["translation"])
        with open(os.path.join(self.ws, "notebook", "ch01.md"),
                  encoding="utf-8") as stream:
            notebook = stream.read()
        begin, end, _header = sgc._markers(1)
        block = notebook.split(begin, 1)[1].split(end, 1)[0]
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", block), block)

        self._write_json("study_state.json", {"language": "bilingual"})
        back = sgc.relocalize_manifest(self.ws, 1, "bilingual")
        self.assertEqual("en", back["relocalized_from"])
        with open(os.path.join(self.ws, "notebook", "ch01.guide.json"),
                  encoding="utf-8") as stream:
            restored = json.load(stream)
        self.assertEqual("bilingual", restored["language"])
        self.assertEqual({"zh": "求条件概率。"},
                         restored["walkthroughs"][0]["translation"])

    def test_ingestion_v2_relocalize_prepares_unsigned_staging_draft(self):
        manifest = self._structured_manifest()
        self._write_json("notebook/ch01.guide.json", manifest)
        self._write_json("study_state.json", {"language": "en"})
        self._write_json(
            ".ingest/build_manifest.json",
            {"schema_version": 1, "pipeline_version": "ingestion-v2"},
        )

        with self.assertRaisesRegex(sgc.ContentError, "requires --output staging JSON"):
            sgc.relocalize_manifest(self.ws, 1, "en")
        for protected in (
            "study_state.json",
            ".ingest/source_manifest.json",
            "notebook/ch02.en.draft.json",
            "notebook/ch01.guide.json",
        ):
            with self.subTest(output=protected), self.assertRaisesRegex(
                    sgc.ContentError, "must match"):
                sgc.relocalize_manifest(self.ws, 1, "en", protected)
        report = sgc.relocalize_manifest(
            self.ws, 1, "en", "notebook/ch01.en.draft.json"
        )
        self.assertTrue(report["prepared"])
        self.assertFalse(report["imported"])
        self.assertFalse(report["artifact_ready"])
        self.assertEqual("pending", report["claim_verification"]["status"])
        self.assertEqual(
            "location_only", report["claim_verification"]["verification_scope"]
        )
        with open(report["staging_path"], encoding="utf-8") as stream:
            staged = json.load(stream)
        with open(os.path.join(self.ws, "notebook", "ch01.guide.json"),
                  encoding="utf-8") as stream:
            canonical = json.load(stream)
        self.assertEqual("en", staged["language"])
        self.assertEqual("bilingual", canonical["language"])

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                0,
                sgc.run([
                    "--workspace", self.ws, "relocalize", "--chapter", "1",
                    "--language", "en", "--output", "notebook/ch01.cli.draft.json",
                ]),
            )
        self.assertIn("unsigned staging prepared", stdout.getvalue())
        self.assertIn("claim verification pending", stdout.getvalue())
        self.assertNotIn("content valid", stdout.getvalue())

    def test_single_language_matching_original_needs_no_translation(self):
        self._write_json("study_state.json", {"language": "en"})
        manifest = self._manifest()
        manifest["language"] = "en"
        for walk in manifest["walkthroughs"]:
            walk["translation"] = {}
        report = sgc.validate_manifest(self.ws, 1, manifest)
        self.assertEqual("en", report["language"])

    def test_figure_only_and_none_prompt_contracts(self):
        figure = self._manifest()
        walk = figure["walkthroughs"][1]
        walk["prompt_asset_mode"] = "figure_only"
        walk["prompt_asset_paths"] = [self.asset]
        sgc.validate_manifest(self.ws, 1, figure)

        no_text = copy.deepcopy(figure)
        del no_text["walkthroughs"][1]["prompt_text"]
        self.assertInvalid(no_text, "prompt_text is required")

        none_with_asset = self._manifest()
        none_with_asset["walkthroughs"][1]["prompt_asset_mode"] = "none"
        self.assertInvalid(none_with_asset, "must be empty")

    def test_assets_must_exist_locally_under_references_assets(self):
        missing = self._manifest()
        missing["walkthroughs"][0]["prompt_asset_paths"] = ["references/assets/missing.png"]
        self.assertInvalid(missing, "missing or unreadable")

        traversal = self._manifest()
        traversal["walkthroughs"][0]["prompt_asset_paths"] = ["references/assets/../prompt.png"]
        self.assertInvalid(traversal, "unsafe path component")

        outside_prefix = self._manifest()
        outside_prefix["walkthroughs"][0]["prompt_asset_paths"] = ["notebook/prompt.png"]
        self.assertInvalid(outside_prefix, "references/assets")

        unsafe_source = self._manifest()
        unsafe_source["knowledge_points"][0]["source_refs"][0]["source_file"] = "../outside.pdf"
        self.assertInvalid(unsafe_source, "unsafe path component")

    def test_symlink_asset_is_rejected_when_supported(self):
        target = os.path.join(self.ws, "target.png")
        link = os.path.join(self.ws, "references", "assets", "link.png")
        with open(target, "wb") as stream:
            stream.write(b"x")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        manifest = self._manifest()
        manifest["walkthroughs"][0]["prompt_asset_paths"] = ["references/assets/link.png"]
        self.assertInvalid(manifest, "symlink/reparse")

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_control_characters(self):
        duplicate = os.path.join(self.ws, "duplicate.json")
        with open(duplicate, "w", encoding="utf-8") as stream:
            stream.write('{"schema_version": 1, "schema_version": 1}')
        with self.assertRaisesRegex(sgc.ContentError, "duplicate JSON key"):
            sgc.load_and_validate_manifest(self.ws, 1, duplicate)

        nonfinite = os.path.join(self.ws, "nonfinite.json")
        with open(nonfinite, "w", encoding="utf-8") as stream:
            stream.write('{"x": NaN}')
        with self.assertRaisesRegex(sgc.ContentError, "non-finite"):
            sgc.load_and_validate_manifest(self.ws, 1, nonfinite)

        nul = self._manifest()
        nul["knowledge_points"][0]["explanation"]["zh"] += "\x00hidden"
        self.assertInvalid(nul, "U+0000")

        replacement = self._manifest()
        replacement["walkthroughs"][0]["answer"]["en"] += "\ufffd"
        self.assertInvalid(replacement, "U+FFFD")

    def test_unknown_schema_keys_and_language_drift_fail_closed(self):
        unknown = self._manifest()
        unknown["generated_by"] = "agent"
        self.assertInvalid(unknown, "unknown keys")

        drifted = self._manifest()
        drifted["language"] = "en"
        self.assertInvalid(drifted, "does not match study_state")

    def test_import_updates_one_bounded_notebook_block_then_writes_json(self):
        manifest = self._manifest()
        manifest["knowledge_points"][0]["explanation_provenance"] = {
            "zh": "ai_translation", "en": "material"
        }
        draft = self._draft(manifest)
        calls = []
        original = sgc._atomic_write_text

        def recording_write(path, text, before_publish=None):
            calls.append(path)
            return original(path, text, before_publish=before_publish)

        with mock.patch.object(sgc, "_atomic_write_text", side_effect=recording_write):
            report = sgc.import_manifest(self.ws, 1, draft)
        self.assertTrue(report["imported"])
        self.assertTrue(calls[0].endswith("ch01.md"), calls)
        self.assertTrue(calls[1].endswith("ch01.guide.json"), calls)

        notebook_path = os.path.join(self.ws, "notebook", "ch01.md")
        with open(notebook_path, "r", encoding="utf-8") as stream:
            notebook = stream.read()
        begin, end, header = sgc._markers(1)
        self.assertIn("Hand-written content.", notebook)
        self.assertIn(r"$P(A\mid B)=0.2/0.5=0.4$", notebook)
        self.assertIn("**例题来源类型 / Source type:** `lecture`", notebook)
        self.assertIn(
            "**答案来源性质 / Answer provenance (中文):** `ai_supplemented`", notebook)
        self.assertIn(
            "**答案来源性质 / Answer provenance (English):** `material`", notebook)
        self.assertIn("AI翻译", notebook)
        self.assertIn("From your materials", notebook)
        self.assertIn("**例题来源类型 / Source type:** `quiz`", notebook)
        self.assertEqual(1, notebook.count(header))
        self.assertEqual(1, notebook.count(begin))
        self.assertEqual(1, notebook.count(end))
        self.assertLess(notebook.index(begin), notebook.index("Knowledge points"))
        self.assertLess(notebook.index("Knowledge points"), notebook.index(end))

        updated = self._manifest()
        updated["knowledge_points"][0]["explanation"]["en"] = "Updated explanation."
        sgc.import_manifest(self.ws, 1, self._draft(updated, "updated.json"))
        with open(notebook_path, "r", encoding="utf-8") as stream:
            notebook2 = stream.read()
        self.assertIn("Updated explanation.", notebook2)
        self.assertIn("Hand-written content.", notebook2)
        self.assertEqual(1, notebook2.count(begin))
        with open(report["manifest_path"], "r", encoding="utf-8") as stream:
            persisted = json.load(stream)
        self.assertEqual("Updated explanation.", persisted["knowledge_points"][0]["explanation"]["en"])

        # The reserved marker is its own notebook block, so later official notebook writes cannot
        # accidentally attach it to and replace an adjacent walkthrough.
        notebook_cli = os.path.join(SCRIPTS, "notebook.py")
        added = subprocess.run(
            [PY, notebook_cli, "--workspace", self.ws, "add-entry", "--chapter", "1",
             "--type", "walkthrough", "--id", "later-note"],
            input="Later tutor note.", capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(0, added.returncode, added.stdout + added.stderr)
        with open(notebook_path, "r", encoding="utf-8") as stream:
            after_notebook_write = stream.read()
        self.assertIn("Updated explanation.", after_notebook_write)
        self.assertEqual(1, after_notebook_write.count(begin))
        self.assertEqual(1, after_notebook_write.count(end))

    def test_failed_notebook_write_never_attempts_json_publication(self):
        draft = self._draft()
        with mock.patch.object(sgc, "_atomic_write_text", side_effect=OSError("disk full")) as writer:
            with self.assertRaisesRegex(sgc.ContentError, "cannot atomically publish"):
                sgc.import_manifest(self.ws, 1, draft)
        self.assertEqual(1, writer.call_count)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "notebook", "ch01.guide.json")))

    def test_v2_fact_snapshot_is_rechecked_at_manifest_publication(self):
        manifest = self._manifest()
        expected = {"schema_version": 1, "token": "bound"}
        report = {
            "claim_verification": {"fact_integrity": expected},
        }
        with mock.patch.object(
                sgc,
                "validate_workspace_fact_integrity",
                return_value={
                    "snapshot": {"schema_version": 1, "token": "drifted"},
                },
        ), self.assertRaisesRegex(sgc.ContentError, "fact inputs changed"):
            sgc._publish_manifest(self.ws, 1, manifest, report)
        self.assertFalse(os.path.exists(
            os.path.join(self.ws, "notebook", "ch01.guide.json")
        ))

    def test_malformed_marker_blocks_import_without_publishing_json(self):
        begin, _end, header = sgc._markers(1)
        with open(os.path.join(self.ws, "notebook", "ch01.md"), "a", encoding="utf-8") as stream:
            stream.write("\n%s\n\n%s\nbroken\n" % (header, begin))
        with self.assertRaisesRegex(sgc.ContentError, "unbalanced"):
            sgc.import_manifest(self.ws, 1, self._draft())
        self.assertFalse(os.path.exists(os.path.join(self.ws, "notebook", "ch01.guide.json")))

    def test_validate_and_import_cli_return_machine_receipts(self):
        draft = self._draft()
        validated = subprocess.run(
            [PY, SCRIPT, "--workspace", self.ws, "validate", "--chapter", "1",
             "--input", draft, "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(0, validated.returncode, validated.stdout + validated.stderr)
        validation_receipt = json.loads(validated.stdout)
        self.assertTrue(validation_receipt["ok"])
        self.assertEqual("validate", validation_receipt["command"])

        imported = subprocess.run(
            [PY, SCRIPT, "--workspace", self.ws, "import", "--chapter", "1",
             "--input", draft, "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(0, imported.returncode, imported.stdout + imported.stderr)
        import_receipt = json.loads(imported.stdout)
        self.assertTrue(import_receipt["imported"])
        self.assertEqual("import", import_receipt["command"])

        invalid = self._manifest()
        invalid["walkthroughs"].pop()
        invalid_path = self._draft(invalid, "invalid.json")
        failed = subprocess.run(
            [PY, SCRIPT, "--workspace", self.ws, "validate", "--chapter", "1",
             "--input", invalid_path, "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(1, failed.returncode)
        self.assertFalse(json.loads(failed.stdout)["ok"])
        self.assertNotIn("Traceback", failed.stderr)


if __name__ == "__main__":
    unittest.main()
