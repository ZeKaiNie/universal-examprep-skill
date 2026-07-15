# -*- coding: utf-8 -*-
"""Real, stdlib-generated workbook tests for the XLSX adapter."""

import base64
import hashlib
import os
import struct
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

from scripts.ingestion import xlsx as X


PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdWorkbook" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>
  <sheet name="Data" sheetId="7" r:id="rIdData"/>
  <sheet name="Hidden Answers" sheetId="2" state="veryHidden" r:id="rIdHidden"/>
 </sheets>
</workbook>"""

WORKBOOK_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdHidden" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
 <Relationship Id="rIdStrings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
 <Relationship Id="rIdData" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

SHARED_STRINGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">
 <si><r><t>Course</t></r><r><t> score</t></r></si>
</sst>"""

SHEET1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Points</t></is></c></row>
  <row r="2"><c r="A2"><v>2</v></c><c r="B2" s="3"><f>SUM(A2,3)</f><v>5</v></c></row>
 </sheetData>
 <mergeCells count="1"><mergeCell ref="C1:D1"/></mergeCells>
 <tableParts count="1"><tablePart r:id="rIdTable"/></tableParts>
 <drawing r:id="rIdDrawing"/>
</worksheet>"""

SHEET2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Instructor key</t></is></c></row></sheetData>
</worksheet>"""

SHEET1_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdDrawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>
 <Relationship Id="rIdTable" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>
</Relationships>"""

TABLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 id="1" name="Scores" displayName="Scores" ref="A1:B2" totalsRowShown="0">
 <autoFilter ref="A1:B2"/>
 <tableColumns count="2"><tableColumn id="1" name="Course score"/><tableColumn id="2" name="Points"/></tableColumns>
</table>"""

DRAWING = b"""<?xml version="1.0" encoding="UTF-8"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <xdr:twoCellAnchor>
  <xdr:from><xdr:col>0</xdr:col><xdr:row>0</xdr:row></xdr:from>
  <xdr:to><xdr:col>1</xdr:col><xdr:row>2</xdr:row></xdr:to>
  <xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Score chart" descr="Project-authored score chart"/></xdr:nvPicPr>
   <xdr:blipFill><a:blip r:embed="rIdImage"/></xdr:blipFill></xdr:pic>
 </xdr:twoCellAnchor>
</xdr:wsDr>"""

