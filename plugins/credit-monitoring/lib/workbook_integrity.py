#!/usr/bin/env python3
"""Workbook and memo content comparison — repackaging vs real edits.

Raw bytes change when Excel or SharePoint re-wraps a file; what a person sees
may not. A manifest signs a workbook over its `xl/` parts (`persistence.file_id`)
and any other file by its raw sha256, but whether a file was edited is decided
by what a person would read: for a workbook, every
cell's value or formula, then its look (`look_difference`: formats, notes,
charts and the rest a person sees); for a memo, its visible text.
`file_moves` names what moved in a workbook, in a person's words.

Stdlib + openpyxl only. If openpyxl is missing, fall back to raw bytes.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import io
import os
import posixpath
import re
import warnings
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html.parser import HTMLParser
from zipfile import BadZipFile, ZipFile

try:
    from plugin.lib import requirements as prerequisites
except ModuleNotFoundError:
    import requirements as prerequisites

openpyxl = prerequisites.openpyxl
if openpyxl is not None:
    from openpyxl.cell.cell import Cell
    from openpyxl.formula.tokenizer import Token, Tokenizer
    from openpyxl.utils import column_index_from_string
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
else:
    ArrayFormula = DataTableFormula = ()

# The openpyxl the look compare is measured on: it reads attributes openpyxl
# keeps private, which move between versions. On any other version the look
# reads as unread and the cell comparison stands alone.
LOOK_OPENPYXL = "3.1.5"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _is_formula(cell):
    """Whether a cell holds a formula, by its type: text that starts with "="
    is text."""
    return cell.data_type == "f"


_QUOTED_SHEET = re.compile(r"'((?:[^']|'')+)'!")
_XL_PREFIX = re.compile(r"^(?:_xl(?:fn|ws)\.)+", re.IGNORECASE)


def _formula_tokens(text):
    """The formula the way Excel reads it: no whitespace, sheet names without
    their quotes, references and functions in upper case, a string literal as
    written."""
    out = []
    for tok in Tokenizer(text).items:
        if tok.type == Token.WSPACE:
            continue
        value = tok.value
        if tok.type == Token.OPERAND and tok.subtype == Token.RANGE:
            value = _QUOTED_SHEET.sub(lambda m: m.group(1).replace("''", "'") + "!", value).upper()
        elif tok.type == Token.FUNC:
            value = _XL_PREFIX.sub("", value).upper()
        elif not (tok.type == Token.OPERAND and tok.subtype == Token.TEXT):
            value = value.upper()
        out.append((tok.type, tok.subtype, value))
    return out


def _same_formula(a, b):
    """Two formulas are the same when they mean the same thing: Excel re-saves
    `='Covenants'!$D$7` as `=Covenants!$D$7`, and quotes around a sheet name,
    letter case and spaces do not count."""
    if a == b:
        return True
    try:
        return _formula_tokens(a) == _formula_tokens(b)
    except Exception:
        return False


def _same_cell(a, b):
    if _blank(a) and _blank(b):
        return True
    if _num(a) and _num(b):
        return abs(a - b) <= max(abs(a), abs(b), 1.0) * 1e-12
    try:
        return a == b
    except Exception:  # a rich-text run does not compare with a plain one: they differ
        return False


def _same_value(cell_a, cell_b):
    """Whether two cells hold the same value or formula. A formula is the
    same by what it means (`_same_formula`), an array or data-table formula
    by its range and text, since openpyxl loads them as objects without
    equality. Anything else compares as `_same_cell`."""
    if not (_is_formula(cell_a) or _is_formula(cell_b)):
        return _same_cell(cell_a.value, cell_b.value)
    if not (_is_formula(cell_a) and _is_formula(cell_b)):
        return False
    a, b = cell_a.value, cell_b.value
    if isinstance(a, (ArrayFormula, DataTableFormula)) or isinstance(
            b, (ArrayFormula, DataTableFormula)):
        if type(a) is not type(b):
            return False
        if isinstance(a, ArrayFormula):
            return a.ref == b.ref and _same_formula(a.text or "", b.text or "")
        return dict(a) == dict(b)
    return _same_formula(a, b)


def _grid_size(*sheets):
    return (max(ws.max_row for ws in sheets), max(ws.max_column for ws in sheets))


def _defined_names(book):
    """Workbook- and sheet-scoped named ranges, name -> formula/target text.

    A name redefinition (e.g. `TotalRevenue` now points at a different cell)
    changes what any formula referencing it computes, with no visible cell
    difference of its own -- it must not disappear from this comparison.
    Each target is read as Excel reads it (`_name_key`), so a re-save that
    drops the quotes around a sheet name is not a repoint.
    """
    names = {name: _name_key(dn.value) for name, dn in book.defined_names.items()}
    for ws in book.worksheets:
        for name, dn in dict(getattr(ws, "defined_names", None) or {}).items():
            names[f"{ws.title}!{name}"] = _name_key(dn.value)
    return names


def _has_external_links(path):
    """Whether the raw zip package references an external workbook link.

    Checked directly against the package structure -- not via openpyxl's
    private, version-dependent `_external_links` attribute, which may be
    empty or incompletely populated for a link openpyxl cannot fully parse.
    An external link's cached values or target are not modelled by this
    comparison at all, present or not, so this must not silently trust an
    internal object's opinion of whether one exists.
    """
    try:
        with ZipFile(path) as z:
            return any(name.lower().startswith("xl/externallinks/") for name in z.namelist())
    except (OSError, BadZipFile):
        return True


def same_workbook_content(path_a, path_b):
    """True when two workbook files show a person the same thing.

    First the cells (values and formulas, a formula by what it means), the
    defined names (calculation-relevant, not a cell value) and external-link
    presence. Then, with every cell the same, the look (`look_difference`): a
    note, a format or a chart a person changed is an edit, so a delivery
    never writes the run's copy over it. A look this cannot read keeps the
    cell reading. Container-only re-wraps (SharePoint/Excel) that change
    neither are the same content.
    """
    if openpyxl is None:
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            return fa.read() == fb.read()
    try:
        book_a = openpyxl.load_workbook(path_a, data_only=False)
        book_b = openpyxl.load_workbook(path_b, data_only=False)
    except Exception:
        return False
    if book_a.sheetnames != book_b.sheetnames:
        return False
    if _has_external_links(path_a) or _has_external_links(path_b):
        return False
    if _defined_names(book_a) != _defined_names(book_b):
        return False
    for name in book_a.sheetnames:
        ws_a, ws_b = book_a[name], book_b[name]
        rows, cols = _grid_size(ws_a, ws_b)
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                if not _same_value(ws_a.cell(r, c), ws_b.cell(r, c)):
                    return False
    look = look_difference(path_a, path_b)
    return look is None or bool(look.get("unread")) or not look.get("what")


def look_unchecked(path_a, path_b):
    """Why the look of two workbooks was not compared, or None when it was.
    `same_workbook_content` calls a pair with the same cells the same content
    either way, so a report must not claim the look matched."""
    look = look_difference(path_a, path_b)
    if look is None or (look.get("what") and not look.get("unread")):
        return None
    return look.get("what") or "the workbook did not read"


# ------------------------------------------------------------ the look compare
# A font color that reads as the default text color: none, automatic, theme
# 1 (dark text), indexed 64 (the system foreground) or opaque black.
_DEFAULT_TEXT = {None, ("auto", True, 0.0), ("theme", 1, 0.0), ("indexed", 64, 0.0),
                 ("rgb", "FF000000", 0.0)}


def _color_look(color):
    if color is None:
        return None
    kind = getattr(color, "type", None)
    value = getattr(color, "value", None)
    value = value.upper() if isinstance(value, str) else value
    return (kind, value, round(float(getattr(color, "tint", 0) or 0), 6))


def _font_look(font):
    color = _color_look(font.color)
    return (font.name, float(font.sz) if font.sz else None, bool(font.b), bool(font.i),
            None if font.u in (None, "none") else font.u, bool(font.strike),
            font.vertAlign or None, None if color in _DEFAULT_TEXT else color)


def _fill_look(fill):
    if getattr(fill, "tagname", None) == "gradientFill":
        return ("gradient", fill.type, fill.degree,
                tuple((s.position, _color_look(s.color)) for s in fill.stop))
    kind = getattr(fill, "patternType", None)
    if not kind:
        return None
    if kind == "solid":
        return (kind, _color_look(fill.fgColor))
    return (kind, _color_look(fill.fgColor), _color_look(fill.bgColor))


def _border_look(border):
    sides = tuple((side.style, _color_look(side.color)) if side is not None and side.style
                  else None
                  for side in (border.left, border.right, border.top, border.bottom,
                               border.diagonal))
    if sides[4] is None:
        return sides
    return sides + (bool(border.diagonalUp), bool(border.diagonalDown))


def _alignment_look(al):
    return (None if al.horizontal in (None, "general") else al.horizontal,
            None if al.vertical in (None, "bottom") else al.vertical,
            bool(al.wrap_text), bool(al.shrink_to_fit), int(al.indent or 0),
            int(al.text_rotation or 0))


def _cell_look(cell, seen):
    """What a person sees of one cell's format, or of a row's or a column's:
    its number format, font, fill, border, alignment, protection and quote
    prefix. `seen` caches the answer per style, so a workbook reads each
    style once."""
    key = tuple(getattr(cell, "_style", ()) or ())
    if key and key in seen:
        return seen[key]
    look = (cell.number_format or "General", _font_look(cell.font), _fill_look(cell.fill),
            _border_look(cell.border), _alignment_look(cell.alignment),
            (cell.protection.locked is not False, bool(cell.protection.hidden)),
            bool(cell.quotePrefix))
    if key:
        seen[key] = look
    return look


def _tree(node, drop=(), skip=(), defaults=None):
    """An XML element as nested tuples: its tag with the namespace, its
    attributes sorted with the ones in `drop` left out, its text, its
    children. An `extLst`, and a child whose local name is in `skip`, is
    left out. `defaults` maps a local name to the attribute values the schema
    gives it when the attribute is absent; an attribute at that value is
    left out too."""
    given = (defaults or {}).get(_local(node.tag), {})
    return (node.tag, tuple(sorted((k, v) for k, v in node.attrib.items()
                                   if k not in drop and given.get(k) != v)),
            (node.text or "").strip(),
            tuple(_tree(c, drop, skip, defaults) for c in node
                  if _local(c.tag) != "extLst" and _local(c.tag) not in skip))


def _local(tag):
    return str(tag).rsplit("}", 1)[-1]


_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_DOC_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_X = "urn:schemas-microsoft-com:office:excel"
# Parts that show a person nothing: the package's own lists, file properties,
# custom XML, Excel Online's `[trash]/` scratch parts, the calculation chain,
# printer settings, the chart-style gallery's parts, the commenters list and
# the sensitivity label a SharePoint library can stamp on a file it stores.
_PART_OUT = re.compile(r"^(\[Content_Types\]\.xml|.*\.rels|docProps/.*|customXml/.*|docMetadata/.*|"
                       r"\[trash\]/.*|xl/calcChain\.xml|xl/printerSettings/.*|xl/persons/.*|"
                       r"xl/charts/(style|colors)\d*\.xml)$")
# A sheet's elements openpyxl reads, and the ones that show nothing.
_SHEET_READ = {"sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData",
               "sheetProtection", "scenarios", "autoFilter", "mergeCells",
               "conditionalFormatting", "dataValidations", "hyperlinks", "printOptions",
               "pageMargins", "pageSetup", "headerFooter", "rowBreaks", "colBreaks",
               "customSheetViews", "drawing", "legacyDrawing", "tableParts"}
_SHEET_OUT = {"sheetCalcPr", "phoneticPr", "sortState"}
_ANCHORS = {"twoCellAnchor", "oneCellAnchor", "absoluteAnchor"}
_PASSWORD = {"password", "algorithmName", "hashValue", "saltValue", "spinCount"}


class _Unread(Exception):
    """A part the look compare cannot read: the two workbooks differ."""


# A sheet that is not a worksheet, in a person's words, by its link's type.
_SHEET_WORDS = {"chartsheet": "a chart sheet", "dialogsheet": "a dialog sheet",
                "xlMacrosheet": "a macro sheet", "xlIntlMacrosheet": "a macro sheet"}


def _rels(z, names, part):
    """[(id, type's last word, target part)] of one part's internal links."""
    folder, base = posixpath.split(part)
    path = posixpath.join(folder, "_rels", base + ".rels")
    if path not in names:
        return []
    out = []
    for rel in ET.fromstring(z.read(path)):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        target = (target[1:] if target.startswith("/")
                  else posixpath.normpath(posixpath.join(folder, target)))
        out.append((rel.get("Id"), (rel.get("Type") or "").rsplit("/", 1)[-1], target))
    return out


# The DrawingML defaults Excel Online leaves out when it writes a theme
# again: a font's pitch family and character set, a fill rectangle's edges.
_FONT_DEFAULTS = {"pitchFamily": "0", "charset": "1"}
_RECT_DEFAULTS = {"l": "0", "t": "0", "r": "0", "b": "0"}
_THEME_DEFAULTS = {**dict.fromkeys(("latin", "ea", "cs", "sym"), _FONT_DEFAULTS),
                   **dict.fromkeys(("tileRect", "fillToRect", "fillRect", "srcRect"),
                                   _RECT_DEFAULTS)}


def _theme_look(raw):
    """The theme's colours, fonts and effects, its names and cached system
    colours left out, and an attribute at its schema default the same as
    an absent one."""
    elements = ET.fromstring(raw).find(f"{{{_NS_A}}}themeElements")
    if elements is None:
        return None
    return tuple(None if node is None else _tree(node, drop=("name", "lastClr"),
                                                 defaults=_THEME_DEFAULTS)
                 for node in (elements.find(f"{{{_NS_A}}}{part}")
                              for part in ("clrScheme", "fontScheme", "fmtScheme")))


def _notes(raw):
    """[(row, column, shown)] of a VML part that holds comment boxes and
    nothing else; None for any other VML part."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    out = []
    for child in root:
        local = _local(child.tag)
        if local in ("shapelayout", "shapetype"):
            continue
        data = child.find(f"{{{_NS_X}}}ClientData") if local == "shape" else None
        if data is None or data.get("ObjectType") != "Note":
            return None
        out.append((int(data.findtext(f"{{{_NS_X}}}Row") or -1),
                    int(data.findtext(f"{{{_NS_X}}}Column") or -1),
                    data.find(f"{{{_NS_X}}}Visible") is not None))
    return out


def _drawing(z, names, part, read):
    """(charts, charts only) of one drawing part. Each chart part it links
    joins `read`: openpyxl reads it."""
    charts, only = 0, True
    for anchor in ET.fromstring(z.read(part)):
        if (_local(anchor.tag) in _ANCHORS
                and any(_local(c.tag) == "graphicFrame" for c in anchor)
                and anchor.find(f".//{{{_NS_C}}}chart") is not None):
            charts += 1
        else:
            only = False
    for _id, kind, target in _rels(z, names, part):
        if kind == "chart":
            read.add(target)
    return charts, only


def _sheet_package(z, names, part, read):
    """What a sheet holds that openpyxl drops: its unread elements, the
    metadata Excel keeps on a cell, which comments show, and how many charts
    its drawings anchor."""
    extra, meta, depth = [], [], 0
    for event, el in ET.iterparse(io.BytesIO(z.read(part)), events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        local = _local(el.tag)
        if local == "c" and (el.get("cm") or el.get("vm")):
            meta.append((el.get("r"), el.get("cm"), el.get("vm")))
        elif depth == 1:
            if local not in _SHEET_READ and local not in _SHEET_OUT:
                extra.append(_tree(el))
            el.clear()
        elif depth == 2 and local == "row":
            el.clear()
    notes, charts = [], 0
    for _id, kind, target in _rels(z, names, part):
        if kind in ("comments", "table"):
            read.add(target)
        elif kind == "vmlDrawing":
            found = _notes(z.read(target))
            if found is not None:
                read.add(target)
                notes += found
        elif kind == "drawing":
            count, only = _drawing(z, names, target, read)
            charts += count
            if only:
                read.add(target)
    return {"extra": extra, "meta": sorted(meta), "notes": sorted(notes), "charts": charts}


def _package(raw):
    """What the look compare reads from the package beside openpyxl: the
    theme, each sheet's `_sheet_package`, and a checksum of every part
    openpyxl does not read. Raises `_Unread` on a chart sheet or any sheet
    that is not a worksheet."""
    with ZipFile(io.BytesIO(raw)) as z:
        names = set(z.namelist())
        book = next(t for _id, kind, t in _rels(z, names, "") if kind == "officeDocument")
        read = {book}
        links = {rid: (kind, t) for rid, kind, t in _rels(z, names, book)}
        theme = None
        for kind, target in links.values():
            if kind in ("styles", "sharedStrings"):
                read.add(target)
            elif kind == "theme":
                read.add(target)
                theme = _theme_look(z.read(target))
        sheets = {}
        for sheet in ET.fromstring(z.read(book)).iter(f"{{{_NS_MAIN}}}sheet"):
            kind, part = links[sheet.get(f"{{{_NS_DOC_R}}}id")]
            if kind != "worksheet":
                raise _Unread(f"the {sheet.get('name')} sheet, "
                              f"{_SHEET_WORDS.get(kind, 'not a worksheet')}, could not be compared")
            read.add(part)
            sheets[sheet.get("name")] = _sheet_package(z, names, part, read)
        unread = {n: hashlib.sha256(z.read(n)).hexdigest() for n in sorted(names)
                  if n not in read and not n.endswith("/") and not _PART_OUT.match(n)}
    return {"theme": theme, "sheets": sheets, "unread": unread}


def _name_key(text):
    try:
        return tuple(_formula_tokens("=" + str(text)))
    except Exception:
        return str(text)


def _look_names(wb):
    out = {}
    for scope, names in [(None, wb.defined_names)] + [(ws.title, ws.defined_names)
                                                       for ws in wb.worksheets]:
        for key, dn in names.items():
            out[(scope, key)] = (_name_key(dn.attr_text), bool(dn.hidden))
    return out


def _book_window(wb):
    view = wb.views[0] if wb.views else None
    return (None if view is None else
            (view.visibility or "visible", view.showSheetTabs is not False))


def _book_protection(wb):
    """What the workbook locks. An empty protection locks nothing, the same
    as none: Excel Online drops it on save."""
    p = wb.security
    return tuple(bool(p and getattr(p, k)) for k in ("lockStructure", "lockWindows",
                                                     "lockRevision"))


def _columns(ws):
    for key, dim in ws.column_dimensions.items():
        low = dim.min or column_index_from_string(key)
        for i in range(low, (dim.max or low) + 1):
            yield i, dim


def _hidden_rows(ws, _seen):
    pr = ws.sheet_properties.outlinePr
    return (bool(ws.sheet_format.zeroHeight), not pr or pr.summaryBelow is not False,
            {r: (bool(d.hidden), int(d.outlineLevel or 0))
             for r, d in ws.row_dimensions.items() if d.hidden or d.outlineLevel})


def _hidden_columns(ws, _seen):
    pr = ws.sheet_properties.outlinePr
    return (not pr or pr.summaryRight is not False,
            {i: (bool(d.hidden), int(d.outlineLevel or 0))
             for i, d in _columns(ws) if d.hidden or d.outlineLevel})


def _line_formats(ws, seen):
    """Each row's and column's own format. One that gives its cells nothing
    beyond the workbook's default format is left out: Excel Online drops it
    on save."""
    plain = _cell_look(Cell(ws), seen)
    rows = {r: _cell_look(d, seen) for r, d in ws.row_dimensions.items() if d.has_style}
    cols = {i: _cell_look(d, seen) for i, d in _columns(ws) if d.has_style}
    return ({r: k for r, k in rows.items() if k != plain},
            {i: k for i, k in cols.items() if k != plain})


def _merged(ws, _seen):
    return sorted(str(r) for r in ws.merged_cells.ranges)


def _ranges(sqref):
    return tuple(sorted(str(r) for r in sqref.ranges))


def _formatting(ws, _seen):
    rules = []
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            rules.append((rule.priority or 0, _ranges(cf.sqref),
                          _tree(rule.to_tree(), drop=("priority", "dxfId")),
                          _tree(rule.dxf.to_tree()) if rule.dxf else None))
    return [r[1:] for r in sorted(rules, key=lambda r: r[0])]


def _validations(ws, _seen):
    out = []
    for dv in ws.data_validations.dataValidation:
        out.append((_ranges(dv.sqref), _tree(dv.to_tree(), drop=("sqref",))))
    return sorted(out, key=repr)


def _panes(ws, _seen):
    pane = ws.sheet_view.pane
    if pane is None:
        return None
    return (pane.state or "split", float(pane.xSplit or 0), float(pane.ySplit or 0))


# A chart's copy of the cells it draws: Excel writes it again on every save,
# rounded its own way. The cell compare reads the cells themselves.
_CHART_CACHE = ("numCache", "strCache", "multiLvlStrCache")


def _charts(ws, _seen):
    out = []
    for chart in ws._charts:
        anchor = chart.anchor
        start, end = getattr(anchor, "_from", None), getattr(anchor, "to", None)
        size, pos = getattr(anchor, "ext", None), getattr(anchor, "pos", None)
        out.append((type(anchor).__name__ if not isinstance(anchor, str) else anchor,
                    (start.col, start.row) if start is not None else None,
                    (end.col, end.row) if end is not None else None,
                    (size.width, size.height) if size is not None else None,
                    (pos.x, pos.y) if pos is not None else None,
                    _tree(chart._write(), skip=_CHART_CACHE)))
    return out


def _sheet_protection(ws, _seen):
    p = ws.protection
    if not p.sheet:
        return None
    return tuple((k, bool(getattr(p, k))) for k in p.__attrs__ if k not in _PASSWORD)


def _filter(ws, _seen):
    f = ws.auto_filter
    if not (f.ref or f.filterColumn or f.sortState):
        return None
    return _tree(f.to_tree())


def _tables(ws, _seen):
    return sorted((_tree(t.to_tree(), drop=("id",)) for t in ws.tables.values()), key=repr)


def _view(ws, _seen):
    v = ws.sheet_view
    return (v.showGridLines is not False, v.showRowColHeaders is not False,
            v.showZeros is not False, bool(v.showFormulas), bool(v.rightToLeft),
            v.showOutlineSymbols is not False,
            None if v.defaultGridColor is not False else v.colorId)


def _print(ws, _seen):
    page, fit, opts, hf = (ws.page_setup, ws.sheet_properties.pageSetUpPr, ws.print_options,
                           ws.HeaderFooter)
    return (ws.print_area, ws.print_title_rows, ws.print_title_cols,
            "landscape" if page.orientation == "landscape" else "portrait",
            int(page.scale or 100), bool(fit and fit.fitToPage),
            1 if page.fitToWidth is None else int(page.fitToWidth),
            1 if page.fitToHeight is None else int(page.fitToHeight),
            bool(opts.gridLines), bool(opts.headings), bool(opts.horizontalCentered),
            bool(opts.verticalCentered),
            tuple(str(getattr(hf, k)) for k in ("oddHeader", "oddFooter", "evenHeader",
                                                 "evenFooter", "firstHeader", "firstFooter")),
            bool(hf.differentOddEven), bool(hf.differentFirst),
            tuple(sorted(b.id for b in ws.row_breaks.brk)),
            tuple(sorted(b.id for b in ws.col_breaks.brk)))


def _scenarios(ws, _seen):
    return _tree(ws.scenarios.to_tree()) if ws.scenarios else None


# Each sheet-level look, with the words a person reads it by.
_SHEET_LOOKS = (
    ("its visibility", lambda ws, _seen: ws.sheet_state),
    ("the hidden or grouped rows", _hidden_rows),
    ("the hidden or grouped columns", _hidden_columns),
    ("a whole row's or column's format", _line_formats),
    ("the merged cells", _merged),
    ("the conditional formatting", _formatting),
    ("the data validation", _validations),
    ("the frozen or split panes", _panes),
    ("a chart", _charts),
    ("the tab colour", lambda ws, _seen: _color_look(ws.sheet_properties.tabColor)),
    ("the sheet's protection", _sheet_protection),
    ("the filter", _filter),
    ("a table", _tables),
    ("the gridlines, headings or zeros shown", _view),
    ("the print setup", _print),
    ("the scenarios", _scenarios),
)


def _comment(cell):
    c = cell.comment
    return (c.text, c.author or None) if c is not None else None


def _link(cell):
    h = cell.hyperlink
    return (h.target, h.location, h.tooltip) if h is not None else None


def _raw(x):
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    with open(x, "rb") as fh:
        return fh.read()


# A part read by its bytes, in a person's words, by where it sits.
_PART_WORDS = (("xl/media/", "a picture"), ("xl/drawings/", "a picture, shape or text box"),
               ("xl/vbaProject", "the macros"), ("xl/pivot", "a pivot table"),
               ("xl/slicer", "a slicer"), ("xl/externalLinks/", "a link to another workbook"),
               ("xl/threadedComments/", "a threaded comment"))


def _part_words(part):
    return next((words for prefix, words in _PART_WORDS if part.startswith(prefix)),
                "a part of the file the check does not look inside")


def _found(sheet, cell, what):
    return {"sheet": sheet, "cell": cell, "what": what}


def look_difference(a, b, listed=()):
    """The first thing a person sees differ between workbooks `a` and `b`
    (paths or bytes), as `{sheet, cell, what}`: `what` names it in a
    person's words, and `sheet` and `cell` are None above their level. None
    when the two show the same thing. A part this cannot read adds
    `unread: True` and `what` says it could not be compared; a workbook that
    does not read at all gives all three None. On an openpyxl other than
    `LOOK_OPENPYXL` every pair reads as unread, `what` naming the version.

    `listed` is what `cell_changes` already names: the values of its cells
    and the sheets it adds or removes are left out, so the answer is what
    else changed. On a sheet where it adds or removes a row, the cells no
    longer line up by position, so only that sheet's own settings compare.

    It reads, through openpyxl:
      * the workbook: the sheets and their order, the defined names, the
        workbook's protection, whether its window and sheet tabs show, the
        colour palette, the date system;
      * each sheet: its visibility; its hidden rows and columns, their
        outline levels and where the outline puts its summary; a whole row's
        or column's format; merged ranges; conditional formatting, each range
        and its rules in order with their formats; data validation; frozen
        and split panes; each chart, by its anchor cells, its size when one
        cell anchors it, and all openpyxl reads of it; the tab colour; the
        protection, without its password; the filter; tables; gridlines,
        their colour, headings, zeros, formulas shown, right to left, outline
        symbols; the print area and titles, orientation, scale, fit to page,
        print options, headers and footers, page breaks; scenarios;
      * each cell: its value or formula (`_same_value`), a bold word inside
        it included; its format (`_cell_look`: number format, font, fill,
        border, alignment, protection, quote prefix); its comment's text and
        author; its link.
    It reads, from the package itself:
      * the theme's colours, fonts and effects;
      * which comments show on the sheet;
      * the metadata Excel keeps on a cell for a spilled formula or a picture
        in the cell;
      * every element of a sheet openpyxl drops (sparklines and other
        extensions, a background picture, form controls, ignored errors);
      * every part openpyxl does not read, by its bytes: a drawing that holds
        anything but charts (a picture, a shape, a text box), a VML part that
        holds anything but comment boxes, macros, slicers, pivot tables,
        external links, the cell metadata part. The same part with the same
        bytes is the same.
    A chart sheet, or any other sheet that is not a worksheet, is unread, and
    a chart a drawing anchors that openpyxl cannot read is a change.

    It leaves out: column widths, row heights and the defaults behind them;
    a chart's offsets inside its anchor cells; a comment box's size and
    place; the selection, the scroll position, the zoom, the view mode, the
    ruler, white space and the window lock; the active sheet and the
    window's size and place; custom views; paper size, margins, the first
    page number, page order, print quality and printer settings; named
    styles (each cell's own format is read); the chart-style gallery's parts
    and the series ids Excel writes in a chart; a chart's copy of the
    cells it draws; the cached results of formulas; the calculation chain
    and settings; phonetic settings and the remembered sort; code names,
    file properties, custom XML and the commenters list; the package's
    content types and links. Excel rewrites the XML of a workbook it only
    opened: the bytes move and nothing this reads does.
    """
    if openpyxl is None or openpyxl.__version__ != LOOK_OPENPYXL:
        have = openpyxl.__version__ if openpyxl is not None else "none"
        return {**_found(None, None, f"the look check needs openpyxl {LOOK_OPENPYXL}, and "
                                     f"this computer has {have}"), "unread": True}
    try:
        raws = [_raw(x) for x in (a, b)]
        packs = [_package(r) for r in raws]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            books = [openpyxl.load_workbook(io.BytesIO(r), rich_text=True) for r in raws]
        return _book_difference(books, packs, listed)
    except _Unread as exc:
        return {**_found(None, None, str(exc)), "unread": True}
    except Exception:  # unnamed: a caller keeps what the cells say
        return _found(None, None, None)


def _book_difference(books, packs, listed=()):
    (book_a, book_b), (pack_a, pack_b) = books, packs
    sheets = {m["sheet"] for m in listed if not m.get("cell") and not m.get("row")}
    cells = {(m["sheet"], m["cell"]) for m in listed if m.get("cell")}
    shifted = {m["sheet"] for m in listed if m.get("row")}
    for words, read in (("the sheets or their order",
                         lambda wb: [n for n in wb.sheetnames if n not in sheets]),
                        ("a defined name", _look_names),
                        ("the workbook's protection", _book_protection),
                        ("the workbook's visibility or its sheet tabs", _book_window),
                        ("the colour palette", lambda wb: list(wb._colors)),
                        ("the date system", lambda wb: wb.epoch)):
        if read(book_a) != read(book_b):
            return _found(None, None, words)
    if pack_a["theme"] != pack_b["theme"]:
        return _found(None, None, "the theme")
    if pack_a["unread"] != pack_b["unread"]:
        part = next(n for n in sorted(set(pack_a["unread"]) | set(pack_b["unread"]))
                    if pack_a["unread"].get(n) != pack_b["unread"].get(n))
        return _found(None, None, _part_words(part))
    seen_a, seen_b = {}, {}
    for name in (n for n in book_a.sheetnames if n not in sheets):
        ws_a, ws_b = book_a[name], book_b[name]
        side_a, side_b = pack_a["sheets"][name], pack_b["sheets"][name]
        if side_a["charts"] != len(ws_a._charts) or side_b["charts"] != len(ws_b._charts):
            return _found(name, None, f"a chart on {name}")
        for words, read in _SHEET_LOOKS:
            if read(ws_a, seen_a) != read(ws_b, seen_b):
                return _found(name, None, f"{words} on {name}")
        rows, cols = (0, 0) if name in shifted else _grid_size(ws_a, ws_b)
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                cell_a, cell_b = ws_a.cell(r, c), ws_b.cell(r, c)
                where = cell_a.coordinate
                for words, same in (
                        ("the value of", lambda: (name, where) in cells
                         or _same_value(cell_a, cell_b)),
                        ("the format of",
                         lambda: _cell_look(cell_a, seen_a) == _cell_look(cell_b, seen_b)),
                        ("the comment on", lambda: _comment(cell_a) == _comment(cell_b)),
                        ("the link on", lambda: _link(cell_a) == _link(cell_b))):
                    if not same():
                        return _found(name, where, f"{words} {where} on {name}")
        for key, words in (("extra", "a sparkline, form control or other sheet extra"),
                           ("notes", "which comments show"),
                           ("meta", "a spilled formula or a picture in a cell")):
            if side_a[key] != side_b[key]:
                return _found(name, None, f"{words} on {name}")
    return None


# ------------------------------------------------------------ the moves
LABEL_COL = 1  # column A holds each line's name in the monthly model


def _cell_text(v):
    """A cell's value for a before or after: a number stays a number, a
    formula is its text, a date its ISO form, a blank None."""
    if _blank(v):
        return None
    if isinstance(v, ArrayFormula):
        return v.text or str(v)
    if isinstance(v, DataTableFormula):
        return f"=TABLE({v.r1 or ''},{v.r2 or ''})"
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v if isinstance(v, (int, float, str)) else str(v)


def _label_left(ws, row, col):
    """The row's label: the text in its label column, left of `col`."""
    if col <= LABEL_COL:
        return None
    lab = ws.cell(row, LABEL_COL).value
    return lab.strip() if isinstance(lab, str) and lab.strip() else None


def _column_head(ws, row, col):
    """The column's heading: the nearest text or date above `row` in `col`,
    a date as its month. Text over 80 characters or on several lines is a
    note, not a heading, and the search stops at it. The label column has
    no heading: the text above a line's name is another line."""
    if col <= LABEL_COL:
        return None
    for r in range(row - 1, 0, -1):
        cell = ws.cell(r, col)
        v = cell.value
        if isinstance(v, (datetime, date)):
            return v.strftime("%b %Y")
        if isinstance(v, str) and v.strip() and not _is_formula(cell):
            text = v.strip()
            return text if len(text) <= 80 and "\n" not in text else None
    return None


def _row_match(ws, row, cols):
    """What a row is matched on: its label, or every value of a row with
    none."""
    lab = ws.cell(row, LABEL_COL).value
    if isinstance(lab, str) and lab.strip():
        return lab.strip()
    return ("",) + tuple(_cell_text(ws.cell(row, c).value) for c in range(1, cols + 1))


def _row_words(ws, row, cols):
    """A row added or removed, as a person reads it: its label, else its
    values."""
    lab = _label_left(ws, row, LABEL_COL + 1)
    if lab:
        return lab
    values = [str(_cell_text(ws.cell(row, c).value)) for c in range(1, cols + 1)
              if _cell_text(ws.cell(row, c).value) is not None]
    return ", ".join(values) or "a blank row"


def _cell_moves(sn, ws0, ws1, r0, r1, cols):
    out = []
    for c in range(1, cols + 1):
        c0, c1 = ws0.cell(r0, c), ws1.cell(r1, c)
        v0, v1 = c0.value, c1.value
        if _same_value(c0, c1):
            continue
        label = _label_left(ws1, r1, c) or _label_left(ws0, r0, c)
        head = _column_head(ws1, r1, c) or _column_head(ws0, r0, c)
        where = ws1.cell(r1, c).coordinate
        out.append({"what": ", ".join(x for x in (label, head) if x) or where,
                    "sheet": sn, "cell": where,
                    "was": _cell_text(v0), "now": _cell_text(v1)})
    return out


def _last_row(ws):
    """The last row that holds a value. A format on empty rows below the
    table makes them rows to openpyxl, not to a person."""
    for r in range(ws.max_row, 0, -1):
        if any(not _blank(c.value) for c in ws[r]):
            return r
    return 0


def _row_moves(sn, ws0, ws1):
    """One sheet's moves. The rows are matched first, on their labels
    (`_row_match`), so a row added or removed is one entry and every row
    below it compares with its own row. Cells compare within matched
    rows."""
    rows0, rows1 = _last_row(ws0), _last_row(ws1)
    cols = max(ws0.max_column, ws1.max_column)
    keys0 = [_row_match(ws0, r, cols) for r in range(1, rows0 + 1)]
    keys1 = [_row_match(ws1, r, cols) for r in range(1, rows1 + 1)]
    out = []
    for tag, i0, i1, j0, j1 in difflib.SequenceMatcher(None, keys0, keys1,
                                                       autojunk=False).get_opcodes():
        paired = min(i1 - i0, j1 - j0) if tag in ("equal", "replace") else 0
        for k in range(paired):
            out.extend(_cell_moves(sn, ws0, ws1, i0 + k + 1, j0 + k + 1, cols))
        for r in range(i0 + paired + 1, i1 + 1):
            out.append({"what": "Row removed", "sheet": sn, "cell": None, "row": r,
                        "was": _row_words(ws0, r, cols), "now": None})
        for r in range(j0 + paired + 1, j1 + 1):
            out.append({"what": "Row added", "sheet": sn, "cell": None, "row": r,
                        "was": None, "now": _row_words(ws1, r, cols)})
    return out


def cell_changes(a, b):
    """Every cell whose value or formula differs between two workbooks, the
    older `a` and the newer `b`, as `{what, sheet, cell, was, now}`. `what`
    names the row's label and the column's heading, as a person reads the
    sheet, else the cell. A row one sheet lacks is one entry, `Row added` or
    `Row removed` with its `row` (`_row_moves`). A sheet one workbook lacks
    is one entry. A chart sheet holds no cells to compare. The cached
    results of formulas are not read."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old, new = openpyxl.load_workbook(a), openpyxl.load_workbook(b)
    grids = {ws.title for ws in old.worksheets} & {ws.title for ws in new.worksheets}
    out = []
    for sn in new.sheetnames:
        if sn not in old.sheetnames:
            out.append({"what": f"The {sn} sheet", "sheet": sn, "cell": None,
                        "was": None, "now": "added"})
    for sn in old.sheetnames:
        if sn not in new.sheetnames:
            out.append({"what": f"The {sn} sheet", "sheet": sn, "cell": None,
                        "was": "there", "now": "removed"})
        elif sn in grids:
            out.extend(_row_moves(sn, old[sn], new[sn]))
    return out


def file_moves(a, b):
    """What moved from workbook `a` to `b`, in a person's words: `{moved}`,
    each from `cell_changes`. `look` names the first other thing that looks
    different (`look_difference`), next to the moved cells or alone. With
    neither, `reads_the_same` says only the bytes moved. `unread` says why
    the look was not compared, or with no cell moved, why nothing inside
    was. Never raises: it explains a change, and must not stand in the way
    of reporting one."""
    if openpyxl is None:
        return {"moved": [], "unread": "openpyxl is not installed"}
    try:
        moved = cell_changes(a, b)
        look = look_difference(a, b, listed=moved)
    except Exception as exc:
        return {"moved": [], "unread": f"the workbook did not read ({type(exc).__name__})"}
    if look is None:
        if moved:
            return {"moved": moved}
        if _has_external_links(a) or _has_external_links(b):
            return {"moved": [], "look": _found(None, None, "a link to another workbook")}
        return {"moved": [], "reads_the_same": True}
    if look.get("unread") or not look.get("what"):
        return {"moved": moved, "unread": look.get("what") or "the workbook did not read"}
    return {"moved": moved, "look": look}


def _col_empty(ws, c, rows):
    return all(_blank(ws.cell(r, c).value) for r in range(1, rows + 1))


def _dropped_result(path_a, path_b, book_b):
    """The first formula cell whose saved result the edited workbook lost.

    Excel saves each formula with its last calculated result, and that result
    is all the analysis engines read. A tool that writes formulas without
    calculating them (openpyxl, for one) keeps every formula but drops every
    result, so one edited figure blanks every subtotal -- Gross Profit, EBITDA,
    Net Income -- in every month. Compared against the frozen run, so a formula
    already blank there is not held.
    """
    try:
        calc_a = openpyxl.load_workbook(path_a, data_only=True)
        calc_b = openpyxl.load_workbook(path_b, data_only=True)
    except Exception:
        return "the workbook's calculated results could not be read"
    for name in book_b.sheetnames:
        ws_a, ws_b = calc_a[name], calc_b[name]
        for row in book_b[name].iter_rows():
            for cell in row:
                if (_is_formula(cell) and _blank(ws_b[cell.coordinate].value)
                        and not _blank(ws_a[cell.coordinate].value)):
                    return (f"it was saved without its calculated results ({name} "
                            f"{cell.coordinate} holds a formula with no saved result), "
                            "so every total would read as blank; open it in Excel and "
                            "save it, then run check again")
    return None


def _held_workbook_reason(path_a, path_b):
    """Why a changed workbook cannot be taken as a single reported figure.

    Conservative: structural changes, formula cells, changed labels or
    headers, cleared figures and text-in-figure cells are held for explicit
    user clarification rather than silently pinned as a source figure.
    Returns None only when every difference is a plain reported-figure
    value change (number to number, or a number filled into a previously
    blank cell of an existing row and column) and every formula result the
    frozen workbook saved is still saved. A workbook whose cells show no
    change at all, only its look, is not held: a run that consumes it
    delivers it as saved.

    Fail-closed: an unreadable file, a missing classifier, a defined-name
    change or an external-link presence is held, never treated as eligible.
    """
    if openpyxl is None:
        return "the workbook could not be classified (openpyxl unavailable)"
    try:
        book_a = openpyxl.load_workbook(path_a, data_only=False)
        book_b = openpyxl.load_workbook(path_b, data_only=False)
    except Exception:
        return "the workbook could not be opened as a workbook"
    if set(book_a.sheetnames) != set(book_b.sheetnames):
        return "a sheet was added or removed"
    if _defined_names(book_a) != _defined_names(book_b):
        return "a defined name was added, removed or repointed"
    if _has_external_links(path_a) or _has_external_links(path_b):
        return "the workbook references an external workbook link"
    for name in book_a.sheetnames:
        ws_a, ws_b = book_a[name], book_b[name]
        rows, cols = _grid_size(ws_a, ws_b)
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                cell_a, cell_b = ws_a.cell(r, c), ws_b.cell(r, c)
                a, b = cell_a.value, cell_b.value
                if _same_value(cell_a, cell_b):
                    continue
                coord = ws_a.cell(r, c).coordinate
                if _is_formula(cell_a) or _is_formula(cell_b):
                    return f"a formula cell changed ({name} {coord})"
                if c == LABEL_COL and isinstance(a, str) and not _blank(a):
                    return f"a label cell changed ({name} {coord})"
                if _blank(b) and not _blank(a):
                    return f"a figure was cleared ({name} {coord})"
                if not ((_num(a) or _blank(a)) and (_num(b) or _blank(b))):
                    return f"text was typed in a figure cell ({name} {coord})"
                if _blank(a) and not _blank(b):
                    row_empty = all(_blank(ws_a.cell(r, cc).value) for cc in range(1, cols + 1))
                    if row_empty:
                        return f"a new row was added ({name} row {r})"
                    if _col_empty(ws_a, c, rows):
                        return f"a new column was added ({name} column {c})"
                    continue
                if _num(a) and _num(b):
                    continue
                return f"text was typed in a figure cell ({name} {coord})"
    return _dropped_result(path_a, path_b, book_b)


class _Text(HTMLParser):
    BLOCKS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.skip += 1
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template") and self.skip:
            self.skip -= 1
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def visible_lines(raw):
    """Memo as a reader sees it: tags gone, entities decoded, whitespace normalised."""
    p = _Text()
    p.feed(raw)
    p.close()
    text = html.unescape("".join(p.parts))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def same_memo_content(path_a, path_b):
    """True when two HTML memos carry the same visible text."""
    try:
        with open(path_a, encoding="utf-8", errors="replace") as fa:
            a = visible_lines(fa.read())
        with open(path_b, encoding="utf-8", errors="replace") as fb:
            b = visible_lines(fb.read())
    except OSError:
        return False
    return a == b


def same_content(path_a, path_b):
    """Whether two copies of one file carry the same content.

    - .xlsx/.xlsm: same cells (values/formulas) and look, ignoring container repackaging
    - .html/.htm: same visible text, ignoring tags/whitespace
    - else: raw bytes equal
    """
    if not os.path.exists(path_a) or not os.path.exists(path_b):
        return False
    # Fast path: bytes equal -> content equal
    try:
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            if fa.read() == fb.read():
                return True
    except OSError:
        return False
    ext = os.path.splitext(str(path_a))[1].lower()
    # Use the extension of either path; they should match but be lenient
    if ext not in (".xlsx", ".xlsm", ".html", ".htm"):
        ext = os.path.splitext(str(path_b))[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return same_workbook_content(path_a, path_b)
    if ext in (".html", ".htm"):
        return same_memo_content(path_a, path_b)
    # Unknown type: bytes already differed -> not same
    return False


def content_changed(frozen_path, edited_path):
    """Classify an edited file against its frozen copy.

    Returns "same" (bytes equal), "same_content" (repackaging only),
    "changed" (real edit), or "missing" (edited file not there).
    """
    if not os.path.exists(edited_path):
        return "missing"
    if not os.path.exists(frozen_path):
        return "changed"
    try:
        with open(frozen_path, "rb") as fa, open(edited_path, "rb") as fb:
            if fa.read() == fb.read():
                return "same"
    except OSError:
        return "changed"
    if same_content(frozen_path, edited_path):
        return "same_content"
    return "changed"
