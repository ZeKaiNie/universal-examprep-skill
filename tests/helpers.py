# -*- coding: utf-8 -*-
"""Builders for tiny DOCX / PPTX / PDF fixtures so tests need no binary files."""
import zipfile

_CT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>%s</Types>'
)
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def make_docx(path, paragraphs):
    """paragraphs: list of str | ("h1", text) | "PAGEBREAK" | ("table", [[cells]])."""
    body = []
    for item in paragraphs:
        if item == "PAGEBREAK":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif isinstance(item, tuple) and item[0] == "table":
            rows = "".join(
                "<w:tr>%s</w:tr>" % "".join("<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % c for c in row)
                for row in item[1])
            body.append("<w:tbl>%s</w:tbl>" % rows)
        elif isinstance(item, tuple):
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading%s"/></w:pPr><w:r><w:t>%s</w:t></w:r></w:p>'
                        % (item[0][1:], item[1]))
        else:
            body.append("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % item)
    doc = '<?xml version="1.0" encoding="UTF-8"?><w:document %s><w:body>%s</w:body></w:document>' % (_W, "".join(body))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", _CT % '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>')
        zf.writestr("word/document.xml", doc)


def make_pptx(path, slides, notes=None):
    """slides: list of list-of-lines; notes: {slide_number: text}."""
    notes = notes or {}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", _CT % "")
        for i, lines in enumerate(slides, 1):
            paras = "".join("<a:p><a:r><a:t>%s</a:t></a:r></a:p>" % l for l in lines)
            zf.writestr("ppt/slides/slide%d.xml" % i,
                        '<?xml version="1.0"?><p:sld %s %s><p:cSld><p:spTree><p:sp><p:txBody>%s</p:txBody></p:sp></p:spTree></p:cSld></p:sld>' % (_P, _A, paras))
            if i in notes:
                zf.writestr("ppt/notesSlides/notesSlide%d.xml" % i,
                            '<?xml version="1.0"?><p:notes %s %s><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>' % (_P, _A, notes[i]))


def make_pdf(path, pages):
    """Minimal uncompressed PDF, one text line per page (ASCII only)."""
    objs = []
    n_pages = len(pages)
    kids = " ".join("%d 0 R" % (3 + 2 * i) for i in range(n_pages))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages))
    font_id = 3 + 2 * n_pages
    for i, text in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        stream = "BT /F1 12 Tf 50 700 Td (%s) Tj ET" % text.replace("(", "\\(").replace(")", "\\)")
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>" % (content_id, font_id))
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += "%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += "".join("%010d 00000 n \n" % o for o in offsets)
    out += "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as fh:
        fh.write(out.encode("latin-1"))
