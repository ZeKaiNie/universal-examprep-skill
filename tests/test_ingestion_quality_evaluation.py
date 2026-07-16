import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.ingestion.evaluation import (
    EvaluationSchemaError,
    evaluate,
    evaluate_files,
    validate_gold,
    validate_prediction,
)
from scripts.ingestion.quality import QualityInputError, assess_page, score_page


def clean_signals(**overrides):
    signals = {
        "page": 1,
        "text": "A clean paragraph about Fourier transforms and sampling theory.",
        "image_count": 0,
        "image_area_ratio": 0.0,
        "vector_count": 0,
        "multi_column_hint": False,
        "table_hint": False,
        "formula_hint": False,
    }
    signals.update(overrides)
    return signals


def gold_fixture():
    return {
        "schema_version": 1,
        "sources": [
            {"source_id": "s1", "pages": [1, 2]},
            {"source_id": "s2", "pages": [1]},
        ],
        "units": [
            {
                "unit_id": "c1",
                "source_id": "s1",
                "page": 1,
                "chapter_id": "ch01",
                "kind": "concept",
                "provenance": "material",
                "requires_visual": False,
            },
            {
                "unit_id": "f1",
                "source_id": "s1",
                "page": 1,
                "chapter_id": "ch01",
                "kind": "formula",
                "provenance": "material",
                "requires_visual": True,
            },
            {
                "unit_id": "e1",
                "source_id": "s1",
                "page": 2,
                "chapter_id": "ch01",
                "kind": "example",
                "provenance": "ai_recovered",
                "requires_visual": False,
            },
            {
                "unit_id": "q1",
                "source_id": "s1",
                "page": 2,
                "chapter_id": "ch01",
                "kind": "question",
                "provenance": "material",
                "requires_visual": True,
            },
            {
                "unit_id": "a1",
                "source_id": "s1",
                "page": 2,
                "chapter_id": "ch01",
                "kind": "answer",
                "provenance": "material",
                "requires_visual": False,
            },
            {
                "unit_id": "t1",
                "source_id": "s2",
                "page": 1,
                "chapter_id": "ch02",
                "kind": "table",
                "provenance": "material",
                "requires_visual": True,
            },
            {
                "unit_id": "g1",
                "source_id": "s2",
                "page": 1,
                "chapter_id": "ch02",
                "kind": "figure",
                "provenance": "material",
                "requires_visual": True,
            },
        ],
        "qa_pairs": [{"question_id": "q1", "answer_id": "a1"}],
        "retrieval_queries": [
            {"query_id": "r1", "relevant_unit_ids": ["q1"]},
            {"query_id": "r2", "relevant_unit_ids": ["f1", "t1"]},
        ],
    }


def predicted_unit(
    unit_id,
    kind,
    page=1,
    chapter_id="ch01",
    provenance="material",
    requires_visual=False,
    asset_role=None,
    asset_path=None,
    asset_sha256=None,
    exposed=False,
):
    return {
        "unit_id": unit_id,
        "source_id": "s1",
        "page": page,
        "chapter_id": chapter_id,
        "kind": kind,
        "provenance": provenance,
        "requires_visual": requires_visual,
        "asset_role": asset_role,
        "asset_path": asset_path,
        "asset_sha256": asset_sha256,
        "exposed_in_question": exposed,
    }


