#!/usr/bin/env python3
"""Generate the redistribution-safe ingestion fixture pack (stdlib only)."""

import argparse
import hashlib
import io
import json
import os
import struct
import tempfile
import zipfile
import zlib


BASE = os.path.dirname(os.path.abspath(__file__))
GENERATED_FILES = (
    "layout.pdf",
    "scan.pdf",
    "scan.png",
    "shared_prompt_answer.pdf",
    "workbook.xlsx",
)


def _atomic_write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".gold-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _pdf_stream(payload, attributes=b""):
    separator = b" " if attributes else b""
    return (
        b"<< /Length " + str(len(payload)).encode("ascii") + separator + attributes
        + b" >>\nstream\n" + payload + b"\nendstream"
    )


def _build_pdf(objects):
    if sorted(objects) != list(range(1, max(objects) + 1)):
        raise ValueError("PDF object numbers must be contiguous")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number in range(1, max(objects) + 1):
        offsets.append(len(output))
        output.extend(("%d 0 obj\n" % number).encode("ascii"))
        output.extend(objects[number])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    output.extend(
        ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
         % (len(objects) + 1, xref)).encode("ascii")
    )
    return bytes(output)


def _pdf_literal(value):
    encoded = value.encode("ascii")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _text(x, y, size, value):
    return (
        b"BT /F1 " + str(size).encode("ascii") + b" Tf "
        + str(x).encode("ascii") + b" " + str(y).encode("ascii")
        + b" Td (" + _pdf_literal(value) + b") Tj ET\n"
    )


def _layout_pdf():
    page_one = b"".join((
        _text(72, 744, 18, "Layout Gold Set"),
        _text(72, 700, 12, "Left column A: alpha"),
        _text(72, 676, 12, "Left column B: beta"),
        _text(326, 700, 12, "Right column A: gamma"),
        _text(326, 676, 12, "Right column B: delta"),
    ))
    grid = b"".join((
        b"0.8 w\n",
        b"72 620 m 500 620 l S\n",
        b"72 580 m 500 580 l S\n",
        b"72 540 m 500 540 l S\n",
        b"72 540 m 72 620 l S\n",
        b"270 540 m 270 620 l S\n",
        b"500 540 m 500 620 l S\n",
    ))
    page_two = b"".join((
        _text(72, 744, 18, "Vector Table and Formula"),
        grid,
        _text(88, 595, 11, "Item"),
        _text(288, 595, 11, "Value"),
        _text(88, 555, 11, "sample"),
        _text(288, 555, 11, "7"),
        _text(72, 480, 14, "f(x) = x^2 + 1"),
    ))
    return _build_pdf({
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        4: _pdf_stream(page_one),
        5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        6: _pdf_stream(page_two),
        7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    })


def _scan_pixels():
    width, height = 16, 12
    return bytes(
        32 if (x // 4 + y // 3) % 2 == 0 else 224
        for y in range(height) for x in range(width)
    )


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _scan_png():
    width, height = 16, 12
    pixels = _scan_pixels()
    scanlines = b"".join(
        b"\x00" + pixels[row * width:(row + 1) * width]
        for row in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + _png_chunk(b"IEND", b"")
    )


def _scan_pdf():
    pixels = zlib.compress(_scan_pixels(), 9)
    image_attributes = (
        b"/Type /XObject /Subtype /Image /Width 16 /Height 12 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"
    )
    return _build_pdf({
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
        4: _pdf_stream(b"q 480 0 0 360 66 216 cm /Im1 Do Q\n"),
        5: _pdf_stream(pixels, image_attributes),
    })


def _shared_prompt_answer_pdf():
    content = b"".join((
        _text(72, 720, 17, "Question 1: Find the missing value."),
        _text(72, 690, 12, "Use the project-authored diagram below."),
        b"1 w 120 520 120 90 re S\n",
        b"120 565 m 240 565 l S\n",
        _text(145, 580, 11, "six groups"),
        _text(152, 540, 11, "seven each"),
        b"0.5 w 54 396 m 558 396 l S\n",
        _text(72, 280, 17, "Answer: 42 units."),
        _text(72, 250, 12, "Reason: six groups of seven."),
    ))
    return _build_pdf({
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        4: _pdf_stream(content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    })


def _zip_bytes(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, parts[name])
    return output.getvalue()


def _workbook_xlsx():
    parts = {
        "[Content_Types].xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdWorkbook" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Gold Data" sheetId="1" r:id="rIdSheet"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdSheet" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rIdStrings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>""",
        "xl/sharedStrings.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>Topic</t></si></sst>""",
        "xl/worksheets/sheet1.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Points</t></is></c></row>
  <row r="2"><c r="A2" t="inlineStr"><is><t>Layout</t></is></c><c r="B2"><f>2+3</f><v>5</v></c></row>
 </sheetData>
 <tableParts count="1"><tablePart r:id="rIdTable"/></tableParts>
</worksheet>""",
        "xl/worksheets/_rels/sheet1.xml.rels": b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdTable" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table1.xml"/>
</Relationships>""",
        "xl/tables/table1.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="1" name="GoldTable" displayName="GoldTable" ref="A1:B2" totalsRowShown="0">
 <tableColumns count="2"><tableColumn id="1" name="Topic"/><tableColumn id="2" name="Points"/></tableColumns>
</table>""",
    }
    return _zip_bytes(parts)


def _load_source():
    source_path = os.path.join(BASE, "source.json")
    with open(source_path, "rb") as stream:
        payload = stream.read()
    try:
        source = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("source.json is not valid strict UTF-8 JSON: %s" % exc)
    files = [artifact.get("file") for artifact in source.get("artifacts", [])]
    if len(files) != len(set(files)) or set(files) != set(GENERATED_FILES):
        raise SystemExit("source.json artifact list does not match the generator outputs")
    if source.get("license") != "CC0-1.0":
        raise SystemExit("source.json must retain the CC0-1.0 license declaration")
    return source, payload


def generate(output_directory):
    source, source_payload = _load_source()
    artifacts = {
        "layout.pdf": _layout_pdf(),
        "scan.pdf": _scan_pdf(),
        "scan.png": _scan_png(),
        "shared_prompt_answer.pdf": _shared_prompt_answer_pdf(),
        "workbook.xlsx": _workbook_xlsx(),
    }
    facts = {item["file"]: item["facts"] for item in source["artifacts"]}
    manifest_artifacts = []
    for filename in sorted(artifacts):
        payload = artifacts[filename]
        _atomic_write(os.path.join(output_directory, filename), payload)
        manifest_artifacts.append({
            "file": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
            "facts": facts[filename],
        })
    manifest = {
        "schema_version": 1,
        "generator": "generate.py",
        "source": "source.json",
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        "license": source["license"],
        "authorship": source["authorship"],
        "artifacts": manifest_artifacts,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_write(os.path.join(output_directory, "manifest.json"), manifest_payload)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=BASE,
        help="directory for generated binaries and manifest (default: fixture directory)",
    )
    args = parser.parse_args(argv)
    output = os.path.abspath(args.output)
    generate(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
