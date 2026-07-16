# -*- coding: utf-8 -*-
import base64
import io
import os
import struct
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from image_validation import ImageValidationError, validate_image_blob  # noqa: E402

try:
    from PIL import Image as PillowImage
    PillowImage.init()
except Exception:  # pragma: no cover - the unavailable-decoder test covers this route
    PillowImage = None


# Produced by Pillow from Image.new("RGB", (1, 1), "white").save(..., "JPEG").
PILLOW_JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEG"
    "E1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RF"
    "RkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKj"
    "pKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP0"
    "9fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgEC"
    "BAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLR"
    "ChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
    "dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbH"
    "yMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iii"
    "gD//2Q=="
)


class SharedRasterValidation(unittest.TestCase):
    @unittest.skipUnless(PillowImage, "Pillow is not installed")
    def test_accepts_real_pillow_jpeg(self):
        self.assertEqual(
            validate_image_blob("image/jpeg", PILLOW_JPEG_1X1), (1, 1)
        )

    @unittest.skipUnless(PillowImage, "Pillow is not installed")
    def test_accepts_valid_gif_bmp_and_available_webp(self):
        formats = (("GIF", "image/gif"), ("BMP", "image/bmp"))
        if "WEBP" in PillowImage.SAVE:
            formats += (("WEBP", "image/webp"),)
        for image_format, mime in formats:
            with self.subTest(image_format=image_format):
                output = io.BytesIO()
                PillowImage.new("RGB", (3, 2), "white").save(
                    output, image_format
                )
                self.assertEqual(
                    validate_image_blob(mime, output.getvalue()), (3, 2)
                )

    @unittest.skipUnless(PillowImage, "Pillow is not installed")
    def test_rejects_jpeg_with_invalid_entropy_stream(self):
        start = PILLOW_JPEG_1X1.index(b"\xff\xda")
        header_length = struct.unpack(
            ">H", PILLOW_JPEG_1X1[start + 2:start + 4]
        )[0]
        malformed = (
            PILLOW_JPEG_1X1[:start + 2 + header_length]
            + b"\xff\xd8\xff\xd9"
        )
        with self.assertRaisesRegex(ImageValidationError, "pixel decode failed"):
            validate_image_blob("image/jpeg", malformed)

    @unittest.skipUnless(PillowImage, "Pillow is not installed")
    def test_rejects_gif_with_invalid_lzw_code_size(self):
        output = io.BytesIO()
        PillowImage.new("P", (16, 16), 0).save(output, "GIF")
        malformed = bytearray(output.getvalue())
        descriptor = malformed.index(0x2c)
        packed = malformed[descriptor + 9]
        code_size = descriptor + 10
        if packed & 0x80:
            code_size += 3 * (1 << ((packed & 0x07) + 1))
        malformed[code_size] = 0
        with self.assertRaisesRegex(ImageValidationError, "pixel decode failed"):
            validate_image_blob("image/gif", bytes(malformed))

    def test_fails_closed_when_pillow_decoder_is_unavailable(self):
        with mock.patch.dict(
                sys.modules, {"PIL": None, "PIL.Image": None}):
            with self.assertRaisesRegex(
                    ImageValidationError, "Pillow is required"):
                validate_image_blob("image/jpeg", PILLOW_JPEG_1X1)

    def test_rejects_malformed_standard_rasters(self):
        malformed_webp = (
            b"RIFF" + struct.pack("<I", 12) + b"WEBP"
            + b"VP8 " + struct.pack("<I", 4) + b"bad!"
        )
        cases = (
            ("image/jpeg", b"\xff\xd8\xff\xd9"),
            ("image/webp", malformed_webp),
            ("image/gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"),
            ("image/bmp", b"BM" + b"\x00" * 52),
        )
        for mime, payload in cases:
            with self.subTest(mime=mime):
                with self.assertRaises(ImageValidationError):
                    validate_image_blob(mime, payload)


class CIWorkflowContract(unittest.TestCase):
    def test_ci_installs_pinned_decoder_for_non_png_integration(self):
        workflow_path = os.path.join(ROOT, ".github", "workflows", "ci.yml")
        with open(workflow_path, encoding="utf-8") as handle:
            workflow = handle.read()
        self.assertIn("Pillow==10.4.0", workflow)
        self.assertIn("latex2mathml==3.60.0", workflow)


if __name__ == "__main__":
    unittest.main()
