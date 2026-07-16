"""Strict, deterministic ingestion and retrieval evaluation.

This module intentionally has no LLM judge and no third-party dependencies.
Gold and prediction documents use compact JSON schemas described by the field
constants below.  Unknown/missing fields, duplicate identities, invalid
references, and malformed values fail closed with :class:`EvaluationSchemaError`.
"""

import json
from pathlib import Path


SCHEMA_VERSION = 1

EVALUATED_KINDS = (
    "concept",
    "formula",
    "example",
    "question",
    "answer",
    "table",
    "figure",
)

PREDICTION_KINDS = frozenset(
    EVALUATED_KINDS
    + (
        "title",
        "heading",
        "text",
        "list",
        "diagram",
        "caption",
        "code",
        "speaker_notes",
        "page_anchor",
        "other",
    )
)

PROVENANCE_VALUES = frozenset(("material", "ai_recovered", "ai_supplemented"))
SOURCE_STATUSES = frozenset((
    "discovered", "parsed", "review_required", "unsupported", "failed",
    "complete", "unrecoverable", "superseded",
))
ASSET_ROLES = frozenset(
    (
        "question_context",
        "answer_context",
        "worked_solution",
        "student_attempt",
        "figure",
        "diagram",
        "table",
        "source_page",
        "other",
    )
)
LEAKAGE_SIDE_ROLES = frozenset(("answer_context", "worked_solution", "student_attempt"))

GOLD_FIELDS = ("schema_version", "sources", "units", "qa_pairs", "retrieval_queries")
PREDICTION_FIELDS = (
    "schema_version",
    "sources",
    "units",
    "qa_pairs",
    "retrieval_results",
)
GOLD_SOURCE_FIELDS = ("source_id", "pages")
PREDICTION_SOURCE_FIELDS = ("source_id", "status", "accounted_pages")
GOLD_UNIT_FIELDS = (
    "unit_id",
    "source_id",
    "page",
    "chapter_id",
    "kind",
    "provenance",
    "requires_visual",
)
PREDICTION_UNIT_FIELDS = GOLD_UNIT_FIELDS + (
    "asset_role", "asset_path", "asset_sha256", "exposed_in_question",
)
QA_PAIR_FIELDS = ("question_id", "answer_id")
RETRIEVAL_QUERY_FIELDS = ("query_id", "relevant_unit_ids")
RETRIEVAL_RESULT_FIELDS = ("query_id", "ranked_unit_ids")


class EvaluationSchemaError(ValueError):
    """A gold or prediction document violated the evaluation contract."""


def _fail(message):
    raise EvaluationSchemaError(message)


def _strict_object(value, fields, label):
    if not isinstance(value, dict):
        _fail("%s must be an object" % label)
    expected = set(fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (label, missing, unknown))


def _array(value, label):
    if not isinstance(value, list):
        _fail("%s must be an array" % label)
    return value


def _token(value, label, nullable=False):
    if value is None and nullable:
        return value
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    return value


def _page(value, label):
    if type(value) is not int or value < 1:
        _fail("%s must be an integer >= 1" % label)
    return value


def _boolean(value, label):
    if type(value) is not bool:
        _fail("%s must be a boolean" % label)
    return value


