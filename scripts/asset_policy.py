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
import math
import os
import re
import stat
import unicodedata
from pathlib import Path

try:
    from ingestion.identifiers import (
        is_link_or_reparse,
        normalize_workspace_path,
        safe_workspace_entry,
    )
except ImportError:  # package import from the repository root
    from scripts.ingestion.identifiers import (
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
        for rows in (quiz_rows, teaching_rows, content_units):
            for row in rows:
                for path, _role, key in declarations.get(id(row), ()):
                    identity, problem = _stable_workspace_asset_identity(
                        workspace_root, path, key
                    )
                    if problem:
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


__all__ = [
    "STUDENT_ATTEMPT",
    "collect_asset_roles",
    "has_tainted_official_asset",
    "is_student_attempt_tainted",
    "iter_asset_declarations",
    "physical_asset_key",
    "student_attempt_tainted_keys",
    "workspace_asset_is_student_attempt",
    "workspace_asset_identity_key",
    "ANSWER_ASSET_ROLES",
    "KNOWN_ASSET_ROLES",
    "PROMPT_ASSET_ROLES",
    "SOURCE_ITEM_ASSET_ROLES",
    "audit_asset_policy",
    "canonical_chapter_key",
]
