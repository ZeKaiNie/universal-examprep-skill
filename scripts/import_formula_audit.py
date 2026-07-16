#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from ingestion import (
        FORMULA_FALSE_POSITIVE_REASON_PREFIX,
        ContentUnit,
        IngestionStore,
        ReviewPatch,
        atomic_write_json,
        is_language_neutral_formula,
        is_link_or_reparse,
        read_json,
        safe_workspace_entry,
    )
    from ingestion.pipeline import BUILD_MANIFEST_PATH
    from ingestion.storage import stable_read_json
except ImportError:
    from scripts.ingestion import (
        FORMULA_FALSE_POSITIVE_REASON_PREFIX,
        ContentUnit,
        IngestionStore,
        ReviewPatch,
        atomic_write_json,
        is_language_neutral_formula,
        is_link_or_reparse,
        read_json,
        safe_workspace_entry,
    )
    from scripts.ingestion.pipeline import BUILD_MANIFEST_PATH
    from scripts.ingestion.storage import stable_read_json


ACTIVE_REVIEW_STATUSES = frozenset(("pending", "claimed", "validated"))
FORMULA_FIELDS = ("latex_formulas", "formulas_latex")
SEMANTIC_FIELDS = ("semantic_en", "semantic")
_AUDIT_REQUIRED = frozenset((
    "issue_id", "source_file", "page", "evidence", "false_positive",
))
_AUDIT_OPTIONAL = frozenset(("source_id", "source_sha256"))
_EVIDENCE_OBJECT_REQUIRED = frozenset(("pdf_path", "source_sha256", "method"))
_EVIDENCE_OBJECT_OPTIONAL = frozenset((
    "render_path", "visual_note", "issue_evidence",
))
_ORDINAL_BUCKET = 10_000
_ORDINAL_OFFSET = 1_000_000_000


class FormulaAuditImportError(ValueError):
    pass


def _fail(message):
    raise FormulaAuditImportError(message)