DRAWING_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
</Relationships>"""


def _parts():
    return {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "xl/workbook.xml": WORKBOOK,
        "xl/_rels/workbook.xml.rels": WORKBOOK_RELS,
        "xl/sharedStrings.xml": SHARED_STRINGS,
        "xl/worksheets/sheet1.xml": SHEET1,
        "xl/worksheets/sheet2.xml": SHEET2,
        "xl/worksheets/_rels/sheet1.xml.rels": SHEET1_RELS,
        "xl/tables/table1.xml": TABLE,
        "xl/drawings/drawing1.xml": DRAWING,
        "xl/drawings/_rels/drawing1.xml.rels": DRAWING_RELS,
        "xl/media/image1.png": PNG,
    }


def _write_package(path, parts):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(parts):
            archive.writestr(name, parts[name])


def _large_valid_monochrome_png(width=12000, height=12000):
    compressor = zlib.compressobj(9)
    row = b"\x00" + b"\x00" * ((width + 7) // 8)
    compressed = []
    for unused in range(height):
        block = compressor.compress(row)
        if block:
            compressed.append(block)
    compressed.append(compressor.flush())

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
        + chunk(b"IDAT", b"".join(compressed))
        + chunk(b"IEND", b"")
    )


class XLSXAdapterTest(unittest.TestCase):
    def test_extracts_workbook_order_cells_formula_table_and_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "scores.xlsx")
            assets = os.path.join(temp, "assets")
            _write_package(path, _parts())

            records = X.extract_xlsx(path, "materials/scores.xlsx", assets)
            self.assertEqual([record["page"] for record in records], [1, 2])
            self.assertEqual([record["metadata"]["sheet_name"] for record in records],
                             ["Data", "Hidden Answers"])
            first = records[0]
            self.assertIn("Course score", first["text"])
            sparse_table = next(
                element for element in first["elements"]
                if element["kind"] == "table"
                and element["metadata"].get("representation") == "sparse_coordinate_value_tsv"
            )
            cells = sparse_table["metadata"]["cells"]
            self.assertEqual([cell["coordinate"] for cell in cells], ["A1", "B1", "A2", "B2"])
            self.assertEqual(cells[0]["value"], "Course score")
            self.assertEqual(cells[-1]["formula"], "SUM(A2,3)")
            self.assertEqual(cells[-1]["raw_value"], "5")
            formula = next(element for element in first["elements"]
                           if element["kind"] == "formula")
            self.assertEqual(formula["text"], "=SUM(A2,3)")
            defined = [element for element in first["elements"]
                       if element["kind"] == "table"
                       and "defined_table" in element["metadata"]]
            self.assertEqual(defined[0]["metadata"]["defined_table"]["range"]["ref"], "A1:B2")
            figure = next(element for element in first["elements"]
                          if element["kind"] == "figure")
            self.assertEqual(figure["metadata"]["from_cell"], "A1")
            self.assertEqual(figure["metadata"]["to_cell"], "B3")
            self.assertEqual(figure["asset_sha256"], hashlib.sha256(PNG).hexdigest())
            self.assertEqual(first["embedded_assets"], [figure["asset"]])
            with open(os.path.join(assets, figure["asset"]), "rb") as stream:
                self.assertEqual(stream.read(), PNG)
            self.assertEqual(first["metadata"]["merged_ranges"], ["C1:D1"])
            self.assertEqual(first["quality_signals"]["route"], "recover")

            second = records[1]
            self.assertEqual(second["metadata"]["sheet_state"], "veryhidden")
            self.assertEqual(
                [signal["reason_code"] for signal in second["review_signals"]],
                ["xlsx_hidden_sheet"],
            )

            self.assertEqual(X.extract_xlsx(path, "materials/scores.xlsx", assets), records)
            self.assertFalse(any(name.startswith(".ooxml-") for name in os.listdir(assets)))

    def test_no_asset_root_keeps_image_and_typed_review_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "scores.xlsx")
            _write_package(path, _parts())
            records = X.extract_xlsx(path, "scores.xlsx")
            figure = next(element for element in records[0]["elements"]
                          if element["kind"] == "figure")
            self.assertNotIn("asset", figure)
            self.assertNotIn("asset_role", figure)
            self.assertNotIn("asset_sha256", figure)
            self.assertEqual(records[0]["embedded_assets"], [])
            self.assertIn(
                "xlsx_asset_not_materialized",
                [signal["reason_code"] for signal in records[0]["review_signals"]],
            )

    def test_formula_without_cached_value_is_not_presented_as_a_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "formula.xlsx")
            parts = _parts()
            parts["xl/worksheets/sheet1.xml"] = SHEET1.replace(
                b"<f>SUM(A2,3)</f><v>5</v>", b"<f>SUM(A2,3)</f>"
            )
            _write_package(path, parts)
            records = X.extract_xlsx(path, "formula.xlsx")
            formula = next(element for element in records[0]["elements"]
                           if element["kind"] == "formula")
            self.assertEqual(formula["metadata"]["raw_value"], "")
            self.assertIn(
                "xlsx_formula_without_cached_value",
                [signal["reason_code"] for signal in records[0]["review_signals"]],
            )

    def test_bad_shared_string_index_and_cell_bound_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "bad.xlsx")
            parts = _parts()
            parts["xl/worksheets/sheet1.xml"] = SHEET1.replace(b"<v>0</v>", b"<v>99</v>", 1)
            _write_package(path, parts)
            with self.assertRaises(X.XLSXCorruptError):
                X.extract_xlsx(path, "bad.xlsx")

            _write_package(path, _parts())
            with mock.patch.object(X, "MAX_CELLS_PER_SHEET", 1):
                with self.assertRaises(X.XLSXCorruptError):
                    X.extract_xlsx(path, "bad.xlsx")

    def test_reused_shared_strings_obey_aggregate_expanded_text_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "expanded.xlsx")
            parts = _parts()
            parts["xl/worksheets/sheet1.xml"] = b"""<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData><row r="1">
  <c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c>
  <c r="C1" t="s"><v>0</v></c><c r="D1" t="s"><v>0</v></c>
 </row></sheetData>
