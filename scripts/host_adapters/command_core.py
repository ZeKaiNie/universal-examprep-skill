#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict subprocess boundary for optional workflow hosts.

The commands and workspace receipts remain authoritative.  This adapter merely
uses an allowlisted, shell-free argv surface and validates that process exits
agree with each command's JSON payload before a host routes the next step.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager

try:
    from .. import strict_json
    from ..ingestion.identifiers import (
        is_link_or_reparse, normalize_workspace_path, safe_workspace_entry,
    )
except (ImportError, ValueError):  # imported with scripts/ directly on sys.path
    import strict_json
    from ingestion.identifiers import (
        is_link_or_reparse, normalize_workspace_path, safe_workspace_entry,
    )


SCHEMA_VERSION = 1
SCRIPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READINESS = frozenset(("ready", "usable_with_gaps", "blocked"))
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_MAX_FILES = 20000
SNAPSHOT_MAX_PATHS = 50000
SNAPSHOT_MAX_BYTES = 50 * 1024 * 1024 * 1024
SNAPSHOT_MAX_PATH_BYTES = 4096
SNAPSHOT_MAX_TOTAL_PATH_BYTES = 64 * 1024 * 1024
PROGRESS_ONLY_DEPENDENCIES = frozenset(("study_state.json", "study_progress.md"))
WORKSPACE_LOCK_DEPENDENCIES = frozenset((
    ".ingest/mutation.lock", ".study_state.lock",
))
HOST_VALIDATION_MAX_ITEMS = 400
HOST_VALIDATION_MAX_JSON_BYTES = 4 * 1024 * 1024

COMMAND_SPECS = {
    "exam_start.status": {"script": "exam_start.py", "exits": frozenset((0, 1))},
    "exam_start.confirm": {"script": "exam_start.py", "exits": frozenset((0, 1, 2))},
    "ingest_course": {"script": "ingest_course.py", "exits": frozenset((0, 2, 10))},
    "validate_workspace": {"script": "validate_workspace.py", "exits": frozenset((0, 1, 2))},
    "ingest_review.list": {"script": "ingest_review.py", "exits": frozenset((0,))},
    "update_progress.show": {"script": "update_progress.py", "exits": frozenset((0,))},
}


class CommandCoreError(RuntimeError):
    """A host command was unsafe, malformed, or internally inconsistent."""


def _sha256_text(value):
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _canonical_sha256(value):
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CommandCoreError("value is not canonical JSON: %s" % exc) from exc
    return hashlib.sha256(payload).hexdigest()


