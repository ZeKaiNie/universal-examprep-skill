"""Small, stdlib-only DOCX/PPTX extractor with fail-closed package handling.

The adapter intentionally returns plain dictionaries so it can feed either the
legacy material builder or the versioned ingestion core without coupling either
one to an OOXML library.  DOCX pages are split only at explicit page-break
markers; OOXML does not contain a portable rendered-page layout.
"""

import hashlib
import contextlib
import os
import posixpath
import re
import struct
import tempfile
import urllib.parse
import zipfile
from xml.etree import ElementTree as ET

from .identifiers import is_link_or_reparse


MAX_ZIP_ENTRIES = 4096
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 32 * 1024 * 1024
MAX_TOTAL_COMPRESSED = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_SINGLE_UNCOMPRESSED = 64 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 1000
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_XML_ELEMENTS = 250000
MAX_XML_DEPTH = 256
MAX_XML_ATTRIBUTES = 1000000
MAX_XML_TEXT_CHARS = 32 * 1024 * 1024
_SNAPSHOT_MEMORY_BYTES = 8 * 1024 * 1024

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SAFE_ASSET_EXTENSIONS = frozenset(
    (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")
)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class OOXMLExtractionError(Exception):
    """Base class for an OOXML source that cannot be safely extracted."""


class OOXMLUnsupportedError(OOXMLExtractionError):
    """The source or one of its required ZIP features is unsupported."""


class OOXMLCorruptError(OOXMLExtractionError):
    """The OOXML package is malformed or missing required parts."""


class OOXMLEncryptedError(OOXMLExtractionError):
    """The package is encrypted and cannot be read without credentials."""


class OOXMLSecurityError(OOXMLExtractionError):
    """The package attempted an unsafe path, relationship, or output write."""


class OOXMLBombError(OOXMLSecurityError):
    """The ZIP package exceeded a bounded expansion limit."""


class OOXMLAssetError(OOXMLExtractionError):
    """An embedded asset could not be written atomically."""


def _local_name(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _attribute(element, local_name, default=None):
    for key, value in element.attrib.items():
        if _local_name(key) == local_name:
            return value
    return default


def _relationship_id(element):
    # p:sldId also has an unqualified numeric `id`; prefer the namespaced
    # relationship id, then a conventional rId value.
    fallback = None
    for key, value in element.attrib.items():
        if _local_name(key) != "id":
            continue
        if key.startswith("{") and "relationships" in key:
            return value
        if isinstance(value, str) and value.startswith("rId"):
            fallback = value
    return fallback


def _safe_member_name(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise OOXMLSecurityError("ZIP member has an invalid name")
    if "\\" in name or name.startswith("/") or _DRIVE_RE.match(name):
        raise OOXMLSecurityError("unsafe ZIP member path: %r" % name)
    raw = name[:-1] if name.endswith("/") else name
    if not raw:
        raise OOXMLSecurityError("ZIP member has an empty path")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise OOXMLSecurityError("unsafe ZIP member path: %r" % name)
    normalized = posixpath.normpath(raw)
    if normalized != raw or normalized.startswith("../"):
        raise OOXMLSecurityError("unsafe ZIP member path: %r" % name)
    return normalized


def _relationship_part(source_part):
    directory = posixpath.dirname(source_part)
    basename = posixpath.basename(source_part)
    return posixpath.join(directory, "_rels", basename + ".rels")


def _resolve_internal_target(source_part, target):
    if not isinstance(target, str) or not target or "\x00" in target or "\\" in target:
        raise OOXMLSecurityError("relationship has an unsafe target: %r" % target)
    decoded = urllib.parse.unquote(target)
    parsed = urllib.parse.urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise OOXMLSecurityError("relationship target is not a package-local part: %r" % target)
    target_path = parsed.path
    if not target_path:
        raise OOXMLSecurityError("relationship target is empty")
    if target_path.startswith("/"):
        combined = target_path.lstrip("/")
    else:
        combined = posixpath.join(posixpath.dirname(source_part), target_path)
    normalized = posixpath.normpath(combined)
    if normalized in ("", ".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        raise OOXMLSecurityError("relationship escapes the OOXML package: %r" % target)
    return _safe_member_name(normalized)


def _stat_identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _expected_digest(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise OOXMLExtractionError("expected_sha256 must be a lowercase SHA-256 digest")
    return value


def _copy_stable_source(path, expected_sha256=None):
    """Copy one bounded regular source to an immutable seekable snapshot."""

    expected_sha256 = _expected_digest(expected_sha256)
    try:
        before = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise OOXMLExtractionError("cannot stat OOXML source: %s" % exc) from exc
    if before.st_size < 1:
        raise OOXMLCorruptError("OOXML source is empty")
    if before.st_size > MAX_ARCHIVE_BYTES:
        raise OOXMLBombError(
            "OOXML source is %d bytes (physical limit %d)"
            % (before.st_size, MAX_ARCHIVE_BYTES)
        )

    snapshot = tempfile.SpooledTemporaryFile(max_size=_SNAPSHOT_MEMORY_BYTES, mode="w+b")
    digest = hashlib.sha256()
    copied = 0
    try:
        with open(path, "rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                copied += len(block)
                if copied > MAX_ARCHIVE_BYTES:
                    raise OOXMLBombError(
                        "OOXML source grew beyond the %d-byte physical limit"
                        % MAX_ARCHIVE_BYTES
                    )
                digest.update(block)
                snapshot.write(block)
        after = os.stat(path, follow_symlinks=False)
    except OOXMLExtractionError:
        snapshot.close()
        raise
    except OSError as exc:
        snapshot.close()
        raise OOXMLExtractionError("cannot snapshot OOXML source: %s" % exc) from exc

    actual_digest = digest.hexdigest()
    if copied != before.st_size or _stat_identity(before) != _stat_identity(after):
        snapshot.close()
        raise OOXMLSecurityError("OOXML source changed while its snapshot was being copied")
    if expected_sha256 is not None and actual_digest != expected_sha256:
        snapshot.close()
        raise OOXMLSecurityError(
            "OOXML source revision does not match expected_sha256"
        )
    snapshot.seek(0)
    return snapshot, actual_digest, _stat_identity(after)


def _read_exact(stream, size, label):
    payload = stream.read(size)
    if len(payload) != size:
        raise OOXMLCorruptError("truncated ZIP %s" % label)
    return payload


def _preflight_zip(stream):
    """Bound the ZIP central directory before ``zipfile.ZipFile`` allocates it."""

    stream.seek(0, os.SEEK_END)
    archive_size = stream.tell()
    tail_size = min(archive_size, 22 + 65535)
    stream.seek(archive_size - tail_size)
    tail = _read_exact(stream, tail_size, "end record")
    relative_eocd = tail.rfind(b"PK\x05\x06")
    if relative_eocd < 0 or relative_eocd + 22 > len(tail):
        raise OOXMLCorruptError("ZIP end-of-central-directory record is missing")
    eocd_offset = archive_size - tail_size + relative_eocd
    fields = struct.unpack("<4s4H2LH", tail[relative_eocd:relative_eocd + 22])
    (
        unused_signature, disk_number, central_disk, disk_entries, total_entries,
        central_size, central_offset, comment_size,
    ) = fields
    if relative_eocd + 22 + comment_size != len(tail):
        raise OOXMLCorruptError("ZIP end record/comment length is inconsistent")
    if disk_number or central_disk or disk_entries != total_entries:
        raise OOXMLUnsupportedError("multi-disk ZIP packages are not supported")
    if total_entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        raise OOXMLUnsupportedError("ZIP64 OOXML packages are not supported")
    if total_entries > MAX_ZIP_ENTRIES:
        raise OOXMLBombError(
            "OOXML package has %d entries (limit %d)" % (total_entries, MAX_ZIP_ENTRIES)
        )
    if central_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise OOXMLBombError(
            "OOXML central directory is %d bytes (limit %d)"
            % (central_size, MAX_CENTRAL_DIRECTORY_BYTES)
        )
    central_end = central_offset + central_size
    if central_offset < 0 or central_end != eocd_offset:
        raise OOXMLCorruptError("ZIP central-directory bounds are inconsistent")

    stream.seek(central_offset)
    total_compressed = 0
    total_uncompressed = 0
    names = set()
    for index in range(total_entries):
        fixed = _read_exact(stream, 46, "central-directory header")
        values = struct.unpack("<4s6H3L5H2L", fixed)
        if values[0] != b"PK\x01\x02":
            raise OOXMLCorruptError(
                "ZIP central-directory entry %d has an invalid signature" % index
            )
        (
            unused_signature, unused_made_by, unused_needed, flags, unused_method,
            unused_time, unused_date, unused_crc, compressed_size, uncompressed_size,
            filename_size, extra_size, member_comment_size, start_disk,
            unused_internal, unused_external, local_offset,
        ) = values
        variable_size = filename_size + extra_size + member_comment_size
        if stream.tell() + variable_size > central_end:
            raise OOXMLCorruptError("ZIP central-directory entry is truncated")
        filename_bytes = _read_exact(stream, filename_size, "member name")
        _read_exact(stream, extra_size + member_comment_size, "member metadata")
        if start_disk:
            raise OOXMLUnsupportedError("multi-disk ZIP member is not supported")
        if compressed_size == 0xFFFFFFFF or uncompressed_size == 0xFFFFFFFF:
            raise OOXMLUnsupportedError("ZIP64 member sizes are not supported")
        if local_offset == 0xFFFFFFFF or local_offset >= central_offset:
            raise OOXMLCorruptError("ZIP member local-header offset is invalid")
        if flags & 0x1:
            raise OOXMLEncryptedError("OOXML ZIP member is encrypted")
        try:
            encoding = "utf-8" if flags & 0x800 else "cp437"
            member_name = filename_bytes.decode(encoding, errors="strict")
        except UnicodeError as exc:
            raise OOXMLCorruptError("ZIP member name has invalid encoding") from exc
        safe_name = _safe_member_name(member_name)
        folded = safe_name.casefold()
        if folded in names:
            raise OOXMLCorruptError(
                "OOXML package contains duplicate member: %s" % safe_name
            )
        names.add(folded)
        if uncompressed_size > MAX_SINGLE_UNCOMPRESSED:
            raise OOXMLBombError(
                "OOXML member %s expands to %d bytes (limit %d)"
                % (safe_name, uncompressed_size, MAX_SINGLE_UNCOMPRESSED)
            )
        total_compressed += compressed_size
        total_uncompressed += uncompressed_size
        if total_compressed > MAX_TOTAL_COMPRESSED:
            raise OOXMLBombError(
                "OOXML package declares more than %d compressed bytes"
                % MAX_TOTAL_COMPRESSED
            )
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
            raise OOXMLBombError(
                "OOXML package expands to more than %d bytes" % MAX_TOTAL_UNCOMPRESSED
            )
        if (uncompressed_size >= 1024 * 1024
                and uncompressed_size > max(1, compressed_size) * MAX_ZIP_COMPRESSION_RATIO):
            raise OOXMLBombError(
                "OOXML member %s exceeds the compression-ratio limit" % safe_name
            )
    if stream.tell() != central_end:
        raise OOXMLCorruptError("ZIP central-directory size does not match its entries")


def _verify_source_revision(path, expected_digest, expected_identity):
    try:
        current = os.stat(path, follow_symlinks=False)
        current_digest = _file_digest(path)
        after = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise OOXMLSecurityError("OOXML source changed or disappeared: %s" % exc) from exc
    if (_stat_identity(current) != _stat_identity(after)
            or _stat_identity(after) != expected_identity
            or current_digest != expected_digest):
        raise OOXMLSecurityError("OOXML source changed during extraction")


@contextlib.contextmanager
def _open_stable_zip(path, expected_sha256=None):
    snapshot, digest, identity = _copy_stable_source(path, expected_sha256)
    try:
        snapshot.seek(0)
        if snapshot.read(8) == _OLE_MAGIC:
            raise OOXMLEncryptedError(
                "source is an encrypted OOXML/legacy OLE container; decrypt or re-save it first"
            )
        snapshot.seek(0)
        _preflight_zip(snapshot)
        snapshot.seek(0)
        try:
            with zipfile.ZipFile(snapshot, "r") as archive:
                yield archive
        except zipfile.BadZipFile as exc:
            raise OOXMLCorruptError("damaged OOXML ZIP package: %s" % exc) from exc
        _verify_source_revision(path, digest, identity)
    finally:
        snapshot.close()


class _BoundedTreeBuilder(object):
    """ElementTree target that aborts before an XML tree can exceed budgets."""

    def __init__(self):
        self.builder = ET.TreeBuilder()
        self.elements = 0
        self.depth = 0
        self.attributes = 0
        self.text_chars = 0

    def start(self, tag, attributes):
        self.elements += 1
        self.depth += 1
        self.attributes += len(attributes)
        if self.elements > MAX_XML_ELEMENTS:
            raise OOXMLBombError("OOXML XML element count exceeds the safety limit")
        if self.depth > MAX_XML_DEPTH:
            raise OOXMLBombError("OOXML XML nesting depth exceeds the safety limit")
        if self.attributes > MAX_XML_ATTRIBUTES:
            raise OOXMLBombError("OOXML XML attribute count exceeds the safety limit")
        return self.builder.start(tag, attributes)

    def data(self, data):
        self.text_chars += len(data)
        if self.text_chars > MAX_XML_TEXT_CHARS:
            raise OOXMLBombError("OOXML XML text size exceeds the safety limit")
        return self.builder.data(data)

    def end(self, tag):
        result = self.builder.end(tag)
        self.depth -= 1
        return result

    def close(self):
        return self.builder.close()

    def doctype(self, name, public_id, system_id):
        raise OOXMLSecurityError("DTD declarations are not allowed in OOXML")


def _has_forbidden_xml_declaration(payload):
    carry = b""
    for offset in range(0, len(payload), 64 * 1024):
        chunk = carry + payload[offset:offset + 64 * 1024]
        normalized = chunk.upper().replace(b"\x00", b"")
        if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
            return True
        carry = chunk[-64:]
    return False


class _Package(object):
    def __init__(self, archive):
        self.archive = archive
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise OOXMLBombError(
                "OOXML package has %d entries (limit %d)" % (len(infos), MAX_ZIP_ENTRIES)
            )
        total = 0
        by_name = {}
        folded_names = set()
        for info in infos:
            name = _safe_member_name(info.filename)
            folded = name.casefold()
            if folded in folded_names:
                raise OOXMLCorruptError("OOXML package contains duplicate member: %s" % name)
            folded_names.add(folded)
            if info.flag_bits & 0x1:
                raise OOXMLEncryptedError("OOXML ZIP member is encrypted: %s" % name)
            if info.file_size > MAX_SINGLE_UNCOMPRESSED:
                raise OOXMLBombError(
                    "OOXML member %s expands to %d bytes (limit %d)"
                    % (name, info.file_size, MAX_SINGLE_UNCOMPRESSED)
                )
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise OOXMLBombError(
                    "OOXML package expands to more than %d bytes" % MAX_TOTAL_UNCOMPRESSED
                )
            if not info.is_dir():
                by_name[name] = info
        self.by_name = by_name
        self.binary_cache = {}
        if "[Content_Types].xml" not in self.by_name:
            raise OOXMLCorruptError("OOXML package is missing [Content_Types].xml")
        self.xml("[Content_Types].xml")

    def has(self, name):
        return name in self.by_name

    def read(self, name):
        safe_name = _safe_member_name(name)
        info = self.by_name.get(safe_name)
        if info is None:
            raise OOXMLCorruptError("OOXML package is missing required part: %s" % safe_name)
        try:
            payload = self.archive.read(info)
        except RuntimeError as exc:
            if "password" in str(exc).lower() or "encrypt" in str(exc).lower():
                raise OOXMLEncryptedError("OOXML member is encrypted: %s" % safe_name) from exc
            raise OOXMLCorruptError("cannot read OOXML member %s: %s" % (safe_name, exc)) from exc
        except (zipfile.BadZipFile, EOFError, OSError) as exc:
            raise OOXMLCorruptError("cannot read OOXML member %s: %s" % (safe_name, exc)) from exc
        except NotImplementedError as exc:
            raise OOXMLUnsupportedError(
                "unsupported ZIP compression for OOXML member %s" % safe_name
            ) from exc
        if len(payload) != info.file_size or len(payload) > MAX_SINGLE_UNCOMPRESSED:
            raise OOXMLBombError("OOXML member size changed while reading: %s" % safe_name)
        return payload

    def read_cached(self, name):
        """Read one binary part at most once, bounding repeated references."""

        safe_name = _safe_member_name(name)
        if safe_name not in self.binary_cache:
            self.binary_cache[safe_name] = self.read(safe_name)
        return self.binary_cache[safe_name]

    def xml(self, name):
        payload = self.read(name)
        if len(payload) > MAX_XML_BYTES:
            raise OOXMLBombError("OOXML XML part exceeds %d bytes: %s" % (MAX_XML_BYTES, name))
        # Removing NULs catches UTF-16 declaration tokens; scan in bounded
        # windows so the security check does not duplicate a 32 MiB XML part.
        if _has_forbidden_xml_declaration(payload):
            raise OOXMLSecurityError("DTD/entity declarations are not allowed in OOXML: %s" % name)
        try:
            parser = ET.XMLParser(target=_BoundedTreeBuilder())
            for offset in range(0, len(payload), 64 * 1024):
                parser.feed(payload[offset:offset + 64 * 1024])
            return parser.close()
        except OOXMLExtractionError:
            raise
        except ET.ParseError as exc:
            raise OOXMLCorruptError("malformed OOXML XML part %s: %s" % (name, exc)) from exc


def _relationships(package, source_part, required=False):
    rels_part = _relationship_part(source_part)
    if not package.has(rels_part):
        if required:
            raise OOXMLCorruptError("OOXML package is missing relationships: %s" % rels_part)
        return {}
    root = package.xml(rels_part)
    result = {}
    for node in root.iter():
        if _local_name(node.tag) != "Relationship":
            continue
        relationship_id = node.attrib.get("Id")
        relationship_type = node.attrib.get("Type")
        target = node.attrib.get("Target")
        mode = (node.attrib.get("TargetMode") or "Internal").strip().lower()
        if not relationship_id or not relationship_type or not target:
            raise OOXMLCorruptError("malformed relationship in %s" % rels_part)
        if relationship_id in result:
            raise OOXMLCorruptError("duplicate relationship id %s in %s" % (relationship_id, rels_part))
        if mode not in ("internal", "external"):
            raise OOXMLCorruptError("unknown relationship TargetMode %r" % mode)
        resolved = None
        if mode == "internal":
            resolved = _resolve_internal_target(source_part, target)
        elif relationship_type.rstrip("/").lower().endswith((
            "/image", "/slide", "/notesslide", "/chart", "/diagramdata",
            "/oleobject", "/package", "/audio", "/video",
        )):
            # Hyperlinks are ordinary metadata and are never followed by this adapter.  External
            # relationships for parts we *do* consume must fail closed even if malformed markup
            # forgot to reference the relationship id.
            raise OOXMLSecurityError(
                "external OOXML content relationship is not allowed: %s" % target
            )
        result[relationship_id] = {
            "id": relationship_id,
            "type": relationship_type,
            "target": target,
            "external": mode == "external",
            "resolved": resolved,
        }
    return result


def _resolved_relationship(relationships, relationship_id, expected_suffix):
    relationship = relationships.get(relationship_id)
    if relationship is None:
        raise OOXMLCorruptError("missing relationship id: %s" % relationship_id)
    if relationship["external"]:
        raise OOXMLSecurityError(
            "external relationship is not allowed for %s: %s"
            % (expected_suffix.lstrip("/"), relationship["target"])
        )
    rel_type = relationship["type"].rstrip("/").lower()
    if not rel_type.endswith(expected_suffix.lower()):
        raise OOXMLCorruptError(
            "relationship %s has type %s, expected *%s"
            % (relationship_id, relationship["type"], expected_suffix)
        )
    return relationship["resolved"]


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _validate_raster_payload(extension, payload):
    try:
        # Local import avoids a module-import cycle: standalone raster extraction
        # reuses this module's atomic asset writer.
        from .raster import RasterExtractionError, inspect_raster_payload
        inspect_raster_payload(payload, extension=extension)
    except (RasterExtractionError, TypeError, ValueError) as exc:
        raise OOXMLUnsupportedError("unsafe embedded raster %s: %s" % (
            extension or "(none)", exc,
        )) from exc


class _AssetWriter(object):
    def __init__(self, root, source_file):
        if root is None:
            self.root = None
        else:
            try:
                raw_root = os.fspath(root)
            except TypeError as exc:
                raise OOXMLAssetError("asset_root must be a filesystem path") from exc
            if not isinstance(raw_root, str) or not raw_root.strip() or "\x00" in raw_root:
                raise OOXMLAssetError("asset_root must be a non-empty text path")
            self.root = os.path.abspath(raw_root)
        self.source_file = source_file
        self.cache = {}
        self.part_cache = {}
        self.part_failures = {}
        self.hashes = {}
        self.created = []
        self._ready = False

    def _ensure_root(self):
        if self.root is None or self._ready:
            return
        current = self.root
        while True:
            if os.path.lexists(current) and is_link_or_reparse(current):
                raise OOXMLSecurityError(
                    "asset_root path must not pass through a link/junction/reparse point"
                )
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        if os.path.lexists(self.root):
            if not os.path.isdir(self.root):
                raise OOXMLSecurityError("asset_root must be a real directory, not a link/special file")
        else:
            try:
                os.makedirs(self.root)
            except OSError as exc:
                raise OOXMLAssetError("cannot create asset_root %s: %s" % (self.root, exc)) from exc
        if is_link_or_reparse(self.root):
            raise OOXMLSecurityError("asset_root became a symbolic link")
        self._ready = True

    def save(self, archive_part, payload):
        if archive_part in self.part_cache:
            return self.part_cache[archive_part]
        if archive_part in self.part_failures:
            raise OOXMLUnsupportedError(self.part_failures[archive_part])
        key = (archive_part, hashlib.sha256(payload).hexdigest())
        if key in self.cache:
            result = self.cache[key]
            self.part_cache[archive_part] = result
            return result
        extension = posixpath.splitext(archive_part)[1].lower()
        # Probe before creating the output directory or any temporary file.
        try:
            _validate_raster_payload(extension, payload)
        except OOXMLUnsupportedError as exc:
            self.part_failures[archive_part] = str(exc)
            raise
        self._ensure_root()
        source_stem = os.path.splitext(os.path.basename(self.source_file))[0]
        source_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_stem).strip("._-") or "source"
        source_stem = source_stem[:80]
        source_hash = hashlib.sha256(self.source_file.encode("utf-8")).hexdigest()[:10]
        part_hash = hashlib.sha256(archive_part.encode("utf-8")).hexdigest()[:10]
        content_hash = key[1][:16]
        if self.root is None:
            self.part_cache[archive_part] = None
            return None
        filename = "%s_%s_%s_%s%s" % (
            source_stem,
            source_hash,
            part_hash,
            content_hash,
            extension,
        )
        target = os.path.join(self.root, filename)
        try:
            if os.path.commonpath((self.root, os.path.abspath(target))) != self.root:
                raise OOXMLSecurityError("generated asset path escaped asset_root")
        except ValueError as exc:
            raise OOXMLSecurityError("generated asset path is on another filesystem") from exc
        if os.path.lexists(target):
            if is_link_or_reparse(target) or not os.path.isfile(target):
                raise OOXMLSecurityError("asset target is a link or special file: %s" % target)
            try:
                if _file_digest(target) != key[1]:
                    raise OOXMLAssetError("deterministic asset target contains different bytes: %s" % target)
            except OSError as exc:
                raise OOXMLAssetError("cannot verify existing asset %s: %s" % (target, exc)) from exc
            self.cache[key] = filename
            self.part_cache[archive_part] = filename
            self.hashes[filename] = key[1]
            return filename

        descriptor = None
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".ooxml-", suffix=".tmp", dir=self.root)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if os.path.lexists(target):
                raise OOXMLSecurityError("asset target appeared during atomic write: %s" % target)
            os.replace(temporary, target)
            temporary = None
            self.created.append((target, key[1]))
        except OOXMLExtractionError:
            raise
        except OSError as exc:
            raise OOXMLAssetError("cannot atomically write asset %s: %s" % (target, exc)) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        self.cache[key] = filename
        self.part_cache[archive_part] = filename
        self.hashes[filename] = key[1]
        return filename

    def digest_for(self, filename):
        return self.hashes.get(filename)

    def rollback(self):
        """Remove only assets created by this failed package extraction."""

        for target, expected_hash in reversed(self.created):
            try:
                if (os.path.isfile(target) and not is_link_or_reparse(target)
                        and _file_digest(target) == expected_hash):
                    os.unlink(target)
            except OSError:
                pass
        self.created = []


class _RecordBuilder(object):
    def __init__(self, source_file, page):
        self.source_file = source_file
        self.page = page
        self.elements = []
        self.assets = []
        self._text = []
        self.review_signals = []

    def add(
        self, kind, text="", asset=None, level=None, asset_role=None,
        asset_sha256=None,
    ):
        normalized_text = text if isinstance(text, str) else str(text or "")
        element = {
            "kind": kind,
            "text": normalized_text,
            "asset": asset,
            "ordinal": len(self.elements),
            "bbox": None,
        }
        if level is not None:
            element["level"] = level
        if asset_role is not None:
            element["asset_role"] = asset_role
        if asset_sha256 is not None:
            element["asset_sha256"] = asset_sha256
        self.elements.append(element)
        # Speaker notes are retained as a separate reviewable layer.  They are
        # deliberately not folded into slide prose because teachers commonly put
        # answers or presenter-only hints there.
        if normalized_text and kind not in ("figure", "speaker_notes", "answer"):
            self._text.append(normalized_text)
        if asset is not None and asset not in self.assets:
            self.assets.append(asset)

    def review(self, reason_code, detail):
        signal = {"reason_code": reason_code, "detail": detail}
        if signal not in self.review_signals:
            self.review_signals.append(signal)

    def finish(self):
        return {
            "file": self.source_file,
            "page": self.page,
            "text": "\n".join(self._text),
            "elements": self.elements,
            "embedded_assets": self.assets,
            "review_signals": self.review_signals,
        }


def _paragraph_segments(paragraph):
    segments = [""]
    for node in paragraph.iter():
        name = _local_name(node.tag)
        if name == "t" and node.text:
            segments[-1] += node.text
        elif name == "tab":
            segments[-1] += "\t"
        elif name == "br":
            if (_attribute(node, "type") or "").lower() == "page":
                segments.append("")
            else:
                segments[-1] += "\n"
        elif name == "lastRenderedPageBreak":
            segments.append("")
    return segments


def _docx_styles(package):
    if not package.has("word/styles.xml"):
        return {}
    root = package.xml("word/styles.xml")
    styles = {}
    for style in root.iter():
        if _local_name(style.tag) != "style":
            continue
        style_id = _attribute(style, "styleId")
        if not style_id:
            continue
        style_name = None
        for node in style:
            if _local_name(node.tag) == "name":
                style_name = _attribute(node, "val")
                break
        styles[style_id] = style_name or style_id
    return styles


def _docx_paragraph_kind(paragraph, styles):
    style_id = None
    for node in paragraph.iter():
        if _local_name(node.tag) == "pStyle":
            style_id = _attribute(node, "val")
            break
    if not style_id:
        return "text"
    label = (styles.get(style_id) or style_id).strip().lower().replace("_", " ")
    if label.startswith("heading") or label.startswith("title") or label.startswith("标题"):
        return "heading"
    return "text"


def _docx_heading_level(paragraph, styles):
    style_id = None
    for node in paragraph.iter():
        if _local_name(node.tag) == "pStyle":
            style_id = _attribute(node, "val")
            break
    if not style_id:
        return None
    label = (styles.get(style_id) or style_id).strip().lower().replace("_", " ")
    match = re.search(r"(?:heading|标题)\s*([1-6])", label)
    if match:
        return int(match.group(1))
    return 1 if label.startswith("title") else None


_COMPLEX_RELATIONSHIP_SUFFIXES = (
    "/chart", "/diagramdata", "/diagramlayout", "/diagramcolors", "/diagramquickstyle",
    "/oleobject", "/package", "/audio", "/video",
)
_OMITTED_TEXT_RELATIONSHIP_SUFFIXES = (
    "/footnotes", "/endnotes", "/comments", "/header", "/footer",
)


def _record_complex_relationships(record, relationships):
    for relationship in relationships.values():
        relation_type = relationship["type"].rstrip("/").lower()
        if relation_type.endswith(_COMPLEX_RELATIONSHIP_SUFFIXES):
            record.review(
                "ooxml_complex_content",
                "unparsed OOXML relationship: %s" % relationship["type"],
            )
        if relation_type.endswith(_OMITTED_TEXT_RELATIONSHIP_SUFFIXES):
            record.review(
                "ooxml_omitted_text_part",
                "text-bearing OOXML relationship is not merged into body text: %s"
                % relationship["type"],
            )


def _docx_table_text(table):
    rows = []
    for row in table.iter():
        if _local_name(row.tag) != "tr":
            continue
        cells = []
        for cell in list(row):
            if _local_name(cell.tag) != "tc":
                continue
            paragraphs = []
            for paragraph in cell.iter():
                if _local_name(paragraph.tag) == "p":
                    value = "".join(_paragraph_segments(paragraph)).strip()
                    if value:
                        paragraphs.append(value)
            cells.append("\n".join(paragraphs))
        rows.append("\t".join(cells))
    return "\n".join(row for row in rows if row.strip())


def _image_references(container):
    references = []
    visited = set()

    def append_from(owner):
        alt = next((
            _attribute(node, "descr") or _attribute(node, "title") or _attribute(node, "name")
            for node in owner.iter() if _local_name(node.tag) in ("docPr", "cNvPr")
            and (_attribute(node, "descr") or _attribute(node, "title") or _attribute(node, "name"))
        ), "Embedded image")
        for node in owner.iter():
            name = _local_name(node.tag)
            relationship_id = None
            if name in ("blip", "svgBlip"):
                relationship_id = _attribute(node, "embed") or _attribute(node, "link")
            elif name == "imagedata":
                relationship_id = _attribute(node, "id")
            if relationship_id and id(node) not in visited:
                visited.add(id(node))
                references.append((relationship_id, alt))

    # Bind descriptive text to its own picture/drawing subtree.  Collecting all
    # cNvPr names slide-wide mismatches a text shape's name with the next picture.
    for node in container.iter():
        if _local_name(node.tag) in ("pic", "inline", "anchor", "pict"):
            append_from(node)
    append_from(container)  # orphan image references still remain visible/reviewable
    return references


def _append_image_references(
    record, references, relationships, package, writer, asset_role=None,
):
    for relationship_id, alt in references:
        archive_part = _resolved_relationship(relationships, relationship_id, "/image")
        payload = package.read_cached(archive_part)
        try:
            asset = writer.save(archive_part, payload)
        except OOXMLUnsupportedError as exc:
            record.review("ooxml_unsafe_asset", str(exc))
            asset = None
        if asset is None:
            record.review(
                "ooxml_asset_not_materialized",
                "embedded image %s was detected but no safe local raster asset was produced"
                % archive_part,
            )
        record.add(
            "figure", alt or posixpath.basename(archive_part), asset,
            asset_role=asset_role,
            asset_sha256=writer.digest_for(asset) if asset is not None else None,
        )


def _append_images(record, container, relationships, package, writer, asset_role=None):
    _append_image_references(
        record,
        _image_references(container),
        relationships,
        package,
        writer,
        asset_role=asset_role,
    )


def _paragraph_image_references(paragraph, segment_count):
    """Assign inline image relationships around explicit page-break events."""

    all_refs = _image_references(paragraph)
    alt_by_relationship = {}
    for relationship_id, alt in all_refs:
        alt_by_relationship.setdefault(relationship_id, []).append(alt)
    offsets = {key: 0 for key in alt_by_relationship}
    result = [[] for _unused in range(max(1, segment_count))]
    segment = 0
    for node in paragraph.iter():
        name = _local_name(node.tag)
        if (name == "br" and (_attribute(node, "type") or "").lower() == "page") or (
                name == "lastRenderedPageBreak"):
            segment = min(segment + 1, len(result) - 1)
            continue
        relationship_id = None
        if name in ("blip", "svgBlip"):
            relationship_id = _attribute(node, "embed") or _attribute(node, "link")
        elif name == "imagedata":
            relationship_id = _attribute(node, "id")
        if relationship_id:
            choices = alt_by_relationship.get(relationship_id) or ["Embedded image"]
            offset = offsets.get(relationship_id, 0)
            alt = choices[min(offset, len(choices) - 1)]
            offsets[relationship_id] = offset + 1
            result[segment].append((relationship_id, alt))
    return result


def _resolve_alternate_content(root):
    """Choose one Markup-Compatibility branch instead of concatenating both."""

    resolved = 0

    def visit(parent):
        nonlocal resolved
        for child in list(parent):
            if _local_name(child.tag) != "AlternateContent":
                visit(child)
                continue
            branches = list(child)
            selected = next(
                (branch for branch in branches if _local_name(branch.tag) == "Fallback"),
                branches[0] if branches else None,
            )
            index = list(parent).index(child)
            parent.remove(child)
            if selected is not None:
                for offset, replacement in enumerate(list(selected)):
                    parent.insert(index + offset, replacement)
                    visit(replacement)
            resolved += 1

    visit(root)
    return resolved


def _docx_body_blocks(body):
    """Yield paragraphs/tables, including block content nested in w:sdt controls."""

    for child in list(body):
        name = _local_name(child.tag)
        if name in ("p", "tbl"):
            yield child
        elif name in ("sdt", "customXml", "smartTag"):
            content = next(
                (node for node in child.iter() if _local_name(node.tag) == "sdtContent"),
                None,
            )
            nested_root = content if content is not None else child
            for nested in _docx_body_blocks(nested_root):
                yield nested


def _extract_docx(package, source_file, writer):
    main_part = "word/document.xml"
    root = package.xml(main_part)
    alternate_count = _resolve_alternate_content(root)
    styles = _docx_styles(package)
    relationships = _relationships(package, main_part)
    body = next((node for node in root.iter() if _local_name(node.tag) == "body"), None)
    if body is None:
        raise OOXMLCorruptError("DOCX document.xml has no body")

    records = []
    page_number = 1
    record = _RecordBuilder(source_file, page_number)
    if alternate_count:
        record.review(
            "ooxml_alternate_content_review",
            "%d Markup Compatibility branch(es) used the fallback/first supported view"
            % alternate_count,
        )
    unsupported_blocks = sorted({
        _local_name(child.tag) for child in list(body)
        if _local_name(child.tag) not in (
            "p", "tbl", "sdt", "customXml", "smartTag", "sectPr"
        )
    })
    if unsupported_blocks:
        record.review(
            "ooxml_omitted_text_part",
            "unparsed DOCX body block(s): %s" % ", ".join(unsupported_blocks),
        )
    for child in _docx_body_blocks(body):
        name = _local_name(child.tag)
        if name == "p":
            segments = _paragraph_segments(child)
            image_segments = _paragraph_image_references(child, len(segments))
            has_math = any(
                _local_name(node.tag) in ("oMath", "oMathPara")
                for node in child.iter()
            )
            has_numbering = any(
                _local_name(node.tag) == "numPr" for node in child.iter()
            )
            kind = (
                "formula" if has_math else
                "list" if has_numbering else
                _docx_paragraph_kind(child, styles)
            )
            level = _docx_heading_level(child, styles) if kind == "heading" else None
            if has_math:
                record.review(
                    "ooxml_math_structure_review",
                    "Word OMML math was detected; concatenated glyph text is not a faithful formula tree",
                )
            if has_numbering:
                record.review(
                    "ooxml_list_numbering_review",
                    "Word numbering.xml markers were not reconstructed; list text and order need review",
                )
            for index, segment in enumerate(segments):
                if segment.strip():
                    record.add(kind, segment.strip(), level=level)
                _append_image_references(
                    record, image_segments[index], relationships, package, writer
                )
                if len(segments) > 1 and any(image_segments):
                    record.review(
                        "docx_page_break_image_ambiguous",
                        "an image shares a paragraph containing a page break; XML order was used",
                    )
                if index < len(segments) - 1:
                    records.append(record.finish())
                    page_number += 1
                    record = _RecordBuilder(source_file, page_number)
        elif name == "tbl":
            table_text = _docx_table_text(child)
            if table_text:
                record.add("table", table_text)
            _append_images(record, child, relationships, package, writer)
    if record.elements or not records:
        records.append(record.finish())
    for current in records:
        for relationship in relationships.values():
            relation_type = relationship["type"].rstrip("/").lower()
            if relation_type.endswith(_OMITTED_TEXT_RELATIONSHIP_SUFFIXES):
                current.setdefault("review_signals", []).append({
                    "reason_code": "ooxml_omitted_text_part",
                    "detail": "DOCX header/footer/footnote/endnote/comment content was not merged",
                })
        if any(
            relationship["type"].rstrip("/").lower().endswith(_COMPLEX_RELATIONSHIP_SUFFIXES)
            for relationship in relationships.values()
        ):
            current.setdefault("review_signals", []).append({
                "reason_code": "ooxml_complex_content",
                "detail": "DOCX contains chart/diagram/OLE/media relationships not parsed structurally",
            })
    return records


def _drawing_paragraph_text(paragraph):
    parts = []
    for node in paragraph.iter():
        name = _local_name(node.tag)
        if name == "t" and node.text:
            parts.append(node.text)
        elif name == "br":
            parts.append("\n")
        elif name == "tab":
            parts.append("\t")
    return "".join(parts).strip()


def _ppt_shape_kind(shape):
    placeholder = None
    for node in shape.iter():
        if _local_name(node.tag) == "ph":
            placeholder = (_attribute(node, "type") or "").lower()
            break
    return "heading" if placeholder in ("title", "ctrtitle", "subtitle") else "text"


def _ppt_table_text(table):
    rows = []
    for row in table.iter():
        if _local_name(row.tag) != "tr":
            continue
        cells = []
        for cell in list(row):
            if _local_name(cell.tag) != "tc":
                continue
            paragraphs = []
            for paragraph in cell.iter():
                if _local_name(paragraph.tag) == "p":
                    value = _drawing_paragraph_text(paragraph)
                    if value:
                        paragraphs.append(value)
            cells.append("\n".join(paragraphs))
        rows.append("\t".join(cells))
    return "\n".join(row for row in rows if row.strip())


def _ppt_object_hidden(node):
    return any(
        _local_name(child.tag) == "cNvPr"
        and (_attribute(child, "hidden") or "").strip().lower() in ("1", "true", "on", "yes")
        for child in node.iter()
    )


def _append_ppt_content(record, root, isolate_all=False):
    complex_shape_count = sum(
        1 for node in root.iter() if _local_name(node.tag) in ("cxnSp", "grpSp")
    )
    empty_vector_shapes = sum(
        1 for node in root.iter()
        if _local_name(node.tag) == "sp"
        and any(_local_name(child.tag) == "prstGeom" for child in node.iter())
        and not any(_local_name(child.tag) == "t" and child.text for child in node.iter())
    )
    if complex_shape_count or empty_vector_shapes:
        record.review(
            "ooxml_vector_content",
            "slide contains %d grouped/connector and %d textless vector shapes"
            % (complex_shape_count, empty_vector_shapes),
        )
    if any(
        _local_name(node.tag) in ("oMath", "oMathPara")
        for node in root.iter()
    ):
        record.review(
            "ooxml_math_structure_review",
            "PowerPoint OMML math was detected; visual/structured formula review is required",
        )
    for node in root.iter():
        name = _local_name(node.tag)
        if name == "sp":
            kind = _ppt_shape_kind(node)
            isolated = isolate_all or _ppt_object_hidden(node)
            if isolated and not isolate_all:
                record.review(
                    "ooxml_hidden_shape_answer_candidate",
                    "a hidden PowerPoint shape was isolated from student-visible slide prose",
                )
            for paragraph in node.iter():
                if _local_name(paragraph.tag) != "p":
                    continue
                value = _drawing_paragraph_text(paragraph)
                if value:
                    if isolated:
                        record.add("speaker_notes", value)
                        continue
                    bullet = next((
                        child for child in paragraph.iter()
                        if _local_name(child.tag) in ("buChar", "buAutoNum")
                    ), None)
                    paragraph_kind = "list" if bullet is not None else kind
                    if bullet is not None:
                        marker = _attribute(bullet, "char")
                        if marker:
                            value = "%s %s" % (marker, value)
                        elif _local_name(bullet.tag) == "buAutoNum":
                            record.review(
                                "ooxml_list_numbering_review",
                                "PowerPoint automatic list numbering was not reconstructed",
                            )
                    record.add(
                        paragraph_kind,
                        value,
                        level=1 if paragraph_kind == "heading" else None,
                    )
        elif name == "tbl":
            value = _ppt_table_text(node)
            if value:
                record.add("table", value)


def _append_speaker_notes(
    record, notes_root, notes_relationships=None, package=None, writer=None,
):
    def note_kind(value):
        return (
            "answer"
            if re.search(
                r"(?im)^\s*(?:answers?|solutions?|correct\s+answer|答案|解答|参考答案)\s*[:：]",
                value,
            )
            else "speaker_notes"
        )

    found_shape = False
    for shape in notes_root.iter():
        if _local_name(shape.tag) != "sp":
            continue
        found_shape = True
        placeholder = None
        for node in shape.iter():
            if _local_name(node.tag) == "ph":
                placeholder = (_attribute(node, "type") or "body").lower()
                break
        if placeholder in ("hdr", "ftr", "dt", "sldnum"):
            continue
        for paragraph in shape.iter():
            if _local_name(paragraph.tag) != "p":
                continue
            value = _drawing_paragraph_text(paragraph)
            if value:
                record.add(note_kind(value), value)
    if not found_shape:
        values = [node.text for node in notes_root.iter()
                  if _local_name(node.tag) == "t" and node.text]
        value = "".join(values).strip()
        if value:
            record.add(note_kind(value), value)
    if notes_relationships is not None and package is not None and writer is not None:
        references = _image_references(notes_root)
        if references:
            record.review(
                "speaker_note_answer_asset_candidate",
                "speaker notes contain embedded image evidence isolated as answer_context",
            )
            _append_image_references(
                record,
                references,
                notes_relationships,
                package,
                writer,
                asset_role="answer_context",
            )


def _extract_pptx(package, source_file, writer):
    presentation_part = "ppt/presentation.xml"
    presentation = package.xml(presentation_part)
    presentation_relationships = _relationships(package, presentation_part, required=True)
    slide_ids = []
    for node in presentation.iter():
        if _local_name(node.tag) == "sldId":
            relationship_id = _relationship_id(node)
            if not relationship_id:
                raise OOXMLCorruptError("PPTX slide id is missing its relationship id")
            slide_ids.append(relationship_id)

    records = []
    for page_number, relationship_id in enumerate(slide_ids, 1):
        slide_part = _resolved_relationship(presentation_relationships, relationship_id, "/slide")
        slide_root = package.xml(slide_part)
        alternate_count = _resolve_alternate_content(slide_root)
        slide_relationships = _relationships(package, slide_part)
        record = _RecordBuilder(source_file, page_number)
        hidden_slide = (
            (_attribute(slide_root, "show") or "").strip().lower()
            in ("0", "false", "off", "no")
        )
        if hidden_slide:
            record.review(
                "ooxml_hidden_slide_answer_candidate",
                "a hidden PowerPoint slide was isolated from student-visible prose",
            )
        if any(_local_name(node.tag) == "timing" for node in slide_root.iter()):
            record.review(
                "ooxml_animation_order_review",
                "slide timing/animation may reveal content progressively; XML order is not presentation order",
            )
        if alternate_count:
            record.review(
                "ooxml_alternate_content_review",
                "%d Markup Compatibility branch(es) used the fallback/first supported view"
                % alternate_count,
            )
        _append_ppt_content(record, slide_root, isolate_all=hidden_slide)
        all_image_refs = _image_references(slide_root)
        hidden_image_ids = set()
        if not hidden_slide:
            for node in slide_root.iter():
                if _local_name(node.tag) in ("sp", "pic", "graphicFrame") and _ppt_object_hidden(node):
                    hidden_image_ids.update(ref_id for ref_id, unused in _image_references(node))
            if hidden_image_ids:
                record.review(
                    "ooxml_hidden_shape_answer_candidate",
                    "hidden PowerPoint image content was isolated as answer_context",
                )
        hidden_refs = [
            pair for pair in all_image_refs
            if hidden_slide or pair[0] in hidden_image_ids
        ]
        visible_refs = [
            pair for pair in all_image_refs
            if not hidden_slide and pair[0] not in hidden_image_ids
        ]
        _append_image_references(
            record, visible_refs, slide_relationships, package, writer
        )
        _append_image_references(
            record, hidden_refs, slide_relationships, package, writer,
            asset_role="answer_context",
        )
        _record_complex_relationships(record, slide_relationships)
        for relationship in slide_relationships.values():
            if relationship["type"].rstrip("/").lower().endswith("/notesslide"):
                notes_part = _resolved_relationship(
                    slide_relationships, relationship["id"], "/notesSlide"
                )
                notes_root = package.xml(notes_part)
                notes_alternates = _resolve_alternate_content(notes_root)
                if notes_alternates:
                    record.review(
                        "ooxml_alternate_content_review",
                        "speaker notes contain Markup Compatibility alternate content",
                    )
                notes_relationships = _relationships(package, notes_part)
                _record_complex_relationships(record, notes_relationships)
                _append_speaker_notes(
                    record, notes_root, notes_relationships, package, writer
                )
        records.append(record.finish())
    return records


def extract_ooxml(path, source_file, asset_root=None, expected_sha256=None):
    """Extract a DOCX/PPTX into ordered page/slide records.

    Each record contains ``file``, ``page``, ``text``, ``elements`` and
    ``embedded_assets``.  Element bounding boxes are explicitly ``None`` because
    OOXML source markup does not provide a reliable rendered coordinate system.
    Assets are materialized only when ``asset_root`` is supplied; returned asset
    values are deterministic filenames relative to that root.
    """
    if not isinstance(source_file, str) or not source_file.strip() or "\x00" in source_file:
        raise OOXMLExtractionError("source_file must be a non-empty string")
    try:
        filesystem_path = os.path.abspath(os.fspath(path))
    except TypeError as exc:
        raise OOXMLExtractionError("path must be a filesystem path") from exc
    extension = os.path.splitext(filesystem_path)[1].lower()
    if extension not in (".docx", ".pptx"):
        raise OOXMLUnsupportedError("unsupported OOXML extension: %s" % (extension or "(none)"))
    if not os.path.isfile(filesystem_path):
        raise OOXMLExtractionError("OOXML source is not a regular file: %s" % filesystem_path)
    if is_link_or_reparse(filesystem_path):
        raise OOXMLSecurityError("OOXML source must not be a link/junction/reparse point")
    writer = _AssetWriter(asset_root, source_file)
    try:
        with _open_stable_zip(filesystem_path, expected_sha256=expected_sha256) as archive:
            package = _Package(archive)
            if extension == ".docx":
                return _extract_docx(package, source_file, writer)
            return _extract_pptx(package, source_file, writer)
    except OOXMLExtractionError:
        writer.rollback()
        raise
    except zipfile.BadZipFile as exc:
        writer.rollback()
        raise OOXMLCorruptError("damaged OOXML ZIP package: %s" % exc) from exc
    except NotImplementedError as exc:
        writer.rollback()
        raise OOXMLUnsupportedError("unsupported OOXML ZIP feature: %s" % exc) from exc
    except ET.ParseError as exc:
        writer.rollback()
        raise OOXMLCorruptError("malformed OOXML XML: %s" % exc) from exc
    except OSError as exc:
        writer.rollback()
        raise OOXMLCorruptError("OOXML package changed or became unreadable: %s" % exc) from exc


__all__ = [
    "MAX_SINGLE_UNCOMPRESSED",
    "MAX_ARCHIVE_BYTES",
    "MAX_CENTRAL_DIRECTORY_BYTES",
    "MAX_TOTAL_COMPRESSED",
    "MAX_TOTAL_UNCOMPRESSED",
    "MAX_ZIP_COMPRESSION_RATIO",
    "MAX_XML_BYTES",
    "MAX_XML_ELEMENTS",
    "MAX_XML_DEPTH",
    "MAX_XML_ATTRIBUTES",
    "MAX_XML_TEXT_CHARS",
    "MAX_ZIP_ENTRIES",
    "OOXMLAssetError",
    "OOXMLBombError",
    "OOXMLCorruptError",
    "OOXMLEncryptedError",
    "OOXMLExtractionError",
    "OOXMLSecurityError",
    "OOXMLUnsupportedError",
    "extract_ooxml",
]
