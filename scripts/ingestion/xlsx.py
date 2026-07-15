"""Safe, stdlib-only XLSX worksheet extraction.

Worksheets are page-equivalents.  The extractor preserves workbook order,
sparse cell coordinates, formulas/cached values, defined table metadata, and
embedded raster images without requiring Excel or a third-party spreadsheet
package.  It reuses the hardened OOXML ZIP/relationship/asset primitives.
"""

import os
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .adapters import validate_page_records
from .identifiers import is_link_or_reparse, normalize_workspace_path
from .ooxml import (
    OOXMLCorruptError,
    OOXMLExtractionError,
    OOXMLEncryptedError,
    OOXMLSecurityError,
    OOXMLUnsupportedError,
    _AssetWriter,
    _Package,
    _open_stable_zip,
    _attribute,
    _local_name,
    _relationship_id,
    _relationships,
    _resolved_relationship,
)
from .quality import assess_page


MAX_WORKSHEETS = 1024
MAX_CELLS_PER_SHEET = 200000
MAX_SHARED_STRINGS = 500000
MAX_TABLES_PER_SHEET = 4096
MAX_IMAGES_PER_SHEET = 4096
MAX_CELL_TEXT = 4 * 1024 * 1024
MAX_EXPANDED_TEXT_PER_SHEET = 32 * 1024 * 1024
MAX_EXCEL_ROW = 1048576
MAX_EXCEL_COLUMN = 16384

_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")
_RANGE_RE = re.compile(
    r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6}):"
    r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$"
)


class XLSXExtractionError(OOXMLExtractionError):
    """Base class for workbook-specific extraction failures."""


class XLSXUnsupportedError(XLSXExtractionError, OOXMLUnsupportedError):
    """The source uses a workbook feature this local adapter cannot parse."""


class XLSXCorruptError(XLSXExtractionError, OOXMLCorruptError):
    """The workbook is malformed or internally inconsistent."""


class XLSXSecurityError(XLSXExtractionError):
    """The workbook attempted an unsafe path, relationship, or output write."""


def _column_number(label):
    number = 0
    for char in label.upper():
        if not "A" <= char <= "Z":
            raise XLSXCorruptError("invalid spreadsheet column: %s" % label)
        number = number * 26 + ord(char) - ord("A") + 1
    if not 1 <= number <= MAX_EXCEL_COLUMN:
        raise XLSXCorruptError("spreadsheet column is outside XLSX bounds: %s" % label)
    return number


