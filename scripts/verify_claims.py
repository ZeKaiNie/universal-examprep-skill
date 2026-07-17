#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Import exact-location claims and issue chapter-bound verification receipts."""

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    from .ingestion.claims import (
        CLAIM_RECEIPTS_DIR,
        CLAIM_RECORDS_PATH,
        ClaimValidationError,
        canonical_fact_snapshot_sha256,
        canonical_manifest_sha256,
        compile_claim_proposals,
        _import_claim_records_locked,
        load_claim_records,
        read_claim_jsonl,
        validate_guide_claim_coverage,
        verify_claim_batch,
        verify_claim_records,
    )
    from .ingestion.dedup import (
        load_canonical_groups,
        load_source_conflicts,
        validate_workspace_fact_integrity,
    )
    from .ingestion.identifiers import file_sha256, is_link_or_reparse, safe_workspace_entry
    from .ingestion.models import ContentUnit, SchemaValidationError, SourceRecord
    from .ingestion.pipeline import verify_material_build_receipt
    from .ingestion.storage import (
        ConflictError, atomic_write_json, read_jsonl,
        workspace_publication_lock,
    )
except ImportError:
    from ingestion.claims import (
        CLAIM_RECEIPTS_DIR,
        CLAIM_RECORDS_PATH,
        ClaimValidationError,
        canonical_fact_snapshot_sha256,
        canonical_manifest_sha256,
        compile_claim_proposals,
        _import_claim_records_locked,
        load_claim_records,
        read_claim_jsonl,
        validate_guide_claim_coverage,
        verify_claim_batch,
        verify_claim_records,
    )
    from ingestion.dedup import (
        load_canonical_groups,
        load_source_conflicts,
        validate_workspace_fact_integrity,
    )
    from ingestion.identifiers import file_sha256, is_link_or_reparse, safe_workspace_entry
    from ingestion.models import ContentUnit, SchemaValidationError, SourceRecord
    from ingestion.pipeline import verify_material_build_receipt
    from ingestion.storage import (
        ConflictError,
        atomic_write_json,
        read_jsonl,
        workspace_publication_lock,
    )


SOURCE_MANIFEST_PATH = ".ingest/source_manifest.json"
CONTENT_UNITS_PATH = ".ingest/content_units.jsonl"
CANONICAL_GROUPS_PATH = ".ingest/canonical_groups.jsonl"
SOURCE_CONFLICTS_PATH = ".ingest/source_conflicts.jsonl"
BUILD_MANIFEST_PATH = ".ingest/build_manifest.json"


def _workspace(value):
    root = Path(os.path.abspath(value))
    if is_link_or_reparse(root) or not root.is_dir():
        raise ClaimValidationError("workspace must be an existing non-symlink directory")
    return root.resolve()


def _entry(root, relative_path, label, must_exist=True):
    try:
        path = safe_workspace_entry(root, relative_path)
    except ValueError as exc:
        raise ClaimValidationError("%s: %s" % (label, exc)) from exc
    if must_exist and (path.is_symlink() or not path.is_file()):
        raise ClaimValidationError("%s must be a regular non-symlink file" % label)
    return path


def _canonical_claim_path(value):
    if value != CLAIM_RECORDS_PATH:
        raise ClaimValidationError(
            "--claims is fixed to %s for runtime claim operations" % CLAIM_RECORDS_PATH
        )
    return value


def _canonical_receipt_path(value, chapter_id):
    expected = "%s/%s.json" % (CLAIM_RECEIPTS_DIR, chapter_id)
    if value not in (None, expected):
        raise ClaimValidationError(
            "--receipt is fixed to %s for this chapter" % expected
        )
    return expected


def _pipeline_version(root):
    document = _read_strict_json(
        _entry(root, BUILD_MANIFEST_PATH, "ingestion build manifest")
    )
    if (not isinstance(document, dict)
            or type(document.get("schema_version")) is not int
            or document.get("schema_version") not in (1, 2)):
        raise ClaimValidationError("ingestion build manifest has an invalid schema_version")
    try:
        verify_material_build_receipt(
            root,
            build_manifest=document,
            required=document["schema_version"] == 2,
        )
    except Exception as exc:
        raise ClaimValidationError(
            "ingestion material generation is invalid: %s" % exc
        ) from exc
    version = document.get("pipeline_version")
    if version not in ("ingestion-v1", "ingestion-v2"):
        raise ClaimValidationError("ingestion build manifest pipeline_version is unsupported")
    return version


@contextmanager
def _claim_workspace_lock(root):
    ingest = safe_workspace_entry(root, ".ingest")
    if ingest.is_symlink() or not ingest.is_dir():
        raise ClaimValidationError("claim operations require a real .ingest directory")
    try:
        with workspace_publication_lock(root):
            yield
    except (ConflictError, SchemaValidationError, OSError) as exc:
        raise ClaimValidationError("cannot mutate claim workspace: %s" % exc) from exc


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClaimValidationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ClaimValidationError("non-finite JSON constant is not allowed: %s" % value)


