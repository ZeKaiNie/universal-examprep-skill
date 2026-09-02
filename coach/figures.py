# -*- coding: utf-8 -*-
"""Find and crop pictures from course files so the tutor can show them in chat.

PDF figures need the optional `pypdfium2` package: vector drawings and embedded images are
located from the page object list, clustered into regions, rendered and saved as PNG.
PPTX/DOCX embedded pictures are copied straight out of the zip (stdlib only).
Scanned pages (a picture covering most of the page) are flagged, never cropped as figures.
"""
import os
import re
import struct
import zipfile
import zlib
from collections import Counter

FIG_DIR = "figures"
MIN_W, MIN_H = 48, 36        # points; smaller drawings are rules, bullets, underlines
MARGIN = 8                   # points added around a region before cropping
GAP = 10                     # points; drawings closer than this merge into one figure
SCAN_COVER = 0.6             # image area / page area above which a page is a scan
MAX_PER_PAGE = 4
RENDER_SCALE = 2.0
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def has_pdfium():
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- geometry

def _clip(box, w, h):
    l, b, r, t = box
    return (max(0.0, l), max(0.0, b), min(w, r), min(h, t))


def _area(box):
    l, b, r, t = box
    return max(0.0, r - l) * max(0.0, t - b)


def _touch(a, b, gap):
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def cluster(boxes, gap=GAP):
    """Merge boxes that touch (within `gap`) until nothing changes."""
    boxes = [tuple(b) for b in boxes if _area(b) > 0]
    changed = True
    while changed:
        changed = False
        out = []
        for box in boxes:
            for i, other in enumerate(out):
                if _touch(box, other, gap):
                    out[i] = (min(box[0], other[0]), min(box[1], other[1]), max(box[2], other[2]), max(box[3], other[3]))
                    changed = True
                    break
            else:
                out.append(box)
        boxes = out
    return boxes


# ---------------------------------------------------------------- png

def png_bytes(width, height, channels, stride, buf):
    """Encode a raw RGB/RGBA bitmap (top-down rows) as PNG using only zlib."""
    color_type = 6 if channels == 4 else 2
    raw = bytearray()
    row_len = width * channels
    for y in range(height):
        start = y * stride
        raw.append(0)
        raw += buf[start:start + row_len]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


# ---------------------------------------------------------------- pdf analysis

class PdfPageInfo(object):
    __slots__ = ("number", "width", "height", "figures", "scan", "text_boxes")

    def __init__(self, number, width, height):
        self.number = number
        self.width = width
        self.height = height
        self.figures = []      # candidate figure boxes (l, b, r, t) in PDF points
        self.scan = False
        self.text_boxes = []   # (l, b, r, t, text)


def _page_bounds(obj):
    """Bounds of a page object in page space (objects nested in a Form XObject report local coords)."""
    l, b, r, t = obj.get_bounds()
    corners = [(l, b), (r, b), (l, t), (r, t)]
    cont = obj.container
    while cont is not None:
        a, bb, c, d, e, f = cont.get_matrix().get()
        corners = [(a * x + c * y + e, bb * x + d * y + f) for x, y in corners]
        cont = cont.container
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def analyze_pdf(path):
    """Return {page_number: PdfPageInfo} with figure regions and scan flags."""
    import pypdfium2 as pdfium
    from pypdfium2 import raw as R

    pdf = pdfium.PdfDocument(path)
    infos, raw_boxes = {}, {}
    for i in range(len(pdf)):
        page = pdf[i]
        w, h = page.get_size()
        info = PdfPageInfo(i + 1, w, h)
        images, drawings = [], []
        for obj in page.get_objects(max_depth=4):
            try:
                box = _clip(_page_bounds(obj), w, h)
            except Exception:
                continue
            if obj.type == R.FPDF_PAGEOBJ_IMAGE:
                images.append(box)
            elif obj.type in (R.FPDF_PAGEOBJ_PATH, R.FPDF_PAGEOBJ_SHADING):
                if _area(box) > 1:
                    drawings.append(box)
        page_area = w * h
        if images and sum(_area(b) for b in cluster(images, 2)) >= SCAN_COVER * page_area:
            info.scan = True
        tp = page.get_textpage()
        for k in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(k)
            info.text_boxes.append((l, b, r, t, tp.get_text_bounded(left=l, bottom=b, right=r, top=t)))
        raw_boxes[i + 1] = (images, drawings)
        infos[i + 1] = info

    # decorations: the same box on many pages (slide template lines, logos)
    seen = Counter()
    for images, drawings in raw_boxes.values():
        for box in set(tuple(round(v) for v in b) for b in images + drawings):
            seen[box] += 1
    threshold = max(3, len(infos) * 0.4)
    decor = {box for box, n in seen.items() if n >= threshold}

    for n, info in infos.items():
        if info.scan:
            continue
        images, drawings = raw_boxes[n]
        boxes = [b for b in images + drawings if tuple(round(v) for v in b) not in decor]
        regions = []
        page_area = info.width * info.height
        for box in cluster(boxes):
            if box[2] - box[0] >= MIN_W and box[3] - box[1] >= MIN_H and _area(box) < 0.95 * page_area:
                regions.append(box)
        regions.sort(key=_area, reverse=True)
        info.figures = regions[:MAX_PER_PAGE]
    return infos


