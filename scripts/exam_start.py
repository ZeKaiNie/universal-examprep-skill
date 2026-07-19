#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Executable first-contact gate for an exam workspace.

``status`` is read-only. ``confirm`` is the sole convenience command that may
create a user-confirmed workspace, initialize progress state, set all three
learning choices plus the independent processing and answer-explanation
choices and optional teaching cadence in one update, and
bind an exact workspace/materials receipt in the global registry.  The default
processing choice is lightweight and does not open the eager ingestion gate.
Ingestion imports :func:`check_start_gate` and fails closed unless the user has
explicitly selected ``full`` and the remaining gate evidence is current.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import i18n  # noqa: E402
import update_progress  # noqa: E402

try:  # noqa: E402
    from ingestion import (
        ConflictError,
        IngestionStore,
        is_link_or_reparse,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from ingestion.identifiers import canonical_json, file_sha256, normalize_workspace_path
    from ingestion.storage import atomic_write_json, read_json
    from material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        append_runtime_recovery,
        build_runtime_recovery_from_binding,
        expire_latest_runtime_recovery,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )
except ImportError:  # pragma: no cover - package import fallback
    from scripts.ingestion import (
        ConflictError,
        IngestionStore,
        is_link_or_reparse,
        safe_workspace_entry,
        stable_read_bytes,
        workspace_publication_lock,
    )
    from scripts.ingestion.identifiers import (
        canonical_json,
        file_sha256,
        normalize_workspace_path,
    )
    from scripts.ingestion.storage import atomic_write_json, read_json
    from scripts.material_generation import (
        MATERIAL_BUILD_PENDING_PATH,
        append_runtime_recovery,
        build_runtime_recovery_from_binding,
        expire_latest_runtime_recovery,
        material_recovery_path,
        validate_generation,
        validate_runtime_recovery_log,
    )


CHOICE_FIELDS = ("mode", "time_budget", "language")
PACKAGE_ROOT = os.path.dirname(SCRIPT_DIR)
RUNTIME_RECEIPT_NAME = "exam_runtime_receipt.json"
RUNTIME_RECEIPT_SCHEMA = 1
RUNTIME_ROOT_FILES = ("SKILL.md", "AGENTS.md", "LICENSE")
RUNTIME_TREE_RULES = (
    ("skills", (".md",)),
    ("locales", (".md", ".json")),
    ("scripts", (".py",)),
    ("prompts", (".md",)),
    ("docs", (".md", ".json")),
)
RUNTIME_PATH_EXCLUDES = (
    "docs/history/",
    "docs/plans/",
    "docs/releases/",
)
RUNTIME_FILE_EXCLUDES = frozenset((
    "scripts/build_dist.py",
    "scripts/retrieval_evaluation.py",
    "scripts/ingestion/evaluation.py",
    "docs/formula-audit-importer.md",
    "docs/agent-portability.zh.md",
    "docs/exam-audit.zh.md",
    "docs/file-format.en.md",
    "docs/language-policy.zh.md",
    "docs/localization.md",
    "docs/openai-study-guide-adapter.zh.md",
    "docs/pdf-capability-adapters.en.md",
    "docs/retrieval-evaluation.md",
    "docs/runtime-file-contract.md",
    "docs/skill-architecture.md",
    "docs/skill-architecture.en.md",
))
RUNTIME_DIFF_LIMIT = 20
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class RuntimeReceiptError(ValueError):
    """The installed runtime cannot be identified or safely verified."""


class FullProcessingRequired(RuntimeError):
    """Full-workspace publication was attempted without the exact full gate."""

    def __init__(self, message, *, reason=None, blockers=()):
        super().__init__(message)
        self.reason = reason
        self.blockers = tuple(blockers or ())


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _bounded_text(value, limit=240):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _resolved_path(value):
    """Return one absolute, long-form path spelling for receipts and comparisons."""
    return str(Path(os.path.abspath(value)).resolve(strict=False))


def _runtime_manifest(package_root):
    """Return the deterministic student-runtime surface, rejecting links."""

    root = os.path.abspath(package_root)
    if not os.path.isdir(root) or is_link_or_reparse(root):
        raise RuntimeReceiptError("package root is missing or link-backed")
    relative_paths = list(RUNTIME_ROOT_FILES)
    for directory, extensions in RUNTIME_TREE_RULES:
        tree = os.path.join(root, directory)
        if not os.path.isdir(tree) or is_link_or_reparse(tree):
            raise RuntimeReceiptError("runtime directory is missing or link-backed: %s" % directory)
        for dirpath, dirnames, filenames in os.walk(tree, topdown=True, followlinks=False):
            for dirname in list(dirnames):
                absolute_dir = os.path.join(dirpath, dirname)
                if is_link_or_reparse(absolute_dir):
                    raise RuntimeReceiptError(
                        "runtime directory contains a link/reparse entry"
                    )
            for filename in filenames:
                if not filename.lower().endswith(extensions):
                    continue
                absolute = os.path.join(dirpath, filename)
                relative = os.path.relpath(absolute, root).replace("\\", "/")
                if relative in RUNTIME_FILE_EXCLUDES:
                    continue
                if any(relative.startswith(prefix) for prefix in RUNTIME_PATH_EXCLUDES):
                    continue
                relative_paths.append(relative)

    normalized = []
    for relative in sorted(set(relative_paths)):
        try:
            relative = normalize_workspace_path(relative)
        except ValueError as exc:
            raise RuntimeReceiptError("invalid runtime manifest path") from exc
        absolute = os.path.join(root, *relative.split("/"))
        if (not os.path.isfile(absolute)) or is_link_or_reparse(absolute):
            raise RuntimeReceiptError("runtime file is missing or link-backed: %s" % relative)
        normalized.append(relative)
    if not normalized:
        raise RuntimeReceiptError("runtime manifest is empty")
    return normalized


def _skill_metadata(skill_path):
    try:
        with open(skill_path, "r", encoding="utf-8") as stream:
            lines = []
            for index, line in enumerate(stream):
                if index > 200:
                    break
                lines.append(line.rstrip("\r\n"))
                if index and line.strip() == "---":
                    break
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeReceiptError("SKILL.md is unreadable") from exc
    if not lines or lines[0].lstrip("\ufeff") != "---" or lines[-1] != "---":
        raise RuntimeReceiptError("SKILL.md frontmatter is invalid")
    metadata = {}
    for line in lines[1:-1]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in ("name", "version"):
            metadata[key] = value.strip().strip("\"'")
    if not metadata.get("name") or not metadata.get("version"):
        raise RuntimeReceiptError("SKILL.md name/version metadata is missing")
    return {"name": metadata["name"], "version": metadata["version"]}


