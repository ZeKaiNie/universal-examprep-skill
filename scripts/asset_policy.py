#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared, deterministic policy for student-attempt asset isolation.

Asset paths remain workspace-relative display values everywhere else.  This
module derives a host-aware *physical identity* only for comparisons: Windows
therefore folds case aliases while POSIX hosts remain case-sensitive.

Callers remain responsible for their existing full schema validation before
trusting an asset.  The audit helper nevertheless reports every malformed asset
declaration it can see so a narrow consumer cannot accidentally bypass the
workspace validator.
"""

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from collections import Counter
from pathlib import Path

try:
    from stable_ids import stable_item_id_problem
except ImportError:  # package import from the repository root
    from scripts.stable_ids import stable_item_id_problem

try:
    from ingestion.identifiers import (
        canonical_json,
        is_link_or_reparse,
        normalize_workspace_path,
        safe_workspace_entry,
    )
except ImportError:  # package import from the repository root
    from scripts.ingestion.identifiers import (
        canonical_json,
        is_link_or_reparse,
        normalize_workspace_path,
        safe_workspace_entry,
    )


STUDENT_ATTEMPT = "student_attempt"
PROMPT_ASSET_ROLES = frozenset(("question_context", "figure", "diagram", "table"))
ANSWER_ASSET_ROLES = frozenset(("answer_context", "worked_solution"))
KNOWN_ASSET_ROLES = PROMPT_ASSET_ROLES | ANSWER_ASSET_ROLES | frozenset((
    STUDENT_ATTEMPT, "source_page", "other",
))
SOURCE_ITEM_ASSET_ROLES = PROMPT_ASSET_ROLES | ANSWER_ASSET_ROLES | frozenset((
    STUDENT_ATTEMPT,
))
QUIZ_RUNTIME_TYPES = frozenset((
    "choice", "subjective", "diagram", "fill_blank", "true_false", "code",
))
QUIZ_RUNTIME_TRUE_FALSE = frozenset((
    "true", "false", "t", "f", "yes", "no", "1", "0",
    "真", "假", "对", "错", "正确", "错误",
))
QUIZ_RUNTIME_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
QUIZ_RUNTIME_MAX_IMAGE_BYTES = 64 * 1024 * 1024
QUIZ_RUNTIME_MAX_BANK_BYTES = 128 * 1024 * 1024
QUIZ_RUNTIME_MAX_SESSION_BYTES = 8 * 1024 * 1024


def physical_asset_key(path):
    """Return a host-aware comparison key for one validated relative path."""

    if not isinstance(path, str) or not path or path != path.strip():
        return None
    # Legacy quiz/teaching rows may use safe backslash separators.  The shared
    # normalizer folds those to the same portable identity while still rejecting
    # traversal, drives, UNC paths, URLs/colons, and empty/dot components.
    try:
        canonical = normalize_workspace_path(path)
    except ValueError:
        return None
    host_path = canonical.replace("/", os.sep)
    return os.path.normcase(os.path.normpath(host_path))


def _asset_workspace_root(workspace):
    """Return a lexical real directory without resolving away a root alias."""

    root = Path(os.path.abspath(str(workspace)))
    if os.path.lexists(str(root)) and is_link_or_reparse(root):
        raise ValueError("workspace must not be a symlink, junction, or reparse point")
    if not root.is_dir():
        raise ValueError("workspace must be an existing directory")
    return root


def _stable_workspace_asset_identity(root, path, lexical_key):
    """Bind an existing regular asset to a stable device/inode identity.

    Missing assets retain their lexical identity so the visual builder can still
    plan a new file.  Existing aliases are opened and checked through the same
    inode generation before their identity is trusted; hard links therefore
    collapse while symlinks, junctions, non-regular files, and replacement races
    fail closed.
    """

    try:
        candidate = safe_workspace_entry(root, path)
    except (OSError, ValueError) as exc:
        return ("path", lexical_key), "asset path is unsafe: %r (%s)" % (path, exc)
    if not os.path.lexists(str(candidate)):
        return ("path", lexical_key), None
    try:
        before = os.lstat(str(candidate))
        if (is_link_or_reparse(candidate) or not stat.S_ISREG(before.st_mode)):
            return (
                ("path", lexical_key),
                "existing asset is not a regular non-reparse file: %r" % path,
            )
        before_identity = (
            int(getattr(before, "st_dev", 0)),
            int(getattr(before, "st_ino", 0)),
        )
        if before_identity[1] == 0:
            return (
                ("path", lexical_key),
                "filesystem exposes no stable identity for existing asset: %r" % path,
            )
        before_generation = (
            int(before.st_size),
            int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1000000000))),
        )
        with open(candidate, "rb") as stream:
            opened = os.fstat(stream.fileno())
        after = os.lstat(str(candidate))
        opened_identity = (
            int(getattr(opened, "st_dev", 0)),
            int(getattr(opened, "st_ino", 0)),
        )
        after_identity = (
            int(getattr(after, "st_dev", 0)),
            int(getattr(after, "st_ino", 0)),
        )
        opened_generation = (
            int(opened.st_size),
            int(getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1000000000))),
        )
        after_generation = (
            int(after.st_size),
            int(getattr(after, "st_mtime_ns", int(after.st_mtime * 1000000000))),
        )
        if (not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or is_link_or_reparse(candidate)
                or opened_identity != before_identity
                or after_identity != before_identity
                or opened_generation != before_generation
                or after_generation != before_generation):
            return (
                ("path", lexical_key),
                "existing asset changed while its physical identity was captured: %r" % path,
            )
        return ("file", before_identity[0], before_identity[1]), None
    except OSError as exc:
        return (
            ("path", lexical_key),
            "cannot capture existing asset identity for %r: %s" % (path, exc),
        )


def _stable_workspace_asset_sha256(root, path):
    """Hash one declared asset generation without trusting a pathname twice."""

    try:
        candidate = safe_workspace_entry(root, path)
    except (OSError, ValueError) as exc:
        return None, "asset path is unsafe: %r (%s)" % (path, exc)
    if not os.path.lexists(str(candidate)):
        return None, "digest-bound asset is missing: %r" % path
    try:
        before = os.lstat(str(candidate))
        if (is_link_or_reparse(candidate) or not stat.S_ISREG(before.st_mode)):
            return None, "digest-bound asset is not a regular non-reparse file: %r" % path
        identity = (
            int(getattr(before, "st_dev", 0)),
            int(getattr(before, "st_ino", 0)),
        )
        generation = (
            int(before.st_size),
            int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1000000000))),
        )
        digest = hashlib.sha256()
        with open(candidate, "rb") as stream:
            opened = os.fstat(stream.fileno())
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after_handle = os.fstat(stream.fileno())
        after = os.lstat(str(candidate))

        def file_identity(value):
            return (
                int(getattr(value, "st_dev", 0)),
                int(getattr(value, "st_ino", 0)),
            )

        def file_generation(value):
            return (
                int(value.st_size),
                int(getattr(
                    value, "st_mtime_ns", int(value.st_mtime * 1000000000)
                )),
            )

        if (identity[1] == 0
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(after_handle.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or is_link_or_reparse(candidate)
                or file_identity(opened) != identity
                or file_identity(after_handle) != identity
                or file_identity(after) != identity
                or file_generation(opened) != generation
                or file_generation(after_handle) != generation
                or file_generation(after) != generation):
            return None, "digest-bound asset changed while it was read: %r" % path
        return digest.hexdigest(), None
    except OSError as exc:
        return None, "cannot hash digest-bound asset %r: %s" % (path, exc)


def _iter_asset_revision_declarations(values):
    """Yield ``(path, expected_sha256)`` from top-level and nested records."""

    for value in values or ():
        path = _field(value, "asset_path")
        metadata = _field(value, "metadata")
        if isinstance(path, str):
            expected = metadata.get("asset_sha256") if isinstance(metadata, dict) else None
            if expected is not None:
                yield path, expected
        for asset in _nested_assets(value):
            if isinstance(asset, dict) and asset.get("sha256") is not None:
                yield asset.get("path"), asset.get("sha256")


def _asset_identity_token(identity):
    """Return the stable public token for an existing regular-file identity."""

    if (isinstance(identity, tuple) and len(identity) == 3
            and identity[0] == "file"):
        return "file:%d:%d" % (identity[1], identity[2])
    return None


def workspace_asset_is_student_attempt(path, workspace, policy):
    """Check lexical and live hardlink identity against a workspace policy snapshot.

    This is the required boundary for free-form Markdown/wiki image paths: such a
    path may never have appeared in quiz, teaching, or content-unit declarations,
    yet it can still be a hardlink alias of declared student work.
    """

    if not isinstance(policy, dict):
        raise ValueError("workspace asset policy must be a live snapshot object")
    tainted_keys = policy.get("tainted_keys")
    tainted_identities = policy.get("tainted_identity_keys")
    if (not isinstance(tainted_keys, (set, frozenset))
            or not isinstance(tainted_identities, (set, frozenset))
            or any(not isinstance(value, str) for value in tainted_keys)
            or any(not isinstance(value, str) for value in tainted_identities)):
        raise ValueError("workspace asset policy has invalid taint capabilities")
    key = physical_asset_key(path)
    if key is None:
        raise ValueError("asset path is unsafe or invalid: %r" % path)
    if key in tainted_keys:
        return True
    root = _asset_workspace_root(workspace)
    identity, problem = _stable_workspace_asset_identity(root, path, key)
    if problem:
        raise ValueError(problem)
    token = _asset_identity_token(identity)
    return token is not None and token in tainted_identities


def workspace_asset_identity_key(path, workspace):
    """Return a stable live comparison key, collapsing existing hardlink aliases."""

    key = physical_asset_key(path)
    if key is None:
        raise ValueError("asset path is unsafe or invalid: %r" % path)
    root = _asset_workspace_root(workspace)
    identity, problem = _stable_workspace_asset_identity(root, path, key)
    if problem:
        raise ValueError(problem)
    token = _asset_identity_token(identity)
    return token if token is not None else "path:" + key


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _nested_assets(value):
    direct = _field(value, "assets")
    if isinstance(direct, (list, tuple)):
        yield from direct
    metadata = _field(value, "metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("assets")
        if isinstance(nested, (list, tuple)):
            yield from nested


def iter_asset_declarations(values):
    """Yield ``(original_path, role)`` from item/unit top-level and nested assets."""

    for value in values or ():
        path = _field(value, "asset_path")
        role = _field(value, "asset_role")
        if path is not None or role is not None:
            yield path, role
        for asset in _nested_assets(value):
            if isinstance(asset, dict):
                yield asset.get("path"), asset.get("role")


def collect_asset_roles(*collections):
    """Return ``physical_key -> roles`` across every supplied collection."""

    roles_by_key = {}
    for values in collections:
        for path, role in iter_asset_declarations(values):
            key = physical_asset_key(path)
            if key is None or not isinstance(role, str):
                continue
            roles_by_key.setdefault(key, set()).add(role)
    return roles_by_key


def student_attempt_tainted_keys(*collections):
    """Return every physical path declared as a student attempt anywhere."""

    return {
        key for key, roles in collect_asset_roles(*collections).items()
        if STUDENT_ATTEMPT in roles
    }


def is_student_attempt_tainted(path, tainted_keys):
    key = physical_asset_key(path)
    return key is not None and key in tainted_keys


def has_tainted_official_asset(value, tainted_keys):
    """Whether one item/unit launders a tainted path through a non-attempt role."""

    return any(
        role != STUDENT_ATTEMPT and is_student_attempt_tainted(path, tainted_keys)
        for path, role in iter_asset_declarations((value,))
    )


def canonical_chapter_key(value, *, allow_phase=False):
    """Normalize int/digit/chNN/chapter-N locators for cross-layer item identity."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value) if value >= 1 else None
    text = str(value).strip()
    import re
    prefix = r"(?:ch(?:apter)?|phase)" if allow_phase else r"ch(?:apter)?"
    match = re.fullmatch(r"(?:%s[\s_-]*)?0*(\d+)" % prefix, text, re.I)
    return str(int(match.group(1))) if match and int(match.group(1)) >= 1 else None


