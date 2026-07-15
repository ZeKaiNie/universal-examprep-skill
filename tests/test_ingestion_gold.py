"""Reproducibility and semantic facts for the synthetic ingestion Gold Set."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

from scripts.ingestion.raster import extract_raster, inspect_raster
from scripts.ingestion.xlsx import extract_xlsx


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "tests", "fixtures", "ingestion_gold")
GENERATOR = os.path.join(GOLD, "generate.py")
GENERATED = (
    "layout.pdf",
    "scan.pdf",
    "scan.png",
    "shared_prompt_answer.pdf",
    "workbook.xlsx",
    "manifest.json",
)


def _load_json(name):
    with open(os.path.join(GOLD, name), "r", encoding="utf-8") as stream:
        return json.load(stream)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class IngestionGoldSetTest(unittest.TestCase):
    def test_text_fixtures_use_canonical_lf_bytes(self):
        for filename in ("source.json", "manifest.json", "LICENSE", "generate.py"):
            with self.subTest(filename=filename), open(
                os.path.join(GOLD, filename), "rb"
            ) as stream:
                self.assertNotIn(b"\r", stream.read())

    def test_manifest_hashes_sizes_source_and_license(self):
        source = _load_json("source.json")
        manifest = _load_json("manifest.json")
        self.assertEqual(source["schema_version"], 1)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(source["license"], "CC0-1.0")
        self.assertEqual(manifest["license"], "CC0-1.0")
        self.assertFalse(source["authorship"]["contains_real_course_material"])
        self.assertFalse(source["authorship"]["contains_third_party_media"])
        self.assertFalse(source["authorship"]["contains_personal_data"])
        self.assertEqual(manifest["authorship"], source["authorship"])
        self.assertEqual(
            manifest["source_sha256"], _sha256(os.path.join(GOLD, "source.json"))
        )
        with open(os.path.join(GOLD, "LICENSE"), "r", encoding="utf-8") as stream:
            license_text = stream.read()
        self.assertIn("SPDX-License-Identifier: CC0-1.0", license_text)

        source_by_file = {item["file"]: item["facts"] for item in source["artifacts"]}
        manifest_files = []
        for artifact in manifest["artifacts"]:
            filename = artifact["file"]
            path = os.path.join(GOLD, filename)
            manifest_files.append(filename)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(artifact["sha256"], _sha256(path))
            self.assertEqual(artifact["byte_size"], os.path.getsize(path))
            self.assertEqual(artifact["facts"], source_by_file[filename])
            self.assertGreaterEqual(artifact["facts"]["page_count"], 1)
            self.assertIn("expected_units", artifact["facts"])
            self.assertIn("citations", artifact["facts"])
        self.assertEqual(manifest_files, sorted(source_by_file))

    def test_generator_reproduces_committed_artifacts_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run(
                [sys.executable, GENERATOR, "--output", temp],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(sorted(os.listdir(temp)), sorted(GENERATED))
            for filename in GENERATED:
                with self.subTest(filename=filename):
                    with open(os.path.join(GOLD, filename), "rb") as committed, open(
                        os.path.join(temp, filename), "rb"
                    ) as regenerated:
                        self.assertEqual(regenerated.read(), committed.read())

    def test_pdf_structures_cover_layout_scan_and_shared_crop_cases(self):
        manifest = _load_json("manifest.json")
        by_file = {item["file"]: item for item in manifest["artifacts"]}
        for filename in ("layout.pdf", "scan.pdf", "shared_prompt_answer.pdf"):
            with open(os.path.join(GOLD, filename), "rb") as stream:
                payload = stream.read()
            self.assertTrue(payload.startswith(b"%PDF-1.4\n"))
            self.assertTrue(payload.endswith(b"%%EOF\n"))
            page_count = len(re.findall(rb"/Type\s*/Page(?!s)\b", payload))
            self.assertEqual(page_count, by_file[filename]["facts"]["page_count"])

        with open(os.path.join(GOLD, "layout.pdf"), "rb") as stream:
            layout = stream.read()
        for page in by_file["layout.pdf"]["facts"]["pages"]:
            for quote in page["exact_quotes"]:
                encoded = quote.encode("ascii").replace(b"\\", b"\\\\").replace(
                    b"(", b"\\("
                ).replace(b")", b"\\)")
                self.assertIn(encoded, layout)

        with open(os.path.join(GOLD, "scan.pdf"), "rb") as stream:
            scan = stream.read()
        self.assertIn(b"/Subtype /Image", scan)
        self.assertNotIn(b"BT /F", scan)

        crop_facts = by_file["shared_prompt_answer.pdf"]["facts"]["crops"]
        self.assertEqual([crop["role"] for crop in crop_facts],
                         ["question_context", "answer_context"])
        question, answer = crop_facts
        self.assertGreaterEqual(question["bbox_pdf_points"][1], answer["bbox_pdf_points"][3])
        self.assertIn("Answer: 42 units.", question["must_exclude"])
        self.assertIn("Question 1: Find the missing value.", answer["must_exclude"])

    def test_raster_and_xlsx_fixtures_execute_through_stdlib_adapters(self):
        scan_path = os.path.join(GOLD, "scan.png")
        info = inspect_raster(scan_path)
        self.assertEqual((info.format, info.width, info.height), ("png", 16, 12))
        raster = extract_raster(
            scan_path, "gold/scan.png", auto_sidecar=False,
        )[0]
        self.assertEqual([element["kind"] for element in raster["elements"]], ["figure"])
        self.assertIn(
            "standalone_raster_needs_ocr",
            [signal["reason_code"] for signal in raster["review_signals"]],
        )

        workbook = extract_xlsx(
            os.path.join(GOLD, "workbook.xlsx"), "gold/workbook.xlsx",
        )
        self.assertEqual(len(workbook), 1)
        self.assertEqual(workbook[0]["metadata"]["sheet_name"], "Gold Data")
        formula = next(
            element for element in workbook[0]["elements"] if element["kind"] == "formula"
        )
        self.assertEqual(formula["text"], "=2+3")
        self.assertEqual(formula["metadata"]["raw_value"], "5")

    def test_xlsx_zip_metadata_is_fixed_and_contains_no_macro_parts(self):
        path = os.path.join(GOLD, "workbook.xlsx")
        with zipfile.ZipFile(path, "r") as archive:
            self.assertTrue(archive.infolist())
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0)
                                for info in archive.infolist()))
            self.assertFalse(any(name.lower().endswith(("vbaproject.bin", ".exe", ".dll"))
                                 for name in archive.namelist()))

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf is not installed")
    def test_optional_real_pdf_parser_confirms_pages_text_order_and_image_only_scan(self):
        from pypdf import PdfReader

        layout = PdfReader(os.path.join(GOLD, "layout.pdf"))
        self.assertEqual(len(layout.pages), 2)
        first_text = layout.pages[0].extract_text()
        order = _load_json("source.json")["artifacts"][0]["facts"]["pages"][0][
            "expected_reading_order"
        ]
        positions = [first_text.index(value) for value in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("f(x) = x^2 + 1", layout.pages[1].extract_text())

        scan = PdfReader(os.path.join(GOLD, "scan.pdf"))
        self.assertEqual(len(scan.pages), 1)
        self.assertEqual((scan.pages[0].extract_text() or "").strip(), "")

        shared = PdfReader(os.path.join(GOLD, "shared_prompt_answer.pdf"))
        shared_text = shared.pages[0].extract_text()
        self.assertIn("Question 1: Find the missing value.", shared_text)
        self.assertIn("Answer: 42 units.", shared_text)


if __name__ == "__main__":
    unittest.main()
