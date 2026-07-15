"""Safe standalone raster inspection and page-equivalent extraction."""

import hashlib
import os
import struct
from dataclasses import dataclass

from .adapters import validate_page_records
from .identifiers import is_link_or_reparse, normalize_workspace_path
from .ooxml import (
    OOXMLExtractionError, OOXMLSecurityError, OOXMLUnsupportedError, _AssetWriter,
)
from .quality import assess_page


MAX_RASTER_BYTES = 256 * 1024 * 1024
MAX_RASTER_PIXELS = 250000000
MAX_RASTER_DIMENSION = 100000
MAX_RASTER_DECODED_BYTES = 512 * 1024 * 1024
MAX_SIDECAR_BYTES = 16 * 1024 * 1024

_EXTENSIONS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
}
_MIME_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}


class RasterExtractionError(Exception):
    """Base class for standalone image extraction failures."""


class RasterUnsupportedError(RasterExtractionError):
    """The image format or a format feature is unsupported."""


class RasterCorruptError(RasterExtractionError):
    """The image bytes are truncated or structurally inconsistent."""


class RasterSecurityError(RasterExtractionError):
    """The image/sidecar/output path violates local safety rules."""


@dataclass(frozen=True)
class RasterInfo:
    format: str
    media_type: str
    width: int
    height: int
    byte_size: int
    sha256: str

    @property
    def pixels(self):
        return self.width * self.height

    def to_dict(self):
        return {
            "format": self.format,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


def _safe_regular_file(path, label):
    try:
        value = os.path.abspath(os.fspath(path))
    except TypeError as exc:
        raise RasterExtractionError("%s must be a filesystem path" % label) from exc
    if not os.path.isfile(value):
        raise RasterExtractionError("%s is not a regular file: %s" % (label, value))
    if is_link_or_reparse(value):
        raise RasterSecurityError("%s must not be a link/junction/reparse point" % label)
    return value


def _read_bounded(path, limit, label):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise RasterExtractionError("cannot stat %s: %s" % (label, exc)) from exc
    if size < 1:
        raise RasterCorruptError("%s is empty" % label)
    if size > limit:
        raise RasterSecurityError("%s exceeds the %d-byte limit" % (label, limit))
    try:
        with open(path, "rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise RasterExtractionError("cannot read %s: %s" % (label, exc)) from exc
    if len(payload) != size or len(payload) > limit:
        raise RasterSecurityError("%s changed while being read" % label)
    return payload


def _png_dimensions(payload):
    if len(payload) < 33 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RasterCorruptError("PNG signature/header is missing or truncated")
    if payload[12:16] != b"IHDR" or struct.unpack(">I", payload[8:12])[0] != 13:
        raise RasterCorruptError("PNG first chunk is not a valid IHDR")
    width, height = struct.unpack(">II", payload[16:24])
    offset = 8
    saw_header = False
    saw_end = False
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        chunk_type = payload[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise RasterCorruptError("PNG contains a truncated chunk")
        chunk_data = payload[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length:end])[0]
        import zlib
        if zlib.crc32(chunk_type + chunk_data) & 0xffffffff != expected_crc:
            raise RasterCorruptError("PNG chunk checksum is invalid")
        if not saw_header:
            if chunk_type != b"IHDR" or length != 13:
                raise RasterCorruptError("PNG is missing a valid IHDR chunk")
            saw_header = True
        if chunk_type == b"acTL":
            if length != 8:
                raise RasterCorruptError("PNG acTL animation chunk is malformed")
            frame_count = struct.unpack(">I", chunk_data[:4])[0]
            if frame_count < 1:
                raise RasterCorruptError("PNG acTL animation frame count is invalid")
            raise RasterUnsupportedError(
                "animated PNG/APNG is not supported as one raster page-equivalent"
            )
        if chunk_type in (b"fcTL", b"fdAT"):
            raise RasterUnsupportedError(
                "animated PNG/APNG frame chunks are not supported as one raster page-equivalent"
            )
        if chunk_type == b"IEND":
            if length != 0 or end != len(payload):
                raise RasterCorruptError("PNG IEND is invalid or has trailing bytes")
            saw_end = True
            break
        offset = end
    if not saw_header or not saw_end:
        raise RasterCorruptError("PNG is incomplete")
    return width, height


def _jpeg_dimensions(payload):
    if len(payload) < 4 or not payload.startswith(b"\xff\xd8"):
        raise RasterCorruptError("JPEG SOI signature is missing")
    if not payload.endswith(b"\xff\xd9"):
        raise RasterCorruptError("JPEG EOI marker is missing")
    sof_markers = frozenset((
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    ))
    offset = 2
    dimensions = None
    while offset < len(payload) - 2:
        if payload[offset] != 0xFF:
            raise RasterCorruptError("JPEG marker stream is malformed before scan data")
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            raise RasterCorruptError("JPEG marker is truncated")
        marker = payload[offset]
        offset += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(payload):
            raise RasterCorruptError("JPEG segment length is truncated")
        length = struct.unpack(">H", payload[offset:offset + 2])[0]
        if length < 2 or offset + length > len(payload):
            raise RasterCorruptError("JPEG segment length is invalid")
        if marker in sof_markers:
            if length < 8:
                raise RasterCorruptError("JPEG SOF segment is truncated")
            height, width = struct.unpack(">HH", payload[offset + 3:offset + 7])
            dimensions = (width, height)
        if marker == 0xDA:
            break
        offset += length
    if dimensions is None:
        raise RasterCorruptError("JPEG has no supported SOF dimensions")
    return dimensions


def _gif_dimensions(payload):
    if len(payload) < 14 or payload[:6] not in (b"GIF87a", b"GIF89a"):
        raise RasterCorruptError("GIF signature/header is missing or truncated")
    width, height = struct.unpack("<HH", payload[6:10])
    packed = payload[10]
    offset = 13
    if packed & 0x80:
        offset += 3 * (1 << ((packed & 0x07) + 1))
    if offset > len(payload):
        raise RasterCorruptError("GIF global color table is truncated")

    def skip_sub_blocks(start):
        while True:
            if start >= len(payload):
                raise RasterCorruptError("GIF data sub-block chain is truncated")
            size = payload[start]
            start += 1
            if size == 0:
                return start
            end = start + size
            if end > len(payload):
                raise RasterCorruptError("GIF data sub-block is truncated")
            start = end

    image_count = 0
    while offset < len(payload):
        marker = payload[offset]
        offset += 1
        if marker == 0x3B:  # trailer
            if offset != len(payload):
                raise RasterCorruptError("GIF has trailing bytes after its trailer")
            if image_count < 1:
                raise RasterCorruptError("GIF contains no image descriptor")
            return width, height
        if marker == 0x21:  # extension; every extension payload is a sub-block chain
            if offset >= len(payload):
                raise RasterCorruptError("GIF extension label is truncated")
            offset += 1
            offset = skip_sub_blocks(offset)
            continue
        if marker != 0x2C:
            raise RasterCorruptError("GIF block stream contains an unknown marker")
        image_count += 1
        if image_count > 1:
            raise RasterUnsupportedError(
                "animated/multi-frame GIF is not supported as one raster page-equivalent"
            )
        if offset + 9 > len(payload):
            raise RasterCorruptError("GIF image descriptor is truncated")
        descriptor_packed = payload[offset + 8]
        frame_width, frame_height = struct.unpack("<HH", payload[offset + 4:offset + 8])
        if frame_width < 1 or frame_height < 1:
            raise RasterCorruptError("GIF image descriptor dimensions are invalid")
        offset += 9
        if descriptor_packed & 0x80:
            offset += 3 * (1 << ((descriptor_packed & 0x07) + 1))
            if offset > len(payload):
                raise RasterCorruptError("GIF local color table is truncated")
        if offset >= len(payload):
            raise RasterCorruptError("GIF image data is missing its LZW code size")
        offset += 1
        offset = skip_sub_blocks(offset)
    raise RasterCorruptError("GIF trailer is missing")


def _bmp_dimensions(payload):
    if len(payload) < 26 or not payload.startswith(b"BM"):
        raise RasterCorruptError("BMP signature/header is missing or truncated")
    declared_size = struct.unpack("<I", payload[2:6])[0]
    if declared_size and declared_size != len(payload):
        raise RasterCorruptError("BMP declared file size does not match available bytes")
    pixel_offset = struct.unpack("<I", payload[10:14])[0]
    dib_size = struct.unpack("<I", payload[14:18])[0]
    if dib_size == 12:
        if len(payload) < 26:
            raise RasterCorruptError("BMP core header is truncated")
        width, height = struct.unpack("<HH", payload[18:22])
        planes = struct.unpack("<H", payload[22:24])[0]
        bits_per_pixel = struct.unpack("<H", payload[24:26])[0]
        compression = 0
    elif dib_size >= 40:
        if len(payload) < 14 + dib_size:
            raise RasterCorruptError("BMP DIB header is truncated")
        width, signed_height = struct.unpack("<ii", payload[18:26])
        height = abs(signed_height)
        planes = struct.unpack("<H", payload[26:28])[0]
        bits_per_pixel = struct.unpack("<H", payload[28:30])[0]
        compression = struct.unpack("<I", payload[30:34])[0]
    else:
        raise RasterUnsupportedError("unsupported BMP DIB header size: %d" % dib_size)
    if planes != 1:
        raise RasterCorruptError("BMP planes value must be 1")
    if bits_per_pixel not in (1, 4, 8, 16, 24, 32):
        raise RasterUnsupportedError("unsupported BMP bit depth: %d" % bits_per_pixel)
    if compression != 0:
        raise RasterUnsupportedError("compressed BMP payloads are not supported")
    if pixel_offset < 14 + dib_size or pixel_offset > len(payload):
        raise RasterCorruptError("BMP pixel offset is invalid")
    row_bytes = ((abs(width) * bits_per_pixel + 31) // 32) * 4
    if pixel_offset + row_bytes * height > len(payload):
        raise RasterCorruptError("BMP pixel data is truncated")
    return width, height


def _tiff_number(payload, endian, value_type, count, value_field):
    sizes = {3: 2, 4: 4}
    if value_type not in sizes or count != 1:
        raise RasterUnsupportedError("TIFF dimension tag uses an unsupported field type/count")
    size = sizes[value_type]
    if size <= 4:
        raw = value_field[:size]
    else:  # retained for clarity if supported TIFF scalar types grow later
        offset = struct.unpack(endian + "I", value_field)[0]
        if offset + size > len(payload):
            raise RasterCorruptError("TIFF tag value offset is outside the file")
        raw = payload[offset:offset + size]
    return struct.unpack(endian + ("H" if value_type == 3 else "I"), raw)[0]


def _tiff_dimensions(payload):
    if len(payload) < 8 or payload[:4] not in (b"II*\x00", b"MM\x00*"):
        raise RasterCorruptError("TIFF signature/header is missing or unsupported")
    endian = "<" if payload[:2] == b"II" else ">"
    offset = struct.unpack(endian + "I", payload[4:8])[0]
    if offset < 8 or offset + 2 > len(payload):
        raise RasterCorruptError("TIFF first IFD offset is invalid")
    count = struct.unpack(endian + "H", payload[offset:offset + 2])[0]
    if count > 4096 or offset + 2 + count * 12 + 4 > len(payload):
        raise RasterCorruptError("TIFF IFD is truncated or unreasonably large")
    dimensions = {}
    has_sub_ifds = False
    for index in range(count):
        start = offset + 2 + index * 12
        tag, value_type, value_count = struct.unpack(endian + "HHI", payload[start:start + 8])
        if tag in (256, 257):
            dimensions[tag] = _tiff_number(
                payload, endian, value_type, value_count, payload[start + 8:start + 12]
            )
        elif tag == 330 and value_count:
            has_sub_ifds = True
    if 256 not in dimensions or 257 not in dimensions:
        raise RasterCorruptError("TIFF first image is missing width/height tags")
    next_ifd_position = offset + 2 + count * 12
    next_ifd = struct.unpack(
        endian + "I", payload[next_ifd_position:next_ifd_position + 4]
    )[0]
    if next_ifd:
        if next_ifd < 8 or next_ifd + 2 > len(payload):
            raise RasterCorruptError("TIFF next-IFD offset is outside the file")
        raise RasterUnsupportedError(
            "multi-page TIFF is not supported as one raster page-equivalent"
        )
    if has_sub_ifds:
        raise RasterUnsupportedError(
            "multi-image TIFF SubIFDs are not supported as one raster page-equivalent"
        )
    return dimensions[256], dimensions[257]


def _webp_dimensions(payload):
    if len(payload) < 20 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        raise RasterCorruptError("WebP RIFF header is missing or truncated")
    declared = struct.unpack("<I", payload[4:8])[0] + 8
    if declared != len(payload):
        raise RasterCorruptError("WebP RIFF size does not match the file")
    offset = 12
    dimensions = None
    image_chunks = 0
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise RasterCorruptError("WebP chunk header is truncated")
        chunk = payload[offset:offset + 4]
        length = struct.unpack("<I", payload[offset + 4:offset + 8])[0]
        data_start = offset + 8
        data_end = data_start + length
        next_offset = data_end + (length & 1)
        if data_end > len(payload) or next_offset > len(payload):
            raise RasterCorruptError("WebP chunk payload is truncated")
        data = payload[data_start:data_end]
        if chunk in (b"ANIM", b"ANMF"):
            raise RasterUnsupportedError(
                "animated/multi-frame WebP is not supported as one raster page-equivalent"
            )
        if chunk == b"VP8X":
            if len(data) != 10:
                raise RasterCorruptError("WebP VP8X header has an invalid size")
            if data[0] & 0x02:
                raise RasterUnsupportedError(
                    "animated/multi-frame WebP is not supported as one raster page-equivalent"
                )
            if dimensions is not None:
                raise RasterCorruptError("WebP contains duplicate image headers")
            dimensions = (
                int.from_bytes(data[4:7], "little") + 1,
                int.from_bytes(data[7:10], "little") + 1,
            )
        elif chunk == b"VP8L":
            image_chunks += 1
            if image_chunks > 1:
                raise RasterUnsupportedError(
                    "multi-image WebP is not supported as one raster page-equivalent"
                )
            if len(data) < 5 or data[0] != 0x2F:
                raise RasterCorruptError("WebP VP8L header is invalid")
            if dimensions is None:
                bits = int.from_bytes(data[1:5], "little")
                dimensions = (
                    (bits & 0x3FFF) + 1,
                    ((bits >> 14) & 0x3FFF) + 1,
                )
        elif chunk == b"VP8 ":
            image_chunks += 1
            if image_chunks > 1:
                raise RasterUnsupportedError(
                    "multi-image WebP is not supported as one raster page-equivalent"
                )
            if len(data) < 10 or data[3:6] != b"\x9d\x01\x2a":
                raise RasterCorruptError("WebP VP8 frame header is invalid")
            if dimensions is None:
                dimensions = (
                    struct.unpack("<H", data[6:8])[0] & 0x3FFF,
                    struct.unpack("<H", data[8:10])[0] & 0x3FFF,
                )
        offset = next_offset
    if dimensions is None:
        raise RasterUnsupportedError("WebP contains no supported image header")
    return dimensions


def _format_and_dimensions(payload):
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", _png_dimensions(payload)
    if payload.startswith(b"\xff\xd8"):
        return "jpeg", _jpeg_dimensions(payload)
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "gif", _gif_dimensions(payload)
    if payload.startswith(b"BM"):
        return "bmp", _bmp_dimensions(payload)
    if payload.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff", _tiff_dimensions(payload)
    if payload.startswith(b"RIFF"):
        return "webp", _webp_dimensions(payload)
    raise RasterUnsupportedError("unrecognized raster image signature")


def _validate_dimensions(width, height):
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise RasterCorruptError("raster dimensions must be positive integers")
    if width > MAX_RASTER_DIMENSION or height > MAX_RASTER_DIMENSION:
        raise RasterSecurityError("raster dimension exceeds the safety limit")
    if width * height > MAX_RASTER_PIXELS:
        raise RasterSecurityError("raster pixel count exceeds the safety limit")
    if width * height * 4 > MAX_RASTER_DECODED_BYTES:
        raise RasterSecurityError("raster decoded-memory estimate exceeds the safety limit")


def inspect_raster_payload(payload, extension=None):
    """Inspect bounded raster bytes without decoding pixel buffers."""

    if not isinstance(payload, bytes) or not payload:
        raise RasterCorruptError("raster payload must be non-empty bytes")
    if len(payload) > MAX_RASTER_BYTES:
        raise RasterSecurityError("raster payload exceeds the byte safety limit")
    if extension is not None:
        if not isinstance(extension, str):
            raise RasterUnsupportedError("raster extension must be text")
        extension = extension.lower()
        if extension not in _EXTENSIONS:
            raise RasterUnsupportedError(
                "unsupported raster extension: %s" % (extension or "(none)")
            )
    image_format, dimensions = _format_and_dimensions(payload)
    if extension is not None and _EXTENSIONS[extension] != image_format:
        raise RasterCorruptError(
            "raster extension %s does not match %s bytes" % (extension, image_format)
        )
    width, height = dimensions
    _validate_dimensions(width, height)
    info = RasterInfo(
        format=image_format,
        media_type=_MIME_TYPES[image_format],
        width=width,
        height=height,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return info


def _load_raster(path, expected_sha256=None):
    filesystem_path = _safe_regular_file(path, "raster source")
    extension = os.path.splitext(filesystem_path)[1].lower()
    payload = _read_bounded(filesystem_path, MAX_RASTER_BYTES, "raster source")
    info = inspect_raster_payload(payload, extension=extension)
    if expected_sha256 is not None:
        if (not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256)):
            raise RasterExtractionError(
                "expected_sha256 must be a lowercase SHA-256 digest"
            )
        if info.sha256 != expected_sha256:
            raise RasterSecurityError(
                "raster source revision does not match expected_sha256"
            )
    return filesystem_path, payload, info


def inspect_raster(path):
    """Return signature-derived image dimensions and a content digest."""

    unused_path, unused_payload, info = _load_raster(path)
    return info


def _sidecar_candidates(image_path):
    stem, unused_extension = os.path.splitext(image_path)
    return (
        stem + ".ocr.txt",
        image_path + ".txt",
    )


def _load_sidecar(
    image_path, sidecar_path, auto_sidecar, sidecar_source_file,
    expected_sidecar_sha256,
):
    discovery = None
    if sidecar_path is not None:
        chosen = _safe_regular_file(sidecar_path, "raster sidecar")
        discovery = "explicit"
    elif auto_sidecar:
        candidates = [path for path in _sidecar_candidates(image_path) if os.path.isfile(path)]
        # A linked candidate is still a candidate and must fail visibly rather
        # than being silently ignored in favor of another file.
        if len(candidates) > 1:
            raise RasterExtractionError(
                "multiple raster sidecars are present; pass sidecar_path explicitly: %s"
                % ", ".join(os.path.basename(path) for path in candidates)
            )
        chosen = _safe_regular_file(candidates[0], "raster sidecar") if candidates else None
        discovery = "automatic" if chosen is not None else None
    else:
        chosen = None
    if chosen is None:
        if sidecar_source_file is not None or expected_sidecar_sha256 is not None:
            raise RasterExtractionError(
                "sidecar provenance was supplied but no raster sidecar was selected"
            )
        return None, None, None
    if sidecar_source_file is None:
        raise RasterExtractionError(
            "a raster sidecar requires sidecar_source_file so it remains a first-class source"
        )
    try:
        canonical_sidecar = normalize_workspace_path(sidecar_source_file)
    except (TypeError, ValueError) as exc:
        raise RasterExtractionError(
            "sidecar_source_file must be a canonical relative path"
        ) from exc
    payload = _read_bounded(chosen, MAX_SIDECAR_BYTES, "raster sidecar")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RasterCorruptError("raster sidecar is not strict UTF-8") from exc
    if "\x00" in text:
        raise RasterCorruptError("raster sidecar contains NUL")
    sidecar_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sidecar_sha256 is not None:
        if (not isinstance(expected_sidecar_sha256, str)
                or len(expected_sidecar_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sidecar_sha256)):
            raise RasterExtractionError(
                "expected_sidecar_sha256 must be a lowercase SHA-256 digest"
            )
        if sidecar_sha256 != expected_sidecar_sha256:
            raise RasterSecurityError(
                "raster sidecar revision does not match expected_sidecar_sha256"
            )
    metadata = {
        "source_file": canonical_sidecar,
        "sha256": sidecar_sha256,
        "byte_size": len(payload),
        "discovery": discovery,
    }
    return text, metadata, chosen


def _verify_current_digest(path, expected, label):
    payload = _read_bounded(
        path,
        MAX_SIDECAR_BYTES if label == "raster sidecar" else MAX_RASTER_BYTES,
        label,
    )
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RasterSecurityError("%s changed during extraction" % label)


def extract_raster(
    path, source_file, asset_root=None, sidecar_path=None, auto_sidecar=False,
    sidecar_source_file=None, expected_sha256=None, expected_sidecar_sha256=None,
):
    """Extract one standalone image as one normalized page-equivalent record."""

    try:
        canonical_file = normalize_workspace_path(source_file)
    except (TypeError, ValueError) as exc:
        raise RasterExtractionError("source_file must be a canonical relative path") from exc
    if type(auto_sidecar) is not bool:
        raise RasterExtractionError("auto_sidecar must be a boolean")
    filesystem_path, payload, info = _load_raster(path, expected_sha256=expected_sha256)
    sidecar_text, sidecar_metadata, unused_sidecar_path = _load_sidecar(
        filesystem_path, sidecar_path, auto_sidecar, sidecar_source_file,
        expected_sidecar_sha256,
    )
    try:
        writer = _AssetWriter(asset_root, canonical_file)
    except OOXMLSecurityError as exc:
        raise RasterSecurityError(str(exc)) from exc
    except OOXMLExtractionError as exc:
        raise RasterExtractionError("invalid raster asset output: %s" % exc) from exc
    try:
        asset = writer.save(os.path.basename(filesystem_path), payload)
        review_signals = []
        if asset is None:
            review_signals.append({
                "reason_code": "raster_asset_not_materialized",
                "detail": "standalone raster was inspected but no asset_root was supplied",
            })
        if sidecar_text is None or not sidecar_text.strip():
            review_signals.append({
                "reason_code": "standalone_raster_needs_ocr",
                "detail": (
                    "standalone raster has no non-empty UTF-8 sidecar; route it to an "
                    "installed local OCR/vision adapter or typed agent review"
                ),
            })
        figure = {
            "kind": "figure",
            "text": "Standalone raster source (%d x %d)" % (info.width, info.height),
            "ordinal": 0,
            "bbox": [0.0, 0.0, float(info.width), float(info.height)],
            "method": "native",
            "confidence": 1.0,
            "metadata": {
                "format": info.format,
                "media_type": info.media_type,
                "width": info.width,
                "height": info.height,
                "page_equivalent": True,
            },
        }
        if asset is not None:
            figure.update({
                "asset": asset,
                "asset_role": "source_page",
                "asset_sha256": info.sha256,
            })
        # Sidecar text is deliberately not copied into the raster source record.
        # The builder ingests that text file under its own SourceRecord/receipt;
        # this metadata is only an exact cross-source revision link.
        elements = [figure]
        page_text = ""
        quality = assess_page({
            "page": 1,
            "text": sidecar_text or "",
            "image_count": 1,
            "image_area_ratio": 1.0,
            "vector_count": 0,
            "multi_column_hint": False,
            "table_hint": False,
            "formula_hint": False,
        })
        record = {
            "file": canonical_file,
            "page": 1,
            "text": page_text,
            "elements": elements,
            "embedded_assets": [asset] if asset is not None else [],
            "review_signals": review_signals,
            "quality_signals": quality,
            "metadata": {
                "format": "standalone_raster",
                "page_equivalent": "image",
                "raster": info.to_dict(),
                "sidecar": sidecar_metadata,
            },
        }
        _verify_current_digest(filesystem_path, info.sha256, "raster source")
        if sidecar_metadata is not None:
            _verify_current_digest(
                unused_sidecar_path, sidecar_metadata["sha256"], "raster sidecar"
            )
        return validate_page_records([record])
    except RasterExtractionError:
        writer.rollback()
        raise
    except OOXMLSecurityError as exc:
        writer.rollback()
        raise RasterSecurityError(str(exc)) from exc
    except OOXMLUnsupportedError as exc:
        writer.rollback()
        raise RasterUnsupportedError(str(exc)) from exc
    except OOXMLExtractionError as exc:
        writer.rollback()
        raise RasterExtractionError(str(exc)) from exc
    except OSError as exc:
        writer.rollback()
        raise RasterExtractionError("raster extraction failed: %s" % exc) from exc


__all__ = [
    "MAX_RASTER_BYTES", "MAX_RASTER_DECODED_BYTES", "MAX_RASTER_DIMENSION",
    "MAX_RASTER_PIXELS",
    "MAX_SIDECAR_BYTES", "RasterCorruptError", "RasterExtractionError",
    "RasterInfo", "RasterSecurityError", "RasterUnsupportedError",
    "extract_raster", "inspect_raster", "inspect_raster_payload",
]