def _column_label(number):
    if type(number) is not int or not 1 <= number <= MAX_EXCEL_COLUMN:
        raise XLSXCorruptError("spreadsheet column number is outside XLSX bounds")
    chars = []
    while number:
        number, remainder = divmod(number - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _cell_coordinate(value):
    if not isinstance(value, str):
        raise XLSXCorruptError("cell reference must be text")
    match = _CELL_RE.match(value)
    if not match:
        raise XLSXCorruptError("invalid cell reference: %s" % value)
    column = _column_number(match.group(1))
    row = int(match.group(2))
    if row > MAX_EXCEL_ROW:
        raise XLSXCorruptError("spreadsheet row is outside XLSX bounds: %s" % value)
    return "%s%d" % (_column_label(column), row), column, row


def _range_metadata(value):
    if not isinstance(value, str):
        raise XLSXCorruptError("cell range must be text")
    single = _CELL_RE.match(value)
    if single:
        coordinate, column, row = _cell_coordinate(value)
        return {
            "ref": coordinate,
            "start": coordinate,
            "end": coordinate,
            "start_column": column,
            "start_row": row,
            "end_column": column,
            "end_row": row,
        }
    match = _RANGE_RE.match(value)
    if not match:
        raise XLSXCorruptError("invalid cell range: %s" % value)
    start, start_col, start_row = _cell_coordinate(match.group(1) + match.group(2))
    end, end_col, end_row = _cell_coordinate(match.group(3) + match.group(4))
    if end_col < start_col or end_row < start_row:
        raise XLSXCorruptError("cell range is reversed: %s" % value)
    return {
        "ref": "%s:%s" % (start, end),
        "start": start,
        "end": end,
        "start_column": start_col,
        "start_row": start_row,
        "end_column": end_col,
        "end_row": end_row,
    }


def _direct_child(element, name):
    return next((child for child in list(element) if _local_name(child.tag) == name), None)


def _text_nodes(element):
    if element is None:
        return ""
    return "".join(node.text or "" for node in element.iter() if _local_name(node.tag) == "t")


def _shared_strings(package, workbook_relationships):
    relationship = next((
        rel for rel in workbook_relationships.values()
        if rel["type"].rstrip("/").lower().endswith("/sharedstrings")
    ), None)
    if relationship is None:
        return []
    if relationship["external"]:
        raise OOXMLSecurityError("external sharedStrings relationship is not allowed")
    root = package.xml(relationship["resolved"])
    result = []
    for node in list(root):
        if _local_name(node.tag) != "si":
            continue
        if len(result) >= MAX_SHARED_STRINGS:
            raise XLSXCorruptError(
                "shared string table exceeds %d entries" % MAX_SHARED_STRINGS
            )
        value = _text_nodes(node)
        if len(value) > MAX_CELL_TEXT:
            raise XLSXCorruptError("shared string exceeds the cell text limit")
        result.append(value)
    unique_count = _attribute(root, "uniqueCount")
    if unique_count is not None:
        try:
            declared = int(unique_count)
        except ValueError as exc:
            raise XLSXCorruptError("sharedStrings uniqueCount is invalid") from exc
        if declared != len(result):
            raise XLSXCorruptError(
                "sharedStrings uniqueCount=%d but %d items were found"
                % (declared, len(result))
            )
    return result


def _cell_value(cell, shared_strings):
    value_type = (_attribute(cell, "t") or "n").strip()
    value_node = _direct_child(cell, "v")
    raw = value_node.text or "" if value_node is not None else ""
    if len(raw) > MAX_CELL_TEXT:
        raise XLSXCorruptError("cell value exceeds the text limit")
    if value_type == "s":
        try:
            index = int(raw)
        except ValueError as exc:
            raise XLSXCorruptError("shared-string cell has an invalid index") from exc
        if index < 0 or index >= len(shared_strings):
            raise XLSXCorruptError("shared-string cell index is outside the table")
        return shared_strings[index], raw, value_type
    if value_type == "inlineStr":
        value = _text_nodes(_direct_child(cell, "is"))
        if len(value) > MAX_CELL_TEXT:
            raise XLSXCorruptError("inline string exceeds the text limit")
        return value, value, value_type
    if value_type == "b":
        if raw not in ("0", "1"):
            raise XLSXCorruptError("boolean cell must contain 0 or 1")
        return "TRUE" if raw == "1" else "FALSE", raw, value_type
    if value_type in ("n", "str", "e", "d"):
        return raw, raw, value_type
    raise XLSXUnsupportedError("unsupported XLSX cell type: %s" % value_type)


def _worksheet_cells(root, shared_strings):
    sheet_data = next((node for node in root.iter() if _local_name(node.tag) == "sheetData"), None)
    if sheet_data is None:
        return []
    result = []
    seen = set()
    inferred_row = 0
    expanded_text_bytes = 0
    for row_node in list(sheet_data):
        if _local_name(row_node.tag) != "row":
            continue
        row_value = _attribute(row_node, "r")
        if row_value is None:
            inferred_row += 1
            row_number = inferred_row
        else:
            try:
                row_number = int(row_value)
            except ValueError as exc:
                raise XLSXCorruptError("worksheet row has an invalid r attribute") from exc
            if not 1 <= row_number <= MAX_EXCEL_ROW:
                raise XLSXCorruptError("worksheet row is outside XLSX bounds")
            inferred_row = row_number
        inferred_column = 0
        for cell in list(row_node):
            if _local_name(cell.tag) != "c":
                continue
            if len(result) >= MAX_CELLS_PER_SHEET:
                raise XLSXCorruptError(
                    "worksheet exceeds %d populated cells" % MAX_CELLS_PER_SHEET
                )
            reference = _attribute(cell, "r")
            if reference is None:
                inferred_column += 1
                coordinate = "%s%d" % (_column_label(inferred_column), row_number)
                column = inferred_column
            else:
                coordinate, column, referenced_row = _cell_coordinate(reference)
                if referenced_row != row_number:
                    raise XLSXCorruptError(
                        "cell %s disagrees with containing row %d" % (coordinate, row_number)
                    )
                inferred_column = column
            if coordinate in seen:
                raise XLSXCorruptError("duplicate worksheet cell: %s" % coordinate)
            seen.add(coordinate)
            display, raw, value_type = _cell_value(cell, shared_strings)
            formula_node = _direct_child(cell, "f")
            formula = formula_node.text or "" if formula_node is not None else None
            if formula is not None and len(formula) > MAX_CELL_TEXT:
                raise XLSXCorruptError("cell formula exceeds the text limit")
            expanded_text_bytes += sum(
                len(value.encode("utf-8"))
                for value in (coordinate, display, raw, formula or "")
            )
            if expanded_text_bytes > MAX_EXPANDED_TEXT_PER_SHEET:
                raise XLSXCorruptError(
                    "worksheet expanded text exceeds the %d-byte limit"
                    % MAX_EXPANDED_TEXT_PER_SHEET
                )
            style = _attribute(cell, "s")
            if style is not None:
                try:
                    style = int(style)
                except ValueError as exc:
                    raise XLSXCorruptError("cell style index is invalid") from exc
                if style < 0:
                    raise XLSXCorruptError("cell style index cannot be negative")
            record = {
                "coordinate": coordinate,
                "row": row_number,
                "column": column,
                "value": display,
                "raw_value": raw,
                "cell_type": value_type,
                "style_index": style,
                "formula": formula,
            }
            if formula_node is not None:
                formula_type = _attribute(formula_node, "t")
                shared_index = _attribute(formula_node, "si")
                formula_ref = _attribute(formula_node, "ref")
                record["formula_type"] = formula_type
                if shared_index is not None:
                    try:
                        record["shared_formula_index"] = int(shared_index)
                    except ValueError as exc:
                        raise XLSXCorruptError("shared formula index is invalid") from exc
                else:
                    record["shared_formula_index"] = None
                record["formula_ref"] = (
                    _range_metadata(formula_ref)["ref"] if formula_ref is not None else None
                )
            result.append(record)
    return result


def _worksheet_tables(package, worksheet_part, worksheet_root, relationships):
    tables = []
    for node in worksheet_root.iter():
        if _local_name(node.tag) != "tablePart":
            continue
        if len(tables) >= MAX_TABLES_PER_SHEET:
            raise XLSXCorruptError("worksheet has too many defined tables")
        relationship_id = _relationship_id(node)
        if not relationship_id:
            raise XLSXCorruptError("tablePart is missing its relationship id")
        table_part = _resolved_relationship(relationships, relationship_id, "/table")
        table_root = package.xml(table_part)
        if _local_name(table_root.tag) != "table":
            raise XLSXCorruptError("table relationship did not resolve to a table part")
        table_range = _attribute(table_root, "ref")
        if table_range is None:
            raise XLSXCorruptError("defined table is missing its cell range")
        columns = []
        for column in table_root.iter():
            if _local_name(column.tag) == "tableColumn":
                name = _attribute(column, "name")
                if not name:
                    raise XLSXCorruptError("defined table column is missing a name")
                columns.append(name)
        tables.append({
            "name": _attribute(table_root, "name"),
            "display_name": _attribute(table_root, "displayName"),
            "range": _range_metadata(table_range),
            "columns": columns,
            "part": table_part,
        })
    return tables


def _drawing_cell(anchor, name):
    marker = _direct_child(anchor, name)
    if marker is None:
        return None
    column_node = _direct_child(marker, "col")
    row_node = _direct_child(marker, "row")
    if column_node is None or row_node is None:
        raise XLSXCorruptError("drawing marker is missing row/column")
    try:
        column = int(column_node.text or "") + 1
        row = int(row_node.text or "") + 1
    except ValueError as exc:
        raise XLSXCorruptError("drawing marker row/column is invalid") from exc
    return "%s%d" % (_column_label(column), row)


def _drawing_images(package, drawing_part, writer, review):
    root = package.xml(drawing_part)
    relationships = _relationships(package, drawing_part)
    result = []
    for anchor in list(root):
        anchor_kind = _local_name(anchor.tag)
        if anchor_kind not in ("oneCellAnchor", "twoCellAnchor", "absoluteAnchor"):
            continue
        start = _drawing_cell(anchor, "from")
        end = _drawing_cell(anchor, "to")
        for node in anchor.iter():
            if _local_name(node.tag) not in ("blip", "imagedata"):
                continue
            relationship_id = (
                _attribute(node, "embed") or _attribute(node, "link")
                or _attribute(node, "id")
            )
            if not relationship_id:
                continue
            if len(result) >= MAX_IMAGES_PER_SHEET:
                raise XLSXCorruptError("worksheet has too many embedded images")
            image_part = _resolved_relationship(relationships, relationship_id, "/image")
            payload = package.read_cached(image_part)
            description = next((
                _attribute(item, "descr") or _attribute(item, "title")
                or _attribute(item, "name")
                for item in anchor.iter()
                if _local_name(item.tag) in ("cNvPr", "docPr")
                and (_attribute(item, "descr") or _attribute(item, "title")
                     or _attribute(item, "name"))
            ), "Embedded worksheet image")
            try:
                asset = writer.save(image_part, payload)
            except OOXMLUnsupportedError as exc:
                review("xlsx_unsafe_asset", str(exc))
                asset = None
            if asset is None:
                review(
                    "xlsx_asset_not_materialized",
                    "embedded image %s was detected but no local asset was produced"
                    % image_part,
                )
            result.append({
                "text": description,
                "asset": asset,
                "asset_sha256": writer.digest_for(asset) if asset is not None else None,
                "part": image_part,
                "anchor_type": anchor_kind,
                "from_cell": start,
                "to_cell": end,
            })
    return result


def _worksheet_images(package, worksheet_part, root, relationships, writer, review):
    result = []
    seen_drawings = set()
    for node in root.iter():
        if _local_name(node.tag) != "drawing":
            continue
        relationship_id = _relationship_id(node)
        if not relationship_id:
            raise XLSXCorruptError("worksheet drawing is missing its relationship id")
        drawing_part = _resolved_relationship(relationships, relationship_id, "/drawing")
        if drawing_part in seen_drawings:
            continue
        seen_drawings.add(drawing_part)
        result.extend(_drawing_images(package, drawing_part, writer, review))
    return result


def _unsupported_relationships(relationships):
    safe_suffixes = (
        "/table", "/drawing", "/hyperlink", "/comments", "/vmldrawing",
        "/threadedcomment", "/printersettings",
    )
    result = []
    for relationship in relationships.values():
        relation_type = relationship["type"].rstrip("/").lower()
        if not relation_type.endswith(safe_suffixes):
            result.append(relationship["type"])
        elif relation_type.endswith(("/comments", "/vmldrawing", "/threadedcomment")):
            result.append(relationship["type"])
    return sorted(set(result))


def _sheet_record(package, source_file, page, sheet, shared_strings, writer):
    root = package.xml(sheet["part"])
    if _local_name(root.tag) != "worksheet":
        raise XLSXCorruptError("sheet relationship did not resolve to a worksheet")
    relationships = _relationships(package, sheet["part"])
    review_signals = []

    def review(reason_code, detail):
        signal = {"reason_code": reason_code, "detail": detail}
        if signal not in review_signals:
            review_signals.append(signal)

    if sheet["state"] != "visible":
        review(
            "xlsx_hidden_sheet",
            "worksheet %s has workbook state %s" % (sheet["name"], sheet["state"]),
        )
    cells = _worksheet_cells(root, shared_strings)
    tables = _worksheet_tables(package, sheet["part"], root, relationships)
    images = _worksheet_images(package, sheet["part"], root, relationships, writer, review)
    for relation_type in _unsupported_relationships(relationships):
        review(
            "xlsx_unparsed_relationship",
            "worksheet relationship is preserved only as a review signal: %s" % relation_type,
        )

    merged_ranges = []
    for node in root.iter():
        if _local_name(node.tag) == "mergeCell":
            reference = _attribute(node, "ref")
            if reference is None:
                raise XLSXCorruptError("mergeCell is missing its range")
            merged_ranges.append(_range_metadata(reference)["ref"])

    elements = []

    def add(kind, text, **extra):
        element = {
            "kind": kind,
            "text": text,
            "ordinal": len(elements),
            "bbox": None,
            "method": "native",
            "confidence": 1.0,
        }
        element.update(extra)
        elements.append(element)

    add("heading", sheet["name"], level=1, metadata={
        "sheet_name": sheet["name"],
        "sheet_state": sheet["state"],
    })
    if cells:
        lines = ["Cell\tValue"] + [
            "%s\t%s" % (cell["coordinate"], cell["value"].replace("\r", " ").replace("\n", " "))
            for cell in cells
        ]
        add("table", "\n".join(lines), metadata={
            "sheet_name": sheet["name"],
            "representation": "sparse_coordinate_value_tsv",
            "cells": cells,
        })
    for cell in cells:
        if cell["formula"] is None:
            continue
        metadata = dict(cell)
        add(
            "formula", "=%s" % cell["formula"], metadata=metadata,
        )
        if cell["raw_value"] == "":
            review(
                "xlsx_formula_without_cached_value",
                "formula cell %s has no cached result" % cell["coordinate"],
            )
        if cell.get("formula_type") == "shared" and not cell["formula"]:
            review(
                "xlsx_shared_formula_expression_missing",
                "shared formula cell %s has no local expression; the adapter did not invent one"
                % cell["coordinate"],
            )
        if ("[" in cell["formula"] or re.search(
                r"\b(?:WEBSERVICE|HYPERLINK|RTD)\s*\(", cell["formula"], re.IGNORECASE)):
            review(
                "xlsx_external_formula_reference",
                "formula cell %s may reference external workbook/network data; it was not evaluated"
                % cell["coordinate"],
            )
    for table in tables:
        add(
            "table",
            "Defined table %s (%s)" % (
                table["display_name"] or table["name"] or "(unnamed)",
                table["range"]["ref"],
            ),
            metadata={"defined_table": table, "sheet_name": sheet["name"]},
        )
    embedded_assets = []
    for image in images:
        asset = image["asset"]
        if asset is not None and asset not in embedded_assets:
            embedded_assets.append(asset)
        figure_fields = {
            "metadata": {
                "sheet_name": sheet["name"],
                "part": image["part"],
                "anchor_type": image["anchor_type"],
                "from_cell": image["from_cell"],
                "to_cell": image["to_cell"],
            },
        }
        if asset is not None:
            figure_fields.update({
                "asset": asset,
                "asset_role": "figure",
                "asset_sha256": image["asset_sha256"],
            })
        add("figure", image["text"], **figure_fields)

    text = "\n".join(
        element["text"] for element in elements
        if element["kind"] not in ("figure",) and element["text"]
    )
    quality = assess_page({
        "page": page,
        "text": text,
        "image_count": len(images),
        # XLSX anchors are grid-relative, not rendered page areas.  Keep the
        # numerical signal at zero and expose that limitation in metadata.
        "image_area_ratio": 0.0,
        "vector_count": 0,
        "multi_column_hint": False,
        "table_hint": bool(cells or tables),
        "formula_hint": any(cell["formula"] is not None for cell in cells),
    })
    return {
        "file": source_file,
        "page": page,
        "text": text,
        "elements": elements,
        "embedded_assets": embedded_assets,
        "review_signals": review_signals,
        "quality_signals": quality,
        "metadata": {
            "format": "xlsx",
            "page_equivalent": "worksheet",
            "sheet_name": sheet["name"],
            "sheet_state": sheet["state"],
            "worksheet_part": sheet["part"],
            "cell_count": len(cells),
            "formula_count": sum(cell["formula"] is not None for cell in cells),
            "table_count": len(tables),
            "image_count": len(images),
            "merged_ranges": merged_ranges,
            "rendered_layout_available": False,
        },
    }


def _workbook_sheets(package):
    workbook_part = "xl/workbook.xml"
    root = package.xml(workbook_part)
    if _local_name(root.tag) != "workbook":
        raise XLSXCorruptError("xl/workbook.xml is not a workbook")
    relationships = _relationships(package, workbook_part, required=True)
    result = []
    names = set()
    for node in root.iter():
        if _local_name(node.tag) != "sheet":
            continue
        if len(result) >= MAX_WORKSHEETS:
            raise XLSXCorruptError("workbook exceeds %d worksheets" % MAX_WORKSHEETS)
        name = _attribute(node, "name")
        relationship_id = _relationship_id(node)
        state = (_attribute(node, "state") or "visible").strip().lower()
        if not name or not relationship_id:
            raise XLSXCorruptError("workbook sheet is missing name or relationship id")
        if len(name) > 31 or any(char in name for char in "[]:*?/\\"):
            raise XLSXCorruptError("workbook sheet has an invalid name: %s" % name)
        if name.casefold() in names:
            raise XLSXCorruptError("workbook contains a duplicate sheet name: %s" % name)
        names.add(name.casefold())
        if state not in ("visible", "hidden", "veryhidden"):
            raise XLSXCorruptError("worksheet %s has an invalid state" % name)
        part = _resolved_relationship(relationships, relationship_id, "/worksheet")
        result.append({"name": name, "state": state, "part": part})
    if not result:
        raise XLSXCorruptError("workbook contains no worksheets")
    return result, relationships


def extract_xlsx(path, source_file, asset_root=None, expected_sha256=None):
    """Extract an XLSX into one normalized page record per worksheet."""

    try:
        canonical_file = normalize_workspace_path(source_file)
    except (TypeError, ValueError) as exc:
        raise XLSXExtractionError("source_file must be a canonical relative path") from exc
    try:
        filesystem_path = os.path.abspath(os.fspath(path))
    except TypeError as exc:
        raise XLSXExtractionError("path must be a filesystem path") from exc
    if os.path.splitext(filesystem_path)[1].lower() != ".xlsx":
        raise XLSXUnsupportedError("only .xlsx workbooks are supported")
    if not os.path.isfile(filesystem_path):
        raise XLSXExtractionError("XLSX source is not a regular file: %s" % filesystem_path)
    if is_link_or_reparse(filesystem_path):
        raise XLSXSecurityError("XLSX source must not be a link/junction/reparse point")
    try:
        writer = _AssetWriter(asset_root, canonical_file)
    except OOXMLExtractionError as exc:
        raise XLSXExtractionError("invalid XLSX asset output: %s" % exc) from exc
    try:
        with _open_stable_zip(
            filesystem_path, expected_sha256=expected_sha256,
        ) as archive:
            package = _Package(archive)
            sheets, workbook_relationships = _workbook_sheets(package)
            strings = _shared_strings(package, workbook_relationships)
            records = [
                _sheet_record(
                    package, canonical_file, page, sheet, strings, writer,
                )
                for page, sheet in enumerate(sheets, 1)
            ]
            workbook_review = []
            for relationship in workbook_relationships.values():
                relation_type = relationship["type"].rstrip("/").lower()
                if relationship["external"] or relation_type.endswith((
                    "/externallink", "/connections",
                )):
                    workbook_review.append({
                        "reason_code": "xlsx_external_workbook_reference",
                        "detail": (
                            "workbook relationship was recorded but not followed/evaluated: %s"
                            % relationship["type"]
                        ),
                    })
            active_parts = sorted(
                name for name in package.by_name
                if name.lower().endswith(("vbaproject.bin", ".exe", ".dll"))
                or "/embeddings/" in name.lower()
                or "/activex/" in name.lower()
            )
            if active_parts:
                workbook_review.append({
                    "reason_code": "xlsx_active_or_embedded_content",
                    "detail": "workbook contains inactive/unparsed embedded part(s): %s"
                              % ", ".join(active_parts[:8]),
                })
            for record in records:
                for signal in workbook_review:
                    if signal not in record["review_signals"]:
                        record["review_signals"].append(signal)
        return validate_page_records(records)
    except XLSXExtractionError:
        writer.rollback()
        raise
    except OOXMLSecurityError as exc:
        writer.rollback()
        raise XLSXSecurityError(str(exc)) from exc
    except OOXMLEncryptedError as exc:
        writer.rollback()
        raise XLSXUnsupportedError(str(exc)) from exc
    except OOXMLCorruptError as exc:
        writer.rollback()
        raise XLSXCorruptError(str(exc)) from exc
    except OOXMLUnsupportedError as exc:
        writer.rollback()
        raise XLSXUnsupportedError(str(exc)) from exc
    except OOXMLExtractionError as exc:
        writer.rollback()
        raise XLSXExtractionError(str(exc)) from exc
    except zipfile.BadZipFile as exc:
        writer.rollback()
        raise XLSXCorruptError("damaged XLSX ZIP package: %s" % exc) from exc
    except NotImplementedError as exc:
        writer.rollback()
        raise XLSXUnsupportedError("unsupported XLSX ZIP feature: %s" % exc) from exc
    except ET.ParseError as exc:
        writer.rollback()
        raise XLSXCorruptError("malformed XLSX XML: %s" % exc) from exc
    except OSError as exc:
        writer.rollback()
        raise XLSXCorruptError("XLSX changed or became unreadable: %s" % exc) from exc


__all__ = [
    "MAX_CELLS_PER_SHEET", "MAX_EXPANDED_TEXT_PER_SHEET", "MAX_IMAGES_PER_SHEET",
    "MAX_SHARED_STRINGS",
    "MAX_TABLES_PER_SHEET", "MAX_WORKSHEETS", "XLSXCorruptError",
    "XLSXExtractionError", "XLSXSecurityError", "XLSXUnsupportedError", "extract_xlsx",
]