def render_region(path, page_number, box, out_path, scale=RENDER_SCALE):
    """Crop `box` (l, b, r, t in PDF points, or None for the whole page) to a PNG file."""
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(path)[page_number - 1]
    w, h = page.get_size()
    if box is None:
        crop = (0, 0, 0, 0)
    else:
        l, b, r, t = _clip((box[0] - MARGIN, box[1] - MARGIN, box[2] + MARGIN, box[3] + MARGIN), w, h)
        crop = (l, b, w - r, h - t)
    bitmap = page.render(scale=scale, crop=crop, rev_byteorder=True)
    data = png_bytes(bitmap.width, bitmap.height, bitmap.n_channels, bitmap.stride, bytes(bitmap.buffer))
    with open(out_path, "wb") as fh:
        fh.write(data)
    return bitmap.width, bitmap.height


def find_text_top(info, needle):
    """Top y (PDF points) of the first text box whose text starts with `needle`.

    PDF text rectangles may split a heading ("Problem 4." | "[10 points] Trees"), so shorter
    prefixes are tried when the long one is not found.
    """
    flat = " ".join(needle.split()).lower()
    boxes = [(t, " ".join(text.split()).lower()) for l, b, r, t, text in info.text_boxes]
    for n in (40, 20, 10):
        key = flat[:n]
        if not key:
            continue
        for top, text in boxes:
            if text.startswith(key):
                return top
    return None


# ---------------------------------------------------------------- office media

def _zip_media(zf, rels_name, xml_name):
    """Ordered media paths referenced by a slide/document part."""
    if rels_name not in zf.namelist():
        return []
    import xml.etree.ElementTree as ET
    rels = {}
    for rel in ET.fromstring(zf.read(rels_name)):
        rels[rel.get("Id")] = rel.get("Target", "")
    order = re.findall(r'r:embed="([^"]+)"', zf.read(xml_name).decode("utf-8", "ignore"))
    base = os.path.dirname(xml_name)
    out = []
    for rid in order:
        target = rels.get(rid)
        if not target:
            continue
        full = os.path.normpath(os.path.join(base, target)).replace("\\", "/")
        if os.path.splitext(full)[1].lower() in MEDIA_EXT and full in zf.namelist():
            out.append(full)
    return out


def extract_office_media(path, out_dir, stem):
    """Copy pictures out of a PPTX (per slide) or DOCX (page 1). Returns figure records."""
    records = []
    ext = os.path.splitext(path)[1].lower()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if ext == ".pptx":
            slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.search(r"(\d+)", n).group(1)))
            parts = [(int(re.search(r"(\d+)", n).group(1)), n, "ppt/slides/_rels/%s.rels" % os.path.basename(n)) for n in slides]
        elif ext == ".docx":
            parts = [(1, "word/document.xml", "word/_rels/document.xml.rels")]
        else:
            return records
        for page, xml_name, rels_name in parts:
            for k, media in enumerate(_zip_media(zf, rels_name, xml_name), 1):
                out_name = "%s_p%d_%d%s" % (stem, page, k, os.path.splitext(media)[1].lower())
                with open(os.path.join(out_dir, out_name), "wb") as fh:
                    fh.write(zf.read(media))
                records.append({"page": page, "path": FIG_DIR + "/" + out_name, "kind": "figure", "box": None})
                if k >= MAX_PER_PAGE:
                    break
    return records


