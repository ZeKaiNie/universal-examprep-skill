"""Atomic persistence, source manifests, review queues, and safe patch application."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .identifiers import (
    canonical_json,
    file_sha256,
    is_link_or_reparse,
    normalize_workspace_path,
    safe_workspace_entry,
)
from .models import (
    ChapterPhaseMapping,
    ContentUnit,
    ReviewIssue,
    ReviewPatch,
    SchemaValidationError,
    SourceRecord,
    render_answer_value,
)


class ConflictError(RuntimeError):
    """A stable ID already exists with different content."""


class SourceDriftError(RuntimeError):
    """A source or evidence file no longer matches its reviewed hash."""


class PatchApplicationError(RuntimeError):
    """A valid patch cannot safely be applied to the current ingestion state."""


@contextmanager
def _exclusive_file_lock(path):
    """Hold a crash-safe, process-wide lock without a third-party dependency."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(lock_path, "a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            raise ConflictError("another ingestion mutation is already in progress") from exc
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        stream.close()


def workspace_state_lock(workspace):
    """Serialize progress/notebook writers with atomic completion snapshots."""

    root = _workspace_root(workspace)
    return _exclusive_file_lock(safe_workspace_entry(root, ".study_state.lock"))


@contextmanager
def workspace_publication_lock(workspace):
    """Serialize coordinated state/artifact publishers in state->ingestion order."""

    root = _workspace_root(workspace)
    with workspace_state_lock(root):
        ingest = safe_workspace_entry(root, ".ingest")
        if not os.path.lexists(str(ingest)):
            yield
            return
        if not ingest.is_dir():
            raise ConflictError(".ingest must be a real workspace directory")
        with IngestionStore(root).mutation_lock():
            yield


_MISSING = object()


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SchemaValidationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _json_load(stream):
    return json.load(stream, object_pairs_hook=_no_duplicate_keys)


def _file_identity(value):
    inode = int(getattr(value, "st_ino", 0))
    if inode == 0:
        raise SchemaValidationError("filesystem does not expose a stable file identity")
    return int(getattr(value, "st_dev", 0)), inode


def _file_generation(value):
    return (
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", int(value.st_mtime * 1000000000))),
    )


def stable_read_bytes(path):
    """Capture one regular-file generation and return its exact bytes and SHA-256.

    Authoritative JSON must be parsed from these returned bytes, rather than
    parsed through one open and hashed through a later pathname open.  Reading
    the same handle twice plus checking path/handle identity and generation
    rejects in-place mutation and rename/symlink swaps during the snapshot.
    """

    source = Path(path)
    try:
        before = os.lstat(source)
        if (not stat.S_ISREG(before.st_mode) or is_link_or_reparse(source)):
            raise SchemaValidationError(
                "snapshot source must be a regular non-reparse file: %s" % source
            )
        identity = _file_identity(before)
        with open(source, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or _file_identity(opened) != identity
                    or _file_generation(opened) != _file_generation(before)):
                raise SchemaValidationError(
                    "snapshot source changed between path check and open: %s" % source
                )
            generation = _file_generation(opened)
            payload = stream.read()
            stream.seek(0)
            confirmation = stream.read()
            after_handle = os.fstat(stream.fileno())
        after_path = os.lstat(source)
    except SchemaValidationError:
        raise
    except OSError as exc:
        raise SchemaValidationError("cannot capture stable file snapshot %s: %s" % (source, exc)) from exc
    if (payload != confirmation or len(payload) != generation[0]
            or not stat.S_ISREG(after_path.st_mode)
            or is_link_or_reparse(source)
            or _file_identity(after_handle) != identity
            or _file_identity(after_path) != identity
            or _file_generation(after_handle) != generation
            or _file_generation(after_path) != generation):
        raise SchemaValidationError("snapshot source changed while it was read: %s" % source)
    return payload, {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "identity": identity,
        "generation": generation,
    }


def stable_file_sha256(path):
    """Hash one stable regular-file handle twice without buffering the file."""

    source = Path(path)
    try:
        before = os.lstat(source)
        if not stat.S_ISREG(before.st_mode) or is_link_or_reparse(source):
            raise SchemaValidationError(
                "digest source must be a regular non-reparse file: %s" % source
            )
        identity = _file_identity(before)
        with open(source, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (_file_identity(opened) != identity
                    or _file_generation(opened) != _file_generation(before)):
                raise SchemaValidationError(
                    "digest source changed between path check and open: %s" % source
                )
            generation = _file_generation(opened)
            digests = []
            for _unused in range(2):
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                digests.append(digest.hexdigest())
                stream.seek(0)
            after_handle = os.fstat(stream.fileno())
        after_path = os.lstat(source)
    except SchemaValidationError:
        raise
    except OSError as exc:
        raise SchemaValidationError("cannot capture stable file digest %s: %s" % (source, exc)) from exc
    if (digests[0] != digests[1]
            or not stat.S_ISREG(after_path.st_mode)
            or is_link_or_reparse(source)
            or _file_identity(after_handle) != identity
            or _file_identity(after_path) != identity
            or _file_generation(after_handle) != generation
            or _file_generation(after_path) != generation):
        raise SchemaValidationError("digest source changed while it was read: %s" % source)
    return digests[0], generation[0]


def stable_read_json(path):
    payload, snapshot = stable_read_bytes(path)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, SchemaValidationError) as exc:
        raise SchemaValidationError("invalid stable JSON snapshot in %s: %s" % (path, exc)) from exc
    return value, snapshot


def stable_read_jsonl(path):
    payload, snapshot = stable_read_bytes(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaValidationError("invalid UTF-8 JSONL snapshot in %s: %s" % (path, exc)) from exc
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line, object_pairs_hook=_no_duplicate_keys))
        except (json.JSONDecodeError, SchemaValidationError) as exc:
            raise SchemaValidationError(
                "invalid JSONL in stable snapshot %s line %d: %s" % (path, line_number, exc)
            ) from exc
    return rows, snapshot


