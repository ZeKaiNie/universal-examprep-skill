# -*- coding: utf-8 -*-
"""Real, stdlib-generated DOCX/PPTX fixtures for the lightweight OOXML adapter."""

import base64
import hashlib
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from ingestion import ooxml as O  # noqa: E402


CONTENT_TYPES_DOCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

CONTENT_TYPES_PPTX = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
 <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
 <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
 <Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>
</Types>"""

ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""

PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_package(path, parts):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)


def _docx_parts(image_target="media/image1.png", target_mode=None, extra_parts=None):
    mode = (" TargetMode=\"%s\"" % target_mode) if target_mode else ""
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Core Ideas</w:t></w:r></w:p>
  <w:p><w:r><w:t>Plain paragraph.</w:t></w:r></w:p>
  <w:tbl>
   <w:tr><w:tc><w:p><w:r><w:t>A1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B1</w:t></w:r></w:p></w:tc></w:tr>
   <w:tr><w:tc><w:p><w:r><w:t>A2</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B2</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
  <w:p><w:r><w:drawing><wp:docPr id="1" name="Diagram" descr="Course diagram"/><a:blip r:embed="rIdImage"/></w:drawing></w:r></w:p>
  <w:p><w:r><w:t>Page one ending.</w:t><w:br w:type="page"/><w:t>Page two text.</w:t></w:r></w:p>
 </w:body>
</w:document>"""
    styles = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
</w:styles>"""
    relationships = ("""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="%s"%s/>
</Relationships>""" % (image_target, mode)).encode("utf-8")
    parts = {
        "[Content_Types].xml": CONTENT_TYPES_DOCX,
        "_rels/.rels": ROOT_RELS,
        "word/document.xml": document,
        "word/styles.xml": styles,
        "word/_rels/document.xml.rels": relationships,
        "word/media/image1.png": PNG,
    }
    parts.update(extra_parts or {})
    return parts


def _ppt_slide(text, title=False, image=False):
    placeholder = '<p:ph type="title"/>' if title else ""
    picture = """
  <p:pic><p:nvPicPr><p:cNvPr id="5" name="Picture" descr="Slide chart"/></p:nvPicPr>
   <p:blipFill><a:blip r:embed="rIdImage"/></p:blipFill></p:pic>""" if image else ""
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:cSld><p:spTree>
  <p:sp><p:nvSpPr><p:nvPr>%s</p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>
  %s
 </p:spTree></p:cSld>
</p:sld>""" % (placeholder, text, picture)).encode("utf-8")


def _pptx_parts():
    presentation = b"""<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:sldIdLst><p:sldId id="256" r:id="rIdSecond"/><p:sldId id="257" r:id="rIdFirst"/></p:sldIdLst>
</p:presentation>"""
    presentation_rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdFirst" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
 <Relationship Id="rIdSecond" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>"""
    slide1_rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
 <Relationship Id="rIdNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
</Relationships>"""
    notes = b"""<?xml version="1.0" encoding="UTF-8"?>
<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
 <p:txBody><a:p><a:r><a:t>Remember the speaker note.</a:t></a:r></a:p></p:txBody>
 </p:sp></p:spTree></p:cSld>
</p:notes>"""
    return {
        "[Content_Types].xml": CONTENT_TYPES_PPTX,
        "_rels/.rels": ROOT_RELS,
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": presentation_rels,
        "ppt/slides/slide1.xml": _ppt_slide("Ordered second", title=True, image=True),
        "ppt/slides/_rels/slide1.xml.rels": slide1_rels,
        "ppt/slides/slide2.xml": _ppt_slide("Ordered first"),
        "ppt/notesSlides/notesSlide1.xml": notes,
        "ppt/media/image1.png": PNG,
    }


