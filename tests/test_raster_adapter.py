"""Signature, safety, provenance, and sidecar tests for raster ingestion."""

import base64
import hashlib
import os
import struct
import tempfile
import unittest
import zlib

from scripts.ingestion import raster as R


def _chunk(kind, payload):
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width=3, height=2):
    rows = b"".join(
        b"\x00" + bytes(((x + y * width) * 23 % 256 for x in range(width)))
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, 9))
        + _chunk(b"IEND", b"")
    )


def _jpeg(width=3, height=2):
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + struct.pack(">HH", height, width)
        + b"\x01\x01\x11\x00\xff\xd9"
    )


def _bmp(width=2, height=1):
    row_bytes = ((width * 24 + 31) // 32) * 4
    pixels = b"\x00\x00\xff" * width + b"\x00" * (row_bytes - width * 3)
    size = 54 + len(pixels)
    return (
        b"BM" + struct.pack("<IHHI", size, 0, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 0, 0, 0, 0)
        + pixels
    )


def _tiff(width=4, height=5):
    return (
        b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 2)
        + struct.pack("<HHI", 256, 4, 1) + struct.pack("<I", width)
        + struct.pack("<HHI", 257, 4, 1) + struct.pack("<I", height)
        + struct.pack("<I", 0)
    )


def _multi_page_tiff(width=4, height=5):
    first_ifd = (
        struct.pack("<H", 2)
        + struct.pack("<HHI", 256, 4, 1) + struct.pack("<I", width)
        + struct.pack("<HHI", 257, 4, 1) + struct.pack("<I", height)
        + struct.pack("<I", 38)
    )
    second_ifd = (
        struct.pack("<H", 2)
        + struct.pack("<HHI", 256, 4, 1) + struct.pack("<I", width)
        + struct.pack("<HHI", 257, 4, 1) + struct.pack("<I", height)
        + struct.pack("<I", 0)
    )
    return b"II*\x00" + struct.pack("<I", 8) + first_ifd + second_ifd


def _webp(width=4, height=5):
    data = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    total = b"WEBP" + b"VP8X" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(total)) + total


GIF = base64.b64decode(b"R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


def _animated_gif():
    image_block = GIF[GIF.index(b"\x2c"):-1]
    return GIF[:-1] + image_block + b";"


def _animated_webp(width=4, height=5):
    data = b"\x02\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (
        height - 1
    ).to_bytes(3, "little")
    total = b"WEBP" + b"VP8X" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(total)) + total


def _apng(width=3, height=2):
    static = _png(width, height)
    # The animation-control chunk is valid and sits at the mandated boundary
    # directly after IHDR.  Detection deliberately aborts before decoding frames.
    return static[:33] + _chunk(b"acTL", struct.pack(">II", 2, 0)) + static[33:]