def validation_binding(payload, chapter, content_sha256):
    """Bind one validator payload to its chapter, content, and warnings."""

    if not isinstance(payload, dict):
        raise CommandCoreError("validation binding payload must be an object")
    if type(chapter) is not int or chapter < 1:
        raise CommandCoreError("validation binding chapter must be a positive integer")
    if (not isinstance(content_sha256, str) or len(content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in content_sha256)):
        raise CommandCoreError("validation binding content_sha256 is invalid")
    dependency_snapshot = payload.get("dependency_snapshot")
    if (not isinstance(dependency_snapshot, dict)
            or dependency_snapshot.get("content_sha256") != content_sha256):
        raise CommandCoreError(
            "validation binding content digest disagrees with its dependency receipt")
    dependency_sha256 = dependency_snapshot.get("snapshot_sha256")
    if (not isinstance(dependency_sha256, str) or len(dependency_sha256) != 64
            or any(char not in "0123456789abcdef" for char in dependency_sha256)):
        raise CommandCoreError("validation binding dependency digest is invalid")
    gate_view = {
        key: payload.get(key) for key in (
            "exit_code", "readiness", "capabilities", "error_count",
            "error_summary", "errors", "warning_count", "warning_summary",
            "warnings", "truncated",
        )
    }
    warning_view = {
        "warning_count": payload.get("warning_count"),
        "warning_summary": payload.get("warning_summary"),
        "warnings": payload.get("warnings"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "chapter": chapter,
        "content_sha256": content_sha256,
        "dependency_snapshot_sha256": dependency_sha256,
        "validation_sha256": _canonical_sha256(gate_view),
        "warning_sha256": _canonical_sha256(warning_view),
    }


def _stat_identity(value):
    identity = (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns,
    )
    # Windows can lazily advance creation/change metadata after a just-closed
    # writer even when the file's identity and contents are unchanged.
    return identity if os.name == "nt" else identity + (value.st_ctime_ns,)


def _stable_regular_file(path, capture_limit=None):
    """Read one no-follow regular file and prove its path stayed bound."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        path_before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(path_before.st_mode) or is_link_or_reparse(path):
            raise CommandCoreError("dependency is not a no-follow regular file: %s" % path)
        descriptor = os.open(path, flags)
        handle_before = os.fstat(descriptor)
        if not stat.S_ISREG(handle_before.st_mode):
            raise CommandCoreError("dependency handle is not a regular file: %s" % path)
        digest = hashlib.sha256()
        captured = [] if capture_limit is not None else None
        captured_bytes = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            if captured is not None:
                captured_bytes += len(block)
                if captured_bytes > capture_limit:
                    raise CommandCoreError(
                        "dependency exceeds its bounded read limit: %s" % path)
                captured.append(block)
        handle_after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
    except CommandCoreError:
        raise
    except OSError as exc:
        raise CommandCoreError("cannot hash dependency %s: %s" % (path, exc)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identities = {
        _stat_identity(path_before), _stat_identity(handle_before),
        _stat_identity(handle_after),
        _stat_identity(path_after),
    }
    if len(identities) != 1 or is_link_or_reparse(path):
        raise CommandCoreError("dependency changed while being hashed: %s" % path)
    return digest.hexdigest(), path_after, (
        b"".join(captured) if captured is not None else None)


def _stable_regular_file_sha256(path):
    digest, file_stat, unused_payload = _stable_regular_file(path)
    return digest, file_stat


def _stable_regular_file_bytes(path, limit):
    unused_digest, file_stat, payload = _stable_regular_file(
        path, capture_limit=limit)
    return payload, file_stat


def _snapshot_source_root(workspace):
    try:
        path = os.fspath(safe_workspace_entry(workspace, ".ingest/build_manifest.json"))
    except ValueError:
        return None
    if not os.path.isfile(path) or is_link_or_reparse(path):
        return None
    try:
        raw, unused_stat = _stable_regular_file_bytes(path, 16 * 1024 * 1024)
        manifest = strict_json.loads(raw.decode("utf-8"))
    except CommandCoreError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError):
        return None
    value = manifest.get("source_root") if isinstance(manifest, dict) else None
    if not isinstance(value, str) or not os.path.isabs(value) or not os.path.isdir(value):
        return None
    return os.path.abspath(value)


def collect_dependency_snapshot(workspace):
    """Hash the bounded regular-file trees the validator may read."""

    workspace = _absolute(workspace, "workspace")
    if not os.path.isdir(workspace) or is_link_or_reparse(workspace):
        raise CommandCoreError("dependency snapshot workspace is unsafe")
    roots = [{"name": "workspace", "path": workspace, "excluded_paths": []}]
    source_root = _snapshot_source_root(workspace)
    if source_root is not None:
        workspace_real = os.path.realpath(workspace)
        source_real = os.path.realpath(source_root)
        try:
            already_covered = os.path.commonpath((workspace_real, source_real)) == workspace_real
        except ValueError:
            already_covered = False
        if not already_covered:
            try:
                source_contains_workspace = (
                    os.path.commonpath((source_real, workspace_real)) == source_real)
            except ValueError:
                source_contains_workspace = False
            roots.append({
                "name": "source_root",
                "path": source_root,
                "excluded_paths": [workspace] if source_contains_workspace else [],
            })

    records = []
    directories = []
    total_bytes = 0
    total_path_bytes = 0
    path_count = 0

    def walk_error(exc):
        raise CommandCoreError("cannot enumerate dependency tree: %s" % exc)

    for root in roots:
        root_path_bytes = len(root["path"].encode("utf-8", errors="surrogatepass"))
        if (root_path_bytes > SNAPSHOT_MAX_PATH_BYTES
                or not os.path.isdir(root["path"])
                or is_link_or_reparse(root["path"])):
            raise CommandCoreError("dependency snapshot root is unsafe or over-bound")
        for directory, names, files in os.walk(
                root["path"], topdown=True, onerror=walk_error, followlinks=False):
            names.sort()
            files.sort()
            retained_names = []
            for name in names:
                candidate = os.path.join(directory, name)
                candidate_absolute = os.path.abspath(candidate)
                if any(os.path.normcase(candidate_absolute) == os.path.normcase(excluded)
                       for excluded in root["excluded_paths"]):
                    continue
                if is_link_or_reparse(candidate):
                    raise CommandCoreError(
                        "dependency snapshot contains an unsafe directory: %s" % candidate)
                try:
                    relative = normalize_workspace_path(os.path.relpath(
                        candidate, root["path"]).replace(os.sep, "/"))
                except ValueError as exc:
                    raise CommandCoreError(
                        "dependency snapshot contains an unsafe path: %s" % candidate
                    ) from exc
                if (root["name"] == "workspace"
                        and relative == ".ingest/transactions"):
                    continue
                relative_bytes = len(relative.encode(
                    "utf-8", errors="surrogatepass"))
                path_count += 1
                total_path_bytes += relative_bytes
                if (relative_bytes > SNAPSHOT_MAX_PATH_BYTES
                        or path_count > SNAPSHOT_MAX_PATHS
                        or total_path_bytes > SNAPSHOT_MAX_TOTAL_PATH_BYTES):
                    raise CommandCoreError(
                        "dependency snapshot path data exceeds its bounded limits")
                directories.append({"root": root["name"], "path": relative})
                retained_names.append(name)
            names[:] = retained_names
            for name in files:
                candidate = os.path.join(directory, name)
                try:
                    relative = normalize_workspace_path(os.path.relpath(
                        candidate, root["path"]).replace(os.sep, "/"))
                except ValueError as exc:
                    raise CommandCoreError(
                        "dependency snapshot contains an unsafe path: %s" % candidate
                    ) from exc
                if (relative in WORKSPACE_LOCK_DEPENDENCIES
                        or relative.startswith(".ingest/transactions/")
                        or relative.endswith((".tmp", ".log"))):
                    continue
                relative_bytes = len(relative.encode("utf-8", errors="surrogatepass"))
                path_count += 1
                total_path_bytes += relative_bytes
                if (relative_bytes > SNAPSHOT_MAX_PATH_BYTES
                        or path_count > SNAPSHOT_MAX_PATHS
                        or total_path_bytes > SNAPSHOT_MAX_TOTAL_PATH_BYTES):
                    raise CommandCoreError(
                        "dependency snapshot path data exceeds its bounded limits")
                if len(records) >= SNAPSHOT_MAX_FILES:
                    raise CommandCoreError(
                        "dependency snapshot file count exceeds its bounded limit")
                try:
                    declared = os.stat(candidate, follow_symlinks=False)
                except OSError as exc:
                    raise CommandCoreError(
                        "cannot stat dependency %s: %s" % (candidate, exc)) from exc
                if (not stat.S_ISREG(declared.st_mode) or os.path.islink(candidate)
                        or is_link_or_reparse(candidate)):
                    raise CommandCoreError(
                        "dependency snapshot contains an unsafe file: %s" % candidate)
                if total_bytes + declared.st_size > SNAPSHOT_MAX_BYTES:
                    raise CommandCoreError(
                        "dependency snapshot byte size exceeds its bounded limit")
                digest, file_stat = _stable_regular_file_sha256(candidate)
                if _stat_identity(declared) != _stat_identity(file_stat):
                    raise CommandCoreError(
                        "dependency changed before its stable read: %s" % candidate)
                total_bytes += file_stat.st_size
                records.append({
                    "root": root["name"], "path": relative,
                    "size_bytes": file_stat.st_size, "sha256": digest,
                })
    if _snapshot_source_root(workspace) != source_root:
        raise CommandCoreError(
            "dependency source root changed while snapshot was being collected")
    basis = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "algorithm": "sha256-tree-v1",
        "roots": roots,
        "directories": directories,
        "records": records,
        "directory_count": len(directories),
        "file_count": len(records),
        "total_bytes": total_bytes,
    }
    content_records = [
        row for row in records
        if not (row["root"] == "workspace"
                and row["path"] in PROGRESS_ONLY_DEPENDENCIES)
    ]
    content_basis = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "algorithm": "sha256-tree-content-v1",
        "roots": roots,
        "directories": directories,
        "records": content_records,
        "directory_count": len(directories),
        "file_count": len(content_records),
        "total_bytes": sum(row["size_bytes"] for row in content_records),
    }
    return dict(
        basis,
        snapshot_sha256=_canonical_sha256(basis),
        content_sha256=_canonical_sha256(content_basis),
    )


def dependency_snapshot_receipt(snapshot):
    """Return the bounded public receipt without exposing dependency paths."""

    return {
        "schema_version": snapshot["schema_version"],
        "algorithm": snapshot["algorithm"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "content_sha256": snapshot["content_sha256"],
        "root_count": len(snapshot["roots"]),
        "directory_count": snapshot["directory_count"],
        "file_count": snapshot["file_count"],
        "total_bytes": snapshot["total_bytes"],
    }


def assert_dependency_snapshot_current(document, workspace):
    expected = {
        "schema_version", "algorithm", "snapshot_sha256", "content_sha256", "root_count",
        "directory_count", "file_count", "total_bytes",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise CommandCoreError("validator dependency snapshot schema is invalid")
    if (document.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
            or document.get("algorithm") != "sha256-tree-v1"
            or type(document.get("root_count")) is not int
            or type(document.get("directory_count")) is not int
            or type(document.get("file_count")) is not int
            or type(document.get("total_bytes")) is not int
            or document.get("root_count") not in (1, 2)
            or not 0 <= document.get("directory_count") <= SNAPSHOT_MAX_PATHS
            or not 0 <= document.get("file_count") <= SNAPSHOT_MAX_FILES
            or document.get("directory_count") + document.get(
                "file_count") > SNAPSHOT_MAX_PATHS
            or not 0 <= document.get("total_bytes") <= SNAPSHOT_MAX_BYTES
            or not isinstance(document.get("snapshot_sha256"), str)
            or len(document.get("snapshot_sha256")) != 64
            or any(character not in "0123456789abcdef"
                   for character in document.get("snapshot_sha256"))
            or not isinstance(document.get("content_sha256"), str)
            or len(document.get("content_sha256")) != 64
            or any(character not in "0123456789abcdef"
                   for character in document.get("content_sha256"))):
        raise CommandCoreError("validator dependency snapshot receipt is invalid")
    current = collect_dependency_snapshot(workspace)
    if dependency_snapshot_receipt(current) != document:
        raise CommandCoreError("workspace dependencies changed across validation binding")
    return current


def completion_binding(validation_receipt, progress_receipt, dependency_snapshot):
    """Bind validator and progress receipts to one post-checked tree digest."""

    if (not isinstance(validation_receipt, dict)
            or not isinstance(progress_receipt, dict)
            or not isinstance(dependency_snapshot, dict)):
        raise CommandCoreError("completion binding inputs must be objects")
    validation_payload = validation_receipt.get("payload")
    if (not isinstance(validation_payload, dict)
            or validation_payload.get("dependency_snapshot") != dependency_snapshot):
        raise CommandCoreError(
            "completion validation does not bind the dependency snapshot")
    snapshot_sha256 = dependency_snapshot.get("snapshot_sha256")
    if (not isinstance(snapshot_sha256, str) or len(snapshot_sha256) != 64
            or any(character not in "0123456789abcdef"
                   for character in snapshot_sha256)):
        raise CommandCoreError("completion dependency digest is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "dependency_snapshot_sha256": snapshot_sha256,
        "validation_receipt_sha256": _canonical_sha256(validation_receipt),
        "progress_receipt_sha256": _canonical_sha256(progress_receipt),
    }


def _absolute(value, label):
    if (not isinstance(value, str) or not value or "\x00" in value
            or not os.path.isabs(value)):
        raise CommandCoreError("%s must be an absolute path" % label)
    return os.path.abspath(value)


def _arguments(values):
    if not isinstance(values, (list, tuple)):
        raise CommandCoreError("command arguments must be an array")
    result = []
    for position, value in enumerate(values):
        if (not isinstance(value, str) or "\x00" in value
                or any(char in value for char in ("\r", "\n"))):
            raise CommandCoreError("argument %d must be a safe single-line string" % position)
        result.append(value)
    return result


def _require_bool(payload, key, label):
    if type(payload.get(key)) is not bool:
        raise CommandCoreError("%s JSON field %s must be boolean" % (label, key))


def _validate_payload(name, exit_code, payload):
    if not isinstance(payload, dict):
        raise CommandCoreError("%s must return a JSON object" % name)
    if name in ("exam_start.status", "exam_start.confirm"):
        _require_bool(payload, "process_success", name)
        _require_bool(payload, "ready_to_ingest", name)
        if exit_code != 0 and payload["process_success"]:
            raise CommandCoreError("%s nonzero exit contradicts process_success=true" % name)
        if name == "exam_start.confirm" and exit_code == 0 and not payload["ready_to_ingest"]:
            raise CommandCoreError("successful confirm did not open the ingestion gate")
    elif name == "ingest_course":
        _require_bool(payload, "process_success", name)
        readiness = payload.get("readiness")
        if readiness not in READINESS and readiness != "unknown":
            raise CommandCoreError("ingest_course returned invalid readiness")
        if exit_code == 0 and (not payload["process_success"]
                               or readiness not in ("ready", "usable_with_gaps")):
            raise CommandCoreError("ingest exit 0 contradicts its JSON outcome")
        if exit_code == 10 and (not payload["process_success"] or readiness != "blocked"):
            raise CommandCoreError("ingest exit 10 must mean successful blocked readiness")
        if exit_code not in (0, 10) and payload["process_success"]:
            raise CommandCoreError("ingest operation failure claims process success")
    elif name == "validate_workspace":
        declared = payload.get("exit_code")
        if type(declared) is not int or declared != exit_code:
            raise CommandCoreError("validator process/JSON exit codes disagree")
        if payload.get("readiness") not in READINESS:
            raise CommandCoreError("validator returned invalid readiness")
        if not isinstance(payload.get("capabilities"), dict):
            raise CommandCoreError("validator omitted capability readiness")
        chapter = payload["capabilities"].get("chapter")
        if type(chapter) is not int or chapter < 1:
            raise CommandCoreError("validator omitted its positive capability chapter")
        truncated = payload.get("truncated")
        if (not isinstance(truncated, dict)
                or truncated.get("errors") != 0
                or truncated.get("warnings") != 0):
            raise CommandCoreError(
                "validator host receipt is truncated; resolve the excess with direct "
                "validate_workspace --full/--details-file before host acknowledgement")
        for count_field, rows_field in (
                ("error_count", "errors"), ("warning_count", "warnings")):
            count = payload.get(count_field)
            rows = payload.get(rows_field)
            if (type(count) is not int or count < 0 or not isinstance(rows, list)
                    or count != len(rows)):
                raise CommandCoreError(
                    "validator complete %s receipt is inconsistent" % rows_field)
        if payload["error_count"] + payload["warning_count"] > HOST_VALIDATION_MAX_ITEMS:
            raise CommandCoreError(
                "validator host receipt exceeds the bounded item limit")
        encoded_payload = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
        if len(encoded_payload) > HOST_VALIDATION_MAX_JSON_BYTES:
            raise CommandCoreError(
                "validator host receipt exceeds the bounded JSON limit")
    elif name == "ingest_review.list":
        for field in ("count", "returned", "cursor"):
            if type(payload.get(field)) is not int or payload[field] < 0:
                raise CommandCoreError("review list field %s must be a non-negative integer" % field)
        if not isinstance(payload.get("summary"), dict):
            raise CommandCoreError("review list omitted its summary")
    elif name == "update_progress.show":
        # The state object has its own validator in update_progress.py.  The host
        # only needs a JSON object here and must not create a second state schema.
        if not payload:
            raise CommandCoreError("progress show returned an empty state object")


def run_json_command(name, arguments, timeout=300, runner=None):
    """Run one allowlisted script with no shell and validate its JSON receipt."""

    spec = COMMAND_SPECS.get(name)
    if spec is None:
        raise CommandCoreError("command is not allowlisted: %s" % name)
    argv = [sys.executable, os.path.join(SCRIPT_ROOT, spec["script"])] + _arguments(arguments)
    invoke = runner or subprocess.run
    try:
        completed = invoke(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CommandCoreError("%s could not run: %s" % (name, exc)) from exc
    exit_code = getattr(completed, "returncode", None)
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    if type(exit_code) is not int:
        raise CommandCoreError("%s runner omitted an integer return code" % name)
    if exit_code not in spec["exits"]:
        raise CommandCoreError("%s returned undocumented exit code %s" % (name, exit_code))
    try:
        payload = strict_json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise CommandCoreError("%s did not return strict JSON: %s" % (name, exc)) from exc
    _validate_payload(name, exit_code, payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": name,
        "argv": argv,
        "exit_code": exit_code,
        "payload": payload,
        "stdout_sha256": _sha256_text(stdout),
        "stderr_sha256": _sha256_text(stderr),
        "stderr_excerpt": stderr[:2000],
    }


def exam_start_status(workspace, materials, runner=None):
    workspace = _absolute(workspace, "workspace")
    materials = _absolute(materials, "materials")
    return run_json_command(
        "exam_start.status",
        ["status", "--workspace", workspace, "--materials", materials, "--json"],
        runner=runner,
    )


def exam_start_confirm(workspace, materials, course, mode, time_budget, language,
                       artifact_mode=None, urgent=False, runner=None):
    workspace = _absolute(workspace, "workspace")
    materials = _absolute(materials, "materials")
    values = {"course": course, "mode": mode, "time_budget": time_budget,
              "language": language}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise CommandCoreError("%s must be a non-empty string" % key)
    argv = [
        "confirm", "--course", course, "--workspace", workspace,
        "--materials", materials, "--mode", mode,
        "--time-budget", time_budget, "--language", language,
    ]
    if artifact_mode is not None:
        if artifact_mode not in ("chat", "visual"):
            raise CommandCoreError("artifact_mode must be chat or visual")
        argv.extend(("--artifact-mode", artifact_mode))
    if urgent:
        argv.append("--urgent")
    argv.append("--json")
    return run_json_command("exam_start.confirm", argv, runner=runner)


def ingest_course(workspace, materials, runner=None):
    workspace = _absolute(workspace, "workspace")
    materials = _absolute(materials, "materials")
    return run_json_command(
        "ingest_course",
        ["--materials", materials, "--workspace", workspace, "--json"],
        runner=runner,
    )


def validate_workspace(workspace, chapter=None, runner=None):
    workspace = _absolute(workspace, "workspace")
    argv = [
        workspace, "--json", "--dependency-snapshot", "--max-items", "200",
    ]
    if chapter is not None:
        if type(chapter) is not int or chapter < 1:
            raise CommandCoreError("chapter must be a positive integer")
        argv.extend(("--chapter", str(chapter)))
    snapshot_before = dependency_snapshot_receipt(
        collect_dependency_snapshot(workspace))
    receipt = run_json_command("validate_workspace", argv, runner=runner)
    actual_chapter = receipt["payload"]["capabilities"]["chapter"]
    if chapter is not None and actual_chapter != chapter:
        raise CommandCoreError("validator capability chapter disagrees with requested chapter")
    snapshot_document = receipt["payload"].get("dependency_snapshot")
    assert_dependency_snapshot_current(snapshot_document, workspace)
    if snapshot_before != snapshot_document:
        raise CommandCoreError(
            "workspace dependencies changed while validate_workspace was running")
    receipt["binding"] = validation_binding(
        receipt["payload"], actual_chapter, snapshot_document["content_sha256"])
    return receipt


def review_list(workspace, statuses=None, summary_only=True, runner=None):
    workspace = _absolute(workspace, "workspace")
    argv = ["--workspace", workspace, "--json", "list", "--limit", "50"]
    for status in statuses or ():
        if not isinstance(status, str) or not status:
            raise CommandCoreError("review status must be a non-empty string")
        argv.extend(("--status", status))
    if summary_only:
        argv.append("--summary-only")
    return run_json_command("ingest_review.list", argv, runner=runner)


def progress_show(workspace, runner=None):
    workspace = _absolute(workspace, "workspace")
    receipt = run_json_command(
        "update_progress.show", ["--workspace", workspace, "show"], runner=runner)
    try:
        from ..ingestion import stable_read_bytes
    except (ImportError, ValueError):
        from ingestion import stable_read_bytes
    state_path = safe_workspace_entry(workspace, "study_state.json")
    try:
        payload, snapshot = stable_read_bytes(state_path)
        state = strict_json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise CommandCoreError("cannot bind progress receipt to study_state.json: %s" % exc) from exc
    if state != receipt["payload"]:
        raise CommandCoreError("progress payload disagrees with the stable study_state.json")
    receipt["state_binding"] = {
        "schema_version": SCHEMA_VERSION,
        "path": "study_state.json",
        "sha256": snapshot["sha256"],
        "size_bytes": snapshot["size_bytes"],
    }
    return receipt


@contextmanager
def _completion_guard(workspace):
    try:
        from ..ingestion import workspace_publication_lock
    except (ImportError, ValueError):
        from ingestion import workspace_publication_lock
    with workspace_publication_lock(workspace):
        yield


def completion_snapshot(workspace, chapter=None, runner=None):
    """Read validation and progress from one locked, post-checked dependency state."""

    workspace = _absolute(workspace, "workspace")
    with _completion_guard(workspace):
        baseline = collect_dependency_snapshot(workspace)
        validation_receipt = validate_workspace(
            workspace, chapter=chapter, runner=runner)
        dependency_snapshot = validation_receipt["payload"]["dependency_snapshot"]
        if dependency_snapshot_receipt(baseline) != dependency_snapshot:
            raise CommandCoreError(
                "workspace dependencies changed before completion validation")
        progress_receipt = progress_show(workspace, runner=runner)
        state_record = next((row for row in baseline["records"]
                             if row["root"] == "workspace"
                             and row["path"] == "study_state.json"), None)
        state_binding = progress_receipt.get("state_binding") or {}
        if (state_record is None
                or state_binding.get("sha256") != state_record.get("sha256")
                or state_binding.get("size_bytes") != state_record.get("size_bytes")):
            raise CommandCoreError(
                "progress state does not belong to the validated dependency snapshot")
        assert_dependency_snapshot_current(dependency_snapshot, workspace)
        return {
            "schema_version": SCHEMA_VERSION,
            "validation_receipt": validation_receipt,
            "progress_receipt": progress_receipt,
            "dependency_snapshot": dependency_snapshot,
            "binding": completion_binding(
                validation_receipt, progress_receipt, dependency_snapshot),
        }


__all__ = [
    "COMMAND_SPECS", "CommandCoreError", "assert_dependency_snapshot_current",
    "collect_dependency_snapshot", "completion_binding", "completion_snapshot",
    "dependency_snapshot_receipt", "exam_start_confirm", "exam_start_status",
    "ingest_course", "progress_show", "review_list", "run_json_command",
    "validate_workspace", "validation_binding",
]