def _atomic_write_text(path, text, before_publish=None):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if before_publish is not None:
            before_publish()
        os.replace(temporary, str(destination))
        # Best-effort directory sync on platforms that permit opening directories.
        if os.name != "nt":
            try:
                directory_fd = os.open(str(destination.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_bytes(path, payload):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(destination))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path, text):
    """Public UTF-8/LF atomic text writer used by deterministic compilers."""

    _atomic_write_text(path, text)


def atomic_write_json(path, value, before_publish=None):
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        before_publish=before_publish,
    )


def atomic_write_jsonl(path, rows):
    lines = [canonical_json(row) for row in rows]
    _atomic_write_text(path, "".join(line + "\n" for line in lines))


def read_json(path, default=_MISSING):
    source = Path(path)
    if not source.exists():
        if default is not _MISSING:
            return default
        raise FileNotFoundError(str(source))
    if source.is_symlink() or not source.is_file():
        raise SchemaValidationError("JSON source must be a regular non-symlink file: %s" % source)
    with open(source, "r", encoding="utf-8") as stream:
        try:
            return _json_load(stream)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError("invalid JSON in %s: %s" % (source, exc)) from exc


def read_jsonl(path, default=_MISSING):
    source = Path(path)
    if not source.exists():
        if default is not _MISSING:
            return default
        raise FileNotFoundError(str(source))
    if source.is_symlink() or not source.is_file():
        raise SchemaValidationError("JSONL source must be a regular non-symlink file: %s" % source)
    rows = []
    with open(source, "r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line, object_pairs_hook=_no_duplicate_keys))
            except (json.JSONDecodeError, SchemaValidationError) as exc:
                raise SchemaValidationError(
                    "invalid JSONL in %s line %d: %s" % (source, line_number, exc)
                ) from exc
    return rows


def _workspace_root(workspace):
    lexical = Path(os.path.abspath(str(workspace)))
    if os.path.lexists(str(lexical)) and is_link_or_reparse(lexical):
        raise ValueError("workspace/source root must not be a symlink or junction: %s" % lexical)
    root = lexical.resolve()
    if not root.is_dir():
        raise ValueError("workspace must already exist and be a directory: %s" % root)
    return root


class SourceManifest:
    """A small atomic manifest keyed by path-stable source IDs."""

    DEFAULT_PATH = ".ingest/source_manifest.json"

    def __init__(self, workspace, relative_path=DEFAULT_PATH, source_root=None):
        self.workspace = _workspace_root(workspace)
        self.source_root = _workspace_root(source_root or workspace)
        self.path = safe_workspace_entry(self.workspace, relative_path)

    def records(self):
        payload = read_json(self.path, default={"schema_version": 1, "sources": []})
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "sources"}:
            raise SchemaValidationError("source manifest has an invalid top-level schema")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise SchemaValidationError("source manifest schema_version must be 1")
        if not isinstance(payload["sources"], list):
            raise SchemaValidationError("source manifest sources must be a list")
        result = []
        seen_ids = set()
        seen_paths = set()
        for raw in payload["sources"]:
            record = SourceRecord.from_dict(raw)
            if record.source_id in seen_ids or record.path in seen_paths:
                raise SchemaValidationError("source manifest contains duplicate source identity")
            seen_ids.add(record.source_id)
            seen_paths.add(record.path)
            result.append(record)
        return tuple(sorted(result, key=lambda item: item.path))

    def _write(self, records):
        ordered = sorted(records, key=lambda item: item.path)
        atomic_write_json(
            self.path,
            {"schema_version": 1, "sources": [record.to_dict() for record in ordered]},
        )

    def replace_all(self, records):
        """Atomically replace the discovered-source snapshot after strict validation."""

        normalized = []
        seen_ids = set()
        seen_paths = set()
        for value in records:
            record = value if isinstance(value, SourceRecord) else SourceRecord.from_dict(value)
            if record.source_id in seen_ids or record.path in seen_paths:
                raise ConflictError("replacement source manifest contains duplicate identity")
            seen_ids.add(record.source_id)
            seen_paths.add(record.path)
            normalized.append(record)
        before = [record.to_dict() for record in self.records()]
        after = [record.to_dict() for record in sorted(normalized, key=lambda item: item.path)]
        if before == after:
            return False
        self._write(normalized)
        return True

    def get(self, source_id):
        for record in self.records():
            if record.source_id == source_id:
                return record
        return None

    def upsert(self, record):
        if not isinstance(record, SourceRecord):
            record = SourceRecord.from_dict(record)
        records = list(self.records())
        for index, current in enumerate(records):
            if current.source_id == record.source_id:
                if current.to_dict() == record.to_dict():
                    return False
                if current.path != record.path:
                    raise ConflictError("source ID collision across paths")
                records[index] = record
                self._write(records)
                return True
            if current.path == record.path:
                raise ConflictError("source path has a different stable ID")
        records.append(record)
        self._write(records)
        return True

    def verify_current(self, source_id, expected_sha256):
        record = self.get(source_id)
        if record is None:
            raise SourceDriftError("source is absent from manifest: %s" % source_id)
        if record.sha256 != expected_sha256:
            raise SourceDriftError("source manifest hash changed for %s" % record.path)
        absolute = safe_workspace_entry(self.source_root, record.path)
        if not absolute.is_file() or absolute.is_symlink():
            raise SourceDriftError("source is missing or no longer a regular file: %s" % record.path)
        actual_sha = file_sha256(absolute)
        actual_size = absolute.stat().st_size
        if actual_sha != record.sha256 or actual_size != record.size_bytes:
            raise SourceDriftError("source bytes drifted after review: %s" % record.path)
        return record


