#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect, claim, validate, apply, and rebuild typed ingestion review work."""

import argparse
import collections
import json
import os
import sys

try:
    from ingestion import IngestionStore, ReviewPatch, atomic_write_json, read_json
    from ingestion.pipeline import (
        BUILD_MANIFEST_PATH,
        compile_review_outputs,
        refresh_build_manifest,
    )
except ImportError:
    from scripts.ingestion import IngestionStore, ReviewPatch, atomic_write_json, read_json
    from scripts.ingestion.pipeline import (
        BUILD_MANIFEST_PATH,
        compile_review_outputs,
        refresh_build_manifest,
    )


def _die(message, code=2):
    sys.stderr.write("ingest_review: %s\n" % message)
    raise SystemExit(code)


def _store(workspace):
    root = os.path.abspath(workspace)
    if not os.path.isdir(root):
        _die("workspace does not exist: %s" % root)
    manifest_path = os.path.join(root, *BUILD_MANIFEST_PATH.split("/"))
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        _die("cannot read .ingest/build_manifest.json: %s" % exc)
    source_root = manifest.get("source_root") if isinstance(manifest, dict) else None
    if not isinstance(source_root, str) or not os.path.isdir(source_root):
        _die("source_root is missing or no longer exists")
    return root, IngestionStore(root, source_root=source_root)


def _issue_payload(issue):
    return issue.to_dict()


def _template(issue):
    return {
        "instructions": [
            "Use ReviewPatch.create(...) to generate stable patch_id after filling operations.",
            "Keep status=validated only after checking every evidence hash and source page.",
            "Allowed operations never mutate arbitrary workspace paths.",
        ],
        "issue": issue.to_dict(),
        "allowed_operation_shapes": [
            {"op": "add_unit", "unit": "<full ContentUnit object>"},
            {"op": "replace_unit", "unit_id": "<unit_id>", "unit": "<full ContentUnit object>"},
            {
                "op": "assign_chapter", "unit_id": "<unit_id>",
                "chapter": "<label>", "phase": "<label>",
                "chapter_id": "chNN", "phase_id": "phaseNN",
            },
            {
                "op": "pair_qa",
                "question_unit_id": "<unit_id>",
                "answer_unit_id": "<unit_id>",
            },
            {"op": "classify_asset", "unit_id": "<unit_id>", "asset_role": "<role>"},
            {"op": "mark_resolved", "reason": "<what the evidence confirms>"},
            {"op": "mark_unrecoverable", "reason": "<why evidence cannot be recovered>"},
        ],
    }


def _bounded_details_path(workspace, value):
    """Keep potentially huge detail exports inside the confirmed workspace."""
    if value is None:
        return None
    candidate = os.path.abspath(os.path.join(workspace, value))
    try:
        contained = os.path.commonpath((workspace, candidate)) == workspace
    except ValueError:
        contained = False
    if not contained:
        _die("--details-file must stay inside the workspace")
    parent = os.path.dirname(candidate)
    if not os.path.isdir(parent):
        _die("--details-file parent does not exist: %s" % parent)
    if os.path.lexists(candidate) and os.path.islink(candidate):
        _die("--details-file must not be a symbolic link")
    return candidate