def _nonempty(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("%s must be a non-empty, trimmed string" % label)
    if any(
            char == "\ufffd"
            or ord(char) == 0x7F
            or (ord(char) < 32 and char not in "\t\n\r")
            for char in value):
        _fail("%s contains unsafe control text" % label)
    return value


def _validate_latex(value, label):
    latex = _nonempty(value, label)
    depth = 0
    escaped = False
    for char in latex:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                _fail("%s has an unmatched closing brace" % label)
    if depth:
        _fail("%s has unbalanced braces" % label)
    if not is_language_neutral_formula(latex, latex=latex, kind="formula"):
        _fail("%s is not formula/symbol-only and cannot use source_language=zxx" % label)
    return latex


def _read_stable_json(path, label):
    candidate = Path(os.path.abspath(str(path)))
    if not candidate.is_file() or is_link_or_reparse(candidate):
        _fail("%s must be a regular non-link JSON file: %s" % (label, candidate))
    try:
        value, snapshot = stable_read_json(candidate)
    except Exception as exc:
        _fail("cannot read %s %s: %s" % (label, candidate, exc))
    return candidate, value, snapshot


def _open_store(workspace):
    root = Path(os.path.abspath(str(workspace)))
    if not root.is_dir() or is_link_or_reparse(root):
        _fail("workspace must be an existing regular directory: %s" % root)
    manifest_path = root.joinpath(*BUILD_MANIFEST_PATH.split("/"))
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        _fail("cannot read .ingest/build_manifest.json: %s" % exc)
    source_root = manifest.get("source_root") if isinstance(manifest, dict) else None
    if not isinstance(source_root, str) or not os.path.isdir(source_root):
        _fail("build manifest source_root is missing or no longer exists")
    try:
        return root, IngestionStore(root, source_root=source_root)
    except Exception as exc:
        _fail("cannot mount ingestion store: %s" % exc)


def _validate_issue_evidence(workspace, issue, source_file, page):
    if len(issue.evidence) != 1:
        _fail("%s must have exactly one unambiguous evidence record" % issue.issue_id)
    ref = issue.evidence[0]
    try:
        absolute = safe_workspace_entry(workspace, ref.path)
    except Exception as exc:
        _fail("%s has unsafe issue evidence: %s" % (issue.issue_id, exc))
    if not absolute.is_file() or is_link_or_reparse(absolute):
        _fail("%s issue evidence is missing or hash-drifted" % issue.issue_id)
    try:
        payload, snapshot = stable_read_json(absolute)
    except Exception as exc:
        _fail("%s issue evidence is not readable typed JSON: %s" % (issue.issue_id, exc))
    if snapshot["sha256"] != ref.sha256:
        _fail("%s issue evidence is missing or hash-drifted" % issue.issue_id)
    expected_top = {
        "schema_version", "source_file", "source_id", "source_sha256", "candidate",
    }
    if not isinstance(payload, dict) or set(payload) != expected_top:
        _fail("%s issue evidence has an unexpected schema" % issue.issue_id)
    candidate = payload.get("candidate")
    expected_candidate = {
        "pages", "reason_codes", "source_file", "target_unit_ids",
    }
    if not isinstance(candidate, dict) or set(candidate) != expected_candidate:
        _fail("%s issue candidate evidence has an unexpected schema" % issue.issue_id)
    expected = {
        "source_file": source_file,
        "source_id": issue.source_id,
        "source_sha256": issue.source_sha256,
    }
    if any(payload[key] != value for key, value in expected.items()):
        _fail("%s issue evidence source identity does not match the queue" % issue.issue_id)
    if (payload["schema_version"] != 1
            or candidate["pages"] != [page]
            or candidate["reason_codes"] != ["formula_hint"]
            or candidate["source_file"] != source_file
            or candidate["target_unit_ids"] != list(issue.target_unit_ids)):
        _fail("%s issue evidence locator does not match the queue" % issue.issue_id)
    return ref


def _same_absolute_path(left, right):
    try:
        return os.path.samefile(
            os.path.abspath(str(left)), os.path.abspath(str(right))
        )
    except (OSError, ValueError, TypeError):
        return False


def _validate_audit_evidence(raw, issue, record, store, issue_ref, label):
    if isinstance(raw, str):
        if raw != issue_ref.path:
            _fail("%s must name the exact issue evidence path" % label)
        return False
    if not isinstance(raw, dict):
        _fail("%s must be an issue-evidence path or a visual-review metadata object" % label)
    keys = set(raw)
    missing = _EVIDENCE_OBJECT_REQUIRED - keys
    unknown = keys - _EVIDENCE_OBJECT_REQUIRED - _EVIDENCE_OBJECT_OPTIONAL
    if missing or unknown:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (
            label, sorted(missing), sorted(unknown)
        ))
    if raw["source_sha256"] != issue.source_sha256:
        _fail("%s source_sha256 does not match the active issue" % label)
    _nonempty(raw["method"], label + ".method")
    expected_source = Path(store.manifest.source_root).joinpath(*record.path.split("/"))
    if not isinstance(raw["pdf_path"], str) or not _same_absolute_path(
            raw["pdf_path"], expected_source):
        _fail("%s pdf_path does not identify the current manifest source" % label)
    if "issue_evidence" in raw and raw["issue_evidence"] != issue_ref.path:
        _fail("%s issue_evidence does not match the queue" % label)
    if "render_path" in raw:
        _nonempty(raw["render_path"], label + ".render_path")
    if "visual_note" in raw:
        _nonempty(raw["visual_note"], label + ".visual_note")
    # render_path is deliberately not opened, hashed, copied, or attached to the patch.
    return "render_path" in raw


