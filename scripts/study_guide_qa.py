#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fail-closed visual QA for an already generated chapter Study Guide PDF.

The core imports only the Python standard library.  PDF backends are discovered
and imported lazily when ``render`` runs:

    python scripts/study_guide_qa.py --workspace <ws> --chapter 1 render
    python scripts/study_guide_qa.py --workspace <ws> --chapter 1 accept \
        --inspected-pages all --reviewer codex --reviewer-kind agent

``render`` is machine lint, not visual acceptance.  It renders every PDF page
to ``study_guide/qa/chNN_pNNN.png`` and records immutable page evidence in the
chapter receipt.  ``accept`` verifies all hashes again and requires an explicit
all-pages inspection attestation before changing ``visual_qa.status`` to
``ready``.
"""

import argparse
import datetime
import hashlib
import importlib
import io
import json
import math
import os
import re
import shutil
import stat
from pathlib import Path
import sys
import tempfile

try:  # package imports in readiness; script-directory imports in the CLI/tests
    from . import exam_start, i18n, study_guide_content
    from .ingestion import workspace_publication_lock
    from .image_validation import (
        ImageValidationError, PNG_SIGNATURE,
        png_dimensions as _shared_png_dimensions,
    )
except ImportError:  # pragma: no cover - standalone entrypoint path
    import exam_start
    import i18n
    import study_guide_content
    from ingestion import workspace_publication_lock
    from image_validation import (
        ImageValidationError, PNG_SIGNATURE,
        png_dimensions as _shared_png_dimensions,
    )


SCHEMA_VERSION = 1
ARTIFACT_RECEIPT_SCHEMA_VERSION = 2
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
RECEIPT_FIELDS = frozenset((
    "schema_version", "artifact_type", "chapter", "profile", "language",
    "content_manifest", "content_manifest_sha256", "expected_item_ids",
    "rendered_item_ids", "omitted_item_ids", "html_file", "html_sha256",
    "pdf_file", "pdf_sha256", "pdf_backend", "converter",
    "conversion_input_html_sha256", "conversion_started_at",
    "conversion_completed_at", "conversion_run_sha256", "preflight",
    "start_gate", "generated_at", "status", "visual_qa",
))
START_GATE_FIELDS = frozenset((
    "ready_to_use", "workspace", "materials", "registered_course",
    "runtime_digest", "runtime_file_count", "skill_version", "git_commit",
    "git_branch", "git_dirty", "python_executable",
))
MANUAL_REVIEW_CHECKS = (
    "all_prompt_images_diagrams_and_formulas_are_visible_and_readable",
    "no_content_is_clipped_overlapped_or_hidden_at_page_boundaries",
    "selected_language_and_required_translation_are_complete_and_natural",
    "no_orphan_heading_stranded_caption_or_excessive_blank_region_remains",
)
RAW_TEX_RE = re.compile(
    r"(?:\\[([]|\\(?:frac|sqrt|sum|int|prod|lim|begin|end|left|right|"
    r"operatorname|mathrm|mathbf|mathbb|cdot|times|cup|cap|notin|in|"
    r"leq|geq|alpha|beta|gamma|theta|lambda|sigma|mu)\b|"
    r"\${1,2}[^$\r\n]+\${1,2})"
)
QA_PAGE_RE = re.compile(r"^ch(?P<chapter>\d+)_p(?P<page>\d{3})\.png$")


class QAError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class PDFSnapshotDrift(QAError):
    """The PDF path or the exact bytes bound to this QA run changed."""


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value):
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QAError("receipt contains a non-canonical JSON value: %s" % exc)
    return _sha256_bytes(payload)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number: %s" % value)


def _strict_json_load(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(
                stream, object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise QAError("cannot read strict receipt JSON: %s" % exc)
    if not isinstance(value, dict):
        raise QAError("chapter receipt must be a JSON object")
    return value


def _parse_timestamp(value, label):
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise QAError("receipt %s is not a UTC timestamp" % label)
    try:
        return datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise QAError("receipt %s is not a valid timestamp" % label) from exc


def _safe_single_line(value, label):
    if (not isinstance(value, str) or not value.strip()
            or any(char in value for char in ("\x00", "\r", "\n"))):
        raise QAError("receipt %s must be a non-empty single-line string" % label)
    return value


def _id_list(value, label):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))):
        raise QAError("receipt %s must be a duplicate-free string list" % label)
    return value


def _is_within(root, path):
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def _guard_workspace(raw):
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise QAError("--workspace must be a non-empty path", 2)
    absolute = os.path.abspath(raw)
    if not os.path.isdir(absolute):
        raise QAError("workspace directory does not exist: %s" % absolute, 2)
    # The workspace itself is the trust root.  Reject it when that final directory entry is a
    # link/reparse point, then guard every path below it independently.  Comparing its absolute
    # path with ``realpath`` also inspects host-owned ancestors outside this trust root; Windows
    # CI runners (and legitimate mounted volumes) commonly use a junction there, which must not
    # make an otherwise real workspace unusable.
    try:
        workspace_stat = os.lstat(absolute)
    except OSError as exc:
        raise QAError("cannot stat workspace directory: %s" % exc, 2)
    if os.path.islink(absolute) or _is_reparse_stat(workspace_stat):
        raise QAError("workspace must not be a symlink/junction/reparse point")
    # From this point onward containment checks compare real child paths.  Canonicalize the
    # accepted trust root too, otherwise a host-owned ancestor junction puts root and children
    # in different path namespaces and every legitimate child appears to escape.
    return str(Path(absolute).resolve(strict=True))


def _guard_existing(root, path, label):
    absolute = os.path.abspath(path)
    if os.path.islink(absolute):
        raise QAError("%s must not be a symlink" % label)
    if not os.path.isfile(absolute):
        raise QAError("missing %s: %s" % (label, absolute))
    real = os.path.realpath(absolute)
    if not _is_within(root, real):
        raise QAError("%s escapes the workspace" % label)
    return real


def _guard_directory(root, path, label, create=False):
    absolute = os.path.abspath(path)
    if os.path.lexists(absolute):
        if os.path.islink(absolute):
            raise QAError("%s must not be a symlink" % label)
        if not os.path.isdir(absolute):
            raise QAError("%s must be a directory" % label)
    elif create:
        parent = os.path.dirname(absolute)
        if not os.path.isdir(parent) or os.path.islink(parent):
            raise QAError("parent of %s is missing or unsafe" % label)
        os.mkdir(absolute)
    else:
        raise QAError("missing %s: %s" % (label, absolute))
    real = os.path.realpath(absolute)
    if not _is_within(root, real):
        raise QAError("%s escapes the workspace" % label)
    return real


def _guard_output_file(root, path, label):
    absolute = os.path.abspath(path)
    if os.path.lexists(absolute):
        if os.path.islink(absolute):
            raise QAError("%s must not be a symlink" % label)
        if not os.path.isfile(absolute):
            raise QAError("%s must be a regular file" % label)
    parent = os.path.realpath(os.path.dirname(absolute))
    if not _is_within(root, parent):
        raise QAError("%s escapes the workspace" % label)
    return absolute


def _atomic_json(path, value, root, before_publish=None, staging_dir=None):
    _guard_output_file(root, path, "chapter receipt")
    folder = os.path.dirname(path)
    if staging_dir is not None:
        folder = _guard_directory(
            root, staging_dir, "chapter receipt staging directory", create=False
        )
    fd, temporary = tempfile.mkstemp(prefix=".study-guide-qa-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        if os.path.islink(path):
            raise QAError("chapter receipt became a symlink during write")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _paths(workspace, chapter, create_qa=False):
    guide_dir = _guard_directory(
        workspace, os.path.join(workspace, "study_guide"), "study_guide directory"
    )
    stem = "ch%02d" % chapter
    receipt = _guard_existing(
        workspace, os.path.join(guide_dir, stem + ".receipt.json"), "chapter receipt"
    )
    pdf = _guard_existing(
        workspace, os.path.join(guide_dir, stem + ".pdf"), "chapter PDF"
    )
    qa_dir = _guard_directory(
        workspace, os.path.join(guide_dir, "qa"), "study_guide QA directory",
        create=create_qa,
    )
    return guide_dir, receipt, pdf, qa_dir, stem


def _validate_pdf(path):
    try:
        with open(path, "rb") as stream:
            header = stream.read(5)
    except OSError as exc:
        raise QAError("cannot read chapter PDF: %s" % exc)
    if header != b"%PDF-":
        raise QAError("chapter PDF has an invalid PDF signature")


def _is_reparse_stat(value):
    attrs = getattr(value, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _stat_identity(value, label):
    device = int(getattr(value, "st_dev", 0))
    inode = int(getattr(value, "st_ino", 0))
    if inode == 0:
        raise PDFSnapshotDrift(
            "%s filesystem does not expose a stable file identity" % label
        )
    return device, inode


def _stat_generation(value):
    return (
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1000000000))),
    )


def _directory_stamp(path, label):
    try:
        value = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise PDFSnapshotDrift("cannot stat %s directory: %s" % (label, exc))
    if (not stat.S_ISDIR(value.st_mode) or _is_reparse_stat(value)
            or os.path.islink(path)):
        raise PDFSnapshotDrift("%s directory stopped being a safe directory" % label)
    generation = [
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1000000000)))
    ]
    # On Windows ``st_ctime`` is creation time and may be lazily normalized merely by
    # opening a new file.  POSIX ctime is a useful non-resettable directory-mutation
    # signal, so retain it there in addition to mtime.
    if os.name != "nt":
        generation.append(
            int(getattr(value, "st_ctime_ns", int(value.st_ctime * 1000000000)))
        )
    return _stat_identity(value, "%s directory" % label) + tuple(generation)


def _read_stream_twice(stream):
    stream.seek(0)
    first = stream.read()
    stream.seek(0)
    second = stream.read()
    if first != second:
        raise PDFSnapshotDrift("chapter PDF bytes changed while taking a snapshot")
    return first


def _capture_pdf_snapshot(workspace, path, expected_sha256):
    """Hold and bind one stable PDF generation to immutable bytes.

    The retained handle prevents path replacement on platforms that provide mandatory
    delete sharing, while the parent-directory stamp detects a replace/restore cycle on
    platforms where rename of an open file is allowed.  Renderers receive only ``bytes``;
    they never reopen the mutable canonical path.
    """
    path = _guard_existing(workspace, path, "chapter PDF")
    parent = _guard_directory(
        workspace, os.path.dirname(path), "chapter PDF directory", create=False
    )
    parent_before = _directory_stamp(parent, "chapter PDF")
    stream = None
    try:
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or _is_reparse_stat(before)
                or os.path.islink(path)):
            raise PDFSnapshotDrift("chapter PDF is not a non-reparse regular file")
        identity = _stat_identity(before, "chapter PDF")
        generation = _stat_generation(before)
        stream = open(path, "rb")
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or _is_reparse_stat(opened)
                or _stat_identity(opened, "opened chapter PDF") != identity
                or _stat_generation(opened) != generation):
            raise PDFSnapshotDrift("chapter PDF changed between path check and open")
        payload = _read_stream_twice(stream)
        after_read = os.fstat(stream.fileno())
        after = os.lstat(path)
        parent_after = _directory_stamp(parent, "chapter PDF")
        if (_stat_identity(after_read, "opened chapter PDF") != identity
                or _stat_identity(after, "chapter PDF") != identity
                or _stat_generation(after_read) != generation
                or _stat_generation(after) != generation
                or parent_after != parent_before):
            raise PDFSnapshotDrift("chapter PDF identity changed while taking a snapshot")
        digest = _sha256_bytes(payload)
        if digest != expected_sha256:
            raise PDFSnapshotDrift("chapter PDF hash drifted before QA rendering")
        if not payload.startswith(b"%PDF-"):
            raise PDFSnapshotDrift("chapter PDF snapshot has an invalid PDF signature")
        return {
            "path": path,
            "parent": parent,
            "parent_stamp": parent_before,
            "identity": identity,
            "generation": generation,
            "sha256": digest,
            "bytes": payload,
            "stream": stream,
        }
    except Exception:
        if stream is not None:
            stream.close()
        raise


def _verify_pdf_snapshot(workspace, snapshot, phase):
    """Reprove the retained bytes, canonical path and directory generation."""
    try:
        if _sha256_bytes(snapshot["bytes"]) != snapshot["sha256"]:
            raise PDFSnapshotDrift("immutable PDF snapshot bytes changed")
        stream = snapshot["stream"]
        opened_before = os.fstat(stream.fileno())
        retained = _read_stream_twice(stream)
        opened_after = os.fstat(stream.fileno())
        if (_stat_identity(opened_before, "retained chapter PDF") != snapshot["identity"]
                or _stat_identity(opened_after, "retained chapter PDF")
                != snapshot["identity"]
                or _stat_generation(opened_before) != snapshot["generation"]
                or _stat_generation(opened_after) != snapshot["generation"]
                or _sha256_bytes(retained) != snapshot["sha256"]):
            raise PDFSnapshotDrift("retained chapter PDF generation changed")
        current = _capture_pdf_snapshot(
            workspace, snapshot["path"], snapshot["sha256"]
        )
        try:
            if (current["identity"] != snapshot["identity"]
                    or current["generation"] != snapshot["generation"]
                    or current["parent_stamp"] != snapshot["parent_stamp"]):
                raise PDFSnapshotDrift(
                    "chapter PDF path was replaced or touched during QA rendering"
                )
        finally:
            current["stream"].close()
    except PDFSnapshotDrift as exc:
        raise PDFSnapshotDrift("PDF snapshot drift %s: %s" % (phase, exc))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise PDFSnapshotDrift(
            "PDF snapshot cannot be revalidated %s: %s" % (phase, exc)
        )


def _cleanup_chapter_qa_pages(qa_dir, chapter):
    """Delete only this chapter's published QA page names without following links."""
    for filename in os.listdir(qa_dir):
        match = QA_PAGE_RE.fullmatch(filename)
        if not match or int(match.group("chapter")) != chapter:
            continue
        path = os.path.join(qa_dir, filename)
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
        except OSError:
            # The blocked receipt remains the fail-closed source of truth even if a
            # hostile process races cleanup.  Acceptance will also reject the page.
            pass