class RasterAdapterTest(unittest.TestCase):
    def test_inspects_supported_signatures_without_image_library(self):
        fixtures = (
            ("image.png", _png(3, 2), "png", (3, 2)),
            ("image.jpg", _jpeg(3, 2), "jpeg", (3, 2)),
            ("image.gif", GIF, "gif", (1, 1)),
            ("image.bmp", _bmp(2, 1), "bmp", (2, 1)),
            ("image.tif", _tiff(4, 5), "tiff", (4, 5)),
            ("image.webp", _webp(4, 5), "webp", (4, 5)),
        )
        with tempfile.TemporaryDirectory() as temp:
            for filename, payload, expected_format, dimensions in fixtures:
                with self.subTest(filename=filename):
                    path = os.path.join(temp, filename)
                    with open(path, "wb") as stream:
                        stream.write(payload)
                    info = R.inspect_raster(path)
                    self.assertEqual(info.format, expected_format)
                    self.assertEqual((info.width, info.height), dimensions)
                    self.assertEqual(info.byte_size, len(payload))
                    self.assertEqual(info.sha256, hashlib.sha256(payload).hexdigest())
                    self.assertEqual(info.to_dict()["pixels"], dimensions[0] * dimensions[1])

    def test_multiframe_and_multipage_sources_fail_before_page_equivalent_output(self):
        fixtures = (
            ("animated.gif", _animated_gif()),
            ("animated.webp", _animated_webp()),
            ("multipage.tiff", _multi_page_tiff()),
            ("animated.png", _apng()),
        )
        with tempfile.TemporaryDirectory() as temp:
            for filename, payload in fixtures:
                with self.subTest(filename=filename):
                    path = os.path.join(temp, filename)
                    assets = os.path.join(temp, filename + "-assets")
                    with open(path, "wb") as stream:
                        stream.write(payload)
                    with self.assertRaises(R.RasterUnsupportedError):
                        R.extract_raster(
                            path, filename, asset_root=assets, auto_sidecar=False,
                        )
                    self.assertFalse(os.path.exists(assets))

    def test_extract_without_sidecar_never_reports_empty_text_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "scan.png")
            with open(path, "wb") as stream:
                stream.write(_png())

            records = R.extract_raster(path, "materials/scan.png", auto_sidecar=False)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["page"], 1)
            self.assertEqual(record["text"], "")
            self.assertEqual(record["metadata"]["page_equivalent"], "image")
            self.assertEqual(record["quality_signals"]["route"], "recover")
            self.assertIn("no_text", record["quality_signals"]["reason_codes"])
            self.assertEqual(
                [signal["reason_code"] for signal in record["review_signals"]],
                ["raster_asset_not_materialized", "standalone_raster_needs_ocr"],
            )
            self.assertEqual([element["kind"] for element in record["elements"]],
                             ["figure"])
            self.assertNotIn("asset", record["elements"][0])
            self.assertNotIn("asset_role", record["elements"][0])
            self.assertNotIn("asset_sha256", record["elements"][0])

    def test_explicitly_named_sidecar_stays_first_class_and_asset_copy_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "scan.png")
            sidecar_path = os.path.join(temp, "scan.ocr.txt")
            assets = os.path.join(temp, "assets")
            payload = _png(5, 4)
            sidecar_payload = b"Recovered local text.\nSecond line.\n"
            with open(path, "wb") as stream:
                stream.write(payload)
            with open(sidecar_path, "wb") as stream:
                stream.write(sidecar_payload)

            kwargs = {
                "auto_sidecar": True,
                "sidecar_source_file": "materials/scan.ocr.txt",
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "expected_sidecar_sha256": hashlib.sha256(sidecar_payload).hexdigest(),
            }
            first = R.extract_raster(path, "materials/scan.png", assets, **kwargs)
            second = R.extract_raster(path, "materials/scan.png", assets, **kwargs)
            self.assertEqual(first, second)
            record = first[0]
            self.assertEqual(record["text"], "")
            self.assertEqual([element["kind"] for element in record["elements"]], ["figure"])
            self.assertEqual(record["review_signals"], [])
            self.assertEqual(
                record["metadata"]["sidecar"]["source_file"],
                "materials/scan.ocr.txt",
            )
            self.assertEqual(record["metadata"]["sidecar"]["discovery"], "automatic")
            self.assertEqual(
                record["metadata"]["sidecar"]["sha256"],
                hashlib.sha256(sidecar_payload).hexdigest(),
            )
            asset = record["embedded_assets"][0]
            self.assertEqual(record["elements"][0]["asset"], asset)
            self.assertEqual(record["elements"][0]["asset_role"], "source_page")
            self.assertEqual(record["elements"][0]["asset_sha256"], hashlib.sha256(payload).hexdigest())
            with open(os.path.join(assets, asset), "rb") as stream:
                self.assertEqual(stream.read(), payload)
            self.assertFalse(any(name.startswith(".ooxml-") for name in os.listdir(assets)))

    def test_explicit_sidecar_records_provenance_and_blank_sidecar_still_routes_review(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "image.png")
            sidecar = os.path.join(temp, "manual.md")
            with open(path, "wb") as stream:
                stream.write(_png())
            with open(sidecar, "w", encoding="utf-8") as stream:
                stream.write("   \n")
            record = R.extract_raster(
                path, "image.png", sidecar_path=sidecar, auto_sidecar=False,
                sidecar_source_file="manual.md",
            )[0]
            self.assertEqual(record["metadata"]["sidecar"]["discovery"], "explicit")
            self.assertIn(
                "standalone_raster_needs_ocr",
                [signal["reason_code"] for signal in record["review_signals"]],
            )
            self.assertNotIn("text", [element["kind"] for element in record["elements"]])

    def test_ambiguous_and_non_utf8_sidecars_fail_loudly(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "image.png")
            with open(path, "wb") as stream:
                stream.write(_png())
            with open(os.path.join(temp, "image.ocr.txt"), "w", encoding="utf-8") as stream:
                stream.write("one")
            with open(os.path.join(temp, "image.png.txt"), "w", encoding="utf-8") as stream:
                stream.write("two")
            with self.assertRaises(R.RasterExtractionError):
                R.extract_raster(
                    path, "image.png", auto_sidecar=True,
                    sidecar_source_file="image.ocr.txt",
                )

            os.unlink(os.path.join(temp, "image.png.txt"))
            with open(os.path.join(temp, "image.ocr.txt"), "wb") as stream:
                stream.write(b"\xff\xfe")
            with self.assertRaises(R.RasterCorruptError):
                R.extract_raster(
                    path, "image.png", auto_sidecar=True,
                    sidecar_source_file="image.ocr.txt",
                )

    def test_expected_source_revision_rejects_aba_swap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "image.png")
            original = _png(3, 2)
            replacement = _png(4, 2)
            with open(path, "wb") as stream:
                stream.write(replacement)
            with self.assertRaises(R.RasterSecurityError):
                R.extract_raster(
                    path,
                    "image.png",
                    expected_sha256=hashlib.sha256(original).hexdigest(),
                )

    def test_mismatched_extension_truncation_and_excessive_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            mismatch = os.path.join(temp, "image.jpg")
            with open(mismatch, "wb") as stream:
                stream.write(_png())
            with self.assertRaises(R.RasterCorruptError):
                R.inspect_raster(mismatch)

            truncated = os.path.join(temp, "truncated.png")
            with open(truncated, "wb") as stream:
                stream.write(_png()[:-4])
            with self.assertRaises(R.RasterCorruptError):
                R.inspect_raster(truncated)

            huge = os.path.join(temp, "huge.png")
            with open(huge, "wb") as stream:
                stream.write(_png(R.MAX_RASTER_DIMENSION + 1, 1))
            with self.assertRaises(R.RasterSecurityError):
                R.inspect_raster(huge)

    def test_source_link_is_rejected_when_platform_can_create_it(self):
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, "target.png")
            link = os.path.join(temp, "link.png")
            with open(target, "wb") as stream:
                stream.write(_png())
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(R.RasterSecurityError):
                R.inspect_raster(link)

    def test_asset_root_link_is_rejected_by_hardened_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "image.png")
            real_assets = os.path.join(temp, "real-assets")
            linked_assets = os.path.join(temp, "linked-assets")
            os.mkdir(real_assets)
            with open(path, "wb") as stream:
                stream.write(_png())
            try:
                os.symlink(real_assets, linked_assets, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlink creation is unavailable")
            with self.assertRaises(R.RasterSecurityError):
                R.extract_raster(path, "image.png", linked_assets, auto_sidecar=False)


if __name__ == "__main__":
    unittest.main()
