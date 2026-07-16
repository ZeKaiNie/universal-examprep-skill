#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Validation for raster bytes used by printable artifacts.

PNG uses a strict stdlib decoder because it is the canonical local-renderer
output.  Non-PNG formats keep bounded structural inspection and conditionally
require an already-installed Pillow for full pixel decoding.
"""

import io
import struct
import warnings
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_DECODED_PNG_BYTES = 512 * 1024 * 1024


class ImageValidationError(ValueError):
    pass


def _png_scanline_layout(width, height, bits_per_pixel, interlace):
    if interlace == 0:
        passes = ((0, 0, 1, 1),)
    else:
        passes = (
            (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
            (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
            (0, 1, 1, 2),
        )
    size = 0
    row_groups = []
    for start_x, start_y, step_x, step_y in passes:
        pass_width = 0 if width <= start_x else \
            (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else \
            (height - start_y + step_y - 1) // step_y
        if not pass_width or not pass_height:
            continue
        row_size = 1 + (pass_width * bits_per_pixel + 7) // 8
        pass_size = row_size * pass_height
        if size + pass_size > MAX_DECODED_PNG_BYTES:
            raise ImageValidationError("decoded image exceeds the safety bound")
        row_groups.append((size, row_size, pass_height))
        size += pass_size
    return size, row_groups


def _validate_png_idat(chunks, width, height, depth, color_type, interlace):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    expected, row_groups = _png_scanline_layout(
        width, height, channels * depth, interlace
    )
    compressed = b"".join(chunks)
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(compressed, expected + 1)
        if decoder.unconsumed_tail:
            raise ImageValidationError(
                "IDAT expands beyond the declared image dimensions"
            )
        if len(decoded) <= expected:
            decoded += decoder.flush(expected + 1 - len(decoded))
    except zlib.error as exc:
        raise ImageValidationError(
            "IDAT is not a valid zlib stream: %s" % exc
        )
    if not decoder.eof:
        raise ImageValidationError("IDAT zlib stream is incomplete")
    if decoder.unused_data:
        raise ImageValidationError("IDAT contains bytes after its zlib stream")
    if len(decoded) != expected:
        raise ImageValidationError(
            "decoded scanline length %d does not match expected %d"
            % (len(decoded), expected)
        )
    view = memoryview(decoded)
    for start, row_size, row_count in row_groups:
        if any(value > 4 for value in view[
                start:start + row_size * row_count:row_size]):
            raise ImageValidationError(
                "decoded scanline has an invalid filter byte"
            )


def png_dimensions(payload):
    """Validate a complete PNG and return its declared ``(width, height)``."""

    if not isinstance(payload, (bytes, bytearray)):
        raise ImageValidationError("payload is not bytes")
    payload = bytes(payload)
    if not payload.startswith(PNG_SIGNATURE):
        raise ImageValidationError("missing PNG signature")

    cursor = len(PNG_SIGNATURE)
    chunk_index = 0
    seen_ihdr = False
    seen_plte = False
    seen_idat = False
    idat_closed = False
    idat_chunks = []
    width = height = depth = interlace = None
    color_type = None
    valid_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }

    while cursor < len(payload):
        if len(payload) - cursor < 12:
            raise ImageValidationError(
                "truncated chunk framing at byte %d" % cursor
            )
        length = struct.unpack(">I", payload[cursor:cursor + 4])[0]
        if length > 0x7fffffff:
            raise ImageValidationError(
                "chunk length exceeds the PNG limit at byte %d" % cursor
            )
        chunk_type = payload[cursor + 4:cursor + 8]
        if (len(chunk_type) != 4
                or any(value not in range(ord("A"), ord("Z") + 1)
                       and value not in range(ord("a"), ord("z") + 1)
                       for value in chunk_type)):
            raise ImageValidationError("invalid chunk type at byte %d" % cursor)
        if ord("a") <= chunk_type[2] <= ord("z"):
            raise ImageValidationError(
                "invalid reserved chunk-type bit at byte %d" % cursor
            )
        end = cursor + 12 + length
        if end > len(payload):
            raise ImageValidationError(
                "chunk overruns payload at byte %d" % cursor
            )
        data = payload[cursor + 8:cursor + 8 + length]
        expected_crc = struct.unpack(">I", payload[cursor + 8 + length:end])[0]
        actual_crc = zlib.crc32(chunk_type + data) & 0xffffffff
        if actual_crc != expected_crc:
            raise ImageValidationError(
                "CRC mismatch for chunk at byte %d" % cursor
            )

        name = chunk_type.decode("ascii")
        if chunk_index == 0 and name != "IHDR":
            raise ImageValidationError("IHDR is not the first chunk")
        if name == "IHDR":
            if seen_ihdr or chunk_index != 0 or length != 13:
                raise ImageValidationError("invalid or duplicate IHDR")
            width, height, depth, color_type, compression, filtering, interlace = \
                struct.unpack(">IIBBBBB", data)
            if not (1 <= width <= 0x7fffffff and 1 <= height <= 0x7fffffff):
                raise ImageValidationError("invalid IHDR dimensions")
            if color_type not in valid_depths or depth not in valid_depths[color_type]:
                raise ImageValidationError("invalid IHDR color type / bit depth")
            if compression != 0 or filtering != 0 or interlace not in (0, 1):
                raise ImageValidationError("unsupported or invalid IHDR methods")
            seen_ihdr = True
        elif not seen_ihdr:
            raise ImageValidationError("chunk appears before IHDR")
        elif name == "PLTE":
            if seen_plte or seen_idat or length == 0 or length % 3 or length > 768:
                raise ImageValidationError("invalid PLTE")
            if color_type in (0, 4):
                raise ImageValidationError("PLTE is forbidden for this color type")
            seen_plte = True
        elif name == "IDAT":
            if idat_closed:
                raise ImageValidationError("non-consecutive IDAT chunks")
            if color_type == 3 and not seen_plte:
                raise ImageValidationError(
                    "indexed-color PNG has no PLTE before IDAT"
                )
            seen_idat = True
            idat_chunks.append(data)
        elif name == "IEND":
            if length != 0 or not seen_idat or not any(idat_chunks):
                raise ImageValidationError("invalid IEND or empty IDAT stream")
            if end != len(payload):
                raise ImageValidationError("bytes appear after IEND")
            _validate_png_idat(
                idat_chunks, width, height, depth, color_type, interlace
            )
            return width, height
        else:
            if chunk_type[0] in range(ord("A"), ord("Z") + 1):
                raise ImageValidationError("unknown critical chunk %s" % name)
            if seen_idat:
                idat_closed = True

        if seen_idat and name not in ("IDAT", "IEND"):
            idat_closed = True
        cursor = end
        chunk_index += 1

    raise ImageValidationError("missing terminal IEND")


def png_validation_error(payload):
    try:
        png_dimensions(payload)
    except ImageValidationError as exc:
        return str(exc)
    return None


def is_valid_png_file(path):
    try:
        with open(path, "rb") as stream:
            png_dimensions(stream.read())
    except (OSError, ImageValidationError):
        return False
    return True


def _inspect_standard_raster(mime, blob):
    extensions = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/gif": ".gif", "image/webp": ".webp",
        "image/bmp": ".bmp", "image/x-ms-bmp": ".bmp",
    }
    try:
        try:
            from .ingestion.raster import RasterExtractionError, inspect_raster_payload
        except ImportError:
            from ingestion.raster import RasterExtractionError, inspect_raster_payload
        info = inspect_raster_payload(blob, extension=extensions[mime])
    except (KeyError, RasterExtractionError) as exc:
        raise ImageValidationError(str(exc))
    dimensions = (info.width, info.height)
    try:
        from PIL import Image
    except Exception as exc:
        raise ImageValidationError(
            "Pillow is required to decode non-PNG visual assets"
        ) from exc
    expected_format = info.format.upper()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(blob)) as image:
                if image.format != expected_format or image.size != dimensions:
                    raise ImageValidationError(
                        "decoder metadata does not match structural inspection"
                    )
                image.verify()
            with Image.open(io.BytesIO(blob)) as image:
                if image.format != expected_format or image.size != dimensions:
                    raise ImageValidationError(
                        "decoder metadata does not match structural inspection"
                    )
                image.load()
                decoded_dimensions = image.size
    except ImageValidationError:
        raise
    except Exception as exc:
        raise ImageValidationError(
            "%s pixel decode failed: %s" % (expected_format, exc)
        ) from exc
    if decoded_dimensions != dimensions:
        raise ImageValidationError(
            "decoded dimensions do not match structural inspection"
        )
    return decoded_dimensions


def validate_image_blob(mime, blob):
    """Validate supported raster bytes and return dimensions when available."""

    if not isinstance(blob, (bytes, bytearray)):
        raise ImageValidationError("image payload is not bytes")
    blob = bytes(blob)
    mime = str(mime or "").lower().split(";", 1)[0].strip()
    if mime == "image/png":
        return png_dimensions(blob)
    if mime in {
            "image/jpeg", "image/jpg", "image/gif", "image/webp",
            "image/bmp", "image/x-ms-bmp"}:
        return _inspect_standard_raster(mime, blob)
    raise ImageValidationError("unsupported image MIME: %s" % mime)