def _receipt_basis(receipt):
    return _canonical_hash({
        key: value for key, value in receipt.items()
        if key not in ("visual_qa", "status")
    })


def _render_manifest_payload(qa):
    keys = (
        "schema_version", "renderer", "rendered_at", "page_count",
        "pdf_sha256", "receipt_basis_sha256", "pages", "auto_lint",
        "manual_review_checks", "unresolved_defects",
    )
    return {key: qa.get(key) for key in keys}


def _render_manifest_hash(qa):
    return _canonical_hash(_render_manifest_payload(qa))


def _acceptance_manifest_payload(qa):
    keys = (
        "render_manifest_sha256", "receipt_basis_sha256", "pdf_sha256",
        "page_count", "inspected_pages", "page_verdicts",
        "accepted_manual_review_checks", "reviewer", "reviewer_kind", "accepted_at",
    )
    return {key: qa.get(key) for key in keys}


def _acceptance_manifest_hash(qa):
    return _canonical_hash(_acceptance_manifest_payload(qa))


def _start_gate_snapshot(gate):
    runtime = ((gate.get("runtime_provenance") or {}).get("receipt") or {})
    return {
        "ready_to_use": bool(gate.get("ready_to_use")),
        "workspace": gate.get("workspace"),
        "materials": gate.get("materials"),
        "registered_course": gate.get("registered_course"),
        "runtime_digest": runtime.get("runtime_digest"),
        "runtime_file_count": runtime.get("runtime_file_count"),
        "skill_version": runtime.get("skill_version"),
        "git_commit": runtime.get("git_commit"),
        "git_branch": runtime.get("git_branch"),
        "git_dirty": runtime.get("git_dirty"),
        "python_executable": runtime.get("python_executable"),
    }


