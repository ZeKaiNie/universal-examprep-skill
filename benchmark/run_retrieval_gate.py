#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run the evidence gate without changing the production BM25 default.

The committed tiny sample is intentionally too small and can be inspected
without a workspace::

    python benchmark/run_retrieval_gate.py \
      --gold benchmark/retrieval_gold/sample.insufficient.json

A sufficient frozen gold set additionally requires the exact workspace/index.
Candidate results are supplied as an explicit experimental run receipt; this
runner never imports or downloads a dense model.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import retrieve  # noqa: E402
import strict_json  # noqa: E402
from scripts import retrieval_evaluation as evaluation  # noqa: E402
from scripts.ingestion.identifiers import is_link_or_reparse  # noqa: E402
from scripts.ingestion.models import ContentUnit, SourceRecord  # noqa: E402
from scripts.ingestion.storage import read_json, read_jsonl  # noqa: E402
try:  # script execution places benchmark/ on sys.path; package tests do not.
    import retrieval_candidates as candidates  # noqa: E402
except ImportError:  # pragma: no cover - the branch depends only on import style
    from benchmark import retrieval_candidates as candidates  # noqa: E402


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path, label):
    absolute = os.path.abspath(path)
    if (os.path.islink(absolute) or is_link_or_reparse(absolute)
            or not os.path.isfile(absolute)):
        raise evaluation.RetrievalEvaluationError(
            "%s must be a regular non-symlink file: %s" % (label, absolute))
    try:
        with open(absolute, "r", encoding="utf-8") as stream:
            return strict_json.load(stream)
    except (OSError, UnicodeError, ValueError) as exc:
        raise evaluation.RetrievalEvaluationError(
            "cannot read strict %s JSON: %s" % (label, exc)) from exc