def _read_strict_json(path):
    with open(path, "r", encoding="utf-8") as stream:
        try:
            return json.load(
                stream,
                object_pairs_hook=_no_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except json.JSONDecodeError as exc:
            raise ClaimValidationError("invalid JSON in %s: %s" % (path, exc)) from exc


def _load_sources(root):
    payload = _read_strict_json(_entry(root, SOURCE_MANIFEST_PATH, "source manifest"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "sources"}:
        raise ClaimValidationError("source manifest has an invalid exact schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ClaimValidationError("source manifest schema_version must be 1")
    if not isinstance(payload["sources"], list):
        raise ClaimValidationError("source manifest sources must be an array")
    rows = tuple(SourceRecord.from_dict(row) for row in payload["sources"])
    if len({row.source_id for row in rows}) != len(rows):
        raise ClaimValidationError("source manifest contains duplicate source IDs")
    return rows


def _load_units(root):
    rows = tuple(
        ContentUnit.from_dict(row)
        for row in read_jsonl(_entry(root, CONTENT_UNITS_PATH, "content units"))
    )
    if len({row.unit_id for row in rows}) != len(rows):
        raise ClaimValidationError("content units contain duplicate IDs")
    return rows


def _chapter(value):
    if type(value) is not int or value < 1:
        raise ClaimValidationError("--chapter must be an integer >= 1")
    return "ch%02d" % value


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print("ok=%s operation=%s" % (str(payload.get("ok", False)).lower(), payload.get("operation", "verify")))
    if "path" in payload:
        print("path=%s" % payload["path"])
    if "receipt" in payload:
        print("receipt_id=%s" % payload["receipt"]["receipt_id"])


def _run_import(args):
    root = _workspace(args.workspace)
    claims_relative = _canonical_claim_path(args.claims)
    source = Path(os.path.abspath(args.input_claims))
    if source.is_symlink() or not source.is_file():
        raise ClaimValidationError("--input-claims must be a regular non-symlink file")
    rows = read_claim_jsonl(source)
    units = _load_units(root)
    sources = _load_sources(root)
    verify_claim_batch(rows, units, sources, workspace=root)
    imported = _import_claim_records_locked(root, rows)
    destination = _entry(root, claims_relative, "claim sidecar")
    return {
        "ok": True,
        "operation": "import",
        "verification_scope": "location_only",
        "claim_count": len(imported),
        "claim_records_sha256": file_sha256(destination),
        "path": os.path.relpath(destination, root).replace("\\", "/"),
    }


def _run_create(args):
    root = _workspace(args.workspace)
    claims_relative = _canonical_claim_path(args.claims)
    source = Path(os.path.abspath(args.input_proposals))
    if source.is_symlink() or not source.is_file():
        raise ClaimValidationError("--input-proposals must be a regular non-symlink file")
    document = _read_strict_json(source)
    if not isinstance(document, dict) or set(document) != {"schema_version", "proposals"}:
        raise ClaimValidationError("proposal document must contain exactly schema_version and proposals")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ClaimValidationError("proposal document schema_version must be 1")
    units = _load_units(root)
    sources = _load_sources(root)
    rows = compile_claim_proposals(
        document["proposals"], units, sources, workspace=root
    )
    destination = _entry(root, claims_relative, "claim sidecar", must_exist=False)
    retained = ()
    if not args.replace_all and destination.is_file() and not destination.is_symlink():
        existing = load_claim_records(root, claims_relative)
        incoming_subjects = {
            (
                row.subject.chapter_id,
                row.subject.entity_type,
                row.subject.entity_id,
                row.subject.field,
                row.subject.language,
                row.subject.claim_index,
            )
            for row in rows
        }
        retained = tuple(
            row for row in existing
            if (
                row.subject.chapter_id,
                row.subject.entity_type,
                row.subject.entity_id,
                row.subject.field,
                row.subject.language,
                row.subject.claim_index,
            ) not in incoming_subjects
        )
        # A merge must not preserve stale source revisions invisibly.  Update or
        # deliberately replace the full sidecar when a retained record drifts.
        verify_claim_batch(retained, units, sources, workspace=root)
    imported = _import_claim_records_locked(root, retained + tuple(rows))
    return {
        "ok": True,
        "operation": "create",
        "verification_scope": "location_only",
        "claim_count": len(imported),
        "created_claim_count": len(rows),
        "retained_claim_count": len(retained),
        "replace_all": bool(args.replace_all),
        "claim_ids": [row.claim_id for row in rows],
        "claim_records_sha256": file_sha256(destination),
        "path": os.path.relpath(destination, root).replace("\\", "/"),
    }


def _run_verify(args):
    root = _workspace(args.workspace)
    chapter_id = _chapter(args.chapter)
    claims_relative = _canonical_claim_path(args.claims)
    receipt_relative = _canonical_receipt_path(args.receipt, chapter_id)
    manifest_path = _entry(root, args.manifest, "guide manifest")
    # A verification receipt must not bless malformed JSON, even though it
    # deliberately does not interpret the manifest's semantic assertions.
    manifest = _read_strict_json(manifest_path)
    claim_path = _entry(root, claims_relative, "claim sidecar")
    source_manifest_path = _entry(root, SOURCE_MANIFEST_PATH, "source manifest")
    content_units_path = _entry(root, CONTENT_UNITS_PATH, "content units")
    canonical_groups_path = _entry(root, CANONICAL_GROUPS_PATH, "canonical groups")
    source_conflicts_path = _entry(root, SOURCE_CONFLICTS_PATH, "source conflicts")

    pipeline_version = _pipeline_version(root)
    fact_integrity = (
        validate_workspace_fact_integrity(root)
        if pipeline_version == "ingestion-v2" else None
    )
    rows = load_claim_records(
        root,
        claims_relative,
        allow_empty=(pipeline_version == "ingestion-v2"),
    )
    units = _load_units(root)
    sources = _load_sources(root)
    # v1 strictly parses the two receipt-bound sidecars.  v2 first validates the
    # complete manifest-bound/live-rederived fact snapshot above.
    if fact_integrity is None:
        load_canonical_groups(root, CANONICAL_GROUPS_PATH)
        conflicts = load_source_conflicts(root, SOURCE_CONFLICTS_PATH)
    else:
        conflicts = fact_integrity["conflicts"]
    if pipeline_version == "ingestion-v2":
        unresolved = sorted(
            conflict.conflict_id
            for conflict in conflicts
            if conflict.status == "unresolved"
        )
        if unresolved:
            raise ClaimValidationError(
                "ingestion-v2 claim verification is blocked by unresolved source "
                "conflicts: %r" % unresolved
            )
        validate_guide_claim_coverage(rows, manifest, chapter_id, units)
    bound_hashes = {
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "content_units_sha256": file_sha256(content_units_path),
        "canonical_groups_sha256": file_sha256(canonical_groups_path),
        "source_conflicts_sha256": file_sha256(source_conflicts_path),
        "claim_records_sha256": file_sha256(claim_path),
    }
    fact_snapshot = fact_integrity["snapshot"] if fact_integrity is not None else {
        "schema_version": 1,
        "pipeline_version": "ingestion-v1",
        "inputs": dict(bound_hashes),
    }
    receipt = verify_claim_records(
        rows,
        units,
        sources,
        chapter_id,
        manifest=manifest,
        guide_content_sha256=canonical_manifest_sha256(manifest),
        fact_snapshot_sha256=canonical_fact_snapshot_sha256(fact_snapshot),
        workspace=root,
        **bound_hashes,
    )
    receipt_path = _entry(root, receipt_relative, "receipt destination", must_exist=False)
    def recheck_before_receipt_publish():
        if fact_integrity is None:
            return
        current = validate_workspace_fact_integrity(root)["snapshot"]
        if current != fact_integrity["snapshot"]:
            raise ClaimValidationError(
                "ingestion fact inputs changed during claim receipt publication"
            )

    atomic_write_json(
        receipt_path,
        receipt.to_dict(),
        before_publish=recheck_before_receipt_publish,
    )
    return {
        "ok": True,
        "operation": "verify",
        "verification_scope": "location_only",
        "path": os.path.relpath(receipt_path, root).replace("\\", "/"),
        "receipt": receipt.to_dict(),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="Import and verify exact Unicode code-point source-location claims"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    importer = subparsers.add_parser("import", help="strictly validate and atomically import claims")
    importer.add_argument("--workspace", required=True)
    importer.add_argument("--input-claims", required=True)
    importer.add_argument("--claims", default=CLAIM_RECORDS_PATH)
    importer.add_argument("--json", action="store_true")

    creator = subparsers.add_parser(
        "create", help="compile ergonomic proposals and atomically import full claims"
    )
    creator.add_argument("--workspace", required=True)
    creator.add_argument("--input-proposals", required=True)
    creator.add_argument("--claims", default=CLAIM_RECORDS_PATH)
    creator.add_argument(
        "--replace-all",
        action="store_true",
        help="replace the complete claim sidecar instead of merging proposal subjects",
    )
    creator.add_argument("--json", action="store_true")

    verifier = subparsers.add_parser("verify", help="verify a chapter and write its exact receipt")
    verifier.add_argument("--workspace", required=True)
    verifier.add_argument("--manifest", required=True, help="workspace-relative guide content/manifest JSON")
    verifier.add_argument("--claims", default=CLAIM_RECORDS_PATH)
    verifier.add_argument("--chapter", required=True, type=int)
    verifier.add_argument("--receipt", help="workspace-relative output receipt path")
    verifier.add_argument("--json", action="store_true")
    return parser


def run(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    root = _workspace(args.workspace)
    with _claim_workspace_lock(root):
        if args.command == "import":
            payload = _run_import(args)
        elif args.command == "create":
            payload = _run_create(args)
        else:
            payload = _run_verify(args)
    _emit(payload, args.json)
    return 0


def main():
    try:
        return run()
    except (ClaimValidationError, SchemaValidationError, ValueError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("verify_claims: %s\n" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