</worksheet>"""
            parts["xl/worksheets/_rels/sheet1.xml.rels"] = (
                b"<?xml version=\"1.0\"?><Relationships "
                b"xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>"
            )
            _write_package(path, parts)
            with mock.patch.object(X, "MAX_EXPANDED_TEXT_PER_SHEET", 30):
                with self.assertRaisesRegex(X.XLSXCorruptError, "expanded text"):
                    X.extract_xlsx(path, "expanded.xlsx")

    def test_relationship_traversal_external_image_and_wrong_extension_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "unsafe.xlsx")
            parts = _parts()
            parts["xl/_rels/workbook.xml.rels"] = WORKBOOK_RELS.replace(
                b'Target="worksheets/sheet1.xml"', b'Target="../../outside.xml"'
            )
            _write_package(path, parts)
            with self.assertRaises(X.XLSXSecurityError):
                X.extract_xlsx(path, "unsafe.xlsx")

            parts = _parts()
            parts["xl/drawings/_rels/drawing1.xml.rels"] = DRAWING_RELS.replace(
                b'Target="../media/image1.png"',
                b'Target="https://example.invalid/image.png" TargetMode="External"',
            )
            _write_package(path, parts)
            with self.assertRaises(X.XLSXSecurityError):
                X.extract_xlsx(path, "unsafe.xlsx")

            wrong = os.path.join(temp, "unsafe.xlsm")
            _write_package(wrong, _parts())
            with self.assertRaises(X.XLSXUnsupportedError):
                X.extract_xlsx(wrong, "unsafe.xlsm")

    def test_source_link_is_rejected_when_platform_can_create_it(self):
        with tempfile.TemporaryDirectory() as temp:
            target = os.path.join(temp, "target.xlsx")
            link = os.path.join(temp, "link.xlsx")
            _write_package(target, _parts())
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(X.XLSXSecurityError):
                X.extract_xlsx(link, "link.xlsx")

    def test_expected_source_revision_rejects_aba_swap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "scores.xlsx")
            _write_package(path, _parts())
            with open(path, "rb") as stream:
                expected = hashlib.sha256(stream.read()).hexdigest()
            replacement = _parts()
            replacement["xl/worksheets/sheet1.xml"] = SHEET1.replace(b"<v>2</v>", b"<v>9</v>")
            _write_package(path, replacement)
            with self.assertRaises(X.XLSXSecurityError):
                X.extract_xlsx(path, "scores.xlsx", expected_sha256=expected)

    def test_embedded_raster_uses_standalone_decoded_resource_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "pixel-bomb.xlsx")
            assets = os.path.join(temp, "assets")
            parts = _parts()
            parts["xl/media/image1.png"] = _large_valid_monochrome_png()
            _write_package(path, parts)

            records = X.extract_xlsx(path, "pixel-bomb.xlsx", assets)
            reasons = {
                signal["reason_code"]
                for signal in records[0]["review_signals"]
            }
            self.assertIn("xlsx_unsafe_asset", reasons)
            self.assertIn("xlsx_asset_not_materialized", reasons)
            figure = next(
                element for element in records[0]["elements"]
                if element["kind"] == "figure"
            )
            self.assertNotIn("asset", figure)
            self.assertFalse(os.path.exists(assets))


if __name__ == "__main__":
    unittest.main()