def _canonical_source_item_id(value):
    """Return the stable logical source-item ID without altering display data."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Match ingest.py's established `str(raw_id).strip()` identity exactly:
        # numeric 1 and 1.0 (likewise -0.0 and 0.0) remain distinct IDs.
        return str(value).strip() if math.isfinite(value) else None
    if (isinstance(value, str) and value and value == value.strip()
            and not _has_unicode_control(value)):
        return unicodedata.normalize("NFC", value)
    return None


def _canonical_external_id(value):
    if (isinstance(value, str) and value and value == value.strip()
            and not _has_unicode_control(value)):
        return unicodedata.normalize("NFC", value)
    return None


def _has_unicode_control(value):
    """Reject C0/C1/DEL controls from every logical identity surface."""

    return any(unicodedata.category(char) == "Cc" for char in value)


def _row_chapter(row, label, conflicts, *, source_row=False):
    values = []
    # ContentUnit.phase_id is a workflow phase namespace, not a chapter alias
    # (chapter 5 may legitimately be phase01).  Only legacy source rows use
    # `phase` as a chapter locator.
    names = ("chapter", "phase", "chapter_id") if source_row else ("chapter_id",)
    for name in names:
        raw = _field(row, name)
        if raw is None:
            continue
        key = canonical_chapter_key(raw, allow_phase=(source_row and name == "phase"))
        if key is None:
            conflicts.append("%s has invalid %s locator %r" % (label, name, raw))
            continue
        values.append((name, key))
    if len({key for _name, key in values}) > 1:
        conflicts.append("%s has contradictory chapter/phase locators: %s"
                         % (label, values))
    return values[0][1] if values else None


def _declared_assets(row, label, invalid, allowed_roles=KNOWN_ASSET_ROLES,
                     allow_null_assets=False):
    """Return validated ``(path, role, physical_key)`` declarations for one row."""

    output = []
    top_path = _field(row, "asset_path")
    top_role = _field(row, "asset_role")
    if top_path is not None or top_role is not None:
        if top_path is None or top_role is None:
            invalid.append("%s top-level asset_path/asset_role must be a complete pair" % label)
        else:
            key = physical_asset_key(top_path)
            if key is None:
                invalid.append("%s.asset_path is unsafe or invalid: %r" % (label, top_path))
            if not isinstance(top_role, str) or top_role not in allowed_roles:
                invalid.append("%s.asset_role is missing, non-string, or unknown: %r"
                               % (label, top_role))
            if key is not None and isinstance(top_role, str) and top_role in allowed_roles:
                output.append((top_path, top_role, key))

    containers = []
    if isinstance(row, dict) and "assets" in row:
        containers.append((row.get("assets"), label + ".assets"))
    elif not isinstance(row, dict) and hasattr(row, "assets"):
        containers.append((getattr(row, "assets"), label + ".assets"))
    metadata = _field(row, "metadata")
    if isinstance(metadata, dict) and "assets" in metadata:
        containers.append((metadata.get("assets"), label + ".metadata.assets"))
    for assets, path in containers:
        if assets is None and allow_null_assets and path == label + ".assets":
            # Legacy quiz/teaching rows used JSON null to mean no assets; the
            # visual `--apply` path normalizes this to an array.  This exception
            # is source-layer/top-level only. ContentUnit metadata remains strict.
            continue
        if not isinstance(assets, (list, tuple)):
            invalid.append("%s must be an array" % path)
            continue
        for index, asset in enumerate(assets):
            item_path = "%s[%d]" % (path, index)
            if not isinstance(asset, dict):
                invalid.append("%s must be an object" % item_path)
                continue
            raw_path = asset.get("path")
            role = asset.get("role")
            key = physical_asset_key(raw_path)
            if key is None:
                invalid.append("%s.path is missing, unsafe, or invalid: %r"
                               % (item_path, raw_path))
            if not isinstance(role, str) or role not in allowed_roles:
                invalid.append("%s.role is missing, non-string, or unknown: %r"
                               % (item_path, role))
            if key is not None and isinstance(role, str) and role in allowed_roles:
                output.append((raw_path, role, key))
    return output


def audit_asset_policy(
        quiz_rows=(), teaching_rows=(), content_units=(), *, workspace=None,
        allow_missing_workspace_assets=False):
    """Audit all asset declarations and derive pair-aware logical-item groups.

    The result is deterministic and side-effect free.  A question's ``paired_unit_id`` propagates
    its chapter/external-ID identity to an answer that omits ``external_id``; contradictory paired
    identities fail closed.  Prompt/answer path reuse is rejected only within the same logical
    item, while any student-attempt occurrence taints that physical path workspace-wide.
    """

    # Materialize once: callers may supply generators, and the policy must not
    # silently see a different/empty population in its later aggregation pass.
    quiz_rows = list(quiz_rows or ())
    teaching_rows = list(teaching_rows or ())
    content_units = list(content_units or ())
    labelled = (
        ("references/quiz_bank.json", quiz_rows, "source"),
        ("references/teaching_examples.json", teaching_rows, "source"),
        (".ingest/content_units.jsonl", content_units, "unit"),
    )
    invalid = []
    conflicts = []
    declarations = {}
    row_groups = {}
    unit_rows = {}
    unit_labels = {}
    unit_chapters = {}
    source_ids_by_layer = {}

    workspace_root = None
    if workspace is not None:
        try:
            workspace_root = _asset_workspace_root(workspace)
        except (OSError, ValueError) as exc:
            invalid.append("workspace asset policy root is unsafe: %s" % exc)

    for layer, rows, kind in labelled:
        for index, row in enumerate(rows):
            label = "%s[%d]" % (layer, index)
            if not isinstance(row, dict) and not hasattr(row, "metadata"):
                invalid.append("%s must be an object" % label)
                continue
            declarations[id(row)] = _declared_assets(
                row,
                label,
                invalid,
                SOURCE_ITEM_ASSET_ROLES if kind == "source" else KNOWN_ASSET_ROLES,
                allow_null_assets=(kind == "source"),
            )
            chapter = _row_chapter(row, label, conflicts, source_row=(kind == "source"))
            if kind == "source":
                raw_identity = _field(row, "id")
                identity = _canonical_source_item_id(raw_identity)
                if declarations[id(row)] and identity is None:
                    conflicts.append(
                        "%s has asset evidence but no stable item id" % label
                    )
                row_groups[id(row)] = (
                    "item", chapter or "", identity if identity is not None else label,
                )
                if identity is not None:
                    layer_ids = source_ids_by_layer.setdefault(layer, {})
                    # Source-bank IDs are unique across the whole layer, not
                    # merely within a chapter; quiz and teaching layers remain
                    # independent for backward-compatible cross-layer merging.
                    logical_key = identity
                    if logical_key in layer_ids:
                        conflicts.append(
                            "%s and %s are canonical duplicate item identities"
                            % (layer_ids[logical_key], label)
                        )
                    else:
                        layer_ids[logical_key] = label
            else:
                unit_id = _field(row, "unit_id")
                key = str(unit_id) if unit_id is not None else label
                if key in unit_rows:
                    conflicts.append("duplicate content-unit identity in asset policy: %s" % key)
                unit_rows[key] = row
                unit_labels[key] = label
                unit_chapters[key] = chapter

    # Union paired content units before assigning a logical item identity.
    parent = {key: key for key in unit_rows}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for unit_id, row in unit_rows.items():
        paired = _field(row, "paired_unit_id")
        if paired is None:
            continue
        paired = str(paired)
        if paired == unit_id:
            conflicts.append("%s cannot pair with itself" % unit_labels[unit_id])
            continue
        if paired not in unit_rows:
            conflicts.append("%s paired_unit_id references missing unit %r"
                             % (unit_labels[unit_id], paired))
            continue
        other = unit_rows[paired]
        kinds = {_field(row, "kind"), _field(other, "kind")}
        if kinds != {"question", "answer"}:
            conflicts.append(
                "%s paired_unit_id must form exactly one question/answer pair"
                % unit_labels[unit_id]
            )
            continue
        reciprocal = _field(other, "paired_unit_id")
        if reciprocal is None or str(reciprocal) != unit_id:
            conflicts.append(
                "%s paired_unit_id is not reciprocal with %s"
                % (unit_labels[unit_id], unit_labels[paired])
            )
            continue
        union(unit_id, paired)

    components = {}
    for unit_id in unit_rows:
        components.setdefault(find(unit_id), []).append(unit_id)
    for component_ids in components.values():
        chapters = set()
        external_ids = set()
        phase_ids = set()
        for unit_id in component_ids:
            row = unit_rows[unit_id]
            chapter = unit_chapters[unit_id]
            if chapter:
                chapters.add(chapter)
            external_id = _field(row, "external_id")
            if external_id is not None:
                canonical_external = _canonical_external_id(external_id)
                if canonical_external is None:
                    conflicts.append(
                        "%s has an invalid or untrimmed external_id"
                        % unit_labels[unit_id]
                    )
                else:
                    external_ids.add(canonical_external)
            phase_id = _field(row, "phase_id")
            if phase_id is not None:
                phase_ids.add(str(phase_id))
        if len(chapters) > 1:
            conflicts.append("paired content units span contradictory chapters: %s"
                             % sorted(chapters))
        if len(external_ids) > 1:
            conflicts.append("paired content units have contradictory item identities: %s"
                             % sorted(external_ids))
        if len(phase_ids) > 1:
            conflicts.append("paired content units span contradictory phase IDs: %s"
                             % sorted(phase_ids))
        official_qa_assets = any(
            _field(unit_rows[unit_id], "kind") in ("question", "answer")
            and any(
                role in PROMPT_ASSET_ROLES or role in ANSWER_ASSET_ROLES
                for _path, role, _key in declarations.get(id(unit_rows[unit_id]), ())
            )
            for unit_id in component_ids
        )
        if official_qa_assets and not external_ids:
            conflicts.append(
                "official question/answer asset evidence lacks a canonical external_id; "
                "at least one side of a reciprocal pair must provide one: %s"
                % sorted(component_ids)
            )
        if external_ids:
            group = (
                "item",
                sorted(chapters)[0] if chapters else "",
                sorted(external_ids)[0],
            )
        else:
            group = ("unit-component", tuple(sorted(component_ids)))
        for unit_id in component_ids:
            row_groups[id(unit_rows[unit_id])] = group

    # Backward-compatible source rows occasionally omit a chapter even though
    # their stable item ID is also present in a scoped content unit.  Merge only
    # when exactly one chapter is provable across every layer; ambiguity and a
    # lone unscoped asset row fail closed.
    known_chapters = {}
    for group in row_groups.values():
        if group[0] == "item" and group[1]:
            known_chapters.setdefault(group[2], set()).add(group[1])
    for layer, rows, _kind in labelled:
        for index, row in enumerate(rows):
            group = row_groups.get(id(row))
            if not group or group[0] != "item" or group[1]:
                continue
            chapters = known_chapters.get(group[2], set())
            if len(chapters) == 1:
                row_groups[id(row)] = ("item", next(iter(chapters)), group[2])
            elif declarations.get(id(row)):
                label = "%s[%d]" % (layer, index)
                if chapters:
                    conflicts.append(
                        "%s has unscoped asset evidence with ambiguous item chapters %s"
                        % (label, sorted(chapters))
                    )
                else:
                    conflicts.append(
                        "%s has asset evidence but no stable chapter/phase locator" % label
                    )

    identity_by_key = {}
    if workspace_root is not None:
        identity_captures = {}
        for rows in (quiz_rows, teaching_rows, content_units):
            for row in rows:
                for path, _role, key in declarations.get(id(row), ()):
                    captured = identity_captures.get(key)
                    if captured is None:
                        identity, problem = _stable_workspace_asset_identity(
                            workspace_root, path, key
                        )
                        captured = (path, identity, problem)
                        identity_captures[key] = captured
                    _captured_path, identity, problem = captured
                    if problem and key not in identity_by_key:
                        invalid.append(problem)
                    previous = identity_by_key.setdefault(key, identity)
                    if previous != identity:
                        invalid.append(
                            "asset path identity changed across declarations: %r" % path
                        )
        digest_cache = {}
        for rows in (quiz_rows, teaching_rows, content_units):
            for path, expected in _iter_asset_revision_declarations(rows):
                key = physical_asset_key(path)
                if key is None:
                    # The declaration schema already reports the unsafe path.
                    continue
                if (not isinstance(expected, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                    invalid.append(
                        "asset %r has an invalid expected sha256 revision" % path
                    )
                    continue
                if key not in digest_cache:
                    digest_cache[key] = _stable_workspace_asset_sha256(
                        workspace_root, path
                    )
                actual, problem = digest_cache[key]
                if problem:
                    missing_is_deferred = False
                    if allow_missing_workspace_assets:
                        try:
                            candidate = safe_workspace_entry(workspace_root, path)
                            missing_is_deferred = not os.path.lexists(str(candidate))
                        except (OSError, ValueError):
                            missing_is_deferred = False
                    if not missing_is_deferred:
                        invalid.append(problem)
                elif actual != expected:
                    conflicts.append(
                        "asset revision drift for %r: expected %s, found %s"
                        % (path, expected, actual)
                    )
        # Recheck each unique path once after all declarations/digests.  This
        # preserves drift detection without reopening the same asset for every
        # quiz/teaching/content-unit alias (which made large workspaces
        # effectively quadratic on Windows).
        for key, (path, first_identity, first_problem) in identity_captures.items():
            if first_problem:
                continue
            final_identity, final_problem = _stable_workspace_asset_identity(
                workspace_root, path, key
            )
            if final_problem:
                invalid.append(final_problem)
            elif final_identity != first_identity:
                invalid.append(
                    "asset path identity changed during policy snapshot: %r" % path
                )

    def physical_identity(key):
        return identity_by_key.get(key, ("path", key))

    tainted_identities = set()
    for rows in (quiz_rows or (), teaching_rows or (), content_units or ()):
        for row in rows:
            for _path, role, key in declarations.get(id(row), ()):
                if role == STUDENT_ATTEMPT:
                    tainted_identities.add(physical_identity(key))
    # Consumers compare portable lexical keys.  Expand the taint to every
    # declared alias whose stable live identity is an attempted-work file.
    tainted = {
        key for key in {
            key
            for rows in (quiz_rows, teaching_rows, content_units)
            for row in rows
            for _path, _role, key in declarations.get(id(row), ())
        }
        if physical_identity(key) in tainted_identities
    }
    tainted_identity_keys = {
        token for token in (
            _asset_identity_token(identity) for identity in tainted_identities
        )
        if token is not None
    }

    groups = {}
    for rows in (quiz_rows or (), teaching_rows or (), content_units or ()):
        for row in rows:
            group = row_groups.get(id(row))
            if group is not None:
                groups.setdefault(group, []).extend(declarations.get(id(row), ()))
            for path, role, key in declarations.get(id(row), ()):
                if physical_identity(key) in tainted_identities and role != STUDENT_ATTEMPT:
                    conflicts.append(
                        "%r re-declares globally student_attempt-tainted physical asset %r as %s"
                        % (group, path, role)
                    )
    for group, rows in groups.items():
        roles_by_key = {}
        for _path, role, key in rows:
            roles_by_key.setdefault(physical_identity(key), set()).add(role)
        for roles in roles_by_key.values():
            if roles & PROMPT_ASSET_ROLES and roles & ANSWER_ASSET_ROLES:
                conflicts.append(
                    "item %r declares one physical asset as both prompt and official answer"
                    % (group,)
                )
    return {
        "tainted_keys": tainted,
        "tainted_identity_keys": tainted_identity_keys,
        "conflicts": conflicts,
        "invalid_declarations": invalid,
        "item_groups": groups,
    }


def _stable_runtime_blob(root, relative_path, label, max_bytes):
    """Read one independent regular file and bind bytes to one live generation."""

    try:
        canonical = normalize_workspace_path(relative_path)
    except ValueError as exc:
        raise ValueError("%s path is unsafe: %s" % (label, exc)) from exc
    if canonical != relative_path:
        raise ValueError("%s path is not canonical POSIX form" % label)
    try:
        candidate = safe_workspace_entry(root, canonical)
        before = os.lstat(str(candidate))
    except (OSError, ValueError) as exc:
        raise ValueError("%s is missing or unsafe: %s" % (label, exc)) from exc
    if (is_link_or_reparse(candidate) or not stat.S_ISREG(before.st_mode)
            or int(getattr(before, "st_nlink", 1)) != 1):
        raise ValueError(
            "%s must be an independent regular file, not a link/reparse/hardlink"
            % label
        )
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise ValueError(
            "%s must be non-empty and no larger than %d bytes"
            % (label, max_bytes)
        )

    def identity(value):
        return (
            int(getattr(value, "st_dev", 0)),
            int(getattr(value, "st_ino", 0)),
        )

    def generation(value):
        return (
            int(value.st_size),
            int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1000000000))),
            int(getattr(value, "st_nlink", 1)),
        )

    initial_identity = identity(before)
    initial_generation = generation(before)
    if initial_identity[1] == 0:
        raise ValueError("%s filesystem exposes no stable physical identity" % label)
    payload = bytearray()
    try:
        with open(candidate, "rb") as stream:
            opened = os.fstat(stream.fileno())
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                payload.extend(block)
                if len(payload) > max_bytes:
                    raise ValueError("%s exceeds the byte-size safety bound" % label)
            after_handle = os.fstat(stream.fileno())
        after = os.lstat(str(candidate))
    except OSError as exc:
        raise ValueError("%s cannot be read: %s" % (label, exc)) from exc
    if (not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(after_handle.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or is_link_or_reparse(candidate)
            or identity(opened) != initial_identity
            or identity(after_handle) != initial_identity
            or identity(after) != initial_identity
            or generation(opened) != initial_generation
            or generation(after_handle) != initial_generation
            or generation(after) != initial_generation):
        raise ValueError("%s changed while its bytes were captured" % label)
    token = _asset_identity_token((
        "file", initial_identity[0], initial_identity[1],
    ))
    return {
        "path": canonical,
        "payload": bytes(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": initial_generation[0],
        "mtime_ns": initial_generation[1],
        "physical_identity": token,
    }


def quiz_bank_stat_baseline(workspace):
    """Capture only safe filesystem metadata for a pre-existing quiz bank.

    Startup deliberately does not open or parse the bank.  The first explicit
    quiz/checkpoint transition performs the strict content read and binds its
    exact revision separately.
    """

    root = _asset_workspace_root(workspace)
    relative = "references/quiz_bank.json"
    try:
        candidate = safe_workspace_entry(root, relative)
    except (OSError, ValueError) as exc:
        raise ValueError("quiz bank baseline path is unsafe: %s" % exc) from exc
    if not os.path.lexists(str(candidate)):
        unsigned = {
            "schema_version": 1,
            "receipt_type": "lightweight_quiz_bank_baseline",
            "path": relative,
            "exists": False,
            "size_bytes": None,
            "mtime_ns": None,
            "physical_identity": None,
        }
    else:
        try:
            current = os.lstat(str(candidate))
        except OSError as exc:
            raise ValueError("quiz bank baseline cannot be inspected: %s" % exc) from exc
        if (is_link_or_reparse(candidate) or not stat.S_ISREG(current.st_mode)
                or int(getattr(current, "st_nlink", 1)) != 1):
            raise ValueError(
                "pre-existing quiz bank must be an independent regular file"
            )
        inode = int(getattr(current, "st_ino", 0))
        if inode == 0:
            raise ValueError("quiz bank filesystem exposes no stable physical identity")
        unsigned = {
            "schema_version": 1,
            "receipt_type": "lightweight_quiz_bank_baseline",
            "path": relative,
            "exists": True,
            "size_bytes": int(current.st_size),
            "mtime_ns": int(getattr(
                current, "st_mtime_ns", int(current.st_mtime * 1000000000)
            )),
            "physical_identity": _asset_identity_token((
                "file", int(getattr(current, "st_dev", 0)), inode,
            )),
        }
    result = dict(unsigned)
    result["baseline_id"] = "quiz-base-" + hashlib.sha256(
        canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    return result


def validate_quiz_bank_stat_baseline(value):
    expected = {
        "schema_version", "receipt_type", "path", "exists", "size_bytes",
        "mtime_ns", "physical_identity", "baseline_id",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("quiz bank baseline fields are invalid")
    if (value.get("schema_version") != 1
            or value.get("receipt_type") != "lightweight_quiz_bank_baseline"
            or value.get("path") != "references/quiz_bank.json"
            or not isinstance(value.get("exists"), bool)):
        raise ValueError("quiz bank baseline identity is invalid")
    if value["exists"]:
        if (type(value.get("size_bytes")) is not int or value["size_bytes"] < 0
                or type(value.get("mtime_ns")) is not int or value["mtime_ns"] < 0
                or not re.fullmatch(
                    r"file:\d+:\d+", str(value.get("physical_identity") or "")
                )):
            raise ValueError("quiz bank present-baseline metadata is invalid")
    elif any(value.get(key) is not None for key in (
            "size_bytes", "mtime_ns", "physical_identity")):
        raise ValueError("quiz bank absent-baseline metadata must be null")
    unsigned = dict(value)
    baseline_id = unsigned.pop("baseline_id")
    expected_id = "quiz-base-" + hashlib.sha256(
        canonical_json(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    if baseline_id != expected_id:
        raise ValueError("quiz bank baseline digest is invalid")
    return value


def load_lightweight_quiz_bank_baseline(workspace):
    """Read only the small session ledger and return its immutable init baseline."""

    session_file = _stable_runtime_blob(
        _asset_workspace_root(workspace),
        ".lightweight/session.json",
        "lightweight session",
        QUIZ_RUNTIME_MAX_SESSION_BYTES,
    )
    try:
        session = json.loads(
            session_file["payload"].decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("non-finite JSON constant: %s" % value)
            ),
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("lightweight session is not strict UTF-8 JSON") from exc
    if not isinstance(session, dict):
        raise ValueError("lightweight session top level must be an object")
    baseline = session.get("quiz_bank_baseline")
    validate_quiz_bank_stat_baseline(baseline)
    return baseline


def _current_quiz_bank_stat(workspace):
    current = quiz_bank_stat_baseline(workspace)
    current.pop("baseline_id")
    return current


def _quiz_runtime_has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return bool(value)
    return True


def _quiz_runtime_scope_reason(item, chapter_key):
    raw_values = [
        item.get(name) for name in ("chapter", "phase", "chapter_id")
        if item.get(name) is not None
    ]
    if not raw_values:
        return "scope_missing"
    keys = []
    for value in raw_values:
        key = canonical_chapter_key(value, allow_phase=True)
        if key is None:
            return "scope_invalid"
        keys.append(key)
    if chapter_key is not None and chapter_key not in set(keys):
        return "out_of_scope"
    return None


def _quiz_runtime_choice_error(item):
    options = item.get("options")
    if (not isinstance(options, list) or len(options) < 2
            or any(not isinstance(value, str) or not value.strip()
                   for value in options)):
        return "choice_options_invalid"
    answer = item.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return "choice_answer_missing"

    def forms(value):
        raw = value.strip()
        match = re.match(r"^\s*([A-Za-z0-9]+)\s*[.．)：:、]\s*(.*?)\s*$", raw)
        if match:
            return raw.casefold(), match.group(1).casefold(), match.group(2).casefold()
        return raw.casefold(), raw.casefold(), raw.casefold()

    answer_forms = set(forms(answer))
    matches = [
        index for index, option in enumerate(options)
        if answer_forms & set(forms(option))
    ]
    return None if len(matches) == 1 else "choice_answer_not_unique_option"


def _quiz_runtime_oracle_error(item):
    qtype = item.get("type")
    if qtype == "choice":
        return _quiz_runtime_choice_error(item)
    if qtype == "subjective":
        keywords = item.get("keywords")
        if keywords is None:
            keywords = item.get("answer_keywords")
        if (not isinstance(keywords, list) or not keywords
                or any(not isinstance(value, str) or not value.strip()
                       for value in keywords)):
            return "subjective_keywords_missing"
        return None
    if qtype == "code":
        if not isinstance(item.get("language"), str) or not item["language"].strip():
            return "code_language_missing"
        if not (_quiz_runtime_has_value(item.get("expected_behavior"))
                or _quiz_runtime_has_value(item.get("tests"))):
            return "code_oracle_missing"
        return None
    if qtype == "true_false":
        answer = item.get("answer")
        if isinstance(answer, bool):
            return None
        if (isinstance(answer, str)
                and answer.strip().casefold() in QUIZ_RUNTIME_TRUE_FALSE):
            return None
        return "true_false_oracle_invalid"
    if not _quiz_runtime_has_value(item.get("answer")):
        return "answer_missing"
    return None


def _quiz_runtime_ai_provenance_error(item):
    flag = item.get("ai_generated")
    if flag is not None and not isinstance(flag, bool):
        return "ai_generated_flag_invalid"
    source = item.get("source")
    answer_provenance = item.get("answer_provenance")
    if answer_provenance is not None and not isinstance(answer_provenance, str):
        return "answer_provenance_invalid"
    marked_ai = (
        flag is True
        or source == "ai_generated"
        or answer_provenance in ("ai_generated", "ai_supplemented")
    )
    if marked_ai and source not in ("ai_generated", "mixed"):
        return "ai_answer_provenance_invalid"
    if flag is False and source == "ai_generated":
        return "ai_answer_provenance_conflict"
    return None


def _quiz_runtime_prompt_assets_error(
        workspace, item, policy, cache, visual_required):
    assets = item.get("assets")
    if assets is not None and not isinstance(assets, list):
        return "assets_invalid"
    prompt_assets = [
        asset for asset in (assets or ())
        if isinstance(asset, dict) and asset.get("role") in PROMPT_ASSET_ROLES
    ]
    if not visual_required:
        return None
    if not prompt_assets:
        return "prompt_asset_missing"
    root = _asset_workspace_root(workspace)
    tainted_keys = policy.get("tainted_keys")
    tainted_identities = policy.get("tainted_identity_keys")
    for asset in prompt_assets:
        path = asset.get("path")
        if not isinstance(path, str):
            return "prompt_asset_path_invalid"
        try:
            canonical = normalize_workspace_path(path)
        except ValueError:
            return "prompt_asset_path_invalid"
        if canonical != path:
            return "prompt_asset_path_noncanonical"
        key = physical_asset_key(path)
        if key in tainted_keys:
            return "student_attempt_asset_conflict"
        expected = asset.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            return "prompt_asset_revision_missing"
        extension = os.path.splitext(canonical)[1].lower()
        inferred_mime = QUIZ_RUNTIME_IMAGE_MIMES.get(extension)
        if inferred_mime is None:
            return "prompt_asset_format_unsupported"
        declared_mime = asset.get("media_type") or asset.get("mime_type")
        if declared_mime is not None:
            if not isinstance(declared_mime, str):
                return "prompt_asset_mime_invalid"
            normalized_mime = declared_mime.lower().split(";", 1)[0].strip()
            if normalized_mime == "image/jpg":
                normalized_mime = "image/jpeg"
            if normalized_mime != inferred_mime:
                return "prompt_asset_mime_mismatch"
        source_file = asset.get("source_file")
        source_sha = asset.get("source_sha256")
        if (source_file is None) != (source_sha is None):
            return "prompt_asset_source_revision_incomplete"
        if source_file is not None:
            try:
                normalized_source = normalize_workspace_path(source_file)
            except ValueError:
                return "prompt_asset_source_path_invalid"
            if (normalized_source != source_file
                    or not isinstance(source_sha, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", source_sha)):
                return "prompt_asset_source_revision_invalid"
        cached = cache.get(canonical)
        if cached is None:
            try:
                cached = _stable_runtime_blob(
                    root, canonical, "quiz prompt asset", QUIZ_RUNTIME_MAX_IMAGE_BYTES
                )
            except ValueError:
                return "prompt_asset_unreadable_or_aliased"
            cache[canonical] = cached
        if cached["sha256"] != expected:
            return "prompt_asset_revision_drift"
        if cached["physical_identity"] in tainted_identities:
            return "student_attempt_asset_conflict"
        try:
            try:
                from image_validation import validate_image_blob
            except ImportError:  # pragma: no cover - package import
                from scripts.image_validation import validate_image_blob
            width, height = validate_image_blob(inferred_mime, cached["payload"])
        except (ImportError, ValueError):
            return "prompt_asset_not_decodable"
        if width < 1 or height < 1:
            return "prompt_asset_dimensions_invalid"
    return None


def quiz_runtime_eligibility(workspace, policy, chapter=None, baseline=None):
    """Return the only quiz rows safe to select, grade, or count as evidence.

    ``policy`` must be the complete live three-layer workspace policy snapshot;
    callers obtain it only at an explicit quiz/checkpoint/completion transition.
    Routine lightweight mount/readiness must not call this function.
    """

    global_errors = []
    if not isinstance(policy, dict) or not isinstance(policy.get("quiz_rows"), list):
        global_errors.append("quiz_runtime_policy_invalid")
        rows = []
    else:
        rows = policy["quiz_rows"]
    for key, code in (
            ("unsafe_paths", "quiz_runtime_policy_unsafe"),
            ("conflicts", "quiz_runtime_policy_conflict")):
        values = policy.get(key) if isinstance(policy, dict) else None
        if not isinstance(values, list):
            global_errors.append("quiz_runtime_policy_invalid")
        elif values:
            global_errors.append(code)
    if (not isinstance(policy, dict)
            or not isinstance(policy.get("tainted_keys"), (set, frozenset))
            or not isinstance(policy.get("tainted_identity_keys"), (set, frozenset))):
        global_errors.append("quiz_runtime_policy_invalid")

    chapter_key = None
    if chapter is not None:
        chapter_key = canonical_chapter_key(chapter, allow_phase=True)
        if chapter_key is None:
            global_errors.append("quiz_runtime_target_scope_invalid")

    current_bank = None
    if baseline is not None:
        try:
            validate_quiz_bank_stat_baseline(baseline)
            current_bank = _current_quiz_bank_stat(workspace)
        except (OSError, ValueError):
            global_errors.append("quiz_bank_baseline_unreadable")
        else:
            if not baseline["exists"]:
                global_errors.append("quiz_bank_not_preexisting")
            else:
                expected = {
                    key: baseline[key] for key in (
                        "schema_version", "receipt_type", "path", "exists",
                        "size_bytes", "mtime_ns", "physical_identity",
                    )
                }
                if current_bank != expected:
                    global_errors.append("quiz_bank_changed_since_lightweight_init")

    identity_counts = Counter()
    canonical_ids = []
    for row in rows:
        ident = row.get("id") if isinstance(row, dict) else None
        if (isinstance(ident, (str, int)) and not isinstance(ident, bool)
                and stable_item_id_problem(str(ident)) is None):
            canonical = str(ident)
            canonical_ids.append(canonical)
            identity_counts[canonical] += 1
        else:
            canonical_ids.append(None)

    eligible = []
    eligible_by_id = {}
    excluded_by_id = {}
    exclusions = Counter()
    scoped_items = 0
    asset_cache = {}
    for index, item in enumerate(rows):
        ident = canonical_ids[index]
        reason = None
        if not isinstance(item, dict):
            reason = "item_not_object"
        elif ident is None:
            reason = "id_invalid"
        elif identity_counts[ident] != 1:
            reason = "duplicate_id"
        else:
            scope_reason = _quiz_runtime_scope_reason(item, chapter_key)
            if scope_reason == "out_of_scope":
                reason = scope_reason
            else:
                scoped_items += 1
                reason = scope_reason
        if reason is None:
            gradable = item.get("gradable")
            if gradable is not None and not isinstance(gradable, bool):
                reason = "gradable_invalid"
            elif gradable is False:
                reason = "non_gradable"
        if reason is None:
            if item.get("type") not in QUIZ_RUNTIME_TYPES:
                reason = "type_invalid"
            elif not isinstance(item.get("question"), str) or not item["question"].strip():
                reason = "question_missing"
        if reason is None:
            reason = _quiz_runtime_oracle_error(item)
        if reason is None:
            reason = _quiz_runtime_ai_provenance_error(item)
        if reason is None:
            requires = item.get("requires_assets")
            maybe = item.get("maybe_requires_assets")
            if ((requires is not None and not isinstance(requires, bool))
                    or (maybe is not None and not isinstance(maybe, bool))):
                reason = "asset_flag_invalid"
            else:
                visual = (
                    requires is True or maybe is True
                    or item.get("question_text_status") in ("stub", "page_reference")
                )
                reason = _quiz_runtime_prompt_assets_error(
                    workspace, item, policy, asset_cache, visual
                )
        if reason is None and not global_errors:
            eligible.append(item)
            eligible_by_id[ident] = item
        else:
            if reason is None:
                reason = "global_policy_blocked"
            if reason != "out_of_scope":
                exclusions[reason] += 1
            if ident is not None:
                excluded_by_id.setdefault(ident, set()).add(reason)

    bank_binding = None
    if not global_errors:
        try:
            bank_file = _stable_runtime_blob(
                _asset_workspace_root(workspace),
                "references/quiz_bank.json",
                "quiz bank",
                QUIZ_RUNTIME_MAX_BANK_BYTES,
            )
        except (OSError, ValueError):
            global_errors.append("quiz_bank_revision_unreadable")
            eligible = []
            eligible_by_id = {}
        else:
            try:
                parsed_bank = json.loads(
                    bank_file["payload"].decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError("non-finite JSON constant: %s" % value)
                    ),
                )
            except (UnicodeDecodeError, ValueError):
                global_errors.append("quiz_bank_revision_unreadable")
                eligible = []
                eligible_by_id = {}
                parsed_bank = None
            if parsed_bank != rows:
                global_errors.append("quiz_bank_changed_during_runtime_snapshot")
                eligible = []
                eligible_by_id = {}
            item_hashes = {
                ident: hashlib.sha256(
                    canonical_json(item).encode("utf-8")
                ).hexdigest()
                for ident, item in sorted(eligible_by_id.items())
            }
            unsigned = {
                "schema_version": 1,
                "receipt_type": "quiz_runtime_bank_binding",
                "path": bank_file["path"],
                "bank_sha256": bank_file["sha256"],
                "size_bytes": bank_file["size_bytes"],
                "mtime_ns": bank_file["mtime_ns"],
                "physical_identity": bank_file["physical_identity"],
                "baseline_id": baseline.get("baseline_id") if baseline else None,
                "chapter": chapter_key,
                "eligible_item_sha256": item_hashes,
            }
            bank_binding = dict(unsigned)
            bank_binding["binding_id"] = "quiz-bind-" + hashlib.sha256(
                canonical_json(unsigned).encode("utf-8")
            ).hexdigest()[:24]

    if global_errors:
        bank_binding = None

    return {
        "eligible_items": eligible,
        "eligible_by_id": eligible_by_id,
        "excluded_by_id": {
            ident: tuple(sorted(reasons))
            for ident, reasons in sorted(excluded_by_id.items())
        },
        "exclusion_counts": dict(sorted(exclusions.items())),
        "scoped_items": scoped_items,
        "global_errors": tuple(sorted(set(global_errors))),
        "bank_binding": bank_binding,
    }


def quiz_runtime_ref_error(result, item_id, binding=None):
    """Validate one canonical checkpoint ID and its optional immutable binding."""

    if not isinstance(result, dict):
        return "quiz runtime eligibility result is invalid"
    errors = result.get("global_errors") or ()
    if errors:
        return "quiz runtime gate is blocked: %s" % ",".join(errors)
    ident = str(item_id)
    item = (result.get("eligible_by_id") or {}).get(ident)
    if item is None:
        reasons = (result.get("excluded_by_id") or {}).get(ident)
        if reasons:
            return "checkpoint item is runtime-ineligible: %s" % ",".join(reasons)
        return "checkpoint ID is absent from the runtime-eligible bank: %s" % ident
    if binding is None:
        return None
    required = {"bank_binding_id", "bank_sha256", "item_sha256"}
    if not isinstance(binding, dict) or set(binding) != required:
        return "lightweight checkpoint revision binding is missing or malformed"
    bank = result.get("bank_binding")
    if not isinstance(bank, dict):
        return "quiz runtime bank binding is unavailable"
    expected_item_hash = (bank.get("eligible_item_sha256") or {}).get(ident)
    if (binding.get("bank_binding_id") != bank.get("binding_id")
            or binding.get("bank_sha256") != bank.get("bank_sha256")
            or binding.get("item_sha256") != expected_item_hash):
        return "lightweight checkpoint bank/item revision has drifted"
    return None


def _promotion_identity_resolver(root, identities=None):
    """Resolve each lexical path once while preserving physical-file aliases."""

    identities = {} if identities is None else identities

    def resolve(path):
        key = physical_asset_key(path)
        if key is None:
            raise ValueError("asset path is unsafe or invalid: %r" % path)
        canonical = normalize_workspace_path(path)
        if canonical not in identities:
            identity, problem = _stable_workspace_asset_identity(root, path, key)
            if problem:
                raise ValueError(problem)
            identities[canonical] = (
                path, _asset_identity_token(identity) or "path:" + key
            )
        return identities[canonical][1]

    return resolve


def _promotion_policy_index(policy, resolve):
    """Index all promotion facts in one pass over a complete policy snapshot."""

    index = {}

    def facts(identity):
        return index.setdefault(identity, {
            "roles": set(), "paths": set(), "groups": set(),
            "revisions": set(), "incomplete": False, "owners": [],
        })

    rows = tuple(policy.get(name) or () for name in (
        "quiz_rows", "teaching_rows", "content_units"
    ))
    for layer, collection in enumerate(rows):
        for row in collection:
            row_identities = set()
            for declared, role in iter_asset_declarations((row,)):
                identity = resolve(declared)
                item = facts(identity)
                item["roles"].add(role)
                item["paths"].add(normalize_workspace_path(declared))
                row_identities.add(identity)
            if isinstance(row, dict):
                if layer < 2 and row_identities:
                    try:
                        source = normalize_workspace_path(row.get("source_file"))
                    except ValueError:
                        source = None
                    owner = (row.get("source_type"), source)
                    for identity in row_identities:
                        facts(identity)["owners"].append(owner)
                for asset in _nested_assets(row):
                    if not isinstance(asset, dict):
                        continue
                    identity = resolve(asset.get("path"))
                    try:
                        revision = (
                            asset.get("sha256"),
                            normalize_workspace_path(asset.get("path")),
                            normalize_workspace_path(asset.get("source_file")),
                            asset.get("source_sha256"),
                        )
                    except ValueError:
                        revision = ()
                    item = facts(identity)
                    if (len(revision) != 4 or any(
                            not isinstance(value, str) for value in revision)
                            or not re.fullmatch(r"[0-9a-f]{64}", revision[0])
                            or not re.fullmatch(r"[0-9a-f]{64}", revision[3])):
                        item["incomplete"] = True
                    else:
                        item["revisions"].add(revision)
    for group, declarations in (policy.get("item_groups") or {}).items():
        for declared, _role, _key in declarations:
            facts(resolve(declared))["groups"].add(group)
    return index


def legacy_attempt_promotion_receipts(requests, old, new, workspace):
    """Authorize many legacy corrections with one old/new policy scan each.

    ``requests`` contains ``(path, sha256)`` pairs.  Every returned receipt is
    subjected to the same role, physical identity, path, owner, item-group, and
    provenance constraints as the singular API.  One shared live-identity
    snapshot plus a full end-of-window recheck removes the previous
    promotions-by-declarations Cartesian scan without trusting alias drift.
    """

    requests = tuple(requests)
    if not requests:
        return ()
    root = _asset_workspace_root(workspace)
    identities = {}
    resolve = _promotion_identity_resolver(root, identities)
    targets = []
    for request in requests:
        if not isinstance(request, (list, tuple)) or len(request) != 2:
            raise ValueError("legacy role correction request must be (path, sha256)")
        path, sha256 = request
        targets.append((
            path, normalize_workspace_path(path), sha256, resolve(path),
        ))
    before_index = _promotion_policy_index(old, resolve)
    after_index = _promotion_policy_index(new, resolve)
    final_resolve = _promotion_identity_resolver(root)
    for canonical, (declared, identity) in identities.items():
        if final_resolve(declared) != identity:
            raise ValueError(
                "legacy asset promotion policy path identity drifted during authorization: %s"
                % canonical
            )
    receipts = []
    for path, canonical, sha256, identity in targets:
        before = before_index.get(identity) or {
            "roles": set(), "paths": set(), "groups": set(),
            "revisions": set(), "incomplete": False, "owners": [],
        }
        after = after_index.get(identity) or {
            "roles": set(), "paths": set(), "groups": set(),
            "revisions": set(), "incomplete": False, "owners": [],
        }
        if (before["roles"] != {"answer_context"}
                or after["roles"] != {STUDENT_ATTEMPT}
                or before["paths"] != {canonical}
                or after["paths"] != {canonical}
                or before["groups"] != after["groups"]
                or len(before["groups"]) != 1):
            raise ValueError("legacy role correction conflicts with workspace ownership")
        group = next(iter(before["groups"]))
        if (not isinstance(group, tuple) or len(group) != 3
                or group[0] != "item" or not group[1] or not group[2]):
            raise ValueError("legacy role correction lacks a stable item owner")
        if (before["incomplete"] or after["incomplete"]
                or before["revisions"] != after["revisions"]
                or len(before["revisions"]) != 1):
            raise ValueError("legacy role correction has invalid provenance")
        asset_sha, asset_path, source_path, source_sha = next(
            iter(before["revisions"])
        )
        if asset_sha != sha256:
            raise ValueError(
                "legacy role correction provenance does not match staged bytes"
            )
        for item in (before, after):
            if (not item["owners"] or any(
                    kind != "homework" or source != source_path
                    for kind, source in item["owners"])):
                raise ValueError(
                    "legacy role correction owner is not the same homework source"
                )
        receipts.append({
            "path": asset_path,
            "from_roles": ["answer_context"],
            "to_roles": [STUDENT_ATTEMPT],
            "sha256": sha256,
            "source_file": source_path,
            "source_sha256": source_sha,
            "item_chapter": group[1],
            "item_id": group[2],
            "reason": "legacy_answer_context_to_student_attempt",
        })
    return tuple(receipts)


def legacy_attempt_promotion_receipt(path, sha256, old, new, workspace):
    """Compatibility wrapper for one live-evidence role correction."""

    return legacy_attempt_promotion_receipts(
        ((path, sha256),), old, new, workspace
    )[0]


__all__ = [
    "STUDENT_ATTEMPT",
    "collect_asset_roles",
    "has_tainted_official_asset",
    "is_student_attempt_tainted",
    "iter_asset_declarations",
    "legacy_attempt_promotion_receipt",
    "legacy_attempt_promotion_receipts",
    "physical_asset_key",
    "load_lightweight_quiz_bank_baseline",
    "quiz_bank_stat_baseline",
    "quiz_runtime_eligibility",
    "quiz_runtime_ref_error",
    "student_attempt_tainted_keys",
    "validate_quiz_bank_stat_baseline",
    "workspace_asset_is_student_attempt",
    "workspace_asset_identity_key",
    "ANSWER_ASSET_ROLES",
    "KNOWN_ASSET_ROLES",
    "PROMPT_ASSET_ROLES",
    "SOURCE_ITEM_ASSET_ROLES",
    "QUIZ_RUNTIME_TYPES",
    "audit_asset_policy",
    "canonical_chapter_key",
]