def _enum(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        _fail("%s must be one of %s" % (label, ", ".join(sorted(allowed))))
    return value


def _unique_tokens(values, label, allow_empty=True):
    rows = _array(values, label)
    result = []
    seen = set()
    for index, value in enumerate(rows):
        token = _token(value, "%s[%d]" % (label, index))
        if token in seen:
            _fail("%s contains duplicate value %s" % (label, token))
        seen.add(token)
        result.append(token)
    if not allow_empty and not result:
        _fail("%s must not be empty" % label)
    return tuple(result)


def _unique_pages(values, label):
    rows = _array(values, label)
    result = []
    seen = set()
    for index, value in enumerate(rows):
        page = _page(value, "%s[%d]" % (label, index))
        if page in seen:
            _fail("%s contains duplicate page %d" % (label, page))
        seen.add(page)
        result.append(page)
    return tuple(result)


def _schema_version(document, label):
    value = document["schema_version"]
    if type(value) is not int or value != SCHEMA_VERSION:
        _fail("%s.schema_version must be %d" % (label, SCHEMA_VERSION))


def _validate_sources(rows, prediction):
    label = "prediction.sources" if prediction else "gold.sources"
    fields = PREDICTION_SOURCE_FIELDS if prediction else GOLD_SOURCE_FIELDS
    result = {}
    for index, raw in enumerate(_array(rows, label)):
        item_label = "%s[%d]" % (label, index)
        _strict_object(raw, fields, item_label)
        source_id = _token(raw["source_id"], item_label + ".source_id")
        if source_id in result:
            _fail("%s contains duplicate source_id %s" % (label, source_id))
        if prediction:
            status = _enum(raw["status"], SOURCE_STATUSES, item_label + ".status")
            pages = _unique_pages(raw["accounted_pages"], item_label + ".accounted_pages")
            result[source_id] = {"source_id": source_id, "status": status, "pages": pages}
        else:
            pages = _unique_pages(raw["pages"], item_label + ".pages")
            result[source_id] = {"source_id": source_id, "pages": pages}
    return result


def _validate_units(rows, sources, prediction):
    label = "prediction.units" if prediction else "gold.units"
    fields = PREDICTION_UNIT_FIELDS if prediction else GOLD_UNIT_FIELDS
    result = {}
    allowed_kinds = PREDICTION_KINDS if prediction else frozenset(EVALUATED_KINDS)
    for index, raw in enumerate(_array(rows, label)):
        item_label = "%s[%d]" % (label, index)
        _strict_object(raw, fields, item_label)
        unit_id = _token(raw["unit_id"], item_label + ".unit_id")
        if unit_id in result:
            _fail("%s contains duplicate unit_id %s" % (label, unit_id))
        source_id = _token(raw["source_id"], item_label + ".source_id")
        if source_id not in sources:
            _fail("%s references unknown source_id %s" % (item_label, source_id))
        page = _page(raw["page"], item_label + ".page")
        if page not in set(sources[source_id]["pages"]):
            _fail("%s page %d is not accounted by source %s" % (item_label, page, source_id))
        chapter_id = _token(raw["chapter_id"], item_label + ".chapter_id", nullable=True)
        kind = _enum(raw["kind"], allowed_kinds, item_label + ".kind")
        provenance = _enum(raw["provenance"], PROVENANCE_VALUES, item_label + ".provenance")
        requires_visual = _boolean(raw["requires_visual"], item_label + ".requires_visual")
        unit = {
            "unit_id": unit_id,
            "source_id": source_id,
            "page": page,
            "chapter_id": chapter_id,
            "kind": kind,
            "provenance": provenance,
            "requires_visual": requires_visual,
        }
        if prediction:
            asset_role = raw["asset_role"]
            if asset_role is not None:
                asset_role = _enum(asset_role, ASSET_ROLES, item_label + ".asset_role")
            exposed = _boolean(raw["exposed_in_question"], item_label + ".exposed_in_question")
            asset_path = raw["asset_path"]
            if asset_path is not None:
                asset_path = _token(asset_path, item_label + ".asset_path")
            asset_sha256 = raw["asset_sha256"]
            if asset_sha256 is not None:
                asset_sha256 = _token(asset_sha256, item_label + ".asset_sha256")
                if (len(asset_sha256) != 64
                        or any(char not in "0123456789abcdef" for char in asset_sha256)):
                    _fail("%s.asset_sha256 must be lowercase SHA-256" % item_label)
            if exposed and asset_role is None and kind not in ("answer", "speaker_notes"):
                _fail("%s exposed_in_question requires an asset_role" % item_label)
            unit["asset_role"] = asset_role
            unit["asset_path"] = asset_path
            unit["asset_sha256"] = asset_sha256
            unit["exposed_in_question"] = exposed
        result[unit_id] = unit
    return result


def _validate_qa_pairs(rows, units, label):
    result = []
    seen = set()
    for index, raw in enumerate(_array(rows, label)):
        item_label = "%s[%d]" % (label, index)
        _strict_object(raw, QA_PAIR_FIELDS, item_label)
        question_id = _token(raw["question_id"], item_label + ".question_id")
        answer_id = _token(raw["answer_id"], item_label + ".answer_id")
        pair = (question_id, answer_id)
        if pair in seen:
            _fail("%s contains duplicate Q/A pair %r" % (label, pair))
        seen.add(pair)
        question = units.get(question_id)
        answer = units.get(answer_id)
        if question is None or question["kind"] != "question":
            _fail("%s question_id must reference a question unit" % item_label)
        if answer is None or answer["kind"] != "answer":
            _fail("%s answer_id must reference an answer unit" % item_label)
        result.append(pair)
    return tuple(result)


def _validate_retrieval_queries(rows, units):
    result = {}
    label = "gold.retrieval_queries"
    for index, raw in enumerate(_array(rows, label)):
        item_label = "%s[%d]" % (label, index)
        _strict_object(raw, RETRIEVAL_QUERY_FIELDS, item_label)
        query_id = _token(raw["query_id"], item_label + ".query_id")
        if query_id in result:
            _fail("%s contains duplicate query_id %s" % (label, query_id))
        relevant = _unique_tokens(
            raw["relevant_unit_ids"], item_label + ".relevant_unit_ids", allow_empty=False
        )
        unknown = sorted(set(relevant) - set(units))
        if unknown:
            _fail("%s references unknown unit IDs: %r" % (item_label, unknown))
        result[query_id] = relevant
    return result


def _validate_retrieval_results(rows, units):
    result = {}
    label = "prediction.retrieval_results"
    for index, raw in enumerate(_array(rows, label)):
        item_label = "%s[%d]" % (label, index)
        _strict_object(raw, RETRIEVAL_RESULT_FIELDS, item_label)
        query_id = _token(raw["query_id"], item_label + ".query_id")
        if query_id in result:
            _fail("%s contains duplicate query_id %s" % (label, query_id))
        ranked = _unique_tokens(raw["ranked_unit_ids"], item_label + ".ranked_unit_ids")
        unknown = sorted(set(ranked) - set(units))
        if unknown:
            _fail("%s references unknown unit IDs: %r" % (item_label, unknown))
        result[query_id] = ranked
    return result


def validate_gold(document):
    """Validate and normalize a gold JSON object."""

    _strict_object(document, GOLD_FIELDS, "gold")
    _schema_version(document, "gold")
    sources = _validate_sources(document["sources"], prediction=False)
    units = _validate_units(document["units"], sources, prediction=False)
    qa_pairs = _validate_qa_pairs(document["qa_pairs"], units, "gold.qa_pairs")
    retrieval_queries = _validate_retrieval_queries(document["retrieval_queries"], units)
    return {
        "sources": sources,
        "units": units,
        "qa_pairs": qa_pairs,
        "retrieval_queries": retrieval_queries,
    }


def validate_prediction(document):
    """Validate and normalize a prediction JSON object."""

    _strict_object(document, PREDICTION_FIELDS, "prediction")
    _schema_version(document, "prediction")
    sources = _validate_sources(document["sources"], prediction=True)
    units = _validate_units(document["units"], sources, prediction=True)
    qa_pairs = _validate_qa_pairs(document["qa_pairs"], units, "prediction.qa_pairs")
    retrieval_results = _validate_retrieval_results(document["retrieval_results"], units)
    return {
        "sources": sources,
        "units": units,
        "qa_pairs": qa_pairs,
        "retrieval_results": retrieval_results,
    }


def _ratio(numerator, denominator):
    if denominator == 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _prf(true_positive, false_positive, false_negative):
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    if precision + recall == 0.0:
        f1 = 0.0
    else:
        f1 = round(2.0 * precision * recall / (precision + recall), 6)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _accounting_metrics(gold, prediction):
    gold_source_ids = set(gold["sources"])
    predicted_source_ids = set(prediction["sources"])
    source_overlap = gold_source_ids & predicted_source_ids

    gold_pages = {
        (source_id, page)
        for source_id, source in gold["sources"].items()
        for page in source["pages"]
    }
    predicted_pages = {
        (source_id, page)
        for source_id, source in prediction["sources"].items()
        for page in source["pages"]
    }
    page_overlap = gold_pages & predicted_pages
    source_prf = _prf(
        len(source_overlap),
        len(predicted_source_ids - gold_source_ids),
        len(gold_source_ids - predicted_source_ids),
    )
    page_prf = _prf(
        len(page_overlap),
        len(predicted_pages - gold_pages),
        len(gold_pages - predicted_pages),
    )
    return {
        "source": dict(
            source_prf,
            accounted=len(source_overlap),
            total=len(gold_source_ids),
            coverage=_ratio(len(source_overlap), len(gold_source_ids)),
        ),
        "page": dict(
            page_prf,
            accounted=len(page_overlap),
            total=len(gold_pages),
            coverage=_ratio(len(page_overlap), len(gold_pages)),
        ),
    }


def _chapter_metrics(gold, prediction):
    expected = [unit for unit in gold["units"].values() if unit["chapter_id"] is not None]
    correct = 0
    for unit in expected:
        predicted = prediction["units"].get(unit["unit_id"])
        if predicted is not None and predicted["chapter_id"] == unit["chapter_id"]:
            correct += 1
    return {"correct": correct, "total": len(expected), "accuracy": _ratio(correct, len(expected))}


def _kind_metrics(gold, prediction):
    per_kind = {}
    micro_tp = 0
    micro_fp = 0
    micro_fn = 0
    for kind in EVALUATED_KINDS:
        gold_ids = {unit_id for unit_id, unit in gold["units"].items() if unit["kind"] == kind}
        predicted_ids = {
            unit_id for unit_id, unit in prediction["units"].items() if unit["kind"] == kind
        }
        true_positive = len(gold_ids & predicted_ids)
        false_positive = len(predicted_ids - gold_ids)
        false_negative = len(gold_ids - predicted_ids)
        metric = _prf(true_positive, false_positive, false_negative)
        per_kind[kind] = metric
        micro_tp += true_positive
        micro_fp += false_positive
        micro_fn += false_negative

    macro = {
        name: round(
            sum(per_kind[kind][name] for kind in EVALUATED_KINDS) / float(len(EVALUATED_KINDS)),
            6,
        )
        for name in ("precision", "recall", "f1")
    }
    return {
        "per_kind": per_kind,
        "micro": _prf(micro_tp, micro_fp, micro_fn),
        "macro": macro,
    }


def _qa_metrics(gold, prediction):
    expected = set(gold["qa_pairs"])
    actual = set(prediction["qa_pairs"])
    true_positive = len(expected & actual)
    return _prf(true_positive, len(actual - expected), len(expected - actual))


def _provenance_metrics(gold, prediction):
    correct = 0
    for unit_id, expected in gold["units"].items():
        actual = prediction["units"].get(unit_id)
        if actual is not None and actual["provenance"] == expected["provenance"]:
            correct += 1
    return {
        "correct": correct,
        "total": len(gold["units"]),
        "accuracy": _ratio(correct, len(gold["units"])),
    }


def _visual_metrics(gold, prediction):
    expected_ids = {
        unit_id for unit_id, unit in gold["units"].items() if unit["requires_visual"]
    }
    recovered_ids = {
        unit_id
        for unit_id in expected_ids
        if unit_id in prediction["units"]
        and prediction["units"][unit_id]["requires_visual"]
        and prediction["units"][unit_id]["asset_role"] is not None
        and prediction["units"][unit_id]["asset_role"] != "student_attempt"
        and prediction["units"][unit_id]["asset_path"] is not None
        and prediction["units"][unit_id]["asset_sha256"] is not None
    }
    return {
        "recovered": len(recovered_ids),
        "total": len(expected_ids),
        "recall": _ratio(len(recovered_ids), len(expected_ids)),
    }


def _leakage_metrics(prediction):
    leaking_ids = sorted(
        unit_id
        for unit_id, unit in prediction["units"].items()
        if unit["exposed_in_question"] and (
            unit["asset_role"] in LEAKAGE_SIDE_ROLES
            or unit["kind"] in ("answer", "speaker_notes")
        )
    )
    return {"count": len(leaking_ids), "unit_ids": leaking_ids}


def _retrieval_metrics(gold, prediction):
    unexpected = sorted(set(prediction["retrieval_results"]) - set(gold["retrieval_queries"]))
    if unexpected:
        _fail("prediction contains retrieval results for unknown query IDs: %r" % unexpected)

    recall_at_1 = []
    recall_at_5 = []
    reciprocal_ranks = []
    for query_id in sorted(gold["retrieval_queries"]):
        relevant = set(gold["retrieval_queries"][query_id])
        ranked = prediction["retrieval_results"].get(query_id, ())
        recall_at_1.append(_ratio(len(relevant & set(ranked[:1])), len(relevant)))
        recall_at_5.append(_ratio(len(relevant & set(ranked[:5])), len(relevant)))
        reciprocal_rank = 0.0
        for rank, unit_id in enumerate(ranked, 1):
            if unit_id in relevant:
                reciprocal_rank = 1.0 / float(rank)
                break
        reciprocal_ranks.append(reciprocal_rank)

    count = len(gold["retrieval_queries"])
    if count == 0:
        return {"queries": 0, "recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0}
    return {
        "queries": count,
        "recall_at_1": round(sum(recall_at_1) / float(count), 6),
        "recall_at_5": round(sum(recall_at_5) / float(count), 6),
        "mrr": round(sum(reciprocal_ranks) / float(count), 6),
    }


def evaluate(gold_document, prediction_document):
    """Validate two JSON structures and compute all deterministic metrics."""

    gold = validate_gold(gold_document)
    prediction = validate_prediction(prediction_document)
    return {
        "schema_version": SCHEMA_VERSION,
        "accounted_coverage": _accounting_metrics(gold, prediction),
        "chapter_assignment": _chapter_metrics(gold, prediction),
        "kind_classification": _kind_metrics(gold, prediction),
        "qa_pairing": _qa_metrics(gold, prediction),
        "provenance": _provenance_metrics(gold, prediction),
        "visual_dependency": _visual_metrics(gold, prediction),
        "answer_side_leakage": _leakage_metrics(prediction),
        "retrieval": _retrieval_metrics(gold, prediction),
    }


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def load_json(path):
    """Load strict UTF-8 JSON, rejecting duplicate object keys."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        _fail("evaluation input must be a regular non-symlink file: %s" % source)
    try:
        with open(source, "r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationSchemaError("cannot read strict JSON %s: %s" % (source, exc)) from exc


def evaluate_files(gold_path, prediction_path):
    """Load strict JSON files and return :func:`evaluate` metrics."""

    return evaluate(load_json(gold_path), load_json(prediction_path))