class ReviewQueue:
    """An atomically rewritten JSONL queue with deterministic, idempotent append."""

    DEFAULT_PATH = ".ingest/review_queue.jsonl"

    _TRANSITIONS = {
        "pending": frozenset(("claimed", "validated", "blocked", "resolved", "superseded", "unrecoverable")),
        "claimed": frozenset(("pending", "validated", "blocked", "resolved", "superseded", "unrecoverable")),
        "validated": frozenset(("applied", "blocked", "resolved", "superseded", "unrecoverable")),
        "blocked": frozenset(("pending", "claimed", "superseded", "unrecoverable")),
        "resolved": frozenset(),
        "applied": frozenset(),
        "unrecoverable": frozenset(),
        "superseded": frozenset(),
    }

    def __init__(self, workspace, relative_path=DEFAULT_PATH):
        self.workspace = _workspace_root(workspace)
        self.path = safe_workspace_entry(self.workspace, relative_path)

    def issues(self):
        result = []
        seen = set()
        for raw in read_jsonl(self.path, default=[]):
            issue = ReviewIssue.from_dict(raw)
            if issue.issue_id in seen:
                raise SchemaValidationError("review queue contains duplicate issue_id %s" % issue.issue_id)
            seen.add(issue.issue_id)
            result.append(issue)
        return tuple(sorted(result, key=lambda item: item.issue_id))

    def _write(self, issues):
        atomic_write_jsonl(self.path, [issue.to_dict() for issue in sorted(issues, key=lambda x: x.issue_id)])

    def get(self, issue_id):
        for issue in self.issues():
            if issue.issue_id == issue_id:
                return issue
        return None

    def append(self, issue):
        if not isinstance(issue, ReviewIssue):
            issue = ReviewIssue.from_dict(issue)
        issues = list(self.issues())
        for current in issues:
            if current.issue_id == issue.issue_id:
                if current.to_dict() == issue.to_dict():
                    return False
                raise ConflictError("issue ID already exists with different content")
        issues.append(issue)
        self._write(issues)
        return True

    def replace(self, issue):
        if not isinstance(issue, ReviewIssue):
            issue = ReviewIssue.from_dict(issue)
        issues = list(self.issues())
        for index, current in enumerate(issues):
            if current.issue_id == issue.issue_id:
                if current.to_dict() == issue.to_dict():
                    return False
                issues[index] = issue
                self._write(issues)
                return True
        raise KeyError(issue.issue_id)

    def set_status(self, issue_id, status, expected_status=None):
        issue = self.get(issue_id)
        if issue is None:
            raise KeyError(issue_id)
        if expected_status is not None and issue.status != expected_status:
            raise ConflictError(
                "issue status changed: expected %s, found %s" % (expected_status, issue.status)
            )
        if status == issue.status:
            return issue
        if status not in self._TRANSITIONS.get(issue.status, frozenset()):
            raise ConflictError("invalid issue transition %s -> %s" % (issue.status, status))
        updated = issue.with_status(status)
        self.replace(updated)
        return updated

    def reconcile(self, current_issues):
        """Merge a fresh detector snapshot without discarding review lifecycle state.

        Stable issues retain their current status while descriptions, severity, and
        suggested actions may improve.  Detector issues that disappeared are retained
        as ``superseded`` (terminal issues remain terminal), preserving the audit trail.
        """

        incoming = {}
        for value in current_issues:
            issue = value if isinstance(value, ReviewIssue) else ReviewIssue.from_dict(value)
            if issue.issue_id in incoming:
                raise ConflictError("replacement review snapshot contains duplicate issue_id")
            incoming[issue.issue_id] = issue

        existing = {issue.issue_id: issue for issue in self.issues()}
        merged = []
        for issue_id in sorted(set(existing) | set(incoming)):
            old = existing.get(issue_id)
            new = incoming.get(issue_id)
            if old is not None and new is not None:
                merged.append(
                    ReviewIssue(
                        new.schema_version,
                        new.issue_id,
                        new.source_id,
                        new.source_sha256,
                        new.reason_codes,
                        new.pages,
                        new.evidence,
                        new.target_unit_ids,
                        new.severity,
                        new.description,
                        new.suggested_action,
                        old.status,
                    )
                )
            elif new is not None:
                merged.append(new)
            elif old.status in ("applied", "resolved", "unrecoverable", "superseded"):
                merged.append(old)
            else:
                merged.append(old.with_status("superseded"))

        before = [issue.to_dict() for issue in self.issues()]
        after = [issue.to_dict() for issue in sorted(merged, key=lambda item: item.issue_id)]
        if before == after:
            if not self.path.exists():
                self._write(merged)
            return False
        self._write(merged)
        return True


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    replayed: bool
    changed_operations: int
    issue_status: str

    def __bool__(self):
        return self.applied


