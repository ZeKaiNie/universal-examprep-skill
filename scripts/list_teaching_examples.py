#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""List teaching-example snapshots for exactly one chapter.

The manifest is deliberately separate from ``quiz_bank.json``: it is a teaching-reachability
index, not an assessment pool.  Requiring ``--chapter`` prevents an agent from dumping the whole
course manifest into context and preserves the exam coach's lazy-load contract.

Exit codes: 0 success (including a legacy workspace with no manifest); 2 invalid input/manifest.

``--next-pending`` is the read-only full-mode pacing selector. It derives
candidate completion only from ``study_state.json.phase_evidence`` and then
live-validates any bound event; bare notebook presence and student confirmation
are deliberately not progress evidence.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from urllib.parse import unquote

import i18n
import notebook as notebook_engine
from ingestion import (
    ConflictError,
    is_link_or_reparse,
    workspace_validation_lock,
)
from stable_ids import stable_item_id_problem


for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass


def _die(message):
    sys.stderr.write("list_teaching_examples: " + message + "\n")
    raise SystemExit(2)


def _reject_constant(value):
    raise ValueError("non-standard JSON constant %s" % value)


def _validate_workspace(workspace):
    if not os.path.isdir(workspace):
        _die("workspace does not exist or is not a directory: %s" % workspace)
    references = os.path.join(workspace, "references")
    if os.path.lexists(references) and is_link_or_reparse(references):
        _die("workspace references directory must not be a symbolic link")
    if not os.path.isdir(references):
        _die("workspace is missing the references directory")
    signatures = (
        os.path.join(references, "quiz_bank.json"),
        os.path.join(references, "teaching_examples.json"),
    )
    if not any(
        os.path.isfile(path) and not is_link_or_reparse(path)
        for path in signatures
    ):
        _die("path has no exam-workspace signature (quiz bank or teaching manifest)")