def _parse_audit_row(raw, label):
    if not isinstance(raw, dict):
        _fail("%s must be an object" % label)
    formula_fields = [field for field in FORMULA_FIELDS if field in raw]
    semantic_fields = [field for field in SEMANTIC_FIELDS if field in raw]
    if len(formula_fields) != 1 or len(semantic_fields) != 1:
        _fail("%s must contain exactly one formula alias and one semantic alias" % label)
    allowed = _AUDIT_REQUIRED | _AUDIT_OPTIONAL | set(formula_fields) | set(semantic_fields)
    missing = _AUDIT_REQUIRED - set(raw)
    unknown = set(raw) - allowed
    if missing or unknown:
        _fail("%s schema mismatch; missing=%r unknown=%r" % (
            label, sorted(missing), sorted(unknown)
        ))
    issue_id = _nonempty(raw["issue_id"], label + ".issue_id")
    source_file = _nonempty(raw["source_file"], label + ".source_file")
    page = raw["page"]
    if type(page) is not int or page < 1:
        _fail("%s.page must be an integer >= 1" % label)
    if type(raw["false_positive"]) is not bool:
        _fail("%s.false_positive must be a boolean" % label)
    semantic = _nonempty(raw[semantic_fields[0]], label + "." + semantic_fields[0])
    formulas_raw = raw[formula_fields[0]]
    if not isinstance(formulas_raw, list):
        _fail("%s.%s must be a list" % (label, formula_fields[0]))
    formulas = [
        _validate_latex(value, "%s.%s[%d]" % (label, formula_fields[0], index))
        for index, value in enumerate(formulas_raw)
    ]
    if len(set(formulas)) != len(formulas):
        _fail("%s contains duplicate LaTeX formulas" % label)
    false_positive = raw["false_positive"]
    if false_positive and formulas:
        _fail("%s false_positive audit must not contain formulas" % label)
    if not false_positive and not formulas:
        _fail("%s non-false-positive audit must contain at least one formula" % label)
    source_id = raw.get("source_id")
    source_sha256 = raw.get("source_sha256")
    if source_id is not None:
        _nonempty(source_id, label + ".source_id")
    if source_sha256 is not None:
        _nonempty(source_sha256, label + ".source_sha256")
    return {
        "issue_id": issue_id,
        "source_file": source_file,
        "page": page,
        "evidence": raw["evidence"],
        "false_positive": false_positive,
        "formulas": formulas,
        "semantic": semantic,
        "source_id": source_id,
        "source_sha256": source_sha256,
    }


def _formula_ordinal(issue_id, index):
    if type(index) is not int or index < 0 or index >= _ORDINAL_BUCKET:
        _fail("formula index is outside the deterministic ordinal bucket")
    return _ORDINAL_OFFSET + int(issue_id.split("_", 1)[-1][:12], 16) * _ORDINAL_BUCKET + index


def _page_assignment(units, source_id, source_sha256, page):
    scoped = [
        unit
        for unit in units.values()
        if unit.source_id == source_id
        and unit.source_sha256 == source_sha256
        and unit.page == page
        and (unit.chapter_id is not None or unit.phase_id is not None)
    ]
    chapters = {unit.chapter_id for unit in scoped if unit.chapter_id is not None}
    phases = {unit.phase_id for unit in scoped if unit.phase_id is not None}
    # A less-specific (chNN, null) semantic unit does not conflict with the
    # page anchor's (chNN, phaseNN) assignment. A solution-book page can
    # legitimately contain material mapped to more than one chapter; leave the
    # recovered formula unassigned instead of guessing one of them.
    if len(chapters) > 1 or len(phases) > 1:
        return None, None, True
    return (
        next(iter(chapters)) if chapters else None,
        next(iter(phases)) if phases else None,
        False,
    )


def _false_positive_reason(issue, source_file, page, semantic):
    pages = ",".join(str(value) for value in issue.pages)
    return (
        "%s issue_id=%s pages=%s\n"
        "Visual review of %s page %d, bound to the issue's content-addressed "
        "evidence, found no mathematical formula requiring recovery. %s"
    ) % (
        FORMULA_FALSE_POSITIVE_REASON_PREFIX,
        issue.issue_id,
        pages,
        source_file,
        page,
        semantic,
    )


def _existing_patch(path, expected, reviewer):
    if not path.exists():
        return None
    if not path.is_file() or is_link_or_reparse(path):
        _fail("existing patch output is not a regular file: %s" % path)
    try:
        existing = ReviewPatch.from_dict(read_json(path))
    except Exception as exc:
        _fail("existing patch output is invalid: %s: %s" % (path, exc))
    if (existing.patch_id != expected.patch_id
            or existing.reviewer != reviewer
            or existing.status != "validated"):
        _fail("existing patch output conflicts with the deterministic draft: %s" % path)
    return existing