class IngestionStore:
    """Workspace-local units, mappings, review queue, and an applied-patch ledger."""

    UNITS_PATH = ".ingest/content_units.jsonl"
    MAPPINGS_PATH = ".ingest/chapter_phase_mappings.jsonl"
    BASE_UNITS_PATH = ".ingest/base_content_units.jsonl"
    BASE_MAPPINGS_PATH = ".ingest/base_chapter_phase_mappings.jsonl"
    LEDGER_PATH = ".ingest/review_patches.jsonl"
    PENDING_PATCH_PATH = ".ingest/pending_patch.json"
    PENDING_INGEST_PATH = ".ingest/pending_ingest.json"
    TRANSACTIONS_PATH = ".ingest/transactions"
    LOCK_PATH = ".ingest/mutation.lock"

    def __init__(self, workspace, source_root=None):
        self.workspace = _workspace_root(workspace)
        self.source_root = _workspace_root(source_root or workspace)
        self.manifest = SourceManifest(self.workspace, source_root=self.source_root)
        self.review_queue = ReviewQueue(self.workspace)
        self.units_path = safe_workspace_entry(self.workspace, self.UNITS_PATH)
        self.mappings_path = safe_workspace_entry(self.workspace, self.MAPPINGS_PATH)
        self.base_units_path = safe_workspace_entry(self.workspace, self.BASE_UNITS_PATH)
        self.base_mappings_path = safe_workspace_entry(self.workspace, self.BASE_MAPPINGS_PATH)
        self.ledger_path = safe_workspace_entry(self.workspace, self.LEDGER_PATH)
        self.pending_patch_path = safe_workspace_entry(self.workspace, self.PENDING_PATCH_PATH)
        self.pending_ingest_path = safe_workspace_entry(self.workspace, self.PENDING_INGEST_PATH)
        self.transactions_path = safe_workspace_entry(self.workspace, self.TRANSACTIONS_PATH)
        self.lock_path = safe_workspace_entry(self.workspace, self.LOCK_PATH)

    def _recover_interrupted_ingest(self):
        """Roll back a crash-interrupted multi-file ingestion commit."""

        pending = read_json(self.pending_ingest_path, default=None)
        if pending is None:
            return False
        if (not isinstance(pending, dict)
                or set(pending) != {"schema_version", "transaction_dir", "targets"}
                or pending.get("schema_version") != 1
                or not isinstance(pending.get("transaction_dir"), str)
                or not isinstance(pending.get("targets"), list)):
            raise SchemaValidationError("pending ingest transaction has an invalid schema")

        transaction_dir = safe_workspace_entry(
            self.workspace, pending["transaction_dir"]
        )
        for row in pending["targets"]:
            if (not isinstance(row, dict)
                    or set(row) != {"path", "backup"}
                    or not isinstance(row.get("path"), str)
                    or (row.get("backup") is not None
                        and not isinstance(row.get("backup"), str))):
                raise SchemaValidationError("pending ingest target has an invalid schema")
            destination = safe_workspace_entry(self.workspace, row["path"])
            backup_rel = row["backup"]
            if backup_rel is None:
                if os.path.lexists(str(destination)):
                    if is_link_or_reparse(destination) or not destination.is_file():
                        raise ConflictError(
                            "cannot roll back unsafe ingest target: %s" % row["path"]
                        )
                    destination.unlink()
                continue
            backup = safe_workspace_entry(self.workspace, backup_rel)
            if (not backup.is_file() or is_link_or_reparse(backup)
                    or transaction_dir not in backup.parents):
                raise ConflictError(
                    "ingest rollback backup is missing or unsafe: %s" % backup_rel
                )
            _atomic_write_bytes(destination, backup.read_bytes())

        self.pending_ingest_path.unlink()
        shutil.rmtree(transaction_dir, ignore_errors=True)
        return True

    def mutation_lock(self):
        @contextmanager
        def locked():
            with _exclusive_file_lock(self.lock_path):
                self._recover_interrupted_ingest()
                yield
        return locked()

    @contextmanager
    def ingest_transaction(self, relative_paths):
        """Make a bounded set of workspace files commit or roll back together.

        The caller must already hold ``mutation_lock``.  Backups and a durable
        intent are written before the first target mutation; the next mutation
        automatically rolls back an interrupted transaction after a process crash.
        """

        if self.pending_ingest_path.exists():
            raise ConflictError("an interrupted ingest transaction requires recovery")
        canonical_paths = []
        for value in relative_paths:
            canonical = normalize_workspace_path(value)
            if canonical in canonical_paths:
                continue
            canonical_paths.append(canonical)
        canonical_paths.sort()

        self.transactions_path.mkdir(parents=True, exist_ok=True)
        transaction_dir = Path(tempfile.mkdtemp(
            prefix="ingest-", dir=str(self.transactions_path)
        ))
        transaction_rel = str(transaction_dir.relative_to(self.workspace)).replace(os.sep, "/")
        targets = []
        try:
            for index, relative in enumerate(canonical_paths):
                destination = safe_workspace_entry(self.workspace, relative)
                backup_rel = None
                if os.path.lexists(str(destination)):
                    if is_link_or_reparse(destination) or not destination.is_file():
                        raise ConflictError("ingest target is not a safe regular file: %s" % relative)
                    backup = transaction_dir / ("%06d.bak" % index)
                    with open(destination, "rb") as source, open(backup, "wb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    backup_rel = str(backup.relative_to(self.workspace)).replace(os.sep, "/")
                targets.append({"path": relative, "backup": backup_rel})
            atomic_write_json(self.pending_ingest_path, {
                "schema_version": 1,
                "transaction_dir": transaction_rel,
                "targets": targets,
            })
            try:
                yield
            except BaseException:
                self._recover_interrupted_ingest()
                raise
            self.pending_ingest_path.unlink()
            shutil.rmtree(transaction_dir, ignore_errors=True)
        except BaseException:
            if self.pending_ingest_path.exists():
                self._recover_interrupted_ingest()
            else:
                shutil.rmtree(transaction_dir, ignore_errors=True)
            raise

    def units(self):
        result = {}
        for raw in read_jsonl(self.units_path, default=[]):
            unit = ContentUnit.from_dict(raw)
            current = result.get(unit.unit_id)
            if current is not None:
                raise SchemaValidationError("duplicate unit_id in content store: %s" % unit.unit_id)
            result[unit.unit_id] = unit
        return result

    def mappings(self):
        result = {}
        for raw in read_jsonl(self.mappings_path, default=[]):
            mapping = ChapterPhaseMapping.from_dict(raw)
            if mapping.unit_id in result:
                raise SchemaValidationError("duplicate mapping for unit_id %s" % mapping.unit_id)
            result[mapping.unit_id] = mapping
        return result

    def base_units(self):
        result = {}
        for raw in read_jsonl(self.base_units_path, default=[]):
            unit = ContentUnit.from_dict(raw)
            if unit.unit_id in result:
                raise SchemaValidationError("duplicate unit_id in base content store: %s" % unit.unit_id)
            result[unit.unit_id] = unit
        return result

    def base_mappings(self):
        result = {}
        for raw in read_jsonl(self.base_mappings_path, default=[]):
            mapping = ChapterPhaseMapping.from_dict(raw)
            if mapping.unit_id in result:
                raise SchemaValidationError("duplicate mapping in base mapping store: %s" % mapping.unit_id)
            result[mapping.unit_id] = mapping
        return result

    def _write_units(self, units):
        atomic_write_jsonl(
            self.units_path,
            [units[key].to_dict() for key in sorted(units)],
        )

    def _write_mappings(self, mappings):
        atomic_write_jsonl(
            self.mappings_path,
            [mappings[key].to_dict() for key in sorted(mappings)],
        )

    def _write_base_units(self, units):
        atomic_write_jsonl(
            self.base_units_path,
            [units[key].to_dict() for key in sorted(units)],
        )

    def _write_base_mappings(self, mappings):
        atomic_write_jsonl(
            self.base_mappings_path,
            [mappings[key].to_dict() for key in sorted(mappings)],
        )

    def sync_base(self, units, mappings=()):
        """Install parser truth, then deterministically replay the patch ledger."""

        base_units = {}
        for value in units:
            unit = value if isinstance(value, ContentUnit) else ContentUnit.from_dict(value)
            if unit.unit_id in base_units:
                raise ConflictError("base snapshot contains duplicate unit_id")
            record = self.manifest.get(unit.source_id)
            if record is None or record.sha256 != unit.source_sha256 or record.path != unit.source_file:
                raise SourceDriftError("base unit does not match the current source manifest")
            base_units[unit.unit_id] = unit

        base_mappings = {}
        for value in mappings:
            mapping = value if isinstance(value, ChapterPhaseMapping) else ChapterPhaseMapping.from_dict(value)
            if mapping.unit_id in base_mappings:
                raise ConflictError("base snapshot contains duplicate chapter mapping")
            unit = base_units.get(mapping.unit_id)
            if unit is None:
                raise PatchApplicationError("base mapping target is absent from base units")
            if mapping.source_id != unit.source_id or mapping.source_sha256 != unit.source_sha256:
                raise SourceDriftError("base mapping does not match its unit source revision")
            base_mappings[mapping.unit_id] = mapping

        self._write_base_units(base_units)
        self._write_base_mappings(base_mappings)
        self.rebuild_compiled_from_ledger()
        return len(base_units), len(base_mappings)

    def get_unit(self, unit_id):
        return self.units().get(unit_id)

    def append_unit(self, unit, verify_source=True):
        if not isinstance(unit, ContentUnit):
            unit = ContentUnit.from_dict(unit)
        if verify_source:
            record = self.manifest.verify_current(unit.source_id, unit.source_sha256)
            if unit.source_file != record.path:
                raise SourceDriftError("unit source_file does not match its manifest record")
        with self.mutation_lock():
            units = self.base_units()
            current = units.get(unit.unit_id)
            if current is not None:
                if current.to_dict() == unit.to_dict():
                    return False
                raise ConflictError("unit ID already exists with different content")
            units[unit.unit_id] = unit
            self._write_base_units(units)
            if not self.base_mappings_path.exists():
                self._write_base_mappings({})
            self.rebuild_compiled_from_ledger()
            return True

    def _ledger(self):
        result = {}
        for raw in read_jsonl(self.ledger_path, default=[]):
            expected = {"patch_id", "fingerprint", "issue_id", "source_id", "source_sha256", "patch"}
            if not isinstance(raw, dict) or set(raw) != expected:
                raise SchemaValidationError("patch ledger entry has an invalid schema")
            patch = ReviewPatch.from_dict(raw["patch"])
            if patch.patch_id != raw["patch_id"]:
                raise SchemaValidationError("patch ledger ID does not match embedded patch")
            if raw["fingerprint"] != self._patch_fingerprint(patch):
                raise SchemaValidationError("patch ledger fingerprint does not match embedded patch")
            if raw["patch_id"] in result:
                raise SchemaValidationError("patch ledger contains duplicate patch_id")
            result[raw["patch_id"]] = raw
        return result

    @staticmethod
    def _patch_fingerprint(patch):
        logical = {
            "issue_id": patch.issue_id,
            "source_id": patch.source_id,
            "source_sha256": patch.source_sha256,
            "operations": list(patch.operations),
            "evidence": [item.to_dict() for item in patch.evidence],
        }
        return hashlib.sha256(canonical_json(logical).encode("utf-8")).hexdigest()

    def ledger_entries(self):
        return list(self._ledger().values())

    def _verify_evidence(self, issue, patch):
        issue_evidence = {(item.path, item.sha256) for item in issue.evidence}
        for evidence in patch.evidence:
            identity = (evidence.path, evidence.sha256)
            if identity not in issue_evidence:
                raise PatchApplicationError("patch evidence was not declared by its review issue")
            absolute = safe_workspace_entry(self.workspace, evidence.path)
            if not absolute.is_file() or absolute.is_symlink():
                raise SourceDriftError("review evidence is missing: %s" % evidence.path)
            if file_sha256(absolute) != evidence.sha256:
                raise SourceDriftError("review evidence drifted: %s" % evidence.path)

    @staticmethod
    def _operation_targets(operation):
        name = operation["op"]
        if name in ("replace_unit", "assign_chapter", "classify_asset"):
            return {operation["unit_id"]}
        if name == "pair_qa":
            return {operation["question_unit_id"], operation["answer_unit_id"]}
        return set()

    def _validate_patch_context(self, patch, issue, allow_terminal=False, units=None):
        record = self.manifest.verify_current(patch.source_id, patch.source_sha256)
        if issue is None:
            raise PatchApplicationError("patch issue is absent from the review queue")
        if issue.source_id != patch.source_id or issue.source_sha256 != patch.source_sha256:
            raise SourceDriftError("patch source identity/hash does not match its issue")
        allowed = ("pending", "claimed", "validated")
        if allow_terminal:
            allowed = allowed + ("applied", "resolved", "unrecoverable")
        if issue.status not in allowed:
            raise PatchApplicationError("issue status does not permit patch application: %s" % issue.status)
        self._verify_evidence(issue, patch)

        declared_targets = set(issue.target_unit_ids)
        actual_targets = set()
        added_targets = set()
        for operation in patch.operations:
            actual_targets.update(self._operation_targets(operation))
            if operation["op"] == "add_unit":
                proposed = ContentUnit.from_dict(operation["unit"])
                added_targets.add(proposed.unit_id)
                if issue.pages and proposed.page not in issue.pages:
                    raise PatchApplicationError(
                        "add_unit page is outside the review issue evidence pages"
                    )
        outside = actual_targets - added_targets - declared_targets
        if not declared_targets:
            disallowed = [
                operation["op"] for operation in patch.operations
                if operation["op"] not in ("add_unit", "mark_resolved", "mark_unrecoverable")
            ]
            if disallowed:
                raise PatchApplicationError(
                    "an unbound review issue cannot mutate existing units: %s"
                    % ", ".join(sorted(set(disallowed)))
                )
        if declared_targets and outside:
            # A missing-answer issue is normally bound to the question source.  It
            # may pair to an already-ingested answer-book unit when both carry the
            # same external question ID; that second source revision is still
            # verified by _apply_operations_to_state.
            units = self.units() if units is None else units
            proposed_by_id = {}
            for operation in patch.operations:
                if operation["op"] in ("add_unit", "replace_unit"):
                    proposed = ContentUnit.from_dict(operation["unit"])
                    proposed_by_id[proposed.unit_id] = proposed
            allowed_cross_source_answers = set()
            for operation in patch.operations:
                if operation["op"] != "pair_qa":
                    continue
                question = proposed_by_id.get(
                    operation["question_unit_id"],
                    units.get(operation["question_unit_id"]),
                )
                answer = proposed_by_id.get(
                    operation["answer_unit_id"],
                    units.get(operation["answer_unit_id"]),
                )
                same_external_id = (
                    question is not None and answer is not None
                    and question.kind == "question" and answer.kind == "answer"
                    and question.external_id
                    and answer.external_id == question.external_id
                )
                if same_external_id and question.unit_id in declared_targets:
                    allowed_cross_source_answers.add(answer.unit_id)
                if same_external_id and answer.unit_id in declared_targets:
                    allowed_cross_source_answers.add(question.unit_id)
            outside -= allowed_cross_source_answers
        if declared_targets and outside:
            raise PatchApplicationError("patch mutates a unit outside issue.target_unit_ids")
        return record

    @staticmethod
    def _answer_has_value(answer):
        if answer is None or answer.kind != "answer":
            return False
        if "answer_value" in answer.metadata:
            return answer.metadata["answer_value"] not in (None, "", [], {})
        return bool(answer.text.strip())

    def _validate_issue_postcondition(
        self, issue, patch, units, mappings, changed, final_status,
        require_change=True,
    ):
        if final_status == "unrecoverable":
            return
        if final_status == "resolved":
            if issue.severity == "blocking":
                raise PatchApplicationError(
                    "blocking evidence-loss issues cannot be mark_resolved; recover content or "
                    "mark_unrecoverable"
                )
            return
        if require_change and changed <= 0:
            raise PatchApplicationError(
                "patch made no material change and cannot close a review issue"
            )
        reasons = set(issue.reason_codes)
        targets = [units.get(unit_id) for unit_id in issue.target_unit_ids]
        if "missing_answer" in reasons:
            questions = [unit for unit in targets if unit is not None and unit.kind == "question"]
            if not questions:
                raise PatchApplicationError("missing_answer issue has no question target")
            unresolved = [
                question.unit_id for question in questions
                if not self._answer_has_value(units.get(question.paired_unit_id))
            ]
            if unresolved:
                raise PatchApplicationError(
                    "missing_answer postcondition failed for %s" % ", ".join(unresolved)
                )
        if "speaker_note_answer_candidate" in reasons:
            answers = [unit for unit in targets if unit is not None and unit.kind == "answer"]
            if answers and any(
                not (answer.paired_unit_id and units.get(answer.paired_unit_id)
                     and units[answer.paired_unit_id].kind == "question")
                for answer in answers
            ):
                raise PatchApplicationError(
                    "speaker-note answer must be paired to a question or marked unrecoverable"
                )

    def _validate_unit_metadata_context(self, units):
        anchors = {
            (unit.source_id, unit.page)
            for unit in units.values() if unit.kind == "page_anchor"
        }
        current_source_hashes = {
            record.sha256 for record in self.manifest.records()
        }
        for unit in units.values():
            metadata = unit.metadata
            if not metadata:
                continue
            if (unit.provenance == "ai_supplemented"
                    and metadata.get("source") not in (None, "ai_generated")):
                raise PatchApplicationError(
                    "AI-supplemented unit metadata.source must be ai_generated"
                )
            for page in metadata.get("source_pages") or ():
                if (unit.source_id, page) not in anchors:
                    raise PatchApplicationError(
                        "metadata.source_pages lacks a same-source page_anchor"
                    )
            if unit.kind == "answer":
                answer_file = metadata.get("answer_source_file")
                if answer_file is not None and answer_file != unit.source_file:
                    raise PatchApplicationError(
                        "answer metadata.answer_source_file must match the answer unit source"
                    )
                for page in metadata.get("answer_source_pages") or ():
                    if (unit.source_id, page) not in anchors:
                        raise PatchApplicationError(
                            "metadata.answer_source_pages lacks a same-source page_anchor"
                        )
                if "answer_value" in metadata:
                    value = metadata["answer_value"]
                    if unit.text != render_answer_value(value):
                        raise PatchApplicationError(
                            "answer metadata.answer_value disagrees with answer text"
                        )
            for asset in metadata.get("assets") or ():
                path = asset["path"]
                if not path.startswith("references/assets/"):
                    raise PatchApplicationError(
                        "quiz metadata assets must live under references/assets"
                    )
                absolute = safe_workspace_entry(self.workspace, path)
                if not absolute.is_file():
                    raise PatchApplicationError("quiz metadata asset is missing: %s" % path)
                expected_hash = asset.get("sha256")
                if expected_hash is not None and file_sha256(absolute) != expected_hash:
                    raise SourceDriftError("quiz metadata asset hash drifted: %s" % path)
                source_hash = asset.get("source_sha256")
                if source_hash is not None and source_hash not in current_source_hashes:
                    raise SourceDriftError(
                        "quiz metadata asset source hash is not in the current manifest"
                    )

    def _apply_operations_to_state(self, patch, units, mappings, record):
        changed = 0
        final_status = "applied"
        for operation in patch.operations:
            name = operation["op"]
            changed_this_operation = False
            proposed = None
            if name in ("add_unit", "replace_unit"):
                proposed = ContentUnit.from_dict(operation["unit"])
                if (proposed.source_id != patch.source_id
                        or proposed.source_sha256 != patch.source_sha256
                        or proposed.source_file != record.path):
                    raise SourceDriftError(
                        "patched unit does not match the manifest source identity/hash/path"
                    )

            if name == "add_unit":
                current = units.get(proposed.unit_id)
                if current is None:
                    units[proposed.unit_id] = proposed
                    changed_this_operation = True
                elif current.to_dict() != proposed.to_dict():
                    raise ConflictError("add_unit collides with an existing unit")

            elif name == "replace_unit":
                current = units.get(operation["unit_id"])
                if current is None:
                    raise PatchApplicationError("replace_unit target does not exist")
                if current.source_id != patch.source_id or current.source_sha256 != patch.source_sha256:
                    raise SourceDriftError("replace_unit target belongs to a different source revision")
                if proposed.unit_id != current.unit_id:
                    raise PatchApplicationError(
                        "replace_unit cannot change the stable locator identity"
                    )
                if current.to_dict() != proposed.to_dict():
                    units[current.unit_id] = proposed
                    changed_this_operation = True

            elif name == "assign_chapter":
                unit = units.get(operation["unit_id"])
                if unit is None:
                    raise PatchApplicationError("assign_chapter target does not exist")
                if unit.source_id != patch.source_id or unit.source_sha256 != patch.source_sha256:
                    raise SourceDriftError("assign_chapter target belongs to a different source revision")
                assigned = unit.with_chapter(operation["chapter_id"], operation["phase_id"])
                mapping = ChapterPhaseMapping.create(
                    unit.unit_id,
                    patch.source_id,
                    patch.source_sha256,
                    operation["chapter"],
                    operation["phase"],
                    operation["chapter_id"],
                    operation["phase_id"],
                )
                current_mapping = mappings.get(unit.unit_id)
                if unit.to_dict() != assigned.to_dict():
                    units[unit.unit_id] = assigned
                    changed_this_operation = True
                if current_mapping is None or current_mapping.to_dict() != mapping.to_dict():
                    mappings[unit.unit_id] = mapping
                    changed_this_operation = True

            elif name == "pair_qa":
                question = units.get(operation["question_unit_id"])
                answer = units.get(operation["answer_unit_id"])
                if question is None or answer is None:
                    raise PatchApplicationError("pair_qa target does not exist")
                if question.kind != "question" or answer.kind != "answer":
                    raise PatchApplicationError("pair_qa requires question and answer unit kinds")
                if (not question.external_id or not answer.external_id
                        or question.external_id != answer.external_id):
                    raise PatchApplicationError(
                        "pair_qa requires the same non-empty external_id on both units"
                    )
                for unit in (question, answer):
                    unit_record = self.manifest.verify_current(
                        unit.source_id, unit.source_sha256
                    )
                    if unit.source_file != unit_record.path:
                        raise SourceDriftError(
                            "pair_qa target source_file disagrees with the source manifest"
                        )
                if question.paired_unit_id not in (None, answer.unit_id):
                    raise ConflictError("question is already paired to a different answer")
                if answer.paired_unit_id not in (None, question.unit_id):
                    raise ConflictError("answer is already paired to a different question")
                paired_question = question.with_pair(answer.unit_id)
                paired_answer = answer.with_pair(question.unit_id)
                if question.to_dict() != paired_question.to_dict():
                    units[question.unit_id] = paired_question
                    changed_this_operation = True
                if answer.to_dict() != paired_answer.to_dict():
                    units[answer.unit_id] = paired_answer
                    changed_this_operation = True

            elif name == "classify_asset":
                unit = units.get(operation["unit_id"])
                if unit is None:
                    raise PatchApplicationError("classify_asset target does not exist")
                if unit.source_id != patch.source_id or unit.source_sha256 != patch.source_sha256:
                    raise SourceDriftError("classify_asset target belongs to a different source revision")
                if unit.asset_path is None:
                    raise PatchApplicationError("classify_asset target has no asset_path")
                classified = unit.with_asset_role(operation["asset_role"])
                if unit.to_dict() != classified.to_dict():
                    units[unit.unit_id] = classified
                    changed_this_operation = True

            elif name == "mark_resolved":
                final_status = "resolved"

            elif name == "mark_unrecoverable":
                final_status = "unrecoverable"

            if changed_this_operation:
                changed += 1
        self._validate_unit_metadata_context(units)
        self._validate_question_identities(units)
        return changed, final_status

    @staticmethod
    def _validate_question_identities(units):
        question_external_ids = {}
        for unit in units.values():
            if unit.kind != "question" or not unit.external_id:
                continue
            previous = question_external_ids.get(unit.external_id)
            if previous is not None and previous != unit.unit_id:
                raise ConflictError(
                    "question external_id is not unique: %s" % unit.external_id
                )
            question_external_ids[unit.external_id] = unit.unit_id

    def _expected_compiled_state(self):
        units = dict(self.base_units())
        mappings = dict(self.base_mappings())
        for mapping in mappings.values():
            unit = units.get(mapping.unit_id)
            if unit is None:
                raise PatchApplicationError("base mapping target is absent from base units")
            if (mapping.source_id != unit.source_id
                    or mapping.source_sha256 != unit.source_sha256
                    or mapping.chapter_id != unit.chapter_id
                    or mapping.phase_id != unit.phase_id):
                raise SourceDriftError("base mapping disagrees with its content unit")

        for entry in self._ledger().values():
            patch = ReviewPatch.from_dict(entry["patch"])
            record = self.manifest.get(patch.source_id)
            # A source revision change keeps the append-only historical entry but
            # never replays it onto the new bytes.
            if record is None or record.sha256 != patch.source_sha256:
                continue
            issue = self.review_queue.get(patch.issue_id)
            record = self._validate_patch_context(
                patch, issue, allow_terminal=True, units=units
            )
            _changed, expected_status = self._apply_operations_to_state(
                patch, units, mappings, record
            )
            self._validate_issue_postcondition(
                issue, patch, units, mappings, _changed, expected_status,
                require_change=False,
            )
            if issue.status != expected_status:
                raise PatchApplicationError(
                    "ledger patch status disagrees with review issue: %s" % patch.issue_id
                )
        self._validate_unit_metadata_context(units)
        self._validate_question_identities(units)
        return units, mappings

    def rebuild_compiled_from_ledger(self):
        units, mappings = self._expected_compiled_state()
        self._write_units(units)
        self._write_mappings(mappings)
        return units, mappings

    def verify_compiled_matches_ledger(self):
        expected_units, expected_mappings = self._expected_compiled_state()
        if ({key: value.to_dict() for key, value in self.units().items()}
                != {key: value.to_dict() for key, value in expected_units.items()}):
            raise PatchApplicationError(
                "compiled content_units do not match base + review ledger; run rebuild"
            )
        if ({key: value.to_dict() for key, value in self.mappings().items()}
                != {key: value.to_dict() for key, value in expected_mappings.items()}):
            raise PatchApplicationError(
                "compiled chapter mappings do not match base + review ledger; run rebuild"
            )
        return True

    def ledger_touched_unit_ids(self):
        touched = set()
        for entry in self._ledger().values():
            patch = ReviewPatch.from_dict(entry["patch"])
            record = self.manifest.get(patch.source_id)
            if record is None or record.sha256 != patch.source_sha256:
                continue
            for operation in patch.operations:
                if operation["op"] in ("add_unit", "replace_unit"):
                    touched.add(operation["unit"].get("unit_id"))
                else:
                    touched.update(self._operation_targets(operation))
        return {unit_id for unit_id in touched if unit_id}

    def claim_issue(self, issue_id):
        with self.mutation_lock():
            return self.review_queue.set_status(
                issue_id, "claimed", expected_status="pending"
            )

    def refresh_source_statuses(self):
        """Derive source review status from the typed queue without changing identity."""

        open_statuses = frozenset(("pending", "claimed", "validated", "blocked"))
        open_source_ids = {
            issue.source_id for issue in self.review_queue.issues()
            if issue.status in open_statuses
        }
        records = []
        changed = False
        for record in self.manifest.records():
            if record.status in ("failed", "unsupported", "unrecoverable", "superseded"):
                records.append(record)
                continue
            status = "review_required" if record.source_id in open_source_ids else "complete"
            current = SourceRecord.create(
                record.path, record.sha256, record.size_bytes, record.media_type, status=status
            )
            records.append(current)
            changed = changed or current.to_dict() != record.to_dict()
        if changed:
            self.manifest.replace_all(records)
        return changed

    def validate_patch(self, patch):
        """Validate schema, live source/evidence, scope, and semantic outcome without writing."""

        if not isinstance(patch, ReviewPatch):
            patch = ReviewPatch.from_dict(patch)
        if patch.status != "validated":
            raise PatchApplicationError("only a validated patch may pass contextual validation")
        with self.mutation_lock():
            units, mappings = self._expected_compiled_state()
            issue = self.review_queue.get(patch.issue_id)
            record = self._validate_patch_context(
                patch, issue, allow_terminal=False, units=units
            )
            units = dict(units)
            mappings = dict(mappings)
            changed, final_status = self._apply_operations_to_state(
                patch, units, mappings, record
            )
            self._validate_issue_postcondition(
                issue, patch, units, mappings, changed, final_status,
                require_change=True,
            )
        return patch

    def apply_patch(self, patch):
        """Apply a validated patch under a process lock and write-ahead intent."""

        if not isinstance(patch, ReviewPatch):
            patch = ReviewPatch.from_dict(patch)
        if patch.status != "validated":
            raise PatchApplicationError("only a validated patch may be applied")

        fingerprint = self._patch_fingerprint(patch)
        with self.mutation_lock():
            ledger = self._ledger()
            prior = ledger.get(patch.patch_id)
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise ConflictError("patch ID is already recorded with a different fingerprint")
                if self.pending_patch_path.exists():
                    pending = read_json(self.pending_patch_path)
                    if pending.get("patch_id") == patch.patch_id:
                        self.pending_patch_path.unlink()
                self.rebuild_compiled_from_ledger()
                current_issue = self.review_queue.get(patch.issue_id)
                return ApplyResult(
                    False, True, 0,
                    current_issue.status if current_issue is not None else "applied",
                )

            pending = read_json(self.pending_patch_path, default=None)
            intent = {
                "schema_version": 1,
                "patch_id": patch.patch_id,
                "fingerprint": fingerprint,
                "patch": patch.to_dict(),
            }
            if pending is not None and pending != intent:
                raise ConflictError(
                    "a different interrupted patch is pending; recover it before applying another"
                )
            resuming = pending == intent

            expected_units, expected_mappings = self._expected_compiled_state()
            if (not resuming
                    and {key: value.to_dict() for key, value in self.units().items()}
                    != {key: value.to_dict() for key, value in expected_units.items()}):
                raise PatchApplicationError(
                    "compiled content_units were modified outside the ledger; run rebuild"
                )
            if (not resuming
                    and {key: value.to_dict() for key, value in self.mappings().items()}
                    != {key: value.to_dict() for key, value in expected_mappings.items()}):
                raise PatchApplicationError(
                    "compiled chapter mappings were modified outside the ledger; run rebuild"
                )

            issue = self.review_queue.get(patch.issue_id)
            record = self._validate_patch_context(
                patch, issue, allow_terminal=resuming, units=expected_units
            )

            units = dict(expected_units)
            mappings = dict(expected_mappings)
            changed, final_status = self._apply_operations_to_state(
                patch, units, mappings, record
            )
            self._validate_issue_postcondition(
                issue, patch, units, mappings, changed, final_status
            )
            # Only create the write-ahead intent after every deterministic schema,
            # scope, source, operation, and semantic postcondition check passed.
            # Invalid patches therefore cannot strand the workspace in a pending
            # state; from this point onward any failure is a real interrupted commit.
            if not resuming:
                atomic_write_json(self.pending_patch_path, intent)
            self._write_units(units)
            self._write_mappings(mappings)
            self.review_queue.replace(issue.with_status(final_status))
            self.refresh_source_statuses()

            entry = {
                "patch_id": patch.patch_id,
                "fingerprint": fingerprint,
                "issue_id": patch.issue_id,
                "source_id": patch.source_id,
                "source_sha256": patch.source_sha256,
                "patch": patch.to_dict(),
            }
            atomic_write_jsonl(self.ledger_path, list(ledger.values()) + [entry])
            self.pending_patch_path.unlink()
            return ApplyResult(True, False, changed, final_status)
