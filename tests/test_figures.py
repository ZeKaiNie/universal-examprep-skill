# -*- coding: utf-8 -*-
import os
import shutil
import struct
import tempfile
import unittest
import zipfile
import zlib

from coach import figures
from coach.extract import Source
from tests.helpers import make_pdf

HAS_PDFIUM = figures.has_pdfium()


def read_png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


class GeometryTest(unittest.TestCase):
    def test_cluster_merges_touching_boxes(self):
        boxes = [(0, 0, 10, 10), (12, 0, 20, 10), (100, 100, 110, 110)]
        self.assertEqual(sorted(figures.cluster(boxes, gap=5)), [(0, 0, 20, 10), (100, 100, 110, 110)])

    def test_png_bytes_roundtrip(self):
        w, h = 3, 2
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255] * 2)
        data = figures.png_bytes(w, h, 3, w * 3, rgb)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (w, h))
        idat = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[idat - 8: idat - 4])[0]
        raw = zlib.decompress(data[idat: idat + length])
        self.assertEqual(raw, b"\x00" + rgb[:9] + b"\x00" + rgb[9:])


class OfficeMediaTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pptx_pictures_are_copied_per_slide(self):
        path = os.path.join(self.dir, "deck.pptx")
        png = figures.png_bytes(1, 1, 3, 3, b"\x00\x00\x00")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", '<p:sld><p:pic><a:blip r:embed="rId2"/></p:pic></p:sld>')
            zf.writestr("ppt/slides/_rels/slide1.xml.rels",
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId2" Target="../media/image1.png"/></Relationships>')
            zf.writestr("ppt/media/image1.png", png)
            zf.writestr("ppt/slides/slide2.xml", "<p:sld/>")
        out = os.path.join(self.dir, "figs")
        os.makedirs(out)
        recs = figures.extract_office_media(path, out, "deck")
        self.assertEqual([(r["page"], r["path"]) for r in recs], [(1, "figures/deck_p1_1.png")])
        self.assertTrue(os.path.exists(os.path.join(out, "deck_p1_1.png")))


@unittest.skipUnless(HAS_PDFIUM, "pypdfium2 not installed")
class PdfFigureTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ecc-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_render_page_and_region(self):
        pdf = os.path.join(self.dir, "l.pdf")
        make_pdf(pdf, ["Lecture 1: Intro"])
        out = os.path.join(self.dir, "page.png")
        w, h = figures.render_region(pdf, 1, None, out, scale=1.0)
        self.assertEqual((w, h), (612, 792))
        self.assertEqual(read_png_size(out), (612, 792))
        w, h = figures.render_region(pdf, 1, (100, 600, 300, 750), out, scale=1.0)
        self.assertEqual((w, h), (200 + 2 * figures.MARGIN, 150 + 2 * figures.MARGIN))

    def test_text_only_pdf_has_no_figures_and_no_scan(self):
        pdf = os.path.join(self.dir, "l.pdf")
        make_pdf(pdf, ["Only text here", "Second page"])
        infos = figures.analyze_pdf(pdf)
        self.assertEqual(sorted(infos), [1, 2])
        self.assertEqual(infos[1].figures, [])
        self.assertFalse(infos[1].scan)
        self.assertEqual(figures.find_text_top(infos[2], "Second page") is not None, True)

    def test_extract_figures_driver(self):
        pdf = os.path.join(self.dir, "l.pdf")
        make_pdf(pdf, ["Only text here"])
        src = Source(pdf, "l.pdf", "lecture")
        ws = os.path.join(self.dir, "ws")
        os.makedirs(ws)
        records, scans, infos = figures.extract_figures(self.dir, [src], ws)
        self.assertEqual(records, [])
        self.assertEqual(scans, {})
        self.assertIn("l.pdf", infos)


if __name__ == "__main__":
    unittest.main()