def _git_command(package_root, arguments):
    try:
        result = subprocess.run(
            ["git", "-C", package_root] + list(arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError:
        return None, "git_not_found"
    except subprocess.TimeoutExpired:
        return None, "git_timeout"
    except OSError:
        return None, "git_unavailable"
    if result.returncode != 0:
        return None, "git_exit_%d" % result.returncode
    return result.stdout.strip(), None


def _git_snapshot(package_root):
    unavailable = {
        "available": False,
        "commit": None,
        "branch": None,
        "dirty": None,
        "reason": None,
    }
    identity, reason = _git_command(
        package_root, ("rev-parse", "--show-toplevel", "HEAD")
    )
    if reason:
        unavailable["reason"] = reason
        return unavailable
    identity_lines = identity.splitlines()
    if len(identity_lines) != 2:
        unavailable["reason"] = "git_identity_invalid"
        return unavailable
    top, commit = identity_lines
    if os.path.normcase(os.path.realpath(top)) != os.path.normcase(
            os.path.realpath(package_root)):
        unavailable["reason"] = "package_root_not_git_root"
        return unavailable
    status, status_reason = _git_command(
        package_root,
        ("status", "--porcelain=v2", "--branch", "--untracked-files=normal"),
    )
    if status_reason:
        unavailable["reason"] = status_reason
        return unavailable
    branch = None
    dirty = False
    for line in status.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            branch = None if head == "(detached)" else head
        elif not line.startswith("# "):
            dirty = True
    return {
        "available": True,
        "commit": commit,
        "branch": branch or None,
        "dirty": dirty,
        "reason": "detached_head" if branch is None else None,
    }


def _capture_runtime_identity(package_root=None):
    """Hash the installed runtime without mutating it."""

    root = os.path.abspath(package_root or PACKAGE_ROOT)
    manifest = _runtime_manifest(root)
    files = []
    for relative in manifest:
        absolute = os.path.join(root, *relative.split("/"))
        files.append({"path": relative, "sha256": file_sha256(absolute)})
    digest = hashlib.sha256(canonical_json(files).encode("utf-8")).hexdigest()
    return {
        "package_root": _resolved_path(root),
        "skill": _skill_metadata(os.path.join(root, "SKILL.md")),
        "runtime_files": files,
        "runtime_digest": digest,
        "git": _git_snapshot(root),
        "python": {
            "executable": _resolved_path(sys.executable),
            "version": platform.python_version(),
        },
    }


def _runtime_receipt_summary(receipt):
    git = receipt.get("git") or {}
    skill = receipt.get("skill") or {}
    python = receipt.get("python") or {}
    return {
        "created_at": receipt.get("created_at"),
        "package_root": receipt.get("package_root"),
        "skill_version": skill.get("version"),
        "runtime_digest": receipt.get("runtime_digest"),
        "runtime_file_count": len(receipt.get("runtime_files") or []),
        "git_commit": git.get("commit"),
        "git_branch": git.get("branch"),
        "git_dirty": git.get("dirty"),
        "git_reason": git.get("reason"),
        "python_executable": python.get("executable"),
    }


def _validate_runtime_receipt_schema(receipt):
    expected = {
        "schema_version", "receipt_type", "created_at", "package_root", "skill",
        "runtime_files", "runtime_digest", "git", "python",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise RuntimeReceiptError("runtime receipt fields are invalid")
    if (receipt.get("schema_version") != RUNTIME_RECEIPT_SCHEMA
            or receipt.get("receipt_type") != "exam_runtime"):
        raise RuntimeReceiptError("runtime receipt schema/type is invalid")
    if not isinstance(receipt.get("created_at"), str) or not receipt["created_at"].endswith("Z"):
        raise RuntimeReceiptError("runtime receipt timestamp is invalid")
    if not isinstance(receipt.get("package_root"), str) or not receipt["package_root"]:
        raise RuntimeReceiptError("runtime receipt package_root is invalid")
    if (not isinstance(receipt.get("skill"), dict)
            or set(receipt["skill"]) != {"name", "version"}
            or not all(isinstance(receipt["skill"].get(key), str)
                       and receipt["skill"].get(key) for key in ("name", "version"))):
        raise RuntimeReceiptError("runtime receipt skill metadata is invalid")
    if not _HEX64.fullmatch(str(receipt.get("runtime_digest") or "")):
        raise RuntimeReceiptError("runtime receipt digest is invalid")
    rows = receipt.get("runtime_files")
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise RuntimeReceiptError("runtime receipt file manifest is invalid")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise RuntimeReceiptError("runtime receipt file entry is invalid")
        try:
            path = normalize_workspace_path(row.get("path"))
        except ValueError as exc:
            raise RuntimeReceiptError("runtime receipt file path is invalid") from exc
        if path in seen or not _HEX64.fullmatch(str(row.get("sha256") or "")):
            raise RuntimeReceiptError("runtime receipt file hash/identity is invalid")
        seen.add(path)
    if [row["path"] for row in rows] != sorted(seen):
        raise RuntimeReceiptError("runtime receipt file manifest is not canonical")
    git = receipt.get("git")
    if not isinstance(git, dict) or set(git) != {
            "available", "commit", "branch", "dirty", "reason"}:
        raise RuntimeReceiptError("runtime receipt git metadata is invalid")
    if type(git.get("available")) is not bool:
        raise RuntimeReceiptError("runtime receipt git availability is invalid")
    if git["available"]:
        if not _GIT_OBJECT_ID.fullmatch(str(git.get("commit") or "")):
            raise RuntimeReceiptError("runtime receipt git commit is invalid")
        if type(git.get("dirty")) is not bool:
            raise RuntimeReceiptError("runtime receipt git dirty state is invalid")
    elif any(git.get(key) is not None for key in ("commit", "branch", "dirty")):
        raise RuntimeReceiptError("unavailable git metadata must use null values")
    if git.get("branch") is not None and not isinstance(git.get("branch"), str):
        raise RuntimeReceiptError("runtime receipt git branch is invalid")
    if git.get("reason") is not None and not isinstance(git.get("reason"), str):
        raise RuntimeReceiptError("runtime receipt git reason is invalid")
    if not isinstance(receipt.get("python"), dict) or set(receipt["python"]) != {
            "executable", "version"}:
        raise RuntimeReceiptError("runtime receipt Python metadata is invalid")
    if not all(isinstance(receipt["python"].get(key), str)
               and receipt["python"].get(key) for key in ("executable", "version")):
        raise RuntimeReceiptError("runtime receipt Python metadata is invalid")
    calculated = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    if calculated != receipt["runtime_digest"]:
        raise RuntimeReceiptError("runtime receipt aggregate digest is inconsistent")
    return receipt


def _runtime_receipt_document_bytes(receipt):
    return (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _build_runtime_receipt(identity=None):
    receipt = dict(identity or _capture_runtime_identity())
    receipt.update({
        "schema_version": RUNTIME_RECEIPT_SCHEMA,
        "receipt_type": "exam_runtime",
        "created_at": _utc_now(),
    })
    return _validate_runtime_receipt_schema(receipt)


def _write_runtime_receipt(workspace):
    # Runtime hashing can be comparatively expensive and touches only the
    # installed package.  Keep it outside the workspace critical section, then
    # serialize only the validator-visible atomic publication.  In particular,
    # _confirm must never hold this lock while its update_progress subprocesses
    # run: those commands acquire the same state->ingestion lock themselves.
    receipt = _build_runtime_receipt()
    with workspace_publication_lock(workspace):
        destination = safe_workspace_entry(workspace, RUNTIME_RECEIPT_NAME)
        atomic_write_json(destination, receipt)
        if is_link_or_reparse(destination) or not os.path.isfile(destination):
            raise RuntimeReceiptError("runtime receipt write did not produce a regular file")
    return receipt


def _strict_json_payload(payload, label):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key: %s" % key)
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("non-finite JSON constant: %s" % value)

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeReceiptError("%s is not strict JSON" % label) from exc


def _stable_pending_generation(workspace, require_complete_sources=True):
    path = safe_workspace_entry(workspace, MATERIAL_BUILD_PENDING_PATH)
    if not os.path.isfile(path) or is_link_or_reparse(path):
        if os.path.lexists(str(path)):
            raise RuntimeReceiptError("material build pending marker is unsafe")
        return None
    payload, _snapshot = stable_read_bytes(path)
    pending = _strict_json_payload(payload, "material build pending marker")
    validate_generation(pending, expected_status="pending")
    sources_complete = True
    for binding in (pending["raw_input"], pending["parse_report"]):
        try:
            source = safe_workspace_entry(workspace, binding["path"])
            source_payload, _source_snapshot = stable_read_bytes(source)
            matches = hashlib.sha256(source_payload).hexdigest() == binding["sha256"]
        except (OSError, TypeError, ValueError):
            matches = False
        if not matches:
            sources_complete = False
            if require_complete_sources:
                raise RuntimeReceiptError(
                    "material build pending source hash is stale: %s" % binding["path"]
                )
    return {
        "document": pending,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "path": path,
        "sources_complete": sources_complete,
    }


def _recovery_target(workspace, pending_snapshot, action):
    """Resolve the generation whose authorization must be refreshed."""

    pending = pending_snapshot["document"]
    return {
        "pending_binding": {
            "path": MATERIAL_BUILD_PENDING_PATH,
            "sha256": pending_snapshot["sha256"],
            "generation_id": pending["generation_id"],
            "supersedes_generation_id": pending.get("supersedes_generation_id"),
        },
        "path": material_recovery_path(pending["generation_id"]),
        "existing_log": None,
        "interrupted_successor": not pending_snapshot["sources_complete"],
    }


def _runtime_binding_for_recovery(workspace):
    path = safe_workspace_entry(workspace, RUNTIME_RECEIPT_NAME)
    if not os.path.lexists(str(path)):
        return {
            "path": RUNTIME_RECEIPT_NAME,
            "state": "missing",
            "sha256": None,
            "runtime_digest": None,
        }
    if not os.path.isfile(path) or is_link_or_reparse(path):
        raise RuntimeReceiptError("existing runtime receipt is unsafe")
    payload, _snapshot = stable_read_bytes(path)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        receipt = _validate_runtime_receipt_schema(
            _strict_json_payload(payload, "runtime receipt")
        )
    except RuntimeReceiptError:
        return {
            "path": RUNTIME_RECEIPT_NAME,
            "state": "invalid",
            "sha256": digest,
            "runtime_digest": None,
        }
    return {
        "path": RUNTIME_RECEIPT_NAME,
        "state": "valid",
        "sha256": digest,
        "runtime_digest": receipt["runtime_digest"],
    }


def _runtime_receipt_status(workspace):
    result = {
        "verified": False,
        "reason": "runtime_receipt_missing",
        "receipt": None,
        "current": None,
        "changed_files": [],
        "changed_file_count": 0,
        "changed_files_truncated": False,
    }
    if not os.path.isdir(workspace):
        return result
    if is_link_or_reparse(workspace):
        result["reason"] = "unsafe_runtime_receipt"
        return result
    try:
        path = safe_workspace_entry(workspace, RUNTIME_RECEIPT_NAME)
    except (OSError, ValueError):
        result["reason"] = "unsafe_runtime_receipt"
        return result
    if not os.path.isfile(path):
        if os.path.lexists(str(path)):
            result["reason"] = "unsafe_runtime_receipt"
        return result
    try:
        recorded = _validate_runtime_receipt_schema(read_json(path))
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        result["reason"] = "runtime_receipt_unreadable_or_invalid"
        return result
    try:
        current = _capture_runtime_identity()
    except (ConflictError, OSError, RuntimeReceiptError, UnicodeDecodeError, ValueError) as exc:
        result["reason"] = "runtime_snapshot_failed"
        result["snapshot_error"] = _bounded_text(exc)
        result["receipt"] = _runtime_receipt_summary(recorded)
        return result
    result["receipt"] = _runtime_receipt_summary(recorded)
    result["current"] = _runtime_receipt_summary(current)
    comparable_keys = (
        "package_root", "skill", "runtime_files", "runtime_digest", "python"
    )
    recorded_git = recorded.get("git") or {}
    current_git = current.get("git") or {}
    git_identity_matches = (
        recorded_git.get("available") == current_git.get("available")
        and (
            not recorded_git.get("available")
            or recorded_git.get("commit") == current_git.get("commit")
        )
    )
    git_metadata_drift = [
        key for key in ("branch", "dirty", "reason")
        if recorded_git.get(key) != current_git.get(key)
    ]
    result["git_metadata_drift"] = git_metadata_drift
    if (all(recorded.get(key) == current.get(key) for key in comparable_keys)
            and git_identity_matches):
        result["verified"] = True
        result["reason"] = "verified"
        return result

    recorded_files = {row["path"]: row["sha256"] for row in recorded["runtime_files"]}
    current_files = {row["path"]: row["sha256"] for row in current["runtime_files"]}
    changed = sorted(
        path for path in set(recorded_files) | set(current_files)
        if recorded_files.get(path) != current_files.get(path)
    )
    result["reason"] = "runtime_drift"
    result["changed_file_count"] = len(changed)
    result["changed_files"] = changed[:RUNTIME_DIFF_LIMIT]
    result["changed_files_truncated"] = len(changed) > RUNTIME_DIFF_LIMIT
    identity_drift = []
    for key in ("package_root", "skill", "python"):
        if recorded.get(key) != current.get(key):
            identity_drift.append(key)
    if not git_identity_matches:
        identity_drift.append("git_commit")
    result["identity_drift"] = identity_drift
    return result


def _path_is_same_or_inside(root, candidate):
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(candidate)
    try:
        return os.path.commonpath((root_real, candidate_real)) == root_real
    except ValueError:
        return False


def _path_has_link_or_reparse(path):
    current = os.path.abspath(path)
    while True:
        if os.path.lexists(current) and is_link_or_reparse(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if payload.get("process_success"):
        print("ready_to_start=%s" % str(payload.get("ready_to_start", False)).lower())
        print("ready_to_ingest=%s" % str(payload.get("ready_to_ingest", False)).lower())
        print("processing_mode=%s" % payload.get("processing_mode", "lightweight"))
        print("artifact_mode_preference=%s" % payload.get(
            "artifact_mode_preference", "chat"))
        print("artifact_mode_effective=%s" % payload.get(
            "artifact_mode_effective", "chat"))
        print("artifact_mode_dormant=%s" % str(
            payload.get("artifact_mode_dormant", False)).lower())
        print("answer_explanation_mode=%s" % payload.get(
            "answer_explanation_mode", "ordinary"))
        print("interaction_style_preference=%s" % payload.get(
            "interaction_style_preference", "batch"))
        print("interaction_style_effective=%s" % payload.get(
            "interaction_style_effective", "batch"))
        print("interaction_style_dormant=%s" % str(
            payload.get("interaction_style_dormant", False)).lower())
        if payload.get("interaction_style_dormant_reason"):
            print("interaction_style_dormant_reason=%s" % payload[
                "interaction_style_dormant_reason"])
        print("workspace=%s" % payload.get("workspace"))
        print("materials=%s" % payload.get("materials"))
    else:
        print("exam_start failed: %s" % payload.get("error", "unknown error"))


def _load_state_status(workspace):
    state_path = os.path.join(workspace, update_progress.STATE_NAME)
    values = {field: None for field in CHOICE_FIELDS}
    if not os.path.isdir(workspace):
        return {
            "ready": False,
            "reason": "workspace_missing",
            "missing": list(CHOICE_FIELDS),
            "invalid": {},
            "values": values,
        }
    if not os.path.isfile(state_path):
        return {
            "ready": False,
            "reason": "study_state_missing",
            "missing": list(CHOICE_FIELDS),
            "invalid": {},
            "values": values,
        }
    if is_link_or_reparse(state_path):
        return {
            "ready": False,
            "reason": "unsafe_study_state",
            "missing": list(CHOICE_FIELDS),
            "invalid": {},
            "values": values,
        }
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError):
        return {
            "ready": False,
            "reason": "study_state_unreadable",
            "missing": list(CHOICE_FIELDS),
            "invalid": {},
            "values": values,
        }
    if not isinstance(state, dict):
        return {
            "ready": False,
            "reason": "study_state_invalid",
            "missing": list(CHOICE_FIELDS),
            "invalid": {},
            "values": values,
        }
    values = {field: state.get(field) for field in CHOICE_FIELDS}
    missing = [field for field, value in values.items() if not value]
    allowed = {
        "mode": set(i18n.MODES),
        "time_budget": set(i18n.TIERS),
        "language": set(i18n.LANGS),
    }
    invalid = {
        field: value for field, value in values.items()
        if value and value not in allowed[field]
    }
    preferences = state.get("preferences")
    if preferences is not None and not isinstance(preferences, dict):
        invalid["preferences"] = type(preferences).__name__
    elif isinstance(preferences, dict):
        raw_interaction_style = preferences.get("interaction_style", "batch")
        if raw_interaction_style not in update_progress.INTERACTION_STYLES:
            invalid["interaction_style"] = raw_interaction_style
    interaction_style_preference = i18n.workspace_interaction_style_preference(state)
    interaction_style_effective = i18n.workspace_effective_interaction_style(state)
    interaction_style_dormant = i18n.workspace_interaction_style_dormant(state)
    interaction_style_dormant_reason = None
    if interaction_style_dormant:
        interaction_style_dormant_reason = (
            "processing_mode_not_full"
            if i18n.workspace_processing_mode(state) != "full"
            else "no_questions"
        )
    ready = not missing and not invalid
    return {
        "ready": ready,
        "reason": "ready" if ready else (
            "learning_choices_missing" if missing else "learning_choices_invalid"
        ),
        "missing": missing,
        "invalid": invalid,
        "values": values,
        "processing_mode": i18n.workspace_processing_mode(state),
        "artifact_mode_preference": i18n.workspace_artifact_mode(state),
        "artifact_mode_effective": i18n.workspace_effective_artifact_mode(state),
        "artifact_mode_dormant": i18n.workspace_artifact_mode_dormant(state),
        "answer_explanation_mode": i18n.workspace_answer_explanation_mode(state),
        "interaction_style_preference": interaction_style_preference,
        "interaction_style_effective": interaction_style_effective,
        "interaction_style_dormant": interaction_style_dormant,
        "interaction_style_dormant_reason": interaction_style_dormant_reason,
    }


def check_start_gate(workspace, materials):
    """Return a bounded, machine-readable decision without writing anything."""
    workspace_lexical = os.path.abspath(workspace)
    materials_lexical = os.path.abspath(materials)
    try:
        registry = update_progress.load_registry()
        confirmation = update_progress.workspace_confirmation_status(
            workspace_lexical, materials_lexical, registry=registry
        )
    except SystemExit as exc:
        confirmation = {
            "confirmed": False,
            "reason": "registry_unreadable",
            "workspace": workspace_lexical,
            "materials": materials_lexical,
            "exit_code": int(exc.code or 1),
        }
    except (OSError, TypeError, ValueError) as exc:
        confirmation = {
            "confirmed": False,
            "reason": "registry_unreadable",
            "workspace": workspace_lexical,
            "materials": materials_lexical,
            "error": str(exc),
        }
    workspace = _resolved_path(workspace_lexical)
    materials = _resolved_path(materials_lexical)
    choices = _load_state_status(workspace)
    runtime = _runtime_receipt_status(workspace)
    workspace_location = {
        "safe": not _path_is_same_or_inside(PACKAGE_ROOT, workspace),
        "reason": "outside_runtime_package",
    }
    if not workspace_location["safe"]:
        workspace_location["reason"] = "workspace_inside_runtime_package"
    blockers = []
    if not confirmation.get("confirmed"):
        blockers.append("workspace_confirmation")
    if not choices.get("ready"):
        blockers.append("learning_choices")
    if not runtime.get("verified"):
        blockers.append("runtime_provenance")
    if not workspace_location["safe"]:
        blockers.append("workspace_location")
    operation_errors = []
    if confirmation.get("reason") == "registry_unreadable":
        operation_errors.append("registry_unreadable")
    if runtime.get("reason") == "runtime_snapshot_failed":
        operation_errors.append("runtime_snapshot_failed")
    ready_to_start = not blockers
    processing_mode = choices.get("processing_mode") or "lightweight"
    artifact_mode_preference = choices.get("artifact_mode_preference") or "chat"
    artifact_mode_effective = choices.get("artifact_mode_effective") or "chat"
    artifact_mode_dormant = bool(choices.get("artifact_mode_dormant"))
    answer_explanation_mode = (
        choices.get("answer_explanation_mode") or "ordinary"
    )
    interaction_style_preference = choices.get(
        "interaction_style_preference") or "batch"
    interaction_style_effective = choices.get(
        "interaction_style_effective") or "batch"
    interaction_style_dormant = bool(choices.get("interaction_style_dormant"))
    interaction_style_dormant_reason = choices.get(
        "interaction_style_dormant_reason")
    ingestion_blockers = list(blockers)
    if processing_mode != "full":
        ingestion_blockers.append("processing_mode_lightweight")
    ready_to_ingest = ready_to_start and processing_mode == "full"
    if operation_errors:
        next_action = "repair_start_gate_operation"
    elif not ready_to_start:
        next_action = "exam_start confirm"
    elif processing_mode == "lightweight":
        next_action = "lightweight_session"
    else:
        next_action = "ingest_course"
    return {
        "process_success": not operation_errors,
        "workspace": workspace,
        "materials": materials,
        "workspace_confirmation": confirmation,
        "learning_choices": choices,
        "runtime_provenance": runtime,
        "workspace_location": workspace_location,
        "processing_mode": processing_mode,
        "artifact_mode_preference": artifact_mode_preference,
        "artifact_mode_effective": artifact_mode_effective,
        "artifact_mode_dormant": artifact_mode_dormant,
        "answer_explanation_mode": answer_explanation_mode,
        "interaction_style_preference": interaction_style_preference,
        "interaction_style_effective": interaction_style_effective,
        "interaction_style_dormant": interaction_style_dormant,
        "interaction_style_dormant_reason": interaction_style_dormant_reason,
        "start_permission": {
            "allowed": ready_to_start,
            "blockers": list(blockers),
        },
        "ingestion_permission": {
            "allowed": ready_to_ingest,
            "blockers": ingestion_blockers,
        },
        "operation_errors": operation_errors,
        "ready_to_start": ready_to_start,
        "ready_to_ingest": ready_to_ingest,
        "next_action": next_action,
    }


def check_registered_workspace_gate(workspace):
    """Resolve a workspace's confirmed materials pair and verify the same start gate.

    Artifact commands do not receive ``--materials``.  They must not therefore
    weaken the first-contact contract: the exact pair is recovered from the
    registry and rechecked, including learning choices and runtime provenance.
    The response is bounded even if a damaged registry contains duplicate rows.
    """
    workspace_lexical = os.path.abspath(workspace)
    workspace = _resolved_path(workspace_lexical)
    blocked = {
        "process_success": True,
        "workspace": workspace,
        "ready_to_use": False,
        "ready_to_ingest": False,
        "reason": "workspace_not_registered",
        "candidate_count": 0,
        "candidate_materials": [],
        "next_action": "exam_start confirm",
    }
    try:
        registry = update_progress.load_registry()
    except (SystemExit, OSError, TypeError, ValueError):
        blocked["process_success"] = False
        blocked["reason"] = "registry_unreadable"
        return blocked
    rows = [
        row for row in registry.get("workspaces", [])
        if isinstance(row, dict)
        and update_progress._same_canonical_path(row.get("path"), workspace)
        and isinstance(row.get("materials"), str)
        and row.get("materials")
    ]
    blocked["candidate_count"] = len(rows)
    blocked["candidate_materials"] = [
        os.path.abspath(row["materials"]) for row in rows[:RUNTIME_DIFF_LIMIT]
    ]
    if not rows:
        return blocked
    if len(rows) != 1:
        blocked["reason"] = "workspace_registration_ambiguous"
        blocked["candidate_materials_truncated"] = len(rows) > RUNTIME_DIFF_LIMIT
        return blocked
    attempts = []
    for row in rows[:RUNTIME_DIFF_LIMIT]:
        gate = check_start_gate(workspace_lexical, row["materials"])
        blocked.update({
            "processing_mode": gate.get("processing_mode", "lightweight"),
            "artifact_mode_preference": gate.get(
                "artifact_mode_preference", "chat"),
            "artifact_mode_effective": gate.get("artifact_mode_effective", "chat"),
            "artifact_mode_dormant": bool(gate.get("artifact_mode_dormant")),
            "answer_explanation_mode": gate.get(
                "answer_explanation_mode", "ordinary"),
            "interaction_style_preference": gate.get(
                "interaction_style_preference", "batch"),
            "interaction_style_effective": gate.get(
                "interaction_style_effective", "batch"),
            "interaction_style_dormant": bool(gate.get(
                "interaction_style_dormant")),
            "interaction_style_dormant_reason": gate.get(
                "interaction_style_dormant_reason"),
        })
        if gate.get("ready_to_ingest"):
            gate = dict(gate)
            gate["ready_to_use"] = True
            gate["registered_course"] = row.get("course")
            return gate
        attempts.append({
            "materials": os.path.abspath(row["materials"]),
            "blockers": list(
                (gate.get("ingestion_permission") or {}).get("blockers") or []
            ),
            "operation_errors": list(gate.get("operation_errors") or []),
        })
    blocked["reason"] = "registered_workspace_gate_blocked"
    blocked["attempts"] = attempts
    if any(attempt.get("operation_errors") for attempt in attempts):
        blocked["process_success"] = False
    blocked["candidate_materials_truncated"] = len(rows) > RUNTIME_DIFF_LIMIT
    return blocked


def require_full_processing(workspace, materials=None, purpose="full workspace publication"):
    """Require the current exact-pair gate and explicit ``processing_mode=full``.

    Lower-level builders/compilers pass ``materials`` and recheck that exact pair.
    Commands without a materials argument recover the sole registered pair instead.
    This function is read-only and returns the current gate for caller receipts.
    """

    workspace = os.path.abspath(workspace)
    gate = (
        check_start_gate(workspace, os.path.abspath(materials))
        if materials is not None
        else check_registered_workspace_gate(workspace)
    )
    if (gate.get("ready_to_ingest") is True
            and gate.get("processing_mode") == "full"):
        return gate
    blockers = list(
        (gate.get("ingestion_permission") or {}).get("blockers") or []
    )
    if (gate.get("processing_mode") != "full"
            and "processing_mode_lightweight" not in blockers):
        blockers.append("processing_mode_lightweight")
    reason = gate.get("reason") or "full_processing_gate_blocked"
    detail = ", ".join(str(value) for value in blockers[:RUNTIME_DIFF_LIMIT])
    raise FullProcessingRequired(
        "%s requires the exact confirmed workspace/materials pair, current runtime "
        "provenance, complete learning choices, and explicit processing_mode=full "
        "(reason=%s%s)"
        % (purpose, reason, "; blockers=" + detail if detail else ""),
        reason=reason,
        blockers=blockers,
    )


def _inside_materials(materials, workspace):
    return _path_is_same_or_inside(materials, workspace)


def _canonical_choices(args):
    missing = []
    inferred = []
    mode = args.mode
    time_budget = args.time_budget
    language = args.language
    if args.urgent:
        if mode is None:
            mode = "from_scratch"
            inferred.append("mode")
        if time_budget is None:
            time_budget = "le1d"
            inferred.append("time_budget")
        # The command cannot inspect the student's opening message.  Requiring
        # a caller-supplied language prevents an accidental bilingual inference.
        if language is None:
            missing.append("language")
    else:
        if mode is None:
            missing.append("mode")
        if time_budget is None:
            missing.append("time_budget")
        if language is None:
            missing.append("language")
    if missing:
        return None, missing, inferred, None

    canonical_mode, _implied_tier, mode_warning = i18n.canon_mode(mode)
    canonical_tier, tier_warning = i18n.canon_tier(time_budget)
    canonical_language, language_warning = i18n.canon_language(language)
    values = {
        "mode": canonical_mode,
        "time_budget": canonical_tier,
        "language": canonical_language,
    }
    invalid = {
        field: value for field, value, allowed in (
            ("mode", canonical_mode, i18n.MODES),
            ("time_budget", canonical_tier, i18n.TIERS),
            ("language", canonical_language, i18n.LANGS),
        ) if value not in allowed
    }
    warning = mode_warning or tier_warning or language_warning
    if args.urgent and canonical_tier != "le1d":
        invalid["time_budget"] = canonical_tier
        warning = "--urgent is only valid for the le1d time budget"
    return values, [], inferred, {"invalid": invalid, "warning": warning}


def _run_progress(arguments):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "update_progress.py")] + arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _recovery_start_preconditions(workspace, materials):
    try:
        registry = update_progress.load_registry()
        confirmation = update_progress.workspace_confirmation_status(
            workspace, materials, registry=registry
        )
    except (SystemExit, OSError, TypeError, ValueError) as exc:
        raise RuntimeReceiptError(
            "workspace registry is unavailable during material recovery"
        ) from exc
    if not confirmation.get("confirmed"):
        raise RuntimeReceiptError(
            "material recovery requires the already-confirmed workspace/materials pair"
        )
    choices = _load_state_status(workspace)
    if not choices.get("ready"):
        raise RuntimeReceiptError(
            "material recovery requires the existing canonical learning choices"
        )
    if _path_is_same_or_inside(PACKAGE_ROOT, workspace):
        raise RuntimeReceiptError("workspace is inside the installed runtime package")
    return confirmation, choices


def _recover_material_build(args):
    """Rebind runtime provenance without weakening a material blocker."""

    workspace_lexical = os.path.abspath(args.workspace)
    materials_lexical = os.path.abspath(args.materials)
    base = {
        "process_success": False,
        "ready_to_start": False,
        "ready_to_ingest": False,
        "workspace": workspace_lexical,
        "materials": materials_lexical,
        "action": args.action,
    }
    if not os.path.isdir(workspace_lexical):
        base["error"] = "workspace directory does not exist"
        return 2, base
    if not os.path.isdir(materials_lexical):
        base["error"] = "materials directory does not exist"
        return 2, base
    if (_path_has_link_or_reparse(workspace_lexical)
            or _path_has_link_or_reparse(materials_lexical)):
        base["error"] = "workspace/materials path must not be link-backed"
        return 2, base
    workspace = _resolved_path(workspace_lexical)
    materials = _resolved_path(materials_lexical)
    base["workspace"] = workspace
    base["materials"] = materials
    try:
        _recovery_start_preconditions(workspace, materials)
        pending_snapshot = _stable_pending_generation(
            workspace, require_complete_sources=False
        )
        if pending_snapshot is None:
            raise RuntimeReceiptError(
                "no pending material generation exists; ordinary confirm is sufficient"
            )
        recovery_target = _recovery_target(
            workspace, pending_snapshot, args.action
        )
        replacement_receipt = _build_runtime_receipt()
        replacement_bytes = _runtime_receipt_document_bytes(replacement_receipt)
        replacement_binding = {
            "path": RUNTIME_RECEIPT_NAME,
            "state": "valid",
            "sha256": hashlib.sha256(replacement_bytes).hexdigest(),
            "runtime_digest": replacement_receipt["runtime_digest"],
        }
        with workspace_publication_lock(
                workspace, allow_material_generation=True):
            locked_pending = _stable_pending_generation(
                workspace, require_complete_sources=False
            )
            if (locked_pending is None
                    or locked_pending["sha256"] != pending_snapshot["sha256"]
                    or locked_pending["document"] != pending_snapshot["document"]
                    or locked_pending["sources_complete"]
                    != pending_snapshot["sources_complete"]):
                raise ConflictError(
                    "pending material generation changed before recovery publication"
                )
            locked_target = _recovery_target(
                workspace, locked_pending, args.action
            )
            if (locked_target["pending_binding"]
                    != recovery_target["pending_binding"]
                    or locked_target["path"] != recovery_target["path"]):
                raise ConflictError(
                    "material recovery predecessor changed before publication"
                )
            confirmation, choices = _recovery_start_preconditions(
                workspace, materials
            )
            previous_binding = _runtime_binding_for_recovery(workspace)
            recovery = build_runtime_recovery_from_binding(
                locked_target["pending_binding"],
                args.action,
                previous_binding,
                replacement_binding,
                _utc_now(),
            )
            recovery_relative = locked_target["path"]
            recovery_path = safe_workspace_entry(workspace, recovery_relative)
            recovery_log = None
            if os.path.lexists(str(recovery_path)):
                if not os.path.isfile(recovery_path) or is_link_or_reparse(recovery_path):
                    raise RuntimeReceiptError("material recovery log is unsafe")
                log_payload, _log_snapshot = stable_read_bytes(recovery_path)
                recovery_log = _strict_json_payload(
                    log_payload, "material recovery log"
                )
                validate_runtime_recovery_log(recovery_log)
                latest = recovery_log["records"][-1]
                if (latest["authorization"]["authorization_id"]
                        == recovery["authorization"]["authorization_id"]
                        and latest["outcome"] is None):
                    recovery = None
                else:
                    recovery_log = expire_latest_runtime_recovery(recovery_log)
            if recovery is not None:
                recovery_log = append_runtime_recovery(recovery_log, recovery)
            store = IngestionStore(workspace)
            with store.ingest_transaction((
                    RUNTIME_RECEIPT_NAME, recovery_relative)):
                atomic_write_json(recovery_path, recovery_log)
                atomic_write_json(
                    safe_workspace_entry(workspace, RUNTIME_RECEIPT_NAME),
                    replacement_receipt,
                )
    except (ConflictError, OSError, RuntimeReceiptError, UnicodeDecodeError,
            TypeError, ValueError) as exc:
        base["error"] = "material build recovery failed: %s" % _bounded_text(exc)
        base["failed_step"] = "material_build_recovery"
        base["next_action"] = "inspect the pending generation and retry explicit recovery"
        return 1, base

    gate = check_start_gate(workspace, materials)
    base.update({
        "process_success": gate["ready_to_ingest"],
        "ready_to_ingest": gate["ready_to_ingest"],
        "generation_id": pending_snapshot["document"]["generation_id"],
        "authorization_generation_id": recovery_target["pending_binding"][
            "generation_id"
        ],
        "interrupted_successor": recovery_target["interrupted_successor"],
        "pending_sha256": pending_snapshot["sha256"],
        "recovery_record": recovery_relative,
        "workspace_confirmation": confirmation,
        "learning_choices": choices,
        "runtime_provenance": gate["runtime_provenance"],
        "runtime_receipt": _runtime_receipt_summary(replacement_receipt),
        "ingestion_permission": gate["ingestion_permission"],
        "next_action": "ingest_course",
    })
    if not gate["ready_to_ingest"]:
        base["error"] = (
            "runtime recovery was recorded, but the exact start gate changed; "
            "inspect status before retrying"
        )
        return 1, base
    return 0, base


def _confirm(args):
    workspace_lexical = os.path.abspath(args.workspace)
    materials_lexical = os.path.abspath(args.materials)
    base = {
        "process_success": False,
        "ready_to_start": False,
        "ready_to_ingest": False,
        "workspace": workspace_lexical,
        "materials": materials_lexical,
        "course": (args.course or "").strip(),
        "urgent": bool(args.urgent),
        "processing_mode_requested": args.processing_mode,
        "answer_explanation_mode_requested": args.answer_explanation_mode,
        "interaction_style_requested": args.interaction_style,
    }
    choices, missing, inferred, validation = _canonical_choices(args)
    if missing:
        base["error"] = "all three learning choices are required"
        base["missing_learning_choices"] = missing
        base["next_action"] = "confirm the missing choices and rerun exam_start confirm"
        return 2, base
    if validation["invalid"]:
        base["error"] = validation["warning"] or "invalid learning choice"
        base["invalid_learning_choices"] = validation["invalid"]
        return 2, base
    if not base["course"]:
        base["error"] = "course must not be empty"
        return 2, base
    if not os.path.isdir(materials_lexical):
        base["error"] = "materials directory does not exist"
        return 2, base
    if _path_has_link_or_reparse(materials_lexical):
        base["error"] = "materials path must not contain a symbolic link, junction, or reparse point"
        return 2, base
    if _inside_materials(materials_lexical, workspace_lexical):
        base["error"] = "workspace must not equal or live inside materials"
        return 2, base
    if _path_is_same_or_inside(PACKAGE_ROOT, workspace_lexical):
        base["error"] = "workspace must not equal or live inside the installed runtime package"
        return 2, base
    if os.path.lexists(workspace_lexical) and not os.path.isdir(workspace_lexical):
        base["error"] = "workspace exists and is not a directory"
        return 2, base
    if _path_has_link_or_reparse(workspace_lexical):
        base["error"] = "workspace path must not contain a symbolic link, junction, or reparse point"
        return 2, base
    try:
        os.makedirs(workspace_lexical, exist_ok=True)
    except OSError as exc:
        base["error"] = "workspace creation failed: %s" % exc
        return 1, base
    if _path_has_link_or_reparse(workspace_lexical):
        base["error"] = "workspace path became link-backed during creation"
        return 1, base

    workspace = _resolved_path(workspace_lexical)
    materials = _resolved_path(materials_lexical)
    base["workspace"] = workspace
    base["materials"] = materials

    try:
        light_session_path = safe_workspace_entry(
            workspace, ".lightweight/session.json"
        )
    except (OSError, TypeError, ValueError) as exc:
        base["error"] = "lightweight session path is unsafe: %s" % exc
        base["failed_step"] = "lightweight_pair_binding"
        return 2, base
    if os.path.lexists(str(light_session_path)):
        if (not os.path.isfile(light_session_path)
                or is_link_or_reparse(light_session_path)):
            base["error"] = "existing lightweight session ledger is unsafe"
            base["failed_step"] = "lightweight_pair_binding"
            return 2, base
        try:
            payload, _snapshot = stable_read_bytes(light_session_path)
            light_session = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            base["error"] = "existing lightweight session ledger is invalid: %s" % exc
            base["failed_step"] = "lightweight_pair_binding"
            return 2, base
        recorded_workspace = light_session.get("workspace") \
            if isinstance(light_session, dict) else None
        recorded_materials = light_session.get("materials") \
            if isinstance(light_session, dict) else None
        if (not isinstance(recorded_workspace, str)
                or not isinstance(recorded_materials, str)):
            base["error"] = "existing lightweight session has no canonical path pair"
            base["failed_step"] = "lightweight_pair_binding"
            return 2, base
        if (_resolved_path(recorded_workspace) != workspace
                or _resolved_path(recorded_materials) != materials):
            base["error"] = (
                "this workspace already contains lightweight history for a different "
                "materials root; choose a new workspace so old evidence remains auditable"
            )
            base["failed_step"] = "lightweight_pair_binding"
            base["next_action"] = "confirm the new materials with a new workspace path"
            return 2, base

    try:
        pending_snapshot = _stable_pending_generation(
            workspace, require_complete_sources=False
        )
    except (OSError, RuntimeReceiptError, TypeError, ValueError) as exc:
        base["error"] = "pending material generation is invalid: %s" % _bounded_text(exc)
        base["failed_step"] = "material_build_recovery"
        return 1, base
    if pending_snapshot is not None:
        base["error"] = (
            "ordinary confirm cannot replace runtime provenance while a material "
            "generation is pending"
        )
        base["failed_step"] = "material_build_recovery"
        base["generation_id"] = pending_snapshot["document"]["generation_id"]
        base["next_action"] = (
            "exam_start recover-material-build --action resume|supersede"
        )
        return 2, base

    state_path = os.path.join(workspace, update_progress.STATE_NAME)
    if not os.path.isfile(state_path):
        initialized = _run_progress(["--workspace", workspace, "init"])
        if initialized.returncode != 0:
            base["error"] = (initialized.stderr or initialized.stdout).strip()
            base["failed_step"] = "study_state_init"
            return initialized.returncode or 1, base

    set_args = [
        "--workspace", workspace, "set",
        "--mode", choices["mode"],
        "--time-budget", choices["time_budget"],
        "--language", choices["language"],
    ]
    # Reconfirmation is not an implicit processing-mode switch.  Existing state
    # retains its choice; a newly initialized state already defaults safely to
    # lightweight.  Only an explicit flag changes this independent preference.
    if args.processing_mode is not None:
        set_args.extend(("--processing-mode", args.processing_mode))
    if args.artifact_mode is not None:
        set_args.extend(("--artifact-mode", args.artifact_mode))
    if args.answer_explanation_mode is not None:
        set_args.extend((
            "--answer-explanation-mode", args.answer_explanation_mode,
        ))
    if args.interaction_style is not None:
        set_args.extend(("--interaction-style", args.interaction_style))
    selected = _run_progress(set_args)
    if selected.returncode != 0:
        base["error"] = (selected.stderr or selected.stdout).strip()
        base["failed_step"] = "learning_choices"
        return selected.returncode or 1, base

    register_args = [
        "workspace-register",
        "--course", base["course"],
        "--path", workspace,
        "--materials", materials,
        "--confirmed",
    ]
    if args.urgent:
        register_args.append("--urgent")
    registered = _run_progress(register_args)
    if registered.returncode != 0:
        base["error"] = (registered.stderr or registered.stdout).strip()
        base["failed_step"] = "workspace_registration"
        return registered.returncode or 1, base

    try:
        runtime_receipt = _write_runtime_receipt(workspace)
    except (OSError, RuntimeReceiptError, UnicodeDecodeError, ValueError) as exc:
        base["error"] = "runtime provenance receipt failed: %s" % _bounded_text(exc)
        base["failed_step"] = "runtime_provenance"
        return 1, base

    gate = check_start_gate(workspace, materials)
    base.update({
        "process_success": gate["ready_to_start"],
        "ready_to_start": gate["ready_to_start"],
        "ready_to_ingest": gate["ready_to_ingest"],
        "processing_mode": gate["processing_mode"],
        "artifact_mode_preference": gate["artifact_mode_preference"],
        "artifact_mode_effective": gate["artifact_mode_effective"],
        "artifact_mode_dormant": gate["artifact_mode_dormant"],
        "answer_explanation_mode": gate["answer_explanation_mode"],
        "interaction_style_preference": gate["interaction_style_preference"],
        "interaction_style_effective": gate["interaction_style_effective"],
        "interaction_style_dormant": gate["interaction_style_dormant"],
        "interaction_style_dormant_reason": gate[
            "interaction_style_dormant_reason"],
        "learning_choices": choices,
        "inferred_learning_choices": inferred,
        "workspace_confirmation": gate["workspace_confirmation"],
        "runtime_provenance": gate["runtime_provenance"],
        "runtime_receipt": _runtime_receipt_summary(runtime_receipt),
        "ingestion_permission": gate["ingestion_permission"],
        "next_action": gate["next_action"],
    })
    if not gate["ready_to_start"]:
        base["error"] = "confirmation completed but the start gate remains blocked"
        return 1, base
    return 0, base


def build_parser():
    parser = argparse.ArgumentParser(description="Exam workspace first-contact gate")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="read-only exact-pair and learning-choice check")
    status.add_argument("--workspace", required=True)
    status.add_argument("--materials", required=True)
    status.add_argument("--json", action="store_true", dest="as_json")

    confirm = sub.add_parser(
        "confirm",
        help="create/register the confirmed workspace and persist all three choices",
    )
    confirm.add_argument("--course", required=True)
    confirm.add_argument("--workspace", required=True)
    confirm.add_argument("--materials", required=True)
    confirm.add_argument("--mode")
    confirm.add_argument("--time-budget", dest="time_budget")
    confirm.add_argument("--language")
    confirm.add_argument(
        "--processing-mode", choices=i18n.PROCESSING_MODES,
        default=None,
        help=("explicit material-processing choice; omitted reconfirmations preserve "
              "the existing value, while a newly initialized workspace defaults to "
              "lightweight"),
    )
    confirm.add_argument("--artifact-mode", choices=i18n.ARTIFACT_MODES, default=None)
    confirm.add_argument(
        "--answer-explanation-mode",
        choices=i18n.ANSWER_EXPLANATION_MODES,
        default=None,
        help=("explicit per-item explanation choice; omitted reconfirmations "
              "preserve the existing value, and new/legacy/invalid state "
              "defaults safely to ordinary"),
    )
    confirm.add_argument(
        "--interaction-style", choices=update_progress.INTERACTION_STYLES,
        default=None,
        help=("optional full-mode teaching cadence; omission preserves the existing "
              "value, while new and missing legacy state use batch"),
    )
    confirm.add_argument(
        "--urgent", action="store_true",
        help="explicitly apply the <=1-day exception; defaults mode/time only",
    )
    confirm.add_argument("--json", action="store_true", dest="as_json")

    recover = sub.add_parser(
        "recover-material-build",
        help=(
            "explicitly rebind runtime provenance for one exact pending material "
            "generation"
        ),
    )
    recover.add_argument("--workspace", required=True)
    recover.add_argument("--materials", required=True)
    recover.add_argument(
        "--action", choices=("resume", "supersede"), required=True,
        help=(
            "resume compiles the bound generation; supersede abandons it only "
            "when a successor is atomically published"
        ),
    )
    recover.add_argument("--json", action="store_true", dest="as_json")
    return parser


def run(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "status":
        payload = check_start_gate(args.workspace, args.materials)
        _emit(payload, args.as_json)
        return 0 if payload.get("process_success") else 1
    if args.command == "recover-material-build":
        code, payload = _recover_material_build(args)
    else:
        code, payload = _confirm(args)
    _emit(payload, args.as_json)
    return code


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(run())