def _scope_key(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return str(value)
    text = str(value).strip() if value is not None else ""
    if text.isdigit() and int(text) >= 1:
        return str(int(text))
    return text


def load_manifest(workspace):
    _validate_workspace(workspace)
    path = os.path.join(workspace, "references", "teaching_examples.json")
    # lexists sees a broken symlink; exists would misclassify it as a legacy workspace with no
    # manifest and silently bypass the containment contract.
    if not os.path.lexists(path):
        return [], True
    if is_link_or_reparse(path):
        _die("references/teaching_examples.json must not be a symbolic link")
    ws_real = os.path.normcase(os.path.realpath(workspace))
    path_real = os.path.normcase(os.path.realpath(path))
    if path_real != ws_real and not path_real.startswith(ws_real + os.sep):
        _die("teaching manifest escapes the workspace")
    if not os.path.isfile(path):
        _die("references/teaching_examples.json is not a regular file")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f, parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        _die("invalid teaching manifest: %s" % exc)
    if not isinstance(data, list):
        _die("references/teaching_examples.json must contain a JSON array")
    seen_ids = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            _die("teaching_examples[%d] must be an object" % i)
        item_id_problem = stable_item_id_problem(item.get("id"))
        if item_id_problem:
            _die(
                "teaching_examples[%d].id violates the stable notebook/Guide "
                "contract: %s" % (i, item_id_problem)
            )
        item_id = item["id"]
        if item_id in seen_ids:
            _die("teaching_examples contains duplicate id %r" % item_id)
        seen_ids.add(item_id)
        if item.get("chapter") is not None and item.get("phase") is not None:
            chapter, phase = _scope_key(item.get("chapter")), _scope_key(item.get("phase"))
            if chapter != phase:
                _die("teaching_examples[%d] has conflicting chapter=%r and phase=%r" %
                     (i, item.get("chapter"), item.get("phase")))
    return data, False


def _stable_unique_strings(values, label):
    if not isinstance(values, list):
        _die("%s must be an array" % label)
    result, seen = [], set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            _die("%s[%d] must be a non-empty string" % (label, index))
        if value != value.strip():
            _die("%s[%d] must not have surrounding whitespace" % (label, index))
        if value in seen:
            _die("%s contains duplicate value %r" % (label, value))
        result.append(value)
        seen.add(value)
    return result


def _load_full_mode_state(workspace, chapter):
    """Load only the state fields needed by the pending selector under its lock.

    Missing or malformed state fails closed.  The selector is intentionally a
    full-mode feature; lightweight teaching has a different page-batch state
    machine and must never be inferred from this manifest.
    """
    path = os.path.join(workspace, "study_state.json")
    if not os.path.lexists(path):
        _die("--next-pending requires study_state.json")
    if is_link_or_reparse(path) or not os.path.isfile(path):
        _die("study_state.json must be a regular, non-symbolic-link file")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            state = json.load(stream, parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        _die("invalid study_state.json: %s" % exc)
    if not isinstance(state, dict):
        _die("study_state.json must contain an object")
    preferences = state.get("preferences", {})
    if preferences is None:
        preferences = {}
    if not isinstance(preferences, dict):
        _die("study_state.json.preferences must be an object")
    interaction_style = preferences.get("interaction_style", "batch")
    if interaction_style not in i18n.INTERACTION_STYLES:
        _die("study_state.json.preferences.interaction_style must be batch|step_by_step")
    if i18n.workspace_effective_interaction_style(state) != "step_by_step":
        _die(
            "--next-pending requires effective interaction_style=step_by_step; "
            "lightweight or no_questions makes the saved preference dormant")
    current = state.get("current_phase")
    if not isinstance(current, int) or isinstance(current, bool) or current < 1:
        _die("study_state.json.current_phase must be a positive integer")
    if _scope_key(current) != chapter:
        _die("--next-pending chapter %s does not match current_phase %s"
             % (chapter, current))
    evidence = state.get("phase_evidence")
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, dict):
        _die("study_state.json.phase_evidence must be an object")
    for key, value in evidence.items():
        if (not isinstance(key, str) or not key.isdigit() or int(key) < 1
                or str(int(key)) != key):
            _die("study_state.json.phase_evidence keys must be canonical positive integers; "
                 "got %r" % key)
        if not isinstance(value, dict):
            _die("study_state.json.phase_evidence[%s] must be an object" % key)
    return state


def pending_manifest_structure_problems(items):
    """Return structural problems that make manifest-ordered teaching unsafe."""

    if not isinstance(items, list):
        return ["teaching_examples.json must contain an array"]
    problems = []
    seen_ids = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append("teaching_examples[%d] must be an object" % index)
            continue
        item_id = item.get("id")
        id_problem = stable_item_id_problem(item_id)
        if id_problem:
            problems.append(
                "teaching_examples[%d].id violates the stable notebook/Guide "
                "contract: %s" % (index, id_problem)
            )
        elif item_id in seen_ids:
            problems.append("teaching_examples contains duplicate id %r" % item_id)
        else:
            seen_ids.add(item_id)
        chapter_value = item.get("chapter")
        phase_value = item.get("phase")
        chapter = _scope_key(chapter_value) if chapter_value is not None else None
        phase = _scope_key(phase_value) if phase_value is not None else None
        if chapter is not None and phase is not None and chapter != phase:
            problems.append(
                "teaching_examples[%d] has conflicting chapter=%r and phase=%r"
                % (index, chapter_value, phase_value)
            )
        scope = chapter if chapter is not None else phase
        if scope is None or not scope.isdigit() or int(scope) < 1:
            problems.append(
                "teaching_examples[%d] has no parseable positive chapter/phase"
                % index
            )
    return problems


def _validate_pending_scopes(items):
    problems = pending_manifest_structure_problems(items)
    if problems:
        _die(problems[0])


_BINDING_FIELDS = frozenset((
    "id", "notebook_ref", "notebook_block_sha256", "manifest_item_sha256",
))
_STALE_DIAGNOSTIC_LIMIT = 32


def _item_sha256(item):
    try:
        payload = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _die("teaching manifest item is not strict JSON: %s" % exc)
    return hashlib.sha256(payload).hexdigest()


def _require_complete_roster(workspace, chapter, items):
    """Require every retained ID to have a current same-chapter teaching snapshot."""
    path = os.path.join(workspace, "references", "teaching_baseline.json")
    if not os.path.lexists(path):
        return
    if is_link_or_reparse(path) or not os.path.isfile(path):
        _die("references/teaching_baseline.json must be a regular non-link file")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        _die("invalid teaching baseline: %s" % exc)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("policy") != "append_only"
    ):
        _die("step-by-step requires a valid append-only teaching baseline")
    mapping = payload.get("teaching_example_ids_by_chapter")
    flat = payload.get("teaching_example_ids")
    if not isinstance(mapping, dict) or not isinstance(flat, list):
        _die("step-by-step requires a valid chapter-mapped teaching baseline")
    if (
        any(
            stable_item_id_problem(value) is not None
            for value in flat
        )
        or len(flat) != len(set(flat))
    ):
        _die("teaching baseline ID list is invalid or duplicated")
    mapped = set()
    for raw_scope, values in mapping.items():
        scope = _scope_key(raw_scope)
        if (
            not isinstance(raw_scope, str)
            or not scope.isdigit()
            or str(int(scope)) != scope
            or not isinstance(values, list)
            or any(
                stable_item_id_problem(value) is not None
                for value in values
            )
            or len(values) != len(set(values))
        ):
            _die("teaching baseline chapter mapping is invalid")
        if mapped & set(values):
            _die("teaching baseline assigns one ID to multiple chapters")
        mapped.update(values)
    if mapped != set(flat):
        _die("teaching baseline flat IDs disagree with the chapter mapping")
    manifest_ids = {item["id"] for item in items}
    missing = sorted(set(flat) - manifest_ids)
    if missing:
        _die(
            "teaching roster is incomplete; baseline IDs missing from "
            "teaching_examples.json (quiz-only fallback is forbidden): %s; rebuild/review "
            "the full roster" % ", ".join(missing))

    # Presence in the flat manifest is not enough: a retained baseline item must
    # still belong to the same canonical chapter.  Otherwise moving an item to a
    # different chapter could make the old chapter look exhausted even though its
    # append-only baseline evidence has silently disappeared from that scope.
    manifest_scope_by_id = {
        item["id"]: next(iter(_chapter_keys(item))) for item in items
    }
    scope_drift = []
    for baseline_scope, values in mapping.items():
        for item_id in values:
            actual_scope = manifest_scope_by_id.get(item_id)
            if actual_scope is not None and actual_scope != baseline_scope:
                scope_drift.append((item_id, baseline_scope, actual_scope))
    if scope_drift:
        _die(
            "teaching roster chapter drift detected: %s; rebuild/review the full roster"
            % ", ".join(
                "%s baseline=%s manifest=%s" % row for row in scope_drift
            )
        )


