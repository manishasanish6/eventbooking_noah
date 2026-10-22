"""Minimal .xlsx writer built on the standard library only.

An .xlsx file is a zip of XML parts, so we can produce one without adding a
dependency such as openpyxl. Supports the small feature set the admin exports
need: multiple sheets, typed cells (text/number), column widths, a bold frozen
header row, and full-cell fills used for the seat-status colour code.
"""

import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _clean(value) -> str:
    """Make a string safe for XML: strip control chars and invalid surrogates."""
    text = _ILLEGAL_XML.sub("", str(value))
    return "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)


def _col_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _sheet_name(name: str, used: set) -> str:
    """Excel sheet names: <=31 chars, no []:*?/\\, unique, never blank."""
    base = _INVALID_SHEET_CHARS.sub(" ", _clean(name)).strip()[:31] or "Sheet"
    candidate, n = base, 2
    while candidate.lower() in used:
        suffix = f" ({n})"
        candidate = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate.lower())
    return candidate


def _cell_ref(ref_fill, row, col) -> str:
    return f"{_col_letter(col)}{row}"


def _cell_xml(row, col, cell, style_id: int) -> str:
    ref = _cell_ref(None, row, col)
    style = f' s="{style_id}"' if style_id else ""
    value = cell.get("value", "")

    if value is None or value == "":
        return f'<c r="{ref}"{style}/>'

    if isinstance(value, bool):
        return f'<c r="{ref}"{style} t="b"><v>{1 if value else 0}</v></c>'

    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'

    return (
        f'<c r="{ref}"{style} t="inlineStr">'
        f'<is><t xml:space="preserve">{escape(_clean(value))}</t></is></c>'
    )


def _sheet_xml(rows, widths, freeze_header: bool) -> str:
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)
        ) + "</cols>"

    pane = ""
    if freeze_header:
        pane = (
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
        )

    body = []
    for r_index, row in enumerate(rows, start=1):
        cells = "".join(
            _cell_xml(r_index, c_index, cell, int(cell.get("style", 0)))
            for c_index, cell in enumerate(row, start=1)
        )
        body.append(f'<row r="{r_index}">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"{cols}<sheetData>{''.join(body)}</sheetData>"
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        "</worksheet>"
    )


def _styles_xml(fills) -> str:
    """fills: ordered ARGB colours; index i maps to cellXfs style id i+2."""
    fill_defs = ['<fill><patternFill patternType="none"/></fill>',
                 '<fill><patternFill patternType="gray125"/></fill>']
    for colour in fills:
        fill_defs.append(_fill_xml(colour))
    fill_count = len(fill_defs)

    # cellXfs indices: 0 default, 1 bold header, 2+ the solid fills below.
    xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" '
        'applyFont="1" applyAlignment="1"><alignment vertical="center"/></xf>',
    ]
    for i, colour in enumerate(fills, start=1):
        xfs.append(
            f'<xf numFmtId="0" fontId="0" fillId="{i + 1}" borderId="0" xfId="0" '
            'applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        )

    # cellXfs indices: 0 default, 1 bold header, 2..n+1 the fills above
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        "</fonts>"
        f'<fills count="{fill_count}">{"".join(fill_defs)}</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def _fill_xml(colour: str) -> str:
    return (
        '<fill><patternFill patternType="solid">'
        f'<fgColor rgb="{colour}"/><bgColor indexed="64"/>'
        "</patternFill></fill>"
    )


def build_xlsx(sheets) -> bytes:
    """Serialise sheets into .xlsx bytes.

    sheets: list of dicts:
        {"name": str, "rows": [[cell, ...], ...], "widths": [float, ...],
         "freeze": bool}
    A cell is either a bare value or {"value": v, "style": "fill:ARGB"}.

    Every distinct fill colour used anywhere is registered in the shared style
    table automatically, so callers never have to keep per-sheet palettes in
    sync. Style ids are assigned as: 0 = default, 1 = bold header, 2+ = fills.
    """
    import io

    used_names = set()
    normalised = []
    # Colour -> cellXfs style id, filled lazily as colours are encountered.
    fill_styles: dict = {}

    def style_for(value) -> int:
        if isinstance(value, bool) or value in (None, 0, ""):
            return 0
        if isinstance(value, int):
            return value  # explicit pre-assigned style id
        if isinstance(value, str) and value.startswith("fill:"):
            colour = value.split(":", 1)[1]
            if colour not in fill_styles:
                fill_styles[colour] = len(fill_styles) + 2
            return fill_styles[colour]
        return 0

    for sheet in sheets:
        rows = []
        for row in sheet.get("rows") or []:
            out_row = []
            for cell in row:
                if isinstance(cell, dict):
                    out_row.append({
                        "value": cell.get("value"),
                        "style": style_for(cell.get("style", 0)),
                    })
                else:
                    out_row.append({"value": cell, "style": 0})
            rows.append(out_row)
        normalised.append({
            "name": _sheet_name(sheet.get("name") or "Sheet", used_names),
            "rows": rows,
            "widths": sheet.get("widths") or [],
            "freeze": bool(sheet.get("freeze", True)),
        })

    ordered_fills = sorted(fill_styles, key=lambda c: fill_styles[c])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(normalised) + 1)
        )
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(
            f'<sheet name="{escape(s["name"])}" sheetId="{i}" r:id="rId{i}"/>'
            for i, s in enumerate(normalised, start=1)
        )
        + "</sheets></workbook>"
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(normalised) + 1)
        )
        + f'<Relationship Id="rId{len(normalised) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>Noah Events</dc:creator>"
        "<cp:lastModifiedBy>Noah Events</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        "</cp:coreProperties>"
    )

    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>Noah Events</Application>"
        "</Properties>"
    )

    # A single shared styles part: fills are registered globally by colour.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", _styles_xml(ordered_fills))
        for i, sheet in enumerate(normalised, start=1):
            zf.writestr(
                f"xl/worksheets/sheet{i}.xml",
                _sheet_xml(sheet["rows"], sheet["widths"], sheet["freeze"]),
            )
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)

    return buffer.getvalue()