def _validate_start_gate_snapshot(value, workspace):
    if not isinstance(value, dict) or set(value) != START_GATE_FIELDS:
        raise QAError("receipt start_gate schema is invalid")
    if value.get("ready_to_use") is not True:
        raise QAError("receipt start_gate was not ready")
    for field in ("workspace", "materials", "registered_course", "skill_version",
                  "python_executable"):
        _safe_single_line(value.get(field), "start_gate.%s" % field)
    if not exam_start.update_progress._same_canonical_path(value["workspace"], workspace):
        raise QAError("receipt start_gate belongs to another workspace")
    if not isinstance(value.get("runtime_digest"), str) or not HASH_RE.fullmatch(
            value["runtime_digest"]):
        raise QAError("receipt start_gate.runtime_digest is invalid")
    count = value.get("runtime_file_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise QAError("receipt start_gate.runtime_file_count is invalid")
    if value.get("git_commit") is not None and not re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", str(value["git_commit"])):
        raise QAError("receipt start_gate.git_commit is invalid")
    if value.get("git_branch") is not None:
        _safe_single_line(value["git_branch"], "start_gate.git_branch")
    if value.get("git_dirty") is not None and type(value["git_dirty"]) is not bool:
        raise QAError("receipt start_gate.git_dirty is invalid")


def _conversion_input_hash(html_path):
    try:
        with open(html_path, "r", encoding="utf-8") as stream:
            document = stream.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise QAError("cannot read current Study Guide HTML: %s" % exc)
    ready = document.replace(
        '<details class="quiz-answer">', '<details open class="quiz-answer">'
    )
    return _sha256_bytes(ready.encode("utf-8"))


def _conversion_run_hash(chapter, profile, language, html_sha256, pdf_sha256,
                         conversion_input_html_sha256, converter,
                         conversion_started_at, conversion_completed_at,
                         start_gate):
    return _canonical_hash({
        "artifact_type": "study_guide",
        "chapter": chapter,
        "profile": profile,
        "language": language,
        "html_sha256": html_sha256,
        "pdf_sha256": pdf_sha256,
        "conversion_input_html_sha256": conversion_input_html_sha256,
        "converter": converter,
        "conversion_started_at": conversion_started_at,
        "conversion_completed_at": conversion_completed_at,
        "start_gate": start_gate,
    })


def _strict_state_language(workspace):
    path = _guard_existing(
        workspace, os.path.join(workspace, "study_state.json"), "study state"
    )
    state = _strict_json_load(path)
    raw = state.get("language")
    code, unused_warning = i18n.canon_language(raw)
    if code not in i18n.LANGS:
        raise QAError("current study_state.json.language is invalid")
    return code


def validate_receipt_chain(workspace, chapter, require_pdf=True):
    """Validate one immutable render/conversion identity chain against current state.

    This never fills a missing field.  A native or externally dropped PDF has no same-run
    browser conversion receipt and therefore cannot be visually accepted or published.
    """
    workspace = _guard_workspace(workspace)
    if isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 1:
        raise QAError("chapter must be a positive integer", 2)
    guide_dir = _guard_directory(
        workspace, os.path.join(workspace, "study_guide"), "study_guide directory"
    )
    stem = "ch%02d" % chapter
    receipt_path = _guard_existing(
        workspace, os.path.join(guide_dir, stem + ".receipt.json"), "chapter receipt"
    )
    receipt = _strict_json_load(receipt_path)
    if set(receipt) != RECEIPT_FIELDS:
        missing = sorted(RECEIPT_FIELDS - set(receipt))
        extra = sorted(set(receipt) - RECEIPT_FIELDS)
        raise QAError("artifact receipt fields are invalid; missing=%s extra=%s" %
                      (missing, extra))
    if (receipt.get("schema_version") != ARTIFACT_RECEIPT_SCHEMA_VERSION
            or receipt.get("artifact_type") != "study_guide"):
        raise QAError("artifact receipt schema/type is invalid")
    if receipt.get("chapter") != chapter:
        raise QAError("chapter receipt does not belong to chapter %d" % chapter)
    profile = receipt.get("profile")
    language = receipt.get("language")
    if profile not in ("full", "abridged"):
        raise QAError("receipt profile is invalid")
    if language not in ("zh", "en", "bilingual"):
        raise QAError("receipt language is invalid")
    if _strict_state_language(workspace) != language:
        raise QAError("receipt language drifted from current study state")
    generated_at = _parse_timestamp(receipt.get("generated_at"), "generated_at")
    if not isinstance(receipt.get("preflight"), dict):
        raise QAError("receipt preflight is missing or invalid")
    if (receipt["preflight"].get("status") not in
            ("passed", "injected-test-converter")
            or receipt["preflight"].get("probe_error") not in (None, "")
            or receipt["preflight"].get("missing_needed") not in (None, [])):
        raise QAError("receipt preflight did not pass cleanly")
    if not isinstance(receipt.get("visual_qa"), dict):
        raise QAError("receipt visual_qa must be an object")

    expected_manifest_rel = "notebook/ch%02d.guide.json" % chapter
    if receipt.get("content_manifest") != expected_manifest_rel:
        raise QAError("receipt content_manifest is not the canonical chapter manifest")
    manifest_path = _guard_existing(
        workspace, os.path.join(workspace, *expected_manifest_rel.split("/")),
        "typed chapter manifest",
    )
    manifest_hash = receipt.get("content_manifest_sha256")
    if not isinstance(manifest_hash, str) or not HASH_RE.fullmatch(manifest_hash):
        raise QAError("receipt content_manifest_sha256 is invalid")
    if _sha256_file(manifest_path) != manifest_hash:
        raise QAError("typed chapter manifest hash drifted from artifact receipt")
    try:
        unused_manifest, report = study_guide_content.load_and_validate_manifest(
            workspace, chapter
        )
    except (study_guide_content.ContentError, OSError, UnicodeError, ValueError) as exc:
        raise QAError("current typed chapter manifest is invalid: %s" % exc)
    report_manifest_path = os.path.realpath(report.get("input_path") or "")
    if os.path.normcase(report_manifest_path) != os.path.normcase(manifest_path):
        raise QAError("manifest validator did not load the canonical chapter manifest")
    for field, report_field in (
            ("profile", "profile"), ("language", "language"),
            ("expected_item_ids", "expected_item_ids"),
            ("rendered_item_ids", "walkthrough_item_ids"),
            ("omitted_item_ids", "omitted_item_ids")):
        if field.endswith("_ids"):
            _id_list(receipt.get(field), field)
        if receipt.get(field) != report.get(report_field):
            raise QAError("receipt %s drifted from current typed manifest" % field)

    expected_html_rel = "study_guide/%s.html" % stem
    if receipt.get("html_file") != expected_html_rel:
        raise QAError("receipt html_file is not the canonical chapter HTML")
    html_path = _guard_existing(
        workspace, os.path.join(workspace, *expected_html_rel.split("/")),
        "chapter HTML",
    )
    html_hash = receipt.get("html_sha256")
    if not isinstance(html_hash, str) or not HASH_RE.fullmatch(html_hash):
        raise QAError("receipt html_sha256 is invalid")
    if _sha256_file(html_path) != html_hash:
        raise QAError("chapter HTML hash drifted from artifact receipt")

    recorded_gate = receipt.get("start_gate")
    _validate_start_gate_snapshot(recorded_gate, workspace)
    try:
        current_gate = exam_start.check_registered_workspace_gate(workspace)
    except Exception as exc:
        raise QAError("current workspace start gate failed: %s" % exc)
    if not isinstance(current_gate, dict) or not current_gate.get("ready_to_use"):
        raise QAError("current workspace start gate is not ready")
    if _start_gate_snapshot(current_gate) != recorded_gate:
        raise QAError("workspace/runtime start gate drifted from artifact receipt")

    pdf_fields = (
        "pdf_file", "pdf_sha256", "converter", "conversion_input_html_sha256",
        "conversion_started_at", "conversion_completed_at", "conversion_run_sha256",
    )
    has_pdf_binding = all(receipt.get(field) is not None for field in pdf_fields)
    any_pdf_binding = any(receipt.get(field) is not None for field in pdf_fields)
    backend = receipt.get("pdf_backend")
    if backend not in ("html", "browser", "native"):
        raise QAError("receipt pdf_backend is invalid")
    if receipt["preflight"].get("pdf_backend") != backend:
        raise QAError("receipt preflight backend does not match pdf_backend")
    context = {
        "workspace": workspace, "guide_dir": guide_dir, "stem": stem,
        "receipt_path": receipt_path, "manifest_path": manifest_path,
        "html_path": html_path, "pdf_path": None,
    }
    if not has_pdf_binding:
        if any_pdf_binding:
            raise QAError("artifact receipt contains a partial PDF conversion binding")
        expected_status = "awaiting_native_pdf" if backend == "native" else "html_ready"
        if receipt.get("status") != expected_status:
            raise QAError("unbound artifact receipt status is inconsistent")
        if require_pdf:
            raise QAError(
                "artifact receipt has no same-run browser PDF binding; external/native PDF "
                "binding is forbidden"
            )
        return receipt, context

    if backend != "browser":
        raise QAError("only the same-run browser route can bind a Study Guide PDF")
    if receipt.get("status") not in ("qa_pending", "ready"):
        raise QAError("bound PDF artifact receipt status is invalid")
    expected_pdf_rel = "study_guide/%s.pdf" % stem
    if receipt.get("pdf_file") != expected_pdf_rel:
        raise QAError("receipt pdf_file is not the canonical chapter PDF")
    pdf_path = _guard_existing(
        workspace, os.path.join(workspace, *expected_pdf_rel.split("/")), "chapter PDF"
    )
    _validate_pdf(pdf_path)
    pdf_hash = receipt.get("pdf_sha256")
    if not isinstance(pdf_hash, str) or not HASH_RE.fullmatch(pdf_hash):
        raise QAError("receipt pdf_sha256 is invalid")
    if _sha256_file(pdf_path) != pdf_hash:
        raise QAError("chapter PDF hash drifted from artifact receipt")
    converter = _safe_single_line(receipt.get("converter"), "converter")
    input_hash = receipt.get("conversion_input_html_sha256")
    if not isinstance(input_hash, str) or not HASH_RE.fullmatch(input_hash):
        raise QAError("receipt conversion_input_html_sha256 is invalid")
    if _conversion_input_hash(html_path) != input_hash:
        raise QAError("browser conversion input drifted from the current HTML")
    started = _parse_timestamp(receipt.get("conversion_started_at"),
                               "conversion_started_at")
    completed = _parse_timestamp(receipt.get("conversion_completed_at"),
                                 "conversion_completed_at")
    if completed < started:
        raise QAError("browser conversion completion precedes its start")
    if generated_at < completed:
        raise QAError("artifact receipt predates browser conversion completion")
    run_hash = receipt.get("conversion_run_sha256")
    expected_run_hash = _conversion_run_hash(
        chapter, profile, language, html_hash, pdf_hash, input_hash, converter,
        receipt["conversion_started_at"], receipt["conversion_completed_at"],
        recorded_gate,
    )
    if not isinstance(run_hash, str) or run_hash != expected_run_hash:
        raise QAError("browser conversion run hash is invalid or drifted")
    context["pdf_path"] = pdf_path
    return receipt, context


def _white_ratio(samples, channels):
    if not isinstance(samples, (bytes, bytearray)) or not samples or channels < 1:
        raise QAError("renderer returned no pixel samples")
    usable = len(samples) - (len(samples) % channels)
    if usable <= 0:
        raise QAError("renderer returned malformed pixel samples")
    white = 0
    pixel_count = usable // channels
    # A full 150-DPI page contains millions of pixels.  A deterministic,
    # evenly spaced sample keeps QA bounded without adding NumPy to the core.
    step_pixels = max(1, pixel_count // 250000)
    total = 0
    for pixel in range(0, pixel_count, step_pixels):
        offset = pixel * channels
        rgb = samples[offset:offset + min(channels, 3)]
        if rgb and all(component >= 250 for component in rgb):
            white += 1
        total += 1
    return white / float(total)


class PyMuPDFBackend(object):
    name = "pymupdf"

    def __init__(self, module):
        self.module = module

    def render_pages(self, pdf_input):
        if isinstance(pdf_input, (bytes, bytearray, memoryview)):
            document = self.module.open(
                stream=bytes(pdf_input), filetype="pdf"
            )
        else:  # compatibility for direct backend consumers; QA passes bytes only
            document = self.module.open(pdf_input)
        try:
            output = []
            for index in range(document.page_count):
                page = document[index]
                text = page.get_text("text") or ""
                pixmap = page.get_pixmap(dpi=150, alpha=False)
                png = pixmap.tobytes("png")
                output.append({
                    "png": png,
                    "text": text,
                    "width": int(pixmap.width),
                    "height": int(pixmap.height),
                    "white_ratio": _white_ratio(pixmap.samples, int(pixmap.n)),
                })
            return output
        finally:
            document.close()


class PDFiumBackend(object):
    name = "pypdfium2"

    def __init__(self, module):
        self.module = module

    def render_pages(self, pdf_input):
        if isinstance(pdf_input, memoryview):
            pdf_input = pdf_input.tobytes()
        document = self.module.PdfDocument(pdf_input)
        output = []
        try:
            for index in range(len(document)):
                page = document[index]
                text_page = None
                try:
                    text_page = page.get_textpage()
                    text = text_page.get_text_range() or ""
                    image = page.render(scale=2.0).to_pil().convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    output.append({
                        "png": buffer.getvalue(),
                        "text": text,
                        "width": int(image.width),
                        "height": int(image.height),
                        "white_ratio": _white_ratio(image.tobytes(), 3),
                    })
                finally:
                    if text_page is not None and hasattr(text_page, "close"):
                        text_page.close()
                    if hasattr(page, "close"):
                        page.close()
            return output
        finally:
            if hasattr(document, "close"):
                document.close()


def detect_backend():
    """Return the first complete installed renderer without importing it at module load."""
    try:
        fitz = importlib.import_module("fitz")
        if hasattr(fitz, "open"):
            return PyMuPDFBackend(fitz)
    except Exception:
        pass
    try:
        pdfium = importlib.import_module("pypdfium2")
        # ``to_pil`` is the supported PNG bridge; require Pillow explicitly.
        importlib.import_module("PIL")
        if hasattr(pdfium, "PdfDocument"):
            return PDFiumBackend(pdfium)
    except Exception:
        pass
    return None


def _page_number_visible(text, page_number, page_count):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    tail = lines[-8:]
    number = str(page_number)
    total = str(page_count)
    patterns = (
        re.compile(r"^(?:page|p\.?)[ \t]*0*%s(?:[ \t]*(?:/|of)[ \t]*0*%s)?$"
                   % (re.escape(number), re.escape(total)), re.IGNORECASE),
        re.compile(r"^第[ \t]*0*%s[ \t]*页(?:[ \t]*(?:/|共)[ \t]*0*%s[ \t]*页?)?$"
                   % (re.escape(number), re.escape(total))),
        re.compile(r"^第[ \t]*/[ \t]*(?:page|p\.?)[ \t]*0*%s[ \t]*(?:/|of)[ \t]*0*%s$"
                   % (re.escape(number), re.escape(total)), re.IGNORECASE),
        re.compile(r"^0*%s[ \t]*(?:/|of)[ \t]*0*%s$"
                   % (re.escape(number), re.escape(total)), re.IGNORECASE),
        re.compile(r"^[-–—]?[ \t]*0*%s[ \t]*[-–—]?$" % re.escape(number)),
    )
    if any(pattern.fullmatch(line) for line in tail for pattern in patterns):
        return True
    # An explicit labeled page number may be emitted before the final extraction
    # line by some backends, so search all lines only for the labeled forms.
    return any(patterns[0].fullmatch(line) or patterns[1].fullmatch(line)
               or patterns[2].fullmatch(line)
               for line in lines)


def _png_dimensions(payload):
    try:
        return _shared_png_dimensions(payload)
    except ImageValidationError as exc:
        raise QAError("renderer did not produce a valid PNG: %s" % exc)


def _looks_like_orphan_heading(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    while lines and re.fullmatch(
            r"(?:page|p\.?|第)?\s*\d+(?:\s*(?:/|of|共)\s*\d+\s*页?)?",
            lines[-1], re.I):
        lines.pop()
    if not lines:
        return False
    tail = lines[-1]
    return len(tail) <= 80 and bool(re.search(
        r"(?:chapter|knowledge point|worked example|example|solution|quiz|"
        r"第\s*\d+\s*章|知识点|例题|解答|答案|测验)\s*[:：]?$", tail, re.I
    ))


def _lint_page(page, page_number, page_count):
    if not isinstance(page, dict):
        raise QAError("renderer page %d is not an object" % page_number)
    png = page.get("png")
    text = page.get("text")
    width = page.get("width")
    height = page.get("height")
    white_ratio = page.get("white_ratio")
    if not isinstance(png, (bytes, bytearray)) or not bytes(png).startswith(PNG_SIGNATURE):
        raise QAError("renderer page %d did not produce a valid PNG" % page_number)
    png_width, png_height = _png_dimensions(png)
    if not isinstance(text, str):
        raise QAError("renderer page %d did not produce text for lint" % page_number)
    if (isinstance(width, bool) or not isinstance(width, int) or width < 1
            or isinstance(height, bool) or not isinstance(height, int) or height < 1):
        raise QAError("renderer page %d returned invalid dimensions" % page_number)
    if (isinstance(white_ratio, bool) or not isinstance(white_ratio, (int, float))
            or not math.isfinite(white_ratio) or not 0.0 <= white_ratio <= 1.0):
        raise QAError("renderer page %d returned invalid white_ratio" % page_number)
    if (png_width, png_height) != (width, height):
        raise QAError("renderer page %d dimensions disagree with the PNG IHDR" % page_number)

    nonspace = sum(1 for char in text if not char.isspace())
    whitespace_ratio = (
        sum(1 for char in text if char.isspace()) / float(len(text)) if text else 1.0
    )
    nul_count = text.count("\x00")
    replacement_count = text.count("\ufffd")
    raw_tex_count = len(RAW_TEX_RE.findall(text))
    number_visible = _page_number_visible(text, page_number, page_count)
    orphan_heading = _looks_like_orphan_heading(text)
    pixel_count = width * height
    aspect_ratio = width / float(height)
    defects = []

    def add(code, message):
        defects.append({"page": page_number, "code": code, "message": message})

    if white_ratio >= 0.9995 and nonspace == 0:
        add("blank_page", "rendered page is effectively blank")
    elif white_ratio >= 0.999 and nonspace < 8:
        add("abnormal_blankness", "rendered page is abnormally empty")
    elif white_ratio >= 0.9975 and nonspace < 60:
        add("excessive_blank_region",
            "rendered page has very little readable content and excessive blank area")
    if width < 800 or height < 1000 or pixel_count < 800000:
        add("raster_resolution_too_low",
            "rendered page raster is below the minimum readable QA resolution")
    if not 0.45 <= aspect_ratio <= 1.50:
        add("abnormal_page_aspect_ratio", "rendered page has an abnormal aspect ratio")
    if nul_count or replacement_count:
        add("nul_or_replacement_text",
            "extracted page text contains NUL or Unicode replacement characters")
    if raw_tex_count:
        add("raw_tex_visible", "extracted page text still contains raw TeX syntax")
    if len(text) >= 40 and whitespace_ratio >= 0.80 and nonspace < 30:
        add("abnormal_text_whitespace", "extracted page text is dominated by abnormal whitespace")
    if not number_visible:
        add("page_number_not_visible", "page number is not visible in extracted page text")
    if orphan_heading:
        add("possible_orphan_heading",
            "page appears to end with a heading that has no following explanation")

    record = {
        "page": page_number,
        "width": width,
        "height": height,
        "png_sha256": _sha256_bytes(bytes(png)),
        "text_sha256": _sha256_bytes(text.encode("utf-8")),
        "metrics": {
            "white_ratio": round(float(white_ratio), 6),
            "pixel_count": pixel_count,
            "aspect_ratio": round(aspect_ratio, 6),
            "text_char_count": len(text),
            "nonspace_char_count": nonspace,
            "text_whitespace_ratio": round(float(whitespace_ratio), 6),
            "nul_count": nul_count,
            "replacement_char_count": replacement_count,
            "raw_tex_count": raw_tex_count,
            "page_number_text_visible": number_visible,
            "possible_orphan_heading": orphan_heading,
        },
        "defects": defects,
    }
    return record, bytes(png), defects


def _summary(qa):
    return {
        "status": qa.get("status"),
        "renderer": qa.get("renderer"),
        "page_count": qa.get("page_count"),
        "defect_count": len(qa.get("unresolved_defects") or []),
    }


def render(workspace, chapter, backend=None, now=None):
    receipt, context = validate_receipt_chain(workspace, chapter, require_pdf=True)
    workspace = context["workspace"]
    receipt_path = context["receipt_path"]
    pdf_path = context["pdf_path"]
    stem = context["stem"]
    qa_dir = _guard_directory(
        workspace, os.path.join(context["guide_dir"], "qa"),
        "study_guide QA directory", create=True,
    )
    actual_pdf_hash = receipt["pdf_sha256"]
    backend = backend or detect_backend()
    if backend is None:
        raise QAError(
            "no PDF rendering backend; install PyMuPDF or pypdfium2 + Pillow", 3
        )
    backend_name = getattr(backend, "name", None)
    if not isinstance(backend_name, str) or not backend_name.strip():
        raise QAError("renderer backend must expose a non-empty name")
    timestamp = now or _now_utc()
    receipt_basis = _receipt_basis(receipt)
    receipt["status"] = "qa_pending"
    receipt["visual_qa"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "rendering",
        "renderer": backend_name,
        "rendered_at": timestamp,
        "pdf_sha256": actual_pdf_hash,
        "receipt_basis_sha256": receipt_basis,
        "page_count": 0,
        "pages": [],
        "auto_lint": {"status": "pending", "defects": []},
        "manual_review_checks": list(MANUAL_REVIEW_CHECKS),
        "unresolved_defects": [],
    }
    _atomic_json(receipt_path, receipt, workspace)

    stage = tempfile.mkdtemp(prefix=".%s-" % stem, dir=qa_dir)
    pdf_snapshot = None
    try:
        pdf_snapshot = _capture_pdf_snapshot(
            workspace, pdf_path, actual_pdf_hash
        )
        # Backends receive the already verified immutable bytes.  Giving a renderer the
        # canonical path would reopen the TOCTOU window closed by the snapshot protocol.
        rendered = backend.render_pages(pdf_snapshot["bytes"])
        _verify_pdf_snapshot(workspace, pdf_snapshot, "after renderer return")
        if not isinstance(rendered, (list, tuple)) or not rendered:
            raise QAError("renderer returned zero pages")
        page_count = len(rendered)
        top_page_count = receipt.get("page_count")
        if top_page_count is not None:
            if (isinstance(top_page_count, bool)
                    or not isinstance(top_page_count, int) or top_page_count < 1):
                raise QAError("receipt page_count is invalid")
            if top_page_count != page_count:
                raise QAError("rendered page count drifted from receipt page_count")

        page_records = []
        all_defects = []
        staged = []
        for index, page in enumerate(rendered, 1):
            record, png, defects = _lint_page(page, index, page_count)
            filename = "%s_p%03d.png" % (stem, index)
            record["png"] = "study_guide/qa/" + filename
            stage_path = os.path.join(stage, filename)
            with open(stage_path, "wb") as stream:
                stream.write(png)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((stage_path, filename))
            page_records.append(record)
            all_defects.extend(defects)

        _verify_pdf_snapshot(workspace, pdf_snapshot, "before QA page publication")
        for stage_path, filename in staged:
            destination = _guard_output_file(
                workspace, os.path.join(qa_dir, filename), "rendered QA page"
            )
            os.replace(stage_path, destination)

        expected = {filename for unused, filename in staged}
        for filename in os.listdir(qa_dir):
            match = QA_PAGE_RE.fullmatch(filename)
            if not match or int(match.group("chapter")) != chapter or filename in expected:
                continue
            stale = os.path.join(qa_dir, filename)
            if os.path.islink(stale) or not os.path.isfile(stale):
                raise QAError("stale QA page is not a safe regular file: %s" % filename)
            os.remove(stale)
        _verify_pdf_snapshot(workspace, pdf_snapshot, "after QA page publication")

        qa = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked" if all_defects else "rendered",
            "renderer": backend_name,
            "rendered_at": timestamp,
            "pdf_sha256": actual_pdf_hash,
            "receipt_basis_sha256": receipt_basis,
            "page_count": page_count,
            "pages": page_records,
            "auto_lint": {
                "status": "failed" if all_defects else "passed",
                "checks": [
                    "blank_page", "nul_or_replacement_text", "raw_tex_visible",
                    "abnormal_text_whitespace", "page_number_text_visible",
                    "raster_resolution", "page_aspect_ratio",
                    "excessive_blank_region", "possible_orphan_heading",
                ],
                "thresholds": {
                    "blank_white_ratio": 0.9995,
                    "abnormal_blank_white_ratio": 0.999,
                    "excessive_blank_white_ratio": 0.9975,
                    "abnormal_text_whitespace_ratio": 0.80,
                    "minimum_width": 800,
                    "minimum_height": 1000,
                    "minimum_pixel_count": 800000,
                    "minimum_aspect_ratio": 0.45,
                    "maximum_aspect_ratio": 1.50,
                },
                "defects": all_defects,
            },
            "manual_review_checks": list(MANUAL_REVIEW_CHECKS),
            "unresolved_defects": list(all_defects),
        }
        qa["render_manifest_sha256"] = _render_manifest_hash(qa)
        receipt["visual_qa"] = qa
        _atomic_json(
            receipt_path, receipt, workspace,
            before_publish=lambda: _verify_pdf_snapshot(
                workspace, pdf_snapshot, "immediately before receipt publication"
            ),
            staging_dir=qa_dir,
        )
        return (10 if all_defects else 0), _summary(qa)
    except Exception as exc:
        if isinstance(exc, PDFSnapshotDrift):
            _cleanup_chapter_qa_pages(qa_dir, chapter)
        qa = receipt.get("visual_qa") if isinstance(receipt.get("visual_qa"), dict) else {}
        qa.update({
            "status": "blocked",
            "error": str(exc)[:500],
            "unresolved_defects": [{
                "page": None, "code": "render_failed", "message": str(exc)[:500],
            }],
        })
        receipt["status"] = "qa_pending"
        receipt["visual_qa"] = qa
        _atomic_json(receipt_path, receipt, workspace)
        if isinstance(exc, QAError):
            raise
        raise QAError("PDF QA rendering failed: %s" % exc)
    finally:
        if pdf_snapshot is not None:
            pdf_snapshot["stream"].close()
        shutil.rmtree(stage, ignore_errors=True)


def _verify_page_records(workspace, qa_dir, stem, qa):
    page_count = qa.get("page_count")
    pages = qa.get("pages")
    if (isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1):
        raise QAError("visual_qa page_count is missing or invalid")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise QAError("visual_qa does not contain every rendered page")
    seen = set()
    for index, record in enumerate(pages, 1):
        if not isinstance(record, dict) or record.get("page") != index:
            raise QAError("visual_qa page records are incomplete or out of order")
        if index in seen:
            raise QAError("visual_qa contains a duplicate page record")
        seen.add(index)
        expected_name = "%s_p%03d.png" % (stem, index)
        expected_rel = "study_guide/qa/" + expected_name
        if record.get("png") != expected_rel:
            raise QAError("visual_qa page %d has an unsafe or unexpected PNG path" % index)
        expected_hash = record.get("png_sha256")
        if not (isinstance(expected_hash, str)
                and re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            raise QAError("visual_qa page %d has an invalid PNG digest" % index)
        path = _guard_existing(
            workspace, os.path.join(qa_dir, expected_name), "rendered QA page %d" % index
        )
        if _sha256_file(path) != expected_hash:
            raise QAError("rendered QA page %d hash drifted" % index)
    if seen != set(range(1, page_count + 1)):
        raise QAError("visual_qa page set is incomplete")


def _parse_page_verdicts(values, qa):
    page_count = qa.get("page_count")
    pages = qa.get("pages")
    if not isinstance(values, list) or not values:
        raise QAError(
            "accept requires one --page-verdict N=pass[:notes] for every rendered page", 2
        )
    records = {}
    pattern = re.compile(r"^(?P<page>[1-9]\d*)=(?P<verdict>[a-z]+)(?::(?P<notes>.*))?$")
    for raw in values:
        if not isinstance(raw, str) or any(char in raw for char in ("\x00", "\r", "\n")):
            raise QAError("--page-verdict must be a safe single-line value", 2)
        match = pattern.fullmatch(raw.strip())
        if not match:
            raise QAError("invalid --page-verdict %r; expected N=pass[:notes]" % raw, 2)
        page = int(match.group("page"))
        verdict = match.group("verdict")
        notes = (match.group("notes") or "").strip()
        if page in records:
            raise QAError("duplicate --page-verdict for page %d" % page, 2)
        if page < 1 or page > page_count:
            raise QAError("--page-verdict page %d is outside 1..%d" %
                          (page, page_count), 2)
        if verdict != "pass":
            raise QAError("page %d has non-pass verdict %s; acceptance is blocked" %
                          (page, verdict), 1)
        if len(notes) > 500:
            raise QAError("--page-verdict notes are limited to 500 characters", 2)
        records[page] = {
            "page": page,
            "verdict": "pass",
            "notes": notes,
            "png_sha256": pages[page - 1]["png_sha256"],
        }
    missing = sorted(set(range(1, page_count + 1)) - set(records))
    if missing:
        raise QAError("page verdicts do not cover every page; missing=%s" % missing, 2)
    return [records[number] for number in range(1, page_count + 1)]


def accept(workspace, chapter, inspected_pages, reviewer, reviewer_kind="agent",
           unresolved_defects=None, page_verdicts=None, now=None):
    receipt, context = validate_receipt_chain(workspace, chapter, require_pdf=True)
    workspace = context["workspace"]
    receipt_path = context["receipt_path"]
    pdf_path = context["pdf_path"]
    stem = context["stem"]
    qa_dir = _guard_directory(
        workspace, os.path.join(context["guide_dir"], "qa"),
        "study_guide QA directory", create=False,
    )
    actual_pdf_hash = receipt["pdf_sha256"]
    qa = receipt.get("visual_qa")
    if not isinstance(qa, dict) or qa.get("schema_version") != SCHEMA_VERSION:
        raise QAError("visual_qa receipt is missing; run render first")
    if qa.get("pdf_sha256") != actual_pdf_hash:
        raise QAError("visual_qa PDF hash drifted; rerun render")
    if qa.get("receipt_basis_sha256") != _receipt_basis(receipt):
        raise QAError("chapter receipt changed after QA rendering; rerun render")
    manifest_hash = qa.get("render_manifest_sha256")
    if (not isinstance(manifest_hash, str)
            or manifest_hash != _render_manifest_hash(qa)):
        raise QAError("visual_qa render evidence changed after rendering")
    _verify_page_records(workspace, qa_dir, stem, qa)

    if inspected_pages != "all":
        raise QAError("accept requires the explicit attestation --inspected-pages all", 2)
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or any(char in reviewer for char in ("\x00", "\n", "\r"))):
        raise QAError("--reviewer must be a non-empty single-line name", 2)
    if reviewer_kind not in ("user", "agent"):
        raise QAError("--reviewer-kind must be user or agent", 2)
    if unresolved_defects:
        raise QAError("visual acceptance refused because unresolved defects were declared")
    auto_lint = qa.get("auto_lint")
    if (not isinstance(auto_lint, dict) or auto_lint.get("status") != "passed"
            or auto_lint.get("defects") not in ([], None)):
        raise QAError("visual acceptance refused because automatic lint did not pass")
    if qa.get("unresolved_defects") not in ([], None):
        raise QAError("visual acceptance refused because unresolved defects remain")
    if qa.get("status") not in ("rendered", "ready"):
        raise QAError("visual_qa is not in a render-complete state")
    if qa.get("manual_review_checks") != list(MANUAL_REVIEW_CHECKS):
        raise QAError("visual_qa manual review checklist is missing or drifted")
    verdict_records = _parse_page_verdicts(page_verdicts or [], qa)

    pdf_snapshot = _capture_pdf_snapshot(workspace, pdf_path, actual_pdf_hash)
    try:
        qa["status"] = "ready"
        qa["inspected_pages"] = "all"
        qa["page_verdicts"] = verdict_records
        qa["accepted_manual_review_checks"] = list(MANUAL_REVIEW_CHECKS)
        qa["reviewer"] = reviewer.strip()
        qa["reviewer_kind"] = reviewer_kind
        qa["accepted_at"] = now or _now_utc()
        qa["acceptance_manifest_sha256"] = _acceptance_manifest_hash(qa)
        receipt["status"] = "ready"
        receipt["visual_qa"] = qa
        _atomic_json(
            receipt_path, receipt, workspace,
            before_publish=lambda: _verify_pdf_snapshot(
                workspace, pdf_snapshot, "immediately before acceptance receipt"
            ),
            staging_dir=qa_dir,
        )
        return 0, _summary(qa)
    finally:
        pdf_snapshot["stream"].close()


def _parser():
    parser = argparse.ArgumentParser(
        description="Render and explicitly accept every page of a chapter Study Guide PDF."
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("render", help="render every PDF page and run fail-closed automatic lint")
    accept_parser = commands.add_parser(
        "accept", help="record explicit visual inspection after verifying all render evidence"
    )
    accept_parser.add_argument("--inspected-pages", required=True)
    accept_parser.add_argument("--reviewer", required=True)
    accept_parser.add_argument("--reviewer-kind", choices=("user", "agent"), default="agent")
    accept_parser.add_argument(
        "--page-verdict", action="append", default=[],
        help=("repeat once per rendered page as N=pass or N=pass:notes; a missing, "
              "duplicate, or non-pass page blocks acceptance"),
    )
    accept_parser.add_argument(
        "--unresolved-defect", action="append", default=[],
        help="declare a remaining defect; any value makes acceptance fail closed",
    )
    return parser


def run(argv=None, backend=None, now=None, _state_locked=False):
    args = _parser().parse_args(argv)
    if args.chapter < 1:
        raise QAError("--chapter must be a positive integer", 2)
    workspace = _guard_workspace(args.workspace)
    if not _state_locked:
        with workspace_publication_lock(workspace):
            return run(argv, backend=backend, now=now, _state_locked=True)
    if args.command == "render":
        code, payload = render(workspace, args.chapter, backend=backend, now=now)
    else:
        code, payload = accept(
            workspace, args.chapter, args.inspected_pages, args.reviewer,
            reviewer_kind=args.reviewer_kind,
            unresolved_defects=args.unresolved_defect,
            page_verdicts=args.page_verdict, now=now,
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print("study_guide_qa: status=%s pages=%s renderer=%s defects=%s" % (
            payload.get("status"), payload.get("page_count"),
            payload.get("renderer"), payload.get("defect_count"),
        ))
    return code


def main(argv=None, backend=None, now=None):
    try:
        return run(argv, backend=backend, now=now)
    except QAError as exc:
        sys.stderr.write("study_guide_qa: %s\n" % exc)
        return exc.code
    except OSError as exc:
        sys.stderr.write("study_guide_qa: file operation failed: %s\n" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