def _binding_structure(workspace, chapter, binding):
    """Validate immutable binding shape/identity and return its live target.

    These failures are structural corruption, not recoverable content staleness,
    so callers must continue to fail closed rather than treating them as pending.
    """

    if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
        _die("teaching_example_bindings entries must use the exact binding schema")
    item_id = binding.get("id")
    notebook_ref = binding.get("notebook_ref")
    if stable_item_id_problem(item_id):
        _die("teaching-example binding id must be canonical")
    if (
        not isinstance(notebook_ref, str)
        or not notebook_ref.strip()
        or notebook_ref != notebook_ref.strip()
        or any(char in notebook_ref for char in ("\x00", "\r", "\n"))
        or notebook_ref.count("#") != 1
    ):
        _die("teaching-example binding notebook_ref must contain one canonical anchor")
    relative, fragment = notebook_ref.split("#", 1)
    relative = relative.replace("\\", "/")
    fragment = unquote(fragment)
    expected_relative = "notebook/ch%02d.md" % int(chapter)
    if (
        relative != expected_relative
        or not fragment
        or any(char in fragment for char in ("\x00", "\r", "\n"))
    ):
        _die("teaching-example binding must target %s#<anchor>" % expected_relative)
    for field in ("notebook_block_sha256", "manifest_item_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(field) or "")):
            _die("teaching-example binding %s must be lowercase SHA-256" % field)
    notebook_dir = os.path.join(workspace, "notebook")
    path = os.path.join(workspace, *relative.split("/"))
    if (
        os.path.lexists(notebook_dir)
        and is_link_or_reparse(notebook_dir)
    ):
        _die("workspace notebook directory must not be a link or reparse point")
    if os.path.lexists(notebook_dir) and not os.path.isdir(notebook_dir):
        _die("workspace notebook path exists but is not a directory")
    if os.path.lexists(path) and is_link_or_reparse(path):
        _die("teaching-example binding notebook file must not be a link or reparse point")
    workspace_real = os.path.normcase(os.path.realpath(workspace))
    path_real = os.path.normcase(os.path.realpath(path))
    try:
        contained = os.path.commonpath((workspace_real, path_real)) == workspace_real
    except ValueError:
        contained = False
    if not contained:
        _die("teaching-example binding notebook path escapes the workspace")
    return {
        "id": item_id,
        "notebook_ref": notebook_ref,
        "fragment": fragment,
        "path": path,
    }


def _live_notebook_binding(target, binding):
    """Return a stable stale-reason code, or ``None`` when the binding is live."""

    item_id = target["id"]
    fragment = target["fragment"]
    path = target["path"]
    if is_link_or_reparse(path):
        _die("teaching-example binding notebook became a link or reparse point")
    if not os.path.lexists(path):
        return "notebook_missing"
    if not os.path.isfile(path):
        _die("teaching-example binding notebook target is not a regular file")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            preamble, blocks = notebook_engine.parse_chapter(stream.read())
        matches = [
            block for block, anchor in zip(
                blocks, notebook_engine.anchors_for(preamble, blocks))
            if anchor == fragment
        ]
    except (OSError, UnicodeDecodeError) as exc:
        _die("teaching-example binding notebook is unreadable: %s" % exc)
    except ValueError as exc:
        _die("teaching-example binding notebook is structurally invalid: %s" % exc)
    if len(matches) != 1:
        return "notebook_anchor_not_unique"
    block = matches[0]
    label, _timestamp = notebook_engine._block_meta(block.get("lines") or [])
    if (
        block.get("id") != item_id
        or notebook_engine._label_to_type().get(label) != "walkthrough"
        or not notebook_engine.block_has_teaching_example_marker(block, item_id)
    ):
        return "notebook_marked_walkthrough_mismatch"
    try:
        live_hash = notebook_engine.block_sha256(block)
    except ValueError as exc:
        _die("teaching-example walkthrough block is structurally invalid: %s" % exc)
    if live_hash != binding.get("notebook_block_sha256"):
        return "notebook_block_revision_changed"
    return None


def _validated_step_completed(workspace, chapter, hits, state):
    record = (state.get("phase_evidence") or {}).get(chapter, {})
    recorded_value = record.get("teaching_examples")
    notebook_value = record.get("notebook")
    binding_value = record.get("teaching_example_bindings")
    recorded = _stable_unique_strings(
        [] if recorded_value is None else recorded_value,
        "study_state.json.phase_evidence[%s].teaching_examples" % chapter,
    )
    if any(stable_item_id_problem(value) is not None for value in recorded):
        _die(
            "study_state.json.phase_evidence[%s].teaching_examples violates "
            "the shared stable ID contract" % chapter
        )
    notebook_refs = _stable_unique_strings(
        [] if notebook_value is None else notebook_value,
        "study_state.json.phase_evidence[%s].notebook" % chapter,
    )
    bindings = [] if binding_value is None else binding_value
    if not isinstance(bindings, list):
        _die("study_state.json teaching_example_bindings must be an array")
    by_id = {}
    targets = {}
    id_by_notebook_ref = {}
    item_by_id = {item["id"]: item for item in hits}
    for binding in bindings:
        target = _binding_structure(workspace, chapter, binding)
        item_id = target["id"]
        if item_id in by_id:
            _die("teaching_example_bindings contains duplicate id %r" % item_id)
        previous_id = id_by_notebook_ref.get(binding["notebook_ref"])
        if previous_id is not None:
            _die(
                "teaching_example_bindings IDs %r and %r share one notebook_ref"
                % (previous_id, item_id)
            )
        by_id[item_id] = binding
        targets[item_id] = target
        id_by_notebook_ref[binding["notebook_ref"]] = item_id
        item = item_by_id.get(item_id)
        if item is None:
            _die("teaching-example evidence is outside the current chapter roster: %s" % item_id)
        if binding.get("notebook_ref") not in notebook_refs:
            _die("teaching-example binding lacks matching notebook evidence: %s" % item_id)
    missing_ids = set(by_id) - set(recorded)
    if missing_ids:
        _die(
            "teaching-example bindings lack matching teaching IDs: %s"
            % ", ".join(sorted(missing_ids)))
    unexpected = set(recorded) - set(item_by_id)
    if unexpected:
        _die(
            "teaching-example evidence is outside the current chapter roster: %s"
            % ", ".join(sorted(unexpected)))

    stale_by_id = {}
    for item in hits:
        item_id = item["id"]
        binding = by_id.get(item_id)
        if binding is None:
            continue
        problems = []
        if _item_sha256(item) != binding.get("manifest_item_sha256"):
            problems.append("manifest_item_revision_changed")
        live_problem = _live_notebook_binding(targets[item_id], binding)
        if live_problem is not None:
            problems.append(live_problem)
        if problems:
            # At most one manifest and one notebook condition are reported for
            # each roster item.  No file content or exception text enters JSON.
            stale_by_id[item_id] = problems

    # Unbound IDs are legitimate batch-mode evidence from before a cadence switch;
    # bound IDs are step events and count only while their live checks pass.
    recorded_set = set(recorded)
    valid = (recorded_set - set(by_id)) | (set(by_id) - set(stale_by_id))
    completed = [item["id"] for item in hits if item["id"] in valid]
    return completed, recorded, stale_by_id


def _pending_payload(workspace, chapter, items, hits, missing):
    if missing:
        _die("--next-pending requires references/teaching_examples.json")
    _validate_pending_scopes(items)
    manifest_ids = [item["id"] for item in hits]
    _require_complete_roster(workspace, chapter, items)
    state = _load_full_mode_state(workspace, chapter)
    completed_ids, recorded, stale_by_id = _validated_step_completed(
        workspace, chapter, hits, state)
    manifest_set = set(manifest_ids)
    completed_set = set(completed_ids)
    pending_items = [item for item in hits if item["id"] not in completed_set]
    unexpected = [item_id for item_id in recorded if item_id not in manifest_set]
    stale_ids = [item["id"] for item in hits if item["id"] in stale_by_id]
    reported_stale_ids = stale_ids[:_STALE_DIAGNOSTIC_LIMIT]
    return {
        "chapter": chapter,
        "manifest_missing": False,
        "processing_mode": "full",
        "interaction_style_preference": (
            i18n.workspace_interaction_style_preference(state)),
        "interaction_style_effective": (
            i18n.workspace_effective_interaction_style(state)),
        "interaction_style_dormant": (
            i18n.workspace_interaction_style_dormant(state)),
        "total": len(hits),
        "completed": len(completed_ids),
        "pending": len(pending_items),
        "completed_ids": completed_ids,
        "pending_ids": [item["id"] for item in pending_items],
        "stale_binding_count": len(stale_ids),
        "stale_binding_ids": reported_stale_ids,
        "stale_binding_problems": [
            {"id": item_id, "problems": stale_by_id[item_id]}
            for item_id in reported_stale_ids
        ],
        "stale_binding_diagnostics_truncated": (
            len(stale_ids) > _STALE_DIAGNOSTIC_LIMIT
        ),
        "next": pending_items[0] if pending_items else None,
        "teaching_example_roster_exhausted": not pending_items,
        "unexpected_evidence": unexpected,
    }


def _chapter_keys(item):
    value = item.get("chapter") if item.get("chapter") is not None else item.get("phase")
    return {_scope_key(value)} if value is not None else set()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="List teaching examples for one chapter (never reads the assessment bank)."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--chapter", required=True,
                        help="required exact chapter-or-phase value; whole-course dumps are disabled")
    parser.add_argument("--limit", type=int, default=0, help="0 = all matches in this chapter")
    parser.add_argument(
        "--next-pending", action="store_true",
        help="full mode only: return the first item lacking phase teaching-example evidence",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 0:
        _die("--limit must be >= 0")
    if args.next_pending and args.limit:
        _die("--limit cannot be combined with --next-pending")

    workspace = os.path.abspath(args.workspace)
    chapter = _scope_key(args.chapter)
    if args.next_pending:
        try:
            with workspace_validation_lock(workspace):
                items, missing = load_manifest(workspace)
                hits = [item for item in items if chapter in _chapter_keys(item)]
                payload = _pending_payload(
                    workspace, chapter, items, hits, missing)
        except ConflictError as exc:
            _die("cannot acquire a consistent workspace snapshot: %s" % exc)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("[teaching examples] chapter %s: %d total, %d completed, %d pending" %
                  (chapter, payload["total"], payload["completed"], payload["pending"]))
            if payload["unexpected_evidence"]:
                print("[unexpected evidence] %s" %
                      ", ".join(payload["unexpected_evidence"]))
            if payload["stale_binding_ids"]:
                print("[stale bindings -> pending] %s" %
                      ", ".join(payload["stale_binding_ids"]))
            if payload["next"] is not None:
                print("- next [#%s] %s" %
                      (payload["next"]["id"],
                       str(payload["next"].get("title") or
                           payload["next"].get("question") or "")[:80]))
            else:
                print("- next: none")
        return 0
    items, missing = load_manifest(workspace)
    hits = [item for item in items if chapter in _chapter_keys(item)]
    total = len(hits)
    if args.limit:
        hits = hits[:args.limit]

    if args.json:
        print(json.dumps({
            "chapter": chapter,
            "manifest_missing": missing,
            "total_matched": total,
            "returned": len(hits),
            "items": hits,
        }, ensure_ascii=False, indent=2))
        return 0

    if missing:
        print("[teaching examples] legacy workspace: manifest absent; 0 matches")
        return 0
    print("[teaching examples] chapter %s: %d matches (showing %d)" %
          (chapter, total, len(hits)))
    for item in hits:
        pages = ",".join(str(p) for p in (item.get("source_pages") or [])) or "?"
        print("- [#%s] %s | %s p.%s | %s" % (
            item.get("id"), item.get("teaching_role", "?"),
            item.get("source_file", "source unknown"), pages,
            str(item.get("title") or item.get("question") or "")[:80],
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