def asset_digest(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def prediction_fixture():
    return {
        "schema_version": 1,
        "sources": [
            {"source_id": "s1", "status": "parsed", "accounted_pages": [1, 2]},
        ],
        "units": [
            predicted_unit("c1", "concept"),
            predicted_unit(
                "f1",
                "table",
                chapter_id="ch99",
                requires_visual=True,
                asset_role="figure",
                asset_path="references/assets/f1.png",
                asset_sha256=asset_digest("f1"),
            ),
            predicted_unit(
                "e1", "example", page=2, provenance="ai_supplemented"
            ),
            predicted_unit("q1", "question", page=2, requires_visual=False),
            predicted_unit("a1", "answer", page=2, exposed=True),
            predicted_unit("a2", "answer", page=2),
            predicted_unit("n1", "speaker_notes", exposed=True),
            predicted_unit(
                "x1",
                "figure",
                asset_role="answer_context",
                asset_path="references/assets/x1.png",
                asset_sha256=asset_digest("x1"),
                exposed=True,
            ),
        ],
        "qa_pairs": [{"question_id": "q1", "answer_id": "a2"}],
        "retrieval_results": [
            {"query_id": "r1", "ranked_unit_ids": ["x1", "q1"]},
            {"query_id": "r2", "ranked_unit_ids": ["f1", "x1"]},
        ],
    }


class PageQualityTest(unittest.TestCase):
    def test_clean_complete_page_uses_fast_route(self):
        result = assess_page(clean_signals())
        self.assertEqual(
            {"score": 1.0, "reason_codes": [], "route": "fast"}, result
        )
        self.assertEqual(result, score_page(dict(reversed(list(clean_signals().items())))))

    def test_blank_visual_page_routes_to_recovery(self):
        result = assess_page(
            clean_signals(text=" \n\t", image_count=1, image_area_ratio=0.9)
        )
        self.assertEqual("recover", result["route"])
        self.assertIn("no_text", result["reason_codes"])
        self.assertIn("visual_heavy", result["reason_codes"])
        self.assertLess(result["score"], 0.2)

    def test_blank_page_without_recovery_evidence_routes_to_review(self):
        result = assess_page(clean_signals(text=""))
        self.assertEqual("review", result["route"])
        self.assertEqual(["no_text"], result["reason_codes"])

        formula_page = assess_page(clean_signals(text="", formula_hint=True))
        self.assertEqual("recover", formula_page["route"])
        self.assertEqual(["no_text", "formula_hint"], formula_page["reason_codes"])

    def test_corruption_residue_and_repetition_are_all_reported(self):
        text = "endstream ÃÃ \x00\ufffd AAAAAA token token token token"
        result = assess_page(clean_signals(text=text))
        self.assertEqual("recover", result["route"])
        for reason in (
            "extraction_residue",
            "nul_or_replacement_char",
            "garbled_text",
            "repeated_characters",
        ):
            self.assertIn(reason, result["reason_codes"])
        self.assertLess(result["score"], 0.4)

    def test_layout_and_media_hints_are_not_sent_down_fast_path(self):
        result = assess_page(
            clean_signals(
                text="Short labels",
                image_count=3,
                image_area_ratio=0.5,
                vector_count=150,
                multi_column_hint=True,
                table_hint=True,
                formula_hint=True,
            )
        )
        self.assertEqual("recover", result["route"])
        self.assertEqual(
            ["visual_heavy", "multi_column_hint", "table_hint", "formula_hint"],
            result["reason_codes"],
        )

    def test_missing_signals_fail_closed_instead_of_looking_clean(self):
        result = assess_page({"page": 1, "text": "Looks clean in isolation"})
        self.assertEqual("review", result["route"])
        self.assertIn("missing_signals", result["reason_codes"])
        self.assertLessEqual(result["score"], 0.25)

        explicit_null = clean_signals(image_area_ratio=None)
        self.assertEqual("review", assess_page(explicit_null)["route"])

    def test_invalid_signal_types_and_unknown_fields_are_rejected(self):
        for bad in (
            clean_signals(page=True),
            clean_signals(image_count=-1),
            clean_signals(image_area_ratio=float("nan")),
            clean_signals(table_hint=1),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(QualityInputError):
                    assess_page(bad)
        unknown = clean_signals()
        unknown["ocr_confidence"] = 0.99
        with self.assertRaises(QualityInputError):
            assess_page(unknown)


class IngestionEvaluationTest(unittest.TestCase):
    def test_all_requested_metrics_are_deterministic(self):
        result = evaluate(gold_fixture(), prediction_fixture())

        self.assertEqual(
            {
                "true_positive": 1,
                "false_positive": 0,
                "false_negative": 1,
                "precision": 1.0,
                "recall": 0.5,
                "f1": 0.666667,
                "accounted": 1,
                "total": 2,
                "coverage": 0.5,
            },
            result["accounted_coverage"]["source"],
        )
        self.assertEqual(
            {
                "true_positive": 2,
                "false_positive": 0,
                "false_negative": 1,
                "precision": 1.0,
                "recall": 0.666667,
                "f1": 0.8,
                "accounted": 2,
                "total": 3,
                "coverage": 0.666667,
            },
            result["accounted_coverage"]["page"],
        )
        self.assertEqual(
            {"correct": 4, "total": 7, "accuracy": 0.571429},
            result["chapter_assignment"],
        )

        kinds = result["kind_classification"]
        self.assertEqual(1.0, kinds["per_kind"]["concept"]["f1"])
        self.assertEqual(0.0, kinds["per_kind"]["formula"]["recall"])
        self.assertEqual(0.5, kinds["per_kind"]["answer"]["precision"])
        self.assertEqual(0.571429, kinds["micro"]["precision"])
        self.assertEqual(0.571429, kinds["micro"]["recall"])
        self.assertEqual(0.5, kinds["macro"]["precision"])

        self.assertEqual(0, result["qa_pairing"]["true_positive"])
        self.assertEqual(1, result["qa_pairing"]["false_positive"])
        self.assertEqual(1, result["qa_pairing"]["false_negative"])
        self.assertEqual(
            {"correct": 4, "total": 7, "accuracy": 0.571429},
            result["provenance"],
        )
        self.assertEqual(
            {"recovered": 1, "total": 4, "recall": 0.25},
            result["visual_dependency"],
        )
        self.assertEqual(
            {"count": 3, "unit_ids": ["a1", "n1", "x1"]},
            result["answer_side_leakage"],
        )
        self.assertEqual(
            {"queries": 2, "recall_at_1": 0.25, "recall_at_5": 0.75, "mrr": 0.75},
            result["retrieval"],
        )

        reordered_gold = copy.deepcopy(gold_fixture())
        reordered_gold["units"].reverse()
        reordered_prediction = copy.deepcopy(prediction_fixture())
        reordered_prediction["units"].reverse()
        self.assertEqual(result, evaluate(reordered_gold, reordered_prediction))

    def test_perfect_prediction_reaches_one_without_llm_judging(self):
        gold = gold_fixture()
        # Recall@1 can only be 1.0 when each query has at most one relevant ID.
        gold["retrieval_queries"][1]["relevant_unit_ids"] = ["f1"]
        prediction = {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": source["source_id"],
                    "status": "parsed",
                    "accounted_pages": list(source["pages"]),
                }
                for source in gold["sources"]
            ],
            "units": [
                dict(
                    unit,
                    asset_role=(
                        "question_context"
                        if unit["kind"] == "question" and unit["requires_visual"]
                        else unit["kind"]
                        if unit["kind"] in ("table", "figure") and unit["requires_visual"]
                        else "figure"
                        if unit["requires_visual"]
                        else None
                    ),
                    asset_path=(
                        "references/assets/%s.png" % unit["unit_id"]
                        if unit["requires_visual"]
                        else None
                    ),
                    asset_sha256=(
                        asset_digest(unit["unit_id"])
                        if unit["requires_visual"]
                        else None
                    ),
                    exposed_in_question=False,
                )
                for unit in gold["units"]
            ],
            "qa_pairs": copy.deepcopy(gold["qa_pairs"]),
            "retrieval_results": [
                {
                    "query_id": query["query_id"],
                    "ranked_unit_ids": list(query["relevant_unit_ids"]),
                }
                for query in gold["retrieval_queries"]
            ],
        }
        result = evaluate(gold, prediction)
        self.assertEqual(1.0, result["accounted_coverage"]["source"]["coverage"])
        self.assertEqual(1.0, result["accounted_coverage"]["page"]["coverage"])
        self.assertEqual(1.0, result["chapter_assignment"]["accuracy"])
        self.assertEqual(1.0, result["kind_classification"]["micro"]["f1"])
        self.assertEqual(1.0, result["qa_pairing"]["f1"])
        self.assertEqual(1.0, result["provenance"]["accuracy"])
        self.assertEqual(1.0, result["visual_dependency"]["recall"])
        self.assertEqual(0, result["answer_side_leakage"]["count"])
        self.assertEqual(1.0, result["retrieval"]["recall_at_1"])
        self.assertEqual(1.0, result["retrieval"]["recall_at_5"])
        self.assertEqual(1.0, result["retrieval"]["mrr"])

    def test_student_attempt_is_leakage_and_not_visual_recovery(self):
        gold = gold_fixture()
        prediction = prediction_fixture()
        formula = next(unit for unit in prediction["units"] if unit["unit_id"] == "f1")
        formula.update({
            "kind": "formula",
            "chapter_id": "ch01",
            "asset_role": "student_attempt",
            "asset_path": "references/assets/student-attempt.png",
            "asset_sha256": asset_digest("student-attempt"),
            "exposed_in_question": True,
        })

        result = evaluate(gold, prediction)

        self.assertEqual(0, result["visual_dependency"]["recovered"])
        self.assertIn("f1", result["answer_side_leakage"]["unit_ids"])

    def test_empty_gold_and_prediction_use_explicit_zero_metrics(self):
        gold = {
            "schema_version": 1,
            "sources": [],
            "units": [],
            "qa_pairs": [],
            "retrieval_queries": [],
        }
        prediction = {
            "schema_version": 1,
            "sources": [],
            "units": [],
            "qa_pairs": [],
            "retrieval_results": [],
        }
        result = evaluate(gold, prediction)
        self.assertEqual(0.0, result["accounted_coverage"]["source"]["coverage"])
        self.assertEqual(0.0, result["chapter_assignment"]["accuracy"])
        self.assertEqual(0.0, result["kind_classification"]["micro"]["f1"])
        self.assertEqual(0.0, result["qa_pairing"]["f1"])
        self.assertEqual(0.0, result["provenance"]["accuracy"])
        self.assertEqual(0.0, result["visual_dependency"]["recall"])
        self.assertEqual(
            {"queries": 0, "recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0},
            result["retrieval"],
        )

    def test_strict_top_level_nested_types_and_relationships(self):
        bad_gold = gold_fixture()
        bad_gold["unexpected"] = []
        with self.assertRaises(EvaluationSchemaError):
            validate_gold(bad_gold)

        bad_prediction = prediction_fixture()
        bad_prediction["sources"][0]["status"] = "done"
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        bad_prediction["units"][0]["page"] = True
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        bad_prediction["units"][0]["source_id"] = "missing"
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        bad_prediction["units"][0]["exposed_in_question"] = True
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        del bad_prediction["units"][0]["asset_path"]
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        bad_prediction["units"][1]["asset_sha256"] = "not-a-sha256"
        with self.assertRaises(EvaluationSchemaError):
            validate_prediction(bad_prediction)

        bad_prediction = prediction_fixture()
        bad_prediction["retrieval_results"].append(
            {"query_id": "unknown", "ranked_unit_ids": []}
        )
        with self.assertRaises(EvaluationSchemaError):
            evaluate(gold_fixture(), bad_prediction)

    def test_retrieval_references_must_resolve_to_declared_units(self):
        bad_gold = gold_fixture()
        bad_gold["retrieval_queries"][0]["relevant_unit_ids"] = ["missing-gold-unit"]
        with self.assertRaisesRegex(EvaluationSchemaError, "unknown unit IDs"):
            validate_gold(bad_gold)

        bad_prediction = prediction_fixture()
        bad_prediction["retrieval_results"][0]["ranked_unit_ids"] = [
            "missing-predicted-unit"
        ]
        with self.assertRaisesRegex(EvaluationSchemaError, "unknown unit IDs"):
            validate_prediction(bad_prediction)

    def test_accounting_precision_penalizes_extra_sources_and_pages(self):
        prediction = prediction_fixture()
        prediction["sources"].append(
            {"source_id": "invented", "status": "parsed", "accounted_pages": [99]}
        )

        accounting = evaluate(gold_fixture(), prediction)["accounted_coverage"]
        self.assertEqual(1, accounting["source"]["false_positive"])
        self.assertEqual(0.5, accounting["source"]["precision"])
        self.assertEqual(1, accounting["page"]["false_positive"])
        self.assertEqual(0.666667, accounting["page"]["precision"])

    def test_strict_file_loading_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold_path = root / "gold.json"
            prediction_path = root / "prediction.json"
            gold_path.write_text(json.dumps(gold_fixture()), encoding="utf-8")
            prediction_path.write_text(json.dumps(prediction_fixture()), encoding="utf-8")
            self.assertEqual(
                evaluate(gold_fixture(), prediction_fixture()),
                evaluate_files(gold_path, prediction_path),
            )

            prediction_path.write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8"
            )
            with self.assertRaises(EvaluationSchemaError):
                evaluate_files(gold_path, prediction_path)


if __name__ == "__main__":
    unittest.main()