# ---------------------------------------------------------------- driver

def safe_stem(rel):
    stem = os.path.splitext(os.path.basename(rel))[0]
    return re.sub(r"[^\w\-]+", "_", stem).strip("_")[:40] or "file"


def extract_figures(materials, sources, ws):
    """Write figure PNGs under <ws>/figures and return (records, scan_pages, pdf_infos).

    records: [{"file", "page", "path", "kind", "box"}]; scan_pages: {rel: {page,...}};
    pdf_infos: {rel: {page: PdfPageInfo}} for later question crops.
    """
    out_dir = os.path.join(ws, FIG_DIR)
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, old))
    records, scan_pages, pdf_infos = [], {}, {}
    pdfium_ok = has_pdfium()
    for src in sources:
        if src.error or src.kind == "figure":
            continue
        ext = os.path.splitext(src.path)[1].lower()
        stem = safe_stem(src.rel)
        try:
            if ext in (".pptx", ".docx"):
                for rec in extract_office_media(src.path, out_dir, stem):
                    rec["file"] = src.rel
                    records.append(rec)
            elif ext == ".pdf" and pdfium_ok:
                infos = analyze_pdf(src.path)
                pdf_infos[src.rel] = infos
                scans = {n for n, info in infos.items() if info.scan}
                if scans:
                    scan_pages[src.rel] = scans
                for n, info in infos.items():
                    for k, box in enumerate(info.figures, 1):
                        name = "%s_p%d_%d.png" % (stem, n, k)
                        w, h = render_region(src.path, n, box, os.path.join(out_dir, name))
                        records.append({"file": src.rel, "page": n, "path": FIG_DIR + "/" + name, "kind": "figure",
                                        "box": [round(v) for v in box], "w": w, "h": h})
        except Exception as exc:  # one bad file must not stop setup
            src.warnings.append("figures_failed: %s" % exc)
    return records, scan_pages, pdf_infos


def crop_blocks(materials, ws, rel, infos, segments, stem, label):
    """Crop question/answer regions. segments: [(page, top_y, bottom_y)] in PDF points.

    Only segments that overlap a detected figure are cropped (plain text needs no picture).
    Returns a list of workspace-relative PNG paths.
    """
    out_dir = os.path.join(ws, FIG_DIR)
    paths = []
    path = os.path.join(materials, rel)
    for k, (page, top, bottom) in enumerate(segments, 1):
        info = infos.get(page)
        if info is None:
            continue
        region = (0 + 20, max(0.0, bottom), info.width - 20, min(info.height, top))
        if info.scan:
            # a scanned printed exam: the whole block is a picture. Crop it only when the OCR layer
            # shows real content below the label line (blank paper after a list is not a question).
            inside = [tb for tb in info.text_boxes if tb[1] >= region[1] - 1 and tb[3] <= region[3] + 1]
            if region[3] - region[1] < 60 or len(inside) < 3:
                continue
        elif not any(_touch(region, fig, 0) for fig in info.figures):
            continue
        else:
            # trim empty paper below the last text line / drawing inside the block
            content = [tb[1] for tb in info.text_boxes if tb[1] >= region[1] - 1 and tb[3] <= region[3] + 1]
            content += [fig[1] for fig in info.figures if fig[1] >= region[1] - 1 and fig[3] <= region[3] + 1]
            if content:
                region = (region[0], max(region[1], min(content) - 6), region[2], region[3])
        name = "%s_%s_%d.png" % (stem, label, k)
        try:
            render_region(path, page, region, os.path.join(out_dir, name))
        except Exception:
            continue
        paths.append(FIG_DIR + "/" + name)
    return paths
