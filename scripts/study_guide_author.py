#!/usr/bin/env python3
"""Deterministic, revision-bound Study Guide authoring compiler.

This command deliberately separates four trust domains:

* ``prepare`` copies source facts into a hash-bound packet and incomplete template;
* an author (normally an LLM) supplies explanation annotations only;
* ``persist-notebooks`` records every walkthrough through ``notebook.py``;
* ``compile`` mechanically combines those inputs without allowing annotations
  to replace source text, assets, source locations, or exact LaTeX.

The compiler does not import a Guide, create claim records, render HTML/PDF, or
accept a Study Guide visually.  Those remain explicit downstream operations.
Only the Python standard library and repository-local modules are used.
"""

from __future__ import print_function

import argparse
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import threading
from pathlib import PureWindowsPath
from urllib.parse import quote as url_quote


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import notebook as notebook_engine  # noqa: E402
import readiness  # noqa: E402
import exam_start  # noqa: E402
import study_guide_content as guide_content  # noqa: E402
from stable_ids import stable_item_id_problem  # noqa: E402
from asset_crops import (  # noqa: E402
    CropContractError,
    load_crop_receipt_report,
    verify_crop_asset_live_binding,
)
from math_text_policy import find_math_layout_hazard  # noqa: E402
from study_guide_provenance import (  # noqa: E402
    PROVENANCE_CATEGORIES,
    PROVENANCE_ICONS as SHARED_PROVENANCE_ICONS,
    ProvenanceConflictError,
    clean_visible_provenance,
    notebook_has_provenance_legend,
    notebook_legend_lines,
    strip_visible_provenance_prefix,
)
from ingestion import (  # noqa: E402
    ConflictError,
    SourceDriftError,
    SourceManifest,
    UnsafePathError,
    canonical_json,
    file_sha256,
    is_link_or_reparse,
    normalize_workspace_path,
    read_jsonl,
    safe_workspace_entry,
    workspace_publication_lock,
)
from ingestion.claims import (  # noqa: E402
    ClaimValidationError,
    load_claim_records,
    validate_claim_subject_bindings,
    validate_guide_claim_coverage,
)


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 64 * 1024 * 1024
SNAPSHOT_PATHS = (
    "study_state.json",
    "references/teaching_examples.json",
    "references/quiz_bank.json",
    ".ingest/content_units.jsonl",
    ".ingest/source_manifest.json",
    ".ingest/build_manifest.json",
    ".ingest/parser_receipts.json",
    ".ingest/review_queue.jsonl",
    ".ingest/review_patches.jsonl",
    ".ingest/pending_patch.json",
    ".ingest/canonical_groups.jsonl",
    ".ingest/source_conflicts.jsonl",
    ".ingest/source_priorities.jsonl",
    ".ingest/material_build_receipt.json",
    ".ingest/parse_report.json",
    ".ingest/material_build_pending.json",
    ".ingest/pending_ingest.json",
)
PROMPT_ROLES = frozenset(guide_content.PROMPT_ASSET_ROLES)
ANSWER_ROLES = frozenset(guide_content.ANSWER_ASSET_ROLES)
TARGET_LANGUAGES = {
    "zh": frozenset(("zh",)),
    "en": frozenset(("en",)),
    "bilingual": frozenset(("zh", "en")),
}
ANSWER_EXPLANATION_MODES = frozenset(("ordinary", "isolated"))
OUTPUT_SUFFIXES = {
    "packet": "authoring-packet.json",
    "annotations": "authoring-annotations.json",
    "annotations_template": "authoring-annotations.template.json",
    "bindings": "authoring-bindings.json",
    "claim_draft": "guide.claim-draft.json",
    "claim_proposals": "claim-proposals.json",
    "claim_attached": "guide.claims.json",
}
PROVENANCE_EMOJI = dict(SHARED_PROVENANCE_ICONS)
_NOTEBOOK_ENGINE_IO_LOCK = threading.RLock()
NOTEBOOK_COPY = {
    "zh": {
        "question_figure": "① 题面图",
        "prompt_figure": "题面图",
        "no_prompt_figure": "本题没有绑定题面图。",
        "original_prompt": "原题",
        "translation": "题面翻译",
        "what_asked_heading": "② 问什么",
        "question": "问题",
        "knowledge_point_use": "知识点用法 %s",
        "quantities_heading": "③ 从题面读取的量",
        "known": "已知",
        "unknown": "未知",
        "none": "无",
        "formula_heading": "④ 核心公式",
        "formula_meaning": "公式含义",
        "applicability": "适用条件",
        "variable": "变量 %s",
        "why_applicable": "为什么适用",
        "variable_mapping": "变量对应 %s",
        "substitution": "代入",
        "no_formula": "无需公式",
        "steps_heading": "⑤ 逐步演算",
        "answer_heading": "答案",
        "answer": "答案",
        "answer_explanation": "⑥ 为什么这个答案成立",
        "answer_figure": "答案图",
        "source_heading": "⑦ 来源溯源",
        "no_examples": "材料未提供对应例题。",
    },
    "en": {
        "question_figure": "① Question figure",
        "prompt_figure": "question figure",
        "no_prompt_figure": "No prompt-side figure is bound to this item.",
        "original_prompt": "Original prompt",
        "translation": "Prompt translation",
        "what_asked_heading": "② What is being asked",
        "question": "Question",
        "knowledge_point_use": "Knowledge-point use %s",
        "quantities_heading": "③ Quantities read from the prompt",
        "known": "Known",
        "unknown": "Unknown",
        "none": "none",
        "formula_heading": "④ Core formula",
        "formula_meaning": "Formula meaning",
        "applicability": "Applicability",
        "variable": "Variable %s",
        "why_applicable": "Why applicable",
        "variable_mapping": "Variable mapping %s",
        "substitution": "Substitution",
        "no_formula": "No formula",
        "steps_heading": "⑤ Step-by-step work",
        "answer_heading": "Answer",
        "answer": "Answer",
        "answer_explanation": "⑥ Why this answer works",
        "answer_figure": "answer figure",
        "source_heading": "⑦ Source trace",
        "no_examples": "The materials do not provide a corresponding example.",
    },
}
class AuthoringError(ValueError):
    """The authoring input or live workspace violated a fail-closed gate."""


def _reject_constant(value):
    raise AuthoringError("non-finite JSON constant is not allowed: %s" % value)