def _atomic_json(path, payload):
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    os.makedirs(parent, exist_ok=True)
    if (os.path.lexists(absolute)
            and (os.path.islink(absolute) or is_link_or_reparse(absolute))):
        raise evaluation.RetrievalEvaluationError(
            "output must not be a link/reparse entry: %s" % absolute)
    fd, temporary = tempfile.mkstemp(prefix=".retrieval-gate-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, absolute)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _regular_file(path, label):
    if (not os.path.isfile(path) or os.path.islink(path)
            or is_link_or_reparse(path)):
        raise evaluation.RetrievalEvaluationError(
            "%s must be a regular non-link/reparse file: %s" % (label, path))
    return path


def validate_workspace_map(document, gold_document=None):
    """Validate a strict course_id -> distinct absolute workspace map."""

    if not isinstance(document, dict) or set(document) != {"schema_version", "courses"}:
        raise evaluation.RetrievalEvaluationError(
            "workspace map fields must be exactly schema_version/courses")
    if document["schema_version"] != evaluation.SCHEMA_VERSION:
        raise evaluation.RetrievalEvaluationError("workspace map schema_version must be 1")
    if not isinstance(document["courses"], list) or not document["courses"]:
        raise evaluation.RetrievalEvaluationError("workspace map courses must be non-empty")
    result = {}
    real_paths = {}
    previous = None
    for position, row in enumerate(document["courses"]):
        label = "workspace map courses[%d]" % position
        if not isinstance(row, dict) or set(row) != {"course_id", "workspace"}:
            raise evaluation.RetrievalEvaluationError(
                "%s fields must be exactly course_id/workspace" % label)
        course_id = row["course_id"]
        workspace = row["workspace"]
        if (not isinstance(course_id, str) or not course_id or course_id != course_id.strip()
                or any(char in course_id for char in "\x00\r\n")):
            raise evaluation.RetrievalEvaluationError("%s course_id is invalid" % label)
        if previous is not None and course_id <= previous:
            raise evaluation.RetrievalEvaluationError(
                "workspace map courses must be sorted by course_id")
        previous = course_id
        if course_id in result:
            raise evaluation.RetrievalEvaluationError(
                "workspace map contains duplicate course_id %s" % course_id)
        if (not isinstance(workspace, str) or workspace != workspace.strip()
                or not os.path.isabs(workspace)
                or "\x00" in workspace or any(char in workspace for char in "\r\n")):
            raise evaluation.RetrievalEvaluationError(
                "%s workspace must be an absolute path" % label)
        workspace = os.path.abspath(workspace)
        if (not os.path.isdir(workspace) or os.path.islink(workspace)
                or is_link_or_reparse(workspace)):
            raise evaluation.RetrievalEvaluationError(
                "%s workspace must be a regular non-link/reparse directory" % label)
        identity = os.path.normcase(os.path.realpath(workspace))
        if identity in real_paths:
            raise evaluation.RetrievalEvaluationError(
                "workspace map reuses one workspace for multiple courses: %s/%s" % (
                    real_paths[identity], course_id))
        real_paths[identity] = course_id
        result[course_id] = workspace
    if gold_document is not None:
        gold = evaluation.validate_gold(gold_document)
        expected = set(gold["index_bundle"])
        actual = set(result)
        if actual != expected:
            raise evaluation.RetrievalEvaluationError(
                "workspace map course IDs must exactly equal gold bundle IDs; missing=%r unknown=%r"
                % (sorted(expected - actual), sorted(actual - expected)))
    return result


def _load_ingest_truth(workspace, course_id):
    ingest = os.path.join(workspace, ".ingest")
    manifest_path = _regular_file(
        os.path.join(ingest, "source_manifest.json"),
        "%s source manifest" % course_id)
    units_path = _regular_file(
        os.path.join(ingest, "content_units.jsonl"),
        "%s content units" % course_id)
    try:
        manifest = read_json(manifest_path)
        if (not isinstance(manifest, dict)
                or set(manifest) != {"schema_version", "sources"}
                or manifest.get("schema_version") != 1
                or not isinstance(manifest.get("sources"), list)):
            raise ValueError("source manifest has an invalid top-level schema")
        sources = [SourceRecord.from_dict(row) for row in manifest["sources"]]
        units = [ContentUnit.from_dict(row) for row in read_jsonl(units_path)]
    except Exception as exc:
        raise evaluation.RetrievalEvaluationError(
            "%s ingest truth is invalid: %s" % (course_id, exc)) from exc
    source_by_id = {row.source_id: row for row in sources}
    unit_by_id = {row.unit_id: row for row in units}
    if len(source_by_id) != len(sources):
        raise evaluation.RetrievalEvaluationError(
            "%s source manifest contains duplicate source IDs" % course_id)
    if len(unit_by_id) != len(units):
        raise evaluation.RetrievalEvaluationError(
            "%s content units contain duplicate unit IDs" % course_id)
    for unit in units:
        source = source_by_id.get(unit.source_id)
        if source is None:
            raise evaluation.RetrievalEvaluationError(
                "%s unit %s references an unknown source" % (course_id, unit.unit_id))
        if unit.source_sha256 != source.sha256 or unit.source_file != source.path:
            raise evaluation.RetrievalEvaluationError(
                "%s unit %s disagrees with source manifest identity" % (
                    course_id, unit.unit_id))
    return {
        "manifest_path": manifest_path,
        "units_path": units_path,
        "sources": source_by_id,
        "units": unit_by_id,
    }


def load_course_shards(gold_document, workspace_map_document, index_loader=None):
    """Load and evidence-check every real course shard named by the frozen gold."""

    gold = evaluation.validate_gold(gold_document)
    workspaces = validate_workspace_map(workspace_map_document, gold_document)
    loader = index_loader or retrieve.load_index
    shards = {}
    for course_id in sorted(workspaces):
        workspace = workspaces[course_id]
        bundle = gold["index_bundle"][course_id]
        index_path = _regular_file(
            os.path.join(workspace, "references", "retrieval_index.json"),
            "%s retrieval index" % course_id)
        truth = _load_ingest_truth(workspace, course_id)
        actual_hashes = {
            "index_sha256": _sha256_file(index_path),
            "content_units_sha256": _sha256_file(truth["units_path"]),
            "source_manifest_sha256": _sha256_file(truth["manifest_path"]),
        }
        for field, actual in actual_hashes.items():
            if actual != bundle[field]:
                raise evaluation.RetrievalEvaluationError(
                    "%s %s does not match the frozen gold bundle" % (course_id, field))
        try:
            index = loader(workspace)
        except SystemExit as exc:
            raise evaluation.RetrievalEvaluationError(
                "%s retrieval index failed integrity loading: %s" % (
                    course_id, exc.code)) from exc
        if index is None:
            raise evaluation.RetrievalEvaluationError(
                "%s retrieval index is unavailable" % course_id)
        evaluation.validate_index_bindings(gold_document, index, course_id=course_id)
        queries = [query for query in gold["queries"].values()
                   if query["course_id"] == course_id]
        labeled = set()
        for query in queries:
            labeled.update(query["relevant_unit_ids"])
            labeled.update(query["hard_negative_unit_ids"])
            for evidence in query["evidence"]:
                unit = truth["units"].get(evidence["unit_id"])
                if unit is None:
                    raise evaluation.RetrievalEvaluationError(
                        "%s evidence unit %s is absent from content_units.jsonl" % (
                            course_id, evidence["unit_id"]))
                source = truth["sources"].get(evidence["source_id"])
                if source is None:
                    raise evaluation.RetrievalEvaluationError(
                        "%s evidence source %s is absent from source_manifest.json" % (
                            course_id, evidence["source_id"]))
                for field, actual, expected in (
                    ("source_id", unit.source_id, evidence["source_id"]),
                    ("source_sha256", unit.source_sha256, evidence["source_sha256"]),
                    ("page", unit.page, evidence["page"]),
                    ("manifest source_sha256", source.sha256, evidence["source_sha256"]),
                ):
                    if actual != expected:
                        raise evaluation.RetrievalEvaluationError(
                            "%s evidence %s mismatch for unit %s" % (
                                course_id, field, evidence["unit_id"]))
        missing_units = sorted(labeled - set(truth["units"]))
        if missing_units:
            raise evaluation.RetrievalEvaluationError(
                "%s labeled units are absent from content_units.jsonl: %r" % (
                    course_id, missing_units))
        shards[course_id] = {
            "workspace": workspace,
            "index": index,
            "index_path": index_path,
            "truth": truth,
            "bundle": bundle,
        }
    return shards


def _hit_receipt(hit, rank):
    score = float(hit["score"])
    return {
        "doc_id": hit["id"],
        "rank": rank,
        "unit_ids": list(hit.get("unit_ids") or ()),
        "score": score,
        "score_kind": "bm25",
        "components": [{
            "backend": "bm25",
            "rank": rank,
            "score": score,
            "score_kind": "bm25",
        }],
    }


def run_bm25_bundle(gold_document, workspace_map_document, top_k=5,
                    min_score=0.0, repeats=3):
    """Run each query against only its course's existing BM25 index shard."""

    if type(top_k) is not int or top_k < 1:
        raise evaluation.RetrievalEvaluationError("top_k must be a positive integer")
    if type(repeats) is not int or repeats < 2:
        raise evaluation.RetrievalEvaluationError("repeats must be >= 2")
    if (isinstance(min_score, bool) or not isinstance(min_score, (int, float))
            or min_score < 0):
        raise evaluation.RetrievalEvaluationError("min_score must be a non-negative number")
    gold = evaluation.validate_gold(gold_document)
    shards = load_course_shards(gold_document, workspace_map_document)

    repetitions = []
    first_results = None
    first_latencies = None
    for repeat in range(repeats):
        result_map = {}
        latencies = {}
        for query_id in sorted(gold["queries"]):
            query = gold["queries"][query_id]
            shard = shards[query["course_id"]]
            started = time.perf_counter()
            hits, unused_tokens = retrieve.search(
                shard["workspace"], shard["index"], query["query"],
                top_k=top_k, min_score=min_score)
            latency_ms = round((time.perf_counter() - started) * 1000.0, 6)
            latencies[query_id] = latency_ms
            result_map[query_id] = [_hit_receipt(hit, rank)
                                    for rank, hit in enumerate(hits, 1)]
        repetitions.append(result_map)
        if repeat == 0:
            first_results = result_map
            first_latencies = latencies

    rows = []
    for query_id in sorted(gold["queries"]):
        hits = first_results[query_id]
        rows.append({
            "query_id": query_id,
            "abstain": not hits,
            "abstain_reason": "no_hit_above_gate" if not hits else None,
            "latency_ms": first_latencies[query_id],
            "hits": hits,
        })
    config = {
        "backend": "bm25",
        "index_versions": {
            course_id: shard["index"].get("version")
            for course_id, shard in sorted(shards.items())
        },
        "top_k": top_k,
        "min_score": float(min_score),
        "repeats": repeats,
        "tokenization": "retrieve.py",
    }
    p95 = evaluation.percentile(list(first_latencies.values()), 0.95)
    run_basis = {
        "gold_sha256": gold["canonical_sha256"],
        "index_bundle_sha256": gold["index_bundle_sha256"],
        "config_sha256": evaluation.canonical_sha256(config),
        "results": rows,
    }
    receipt = {
        "schema_version": evaluation.SCHEMA_VERSION,
        "run_id": "bm25-" + evaluation.canonical_sha256(run_basis)[:16],
        "gold_sha256": gold["canonical_sha256"],
        "index_bundle": [dict(row) for row in gold["index_bundle_rows"]],
        "index_bundle_sha256": gold["index_bundle_sha256"],
        "backend": {
            "name": "stdlib-bm25",
            "kind": "bm25",
            "version": "retrieve-index-bundle",
            "config_sha256": evaluation.canonical_sha256(config),
        },
        "parent": None,
        "fusion": None,
        "resources": {
            "query_count": len(rows),
            "indexed_docs": sum(int(shard["index"].get("n_docs") or 0)
                                for shard in shards.values()),
            "index_size_bytes": sum(os.path.getsize(shard["index_path"])
                                    for shard in shards.values()),
            "p95_latency_ms": p95,
            "top5_stability": candidates.top_k_stability(repetitions, top_k=min(5, top_k)),
        },
        "results": rows,
    }
    evaluation.validate_run_receipt(receipt, gold_document=gold_document)
    return receipt


def run_bm25(gold_document, workspace, top_k=5, min_score=0.0, repeats=3):
    """Compatibility helper for an explicitly single-course, insufficient set."""

    gold = evaluation.validate_gold(gold_document)
    if len(gold["index_bundle"]) != 1:
        raise evaluation.RetrievalEvaluationError(
            "single-workspace helper requires exactly one course shard")
    if evaluation.evidence_sufficiency(gold_document)["sufficient"]:
        raise evaluation.RetrievalEvaluationError(
            "a sufficient promotion gate must use the multi-course workspace map")
    course_id = next(iter(gold["index_bundle"]))
    workspace_map = {
        "schema_version": evaluation.SCHEMA_VERSION,
        "courses": [{"course_id": course_id, "workspace": os.path.abspath(workspace)}],
    }
    return run_bm25_bundle(
        gold_document, workspace_map, top_k=top_k,
        min_score=min_score, repeats=repeats)


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Evidence gate for BM25 versus optional experimental retrieval receipts")
    parser.add_argument("--gold", required=True)
    parser.add_argument(
        "--workspace-map",
        help="strict JSON mapping every frozen course_id to one distinct absolute workspace",
    )
    parser.add_argument("--candidate-receipt")
    parser.add_argument(
        "--candidate-input-receipt", action="append", default=[],
        help="real non-baseline input receipt used by an RRF/hybrid candidate (repeatable)",
    )
    parser.add_argument("--baseline-out")
    parser.add_argument("--out")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--bootstrap-resamples", type=int, default=5000)
    args = parser.parse_args(argv)

    if args.candidate_input_receipt and not args.candidate_receipt:
        raise evaluation.RetrievalEvaluationError(
            "candidate input receipts require --candidate-receipt")

    gold_document = _load_json(args.gold, "gold")
    sufficiency = evaluation.evidence_sufficiency(gold_document)
    baseline = None
    candidate = None
    candidate_inputs = None
    if sufficiency["sufficient"]:
        if not args.workspace_map:
            decision = evaluation.decide(gold_document)
            decision["reasons"] = ["workspace_map_missing_for_sufficient_gold"]
        else:
            workspace_map = _load_json(args.workspace_map, "workspace map")
            baseline = run_bm25_bundle(
                gold_document, workspace_map, top_k=args.top_k,
                min_score=args.min_score, repeats=args.repeats)
            if args.candidate_receipt:
                candidate = _load_json(args.candidate_receipt, "candidate receipt")
                kind = ((candidate.get("backend") or {}).get("kind")
                        if isinstance(candidate, dict) else None)
                if kind in ("rrf", "hybrid"):
                    candidate_inputs = [baseline] + [
                        _load_json(path, "candidate input receipt")
                        for path in args.candidate_input_receipt
                    ]
                elif args.candidate_input_receipt:
                    raise evaluation.RetrievalEvaluationError(
                        "candidate input receipts are valid only for RRF/hybrid candidates")
            decision = evaluation.decide(
                gold_document, baseline, candidate,
                resamples=args.bootstrap_resamples,
                candidate_input_documents=candidate_inputs)
    else:
        decision = evaluation.decide(gold_document)

    if baseline is not None:
        decision["baseline_receipt_sha256"] = evaluation.canonical_sha256(baseline)
    if candidate is not None:
        decision["candidate_receipt_sha256"] = evaluation.canonical_sha256(candidate)
    if args.baseline_out and baseline is not None:
        _atomic_json(args.baseline_out, baseline)
    if args.out:
        _atomic_json(args.out, decision)
    print(json.dumps(decision, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


def main(argv=None):
    try:
        return run(argv)
    except (evaluation.RetrievalEvaluationError, candidates.CandidateError,
            OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("run_retrieval_gate: %s\n" % exc)
        return 2
    except SystemExit as exc:
        # retrieve.load_index uses SystemExit for its fail-closed integrity errors.
        if isinstance(exc.code, int):
            return exc.code
        return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