def draft_formula_audits(
        workspace, audit_paths, output_dir, reviewer="ai-formula-audit",
        created_at=None):
    reviewer = _nonempty(reviewer, "reviewer")
    if not audit_paths:
        _fail("at least one audit JSON is required")
    root, store = _open_store(workspace)
    output = Path(os.path.abspath(str(output_dir)))
    if output.exists() and (not output.is_dir() or is_link_or_reparse(output)):
        _fail("output-dir must be a regular directory: %s" % output)

    audit_files = []
    rows = []
    seen_issues = set()
    ignored_render_paths = 0
    for audit_path in audit_paths:
        path, payload, snapshot = _read_stable_json(audit_path, "audit JSON")
        if not isinstance(payload, list) or not payload:
            _fail("audit JSON must contain a non-empty list: %s" % path)
        audit_files.append({"path": str(path), "sha256": snapshot["sha256"]})
        for index, raw in enumerate(payload):
            label = "%s[%d]" % (path, index)
            row = _parse_audit_row(raw, label)
            if row["issue_id"] in seen_issues:
                _fail("duplicate issue across audit inputs: %s" % row["issue_id"])
            seen_issues.add(row["issue_id"])
            rows.append(row)

    units = store.units()
    generated_formula_keys = set()
    existing_formula_keys = {
        (unit.source_id, unit.source_sha256, unit.page, unit.latex.strip())
        for unit in units.values()
        if unit.kind == "formula"
        and isinstance(unit.latex, str)
        and unit.latex.strip()
    }
    patches = []
    timestamp = created_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    formula_count = 0
    false_positive_count = 0
    ambiguous_assignment_issues = []
    verified_sources = {}
    issue_index = {
        issue.issue_id: issue for issue in store.review_queue.issues()
    }

    for row in sorted(rows, key=lambda value: value["issue_id"]):
        issue = issue_index.get(row["issue_id"])
        if issue is None:
            _fail("audit names an unknown issue: %s" % row["issue_id"])
        if issue.status not in ACTIVE_REVIEW_STATUSES:
            _fail("issue is not active for patch drafting: %s (%s)" % (
                issue.issue_id, issue.status
            ))
        if tuple(issue.reason_codes) != ("formula_hint",):
            _fail("issue is not a formula_hint-only issue: %s" % issue.issue_id)
        if tuple(issue.pages) != (row["page"],):
            _fail("audit page does not match issue pages: %s" % issue.issue_id)
        revision = (issue.source_id, issue.source_sha256)
        if revision not in verified_sources:
            try:
                verified_sources[revision] = store.manifest.verify_current(*revision)
            except Exception as exc:
                _fail("current source verification failed for %s: %s" % (
                    issue.issue_id, exc
                ))
        record = verified_sources[revision]
        if row["source_file"] != record.path:
            _fail("audit source_file does not match the manifest: %s" % issue.issue_id)
        if row["source_id"] is not None and row["source_id"] != issue.source_id:
            _fail("audit source_id does not match the issue: %s" % issue.issue_id)
        if (row["source_sha256"] is not None
                and row["source_sha256"] != issue.source_sha256):
            _fail("audit source_sha256 does not match the issue: %s" % issue.issue_id)
        issue_ref = _validate_issue_evidence(root, issue, record.path, row["page"])
        ignored_render_paths += int(_validate_audit_evidence(
            row["evidence"], issue, record, store, issue_ref,
            "%s.evidence" % issue.issue_id,
        ))

        if row["false_positive"]:
            if issue.severity != "warning":
                _fail("false_positive is permitted only for warning issues: %s" % issue.issue_id)
            operations = [{
                "op": "mark_resolved",
                "reason": _false_positive_reason(
                    issue, record.path, row["page"], row["semantic"]
                ),
            }]
            false_positive_count += 1
        else:
            chapter_id, phase_id, ambiguous_assignment = _page_assignment(
                units, issue.source_id, issue.source_sha256, row["page"]
            )
            if ambiguous_assignment:
                ambiguous_assignment_issues.append(issue.issue_id)
            operations = []
            for index, latex in enumerate(row["formulas"]):
                ordinal = _formula_ordinal(issue.issue_id, index)
                formula_key = (
                    issue.source_id, issue.source_sha256, row["page"], latex,
                )
                if (formula_key in existing_formula_keys
                        or formula_key in generated_formula_keys):
                    _fail("formula already exists at %s page %d: %s" % (
                        record.path, row["page"], latex
                    ))
                unit = ContentUnit.create(
                    source_id=issue.source_id,
                    source_sha256=issue.source_sha256,
                    source_file=record.path,
                    kind="formula",
                    text=latex,
                    latex=latex,
                    page=row["page"],
                    ordinal=ordinal,
                    chapter_id=chapter_id,
                    phase_id=phase_id,
                    metadata={"source_language": "zxx"},
                    method="vision",
                    confidence=1.0,
                    provenance="ai_recovered",
                )
                generated_formula_keys.add(formula_key)
                operations.append({"op": "add_unit", "unit": unit.to_dict()})
                formula_count += 1

        patch = ReviewPatch.create(
            issue.issue_id,
            issue.source_id,
            issue.source_sha256,
            operations,
            list(issue.evidence),
            reviewer=reviewer,
            created_at=timestamp,
            status="validated",
        )
        patch_path = output / ("formula-%s.patch.json" % issue.issue_id)
        patch = _existing_patch(patch_path, patch, reviewer) or patch
        patches.append((patch_path, patch))

    try:
        store.validate_patches([patch for _path, patch in patches])
    except Exception as exc:
        _fail("draft batch failed contextual validation: %s" % exc)

    patch_paths = [str(path) for path, _patch in patches]
    patch_list_path = output / "patch-list.json"
    summary_path = output / "formula-audit-import-summary.json"
    summary = {
        "schema_version": 1,
        "workspace": str(root),
        "audit_files": sorted(audit_files, key=lambda row: row["path"]),
        "output_dir": str(output),
        "reviewer": reviewer,
        "issue_count": len(patches),
        "formula_issue_count": len(patches) - false_positive_count,
        "false_positive_issue_count": false_positive_count,
        "formula_unit_count": formula_count,
        "ignored_render_path_count": ignored_render_paths,
        "unassigned_ambiguous_chapter_issue_ids": ambiguous_assignment_issues,
        "patch_files": patch_paths,
        "patch_list": str(patch_list_path),
        "summary_file": str(summary_path),
        "ingestion_state_mutation": {
            "claimed": False,
            "applied": False,
            "rebuilt": False,
        },
    }

    output.mkdir(parents=True, exist_ok=True)
    for path, patch in patches:
        atomic_write_json(path, patch.to_dict())
    atomic_write_json(patch_list_path, patch_paths)
    atomic_write_json(summary_path, summary)
    return summary


def _audit_paths(args):
    paths = list(args.audit or ()) + list(args.audit_files or ())
    if not paths:
        _fail("provide one or more --audit JSON files")
    absolute = [os.path.abspath(path) for path in paths]
    if len(set(map(os.path.normcase, absolute))) != len(absolute):
        _fail("audit JSON paths must be unique")
    return absolute


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Draft typed ingestion patches from visual formula-audit JSON."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--audit", action="append",
        help="audit JSON path; repeat for multiple inputs",
    )
    parser.add_argument("audit_files", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--reviewer", default="ai-formula-audit")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        summary = draft_formula_audits(
            args.workspace,
            _audit_paths(args),
            args.output_dir,
            reviewer=args.reviewer,
        )
    except FormulaAuditImportError as exc:
        sys.stderr.write("import_formula_audit: %s\n" % exc)
        return 2
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            "Drafted %(issue_count)d patches / %(formula_unit_count)d formula units; "
            "no workspace mutation. Patch list: %(patch_list)s" % summary
        )
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