def _object_without_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise AuthoringError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def _check_controls(value, label="$", depth=0):
    if depth > 100:
        raise AuthoringError("%s exceeds the maximum JSON nesting depth" % label)
    if isinstance(value, str):
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise AuthoringError("%s contains unsafe control characters" % label)
        if "\ufffd" in value:
            raise AuthoringError("%s contains the Unicode replacement character" % label)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise AuthoringError("%s contains a non-finite number" % label)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuthoringError("%s contains a non-string JSON key" % label)
            _check_controls(key, label + ".<key>", depth + 1)
            _check_controls(item, "%s.%s" % (label, key), depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_controls(item, "%s[%d]" % (label, index), depth + 1)


def _strict_json(path, label):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise AuthoringError("cannot stat %s: %s" % (label, exc))
    if size > MAX_JSON_BYTES:
        raise AuthoringError("%s exceeds the %d-byte input limit" % (label, MAX_JSON_BYTES))
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(
                stream,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
    except AuthoringError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthoringError("cannot read strict JSON %s: %s" % (label, exc))
    _check_controls(value, label)
    try:
        canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise AuthoringError("%s is not strict JSON: %s" % (label, exc))
    return value


def _sha256_json(value):
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthoringError("cannot hash non-canonical JSON: %s" % exc)
    return hashlib.sha256(payload).hexdigest()


def _identifier(value, label):
    problem = stable_item_id_problem(value)
    if problem:
        raise AuthoringError("%s must be a stable identifier: %s" % (label, problem))
    return value


def _nonblank(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AuthoringError("%s must be non-empty, trimmed text" % label)
    _check_controls(value, label)
    return value


def _shape(value, required, optional, label):
    if not isinstance(value, dict):
        raise AuthoringError("%s must be an object" % label)
    expected = set(required) | set(optional)
    actual = set(value)
    missing = sorted(set(required) - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise AuthoringError(
            "%s schema mismatch; missing=%s unknown=%s" % (label, missing, unknown)
        )
    return value


def _array(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise AuthoringError("%s must be %sa JSON array" % (label, "a non-empty " if nonempty else ""))
    return value


def _localized(value, language, label, allow_extra=True, skip_math_codes=()):
    if not isinstance(value, dict) or not value:
        raise AuthoringError("%s must be a localized object" % label)
    if set(value) - {"zh", "en"}:
        raise AuthoringError("%s may contain only zh/en" % label)
    required = TARGET_LANGUAGES[language]
    if not required.issubset(value):
        raise AuthoringError("%s lacks target languages %s" % (label, sorted(required - set(value))))
    if not allow_extra and set(value) != set(required):
        raise AuthoringError("%s must contain exactly %s" % (label, sorted(required)))
    for code, text in value.items():
        text_label = "%s.%s" % (label, code)
        _nonblank(text, text_label)
        if code not in set(skip_math_codes):
            try:
                guide_content._validate_localized_prose_math(text, text_label)
            except guide_content.ContentError as exc:
                raise AuthoringError(str(exc))
    return dict(value)


def _workspace(workspace):
    try:
        return guide_content._guard_workspace(workspace)
    except guide_content.ContentError as exc:
        raise AuthoringError(str(exc))


def _workspace_answer_explanation_mode(workspace):
    """Return the safe effective state choice for Guide answer explanations."""

    state_path = os.path.join(workspace, "study_state.json")
    try:
        state = _strict_json(state_path, "study_state.json")
    except (AuthoringError, OSError, TypeError, ValueError) as exc:
        raise AuthoringError(
            "cannot read answer_explanation_mode from study_state.json: %s" % exc
        ) from exc
    if not isinstance(state, dict):
        raise AuthoringError("study_state.json must be an object")
    return exam_start.i18n.workspace_answer_explanation_mode(state)


def require_current_ingestion_v2(workspace, purpose="Study Guide authoring"):
    """Fail closed unless this is the current writable ingestion-v2 Guide route."""

    try:
        pipeline_version = guide_content._ingestion_pipeline_version(workspace)
    except guide_content.ContentError as exc:
        raise AuthoringError(
            "%s requires a valid ingestion-v2 build manifest: %s" % (purpose, exc)
        ) from exc
    if pipeline_version != "ingestion-v2":
        raise AuthoringError(
            "%s is available only for ingestion-v2; ingestion-v1 is read-only and "
            "may validate only its existing canonical Guide" % purpose
        )
    return pipeline_version


def _safe_workspace_path(workspace, value, label, output=False):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AuthoringError("%s must be a non-empty path" % label)
    root = os.path.abspath(workspace)
    try:
        if os.path.isabs(value):
            # Windows GetFullPathName strips trailing dots/spaces.  Validate
            # lexical components *before* os.path.abspath can erase an alias.
            raw_parts = PureWindowsPath(value).parts
            raw_relative = "/".join(raw_parts[1:])
            normalize_workspace_path(raw_relative)
            target = os.path.abspath(value)
        else:
            canonical_input = normalize_workspace_path(value)
            target = os.path.abspath(os.path.join(root, *canonical_input.split("/")))
    except (UnsafePathError, ValueError) as exc:
        raise AuthoringError("%s is not a canonical portable workspace path: %s" % (label, exc))
    try:
        if os.path.commonpath((root, target)) != root:
            raise AuthoringError("%s escapes the workspace" % label)
    except ValueError:
        raise AuthoringError("%s escapes the workspace" % label)
    try:
        relative = normalize_workspace_path(
            os.path.relpath(target, root).replace(os.sep, "/")
        )
        target = str(safe_workspace_entry(root, relative))
    except (UnsafePathError, ValueError) as exc:
        raise AuthoringError("%s is not a canonical portable workspace path: %s" % (label, exc))
    if output:
        if relative == "notebook" or not relative.startswith("notebook/"):
            raise AuthoringError("%s must be below workspace/notebook" % label)
        if os.path.lexists(target) and not os.path.isfile(target):
            raise AuthoringError("%s exists but is not a regular file" % label)
        parent = os.path.dirname(target)
        if os.path.lexists(parent) and not os.path.isdir(parent):
            raise AuthoringError("%s parent is not a directory" % label)
    else:
        if not os.path.isfile(target) or is_link_or_reparse(target):
            raise AuthoringError("%s must be a regular non-link file inside the workspace" % label)
    return target


def _relative(workspace, path):
    return os.path.relpath(path, workspace).replace(os.sep, "/")


def _snapshot(workspace):
    files = {}
    for relative in SNAPSHOT_PATHS:
        path = os.path.join(workspace, *relative.split("/"))
        if not os.path.lexists(path):
            files[relative] = None
            continue
        try:
            guide_content._guard_workspace_child(
                workspace, path, relative, require_file=True
            )
            files[relative] = {
                "sha256": file_sha256(path),
                "size": os.path.getsize(path),
            }
        except (guide_content.ContentError, OSError, ValueError) as exc:
            raise AuthoringError("cannot snapshot %s: %s" % (relative, exc))
    value = {"schema_version": 1, "files": files}
    return value, _sha256_json(value)


def _source_revision_snapshot(workspace):
    """Verify every manifest source against its current regular-file bytes."""

    try:
        build_path = os.path.join(workspace, ".ingest", "build_manifest.json")
        build = _strict_json(build_path, ".ingest/build_manifest.json")
        if not isinstance(build, dict):
            raise AuthoringError("build_manifest.json must be an object")
        source_root = build.get("source_root", workspace)
        if not isinstance(source_root, str) or not source_root.strip():
            raise AuthoringError("build_manifest.json.source_root must be a non-empty path")
        source_root = os.path.abspath(source_root)
        manifest = SourceManifest(workspace, source_root=source_root)
        records = manifest.records()
        if not records:
            raise AuthoringError("source_manifest.json contains no source revisions")
        rows = []
        for record in records:
            verified = manifest.verify_current(record.source_id, record.sha256)
            rows.append({
                "source_id": verified.source_id,
                "path": verified.path,
                "sha256": verified.sha256,
                "size_bytes": verified.size_bytes,
                "media_type": verified.media_type,
                "status": verified.status,
            })
    except AuthoringError:
        raise
    except (SourceDriftError, OSError, ValueError, TypeError) as exc:
        raise AuthoringError("source revision verification failed: %s" % exc)
    rows.sort(key=lambda row: (row["path"], row["source_id"]))
    value = {
        "schema_version": 1,
        "source_root": source_root,
        "sources": rows,
    }
    return value, _sha256_json(value)


def _packet_hash(packet):
    payload = dict(packet)
    supplied = payload.pop("packet_sha256", None)
    computed = _sha256_json(payload)
    if supplied is not None and supplied != computed:
        raise AuthoringError("authoring packet hash is invalid")
    return computed


def _annotation_hash(annotations):
    return _sha256_json(annotations)


def _asset_hash(workspace, relative):
    try:
        canonical = guide_content._workspace_asset(workspace, relative, "authoring asset")
        path = os.path.join(workspace, *canonical.split("/"))
        return file_sha256(path)
    except (guide_content.ContentError, OSError, ValueError) as exc:
        raise AuthoringError("cannot bind asset %s: %s" % (relative, exc))


def _packet_asset_revisions(packet):
    """Return the exact asset path/hash set carried by an authoring packet."""

    revisions = {}
    for item in packet.get("items") or []:
        if not isinstance(item, dict):
            raise AuthoringError("packet item is malformed while binding assets")
        for key in ("prompt_assets", "answer_assets"):
            for asset in item.get(key) or []:
                if not isinstance(asset, dict):
                    raise AuthoringError("packet asset is malformed")
                path = asset.get("path")
                digest = asset.get("sha256")
                if (
                    not isinstance(path, str) or not path
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    raise AuthoringError("packet asset lacks a canonical path/hash binding")
                previous = revisions.setdefault(path, digest)
                if previous != digest:
                    raise AuthoringError("packet carries conflicting revisions for asset %s" % path)
    for asset in packet.get("semantic_asset_revisions") or []:
        if not isinstance(asset, dict) or set(asset) != {"path", "sha256"}:
            raise AuthoringError("packet semantic asset revision is malformed")
        path = asset["path"]
        digest = asset["sha256"]
        if (
            not isinstance(path, str) or not path
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise AuthoringError("packet semantic asset lacks a canonical path/hash binding")
        previous = revisions.setdefault(path, digest)
        if previous != digest:
            raise AuthoringError("packet carries conflicting revisions for asset %s" % path)
    return [
        {"path": path, "sha256": revisions[path]}
        for path in sorted(revisions)
    ]


def _asset_revisions_for_paths(workspace, paths):
    revisions = []
    for path in sorted(set(paths)):
        revisions.append({"path": path, "sha256": _asset_hash(workspace, path)})
    return revisions


def _verify_asset_revisions(workspace, expected):
    if not isinstance(expected, list):
        raise AuthoringError("expected asset revisions must be an array")
    paths = []
    for index, row in enumerate(expected):
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise AuthoringError("expected asset revision %d is malformed" % index)
        path = row["path"]
        digest = row["sha256"]
        if (
            not isinstance(path, str) or not path
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise AuthoringError("expected asset revision %d is invalid" % index)
        paths.append(path)
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        raise AuthoringError("expected asset revisions must be unique and path-sorted")
    live = _asset_revisions_for_paths(workspace, paths)
    if live != expected:
        raise AuthoringError("authoring asset bytes drifted")
    return live


def _unit_payload(unit, allow_latex=False):
    fields = ("text", "html", "latex") if allow_latex else ("text", "html")
    candidates = []
    for field in fields:
        value = unit.get(field)
        normalized = guide_content._normalize_exact_text(value)
        # Payload field identity is part of the claim contract.  In
        # particular, a formula parser may legitimately publish the same
        # string in ``text`` and ``latex``; collapsing those values would
        # leave the packet unable to bind the required latex claim later.
        # Each field occurs at most once here, so cross-field value
        # deduplication is both unnecessary and semantically incorrect.
        if normalized:
            candidates.append((field, value))
    return candidates


def _source_language(unit, label, required=False):
    metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
    value = metadata.get("source_language")
    if value == "zxx" and not required:
        return None
    if value not in ("zh", "en"):
        if required:
            raise AuthoringError("%s needs explicit metadata.source_language zh/en" % label)
        return None
    return value


def _source_ref(unit, role, asset_path=None, contains_full_prompt=False):
    ref = {
        "source_file": unit["source_file"],
        "pages": [unit["page"]],
        "source_unit_id": unit["unit_id"],
        "role": role,
    }
    if asset_path is not None:
        ref["asset_path"] = asset_path
        if contains_full_prompt:
            ref["contains_full_prompt"] = True
    return ref


def _semantic_ref(unit, role):
    if guide_content._source_role_matches_unit(unit, role, asset_path_bound=False):
        return _source_ref(unit, role)
    expected_roles = guide_content.SOURCE_REF_ASSET_ROLE_POLICY[role][0]
    for asset in sorted(guide_content._unit_asset_records(unit), key=lambda row: row.get("path") or ""):
        if asset.get("role") not in expected_roles or not isinstance(asset.get("path"), str):
            continue
        if guide_content._source_role_matches_unit(unit, role, asset_path_bound=True):
            return _source_ref(unit, role, asset["path"])
    raise AuthoringError(
        "semantic unit %s cannot form a typed %s source ref" % (unit.get("unit_id"), role)
    )


def _crop_receipt_index(workspace):
    path = os.path.join(workspace, ".ingest", "parse_report.json")
    if not os.path.lexists(path):
        return {}
    try:
        _document, index = load_crop_receipt_report(
            workspace, require_index_sha256=True
        )
    except (CropContractError, OSError, TypeError, ValueError) as exc:
        raise AuthoringError("cannot load live crop receipt index: %s" % exc)
    return index


def _asset_record(workspace, source_root, expected_chapter_id, item_id,
                  relative, records, tainted_keys, crop_receipts):
    key = guide_content.physical_asset_key(relative)
    if key is None or key in tainted_keys:
        return None
    roles = sorted({row.get("role") for row in records if isinstance(row.get("role"), str)})
    if "student_attempt" in roles:
        return None
    digest = _asset_hash(workspace, relative)
    declared = sorted({row.get("sha256") for row in records if row.get("sha256") is not None})
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in declared):
        raise AuthoringError("asset %s has an invalid declared sha256" % relative)
    if declared and (len(declared) != 1 or declared[0] != digest):
        raise AuthoringError("asset %s bytes disagree with declared sha256" % relative)
    exact_full_prompt = any(
        isinstance(row, dict)
        and row.get("role") in PROMPT_ROLES
        and (
            bool(row.get("contains_full_prompt"))
            or (
                row.get("role") == "question_context"
                and row.get("type") == "page_image"
            )
        )
        for row in records
    )
    result = {
        "path": relative,
        "sha256": digest,
        "roles": roles,
        "types": sorted({row.get("type") for row in records if isinstance(row.get("type"), str)}),
        # This is intentionally derived from one exact record.  Unioning role,
        # type, and boolean flags across records can manufacture evidence that
        # no parser/source record actually asserted.
        "contains_full_prompt": exact_full_prompt,
    }
    # A receipted crop is not merely another rendition of the same page.  Its
    # item/side isolation contract is what permits the per-question explanation
    # call to receive the image without also seeing neighbouring questions or
    # answers.  Preserve the compact receipt controls in the authoring packet;
    # dropping them here would make a safe crop indistinguishable from a legacy
    # whole-page screenshot.
    crop_fields = (
        "source_page", "source_bbox_pdf_points", "crop_receipt_id",
        "crop_receipt_schema_version", "crop_spec_sha256",
        "semantic_purity_sha256", "content_scope", "isolation",
    )
    semantic_context_fields = (
        "semantic_purity_schema_version", "required_context_ids",
    )
    crop_rows = [
        row for row in records
        if isinstance(row, dict) and "crop_receipt_id" in row
    ]
    if crop_rows:
        if any(
                isinstance(row, dict) and "crop_receipt_id" not in row
                for row in records
        ):
            raise AuthoringError(
                "crop asset %s is also declared without its crop receipt" % relative
            )
        verified_receipts = []
        for row_index, row in enumerate(crop_rows):
            if any(field not in row for field in crop_fields):
                raise AuthoringError(
                    "crop asset %s declaration %d is missing compact receipt controls"
                    % (relative, row_index)
                )
            try:
                receipt = verify_crop_asset_live_binding(
                    workspace,
                    source_root,
                    copy.deepcopy(row),
                    crop_receipts,
                    expected_item_id=item_id,
                    expected_chapter_id=expected_chapter_id,
                )
            except (CropContractError, OSError, TypeError, ValueError) as exc:
                raise AuthoringError(
                    "crop asset %s declaration %d failed exact compact/full/live "
                    "verification: %s" % (relative, row_index, exc)
                )
            # ``contains_full_prompt`` is the only prompt-display flag outside
            # the compact receipt view.  Close it independently on every row;
            # an aggregate ``any(...)`` could otherwise manufacture a full
            # prompt assertion that no individual declaration supports.
            expected_contains_full_prompt = (
                receipt.side == "prompt"
                and receipt.content_scope == "full_prompt"
            )
            if ("contains_full_prompt" not in row
                    or type(row["contains_full_prompt"]) is not bool
                    or row["contains_full_prompt"]
                    != expected_contains_full_prompt):
                raise AuthoringError(
                    "crop asset %s declaration %d contains_full_prompt "
                    "does not close to its full receipt"
                    % (relative, row_index)
                )
            verified_receipts.append(receipt)
        receipt = verified_receipts[0]
        if any(
                candidate.crop_receipt_id != receipt.crop_receipt_id
                for candidate in verified_receipts[1:]
        ):
            raise AuthoringError(
                "crop asset %s declarations resolve to different full receipts"
                % relative
            )
        canonical = {
            field: copy.deepcopy(crop_rows[0][field]) for field in crop_fields
        }
        if receipt.semantic_purity.schema_version >= 2:
            for field in semantic_context_fields:
                if field not in crop_rows[0]:
                    raise AuthoringError(
                        "crop asset %s current semantic receipt is missing %s"
                        % (relative, field)
                    )
                canonical[field] = copy.deepcopy(crop_rows[0][field])
        result.update(canonical)
        result["contains_full_prompt"] = (
            receipt.side == "prompt" and receipt.content_scope == "full_prompt"
        )
        result["crop_receipt_sha256"] = _sha256_json(receipt.to_dict())
    return result


def _blocking(code, **details):
    value = {"code": code}
    value.update(details)
    return value


def _inline_material_answer_binding(unit, question_units, unit_index):
    """Validate one same-page worked-Example answer evidence binding.

    ``answer_origin=inline_material`` is intentionally stronger than ordinary
    material provenance.  The paired question and answer must be two typed
    views of the same exact source revision/page/title, and the item must remain
    teaching-only.  The answer unit still carries its own exact payload/source
    ref, so downstream claims never point at a guessed page or AI-authored text.
    """

    metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
    if metadata.get("answer_origin") != "inline_material":
        return False, "answer unit does not declare inline_material"
    if (
        unit.get("kind") != "answer"
        or unit.get("provenance") != "material"
        or metadata.get("source") != "material"
        or metadata.get("teaching_role") != "worked_example"
        or metadata.get("gradable") is not False
    ):
        return False, "inline answer is not a material non-gradable worked_example"
    title = metadata.get("teaching_title")
    if not isinstance(title, str) or not title.strip():
        return False, "inline answer lacks its exact teaching title"
    if (
        metadata.get("answer_source_file") != unit.get("source_file")
        or metadata.get("answer_source_pages") != [unit.get("page")]
    ):
        return False, "inline answer location differs from its own source unit"
    if metadata.get("answer_value") != unit.get("text"):
        return False, "inline answer_value differs from its exact unit text"
    source_language = metadata.get("source_language")
    if source_language not in ("zh", "en"):
        return False, "inline answer needs explicit source_language zh/en"
    native_unit_id = metadata.get("inline_material_source_unit_id")
    native_unit = unit_index.get(native_unit_id)
    native_metadata = (
        native_unit.get("metadata")
        if isinstance(native_unit, dict)
        and isinstance(native_unit.get("metadata"), dict) else {}
    )
    if (
        not isinstance(native_unit, dict)
        or native_unit.get("kind") != "text"
        or native_unit.get("method") != "native"
        or native_unit.get("provenance") != "material"
        or native_unit.get("external_id") is not None
        or native_unit.get("source_id") != unit.get("source_id")
        or native_unit.get("source_file") != unit.get("source_file")
        or native_unit.get("source_sha256") != unit.get("source_sha256")
        or native_unit.get("page") != unit.get("page")
        or native_unit.get("text") != unit.get("text")
        or native_metadata.get("source_language") != source_language
    ):
        return False, "inline answer does not close to one exact native material text unit"
    normalized_title = " ".join(title.split()).casefold()
    normalized_native = " ".join(native_unit.get("text", "").split()).casefold()
    if not (
        normalized_native.startswith(normalized_title)
        and len(normalized_native) > len(normalized_title)
    ):
        return False, "inline native material text does not begin with its teaching title"
    paired = [
        question for question in question_units
        if question.get("unit_id") == unit.get("paired_unit_id")
        and question.get("paired_unit_id") == unit.get("unit_id")
    ]
    if len(paired) != 1:
        return False, "inline answer lacks one reciprocal paired question"
    question = paired[0]
    question_metadata = (
        question.get("metadata")
        if isinstance(question.get("metadata"), dict) else {}
    )
    if (
        question.get("external_id") != unit.get("external_id")
        or question.get("source_id") != unit.get("source_id")
        or question.get("source_file") != unit.get("source_file")
        or question.get("source_sha256") != unit.get("source_sha256")
        or question.get("page") != unit.get("page")
        or question_metadata.get("teaching_role") != "worked_example"
        or question_metadata.get("teaching_title") != title
        or question_metadata.get("gradable") is not False
        or question_metadata.get("source_language") != source_language
        or question.get("text") != native_unit.get("text")
    ):
        return False, "inline answer and question do not share source/page/title"
    return True, None


def _prepare_item(workspace, source_root, expected_chapter_id, item_id,
                  inventory, blockers, crop_receipts):
    source_type = inventory["source_types"].get(item_id)
    if source_type is None:
        blockers.append(_blocking("missing_source_type", item_id=item_id))

    evidence = inventory["item_evidence"].get(item_id) or {}
    unit_index = inventory["unit_index"]
    question_ids = sorted(evidence.get("question_unit_ids") or ())
    answer_ids = sorted(evidence.get("answer_unit_ids") or ())
    question_units = [unit_index[unit_id] for unit_id in question_ids if unit_id in unit_index]
    answer_units = [unit_index[unit_id] for unit_id in answer_ids if unit_id in unit_index]
    if len(question_units) != len(question_ids):
        blockers.append(_blocking("missing_question_unit_revision", item_id=item_id))
    if not question_units:
        blockers.append(_blocking("missing_direct_question_unit", item_id=item_id))

    languages = set()
    for unit in question_units:
        try:
            languages.add(_source_language(unit, "question unit %s" % unit["unit_id"], True))
        except AuthoringError as exc:
            blockers.append(_blocking(
                "question_source_language_invalid", item_id=item_id,
                source_unit_id=unit.get("unit_id"), detail=str(exc),
            ))
    original_language = next(iter(languages)) if len(languages) == 1 else None
    if len(languages) > 1:
        blockers.append(_blocking(
            "question_source_language_conflict", item_id=item_id,
            languages=sorted(languages),
        ))

    prompt_candidates = {}
    for unit in question_units:
        for field, payload in _unit_payload(unit):
            # Strict claim records currently bind source ``text`` or ``latex``.
            # A natural-language question cannot use formula-only ``latex``;
            # therefore non-image prompts must have a real text payload.
            if field != "text":
                continue
            normalized = guide_content._normalize_exact_text(payload)
            prompt_candidates.setdefault(normalized, []).append(
                {"source_unit_id": unit["unit_id"], "payload_field": field, "value": payload}
            )

    assets = []
    for relative in sorted(inventory["item_assets"].get(item_id, {})):
        records = inventory["item_assets"][item_id][relative]
        try:
            row = _asset_record(
                workspace, source_root, expected_chapter_id, item_id,
                relative, records,
                set(inventory["tainted_asset_keys"]), crop_receipts,
            )
        except AuthoringError as exc:
            blockers.append(_blocking(
                "asset_revision_invalid", item_id=item_id, asset_path=relative,
                detail=str(exc),
            ))
            continue
        if row is not None:
            assets.append(row)
    prompt_assets = [
        row for row in assets if set(row["roles"]) & PROMPT_ROLES
    ]
    answer_assets = [
        row for row in assets if set(row["roles"]) & ANSWER_ROLES
    ]
    for side, scoped_assets in (
            ("prompt", prompt_assets), ("answer", answer_assets)):
        for asset in scoped_assets:
            page_like = bool(
                {"page_image", "crop_image"} & set(asset["types"])
                or asset["contains_full_prompt"]
            )
            isolation = asset.get("isolation")
            contexts = asset.get("required_context_ids")
            isolation_valid = (
                isolation == "target_item_only" and not contexts
            ) or (
                side == "prompt"
                and isolation == "target_with_required_context"
                and isinstance(contexts, list)
                and bool(contexts)
            )
            if page_like and (
                not asset.get("crop_receipt_id") or not isolation_valid
            ):
                blockers.append(_blocking(
                    "isolated_explanation_asset_not_item_scoped",
                    item_id=item_id,
                    side=side,
                    asset_path=asset["path"],
                    asset_types=asset["types"],
                    detail=(
                        "page-shaped Study Guide model input requires a "
                        "revision-bound target-only crop or an explicit "
                        "target-with-required-context prompt crop"
                    ),
                ))
    full_prompt = any(row["contains_full_prompt"] for row in prompt_assets)
    prompt_mode = "full_prompt" if full_prompt else ("figure_only" if prompt_assets else "none")
    requirements = sorted(inventory["item_asset_requirements"].get(item_id) or ())
    statuses = set()
    for unit in question_units:
        metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
        status = metadata.get("question_text_status")
        if isinstance(status, str):
            statuses.add(status)
    if requirements and not prompt_assets:
        blockers.append(_blocking(
            "unverified_page_reference_asset", item_id=item_id,
            declared_requirements=requirements,
        ))
    if statuses & {"page_reference", "stub"} and not full_prompt:
        blockers.append(_blocking(
            "unverified_page_reference_asset", item_id=item_id,
            question_text_statuses=sorted(statuses),
            prompt_asset_mode=prompt_mode,
        ))

    selected_prompt = None
    if prompt_mode != "full_prompt":
        if not prompt_candidates:
            blockers.append(_blocking("missing_prompt_text", item_id=item_id))
        elif len(prompt_candidates) != 1:
            blockers.append(_blocking(
                "ambiguous_prompt_text", item_id=item_id,
                candidate_count=len(prompt_candidates),
            ))
        else:
            records = next(iter(prompt_candidates.values()))
            records.sort(key=lambda row: (row["source_unit_id"], row["payload_field"]))
            selected_prompt = records[0]

    question_rows = []
    for unit in sorted(question_units, key=lambda row: row["unit_id"]):
        question_rows.append({
            "source_unit_id": unit["unit_id"],
            "source_language": _source_language(unit, "question unit", False),
            "payloads": [
                {"payload_field": field, "value": payload}
                for field, payload in _unit_payload(unit)
            ],
            "source_ref": _source_ref(unit, "question"),
        })
    answer_rows = []
    for unit in sorted(answer_units, key=lambda row: row["unit_id"]):
        metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
        base_trusted_material = (
            unit.get("provenance") in ("material", "ai_recovered")
            and metadata.get("source")
            not in ("ai_generated", "mixed", "unknown")
        )
        declared_origin = metadata.get("answer_origin")
        inline_binding_valid = True
        if declared_origin == "inline_material":
            inline_binding_valid, detail = _inline_material_answer_binding(
                unit, question_units, unit_index
            )
            if not inline_binding_valid:
                blockers.append(_blocking(
                    "inline_material_answer_binding_invalid",
                    item_id=item_id,
                    source_unit_id=unit.get("unit_id"),
                    detail=detail,
                ))
        trusted_material = base_trusted_material and inline_binding_valid
        evidence_origin = (
            "inline_material"
            if declared_origin == "inline_material"
            else ("separate_material" if trusted_material else "untrusted")
        )
        answer_rows.append({
            "source_unit_id": unit["unit_id"],
            "source_language": _source_language(unit, "answer unit", False),
            "trusted_material": trusted_material,
            "answer_origin": evidence_origin,
            "payloads": [
                {"payload_field": field, "value": payload}
                for field, payload in _unit_payload(unit, allow_latex=True)
            ],
            "source_ref": _source_ref(unit, "answer"),
        })
    return {
        "item_id": item_id,
        "source_type": source_type,
        "original_language": original_language,
        "question_text_statuses": sorted(statuses),
        "asset_requirements": requirements,
        "prompt_asset_mode": prompt_mode,
        "prompt_assets": prompt_assets,
        "answer_assets": answer_assets,
        "selected_prompt": selected_prompt,
        "question_evidence": question_rows,
        "answer_evidence": answer_rows,
    }


def _prepare_semantics(inventory, chapter, blockers):
    unit_index = inventory["unit_index"]
    semantic_ids, by_kind = guide_content._semantic_unit_ids(inventory["units"], chapter)
    rows = []
    formula_groups = {}
    for unit_id in semantic_ids:
        unit = unit_index[unit_id]
        kind = unit.get("kind")
        role = "formula" if kind == "formula" else "concept"
        try:
            source_ref = _semantic_ref(unit, role)
        except AuthoringError as exc:
            blockers.append(_blocking(
                "semantic_source_ref_unavailable", source_unit_id=unit_id,
                kind=kind, detail=str(exc),
            ))
            source_ref = None
        payloads = [
            {"payload_field": field, "value": payload}
            for field, payload in _unit_payload(unit, allow_latex=True)
        ]
        row = {
            "source_unit_id": unit_id,
            "kind": kind,
            "source_language": _source_language(unit, "semantic unit", False),
            "payloads": payloads,
            "source_ref": source_ref,
        }
        rows.append(row)
        if kind != "formula":
            continue
        latex = unit.get("latex")
        normalized = guide_content._normalize_exact_latex(latex)
        if not normalized:
            blockers.append(_blocking(
                "formula_latex_missing", source_unit_id=unit_id,
            ))
            continue
        formula_groups.setdefault(normalized, {"latex_values": {}, "unit_ids": [], "source_refs": []})
        group = formula_groups[normalized]
        group["latex_values"].setdefault(latex, 0)
        group["latex_values"][latex] += 1
        group["unit_ids"].append(unit_id)
        if source_ref is not None:
            group["source_refs"].append(source_ref)

    compiled_groups = []
    for normalized in sorted(formula_groups):
        group = formula_groups[normalized]
        # Prefer the most frequent exact spelling, then lexical order.  No math
        # syntax is rewritten; every member is proven equivalent only under the
        # validator's whitespace/Unicode normalization.
        latex = sorted(group["latex_values"], key=lambda value: (-group["latex_values"][value], value))[0]
        normalized_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        formula_id = "formula_" + normalized_sha256
        compiled_groups.append({
            "formula_group_id": formula_id,
            "latex": latex,
            "normalized_latex_sha256": normalized_sha256,
            "source_unit_ids": sorted(group["unit_ids"]),
            "source_refs": sorted(
                group["source_refs"], key=lambda ref: ref["source_unit_id"]
            ),
        })
    return rows, compiled_groups, by_kind


def _review_blockers(workspace, chapter):
    blockers = []
    try:
        scope = readiness._review_scope(workspace, chapter)
    except (OSError, ValueError, TypeError) as exc:
        return [_blocking("review_scope_unreadable", detail=str(exc))]
    for reason, count in sorted(scope.get("local_high_risk_reasons", {}).items()):
        blockers.append(_blocking(
            "chapter_high_risk_review_pending", reason=reason, count=count,
        ))
    for reason, count in sorted(scope.get("global_unbound_high_risk_reasons", {}).items()):
        blockers.append(_blocking(
            "global_high_risk_review_pending", reason=reason, count=count,
        ))
    conflicts_path = os.path.join(workspace, ".ingest", "source_conflicts.jsonl")
    if os.path.exists(conflicts_path):
        try:
            for row in read_jsonl(conflicts_path, default=[]):
                if isinstance(row, dict) and row.get("status") == "unresolved":
                    blockers.append(_blocking(
                        "unresolved_source_conflict",
                        conflict_id=row.get("conflict_id"),
                        conflict_kind=row.get("conflict_kind"),
                    ))
        except (OSError, ValueError, TypeError) as exc:
            blockers.append(_blocking("source_conflicts_unreadable", detail=str(exc)))
    for relative, code in (
        (".ingest/material_build_pending.json", "material_build_pending"),
        (".ingest/pending_ingest.json", "ingestion_transaction_pending"),
        (".ingest/pending_patch.json", "review_patch_transaction_pending"),
    ):
        if os.path.lexists(os.path.join(workspace, *relative.split("/"))):
            blockers.append(_blocking(code, path=relative))
    return blockers


def prepare_packet(workspace, chapter):
    workspace = _workspace(workspace)
    exam_start.require_full_processing(
        workspace, purpose="Study Guide authoring packet preparation")
    require_current_ingestion_v2(
        workspace, purpose="Study Guide authoring packet preparation")
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise AuthoringError("chapter must be an integer >= 1")
    expected_chapter_id = "ch%02d" % chapter
    before, before_hash = _snapshot(workspace)
    source_revisions, source_revisions_hash = _source_revision_snapshot(workspace)
    try:
        inventory = guide_content._source_inventory(workspace, chapter)
    except guide_content.ContentError as exc:
        raise AuthoringError("cannot build source inventory: %s" % exc)
    blockers = _review_blockers(workspace, chapter)
    try:
        crop_receipts = _crop_receipt_index(workspace)
    except AuthoringError as exc:
        crop_receipts = {}
        blockers.append(_blocking("crop_receipt_inventory_invalid", detail=str(exc)))
    language = guide_content._workspace_language(workspace)
    if language not in TARGET_LANGUAGES:
        blockers.append(_blocking("study_language_missing_or_invalid"))
        language = None
    answer_explanation_mode = _workspace_answer_explanation_mode(workspace)
    items = [
        _prepare_item(
            workspace, source_revisions["source_root"], expected_chapter_id,
            item_id, inventory, blockers, crop_receipts,
        )
        for item_id in inventory["item_ids"]
    ]
    # This is intentionally a current-chapter/current-denominator check, not a
    # claim that every receipt in the course is globally bijective with this
    # packet.  Receipts from other chapters remain out of scope.  Student work
    # is also excluded because ``student_attempt`` is tainted evidence and is
    # deliberately never admitted to Study Guide model input.
    current_item_ids = set(inventory["item_ids"])
    referenced_crop_receipt_ids = {
        asset["crop_receipt_id"]
        for item in items
        for key in ("prompt_assets", "answer_assets")
        for asset in item.get(key) or ()
        if isinstance(asset, dict)
        and isinstance(asset.get("crop_receipt_id"), str)
    }
    for receipt_id, receipt in sorted(crop_receipts.items()):
        if receipt.role == "student_attempt":
            continue
        in_current_scope = (
            receipt.chapter_id == expected_chapter_id
            or receipt.item_id in current_item_ids
        )
        if in_current_scope and receipt_id not in referenced_crop_receipt_ids:
            blockers.append(_blocking(
                "crop_receipt_orphan_in_current_scope",
                crop_receipt_id=receipt_id,
                receipt_chapter_id=receipt.chapter_id,
                receipt_item_id=receipt.item_id,
                receipt_role=receipt.role,
                expected_chapter_id=expected_chapter_id,
            ))
    semantic_units, formula_groups, semantic_by_kind = _prepare_semantics(
        inventory, chapter, blockers
    )
    semantic_asset_paths = [
        row["source_ref"]["asset_path"]
        for row in semantic_units
        if isinstance(row.get("source_ref"), dict)
        and isinstance(row["source_ref"].get("asset_path"), str)
    ]
    semantic_asset_revisions = _asset_revisions_for_paths(
        workspace, semantic_asset_paths
    )
    if not items:
        blockers.append(_blocking("empty_item_denominator"))
    if not semantic_units:
        blockers.append(_blocking("empty_semantic_denominator"))
    after_source_revisions, after_source_revisions_hash = _source_revision_snapshot(workspace)
    after, after_hash = _snapshot(workspace)
    if (
        before_hash != after_hash
        or source_revisions_hash != after_source_revisions_hash
        or source_revisions != after_source_revisions
    ):
        raise AuthoringError("source facts drifted while the authoring packet was prepared")
    blockers = sorted(blockers, key=lambda row: canonical_json(row))
    packet = {
        "schema_version": SCHEMA_VERSION,
        "chapter": chapter,
        "language": language,
        "answer_explanation_mode": answer_explanation_mode,
        "status": "blocked" if blockers else "ready",
        "source_snapshot": before,
        "source_snapshot_sha256": before_hash,
        "source_revisions": source_revisions,
        "source_revisions_sha256": source_revisions_hash,
        "asset_policy_sha256": inventory["asset_policy_sha256"],
        "inventory_counts": dict(inventory["counts"]),
        "item_ids": list(inventory["item_ids"]),
        "items": items,
        "semantic_unit_ids": [row["source_unit_id"] for row in semantic_units],
        "semantic_units": semantic_units,
        "semantic_counts_by_kind": semantic_by_kind,
        "formula_groups": formula_groups,
        "semantic_asset_revisions": semantic_asset_revisions,
        "blockers": blockers,
    }
    packet["packet_sha256"] = _packet_hash(packet)
    return packet


def build_annotations_template(packet):
    """Build a deliberately incomplete, machine-readable annotation scaffold.

    The template is a sidecar, not an annotations document.  Its wrapper and
    empty placeholders make it impossible to pass ``_validate_annotations``
    accidentally.  An author must copy only ``annotations`` to the fixed target,
    replace every placeholder, group all semantic/formula IDs into knowledge
    points, and then let the ordinary validator prove exact coverage.
    """

    if not isinstance(packet, dict):
        raise AuthoringError("annotation template requires an authoring packet")
    chapter = packet.get("chapter")
    language = packet.get("language")
    answer_explanation_mode = packet.get("answer_explanation_mode")
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise AuthoringError("annotation template packet chapter is invalid")
    if answer_explanation_mode not in ANSWER_EXPLANATION_MODES:
        raise AuthoringError(
            "annotation template packet answer explanation mode is invalid"
        )
    language_codes = sorted(TARGET_LANGUAGES.get(language, ()))

    def localized_placeholder():
        return {code: "" for code in language_codes}

    def explanation_provenance():
        return {code: "ai_supplement" for code in language_codes}

    def answer_provenance():
        return {code: "ai_generated" for code in language_codes}

    knowledge_point_placeholder = {
        "id": "REPLACE_WITH_STABLE_KNOWLEDGE_POINT_ID",
        "title": localized_placeholder(),
        "explanation": localized_placeholder(),
        "explanation_provenance": explanation_provenance(),
        "teaching_explanation": localized_placeholder(),
        "teaching_explanation_provenance": explanation_provenance(),
        # These invalid sentinel IDs are intentional: the author must partition
        # the packet inventories rather than accepting an invented grouping.
        "semantic_unit_ids": ["__ASSIGN_PACKET_SEMANTIC_UNIT_IDS__"],
        "example_ids": [],
        "formula_group_ids": [],
        "material_source_units": {},
    }

    formulas = []
    for group in packet.get("formula_groups") or []:
        formula_id = group.get("formula_group_id") if isinstance(group, dict) else None
        _identifier(formula_id, "packet formula_group_id")
        formulas.append({
            "formula_group_id": formula_id,
            "explanation": localized_placeholder(),
            "explanation_provenance": explanation_provenance(),
            "variables": [{
                "symbol": "",
                "meaning": localized_placeholder(),
                "meaning_provenance": explanation_provenance(),
            }],
            "applicability": localized_placeholder(),
            "applicability_provenance": explanation_provenance(),
        })

    walkthroughs = []
    target_languages = set(language_codes)
    for item in packet.get("items") or []:
        if not isinstance(item, dict):
            raise AuthoringError("packet item is malformed while building annotation template")
        item_id = _identifier(item.get("item_id"), "packet item_id")
        source_languages = (
            {item.get("original_language")}
            if item.get("original_language") in ("zh", "en") else set()
        )
        translation_languages = sorted(target_languages - source_languages)
        walkthrough = {
            "item_id": item_id,
            "title": localized_placeholder(),
            "translation": {code: "" for code in translation_languages},
            "translation_provenance": {
                code: "ai_translation" for code in translation_languages
            },
            "what_asked": localized_placeholder(),
            "what_asked_provenance": explanation_provenance(),
            "known_quantities": [],
            "unknown_quantities": [],
            "solution_kind": "",
            "knowledge_point_ids": [],
            "knowledge_point_uses": {},
            "knowledge_point_uses_provenance": {},
            "formula_uses": [],
            "steps": [],
            "steps_provenance": [],
            "answer": localized_placeholder(),
            "answer_provenance": answer_provenance(),
            "teaching_answer": localized_placeholder(),
            "teaching_answer_provenance": {
                code: "ai_supplemented" for code in language_codes
            },
        }
        if answer_explanation_mode == "ordinary":
            walkthrough["answer_explanation"] = localized_placeholder()
            walkthrough["answer_explanation_provenance"] = {
                code: "ai_supplement" for code in language_codes
            }
        walkthroughs.append(walkthrough)

    annotations = {
        "schema_version": SCHEMA_VERSION,
        "chapter": chapter,
        "packet_sha256": packet.get("packet_sha256"),
        "language": language,
        "answer_explanation_mode": answer_explanation_mode,
        "knowledge_points": [knowledge_point_placeholder],
        "formulas": formulas,
        "walkthroughs": walkthroughs,
    }
    return {
        "template_schema_version": 1,
        "template_status": "incomplete",
        "valid_annotations": False,
        "chapter": chapter,
        "packet_sha256": packet.get("packet_sha256"),
        "answer_explanation_mode": answer_explanation_mode,
        "target_output": _default_path(chapter, "authoring-annotations.json"),
        "instructions": [
            "Copy only the annotations object to target_output.",
            "Replace every empty string and every __...__ sentinel; remove this wrapper.",
            "Create enough knowledge_points to exactly partition packet semantic_unit_ids and formula_group_ids.",
            "Map every packet item_id to at least one knowledge point; an individual knowledge point may use example_ids=[].",
            "Keep every prefilled formula_group_id and walkthrough item_id unchanged and exactly once.",
            "For formula solutions fill formula_uses; for concept/procedure solutions add no_formula_reason and no_formula_reason_provenance.",
            "Use material provenance only for exact same-language packet text; label translations and supplements as AI-authored.",
            "An answer_evidence row with answer_origin=inline_material is exact same-page worked-Example material, not an AI answer or an independent grading key.",
            "Keep exact claimable OCR in explanation/answer, but put the polished beginner-facing prose and typeset math in teaching_explanation/teaching_answer.",
            ("Do not author an answer self-check. Fill each pre-shaped detailed "
             "answer_explanation and keep its provenance ai_supplement."
             if answer_explanation_mode == "ordinary" else
             "Do not author an answer self-check or answer_explanation here. Run "
             "study_guide_explain.py after annotations are complete so every item "
             "receives one isolated beginner explanation."),
            ("After all ordinary annotations are complete, run persist-notebooks; "
             "this template itself is intentionally invalid and cannot satisfy any "
             "completion gate."
             if answer_explanation_mode == "ordinary" else
             "After study_guide_explain.py has finalized every isolated item "
             "explanation, run persist-notebooks; this template itself is "
             "intentionally invalid and cannot satisfy any completion gate."),
        ],
        "packet_inventory": {
            "semantic_unit_ids": list(packet.get("semantic_unit_ids") or []),
            "formula_group_ids": [
                row["formula_group_id"] for row in packet.get("formula_groups") or []
            ],
            "item_ids": list(packet.get("item_ids") or []),
        },
        "knowledge_point_schema_placeholder": knowledge_point_placeholder,
        "annotations": annotations,
    }


def _load_packet(workspace, path, chapter, require_ready=True,
                 allow_legacy_isolated=False):
    source = _safe_workspace_path(workspace, path, "authoring packet")
    packet = _strict_json(source, "authoring packet")
    _shape(
        packet,
        (
            "schema_version", "chapter", "language", "status", "source_snapshot",
            "source_snapshot_sha256", "source_revisions", "source_revisions_sha256",
            "asset_policy_sha256", "inventory_counts",
            "item_ids", "items", "semantic_unit_ids", "semantic_units",
            "semantic_counts_by_kind", "formula_groups", "semantic_asset_revisions",
            "blockers", "packet_sha256",
        ),
        ("answer_explanation_mode",),
        "authoring packet",
    )
    if packet["schema_version"] != SCHEMA_VERSION or packet["chapter"] != chapter:
        raise AuthoringError("authoring packet schema/chapter does not match the command")
    has_answer_explanation_mode = "answer_explanation_mode" in packet
    if has_answer_explanation_mode:
        if packet["answer_explanation_mode"] not in ANSWER_EXPLANATION_MODES:
            raise AuthoringError("authoring packet answer_explanation_mode is invalid")
    elif not allow_legacy_isolated:
        raise AuthoringError(
            "authoring packet lacks answer_explanation_mode; only the internal "
            "read-only historical isolated verifier may accept it"
        )
    _packet_hash(packet)
    current, current_hash = _snapshot(workspace)
    if current_hash != packet["source_snapshot_sha256"] or current != packet["source_snapshot"]:
        raise AuthoringError("source facts drifted after prepare; create a new authoring packet")
    expected = prepare_packet(workspace, chapter)
    if not has_answer_explanation_mode:
        legacy_expected = copy.deepcopy(expected)
        legacy_expected.pop("answer_explanation_mode", None)
        legacy_expected.pop("packet_sha256", None)
        legacy_expected["packet_sha256"] = _packet_hash(legacy_expected)
        matches_expected = canonical_json(legacy_expected) == canonical_json(packet)
    else:
        matches_expected = canonical_json(expected) == canonical_json(packet)
    if not matches_expected:
        raise AuthoringError(
            "authoring packet does not exactly match the deterministic live source inventory"
        )
    if require_ready and (packet["status"] != "ready" or packet["blockers"]):
        raise AuthoringError("authoring packet is blocked; resolve blockers and run prepare again")
    return packet


def _translation(value, language, original_language, label):
    if not isinstance(value, dict) or set(value) - {"zh", "en"}:
        raise AuthoringError("%s must be a zh/en object" % label)
    source = {original_language} if original_language in ("zh", "en") else set()
    expected = set(TARGET_LANGUAGES[language]) - source
    if set(value) != expected:
        raise AuthoringError(
            "%s must contain exactly the missing target languages; expected=%s actual=%s"
            % (label, sorted(expected), sorted(value))
        )
    for code, text in value.items():
        text_label = "%s.%s" % (label, code)
        _nonblank(text, text_label)
        try:
            guide_content._validate_localized_prose_math(text, text_label)
        except guide_content.ContentError as exc:
            raise AuthoringError(str(exc))
    return dict(value)


def _exact_material_match(rows, language, text, label):
    target = guide_content._normalize_exact_text(text)
    matches = []
    for row in rows:
        if row.get("source_language") != language:
            continue
        for payload in row.get("payloads") or []:
            if payload.get("payload_field") != "text":
                continue
            if guide_content._normalize_exact_text(payload.get("value")) == target:
                matches.append({
                    "source_unit_id": row["source_unit_id"],
                    "payload_field": "text",
                    "quote_text": payload["value"],
                })
    unique = {
        (row["source_unit_id"], row["payload_field"], row["quote_text"]): row
        for row in matches
    }
    if len(unique) != 1:
        raise AuthoringError(
            "%s material provenance requires exactly one same-language exact text match; found %d"
            % (label, len(unique))
        )
    return next(iter(unique.values()))


def _field_provenance(value, text_by_language, label, material_matcher=None):
    if not isinstance(value, dict) or set(value) != set(text_by_language):
        raise AuthoringError("%s must label every authored language" % label)
    if any(item not in guide_content.EXPLANATION_PROVENANCE for item in value.values()):
        raise AuthoringError("%s has an invalid provenance label" % label)
    bindings = {}
    material_languages = {code for code, item in value.items() if item == "material"}
    for code in sorted(material_languages):
        if material_matcher is None:
            raise AuthoringError(
                "%s.%s cannot be material because this field has no exact claim route"
                % (label, code)
            )
        bindings[code] = material_matcher(code, text_by_language[code], "%s.%s" % (label, code))
    for code, item in value.items():
        if item == "ai_translation" and not (material_languages - {code}):
            raise AuthoringError(
                "%s.%s is ai_translation without another-language material base"
                % (label, code)
            )
    return dict(value), bindings


def _is_ai_visible_text(value):
    if not isinstance(value, str):
        return False
    for code in ("zh", "en"):
        unused_cleaned, provenance = strip_visible_provenance_prefix(value, code)
        if (provenance is not None
                and PROVENANCE_CATEGORIES[provenance] != "material"):
            return True
    return False


def _visible_substitution(value, provenance, language):
    """Return clean TeX while preserving the legacy helper's validation boundary.

    Provenance is a control-plane field, not mathematical content.  Older
    author output appended a ``\\text{AI...}`` suffix so the renderer could
    rediscover the label.  New manifests carry ``substitution_provenance``
    explicitly and must keep the TeX itself clean.
    """

    del language
    if provenance != "ai_supplement":
        raise AuthoringError("unsupported substitution provenance")
    return value


def _quantities(value, language, label):
    rows = _array(value, label)
    output = []
    for index, row in enumerate(rows):
        path = "%s[%d]" % (label, index)
        _shape(row, ("label", "provenance"), ("symbol", "value", "unit"), path)
        localized_label = _localized(row["label"], language, path + ".label")
        provenance, unused_bindings = _field_provenance(
            row["provenance"], localized_label, path + ".provenance"
        )
        item = {"label": localized_label, "provenance": provenance}
        for field in ("symbol", "value", "unit"):
            if field in row:
                item[field] = _nonblank(row[field], path + "." + field)
        output.append(item)
    return output


def _validate_annotations(packet, annotations, allow_legacy_isolated=False):
    _shape(
        annotations,
        (
            "schema_version", "chapter", "packet_sha256", "language",
            "knowledge_points", "formulas", "walkthroughs",
        ),
        ("answer_explanation_mode",),
        "annotations",
    )
    if annotations["schema_version"] != SCHEMA_VERSION:
        raise AuthoringError("annotations.schema_version must equal 1")
    if annotations["chapter"] != packet["chapter"]:
        raise AuthoringError("annotations.chapter does not match the packet")
    if annotations["packet_sha256"] != packet["packet_sha256"]:
        raise AuthoringError("annotations are bound to another packet revision")
    language = annotations["language"]
    if language != packet["language"] or language not in TARGET_LANGUAGES:
        raise AuthoringError("annotations.language does not match the packet language")
    if "answer_explanation_mode" in annotations:
        answer_explanation_mode = annotations["answer_explanation_mode"]
        if (
            answer_explanation_mode not in ANSWER_EXPLANATION_MODES
            or answer_explanation_mode != packet.get("answer_explanation_mode")
        ):
            raise AuthoringError(
                "annotations.answer_explanation_mode does not match the packet"
            )
    elif allow_legacy_isolated and "answer_explanation_mode" not in packet:
        answer_explanation_mode = "isolated"
    else:
        raise AuthoringError(
            "annotations.answer_explanation_mode is required"
        )

    semantic_by_id = {row["source_unit_id"]: row for row in packet["semantic_units"]}
    formula_by_id = {row["formula_group_id"]: row for row in packet["formula_groups"]}
    item_by_id = {row["item_id"]: row for row in packet["items"]}
    kp_rows = _array(annotations["knowledge_points"], "annotations.knowledge_points", True)
    kp_by_id = {}
    semantic_owner = {}
    formula_owner = {}
    item_kps = {}
    normalized_kps = []
    for index, row in enumerate(kp_rows):
        path = "annotations.knowledge_points[%d]" % index
        _shape(
            row,
            (
                "id", "title", "explanation", "explanation_provenance",
                "semantic_unit_ids", "example_ids", "formula_group_ids",
                "material_source_units",
            ),
            ("teaching_explanation", "teaching_explanation_provenance"),
            path,
        )
        kp_id = _identifier(row["id"], path + ".id")
        if kp_id in kp_by_id:
            raise AuthoringError("duplicate knowledge-point id %s" % kp_id)
        title = _localized(row["title"], language, path + ".title")
        explanation = _localized(row["explanation"], language, path + ".explanation")
        provenance = row["explanation_provenance"]
        if not isinstance(provenance, dict) or set(provenance) != set(explanation):
            raise AuthoringError(
                "%s.explanation_provenance must label every explanation language" % path
            )
        if any(value not in guide_content.EXPLANATION_PROVENANCE for value in provenance.values()):
            raise AuthoringError("%s.explanation_provenance has an invalid label" % path)
        if ("teaching_explanation" in row) != ("teaching_explanation_provenance" in row):
            raise AuthoringError(
                "%s teaching_explanation and teaching_explanation_provenance must appear together"
                % path
            )
        teaching_explanation = None
        teaching_provenance = None
        if "teaching_explanation" in row:
            teaching_explanation = _localized(
                row["teaching_explanation"], language, path + ".teaching_explanation")
            teaching_provenance = row["teaching_explanation_provenance"]
            if (not isinstance(teaching_provenance, dict)
                    or set(teaching_provenance) != set(teaching_explanation)
                    or any(value != "ai_supplement"
                           for value in teaching_provenance.values())):
                raise AuthoringError(
                    "%s.teaching_explanation_provenance must label every language "
                    "ai_supplement; exact material evidence stays in explanation" % path
                )
            for code, text in teaching_explanation.items():
                hazard = find_math_layout_hazard(text)
                if hazard:
                    raise AuthoringError(
                        "%s.teaching_explanation.%s still contains %s OCR math layout; "
                        "the reviewed teaching copy itself must use readable typeset math"
                        % (path, code, hazard["code"])
                    )
        for code, label in provenance.items():
            hazard = find_math_layout_hazard(explanation[code]) if label == "material" else None
            if hazard and teaching_explanation is None:
                raise AuthoringError(
                    "%s.explanation.%s contains %s OCR math layout; add a reviewed "
                    "teaching_explanation with typeset math" % (path, code, hazard["code"])
                )

        semantic_ids = []
        for position, unit_id in enumerate(_array(row["semantic_unit_ids"], path + ".semantic_unit_ids", True)):
            unit_id = _identifier(unit_id, "%s.semantic_unit_ids[%d]" % (path, position))
            if unit_id not in semantic_by_id:
                raise AuthoringError("%s references a semantic unit outside the packet" % path)
            if unit_id in semantic_owner:
                raise AuthoringError(
                    "semantic unit %s is assigned to both %s and %s"
                    % (unit_id, semantic_owner[unit_id], kp_id)
                )
            semantic_owner[unit_id] = kp_id
            semantic_ids.append(unit_id)
        if len(set(semantic_ids)) != len(semantic_ids):
            raise AuthoringError("%s.semantic_unit_ids contains duplicates" % path)
        if not any(semantic_by_id[unit_id]["kind"] != "formula" for unit_id in semantic_ids):
            raise AuthoringError(
                "%s needs at least one non-formula semantic unit for concept evidence" % path
            )

        examples = []
        for position, item_id in enumerate(_array(row["example_ids"], path + ".example_ids")):
            item_id = _identifier(item_id, "%s.example_ids[%d]" % (path, position))
            if item_id not in item_by_id:
                raise AuthoringError("%s references an item outside the packet" % path)
            if item_id in examples:
                raise AuthoringError("%s.example_ids contains duplicates" % path)
            examples.append(item_id)
            item_kps.setdefault(item_id, set()).add(kp_id)

        formula_ids = []
        for position, formula_id in enumerate(_array(row["formula_group_ids"], path + ".formula_group_ids")):
            formula_id = _identifier(
                formula_id, "%s.formula_group_ids[%d]" % (path, position)
            )
            group = formula_by_id.get(formula_id)
            if group is None:
                raise AuthoringError("%s references a formula group outside the packet" % path)
            if formula_id in formula_owner:
                raise AuthoringError("formula group %s is assigned more than once" % formula_id)
            if not set(group["source_unit_ids"]).issubset(semantic_ids):
                raise AuthoringError(
                    "formula group %s must be assigned with all of its semantic units"
                    % formula_id
                )
            formula_owner[formula_id] = kp_id
            formula_ids.append(formula_id)

        material_sources = row["material_source_units"]
        if not isinstance(material_sources, dict) or set(material_sources) - {"zh", "en"}:
            raise AuthoringError("%s.material_source_units must be keyed by zh/en" % path)
        required_material = {
            code for code, label in provenance.items() if label == "material"
        }
        if set(material_sources) != required_material:
            raise AuthoringError(
                "%s.material_source_units must exactly bind material explanations" % path
            )
        normalized_sources = {}
        for code, unit_id in material_sources.items():
            unit_id = _identifier(unit_id, "%s.material_source_units.%s" % (path, code))
            unit = semantic_by_id.get(unit_id)
            if unit is None or unit_id not in semantic_ids or unit["kind"] == "formula":
                raise AuthoringError(
                    "%s.material_source_units.%s must select an assigned textual concept unit"
                    % (path, code)
                )
            if unit.get("source_language") != code:
                raise AuthoringError(
                    "%s.material_source_units.%s source language does not match" % (path, code)
                )
            if not any(payload["payload_field"] == "text" for payload in unit["payloads"]):
                raise AuthoringError(
                    "%s.material_source_units.%s has no claimable text payload" % (path, code)
                )
            normalized_sources[code] = unit_id
        for code, label in provenance.items():
            if label == "ai_translation" and not (required_material - {code}):
                raise AuthoringError(
                    "%s explanation %s is ai_translation without another-language material base"
                    % (path, code)
                )
        normalized = {
            "id": kp_id,
            "title": title,
            "explanation": explanation,
            "explanation_provenance": dict(provenance),
            "semantic_unit_ids": semantic_ids,
            "example_ids": examples,
            "formula_group_ids": formula_ids,
            "material_source_units": normalized_sources,
        }
        if teaching_explanation is not None:
            normalized["teaching_explanation"] = teaching_explanation
            normalized["teaching_explanation_provenance"] = dict(teaching_provenance)
        kp_by_id[kp_id] = normalized
        normalized_kps.append(normalized)

    expected_semantic = set(packet["semantic_unit_ids"])
    if set(semantic_owner) != expected_semantic:
        raise AuthoringError(
            "knowledge points must exactly partition semantic units; missing=%s extra=%s"
            % (sorted(expected_semantic - set(semantic_owner)), sorted(set(semantic_owner) - expected_semantic))
        )
    if set(formula_owner) != set(formula_by_id):
        raise AuthoringError(
            "knowledge points must exactly partition formula groups; missing=%s extra=%s"
            % (sorted(set(formula_by_id) - set(formula_owner)), sorted(set(formula_owner) - set(formula_by_id)))
        )
    if set(item_kps) != set(item_by_id):
        raise AuthoringError(
            "knowledge points must map every item; missing=%s extra=%s"
            % (sorted(set(item_by_id) - set(item_kps)), sorted(set(item_kps) - set(item_by_id)))
        )

    formula_annotations = {}
    for index, row in enumerate(_array(annotations["formulas"], "annotations.formulas")):
        path = "annotations.formulas[%d]" % index
        _shape(
            row,
            (
                "formula_group_id", "explanation", "explanation_provenance",
                "variables", "applicability", "applicability_provenance",
            ),
            (),
            path,
        )
        formula_id = _identifier(row["formula_group_id"], path + ".formula_group_id")
        if formula_id not in formula_by_id or formula_id in formula_annotations:
            raise AuthoringError("%s has an unknown or duplicate formula_group_id" % path)
        group_rows = [
            semantic_by_id[unit_id]
            for unit_id in formula_by_id[formula_id]["source_unit_ids"]
        ]

        def formula_matcher(code, text, field_label):
            return _exact_material_match(group_rows, code, text, field_label)

        explanation = _localized(row["explanation"], language, path + ".explanation")
        explanation_provenance, explanation_bindings = _field_provenance(
            row["explanation_provenance"], explanation,
            path + ".explanation_provenance", formula_matcher,
        )
        applicability = _localized(
            row["applicability"], language, path + ".applicability"
        )
        applicability_provenance, applicability_bindings = _field_provenance(
            row["applicability_provenance"], applicability,
            path + ".applicability_provenance", formula_matcher,
        )
        variables = []
        variable_bindings = {}
        symbols = set()
        for position, variable in enumerate(_array(row["variables"], path + ".variables")):
            variable_path = "%s.variables[%d]" % (path, position)
            _shape(
                variable, ("symbol", "meaning", "meaning_provenance"), (),
                variable_path,
            )
            symbol = _nonblank(variable["symbol"], variable_path + ".symbol")
            if symbol in symbols:
                raise AuthoringError("%s repeats variable symbol %s" % (path, symbol))
            symbols.add(symbol)
            meaning = _localized(variable["meaning"], language, variable_path + ".meaning")
            meaning_provenance, meaning_bindings = _field_provenance(
                variable["meaning_provenance"], meaning,
                variable_path + ".meaning_provenance", formula_matcher,
            )
            variables.append({
                "symbol": symbol,
                "meaning": meaning,
                "meaning_provenance": meaning_provenance,
            })
            variable_bindings[str(position)] = meaning_bindings
        formula_annotations[formula_id] = {
            "formula_group_id": formula_id,
            "explanation": explanation,
            "explanation_provenance": explanation_provenance,
            "variables": variables,
            "applicability": applicability,
            "applicability_provenance": applicability_provenance,
            "material_bindings": {
                "explanation": explanation_bindings,
                "applicability": applicability_bindings,
                "variable_meaning": variable_bindings,
            },
        }
    if set(formula_annotations) != set(formula_by_id):
        raise AuthoringError(
            "annotations.formulas must exactly cover formula groups; missing=%s extra=%s"
            % (sorted(set(formula_by_id) - set(formula_annotations)), sorted(set(formula_annotations) - set(formula_by_id)))
        )

    walkthroughs = {}
    for index, row in enumerate(_array(annotations["walkthroughs"], "annotations.walkthroughs")):
        path = "annotations.walkthroughs[%d]" % index
        walkthrough_required = (
            "item_id", "title", "translation", "translation_provenance",
            "what_asked", "known_quantities",
            "unknown_quantities", "solution_kind", "knowledge_point_ids",
            "what_asked_provenance", "knowledge_point_uses",
            "knowledge_point_uses_provenance", "formula_uses", "steps",
            "steps_provenance", "answer", "answer_provenance",
        )
        if answer_explanation_mode == "ordinary":
            walkthrough_required += (
                "answer_explanation", "answer_explanation_provenance",
            )
        _shape(
            row,
            walkthrough_required,
            (
                "no_formula_reason", "no_formula_reason_provenance",
                "teaching_answer", "teaching_answer_provenance",
            ),
            path,
        )
        item_id = _identifier(row["item_id"], path + ".item_id")
        item = item_by_id.get(item_id)
        if item is None or item_id in walkthroughs:
            raise AuthoringError("%s has an unknown or duplicate item_id" % path)

        def question_matcher(code, text, field_label):
            return _exact_material_match(
                item["question_evidence"], code, text, field_label
            )

        what_asked = _localized(row["what_asked"], language, path + ".what_asked")
        what_asked_provenance, what_asked_bindings = _field_provenance(
            row["what_asked_provenance"], what_asked,
            path + ".what_asked_provenance", question_matcher,
        )
        translation = _translation(
            row["translation"], language, item["original_language"], path + ".translation"
        )
        translation_provenance = row["translation_provenance"]
        if (
            not isinstance(translation_provenance, dict)
            or set(translation_provenance) != set(translation)
            or any(value != "ai_translation" for value in translation_provenance.values())
        ):
            raise AuthoringError(
                "%s.translation_provenance must label every generated translation ai_translation"
                % path
            )
        kp_ids = []
        for position, kp_id in enumerate(_array(row["knowledge_point_ids"], path + ".knowledge_point_ids", True)):
            kp_id = _identifier(kp_id, "%s.knowledge_point_ids[%d]" % (path, position))
            if kp_id not in kp_by_id or kp_id in kp_ids:
                raise AuthoringError("%s has an unknown or duplicate knowledge_point_id" % path)
            kp_ids.append(kp_id)
        if set(kp_ids) != item_kps[item_id]:
            raise AuthoringError("%s.knowledge_point_ids disagree with knowledge-point example links" % path)
        uses = row["knowledge_point_uses"]
        if not isinstance(uses, dict) or set(uses) != set(kp_ids):
            raise AuthoringError("%s.knowledge_point_uses must exactly match knowledge_point_ids" % path)
        uses_provenance = row["knowledge_point_uses_provenance"]
        if not isinstance(uses_provenance, dict) or set(uses_provenance) != set(kp_ids):
            raise AuthoringError(
                "%s.knowledge_point_uses_provenance must exactly match knowledge_point_ids"
                % path
            )
        normalized_uses = {
            kp_id: _localized(uses[kp_id], language, "%s.knowledge_point_uses.%s" % (path, kp_id))
            for kp_id in kp_ids
        }
        normalized_uses_provenance = {}
        for kp_id in kp_ids:
            normalized_uses_provenance[kp_id], unused_bindings = _field_provenance(
                uses_provenance[kp_id], normalized_uses[kp_id],
                "%s.knowledge_point_uses_provenance.%s" % (path, kp_id),
            )
        available_formulas = set()
        for kp_id in kp_ids:
            available_formulas.update(kp_by_id[kp_id]["formula_group_ids"])
        formula_uses = []
        seen_formula_uses = set()
        for position, formula_use in enumerate(_array(row["formula_uses"], path + ".formula_uses")):
            formula_path = "%s.formula_uses[%d]" % (path, position)
            _shape(
                formula_use,
                (
                    "formula_group_id", "why_applicable",
                    "why_applicable_provenance", "variable_mapping", "substitution",
                    "substitution_provenance",
                ),
                (),
                formula_path,
            )
            formula_id = _identifier(formula_use["formula_group_id"], formula_path + ".formula_group_id")
            if formula_id not in available_formulas or formula_id in seen_formula_uses:
                raise AuthoringError("%s uses an unavailable or duplicate formula group" % formula_path)
            seen_formula_uses.add(formula_id)
            mappings = []
            mapped_symbols = set()
            for map_index, mapping in enumerate(_array(formula_use["variable_mapping"], formula_path + ".variable_mapping")):
                mapping_path = "%s.variable_mapping[%d]" % (formula_path, map_index)
                _shape(
                    mapping, ("symbol", "maps_to", "maps_to_provenance"), (),
                    mapping_path,
                )
                symbol = _nonblank(mapping["symbol"], mapping_path + ".symbol")
                if symbol in mapped_symbols:
                    raise AuthoringError("%s repeats variable symbol %s" % (formula_path, symbol))
                mapped_symbols.add(symbol)
                maps_to = _localized(
                    mapping["maps_to"], language, mapping_path + ".maps_to"
                )
                maps_to_provenance, unused_bindings = _field_provenance(
                    mapping["maps_to_provenance"], maps_to,
                    mapping_path + ".maps_to_provenance",
                )
                mappings.append({
                    "symbol": symbol,
                    "maps_to": maps_to,
                    "maps_to_provenance": maps_to_provenance,
                })
            expected_symbols = {
                variable["symbol"] for variable in formula_annotations[formula_id]["variables"]
            }
            if not expected_symbols:
                raise AuthoringError(
                    "%s uses formula group %s but that formula defines no variables; "
                    "every used formula needs an explicit variable mapping for "
                    "student-facing substitution" % (formula_path, formula_id)
                )
            if mapped_symbols != expected_symbols:
                raise AuthoringError(
                    "%s.variable_mapping must exactly cover %s" % (formula_path, sorted(expected_symbols))
                )
            substitution = _nonblank(formula_use["substitution"], formula_path + ".substitution")
            if "\n" in substitution or "\r" in substitution or substitution.startswith("$") or substitution.endswith("$"):
                raise AuthoringError("%s.substitution must be one-line TeX without $ delimiters" % formula_path)
            substitution_provenance = formula_use["substitution_provenance"]
            if substitution_provenance != "ai_supplement":
                raise AuthoringError(
                    "%s.substitution_provenance must be ai_supplement because substitution "
                    "has no exact material-claim route" % formula_path
                )
            why_applicable = _localized(
                formula_use["why_applicable"], language,
                formula_path + ".why_applicable",
            )
            why_provenance, unused_bindings = _field_provenance(
                formula_use["why_applicable_provenance"], why_applicable,
                formula_path + ".why_applicable_provenance",
            )
            formula_uses.append({
                "formula_id": formula_id,
                "why_applicable": why_applicable,
                "why_applicable_provenance": why_provenance,
                "variable_mapping": mappings,
                "substitution": substitution,
                "substitution_provenance": substitution_provenance,
            })
        solution_kind = row["solution_kind"]
        if solution_kind not in guide_content.SOLUTION_KINDS:
            raise AuthoringError("%s.solution_kind is invalid" % path)
        no_formula_reason = None
        no_formula_reason_provenance = None
        no_formula_reason_bindings = {}
        if solution_kind == "formula":
            if (
                not formula_uses or "no_formula_reason" in row
                or "no_formula_reason_provenance" in row
            ):
                raise AuthoringError("%s formula solutions need formula uses and no no_formula_reason" % path)
        else:
            if (
                formula_uses or "no_formula_reason" not in row
                or "no_formula_reason_provenance" not in row
            ):
                raise AuthoringError(
                    "%s non-formula solutions need no_formula_reason, provenance, and no formula uses"
                    % path
                )
            no_formula_reason = _localized(row["no_formula_reason"], language, path + ".no_formula_reason")
            no_formula_reason_provenance, no_formula_reason_bindings = _field_provenance(
                row["no_formula_reason_provenance"], no_formula_reason,
                path + ".no_formula_reason_provenance",
            )
        steps = [
            _localized(step, language, "%s.steps[%d]" % (path, position))
            for position, step in enumerate(_array(row["steps"], path + ".steps", True))
        ]
        raw_steps_provenance = _array(
            row["steps_provenance"], path + ".steps_provenance", True
        )
        if len(raw_steps_provenance) != len(steps):
            raise AuthoringError("%s.steps_provenance must align one-to-one with steps" % path)
        steps_provenance = []
        step_bindings = {}
        for position, step in enumerate(steps):
            provenance_row, bindings_row = _field_provenance(
                raw_steps_provenance[position], step,
                "%s.steps_provenance[%d]" % (path, position),
            )
            steps_provenance.append(provenance_row)
            step_bindings[str(position)] = bindings_row
        provenance = row["answer_provenance"]
        material_evidence_codes = {
            code for code, label in provenance.items() if label == "material"
        } if isinstance(provenance, dict) and "teaching_answer" in row else set()
        answer = _localized(
            row["answer"], language, path + ".answer",
            skip_math_codes=material_evidence_codes,
        )
        if not isinstance(provenance, dict) or set(provenance) != set(answer):
            raise AuthoringError("%s.answer_provenance must label every answer language" % path)
        if any(value not in guide_content.ANSWER_PROVENANCE for value in provenance.values()):
            raise AuthoringError("%s.answer_provenance has an invalid label" % path)
        if ("teaching_answer" in row) != ("teaching_answer_provenance" in row):
            raise AuthoringError(
                "%s teaching_answer and teaching_answer_provenance must appear together" % path
            )
        teaching_answer = None
        teaching_answer_provenance = None
        if "teaching_answer" in row:
            teaching_answer = _localized(
                row["teaching_answer"], language, path + ".teaching_answer")
            teaching_answer_provenance = row["teaching_answer_provenance"]
            if (not isinstance(teaching_answer_provenance, dict)
                    or set(teaching_answer_provenance) != set(teaching_answer)
                    or any(value not in ("ai_supplemented", "ai_generated")
                           for value in teaching_answer_provenance.values())):
                raise AuthoringError(
                    "%s.teaching_answer_provenance must label every language "
                    "ai_supplemented or ai_generated" % path
                )
            for code, text in teaching_answer.items():
                hazard = find_math_layout_hazard(text)
                if hazard:
                    raise AuthoringError(
                        "%s.teaching_answer.%s still contains %s OCR math layout; "
                        "the reviewed teaching copy itself must use readable typeset math"
                        % (path, code, hazard["code"])
                    )
        for code, label in provenance.items():
            hazard = find_math_layout_hazard(answer[code]) if label == "material" else None
            if hazard and teaching_answer is None:
                raise AuthoringError(
                    "%s.answer.%s contains %s OCR math layout; add a reviewed "
                    "teaching_answer with typeset math" % (path, code, hazard["code"])
                )
        for code, label in provenance.items():
            if label != "material":
                continue
            matched = False
            normalized_answer = guide_content._normalize_exact_text(answer[code])
            for evidence in item["answer_evidence"]:
                if evidence["source_language"] != code or not evidence["trusted_material"]:
                    continue
                if any(
                    guide_content._normalize_exact_text(payload["value"]) == normalized_answer
                    for payload in evidence["payloads"]
                ):
                    matched = True
                    break
            if not matched:
                raise AuthoringError(
                    "%s material %s answer does not exactly match trusted same-language evidence"
                    % (path, code)
                )
        answer_explanation = None
        answer_explanation_provenance = None
        if answer_explanation_mode == "ordinary":
            try:
                answer_explanation = (
                    guide_content._validate_detailed_answer_explanation(
                        row["answer_explanation"],
                        row["answer_explanation_provenance"],
                        teaching_answer or answer,
                        language,
                        path + ".answer_explanation",
                    )
                )
            except guide_content.ContentError as exc:
                raise AuthoringError(str(exc)) from exc
            answer_explanation_provenance = dict(
                row["answer_explanation_provenance"]
            )
        walkthrough = {
            "item_id": item_id,
            "title": _localized(row["title"], language, path + ".title"),
            "translation": translation,
            "translation_provenance": dict(translation_provenance),
            "what_asked": what_asked,
            "what_asked_provenance": what_asked_provenance,
            "known_quantities": _quantities(row["known_quantities"], language, path + ".known_quantities"),
            "unknown_quantities": _quantities(row["unknown_quantities"], language, path + ".unknown_quantities"),
            "solution_kind": solution_kind,
            "knowledge_point_ids": kp_ids,
            "knowledge_point_uses": normalized_uses,
            "knowledge_point_uses_provenance": normalized_uses_provenance,
            "formula_uses": formula_uses,
            "steps": steps,
            "steps_provenance": steps_provenance,
            "answer": answer,
            "answer_provenance": dict(provenance),
            "material_bindings": {
                "what_asked": what_asked_bindings,
                "steps": step_bindings,
                "no_formula_reason": no_formula_reason_bindings,
            },
        }
        if teaching_answer is not None:
            walkthrough["teaching_answer"] = teaching_answer
            walkthrough["teaching_answer_provenance"] = dict(
                teaching_answer_provenance)
        if answer_explanation is not None:
            walkthrough["answer_explanation"] = dict(answer_explanation)
            walkthrough["answer_explanation_provenance"] = (
                answer_explanation_provenance
            )
        if no_formula_reason is not None:
            walkthrough["no_formula_reason"] = no_formula_reason
            walkthrough["no_formula_reason_provenance"] = no_formula_reason_provenance
        walkthroughs[item_id] = walkthrough
    if set(walkthroughs) != set(item_by_id):
        raise AuthoringError(
            "annotations.walkthroughs must exactly cover packet items; missing=%s extra=%s"
            % (sorted(set(item_by_id) - set(walkthroughs)), sorted(set(walkthroughs) - set(item_by_id)))
        )
    return {
        "language": language,
        "answer_explanation_mode": answer_explanation_mode,
        "knowledge_points": normalized_kps,
        "knowledge_points_by_id": kp_by_id,
        "formulas_by_id": formula_annotations,
        "walkthroughs_by_id": walkthroughs,
    }


def _load_annotations(workspace, path, packet, allow_legacy_isolated=False):
    source = _safe_workspace_path(workspace, path, "authoring annotations")
    value = _strict_json(source, "authoring annotations")
    return value, _validate_annotations(
        packet, value, allow_legacy_isolated=allow_legacy_isolated)


def rebase_annotations(workspace, chapter, packet_path, annotations_path):
    """Mechanically rebind still-valid authored prose to the current packet.

    Crop-receipt upgrades legitimately change packet/asset hashes without changing
    the authored knowledge-point, formula, or walkthrough inventory.  Re-authoring
    a large chapter for that control-plane-only change is wasteful.  This migration
    is intentionally narrow: it may update ``packet_sha256``, add the packet's
    missing ``answer_explanation_mode`` binding, and remove the deprecated paired
    ``self_check``/``self_check_provenance`` fields.  The complete
    current annotation validator must then accept every remaining field against the
    live packet before the canonical file is atomically replaced.
    """

    workspace = _workspace(workspace)
    require_current_ingestion_v2(
        workspace, purpose="Study Guide annotation rebasing")
    canonical_relative = _reserved_output_relative(chapter, "annotations")
    canonical_packet_relative = _reserved_output_relative(chapter, "packet")

    with workspace_publication_lock(workspace):
        exam_start.require_full_processing(
            workspace, purpose="Study Guide annotation rebase")
        require_current_ingestion_v2(
            workspace, purpose="Study Guide annotation rebase")
        # Resolve and re-check the mutable input only after taking the same lock
        # used for publication.  Otherwise a cooperating writer could replace the
        # canonical file between the path check and the strict JSON read.
        supplied = _safe_workspace_path(
            workspace, annotations_path, "authoring annotations")
        try:
            supplied_relative = normalize_workspace_path(
                _relative(workspace, supplied))
        except (UnsafePathError, ValueError) as exc:
            raise AuthoringError(
                "authoring annotations path is not canonical: %s" % exc)
        if supplied_relative != canonical_relative:
            raise AuthoringError(
                "annotation rebase requires canonical input %s"
                % canonical_relative)
        packet_source = _safe_workspace_path(
            workspace, packet_path, "authoring packet")
        try:
            packet_relative = normalize_workspace_path(
                _relative(workspace, packet_source))
        except (UnsafePathError, ValueError) as exc:
            raise AuthoringError(
                "authoring packet path is not canonical: %s" % exc)
        if packet_relative != canonical_packet_relative:
            raise AuthoringError(
                "annotation rebase requires canonical packet %s"
                % canonical_packet_relative)
        packet = _load_packet(
            workspace, packet_source, chapter, require_ready=True)
        original = _strict_json(supplied, "authoring annotations")
        original_sha256 = _annotation_hash(original)
        try:
            _validate_annotations(packet, original)
        except AuthoringError:
            pass
        else:
            return {
                "changed": False,
                "chapter": chapter,
                "old_packet_sha256": original.get("packet_sha256"),
                "new_packet_sha256": packet["packet_sha256"],
                "original_annotations_sha256": original_sha256,
                "annotations_sha256": original_sha256,
                "removed_self_check_pairs": 0,
                "added_answer_explanation_mode": False,
                "output": supplied_relative,
            }

        _shape(
            original,
            (
                "schema_version", "chapter", "packet_sha256", "language",
                "knowledge_points", "formulas", "walkthroughs",
            ),
            ("answer_explanation_mode",),
            "authoring annotations eligible for mechanical rebase",
        )
        migrated = copy.deepcopy(original)
        old_packet_sha256 = migrated.get("packet_sha256")
        migrated["packet_sha256"] = packet["packet_sha256"]
        added_answer_explanation_mode = False
        if "answer_explanation_mode" not in migrated:
            migrated["answer_explanation_mode"] = packet[
                "answer_explanation_mode"
            ]
            added_answer_explanation_mode = True
        removed = 0
        for index, row in enumerate(_array(
                migrated["walkthroughs"], "annotations.walkthroughs")):
            if not isinstance(row, dict):
                raise AuthoringError(
                    "annotations.walkthroughs[%d] must be an object" % index)
            has_check = "self_check" in row
            has_provenance = "self_check_provenance" in row
            if has_check != has_provenance:
                raise AuthoringError(
                    "annotations.walkthroughs[%d] has an incomplete legacy "
                    "self-check pair; rebase refused" % index)
            if has_check:
                row.pop("self_check")
                row.pop("self_check_provenance")
                removed += 1
        try:
            _validate_annotations(packet, migrated)
        except AuthoringError as exc:
            raise AuthoringError(
                "annotation rebase refused: after the only allowed mechanical "
                "changes, the complete current validator still fails: %s" % exc)

        migrated_sha256 = _annotation_hash(migrated)
        output = _publish_json(
            workspace,
            [(
                canonical_relative,
                migrated,
                "rebased authoring annotations output",
                canonical_relative,
            )],
            expected_snapshot_sha256=packet["source_snapshot_sha256"],
            expected_source_revisions_sha256=packet[
                "source_revisions_sha256"],
            expected_asset_revisions=_packet_asset_revisions(packet),
            lock_held=True,
        )[0]
        return {
            "changed": True,
            "chapter": chapter,
            "old_packet_sha256": old_packet_sha256,
            "new_packet_sha256": packet["packet_sha256"],
            "original_annotations_sha256": original_sha256,
            "annotations_sha256": migrated_sha256,
            "removed_self_check_pairs": removed,
            "added_answer_explanation_mode": added_answer_explanation_mode,
            "output": _relative(workspace, output),
        }


def _load_answer_explanation_receipt(workspace, chapter, packet_path,
                                     annotations_path, receipt_path):
    """Load the canonical, recomputed per-item explanation receipt.

    The isolated-explanation module imports this authoring module, so keep the
    reverse import local and late.  This function is called only for explicit
    ``answer_explanation_mode=isolated``: even direct library callers on that
    route fail closed on a missing receipt.  Ordinary mode never calls it, and
    legacy ingestion-v1 Guides use ``study_guide_content.py`` instead.
    """

    canonical_relative = "notebook/ch%02d.answer-explanation.receipt.json" % chapter
    if receipt_path is None:
        receipt_path = canonical_relative
    canonical = _safe_workspace_path(
        workspace, canonical_relative, "answer explanation receipt")
    supplied = _safe_workspace_path(
        workspace, receipt_path, "answer explanation receipt")
    if os.path.normcase(os.path.abspath(supplied)) != os.path.normcase(
            os.path.abspath(canonical)):
        raise AuthoringError(
            "answer explanation receipt must use canonical path %s"
            % canonical_relative
        )
    try:
        try:
            from . import study_guide_explain as explain
        except (ImportError, ValueError):
            import study_guide_explain as explain
        receipt = explain.load_final_receipt(
            workspace, chapter, packet_path=packet_path,
            annotations_path=annotations_path,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise AuthoringError(
            "isolated answer explanations are missing, incomplete, or stale: %s" % exc
        ) from exc
    return receipt


def _attach_answer_explanations(normalized, receipt, packet):
    if receipt is None:
        return None
    items = receipt.get("items") if isinstance(receipt, dict) else None
    if not isinstance(items, list):
        raise AuthoringError("answer explanation receipt items are invalid")
    by_id = {}
    for row in items:
        if not isinstance(row, dict) or row.get("item_id") in by_id:
            raise AuthoringError("answer explanation receipt has malformed/duplicate items")
        by_id[row.get("item_id")] = row
    if set(by_id) != set(packet["item_ids"]):
        raise AuthoringError(
            "answer explanation receipt must exactly cover packet items; missing=%s extra=%s"
            % (sorted(set(packet["item_ids"]) - set(by_id)),
               sorted(set(by_id) - set(packet["item_ids"])))
        )
    for item_id in packet["item_ids"]:
        row = by_id[item_id]
        walk = normalized["walkthroughs_by_id"][item_id]
        walk["answer_explanation"] = copy.deepcopy(row["answer_explanation"])
        walk["answer_explanation_provenance"] = copy.deepcopy(
            row["answer_explanation_provenance"])
        walk["answer_explanation_receipt"] = {
            key: copy.deepcopy(row[key])
            for key in (
                "request_id", "request_sha256", "response_sha256",
                "provider_receipt", "provider_receipt_sha256",
                "response_event_sha256",
            )
        }
    return {
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": _sha256_json(receipt),
        "contract_id": receipt["contract_id"],
        "prompt_id": receipt["prompt_id"],
        "prompt_sha256": receipt["prompt_sha256"],
    }


def _display_text(value, language):
    if language == "bilingual":
        return "%s / %s" % (value.get("zh", ""), value.get("en", ""))
    return value.get(language, "")


def _notebook_copy(language, key, *values):
    if language == "bilingual":
        return "%s / %s" % (
            NOTEBOOK_COPY["zh"][key] % values,
            NOTEBOOK_COPY["en"][key] % values,
        )
    return NOTEBOOK_COPY[language][key] % values


def _localized_line_label(language, code, key, *values):
    label = NOTEBOOK_COPY[code][key] % values
    if language == "bilingual":
        return "%s（中文）" % label if code == "zh" else "%s (English)" % label
    return label


def _provenance_emoji(value, code):
    if isinstance(value, dict):
        value = value.get(code)
    if isinstance(value, (list, tuple)):
        markers = []
        for item in value:
            marker = PROVENANCE_EMOJI.get(item, "")
            if marker and (not markers or markers[-1] != marker):
                markers.append(marker)
        return "".join(markers)
    return PROVENANCE_EMOJI.get(value, "")


_PROVENANCE_UNSET = object()


def _clean_notebook_visible(value, code, sidecar=None):
    try:
        return clean_visible_provenance(value, code, sidecar)
    except ProvenanceConflictError as exc:
        raise AuthoringError(str(exc)) from exc


def _visible_with_terminal_marker(value, display_provenance, code,
                                  source_provenance=_PROVENANCE_UNSET):
    has_source_provenance = (
        source_provenance is not _PROVENANCE_UNSET
        and source_provenance is not None)
    authoritative = (
        display_provenance if not has_source_provenance
        else source_provenance
    )
    cleaned, inferred_or_explicit = _clean_notebook_visible(
        value, code, authoritative)
    display = (
        inferred_or_explicit
        if not has_source_provenance
        and display_provenance is None
        else display_provenance
    )
    marker = _provenance_emoji(display, code)
    return "%s%s" % (cleaned, (" " + marker) if marker else "")


def _run_terminal_marker(values, index, code):
    current = _provenance_emoji(values[index], code)
    following = (
        _provenance_emoji(values[index + 1], code)
        if index + 1 < len(values) else ""
    )
    return current if current and current != following else ""


def _run_terminal_provenance(values, index):
    terminal = {}
    for code in ("zh", "en"):
        if _run_terminal_marker(values, index, code):
            value = values[index]
            terminal[code] = value.get(code) if isinstance(value, dict) else value
    return terminal


def _notebook_provenance_legend(language):
    return notebook_legend_lines(language)


def _append_answer_with_provenance(lines, answer, provenance, language,
                                   emit_marker=True,
                                   source_provenance=_PROVENANCE_UNSET):
    if language == "bilingual":
        _append_bilingual_block(
            lines,
            "- **%s：** %s" % (
                NOTEBOOK_COPY["zh"]["answer"],
                _visible_with_terminal_marker(
                    answer["zh"], provenance if emit_marker else None, "zh",
                    source_provenance),
            ),
            "**%s:** %s" % (
                NOTEBOOK_COPY["en"]["answer"],
                _visible_with_terminal_marker(
                    answer["en"], provenance if emit_marker else None, "en",
                    source_provenance),
            ),
        )
        return
    if language in ("zh", "bilingual") and "zh" in answer:
        lines.append("- %s：%s" % (
            _localized_line_label(language, "zh", "answer"),
            _visible_with_terminal_marker(
                answer["zh"], provenance if emit_marker else None, "zh",
                source_provenance),
        ))
    if language in ("en", "bilingual") and "en" in answer:
        lines.append("- %s: %s" % (
            _localized_line_label(language, "en", "answer"),
            _visible_with_terminal_marker(
                answer["en"], provenance if emit_marker else None, "en",
                source_provenance),
        ))


def _append_field_with_provenance(lines, label_key, value, provenance, language,
                                  *label_values, **options):
    emit_marker = options.pop("emit_marker", True)
    source_provenance = options.pop("source_provenance", _PROVENANCE_UNSET)
    if options:
        raise TypeError("unexpected provenance rendering option: %s" % sorted(options)[0])
    if language == "bilingual":
        chinese, english = _bilingual_field_lines(
            label_key, value, provenance, *label_values,
            emit_marker=emit_marker,
            source_provenance=source_provenance,
        )
        _append_bilingual_block(lines, chinese, english)
        return
    if language in ("zh", "bilingual") and "zh" in value:
        lines.append("- %s：%s" % (
            _localized_line_label(
                language, "zh", label_key, *label_values
            ), _visible_with_terminal_marker(
                value["zh"], provenance if emit_marker else None, "zh",
                source_provenance),
        ))
    if language in ("en", "bilingual") and "en" in value:
        lines.append("- %s: %s" % (
            _localized_line_label(
                language, "en", label_key, *label_values
            ), _visible_with_terminal_marker(
                value["en"], provenance if emit_marker else None, "en",
                source_provenance),
        ))


def _append_substitution(lines, formula_use, language, emit_marker=True,
                         display_provenance=None):
    provenance = (
        formula_use["substitution_provenance"]
        if display_provenance is None else display_provenance
    )
    cleaned_substitutions = []
    for code in TARGET_LANGUAGES[language]:
        cleaned, unused_resolved = _clean_notebook_visible(
            formula_use["substitution"], code,
            formula_use["substitution_provenance"])
        cleaned_substitutions.append(cleaned)
    if len(set(cleaned_substitutions)) != 1:
        raise AuthoringError(
            "substitution provenance prefixes resolve differently by language")
    substitution = cleaned_substitutions[0]
    if language == "bilingual":
        marker_zh = _provenance_emoji(
            provenance, "zh") if emit_marker else ""
        marker_en = _provenance_emoji(
            provenance, "en") if emit_marker else ""
        _append_bilingual_block(
            lines,
            "- **%s：** $%s$%s" % (
                NOTEBOOK_COPY["zh"]["substitution"], substitution,
                (" " + marker_zh) if marker_zh else "",
            ),
            "**%s:** $%s$%s" % (
                NOTEBOOK_COPY["en"]["substitution"], substitution,
                (" " + marker_en) if marker_en else "",
            ),
        )
        return
    for code in ("zh", "en"):
        if code not in TARGET_LANGUAGES[language]:
            continue
        marker = _provenance_emoji(
            provenance, code) if emit_marker else ""
        punctuation = "：" if code == "zh" else ":"
        lines.append("- %s%s $%s$%s" % (
            _localized_line_label(language, code, "substitution"),
            punctuation, substitution,
            (" " + marker) if marker else "",
        ))


def _finalize_notebook_lines(lines, language):
    del language
    return "\n".join(lines).rstrip() + "\n"


def _quoted_english_lines(value):
    """Return one English Markdown block with the required ``> EN:`` lead-in."""

    rows = value if isinstance(value, (list, tuple)) else [value]
    return [
        ("> EN: %s" if index == 0 else "> %s") % row
        for index, row in enumerate(rows)
    ]


def _append_bilingual_block(lines, chinese, english):
    """Append one Chinese block immediately followed by its pure-English mirror."""

    zh_rows = chinese if isinstance(chinese, (list, tuple)) else [chinese]
    en_rows = english if isinstance(english, (list, tuple)) else [english]
    lines.extend(zh_rows)
    lines.extend(_quoted_english_lines(en_rows))


def _bilingual_field_lines(label_key, value, provenance, *label_values,
                           **options):
    emit_marker = options.pop("emit_marker", True)
    source_provenance = options.pop("source_provenance", _PROVENANCE_UNSET)
    if options:
        raise TypeError("unexpected provenance rendering option: %s" % sorted(options)[0])
    return (
        "- **%s：** %s" % (
            NOTEBOOK_COPY["zh"][label_key] % label_values,
            _visible_with_terminal_marker(
                value["zh"], provenance if emit_marker else None, "zh",
                source_provenance),
        ),
        "**%s:** %s" % (
            NOTEBOOK_COPY["en"][label_key] % label_values,
            _visible_with_terminal_marker(
                value["en"], provenance if emit_marker else None, "en",
                source_provenance),
        ),
    )


def _append_notebook_heading(lines, language, key):
    if language == "bilingual":
        _append_bilingual_block(
            lines, "### %s" % NOTEBOOK_COPY["zh"][key],
            "**%s**" % NOTEBOOK_COPY["en"][key],
        )
    else:
        lines.append("### %s" % NOTEBOOK_COPY[language][key])


def _source_trace_binding(source_revisions, source_ref):
    """Return the bound source row, anchor, and absolute Markdown target."""
    source_file = source_ref.get("source_file")
    if not isinstance(source_file, str) or not source_file:
        raise AuthoringError("source trace lacks a source_file")
    rows = source_revisions.get("sources")
    source_root = source_revisions.get("source_root")
    if not isinstance(rows, list) or not isinstance(source_root, str) or not source_root:
        raise AuthoringError("packet source revisions are malformed")
    matches = [row for row in rows if isinstance(row, dict) and row.get("path") == source_file]
    if len(matches) != 1:
        raise AuthoringError(
            "source trace path is not uniquely bound by packet source revisions: %s"
            % source_file
        )
    try:
        canonical = normalize_workspace_path(source_file)
        absolute = os.path.abspath(str(safe_workspace_entry(source_root, canonical)))
    except (UnsafePathError, ValueError) as exc:
        raise AuthoringError("source trace path is unsafe: %s" % exc)
    anchor = source_ref.get("pages", [None])[0]
    if isinstance(anchor, bool) or not isinstance(anchor, int) or anchor < 1:
        raise AuthoringError("source trace location anchor must be an integer >= 1")
    # Codex/Markdown accepts absolute filesystem targets directly.  Do not use
    # file://, and percent-encode spaces and punctuation without encoding the
    # drive separator or directory separators.
    href = url_quote(absolute.replace("\\", "/"), safe="/:")
    if matches[0].get("media_type") == "application/pdf":
        href = "%s#page=%d" % (href, anchor)
    return matches[0], anchor, href


def _source_trace_href(source_revisions, source_ref):
    """Return a packet-bound absolute Markdown link to a material source."""

    unused_source, unused_anchor, href = _source_trace_binding(
        source_revisions, source_ref
    )
    return href


def _source_trace_location(source_revisions, source_ref, language):
    source, anchor, unused_href = _source_trace_binding(
        source_revisions, source_ref
    )
    media_type = source.get("media_type")
    if media_type == "application/pdf":
        labels = ("PDF 第 %d 页", "PDF page %d")
    elif media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        labels = ("PPTX 第 %d 张幻灯片", "PPTX slide %d")
    elif media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        labels = ("XLSX 第 %d 个工作表", "XLSX worksheet %d")
    elif media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        labels = ("DOCX 逻辑段 %d", "DOCX logical segment %d")
    else:
        labels = ("位置 %d", "location %d")
    if language == "zh":
        return labels[0] % anchor
    if language == "en":
        return labels[1] % anchor
    return "%s / %s" % (labels[0] % anchor, labels[1] % anchor)


def _notebook_body(packet_item, walkthrough, formula_by_id, language,
                   source_revisions, include_provenance_legend=False):
    """Render one provenance-explicit seven-step walkthrough notebook body."""

    lines = []
    if include_provenance_legend:
        lines.extend(_notebook_provenance_legend(language))
        lines.append("")
    if language == "bilingual":
        lines.extend(("> EN: **%s**" % walkthrough["title"]["en"], ""))
    _append_notebook_heading(lines, language, "question_figure")
    if packet_item["prompt_assets"]:
        for asset in packet_item["prompt_assets"]:
            target = url_quote(asset["path"], safe="/:")
            if language == "bilingual":
                _append_bilingual_block(
                    lines,
                    "- **%s：** ![%s](../%s)" % (
                        NOTEBOOK_COPY["zh"]["prompt_figure"],
                        NOTEBOOK_COPY["zh"]["prompt_figure"], target,
                    ),
                    "**%s:** shown above."
                    % NOTEBOOK_COPY["en"]["prompt_figure"].capitalize(),
                )
            else:
                lines.append("- ![%s](../%s)" % (
                    NOTEBOOK_COPY[language]["prompt_figure"], target,
                ))
    else:
        if language == "bilingual":
            _append_bilingual_block(
                lines,
                "- %s" % NOTEBOOK_COPY["zh"]["no_prompt_figure"],
                NOTEBOOK_COPY["en"]["no_prompt_figure"],
            )
        else:
            lines.append("- %s" % NOTEBOOK_COPY[language]["no_prompt_figure"])
    selected = packet_item.get("selected_prompt")
    if language == "bilingual":
        prompt = {}
        prompt_provenance = {}
        original_language = packet_item.get("original_language")
        if selected is not None and original_language in ("zh", "en"):
            prompt[original_language] = selected["value"]
            prompt_provenance[original_language] = "material"
        for code, value in walkthrough["translation"].items():
            prompt[code] = value
            prompt_provenance[code] = walkthrough["translation_provenance"][code]
        lines.append("")
        if set(prompt) == {"zh", "en"}:
            chinese, english = _bilingual_field_lines(
                "original_prompt", prompt, prompt_provenance,
                source_provenance=prompt_provenance,
            )
            _append_bilingual_block(lines, chinese, english)
        elif packet_item.get("prompt_asset_mode") == "full_prompt" and prompt:
            # A full-prompt image already shows the source-language wording.
            # In bilingual mode keep only the missing-language translation, but
            # still mirror the block so the notebook's bilingual layout remains
            # structurally complete.  The short counterpart points to the image
            # instead of duplicating OCR/original prompt text.
            if "zh" in prompt:
                _append_bilingual_block(
                    lines,
                    "- **%s：** %s" % (
                        NOTEBOOK_COPY["zh"]["translation"],
                        _visible_with_terminal_marker(
                            prompt["zh"], prompt_provenance, "zh"),
                    ),
                    "**%s:** shown in the prompt figure above."
                    % NOTEBOOK_COPY["en"]["original_prompt"],
                )
            elif "en" in prompt:
                _append_bilingual_block(
                    lines,
                    "- **%s：** 见上方题面图。"
                    % NOTEBOOK_COPY["zh"]["original_prompt"],
                    "**%s:** %s" % (
                        NOTEBOOK_COPY["en"]["translation"],
                        _visible_with_terminal_marker(
                            prompt["en"], prompt_provenance, "en"),
                    ),
                )
        elif selected is not None:
            _append_bilingual_block(
                lines,
                "- **%s：** %s" % (
                    NOTEBOOK_COPY["zh"]["original_prompt"],
                    _visible_with_terminal_marker(
                        selected["value"], "material", "zh")),
                "**%s:** %s" % (
                    NOTEBOOK_COPY["en"]["original_prompt"],
                    _visible_with_terminal_marker(
                        selected["value"], "material", "en")),
            )
    elif selected is not None:
        punctuation = "：" if language == "zh" else ":"
        lines.extend(("", "%s%s %s" % (
            NOTEBOOK_COPY[language]["original_prompt"], punctuation,
            _visible_with_terminal_marker(
                selected["value"], "material", language),
        )))
    for code, value in (
        walkthrough["translation"].items() if language != "bilingual" else ()
    ):
        if code != language:
            continue
        punctuation = "：" if code == "zh" else ":"
        lines.append("%s%s %s%s" % (
            _localized_line_label(language, code, "translation"),
            punctuation, _visible_with_terminal_marker(
                value, walkthrough["translation_provenance"], code), "",
        ))

    lines.append("")
    _append_notebook_heading(lines, language, "what_asked_heading")
    question_provenance = [walkthrough["what_asked_provenance"]]
    question_provenance.extend(
        walkthrough["knowledge_point_uses_provenance"][kp_id]
        for kp_id in walkthrough["knowledge_point_ids"])
    _append_field_with_provenance(
        lines, "question", walkthrough["what_asked"],
        _run_terminal_provenance(question_provenance, 0), language,
        source_provenance=walkthrough["what_asked_provenance"],
    )
    for kp_index, kp_id in enumerate(walkthrough["knowledge_point_ids"]):
        _append_field_with_provenance(
            lines, "knowledge_point_use",
            walkthrough["knowledge_point_uses"][kp_id],
            _run_terminal_provenance(question_provenance, kp_index + 1), language,
            kp_id,
            source_provenance=walkthrough[
                "knowledge_point_uses_provenance"][kp_id],
        )

    lines.append("")
    _append_notebook_heading(lines, language, "quantities_heading")
    for heading_key, values in (
        ("known", walkthrough["known_quantities"]),
        ("unknown", walkthrough["unknown_quantities"]),
    ):
        if language == "bilingual":
            _append_bilingual_block(
                lines, "- **%s**" % NOTEBOOK_COPY["zh"][heading_key],
                "**%s**" % NOTEBOOK_COPY["en"][heading_key],
            )
        else:
            lines.append("- %s" % NOTEBOOK_COPY[language][heading_key])
        if not values:
            if language == "bilingual":
                _append_bilingual_block(
                    lines, "  - %s" % NOTEBOOK_COPY["zh"]["none"],
                    NOTEBOOK_COPY["en"]["none"],
                )
            else:
                lines.append("  - %s" % NOTEBOOK_COPY[language]["none"])
        quantity_provenance = [row["provenance"] for row in values]
        for quantity_index, quantity in enumerate(values):
            extras = [
                quantity[key] for key in ("symbol", "value", "unit")
                if quantity.get(key)
            ]
            suffix = (" — " + ", ".join(extras)) if extras else ""
            terminal = _run_terminal_provenance(
                quantity_provenance, quantity_index)
            if language == "bilingual":
                _append_bilingual_block(
                    lines,
                    "  - %s" % (
                        _visible_with_terminal_marker(
                            quantity["label"]["zh"] + suffix, terminal, "zh",
                            quantity["provenance"]),),
                    "%s" % (
                        _visible_with_terminal_marker(
                            quantity["label"]["en"] + suffix, terminal, "en",
                            quantity["provenance"]),),
                )
            else:
                lines.append("  - %s" % (
                    _visible_with_terminal_marker(
                        quantity["label"][language] + suffix, terminal, language,
                        quantity["provenance"]),
                ))

    lines.append("")
    _append_notebook_heading(lines, language, "formula_heading")
    if walkthrough["formula_uses"]:
        for formula_use in walkthrough["formula_uses"]:
            formula = formula_by_id[formula_use["formula_id"]]
            lines.extend(("", "$$%s$$" % formula["latex"], ""))
            formula_provenance = [
                formula["explanation_provenance"],
                formula["applicability_provenance"],
            ]
            formula_provenance.extend(
                row["meaning_provenance"] for row in formula["variables"])
            formula_provenance.append(formula_use["why_applicable_provenance"])
            formula_provenance.extend(
                row["maps_to_provenance"]
                for row in formula_use["variable_mapping"])
            formula_provenance.append(formula_use["substitution_provenance"])
            provenance_index = 0
            _append_field_with_provenance(
                lines, "formula_meaning", formula["explanation"],
                _run_terminal_provenance(
                    formula_provenance, provenance_index), language,
                source_provenance=formula["explanation_provenance"],
            )
            provenance_index += 1
            _append_field_with_provenance(
                lines, "applicability", formula["applicability"],
                _run_terminal_provenance(
                    formula_provenance, provenance_index), language,
                source_provenance=formula["applicability_provenance"],
            )
            provenance_index += 1
            for variable in formula["variables"]:
                _append_field_with_provenance(
                    lines, "variable",
                    variable["meaning"],
                    _run_terminal_provenance(
                        formula_provenance, provenance_index),
                    language,
                    variable["symbol"],
                    source_provenance=variable["meaning_provenance"],
                )
                provenance_index += 1
            _append_field_with_provenance(
                lines, "why_applicable", formula_use["why_applicable"],
                _run_terminal_provenance(
                    formula_provenance, provenance_index), language,
                source_provenance=formula_use["why_applicable_provenance"],
            )
            provenance_index += 1
            for mapping in formula_use["variable_mapping"]:
                _append_field_with_provenance(
                    lines, "variable_mapping",
                    mapping["maps_to"],
                    _run_terminal_provenance(
                        formula_provenance, provenance_index),
                    language,
                    mapping["symbol"],
                    source_provenance=mapping["maps_to_provenance"],
                )
                provenance_index += 1
            _append_substitution(
                lines, formula_use, language,
                display_provenance=_run_terminal_provenance(
                    formula_provenance, provenance_index),
            )
    else:
        _append_field_with_provenance(
            lines, "no_formula", walkthrough["no_formula_reason"],
            walkthrough["no_formula_reason_provenance"], language,
        )

    lines.append("")
    _append_notebook_heading(lines, language, "steps_heading")
    steps_provenance = walkthrough["steps_provenance"]
    for index, step in enumerate(walkthrough["steps"]):
        provenance = _run_terminal_provenance(steps_provenance, index)
        if language == "bilingual":
            _append_bilingual_block(
                lines,
                "%d. %s" % (
                    index + 1, _visible_with_terminal_marker(
                        step["zh"], provenance, "zh",
                        steps_provenance[index])),
                "**Step %d:** %s" % (
                    index + 1, _visible_with_terminal_marker(
                        step["en"], provenance, "en",
                        steps_provenance[index])),
            )
        elif language == "zh" and "zh" in step:
            lines.append("%d." % (index + 1))
            lines.append("   - %s" % _visible_with_terminal_marker(
                step["zh"], provenance, "zh", steps_provenance[index]))
        elif language == "en" and "en" in step:
            lines.append("%d." % (index + 1))
            lines.append("   - %s" % _visible_with_terminal_marker(
                step["en"], provenance, "en", steps_provenance[index]))

    lines.append("")
    _append_notebook_heading(lines, language, "answer_heading")
    display_answer = walkthrough.get("teaching_answer", walkthrough["answer"])
    display_answer_provenance = walkthrough.get(
        "teaching_answer_provenance", walkthrough["answer_provenance"])
    _append_answer_with_provenance(
        lines, display_answer, display_answer_provenance, language
    )
    for asset in packet_item["answer_assets"]:
        target = url_quote(asset["path"], safe="/:")
        if language == "bilingual":
            _append_bilingual_block(
                lines,
                "- **%s：** ![%s](../%s)" % (
                    NOTEBOOK_COPY["zh"]["answer_figure"],
                    NOTEBOOK_COPY["zh"]["answer_figure"], target,
                ),
                "**%s:** shown above."
                % NOTEBOOK_COPY["en"]["answer_figure"].capitalize(),
            )
        else:
            lines.append("- ![%s](../%s)" % (
                NOTEBOOK_COPY[language]["answer_figure"], target,
            ))

    if "answer_explanation" in walkthrough:
        lines.append("")
        _append_field_with_provenance(
            lines, "answer_explanation", walkthrough["answer_explanation"],
            walkthrough["answer_explanation_provenance"], language,
        )

    lines.append("")
    _append_notebook_heading(lines, language, "source_heading")
    refs = [row["source_ref"] for row in packet_item["question_evidence"]]
    refs.extend(row["source_ref"] for row in packet_item["answer_evidence"])
    seen = set()
    for ref in refs:
        key = (ref["source_unit_id"], ref["role"])
        if key in seen:
            continue
        seen.add(key)
        href = _source_trace_href(source_revisions, ref)
        if language == "bilingual":
            _append_bilingual_block(
                lines,
                "- [%s · %s](%s) — `%s`（%s）" % (
                    ref["source_file"],
                    _source_trace_location(source_revisions, ref, "zh"), href,
                    ref["source_unit_id"], ref["role"],
                ),
                "[%s · %s](%s) — `%s` (%s)" % (
                    ref["source_file"],
                    _source_trace_location(source_revisions, ref, "en"), href,
                    ref["source_unit_id"], ref["role"],
                ),
            )
        else:
            lines.append("- [%s · %s](%s) — `%s` (%s)" % (
                ref["source_file"],
                _source_trace_location(source_revisions, ref, language), href,
                ref["source_unit_id"], ref["role"],
            ))
    return _finalize_notebook_lines(lines, language)


def _official_item_notebook_bindings(workspace, chapter, item_ids):
    try:
        by_anchor = guide_content._official_walkthrough_entries(workspace, chapter)
    except guide_content.ContentError as exc:
        raise AuthoringError("cannot read official notebook anchors: %s" % exc)
    by_item = {}
    for anchor, entry in by_anchor.items():
        item_id = entry["item_id"]
        if item_id in item_ids:
            if item_id in by_item:
                raise AuthoringError("notebook has multiple walkthrough anchors for item %s" % item_id)
            by_item[item_id] = {
                "anchor": anchor,
                "notebook_block_sha256": entry["notebook_block_sha256"],
            }
    missing = sorted(set(item_ids) - set(by_item))
    if missing:
        raise AuthoringError("notebook lacks official walkthrough anchors for %s" % missing)
    return {item_id: by_item[item_id] for item_id in item_ids}


def _official_item_anchors(workspace, chapter, item_ids):
    return {
        item_id: row["anchor"]
        for item_id, row in _official_item_notebook_bindings(
            workspace, chapter, item_ids
        ).items()
    }


def _capture_notebook_files(workspace, chapter):
    paths = (
        os.path.join(workspace, "notebook", "ch%02d.md" % chapter),
        os.path.join(workspace, "notebook", "index.md"),
    )
    snapshot = {}
    for path in paths:
        if not os.path.exists(path):
            snapshot[path] = None
            continue
        if is_link_or_reparse(path) or not os.path.isfile(path):
            raise AuthoringError("notebook rollback target is not a regular non-link file: %s" % path)
        with open(path, "rb") as stream:
            payload = stream.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise AuthoringError("notebook file exceeds the bounded rollback limit: %s" % path)
        snapshot[path] = payload
    return snapshot


def _notebook_legend_exists_outside_replaced_walkthroughs(
        chapter_payload, replaced_item_ids):
    """Ignore legends in walkthrough entries this authoring pass will replace."""

    if chapter_payload is None:
        return False
    try:
        text = chapter_payload.decode("utf-8")
        pre, blocks = notebook_engine.parse_chapter(text)
        type_map = notebook_engine._label_to_type()
        retained = list(pre)
        for block in blocks:
            entry_type = type_map.get(
                notebook_engine._block_meta(block["lines"])[0])
            if block["id"] in replaced_item_ids and entry_type == "walkthrough":
                continue
            retained.extend(block["lines"])
    except (UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AuthoringError(
            "cannot inspect the existing notebook provenance legend: %s" % exc
        )
    return notebook_has_provenance_legend("\n".join(retained))


def _restore_notebook_files(workspace, snapshot):
    return _restore_notebook_files_locked(workspace, snapshot, lock_held=False)


def _restore_notebook_files_locked(workspace, snapshot, lock_held):
    try:
        lock = contextlib.nullcontext() if lock_held else workspace_publication_lock(workspace)
        with lock:
            for path in sorted(snapshot):
                _restore_published_file(path, snapshot[path])
    except (OSError, ValueError, TypeError) as exc:
        raise AuthoringError("notebook rollback failed: %s" % exc)


def _official_notebook_add_entry(workspace, chapter, item_id, title, language, body):
    """Invoke notebook.py's official add-entry implementation under the caller's lock."""

    args = argparse.Namespace(
        chapter=chapter,
        id=item_id,
        title=title,
        type="walkthrough",
        mistake=False,
        lang=language,
        teaching_example=False,
    )
    # cmd_add_entry reads process-global stdin and emits through process-global
    # stdout/stderr.  The workspace lock prevents file races but does not protect
    # concurrent library calls for another workspace, so serialize this narrow
    # adapter boundary as well.
    with _NOTEBOOK_ENGINE_IO_LOCK:
        previous_stdin = sys.stdin
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            sys.stdin = io.StringIO(body)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = notebook_engine.cmd_add_entry(workspace, args)
        except SystemExit as exc:
            detail = (stderr.getvalue() or stdout.getvalue() or str(exc)).strip()
            raise AuthoringError(
                "notebook.py failed for item %s (exit %s): %s"
                % (item_id, exc.code, detail[:1000])
            )
        except (OSError, ValueError, TypeError) as exc:
            raise AuthoringError(
                "notebook.py failed for item %s: %s" % (item_id, exc))
        finally:
            sys.stdin = previous_stdin
    if status not in (None, 0):
        detail = (stderr.getvalue() or stdout.getvalue() or "unknown notebook.py error").strip()
        raise AuthoringError(
            "notebook.py failed for item %s (exit %s): %s"
            % (item_id, status, detail[:1000])
        )


def persist_notebooks(workspace, chapter, packet_path, annotations_path,
                      binding_output=None, explanation_receipt_path=None):
    workspace = _workspace(workspace)
    exam_start.require_full_processing(
        workspace, purpose="Study Guide notebook publication")
    require_current_ingestion_v2(
        workspace, purpose="Study Guide notebook publication")
    packet = _load_packet(workspace, packet_path, chapter, require_ready=True)
    annotations, normalized = _load_annotations(workspace, annotations_path, packet)
    answer_explanation_mode = normalized["answer_explanation_mode"]
    explanation_binding = None
    if answer_explanation_mode == "isolated":
        explanation_receipt = _load_answer_explanation_receipt(
            workspace, chapter, packet_path, annotations_path,
            explanation_receipt_path,
        )
        explanation_binding = _attach_answer_explanations(
            normalized, explanation_receipt, packet)
    elif explanation_receipt_path is not None:
        raise AuthoringError(
            "ordinary answer_explanation_mode forbids an isolated receipt path"
        )
    packet_items = {row["item_id"]: row for row in packet["items"]}
    formulas = {}
    groups = {row["formula_group_id"]: row for row in packet["formula_groups"]}
    for formula_id, annotation in normalized["formulas_by_id"].items():
        formulas[formula_id] = dict(annotation)
        formulas[formula_id]["latex"] = groups[formula_id]["latex"]

    binding_target = None
    binding_relative = _reserved_output_relative(chapter, "bindings")
    if binding_output is not None:
        binding_target = _reserved_output_target(
            workspace, binding_output, "notebook bindings output", binding_relative
        )

    expected_assets = _packet_asset_revisions(packet)
    try:
        with workspace_publication_lock(workspace):
            exam_start.require_full_processing(
                workspace, purpose="Study Guide notebook publication"
            )
            require_current_ingestion_v2(
                workspace, purpose="Study Guide notebook publication")
            before, before_hash = _snapshot(workspace)
            if before_hash != packet["source_snapshot_sha256"]:
                raise AuthoringError("source facts drifted before notebook persistence")
            source_revisions, source_hash = _source_revision_snapshot(workspace)
            if (
                source_hash != packet["source_revisions_sha256"]
                or source_revisions != packet["source_revisions"]
            ):
                raise AuthoringError("source bytes drifted before notebook persistence")
            _verify_asset_revisions(workspace, expected_assets)
            notebook_before = _capture_notebook_files(workspace, chapter)
            chapter_path = os.path.join(
                workspace, "notebook", "ch%02d.md" % chapter)
            preserved_taught_ids = set()
            try:
                state = _strict_json(
                    os.path.join(workspace, "study_state.json"),
                    "study_state.json",
                )
                teaching = _strict_json(
                    os.path.join(
                        workspace, "references", "teaching_examples.json"),
                    "references/teaching_examples.json",
                )
                baseline_problems = (
                    exam_start.update_progress._step_roster_baseline_problems(
                        workspace, chapter, teaching))
                binding_status = (
                    exam_start.update_progress._step_teaching_binding_status(
                        workspace, state, chapter, teaching))
                problems = baseline_problems + binding_status["problems"]
                if problems:
                    raise AuthoringError(
                        "teaching evidence is stale before Guide notebook "
                        "persistence: %s" % "; ".join(problems))
                # A cadence change does not weaken already-bound evidence.
                # Valid taught blocks remain immutable; unbound batch evidence
                # has no marker and may be replaced by the Guide normally.
                preserved_taught_ids = set(
                    binding_status["valid_bound_id_set"])
            except (OSError, TypeError, ValueError, KeyError) as exc:
                raise AuthoringError(
                    "cannot validate step-by-step teaching evidence before Guide "
                    "notebook persistence: %s" % exc)

            existing_payload = notebook_before.get(chapter_path)
            if existing_payload is not None:
                try:
                    _pre, existing_blocks = notebook_engine.parse_chapter(
                        existing_payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise AuthoringError(
                        "cannot inspect existing notebook teaching markers: %s" % exc)
                for block in existing_blocks:
                    if block.get("id") not in set(packet["item_ids"]):
                        continue
                    marker_lines = [
                        line for line in (block.get("lines") or [])
                        if notebook_engine._TEACHING_EXAMPLE_MARKER_RE.match(line)
                    ]
                    if marker_lines and block.get("id") not in preserved_taught_ids:
                        raise AuthoringError(
                            "notebook item %s has an unbound/stale teaching marker; "
                            "record or repair that taught example before Guide persistence"
                            % block.get("id"))
            replaced_item_ids = set(packet["item_ids"]) - preserved_taught_ids
            first_replaced_item_id = next(
                (item_id for item_id in packet["item_ids"]
                 if item_id in replaced_item_ids),
                None,
            )
            legend_elsewhere = (
                _notebook_legend_exists_outside_replaced_walkthroughs(
                    notebook_before.get(chapter_path), replaced_item_ids
                )
            )
            try:
                for item_id in packet["item_ids"]:
                    current, current_hash = _snapshot(workspace)
                    if current_hash != before_hash or current != before:
                        raise AuthoringError("source facts drifted during notebook persistence")
                    if item_id in preserved_taught_ids:
                        continue
                    walkthrough = normalized["walkthroughs_by_id"][item_id]
                    title = _display_text(
                        walkthrough["title"], normalized["language"])
                    body = _notebook_body(
                        packet_items[item_id], walkthrough, formulas, normalized["language"],
                        packet["source_revisions"],
                        include_provenance_legend=(
                            item_id == first_replaced_item_id and not legend_elsewhere),
                    )
                    _official_notebook_add_entry(
                        workspace, chapter, item_id, title, normalized["language"], body
                    )
                after, after_hash = _snapshot(workspace)
                if after_hash != before_hash or after != before:
                    raise AuthoringError("source facts drifted while notebook entries were written")
                after_sources, after_source_hash = _source_revision_snapshot(workspace)
                if (
                    after_source_hash != packet["source_revisions_sha256"]
                    or after_sources != packet["source_revisions"]
                ):
                    raise AuthoringError("source bytes drifted while notebook entries were written")
                _verify_asset_revisions(workspace, expected_assets)
                # Recheck every pre-existing step binding after all Guide writes.
                # Markdown anchors are chapter-global: changing a different title
                # can alter a preserved block's duplicate-slug suffix even when the
                # preserved block bytes themselves were not edited.  A Guide must
                # not publish bindings after invalidating canonical teaching state.
                post_binding_status = (
                    exam_start.update_progress._step_teaching_binding_status(
                        workspace, state, chapter, teaching))
                if post_binding_status["problems"]:
                    raise AuthoringError(
                        "Guide notebook persistence invalidated teaching evidence: %s"
                        % "; ".join(post_binding_status["problems"]))
                if set(post_binding_status["valid_bound_id_set"]) != preserved_taught_ids:
                    raise AuthoringError(
                        "Guide notebook persistence changed the set of valid taught "
                        "example bindings"
                    )
                notebook_bindings = _official_item_notebook_bindings(
                    workspace, chapter, packet["item_ids"]
                )
                anchors = {
                    item_id: row["anchor"]
                    for item_id, row in notebook_bindings.items()
                }
                block_sha256 = {
                    item_id: row["notebook_block_sha256"]
                    for item_id, row in notebook_bindings.items()
                }
                binding = {
                    "schema_version": SCHEMA_VERSION,
                    "chapter": chapter,
                    "packet_sha256": packet["packet_sha256"],
                    "annotations_sha256": _annotation_hash(annotations),
                    "answer_explanation_mode": answer_explanation_mode,
                    "notebook_anchors": anchors,
                    "notebook_block_sha256": block_sha256,
                }
                if explanation_binding is not None:
                    binding["answer_explanation_receipt_id"] = explanation_binding[
                        "receipt_id"]
                    binding["answer_explanation_receipt_sha256"] = explanation_binding[
                        "receipt_sha256"]
                binding["bindings_sha256"] = _sha256_json(binding)
                if binding_target is not None:
                    _publish_json(
                        workspace,
                        [(
                            binding_target, binding, "notebook bindings output",
                            binding_relative,
                        )],
                        expected_snapshot_sha256=packet["source_snapshot_sha256"],
                        expected_source_revisions_sha256=packet["source_revisions_sha256"],
                        expected_asset_revisions=expected_assets,
                        lock_held=True,
                    )
            except Exception:
                _restore_notebook_files_locked(workspace, notebook_before, lock_held=True)
                raise
    except AuthoringError:
        raise
    except ConflictError as exc:
        raise AuthoringError("cannot acquire the notebook mutation lock: %s" % exc)
    return binding


def _load_bindings(workspace, path, packet, annotations,
                   explanation_binding=None):
    source = _safe_workspace_path(workspace, path, "notebook bindings")
    value = _strict_json(source, "notebook bindings")
    _shape(
        value,
        (
            "schema_version", "chapter", "packet_sha256", "annotations_sha256",
            "answer_explanation_mode",
            "notebook_anchors", "notebook_block_sha256", "bindings_sha256",
        ),
        (
            "answer_explanation_receipt_id",
            "answer_explanation_receipt_sha256",
        ),
        "notebook bindings",
    )
    supplied = value["bindings_sha256"]
    payload = dict(value)
    del payload["bindings_sha256"]
    if supplied != _sha256_json(payload):
        raise AuthoringError("notebook bindings hash is invalid")
    if (
        value["schema_version"] != SCHEMA_VERSION
        or value["chapter"] != packet["chapter"]
        or value["packet_sha256"] != packet["packet_sha256"]
        or value["annotations_sha256"] != _annotation_hash(annotations)
        or value["answer_explanation_mode"]
            != packet["answer_explanation_mode"]
    ):
        raise AuthoringError("notebook bindings are stale or belong to another input")
    has_receipt_id = "answer_explanation_receipt_id" in value
    has_receipt_hash = "answer_explanation_receipt_sha256" in value
    if has_receipt_id != has_receipt_hash:
        raise AuthoringError(
            "notebook bindings must carry both answer explanation receipt fields"
        )
    if explanation_binding is not None:
        if (
            value.get("answer_explanation_receipt_id")
            != explanation_binding["receipt_id"]
            or value.get("answer_explanation_receipt_sha256")
            != explanation_binding["receipt_sha256"]
        ):
            raise AuthoringError(
                "notebook bindings disagree with the current answer explanation receipt"
            )
    elif has_receipt_id:
        raise AuthoringError(
            "notebook bindings require the matching answer explanation receipt"
        )
    if value["answer_explanation_mode"] == "isolated" and explanation_binding is None:
        raise AuthoringError(
            "isolated notebook bindings require the current explanation receipt"
        )
    if value["answer_explanation_mode"] == "ordinary" and has_receipt_id:
        raise AuthoringError(
            "ordinary notebook bindings must not carry isolated receipt fields"
        )
    anchors = value["notebook_anchors"]
    if not isinstance(anchors, dict) or set(anchors) != set(packet["item_ids"]):
        raise AuthoringError("notebook bindings must exactly cover packet items")
    block_sha256 = value["notebook_block_sha256"]
    if (
        not isinstance(block_sha256, dict)
        or set(block_sha256) != set(packet["item_ids"])
        or any(
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in block_sha256.values()
        )
    ):
        raise AuthoringError(
            "notebook block hashes must exactly cover packet items with SHA-256 values"
        )
    live = _official_item_notebook_bindings(
        workspace, packet["chapter"], packet["item_ids"]
    )
    expected_live = {
        item_id: {
            "anchor": anchors[item_id],
            "notebook_block_sha256": block_sha256[item_id],
        }
        for item_id in packet["item_ids"]
    }
    if live != expected_live:
        raise AuthoringError(
            "notebook bindings disagree with the live official walkthrough blocks"
        )
    return value


def _payload_for_unit(packet, unit_id, preferred_field=None):
    for collection in (packet["semantic_units"],):
        for row in collection:
            if row["source_unit_id"] != unit_id:
                continue
            choices = row["payloads"]
            if preferred_field is not None:
                choices = [item for item in choices if item["payload_field"] == preferred_field]
            if choices:
                choices = sorted(choices, key=lambda item: item["payload_field"])
                return choices[0]
    for item in packet["items"]:
        for evidence_key in ("question_evidence", "answer_evidence"):
            for row in item[evidence_key]:
                if row["source_unit_id"] != unit_id:
                    continue
                choices = row["payloads"]
                if preferred_field is not None:
                    choices = [entry for entry in choices if entry["payload_field"] == preferred_field]
                if choices:
                    choices = sorted(choices, key=lambda entry: entry["payload_field"])
                    return choices[0]
    raise AuthoringError("packet unit %s lacks a claimable payload" % unit_id)


def _proposal(chapter, entity_type, entity_id, field, language, unit_id,
              payload_field, role, claim_text, quote_text, claim_index=0):
    return {
        "subject": {
            "chapter_id": "ch%02d" % chapter,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "field": field,
            "language": language,
            "claim_index": claim_index,
        },
        "source_unit_id": unit_id,
        "payload_field": payload_field,
        "role": role,
        "claim_text": claim_text,
        "quote": {"start": 0, "end": len(quote_text)},
    }


def compile_manifest(workspace, chapter, packet_path, annotations_path, bindings_path,
                     explanation_receipt_path=None):
    workspace = _workspace(workspace)
    require_current_ingestion_v2(
        workspace, purpose="Study Guide manifest compilation")
    exam_start.require_full_processing(
        workspace, purpose="Study Guide authoring compilation")
    packet = _load_packet(workspace, packet_path, chapter, require_ready=True)
    expected_assets = _packet_asset_revisions(packet)
    _verify_asset_revisions(workspace, expected_assets)
    annotations, normalized = _load_annotations(workspace, annotations_path, packet)
    answer_explanation_mode = normalized["answer_explanation_mode"]
    explanation_binding = None
    if answer_explanation_mode == "isolated":
        explanation_receipt = _load_answer_explanation_receipt(
            workspace, chapter, packet_path, annotations_path,
            explanation_receipt_path,
        )
        explanation_binding = _attach_answer_explanations(
            normalized, explanation_receipt, packet)
    elif explanation_receipt_path is not None:
        raise AuthoringError(
            "ordinary answer_explanation_mode forbids an isolated receipt path"
        )
    bindings = _load_bindings(
        workspace, bindings_path, packet, annotations,
        explanation_binding=explanation_binding,
    )
    semantic_by_id = {row["source_unit_id"]: row for row in packet["semantic_units"]}
    formula_groups = {row["formula_group_id"]: row for row in packet["formula_groups"]}
    item_by_id = {row["item_id"]: row for row in packet["items"]}

    proposals = []
    knowledge_points = []
    for kp in normalized["knowledge_points"]:
        concept_refs = []
        formulas = []
        for unit_id in kp["semantic_unit_ids"]:
            unit = semantic_by_id[unit_id]
            if unit["kind"] != "formula":
                concept_refs.append(copy.deepcopy(unit["source_ref"]))
        for formula_id in kp["formula_group_ids"]:
            group = formula_groups[formula_id]
            annotation = normalized["formulas_by_id"][formula_id]
            visible_variables = []
            for variable_index, variable in enumerate(annotation["variables"]):
                visible_variables.append({
                    "symbol": variable["symbol"],
                    "meaning": copy.deepcopy(variable["meaning"]),
                    "meaning_provenance": copy.deepcopy(
                        variable["meaning_provenance"]),
                })
            formulas.append({
                "id": formula_id,
                "latex": group["latex"],
                "explanation": copy.deepcopy(annotation["explanation"]),
                "explanation_provenance": copy.deepcopy(
                    annotation["explanation_provenance"]),
                "variables": visible_variables,
                "applicability": copy.deepcopy(annotation["applicability"]),
                "applicability_provenance": copy.deepcopy(
                    annotation["applicability_provenance"]),
                "source_refs": copy.deepcopy(group["source_refs"]),
            })
            claim_unit_id = sorted(group["source_unit_ids"])[0]
            payload = _payload_for_unit(packet, claim_unit_id, "latex")
            proposals.append(_proposal(
                chapter, "formula", formula_id, "latex", "source",
                claim_unit_id, "latex", "formula_evidence", group["latex"], payload["value"],
            ))
            for field in ("explanation", "applicability"):
                for code, binding in sorted(
                    annotation["material_bindings"][field].items()
                ):
                    proposals.append(_proposal(
                        chapter, "formula", formula_id, field, code,
                        binding["source_unit_id"], binding["payload_field"],
                        "formula_evidence", annotation[field][code],
                        binding["quote_text"],
                    ))
            for variable_index, variable in enumerate(annotation["variables"]):
                for code, binding in sorted(
                    annotation["material_bindings"]["variable_meaning"][str(variable_index)].items()
                ):
                    proposals.append(_proposal(
                        chapter, "formula", formula_id, "variable_meaning", code,
                        binding["source_unit_id"], binding["payload_field"],
                        "formula_evidence", variable["meaning"][code],
                        binding["quote_text"], claim_index=variable_index,
                    ))
        for code, unit_id in sorted(kp["material_source_units"].items()):
            payload = _payload_for_unit(packet, unit_id, "text")
            proposals.append(_proposal(
                chapter, "knowledge_point", kp["id"], "explanation", code,
                unit_id, payload["payload_field"], "concept_evidence",
                kp["explanation"][code], payload["value"],
            ))
        compiled_kp = {
            "id": kp["id"],
            "title": copy.deepcopy(kp["title"]),
            "explanation": copy.deepcopy(kp["explanation"]),
            "explanation_provenance": copy.deepcopy(kp["explanation_provenance"]),
            "formulas": formulas,
            "source_refs": sorted(concept_refs, key=lambda ref: ref["source_unit_id"]),
            "example_ids": list(kp["example_ids"]),
            "source_unit_ids": list(kp["semantic_unit_ids"]),
        }
        if "teaching_explanation" in kp:
            compiled_kp["teaching_explanation"] = copy.deepcopy(
                kp["teaching_explanation"])
            compiled_kp["teaching_explanation_provenance"] = copy.deepcopy(
                kp["teaching_explanation_provenance"])
        if not kp["example_ids"]:
            compiled_kp["example_note"] = {
                code: NOTEBOOK_COPY[code]["no_examples"]
                for code in sorted(TARGET_LANGUAGES[normalized["language"]])
            }
        knowledge_points.append(compiled_kp)

    walkthroughs = []
    for item_id in packet["item_ids"]:
        item = item_by_id[item_id]
        authored = normalized["walkthroughs_by_id"][item_id]
        trace = [copy.deepcopy(row["source_ref"]) for row in item["question_evidence"]]
        trace.extend(copy.deepcopy(row["source_ref"]) for row in item["answer_evidence"])
        visible_formula_uses = []
        for formula_use in authored["formula_uses"]:
            visible_formula_uses.append({
                "formula_id": formula_use["formula_id"],
                "why_applicable": copy.deepcopy(formula_use["why_applicable"]),
                "why_applicable_provenance": copy.deepcopy(
                    formula_use["why_applicable_provenance"]),
                "variable_mapping": [
                    {
                        "symbol": mapping["symbol"],
                        "maps_to": copy.deepcopy(mapping["maps_to"]),
                        "maps_to_provenance": copy.deepcopy(
                            mapping["maps_to_provenance"]),
                    }
                    for mapping in formula_use["variable_mapping"]
                ],
                "substitution": _visible_substitution(
                    formula_use["substitution"],
                    formula_use["substitution_provenance"],
                    normalized["language"],
                ),
                "substitution_provenance": formula_use[
                    "substitution_provenance"],
            })
        visible_quantities = {}
        for quantity_key in ("known_quantities", "unknown_quantities"):
            visible_quantities[quantity_key] = []
            for quantity in authored[quantity_key]:
                visible = {
                    "label": copy.deepcopy(quantity["label"]),
                    "provenance": copy.deepcopy(quantity["provenance"]),
                }
                for key in ("symbol", "value", "unit"):
                    if key in quantity:
                        visible[key] = quantity[key]
                visible_quantities[quantity_key].append(visible)
        walk = {
            "item_id": item_id,
            "source_type": item["source_type"],
            "answer_provenance": copy.deepcopy(authored["answer_provenance"]),
            "knowledge_point_ids": list(authored["knowledge_point_ids"]),
            "title": copy.deepcopy(authored["title"]),
            "original_language": item["original_language"],
            "prompt_asset_mode": item["prompt_asset_mode"],
            "prompt_asset_paths": [row["path"] for row in item["prompt_assets"]],
            "answer_asset_paths": [row["path"] for row in item["answer_assets"]],
            "translation": copy.deepcopy(authored["translation"]),
            "translation_provenance": copy.deepcopy(
                authored["translation_provenance"]),
            "what_asked": copy.deepcopy(authored["what_asked"]),
            "what_asked_provenance": copy.deepcopy(
                authored["what_asked_provenance"]),
            "known_quantities": visible_quantities["known_quantities"],
            "unknown_quantities": visible_quantities["unknown_quantities"],
            "formula_uses": visible_formula_uses,
            "steps": copy.deepcopy(authored["steps"]),
            "steps_provenance": copy.deepcopy(authored["steps_provenance"]),
            "answer": copy.deepcopy(authored["answer"]),
            "source_trace": trace,
            "solution_kind": authored["solution_kind"],
            "knowledge_point_uses": copy.deepcopy(
                authored["knowledge_point_uses"]),
            "knowledge_point_uses_provenance": copy.deepcopy(
                authored["knowledge_point_uses_provenance"]),
            "notebook_anchor": bindings["notebook_anchors"][item_id],
            "notebook_block_sha256": bindings["notebook_block_sha256"][item_id],
        }
        if "answer_explanation" in authored:
            walk["answer_explanation"] = copy.deepcopy(
                authored["answer_explanation"])
            walk["answer_explanation_provenance"] = copy.deepcopy(
                authored["answer_explanation_provenance"])
            if "answer_explanation_receipt" in authored:
                walk["answer_explanation_receipt"] = copy.deepcopy(
                    authored["answer_explanation_receipt"])
        if "teaching_answer" in authored:
            walk["teaching_answer"] = copy.deepcopy(authored["teaching_answer"])
            walk["teaching_answer_provenance"] = copy.deepcopy(
                authored["teaching_answer_provenance"])
        if "no_formula_reason" in authored:
            walk["no_formula_reason"] = copy.deepcopy(
                authored["no_formula_reason"])
            walk["no_formula_reason_provenance"] = copy.deepcopy(
                authored["no_formula_reason_provenance"])
        if item["prompt_asset_mode"] != "full_prompt":
            selected = item.get("selected_prompt")
            if selected is None:
                raise AuthoringError("item %s lacks packet-selected prompt text" % item_id)
            walk["prompt_text"] = selected["value"]
            proposals.append(_proposal(
                chapter, "walkthrough", item_id, "prompt_text",
                item["original_language"], selected["source_unit_id"],
                selected["payload_field"], "question_evidence", selected["value"],
                selected["value"],
            ))
        for code, provenance in sorted(authored["answer_provenance"].items()):
            if provenance != "material":
                continue
            normalized_answer = guide_content._normalize_exact_text(authored["answer"][code])
            matches = []
            for evidence in item["answer_evidence"]:
                if evidence["source_language"] != code or not evidence["trusted_material"]:
                    continue
                for payload in evidence["payloads"]:
                    if payload["payload_field"] not in ("text", "latex"):
                        continue
                    if guide_content._normalize_exact_text(payload["value"]) == normalized_answer:
                        matches.append((evidence, payload))
            if not matches:
                raise AuthoringError("material answer %s/%s lost its packet evidence" % (item_id, code))
            matches.sort(key=lambda pair: (
                pair[0]["source_unit_id"],
                0 if pair[1]["payload_field"] == "text" else 1,
            ))
            evidence, payload = matches[0]
            proposals.append(_proposal(
                chapter, "walkthrough", item_id, "answer", code,
                evidence["source_unit_id"], payload["payload_field"], "answer_evidence",
                authored["answer"][code], payload["value"],
            ))
        for field, role in (("what_asked", "question_evidence"),):
            for code, binding in sorted(
                authored["material_bindings"].get(field, {}).items()
            ):
                proposals.append(_proposal(
                    chapter, "walkthrough", item_id, field, code,
                    binding["source_unit_id"], binding["payload_field"], role,
                    authored[field][code], binding["quote_text"],
                ))
        walkthroughs.append(walk)

    manifest = {
        "schema_version": guide_content.SCHEMA_VERSION,
        "chapter": chapter,
        "language": normalized["language"],
        "profile": "full",
        "authoring_protocol_version": 2,
        "answer_explanation_mode": answer_explanation_mode,
        "knowledge_points": knowledge_points,
        "walkthroughs": walkthroughs,
        "omissions": [],
        "semantic_exclusions": [],
    }
    if explanation_binding is not None:
        manifest["answer_explanation_contract"] = copy.deepcopy(
            explanation_binding)
    try:
        report = guide_content.validate_manifest(
            workspace, chapter, manifest, _enforce_v2_claims=False
        )
    except guide_content.ContentError as exc:
        raise AuthoringError("mechanically compiled manifest failed validation: %s" % exc)
    proposal_document = {
        "schema_version": 1,
        "proposals": sorted(
            proposals,
            key=lambda row: (
                row["subject"]["entity_type"], row["subject"]["entity_id"],
                row["subject"]["field"], row["subject"]["language"],
                row["source_unit_id"],
            ),
        ),
    }
    _verify_asset_revisions(workspace, expected_assets)
    return manifest, proposal_document, report


_CLAIM_ENTITY_ALIASES = {
    "knowledge_point": "knowledge_point",
    "formula": "formula",
    "walkthrough": "walkthrough",
    "teaching_item": "walkthrough",
    "quiz_item": "walkthrough",
}
_REF_CLAIM_ROLES = {
    "concept": frozenset(("concept_evidence", "context_evidence")),
    "formula": frozenset(("formula_evidence",)),
    "question": frozenset(("question_evidence", "translation_evidence", "context_evidence")),
    "answer": frozenset(("answer_evidence", "translation_evidence", "context_evidence")),
    "solution": frozenset(("answer_evidence", "translation_evidence", "context_evidence")),
}


def _claim_specs(manifest):
    specs = []
    for kp in manifest["knowledge_points"]:
        provenance = kp.get("explanation_provenance") or {
            code: "material" for code in kp["explanation"]
        }
        for code, label in sorted(provenance.items()):
            if label == "material":
                specs.append({
                    "entity_type": "knowledge_point", "entity_id": kp["id"],
                    "field": "explanation", "language": code,
                    "claim_text": kp["explanation"][code], "refs": kp["source_refs"],
                    "ref_roles": frozenset(("concept",)),
                })
        for formula in kp["formulas"]:
            specs.append({
                "entity_type": "formula", "entity_id": formula["id"],
                "field": "latex", "language": "source",
                "claim_index": 0,
                "claim_text": formula["latex"], "refs": formula["source_refs"],
                "ref_roles": frozenset(("formula",)),
            })
            for field in ("explanation", "applicability"):
                explicit_provenance = formula.get("%s_provenance" % field)
                for code, text in sorted(formula[field].items()):
                    if explicit_provenance is not None:
                        if explicit_provenance[code] != "material":
                            continue
                    elif _is_ai_visible_text(text):
                        # Compatibility for a legacy compiled manifest whose AI
                        # provenance was encoded as a visible text prefix.
                        continue
                    specs.append({
                        "entity_type": "formula", "entity_id": formula["id"],
                        "field": field, "language": code, "claim_index": 0,
                        "claim_text": text, "refs": formula["source_refs"],
                        "ref_roles": frozenset(("formula",)),
                    })
            for variable_index, variable in enumerate(formula["variables"]):
                explicit_provenance = variable.get("meaning_provenance")
                for code, text in sorted(variable["meaning"].items()):
                    if explicit_provenance is not None:
                        if explicit_provenance[code] != "material":
                            continue
                    elif _is_ai_visible_text(text):
                        # Compatibility for legacy inline-labelled output.
                        continue
                    specs.append({
                        "entity_type": "formula", "entity_id": formula["id"],
                        "field": "variable_meaning", "language": code,
                        "claim_index": variable_index,
                        "claim_text": text, "refs": formula["source_refs"],
                        "ref_roles": frozenset(("formula",)),
                    })
    for walk in manifest["walkthroughs"]:
        if "prompt_text" in walk:
            allowed = {"source"}
            if walk["original_language"] in ("zh", "en"):
                allowed.add(walk["original_language"])
            specs.append({
                "entity_type": "walkthrough", "entity_id": walk["item_id"],
                "field": "prompt_text", "language": frozenset(allowed),
                "claim_index": 0,
                "claim_text": walk["prompt_text"], "refs": walk["source_trace"],
                "ref_roles": frozenset(("question",)),
            })
        for code, label in sorted(walk["answer_provenance"].items()):
            if label == "material":
                specs.append({
                    "entity_type": "walkthrough", "entity_id": walk["item_id"],
                    "field": "answer", "language": code,
                    "claim_index": 0,
                    "claim_text": walk["answer"][code], "refs": walk["source_trace"],
                    "ref_roles": frozenset(("answer", "solution")),
                })
        for field, roles in (("what_asked", frozenset(("question",))),):
            explicit_provenance = walk.get("%s_provenance" % field)
            for code, text in sorted((walk.get(field) or {}).items()):
                if explicit_provenance is not None:
                    if explicit_provenance[code] != "material":
                        continue
                elif _is_ai_visible_text(text):
                    # Compatibility for legacy inline-labelled output.
                    continue
                specs.append({
                    "entity_type": "walkthrough", "entity_id": walk["item_id"],
                    "field": field, "language": code, "claim_index": 0,
                    "claim_text": text, "refs": walk["source_trace"],
                    "ref_roles": roles,
                })
    return specs


def _all_claim_refs(manifest):
    for kp in manifest["knowledge_points"]:
        for ref in kp["source_refs"]:
            yield ref
        for formula in kp["formulas"]:
            for ref in formula["source_refs"]:
                yield ref
    for walk in manifest["walkthroughs"]:
        for ref in walk["source_trace"]:
            yield ref


def _manifest_asset_revisions(workspace, manifest):
    paths = []
    for walkthrough in manifest.get("walkthroughs") or []:
        paths.extend(walkthrough.get("prompt_asset_paths") or [])
        paths.extend(walkthrough.get("answer_asset_paths") or [])
    paths.extend(
        ref["asset_path"]
        for ref in _all_claim_refs(manifest)
        if isinstance(ref.get("asset_path"), str)
    )
    return _asset_revisions_for_paths(workspace, paths)


def attach_claims(workspace, chapter, manifest_path):
    workspace = _workspace(workspace)
    require_current_ingestion_v2(
        workspace, purpose="Study Guide claim attachment")
    exam_start.require_full_processing(
        workspace, purpose="Study Guide claim attachment")
    fact_snapshot, fact_snapshot_hash = _snapshot(workspace)
    source_revisions, source_revisions_hash = _source_revision_snapshot(workspace)
    source = _safe_workspace_path(workspace, manifest_path, "claim-draft manifest")
    manifest = _strict_json(source, "claim-draft manifest")
    try:
        guide_content.validate_manifest(
            workspace, chapter, manifest, _enforce_v2_claims=False
        )
    except guide_content.ContentError as exc:
        raise AuthoringError("claim-draft manifest is invalid: %s" % exc)
    asset_revisions = _manifest_asset_revisions(workspace, manifest)
    if any("claim_id" in ref or "quote_span" in ref for ref in _all_claim_refs(manifest)):
        raise AuthoringError("claim-draft manifest already contains claim bindings")
    try:
        records = load_claim_records(workspace, allow_empty=False)
    except ClaimValidationError as exc:
        raise AuthoringError("cannot load canonical claim records: %s" % exc)
    claim_records_path = os.path.join(workspace, ".ingest", "claim_records.jsonl")
    try:
        claim_records_sha256 = file_sha256(claim_records_path)
    except OSError as exc:
        raise AuthoringError("cannot bind canonical claim-record bytes: %s" % exc)

    output = copy.deepcopy(manifest)
    for spec in _claim_specs(output):
        allowed_units = {
            ref["source_unit_id"]
            for ref in spec["refs"]
            if ref.get("role") in spec["ref_roles"]
        }
        matches = []
        for record in records:
            subject = record.subject
            language = spec["language"]
            language_matches = (
                subject.language in language if isinstance(language, frozenset)
                else subject.language == language
            )
            if (
                _CLAIM_ENTITY_ALIASES.get(subject.entity_type) != spec["entity_type"]
                or subject.entity_id != spec["entity_id"]
                or subject.field != spec["field"]
                or subject.claim_index != spec.get("claim_index", 0)
                or not language_matches
                or record.claim_text != spec["claim_text"]
                or record.source.unit_ref.unit_id not in allowed_units
            ):
                continue
            compatible_refs = [
                ref for ref in spec["refs"]
                if ref["source_unit_id"] == record.source.unit_ref.unit_id
                and ref.get("role") in spec["ref_roles"]
                and record.source.role in _REF_CLAIM_ROLES.get(ref.get("role"), frozenset())
            ]
            if compatible_refs:
                matches.append((record, compatible_refs[0]))
        if len(matches) != 1:
            raise AuthoringError(
                "claim binding for %s/%s/%s/%s is %s"
                % (
                    spec["entity_type"], spec["entity_id"], spec["field"],
                    sorted(spec["language"]) if isinstance(spec["language"], frozenset) else spec["language"],
                    "missing" if not matches else "ambiguous (%d candidates)" % len(matches),
                )
            )
        record, ref = matches[0]
        target = ref
        if "claim_id" in target or "quote_span" in target:
            # One physical source location may support multiple distinct
            # ClaimSubjects (for example formula latex and its exact material
            # explanation).  Each claim still needs its own ref object because
            # claim_id/quote_span are scalar bindings.
            target = copy.deepcopy(ref)
            target.pop("claim_id", None)
            target.pop("quote_span", None)
            spec["refs"].append(target)
        target["claim_id"] = record.claim_id
        target["quote_span"] = record.quote.text
    try:
        bound = validate_claim_subject_bindings(records, output, "ch%02d" % chapter)
        inventory = guide_content._source_inventory(workspace, chapter)
        covered = validate_guide_claim_coverage(
            records, output, "ch%02d" % chapter, inventory["units"]
        )
        guide_content.validate_manifest(
            workspace, chapter, output, _enforce_v2_claims=False
        )
    except (ClaimValidationError, guide_content.ContentError) as exc:
        raise AuthoringError("attached claims failed exact validation: %s" % exc)
    after_source_revisions, after_source_hash = _source_revision_snapshot(workspace)
    after_fact_snapshot, after_fact_hash = _snapshot(workspace)
    if after_fact_hash != fact_snapshot_hash or after_fact_snapshot != fact_snapshot:
        raise AuthoringError("source facts drifted while claims were attached")
    if after_source_hash != source_revisions_hash or after_source_revisions != source_revisions:
        raise AuthoringError("source bytes drifted while claims were attached")
    try:
        if file_sha256(claim_records_path) != claim_records_sha256:
            raise AuthoringError("canonical claim records drifted while claims were attached")
    except OSError as exc:
        raise AuthoringError("canonical claim records disappeared while claims were attached: %s" % exc)
    _verify_asset_revisions(workspace, asset_revisions)
    return output, {
        "bound_claim_count": len(bound),
        "covered_assertions": list(covered),
        "source_snapshot_sha256": fact_snapshot_hash,
        "source_revisions_sha256": source_revisions_hash,
        "claim_records_sha256": claim_records_sha256,
        "asset_revisions": asset_revisions,
    }


def _default_path(chapter, suffix):
    return "notebook/ch%02d.%s" % (chapter, suffix)


def _reserved_output_relative(chapter, artifact_kind):
    try:
        suffix = OUTPUT_SUFFIXES[artifact_kind]
    except KeyError:
        raise AuthoringError("unknown authoring output kind: %s" % artifact_kind)
    return _default_path(chapter, suffix)


def _reserved_output_target(workspace, path, label, expected_relative):
    target = _safe_workspace_path(workspace, path, label, output=True)
    try:
        actual = normalize_workspace_path(_relative(workspace, target))
        expected = normalize_workspace_path(expected_relative)
    except (UnsafePathError, ValueError) as exc:
        raise AuthoringError("%s is not a canonical reserved output: %s" % (label, exc))
    if actual != expected:
        raise AuthoringError(
            "%s must resolve exactly to reserved authoring output %s"
            % (label, expected)
        )
    return target


def _replace_path(source, destination):
    """Small seam for deterministic publication-failure tests."""

    os.replace(source, destination)


def _stage_bytes(destination, payload):
    if len(payload) > MAX_JSON_BYTES:
        raise AuthoringError("authoring output exceeds the bounded publication limit")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(destination),
        suffix=".author.tmp",
        dir=os.path.dirname(destination),
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _restore_published_file(destination, original):
    if original is None:
        if os.path.exists(destination):
            os.unlink(destination)
        return
    temporary = _stage_bytes(destination, original)
    try:
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _verify_publication_state(workspace, expected_snapshot_sha256,
                              expected_source_revisions_sha256,
                              expected_claim_records_sha256,
                              expected_asset_revisions, phase):
    if expected_snapshot_sha256 is not None:
        unused_snapshot, live_hash = _snapshot(workspace)
        if live_hash != expected_snapshot_sha256:
            raise AuthoringError("source facts drifted %s output publication" % phase)
    if expected_source_revisions_sha256 is not None:
        unused_sources, live_source_hash = _source_revision_snapshot(workspace)
        if live_source_hash != expected_source_revisions_sha256:
            raise AuthoringError("source bytes drifted %s output publication" % phase)
    if expected_claim_records_sha256 is not None:
        claim_path = os.path.join(workspace, ".ingest", "claim_records.jsonl")
        try:
            live_claim_hash = file_sha256(claim_path)
        except OSError as exc:
            raise AuthoringError("canonical claim records are unavailable: %s" % exc)
        if live_claim_hash != expected_claim_records_sha256:
            raise AuthoringError(
                "canonical claim records drifted %s output publication" % phase
            )
    if expected_asset_revisions is not None:
        _verify_asset_revisions(workspace, expected_asset_revisions)


def _publish_json(workspace, documents, expected_snapshot_sha256=None,
                  expected_source_revisions_sha256=None,
                  expected_claim_records_sha256=None,
                  expected_asset_revisions=None, lock_held=False):
    paths = []
    for path, value, label, expected_relative in documents:
        target = _reserved_output_target(
            workspace, path, label, expected_relative
        )
        paths.append((target, value))
    if len({path for path, _value in paths}) != len(paths):
        raise AuthoringError("output paths must be distinct")
    try:
        lock = contextlib.nullcontext() if lock_held else workspace_publication_lock(workspace)
        with lock:
            exam_start.require_full_processing(
                workspace, purpose="Study Guide authoring output publication"
            )
            require_current_ingestion_v2(
                workspace, purpose="Study Guide authoring output publication")
            _verify_publication_state(
                workspace, expected_snapshot_sha256,
                expected_source_revisions_sha256,
                expected_claim_records_sha256,
                expected_asset_revisions, "before",
            )
            notebook_dir = os.path.join(workspace, "notebook")
            if not os.path.exists(notebook_dir):
                os.makedirs(notebook_dir)
            if is_link_or_reparse(notebook_dir) or not os.path.isdir(notebook_dir):
                raise AuthoringError("workspace/notebook is not a safe regular directory")
            staged = []
            originals = {}
            published = []
            try:
                for target, value in paths:
                    try:
                        payload = (
                            json.dumps(
                                value, ensure_ascii=False, sort_keys=True,
                                indent=2, allow_nan=False,
                            ) + "\n"
                        ).encode("utf-8")
                    except (TypeError, ValueError) as exc:
                        raise AuthoringError("authoring output is not strict JSON: %s" % exc)
                    if os.path.exists(target):
                        if is_link_or_reparse(target) or not os.path.isfile(target):
                            raise AuthoringError("output destination is not a regular non-link file")
                        with open(target, "rb") as stream:
                            original = stream.read(MAX_JSON_BYTES + 1)
                        if len(original) > MAX_JSON_BYTES:
                            raise AuthoringError("existing output exceeds rollback size limit")
                        originals[target] = original
                    else:
                        originals[target] = None
                    staged.append((_stage_bytes(target, payload), target))
                for temporary, target in staged:
                    _replace_path(temporary, target)
                    published.append(target)
                _verify_publication_state(
                    workspace, expected_snapshot_sha256,
                    expected_source_revisions_sha256,
                    expected_claim_records_sha256,
                    expected_asset_revisions, "after",
                )
                staged = []
            except Exception as exc:
                rollback_errors = []
                for target in reversed(published):
                    try:
                        _restore_published_file(target, originals[target])
                    except OSError as rollback_exc:
                        rollback_errors.append("%s: %s" % (target, rollback_exc))
                if rollback_errors:
                    raise AuthoringError(
                        "authoring publication failed and rollback was incomplete: %s"
                        % "; ".join(rollback_errors)
                    ) from exc
                raise
            finally:
                for temporary, _target in staged:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
    except AuthoringError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise AuthoringError("cannot publish authoring output: %s" % exc)
    return [path for path, _value in paths]


def _print_result(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    else:
        for key in sorted(value):
            print("%s: %s" % (key, value[key]))


def _add_common(subparser, chapter=True):
    if chapter:
        subparser.add_argument("--chapter", required=True, type=int)
    subparser.add_argument("--json", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Revision-bound Study Guide authoring compiler. It prepares facts, persists "
            "official notebook walkthroughs, compiles a full claim draft, and attaches "
            "already-imported canonical claim records. It never renders or imports a Guide."
        )
    )
    parser.add_argument("--workspace", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare", help="create a source-fact packet and incomplete annotation template"
    )
    _add_common(prepare)
    prepare.add_argument("--output")

    rebase = commands.add_parser(
        "rebase-annotations",
        help=(
            "rebind canonical authored annotations to the current ready packet; "
            "only packet_sha256, a missing answer_explanation_mode binding, and "
            "paired legacy self-check removal are allowed"
        ),
    )
    _add_common(rebase)
    rebase.add_argument("--packet")
    rebase.add_argument("--annotations")

    persist = commands.add_parser(
        "persist-notebooks", help="validate annotations and persist every official walkthrough"
    )
    _add_common(persist)
    persist.add_argument("--packet")
    persist.add_argument("--annotations")
    persist.add_argument(
        "--answer-explanations",
        help=("canonical finalized receipt from study_guide_explain.py; valid "
              "only when answer_explanation_mode=isolated"),
    )
    persist.add_argument("--output")

    compile_command = commands.add_parser(
        "compile", help="mechanically compile a full claim-draft manifest and proposals"
    )
    _add_common(compile_command)
    compile_command.add_argument("--packet")
    compile_command.add_argument("--annotations")
    compile_command.add_argument("--notebook-bindings")
    compile_command.add_argument(
        "--answer-explanations",
        help=("canonical finalized receipt from study_guide_explain.py; valid "
              "only when answer_explanation_mode=isolated"),
    )
    compile_command.add_argument("--manifest-output")
    compile_command.add_argument("--proposals-output")

    attach = commands.add_parser(
        "attach-claims", help="attach exact, nonambiguous canonical claim records"
    )
    _add_common(attach)
    attach.add_argument("--manifest")
    attach.add_argument("--output")
    return parser


def run(argv=None):
    args = build_parser().parse_args(argv)
    try:
        workspace = _workspace(args.workspace)
        exam_start.require_full_processing(
            workspace, purpose="Study Guide authoring command")
        chapter = args.chapter
        if chapter < 1:
            raise AuthoringError("chapter must be >= 1")
        if args.command == "prepare":
            packet = prepare_packet(workspace, chapter)
            annotations_template = build_annotations_template(packet)
            packet_relative = _reserved_output_relative(chapter, "packet")
            template_relative = _reserved_output_relative(
                chapter, "annotations_template"
            )
            output = args.output or packet_relative
            paths = _publish_json(
                workspace,
                [
                    (output, packet, "authoring packet output", packet_relative),
                    (
                        template_relative,
                        annotations_template,
                        "authoring annotations template output",
                        template_relative,
                    ),
                ],
                expected_snapshot_sha256=packet["source_snapshot_sha256"],
                expected_source_revisions_sha256=packet["source_revisions_sha256"],
                expected_asset_revisions=_packet_asset_revisions(packet),
            )
            result = {
                "ok": not bool(packet["blockers"]),
                "status": packet["status"],
                "chapter": chapter,
                "answer_explanation_mode": packet["answer_explanation_mode"],
                "packet_sha256": packet["packet_sha256"],
                "blockers": packet["blockers"],
                "output": paths[0],
                "annotations_template_output": paths[1],
                "annotations_template_sha256": _sha256_json(
                    annotations_template
                ),
            }
            _print_result(result, args.json)
            return 10 if packet["blockers"] else 0

        if args.command == "rebase-annotations":
            packet_path = args.packet or _default_path(
                chapter, "authoring-packet.json")
            annotations_path = args.annotations or _default_path(
                chapter, "authoring-annotations.json")
            result = rebase_annotations(
                workspace, chapter, packet_path, annotations_path)
            result["ok"] = True
            _print_result(result, args.json)
            return 0

        packet_path = getattr(args, "packet", None) or _default_path(
            chapter, "authoring-packet.json"
        )
        if args.command == "persist-notebooks":
            annotations_path = args.annotations or _default_path(
                chapter, "authoring-annotations.json"
            )
            binding_relative = _reserved_output_relative(chapter, "bindings")
            output = args.output or binding_relative
            output = _reserved_output_target(
                workspace, output, "notebook bindings output", binding_relative
            )
            binding = persist_notebooks(
                workspace, chapter, packet_path, annotations_path,
                binding_output=output,
                explanation_receipt_path=args.answer_explanations,
            )
            result = {
                "ok": True,
                "chapter": chapter,
                "answer_explanation_mode": binding["answer_explanation_mode"],
                "notebook_anchor_count": len(binding["notebook_anchors"]),
                "bindings_sha256": binding["bindings_sha256"],
                "output": output,
            }
            _print_result(result, args.json)
            return 0

        if args.command == "compile":
            annotations_path = args.annotations or _default_path(
                chapter, "authoring-annotations.json"
            )
            bindings_path = args.notebook_bindings or _default_path(
                chapter, "authoring-bindings.json"
            )
            manifest, proposals, report = compile_manifest(
                workspace, chapter, packet_path, annotations_path, bindings_path,
                explanation_receipt_path=args.answer_explanations,
            )
            packet = _load_packet(workspace, packet_path, chapter, require_ready=True)
            manifest_relative = _reserved_output_relative(chapter, "claim_draft")
            proposals_relative = _reserved_output_relative(chapter, "claim_proposals")
            manifest_output = args.manifest_output or manifest_relative
            proposals_output = args.proposals_output or proposals_relative
            paths = _publish_json(
                workspace,
                [
                    (
                        manifest_output, manifest, "claim-draft manifest output",
                        manifest_relative,
                    ),
                    (
                        proposals_output, proposals, "claim proposals output",
                        proposals_relative,
                    ),
                ],
                expected_snapshot_sha256=packet["source_snapshot_sha256"],
                expected_source_revisions_sha256=packet["source_revisions_sha256"],
                expected_asset_revisions=_packet_asset_revisions(packet),
            )
            result = {
                "ok": True,
                "chapter": chapter,
                "answer_explanation_mode": manifest[
                    "answer_explanation_mode"],
                "manifest_output": paths[0],
                "proposals_output": paths[1],
                "walkthrough_count": len(manifest["walkthroughs"]),
                "semantic_unit_count": report["semantic_unit_counts"]["expected"],
                "claim_proposal_count": len(proposals["proposals"]),
            }
            _print_result(result, args.json)
            return 0

        manifest_path = args.manifest or _default_path(chapter, "guide.claim-draft.json")
        manifest, claim_report = attach_claims(workspace, chapter, manifest_path)
        attached_relative = _reserved_output_relative(chapter, "claim_attached")
        output = args.output or attached_relative
        paths = _publish_json(
            workspace,
            [(
                output, manifest, "claim-attached manifest output",
                attached_relative,
            )],
            expected_snapshot_sha256=claim_report["source_snapshot_sha256"],
            expected_source_revisions_sha256=claim_report["source_revisions_sha256"],
            expected_claim_records_sha256=claim_report["claim_records_sha256"],
            expected_asset_revisions=claim_report["asset_revisions"],
        )
        result = {
            "ok": True,
            "chapter": chapter,
            "output": paths[0],
            "bound_claim_count": claim_report["bound_claim_count"],
            "covered_assertion_count": len(claim_report["covered_assertions"]),
        }
        _print_result(result, args.json)
        return 0
    except exam_start.FullProcessingRequired as exc:
        error = {"ok": False, "error": str(exc), "exit_code": 2}
        if getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            sys.stderr.write("study_guide_author: %s\n" % exc)
        return 2
    except (AuthoringError, ClaimValidationError, ConflictError) as exc:
        error = {"ok": False, "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            sys.stderr.write("study_guide_author: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