def _issue_summary(issues):
    by_status = collections.Counter()
    by_severity = collections.Counter()
    by_reason = collections.Counter()
    for issue in issues:
        by_status[issue.status] += 1
        by_severity[issue.severity] += 1
        for reason in issue.reason_codes:
            by_reason[reason] += 1
    return {
        "by_status": dict(sorted(by_status.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_reason": dict(sorted(by_reason.items())),
    }


def _load_patch(path):
    try:
        return ReviewPatch.from_dict(read_json(path))
    except Exception as exc:
        _die("patch validation failed for %s: %s" % (path, exc))


def _batch_patch_paths(args):
    positional = list(args.patch_files or ())
    if args.patch_list and positional:
        _die("apply-batch accepts either positional patch files or --patch-list, not both")
    if args.patch_list:
        try:
            listed = read_json(args.patch_list)
        except Exception as exc:
            _die("cannot read --patch-list: %s" % exc)
        if (not isinstance(listed, list) or not listed
                or any(not isinstance(path, str) or not path.strip() for path in listed)):
            _die("--patch-list must be a non-empty JSON list of patch-file paths")
        positional = listed
    if not positional:
        _die("apply-batch requires patch files or --patch-list")
    if len(set(positional)) != len(positional):
        _die("apply-batch patch-file paths must be unique")
    return positional


def _apply_patch_batch(workspace, store, patch_paths):
    patches = []
    patch_ids = set()
    issue_ids = set()
    for path in patch_paths:
        patch = _load_patch(path)
        if patch.patch_id in patch_ids:
            _die("apply-batch contains duplicate patch_id: %s" % patch.patch_id)
        if patch.issue_id in issue_ids:
            _die("apply-batch requires one patch per issue_id: %s" % patch.issue_id)
        patch_ids.add(patch.patch_id)
        issue_ids.add(patch.issue_id)
        patches.append((path, patch))

    results = []
    for index, (path, patch) in enumerate(patches):
        try:
            result = store.apply_patch(patch)
        except Exception as exc:
            try:
                compile_review_outputs(workspace)
            except Exception as rebuild_exc:
                _die(
                    "batch patch %d/%d failed after %d ledger entries: %s; "
                    "rebuild also failed: %s"
                    % (index + 1, len(patches), len(results), exc, rebuild_exc),
                    code=1,
                )
            _die(
                "batch patch %d/%d failed after %d ledger entries: %s"
                % (index + 1, len(patches), len(results), exc),
                code=1,
            )
        results.append({
            "patch_file": path,
            "patch_id": patch.patch_id,
            "issue_id": patch.issue_id,
            "applied": result.applied,
            "replayed": result.replayed,
            "changed_operations": result.changed_operations,
            "issue_status": result.issue_status,
        })
    compiled = compile_review_outputs(workspace)
    return {
        "patch_count": len(results),
        "applied_count": sum(1 for result in results if result["applied"]),
        "replayed_count": sum(1 for result in results if result["replayed"]),
        "results": results,
        "compiled": compiled,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Typed AI/human review queue for .ingest workspaces"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list review issues")
    list_parser.add_argument(
        "--status", action="append",
        help="filter by status (repeatable; default all)",
    )
    list_parser.add_argument(
        "--reason", action="append",
        help="filter by reason code (repeatable; any matching reason is included)",
    )
    list_parser.add_argument(
        "--cursor", type=int, default=0,
        help="zero-based resume cursor into the stable filtered queue",
    )
    list_parser.add_argument(
        "--limit", type=int, default=50,
        help="maximum issues returned in chat-safe JSON (1-200; default 50)",
    )
    list_parser.add_argument(
        "--summary-only", action="store_true",
        help="return counts without issue bodies",
    )
    list_parser.add_argument(
        "--details-file",
        help="optional workspace-relative JSON file receiving the complete filtered issue list",
    )
    show_parser = sub.add_parser("show", help="show one issue and patch operation shapes")
    show_parser.add_argument("issue_id")
    claim_parser = sub.add_parser("claim", help="atomically claim one pending issue")
    claim_parser.add_argument("issue_id")
    validate_parser = sub.add_parser("validate-patch", help="strictly validate a patch JSON")
    validate_parser.add_argument("patch_file")
    apply_parser = sub.add_parser("apply", help="apply a validated patch and rebuild derivatives")
    apply_parser.add_argument("patch_file")
    batch_parser = sub.add_parser(
        "apply-batch",
        help="apply separate validated patches and rebuild expensive derivatives once",
    )
    batch_parser.add_argument(
        "patch_files", nargs="*",
        help="validated patch JSON files (each patch must target a distinct issue)",
    )
    batch_parser.add_argument(
        "--patch-list",
        help="JSON file containing a non-empty list of validated patch-file paths",
    )
    mark_parser = sub.add_parser(
        "mark-unrecoverable", help="close one issue with an evidence-bound terminal patch"
    )
    mark_parser.add_argument("issue_id")
    mark_parser.add_argument("--reason", required=True)
    mark_parser.add_argument(
        "--evidence-note",
        help="issue-specific pages/files/search scope reviewed before declaring recovery impossible",
    )
    mark_parser.add_argument("--reviewer", default="ai")
    resolved_parser = sub.add_parser(
        "mark-resolved", help="confirm from evidence that extraction is already complete"
    )
    resolved_parser.add_argument("issue_id")
    resolved_parser.add_argument("--reason", required=True)
    resolved_parser.add_argument("--reviewer", default="ai")
    sub.add_parser("pending", help="inspect an interrupted review-patch intent")
    sub.add_parser(
        "recover-pending",
        help="idempotently resume the exact interrupted review patch and rebuild",
    )
    sub.add_parser("rebuild", help="recompile wiki/quiz/index from current applied IR")

    args = parser.parse_args(argv)
    workspace, store = _store(args.workspace)

    if args.command == "list":
        if args.cursor < 0:
            _die("--cursor must be >= 0")
        if args.limit < 1 or args.limit > 200:
            _die("--limit must be between 1 and 200")
        statuses = set(args.status or ())
        reasons = set(args.reason or ())
        issues = [
            issue for issue in store.review_queue.issues()
            if (not statuses or issue.status in statuses)
            and (not reasons or reasons.intersection(issue.reason_codes))
        ]
        total = len(issues)
        if args.cursor > total:
            _die("--cursor %d exceeds filtered issue count %d" % (args.cursor, total))
        details_path = _bounded_details_path(workspace, args.details_file)
        if details_path is not None:
            atomic_write_json(details_path, {
                "workspace": workspace,
                "count": total,
                "summary": _issue_summary(issues),
                "issues": [_issue_payload(issue) for issue in issues],
            })
        page = [] if args.summary_only else issues[args.cursor:args.cursor + args.limit]
        next_cursor = args.cursor + len(page)
        payload = {
            "workspace": workspace,
            "count": total,
            "returned": len(page),
            "cursor": args.cursor,
            "next_cursor": next_cursor if next_cursor < total else None,
            "has_more": next_cursor < total,
            "summary": _issue_summary(issues),
            "details_file": details_path,
            "issues": [_issue_payload(issue) for issue in page],
        }
    elif args.command == "show":
        issue = store.review_queue.get(args.issue_id)
        if issue is None:
            _die("unknown issue_id: %s" % args.issue_id)
        payload = _template(issue)
    elif args.command == "claim":
        try:
            issue = store.claim_issue(args.issue_id)
            refresh_build_manifest(workspace)
        except Exception as exc:
            _die("claim failed: %s" % exc)
        payload = {"claimed": True, "issue": issue.to_dict()}
    elif args.command in ("validate-patch", "apply"):
        patch = _load_patch(args.patch_file)
        if args.command == "validate-patch":
            try:
                store.validate_patch(patch)
            except Exception as exc:
                _die("patch contextual validation failed: %s" % exc)
            payload = {"valid": True, "patch": patch.to_dict()}
        else:
            try:
                result = store.apply_patch(patch)
                compiled = compile_review_outputs(workspace)
            except Exception as exc:
                _die("patch application/rebuild failed: %s" % exc, code=1)
            payload = {
                "applied": result.applied,
                "replayed": result.replayed,
                "changed_operations": result.changed_operations,
                "issue_status": result.issue_status,
                "compiled": compiled,
            }
    elif args.command == "apply-batch":
        payload = _apply_patch_batch(
            workspace, store, _batch_patch_paths(args)
        )
    elif args.command in ("mark-resolved", "mark-unrecoverable"):
        issue = store.review_queue.get(args.issue_id)
        if issue is None:
            _die("unknown issue_id: %s" % args.issue_id)
        evidence_note = getattr(args, "evidence_note", None)
        if (args.command == "mark-unrecoverable"
                and "missing_answer" in issue.reason_codes
                and not (evidence_note or "").strip()):
            _die(
                "missing_answer cannot be marked unrecoverable with a generic reason; "
                "provide --evidence-note naming the candidate files/pages and search scope"
            )
        try:
            operation = (
                "mark_resolved" if args.command == "mark-resolved"
                else "mark_unrecoverable"
            )
            reason = args.reason.strip()
            if args.command == "mark-unrecoverable" and evidence_note:
                reason += "\nEvidence review: " + evidence_note.strip()
            patch = ReviewPatch.create(
                issue.issue_id,
                issue.source_id,
                issue.source_sha256,
                [{"op": operation, "reason": reason}],
                list(issue.evidence),
                reviewer=args.reviewer,
                status="validated",
            )
            result = store.apply_patch(patch)
            compiled = compile_review_outputs(workspace)
        except Exception as exc:
            _die("%s failed: %s" % (args.command, exc), code=1)
        payload = {
            "patch": patch.to_dict(),
            "issue_status": result.issue_status,
            "compiled": compiled,
        }
    elif args.command == "pending":
        try:
            pending = read_json(store.pending_patch_path, default=None)
        except Exception as exc:
            _die("cannot inspect pending patch: %s" % exc)
        payload = {"pending": pending is not None, "intent": pending}
    elif args.command == "recover-pending":
        try:
            pending = read_json(store.pending_patch_path, default=None)
            if pending is None:
                payload = {"recovered": False, "reason": "no_pending_patch"}
            else:
                expected = {"schema_version", "patch_id", "fingerprint", "patch"}
                if not isinstance(pending, dict) or set(pending) != expected:
                    raise ValueError("pending patch intent has an invalid schema")
                patch = ReviewPatch.from_dict(pending["patch"])
                if patch.patch_id != pending["patch_id"]:
                    raise ValueError("pending patch_id disagrees with embedded patch")
                result = store.apply_patch(patch)
                compiled = compile_review_outputs(workspace)
                payload = {
                    "recovered": True,
                    "applied": result.applied,
                    "replayed": result.replayed,
                    "issue_status": result.issue_status,
                    "compiled": compiled,
                }
        except Exception as exc:
            _die("pending patch recovery failed: %s" % exc, code=1)
    elif args.command == "rebuild":
        try:
            payload = compile_review_outputs(workspace)
        except Exception as exc:
            _die("rebuild failed: %s" % exc, code=1)
    else:
        _die("unknown command")

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