class OOXMLAdapter(unittest.TestCase):
    def test_docx_extracts_heading_paragraph_table_pages_and_image_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            assets = os.path.join(temp, "assets")
            _write_package(path, _docx_parts())

            records = O.extract_ooxml(path, "materials/course.docx", assets)
            self.assertEqual([record["page"] for record in records], [1, 2])
            first = records[0]
            self.assertEqual(first["file"], "materials/course.docx")
            kinds = [element["kind"] for element in first["elements"]]
            self.assertEqual(kinds, ["heading", "text", "table", "figure", "text"])
            self.assertEqual(first["elements"][0]["text"], "Core Ideas")
            self.assertEqual(first["elements"][2]["text"], "A1\tB1\nA2\tB2")
            self.assertEqual(first["elements"][3]["text"], "Course diagram")
            self.assertTrue(all(element["bbox"] is None for record in records
                                for element in record["elements"]))
            self.assertEqual(
                [element["ordinal"] for record in records for element in record["elements"]],
                list(range(len(first["elements"]))) + list(range(len(records[1]["elements"]))),
            )
            self.assertEqual(records[1]["text"], "Page two text.")
            self.assertEqual(len(first["embedded_assets"]), 1)
            asset_name = first["embedded_assets"][0]
            self.assertEqual(first["elements"][3]["asset"], asset_name)
            self.assertEqual(
                hashlib.sha256(PNG).hexdigest(),
                first["elements"][3]["asset_sha256"],
            )
            with open(os.path.join(assets, asset_name), "rb") as stream:
                self.assertEqual(stream.read(), PNG)

            # Same source produces the same name/content and never leaves a partial temp file.
            again = O.extract_ooxml(path, "materials/course.docx", assets)
            self.assertEqual(again, records)
            self.assertFalse(any(name.startswith(".ooxml-") for name in os.listdir(assets)))

    def test_repeated_image_relationship_reads_and_materializes_binary_part_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "repeated.docx")
            assets = os.path.join(temp, "assets")
            parts = _docx_parts()
            image_paragraph = (
                b'<w:p><w:r><w:drawing><wp:docPr id="1" name="Diagram" '
                b'descr="Course diagram"/><a:blip r:embed="rIdImage"/>'
                b'</w:drawing></w:r></w:p>'
            )
            parts["word/document.xml"] = parts["word/document.xml"].replace(
                image_paragraph, image_paragraph + image_paragraph
            )
            _write_package(path, parts)

            original_read = O._Package.read
            read_counts = {}

            def counted_read(package, name):
                read_counts[name] = read_counts.get(name, 0) + 1
                return original_read(package, name)

            with mock.patch.object(O._Package, "read", counted_read):
                records = O.extract_ooxml(path, "repeated.docx", assets)

            self.assertEqual(1, read_counts.get("word/media/image1.png"))
            figures = [
                element for record in records for element in record["elements"]
                if element["kind"] == "figure"
            ]
            self.assertEqual(2, len(figures))
            self.assertEqual(figures[0]["asset"], figures[1]["asset"])
            self.assertEqual(1, len(records[0]["embedded_assets"]))
            self.assertEqual([records[0]["embedded_assets"][0]], os.listdir(assets))

    def test_pptx_honors_presentation_order_notes_and_slide_image_relationship(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "deck.pptx")
            assets = os.path.join(temp, "assets")
            _write_package(path, _pptx_parts())

            records = O.extract_ooxml(path, "slides/deck.pptx", assets)
            self.assertEqual([record["page"] for record in records], [1, 2])
            self.assertIn("Ordered first", records[0]["text"])
            self.assertNotIn("Ordered second", records[0]["text"])
            self.assertIn("Ordered second", records[1]["text"])
            self.assertNotIn("Remember the speaker note.", records[1]["text"])
            kinds = [element["kind"] for element in records[1]["elements"]]
            self.assertIn("heading", kinds)
            self.assertIn("figure", kinds)
            self.assertIn("speaker_notes", kinds)
            notes = [element for element in records[1]["elements"]
                     if element["kind"] == "speaker_notes"]
            self.assertEqual([element["text"] for element in notes],
                             ["Remember the speaker note."])
            self.assertEqual(len(records[1]["embedded_assets"]), 1)
            self.assertTrue(os.path.isfile(os.path.join(assets, records[1]["embedded_assets"][0])))

    def test_no_asset_root_keeps_figure_signal_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            _write_package(path, _docx_parts())
            records = O.extract_ooxml(path, "course.docx")
            figures = [element for record in records for element in record["elements"]
                       if element["kind"] == "figure"]
            self.assertEqual(len(figures), 1)
            self.assertIsNone(figures[0]["asset"])
            self.assertTrue(all(record["embedded_assets"] == [] for record in records))
            self.assertEqual(sorted(os.listdir(temp)), ["course.docx"])

    def test_docx_content_control_and_alternate_fallback_are_not_lost_or_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "controls.docx")
            parts = _docx_parts()
            document = parts["word/document.xml"]
            document = document.replace(
                b'<w:p><w:r><w:t>Plain paragraph.</w:t></w:r></w:p>',
                b'<w:sdt><w:sdtContent><w:p><w:r><w:t>Controlled text.</w:t></w:r></w:p>'
                b'</w:sdtContent></w:sdt>'
                b'<w:customXml><w:p><w:r><w:t>Custom XML text.</w:t></w:r></w:p></w:customXml>'
                b'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                b'<mc:Choice Requires="w14"><w:p><w:r><w:t>CHOICE_ONLY</w:t></w:r></w:p></mc:Choice>'
                b'<mc:Fallback><w:p><w:r><w:t>FALLBACK_ONLY</w:t></w:r></w:p></mc:Fallback>'
                b'</mc:AlternateContent>',
            )
            parts["word/document.xml"] = document
            _write_package(path, parts)
            records = O.extract_ooxml(path, "controls.docx")
            text = "\n".join(record["text"] for record in records)
            self.assertIn("Controlled text.", text)
            self.assertIn("Custom XML text.", text)
            self.assertIn("FALLBACK_ONLY", text)
            self.assertNotIn("CHOICE_ONLY", text)
            self.assertTrue(any(
                signal["reason_code"] == "ooxml_alternate_content_review"
                for record in records for signal in record["review_signals"]
            ))

    def test_pptx_hidden_and_animated_content_is_marked_and_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "hidden.pptx")
            assets = os.path.join(temp, "assets")
            parts = _pptx_parts()
            parts["ppt/slides/slide2.xml"] = parts["ppt/slides/slide2.xml"].replace(
                b'<p:sld xmlns:p=', b'<p:sld show="0" xmlns:p=', 1
            ).replace(b'</p:sld>', b'<p:timing/></p:sld>')
            parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace(
                b'<p:cNvPr id="5" name="Picture" descr="Slide chart"/>',
                b'<p:cNvPr id="5" name="Picture" descr="Slide chart" hidden="1"/>',
            )
            _write_package(path, parts)

            records = O.extract_ooxml(path, "slides/hidden.pptx", assets)
            hidden_slide = records[0]
            self.assertNotIn("Ordered first", hidden_slide["text"])
            self.assertIn(
                "Ordered first",
                [element["text"] for element in hidden_slide["elements"]
                 if element["kind"] == "speaker_notes"],
            )
            reasons = {signal["reason_code"] for signal in hidden_slide["review_signals"]}
            self.assertIn("ooxml_hidden_slide_answer_candidate", reasons)
            self.assertIn("ooxml_animation_order_review", reasons)
            hidden_figures = [
                element for element in records[1]["elements"]
                if element["kind"] == "figure" and element.get("asset")
            ]
            self.assertEqual("answer_context", hidden_figures[0]["asset_role"])
            self.assertIn(
                "ooxml_hidden_shape_answer_candidate",
                {signal["reason_code"] for signal in records[1]["review_signals"]},
            )

    def test_docx_image_after_page_break_is_attached_to_second_page(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "break.docx")
            assets = os.path.join(temp, "assets")
            parts = _docx_parts()
            document = parts["word/document.xml"]
            original = (b'<w:p><w:r><w:drawing><wp:docPr id="1" name="Diagram" '
                        b'descr="Course diagram"/><a:blip r:embed="rIdImage"/>'
                        b'</w:drawing></w:r></w:p>')
            document = document.replace(original, b"")
            document = document.replace(
                b'<w:p><w:r><w:t>Page one ending.</w:t><w:br w:type="page"/>'
                b'<w:t>Page two text.</w:t></w:r></w:p>',
                b'<w:p><w:r><w:t>Page one ending.</w:t><w:br w:type="page"/>'
                b'<w:t>Page two text.</w:t><w:drawing><wp:docPr id="1" name="Diagram" '
                b'descr="After break"/><a:blip r:embed="rIdImage"/>'
                b'</w:drawing></w:r></w:p>',
            )
            parts["word/document.xml"] = document
            _write_package(path, parts)
            records = O.extract_ooxml(path, "break.docx", assets)
            self.assertEqual([], records[0]["embedded_assets"])
            self.assertEqual(1, len(records[1]["embedded_assets"]))

    def test_invalid_png_is_reviewed_and_failed_package_rolls_back_prior_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            invalid_path = os.path.join(temp, "invalid.docx")
            invalid_assets = os.path.join(temp, "invalid-assets")
            invalid = _docx_parts()
            invalid["word/media/image1.png"] = b"\x89PNG\r\n\x1a\nshort"
            _write_package(invalid_path, invalid)
            records = O.extract_ooxml(invalid_path, "invalid.docx", invalid_assets)
            self.assertEqual([], records[0]["embedded_assets"])
            self.assertTrue(any(
                signal["reason_code"] == "ooxml_unsafe_asset"
                for record in records for signal in record["review_signals"]
            ))

            broken_path = os.path.join(temp, "broken.docx")
            rollback_assets = os.path.join(temp, "rollback-assets")
            broken = _docx_parts()
            broken["word/document.xml"] = broken["word/document.xml"].replace(
                b'</w:drawing></w:r></w:p>',
                b'</w:drawing></w:r></w:p><w:p><w:r><w:drawing>'
                b'<wp:docPr id="2" descr="Missing"/><a:blip r:embed="rIdMissing"/>'
                b'</w:drawing></w:r></w:p>',
                1,
            )
            broken["word/_rels/document.xml.rels"] = broken[
                "word/_rels/document.xml.rels"
            ].replace(
                b'</Relationships>',
                b'<Relationship Id="rIdMissing" Type="http://schemas.openxmlformats.org/'
                b'officeDocument/2006/relationships/image" Target="media/missing.png"/>'
                b'</Relationships>',
            )
            _write_package(broken_path, broken)
            with self.assertRaises(O.OOXMLCorruptError):
                O.extract_ooxml(broken_path, "broken.docx", rollback_assets)
            self.assertEqual([], os.listdir(rollback_assets))

    def test_external_and_traversing_image_relationships_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            cases = (
                ("https://attacker.invalid/image.png", "External"),
                ("../../../outside.png", None),
                ("..%2f..%2f..%2foutside.png", None),
            )
            for index, (target, mode) in enumerate(cases):
                with self.subTest(target=target):
                    path = os.path.join(temp, "bad%d.docx" % index)
                    _write_package(path, _docx_parts(target, mode))
                    with self.assertRaises(O.OOXMLSecurityError):
                        O.extract_ooxml(path, "bad.docx")

    def test_unsafe_zip_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "bad.docx")
            _write_package(path, _docx_parts(extra_parts={"../escape.bin": b"x"}))
            with self.assertRaises(O.OOXMLSecurityError):
                O.extract_ooxml(path, "bad.docx")

    def test_zip_bomb_entry_count_total_and_single_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            parts = _docx_parts()
            _write_package(path, parts)
            cases = (
                ("MAX_ZIP_ENTRIES", len(parts) - 1),
                ("MAX_TOTAL_UNCOMPRESSED", 32),
                ("MAX_SINGLE_UNCOMPRESSED", 16),
            )
            for constant, limit in cases:
                with self.subTest(limit=constant), mock.patch.object(O, constant, limit):
                    with self.assertRaises(O.OOXMLBombError):
                        O.extract_ooxml(path, "course.docx")

    def test_zip_resource_preflight_runs_before_zipfile_allocates_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            _write_package(path, _docx_parts())
            with mock.patch.object(O, "MAX_CENTRAL_DIRECTORY_BYTES", 1), mock.patch.object(
                O.zipfile, "ZipFile", side_effect=AssertionError("ZipFile must not run")
            ):
                with self.assertRaises(O.OOXMLBombError):
                    O.extract_ooxml(path, "course.docx")

            ratio_path = os.path.join(temp, "ratio.docx")
            parts = _docx_parts(extra_parts={"word/media/padding.bin": b"0" * (2 * 1024 * 1024)})
            _write_package(ratio_path, parts)
            with mock.patch.object(O, "MAX_ZIP_COMPRESSION_RATIO", 2), mock.patch.object(
                O.zipfile, "ZipFile", side_effect=AssertionError("ZipFile must not run")
            ):
                with self.assertRaises(O.OOXMLBombError):
                    O.extract_ooxml(ratio_path, "ratio.docx")

    def test_xml_tree_budgets_abort_during_streaming_parse(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            _write_package(path, _docx_parts())
            for constant, limit in (("MAX_XML_ELEMENTS", 1), ("MAX_XML_DEPTH", 1)):
                with self.subTest(constant=constant), mock.patch.object(O, constant, limit):
                    with self.assertRaises(O.OOXMLBombError):
                        O.extract_ooxml(path, "course.docx")

    def test_expected_source_revision_rejects_swapped_package(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "course.docx")
            _write_package(path, _docx_parts())
            with open(path, "rb") as stream:
                expected = hashlib.sha256(stream.read()).hexdigest()
            parts = _docx_parts()
            parts["word/document.xml"] = parts["word/document.xml"].replace(
                b"Plain paragraph.", b"Replacement paragraph."
            )
            _write_package(path, parts)
            with self.assertRaises(O.OOXMLSecurityError):
                O.extract_ooxml(path, "course.docx", expected_sha256=expected)

    def test_corrupt_encrypted_and_unsupported_inputs_fail_loud(self):
        with tempfile.TemporaryDirectory() as temp:
            corrupt = os.path.join(temp, "corrupt.docx")
            with open(corrupt, "wb") as stream:
                stream.write(b"not a zip")
            with self.assertRaises(O.OOXMLCorruptError):
                O.extract_ooxml(corrupt, "corrupt.docx")

            encrypted = os.path.join(temp, "encrypted.pptx")
            with open(encrypted, "wb") as stream:
                stream.write(O._OLE_MAGIC + b"encrypted payload")
            with self.assertRaises(O.OOXMLEncryptedError):
                O.extract_ooxml(encrypted, "encrypted.pptx")

            unsupported = os.path.join(temp, "sheet.xlsx")
            _write_package(unsupported, {"[Content_Types].xml": CONTENT_TYPES_DOCX})
            with self.assertRaises(O.OOXMLUnsupportedError):
                O.extract_ooxml(unsupported, "sheet.xlsx")


if __name__ == "__main__":
    unittest.main(verbosity=2)
