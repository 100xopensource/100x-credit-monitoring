#!/usr/bin/env python3
"""
Revenue-quality analysis module for the credit portfolio monitor.

Tests the recurring-revenue base directly: ARR bridge, gross/net revenue
retention and logo retention (organic vs combined when an acquisition/basis
break exists), a churned/at-risk customer register, a deferred-revenue walk
tied to the balance sheet, and (when the covenant-spec carries an ARR-
denominated ratio covenant) a covenant stress grid against certified
Indebtedness/ARR.

DESIGN
======
* THE AGENT SUPPLIES THE FILES. This engine is handed the three monthly inputs
  it reads -- the ARR / retention workbook, the deferred-revenue schedule and
  the compliance certificate -- as `--retention-file`,
  `--deferred-revenue-file` and `--certificate-file`. It does not look for
  them: no filename patterns, no month-folder conventions, no directory
  listing. The calling agent has already opened the borrower's reporting folder
  for the period and seen which file is which. Every borrower's folders are
  shaped differently, so the agent settles that once, where it can see the
  files and ask; the engine reads exactly what it was given.
    Pass each path AS CONFIGURED -- the same string as in borrower-config. It
  is read through ml.resolve_path and recorded verbatim in `source_files`.
* Borrower-agnostic, like ap_ar_aging.py and budget_vs_actual.py. NO sheet name
  or chart-of-account label is hardcoded.
* Format sniffing, no format assumed: .xlsx (openpyxl), SpreadsheetML / Excel
  2003 XML (ml.read_spreadsheetml -- same reader ap_ar_aging.py uses), plain
  text, and PDF recognized AS a PDF. Format is read off the file's own bytes, so
  an export that mislabels its extension -- NetSuite ships SpreadsheetML XML as
  .xls -- still reads. This module has no PDF-parsing dependency: a certificate
  delivered as a PDF is read through an extracted-text sidecar (.txt, same stem)
  and reports as unavailable without one, rather than having its bytes regexed.
* Every worksheet is tried, and month headers resolve through the shared
  ml.month_label reader, so detail on a later tab and columns headed with real
  date cells or full month names read the same as "May 2026" on tab one.
* INPUT-GATED: every section is independently gated on its own input(s). A file
  the agent did not supply, a file that has not arrived, or a parse error DOES
  NOT crash the module -- the section is marked unavailable, an exception entry
  is emitted, and every other section still computes from what IS available.
  Same philosophy as the other engines: skip and flag, never crash.
* Balance-sheet deferred-revenue and model recurring-revenue lines are read
  from the canonical monthly financials model THROUGH monitor_lib -- this
  module never re-derives model figures.
* Covenant stress grid activates only when covenant-spec.json carries a
  max_ratio covenant whose denominator resolves to an ARR/recurring-revenue
  definition (matched by definition name / ref, not hardcoded to one
  borrower's covenant id).
* Deterministic: same inputs -> same outputs. No randomness, no wall-clock
  dependence besides the requested period.
* PROVENANCE (trace-to-file + calculation records): output is
  the standard conclusion envelope (monitor_lib.conclusion) -- ISO "YYYY-MM"
  period, `results` as a list of section records, and a top-level deduped
  `source_files` path list. Every memo-surfaceable record (ARR bridge,
  retention metrics, each churn-register entry, DR walk + BS tie-out,
  covenant stress grid, tie-outs) carries a structured `source_ref`
  (file/sheet/row/cells indexing into `source_files`; paths recorded AS
  CONFIGURED, pre-sandbox-resolution, so downstream consumers can join store
  hyperlinks) plus a plain-text `calc` note saying how the figure was
  arrived at. validate_output.py gates the output before archiving.

Usage (matches the siblings):
    python revenue_quality.py --config <borrower-config.json> --period 2026-05 \
        [--retention-file <ARR / retention workbook>] \
        [--deferred-revenue-file <deferred-revenue schedule>] \
        [--certificate-file <compliance certificate>] \
        [--out <output .json>]

`--period` is "YYYY-MM" for the test/reporting month (maps to the "Mon YYYY"
model month label). Each file flag is optional: withhold one and its section
reports as unavailable, which is the same behavior as a file that had not
arrived that month.
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

# --- Resolve the shared lib robustly: walk up from this file to the plugin
# root, then lib/ (same pattern as the other reference engines). -------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_LIB_DIR = os.path.join(_PLUGIN_ROOT, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import monitor_lib as ml  # noqa: E402
import rows_emit  # noqa: E402
import covenant_compliance as cc  # noqa: E402

import openpyxl  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

# ===========================================================================
# Constants / config-driven defaults
# ===========================================================================

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_LABEL_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")

# Tie-out tolerance (dollars). Consistent with ap_ar_aging.py's TIE_TOL.
DEFAULT_TIE_TOLERANCE = 1.0

# Default model line labels, overridable via module_inputs.revenue-quality /
# borrower-config "model_lines" block (borrower model layouts vary -- e.g. the
# One model may use "Deferred Revenue" / "Recurring Revenue" while a numbered
# chart-of-accounts model uses "2xxx - Deferred Revenue"). Resolution tries
# the configured/default label first, then falls back to Model.find_row with
# a permissive regex so the module still degrades gracefully rather than
# crashing on a labeling difference.
DEFAULT_MODEL_LINES = {
    "deferred_revenue_bs": "Deferred Revenue",
    "recurring_revenue_is": "Recurring Revenue",
    "indebtedness_bs": None,   # if unset, summed from debt_lines below
}
DEFAULT_DEBT_LINES = ["Long-Term Debt (Current)", "Long-Term Debt (LT)"]

# Recurring-revenue annualization convention: monthly recurring revenue x 12.
ANNUALIZATION_MULTIPLE = 12

# At-risk contraction threshold (fraction of a customer's opening ARR lost,
# short of full churn) used to flag a customer as "at-risk" in the churn
# register when the source file gives no explicit status/flag column.
AT_RISK_CONTRACTION_PCT = 0.20


# ===========================================================================
# Config-driven auto-discovery (reporting_packages_folder -> monthly files)
# ===========================================================================
def _month_label_from_period(period):
    """'2026-05' -> 'May 2026'; 'Mon YYYY' passes through unchanged."""
    m = re.match(r"^(\d{4})-(\d{2})$", str(period).strip())
    if m:
        year, mnum = int(m.group(1)), int(m.group(2))
        return f"{_MONTH_ABBR[mnum - 1]} {year}"
    if _MONTH_LABEL_RE.match(str(period).strip()):
        return str(period).strip()
    raise ValueError(f"unrecognized period: {period!r} (expected 'YYYY-MM')")


def _prior_month_label(month_label):
    """One calendar month before `month_label` ('Jan 2025' -> 'Dec 2024')."""
    return ml.trailing_months(month_label, 2)[0]


# ===========================================================================
# Format sniffing / generic tabular readers
# ===========================================================================
def _sniff_format(path):
    """Return one of 'xlsx', 'spreadsheetml', 'pdf', 'text', or 'unknown' by
    content, not just extension (borrower exports mislabel extensions -- e.g.
    NetSuite SpreadsheetML XML shipped with a .xls extension).

    A PDF gets its OWN format rather than falling through to 'text': its bytes
    read as text produce stray digits that a certificate regex will happily
    match, publishing an invented figure as borrower-certified. Named as a pdf,
    the certificate reader looks for an extracted-text sidecar instead."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        if head[:2] == b"PK":
            return "xlsx"
        if head[:4] == b"%PDF":
            return "pdf"
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            head_txt = fh.read(2048)
        if "<?xml" in head_txt or "urn:schemas-microsoft-com:office:spreadsheet" in head_txt:
            return "spreadsheetml"
        if ext in (".txt",):
            return "text"
        if ext in (".xlsx", ".xlsm"):
            return "xlsx"
        if ext in (".xls",):
            return "spreadsheetml"
        return "text" if head_txt.strip() else "unknown"
    except (OSError, UnicodeDecodeError):
        return "unknown"


def read_sheets(path):
    """Every worksheet of a tabular source, in workbook order, as
    [(rows, sheet_name), ...] — rows is a list of string/number rows
    (list-of-lists; index i is worksheet row i+1, so source refs can carry
    1-based row numbers), sheet_name is None for a plain-text source.

    Borrower workbooks often keep the detail on a later tab behind a cover or
    summary tab, so the parsers try each tab rather than only the first. Raises
    on genuinely unreadable input; callers catch and gate."""
    fmt = _sniff_format(path)
    if fmt == "xlsx":
        wb = openpyxl.load_workbook(path, data_only=True)
        out = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows = [[ws.cell(row=r, column=c).value
                     for c in range(1, ws.max_column + 1)]
                    for r in range(1, ws.max_row + 1)]
            out.append((rows, ws.title))
        return out
    if fmt == "spreadsheetml":
        sheets = ml.read_spreadsheetml(path)
        if not sheets:
            raise ValueError(f"no worksheet in {path}")
        return [(list(rows), name) for name, rows in sheets.items()]
    if fmt == "text":
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return [([[line.rstrip("\n")] for line in fh], None)]
    if fmt == "pdf":
        raise ValueError(
            f"{path} is a PDF; this module reads spreadsheets and extracted text. "
            "Hand it the exported workbook, or an extracted-text sidecar (.txt) "
            "beside the PDF with the same name.")
    raise ValueError(f"unrecognized/unreadable format for {path}")


def read_table_rows(path):
    """The FIRST worksheet of a tabular source as (rows, sheet_name) — the
    single-sheet read, for callers that want exactly one grid."""
    return read_sheets(path)[0]


_norm = ml.norm_label


# ===========================================================================
# Retention Analysis parsing (customer-level ARR, >=2 month columns)
# ===========================================================================
_NAME_HEADER_TOKENS = ("customer", "account", "client", "logo")
_COHORT_HEADER_TOKENS = ("cohort", "acquired", "source", "basis", "entity")
_STATUS_HEADER_TOKENS = ("status", "reason", "notes", "flag")

# Worksheet names that say a tab holds the retention grid itself. A
# revenue-by-customer dump is customer x month too, so shape alone picks it over
# the ARR waterfall when the dump comes first in the book (it does, on real
# files) — the tab whose name says retention is tried before the ones that only
# have the shape.
_RETENTION_SHEET_TOKENS = ("retention", "waterfall", "churn", "arr", "nrr", "grr")


def _retention_sheets_first(sheets):
    """`read_sheets` output reordered so retention-named tabs come first, each
    group in workbook order. Name is a preference, not a filter: a named tab
    still has to carry one of the two shapes to win."""
    named, rest = [], []
    for rows, sheet in sheets:
        low = _norm(sheet or "")
        target = named if any(tok in low for tok in _RETENTION_SHEET_TOKENS) else rest
        target.append((rows, sheet))
    return named + rest


def _month_header_candidates(rows):
    """Every row that heads columns with months, as [(row index, {col: month})],
    best first — most month cells wins, earliest row breaks a tie.

    A bare date in a title or as-of row resolves to a month like any other cell,
    so the row carrying the real month axis is chosen on the evidence of how many
    months it holds rather than by being the first one seen."""
    cands = []
    for i, r in enumerate(rows):
        if not r:
            continue
        cand = {}
        for ci, cell in enumerate(r):
            if cell is None:
                continue
            mlabel = ml.month_label(cell)
            if mlabel:
                cand[ci] = mlabel
        if cand:
            cands.append((i, cand))
    cands.sort(key=lambda ic: (-len(ic[1]), ic[0]))
    return cands


def parse_retention_analysis(path):
    """Parse a Retention Analysis export.

    Tries two shapes, in order (borrower-agnostic; only the SHAPE is assumed,
    not exact headers or which shape a given borrower uses):

      1. Customer-level: one header row with a customer-name column, one or
         more month columns labeled 'Mon YYYY', and optionally a
         cohort/acquired-flag column and a status/reason column.
      2. Entity/metric roll-up: no customer-name column at all -- column A
         carries ARR-bridge METRIC labels (Beginning ARR, (+) New, (+)
         Upsell, (-) Downsell, (-) Churn, (+) Acquired ARR, (-) Adjustments,
         Ending ARR, ...) and the data columns are still 'Mon YYYY' months.
         This is the shape actually produced by some borrowers' finance
         teams (an entity-level bridge, not a per-customer grid) and is
         parsed into one synthetic pseudo-"customer" bridge per (mapped)
         metric so the same build_bridge()/retention_metrics() math applies
         uniformly downstream -- see `_ROLLUP_ARR_BASIS` below for how the
         bridge legs are recovered directly from the labeled rows instead of
         customer deltas.

    Returns:
        {
          "months": ["Apr 2026", "May 2026", ...] (as found, chronological),
          "customers": [
             {"customer": str, "arr_by_month": {month: float|None},
              "cohort": str|None, "status": str|None,
              "row": int (1-based source row, for source refs)}, ...
          ],
          "rollup": None | {  # present only for shape 2
             "months": [...], "opening": {month: v}, "new": {month: v},
             "expansion": {month: v}, "contraction": {month: v},
             "churn": {month: v}, "closing": {month: v},
             "leg_rows": {leg: first 1-based source row},      # for source refs
             "leg_all_rows": {leg: [every row that fed it]},   # movements can
                                                               # span rows
          },
          # provenance for source refs:
          "sheet": str|None, "header_row": int,
          "month_cols": {month_label: column_letter},
        }
    Every tab of the workbook is tried, retention-named tabs first: a
    revenue-by-customer dump is customer x month as well, so on a workbook that
    carries both, the tab whose name says retention is the retention grid.
    Month columns are resolved through the shared `monitor_lib.month_label`
    reader, so a file that heads its columns with real date cells or full month
    names ("January 2026") parses the same as one using "May 2026", and the row
    holding the most months is taken as the axis so a bare as-of date in a title
    row cannot pass for it. The customer-name header may sit on a DIFFERENT row
    from the month headers — a two-row header is common — so it is looked for
    within a few rows of the month row, and cohort/status headers are read off
    both rows.

    Raises ValueError if no header row / no month columns can be located.
    """
    sheets = _retention_sheets_first(read_sheets(path))
    # Customer-level detail anywhere in the workbook beats a roll-up on the
    # first tab, so shape 1 gets every tab before shape 2 is tried.
    for rows, sheet in sheets:
        parsed = _parse_retention_customers(rows, sheet)
        if parsed is not None:
            return parsed
    for rows, sheet in sheets:
        rollup = _parse_retention_rollup(rows)
        if rollup is not None:
            return {"months": rollup["months"], "customers": [], "rollup": rollup,
                    "sheet": sheet, "header_row": rollup.get("header_row"),
                    "month_cols": rollup.get("month_cols") or {}}
    raise ValueError(f"could not locate a customer/month header row in {path}")


def _name_header_col(rows, month_idx, month_cols, look=3):
    """(column, row index) of the customer-name header for a month-header row:
    the name token on that same row, else on a row within `look` rows of it
    (exports commonly split the header — month labels on one row, "Customer"
    over the label column on another). (None, None) when none is nearby."""
    order = [month_idx] + [month_idx + d for d in range(1, look + 1)] \
                        + [month_idx - d for d in range(1, look + 1)]
    for i in order:
        if i < 0 or i >= len(rows) or not rows[i]:
            continue
        for ci, cell in enumerate(rows[i]):
            if cell is None or ci in month_cols:
                continue
            if any(tok in _norm(cell) for tok in _NAME_HEADER_TOKENS):
                return ci, i
    return None, None


def _parse_retention_customers(rows, sheet):
    """Shape 1 (customer-level ARR grid) for ONE worksheet, or None if this
    sheet does not carry that shape."""
    header_idx, header = None, None
    month_cols = {}  # col_idx -> month_label
    name_col = None
    name_row = None
    cohort_col = None
    status_col = None

    for i, candidate_months in _month_header_candidates(rows):
        candidate_name_col, candidate_name_row = _name_header_col(rows, i, candidate_months)
        if candidate_name_col is not None:
            header_idx, header = i, rows[i]
            month_cols = candidate_months
            name_col, name_row = candidate_name_col, candidate_name_row
            # Data starts below BOTH header rows when the header is split.
            data_idx = max(i, name_row)
            break

    if header_idx is not None:
        # A split header puts the cohort and status columns on whichever row
        # carries the names, not on the month row, so both rows are scanned —
        # otherwise status never binds and at-risk / churned classification
        # falls back to bare ARR deltas instead of the file's own status text.
        header_rows = [header] if name_row == header_idx else [header, rows[name_row]]
        for hrow in header_rows:
            for ci, cell in enumerate(hrow):
                if cell is None or ci in month_cols or ci == name_col:
                    continue
                low = _norm(cell)
                if cohort_col is None and any(tok in low for tok in _COHORT_HEADER_TOKENS):
                    cohort_col = ci
                elif status_col is None and any(tok in low for tok in _STATUS_HEADER_TOKENS):
                    status_col = ci

        months_sorted = sorted(month_cols.items(), key=lambda kv: _month_sort_key(kv[1]))

        customers = []
        for rnum, r in enumerate(rows[data_idx + 1:], start=data_idx + 2):
            if not r or name_col >= len(r):
                continue
            name = r[name_col]
            if name is None or _norm(name) in ("", "total", "grand total"):
                continue
            name = re.sub(r"\s+", " ", str(name)).strip()
            arr_by_month = {}
            for ci, mlabel in months_sorted:
                v = ml.safe_float(r[ci]) if ci < len(r) else None
                arr_by_month[mlabel] = v if v is not None else 0.0
            cohort = None
            if cohort_col is not None and cohort_col < len(r) and r[cohort_col] is not None:
                cohort = str(r[cohort_col]).strip()
            status = None
            if status_col is not None and status_col < len(r) and r[status_col] is not None:
                status = str(r[status_col]).strip()
            customers.append({
                "customer": name, "arr_by_month": arr_by_month,
                "cohort": cohort, "status": status, "row": rnum,
            })

        return {"months": [m for _, m in months_sorted], "customers": customers,
                "rollup": None, "sheet": sheet, "header_row": header_idx + 1,
                "month_cols": {m: get_column_letter(ci + 1) for ci, m in months_sorted}}

    return None


# Metric-label -> bridge-leg token map for the entity/metric roll-up shape.
# Matched by substring against the normalized column-A label, so borrower
# wording variants ("(+) New", "New Logos", "New ARR") all resolve the same
# way; not tied to one borrower's exact labels.
_ROLLUP_LEG_TOKENS = {
    "opening": ("beginning arr", "opening arr", "beginning balance"),
    "new": ("(+) new", "new logo", "new arr", "new business"),
    "expansion": ("upsell", "expansion", "(+) upsize"),
    "contraction": ("downsell", "contraction", "(-) downsize"),
    "churn": ("(-) churn", "churn", "cancellation"),
    "acquired": ("acquired arr", "(+) acquired"),
    "adjustments": ("adjustment", "true-up", "true up"),
    "closing": ("ending arr", "closing arr", "ending balance"),
}

# Legs that are MOVEMENTS in the month: several rows can each state part of one
# ("(-) Churn" and "(-) Strategic Churn" are both churn), so these add up every
# row that names them. Opening and closing are balances — a level, stated once —
# so there the first row that gives the month stands.
_ROLLUP_FLOW_LEGS = ("new", "expansion", "contraction", "churn", "acquired",
                     "adjustments")

# A row stating a rate rather than a dollar movement ("Churn %", "NRR rate").
# Its label carries a leg token, and its value is a share, so it is read as
# neither the leg nor part of it. Whole words only — "Strategic Churn" is a
# churn row, and "strategic" has "rate" sitting inside it.
_ROLLUP_RATIO_RE = re.compile(r"%|\bpercent|\bpct\b|\brate\b|\bratio\b")


def _parse_retention_rollup(rows):
    """Detect and parse an entity/metric roll-up Retention Analysis (no
    customer column; column A holds ARR-bridge metric labels, data columns
    are 'Mon YYYY'). Returns the rollup dict or None if this shape isn't
    present either (caller then raises the original "no header row" error).
    """
    # Each month-bearing row is tried as the axis, best first, and the one whose
    # rows below actually carry bridge legs is the one kept — a title row holding
    # a single bare date parses as a month too, and claiming it as the axis used
    # to fail the whole parse with the real axis sitting untried underneath.
    for header_idx, month_cols in _month_header_candidates(rows):
        rollup = _rollup_from_header(rows, header_idx, month_cols)
        if rollup is not None:
            return rollup
    return None


def _label_col_before(r, boundary_col):
    """Column index of a row's own label cell: the LAST non-None cell
    strictly to the left of `boundary_col` (where the row's numeric/date
    data starts). Borrower exports commonly carry one or more blank margin
    columns before the label ("column A" is not a safe assumption) -- e.g.
    a sheet whose real label sits in column B with column A blank
    throughout. Falls back to column 0 when nothing is found (row genuinely
    has no label), matching the old hardcoded behavior for a file that
    really does put the label in column A."""
    for ci in range(min(boundary_col, len(r)) - 1, -1, -1):
        if r[ci] is not None:
            return ci
    return 0


def _rollup_from_header(rows, header_idx, month_cols):
    """The roll-up dict read against ONE candidate month-header row, or None when
    that row heads no ARR-bridge legs."""
    months_sorted = sorted(month_cols.items(), key=lambda kv: _month_sort_key(kv[1]))
    first_month_col = min(month_cols) if month_cols else 0
    legs = {key: {} for key in _ROLLUP_LEG_TOKENS}
    leg_rows = {}        # leg -> first row that fed it (ordering + refs)
    leg_all_rows = {}    # leg -> every row that fed it, in file order
    found_any_leg = False

    for rnum, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not r:
            continue
        label_col = _label_col_before(r, first_month_col)
        raw_label = r[label_col] if label_col < len(r) else None
        label = _norm(raw_label) if raw_label is not None else ""
        if not label:
            continue
        if _ROLLUP_RATIO_RE.search(label):
            continue
        matched_leg = None
        for leg, tokens in _ROLLUP_LEG_TOKENS.items():
            if any(tok in label for tok in tokens):
                matched_leg = leg
                break
        if matched_leg is None:
            continue
        for ci, mlabel in months_sorted:
            v = ml.safe_float(r[ci]) if ci < len(r) else None
            if v is None:
                continue
            if matched_leg in _ROLLUP_FLOW_LEGS:
                legs[matched_leg][mlabel] = round(
                    legs[matched_leg].get(mlabel, 0.0) + v, 2)
            elif mlabel in legs[matched_leg]:
                continue
            else:
                legs[matched_leg][mlabel] = v
            leg_rows.setdefault(matched_leg, rnum)
            fed_by = leg_all_rows.setdefault(matched_leg, [])
            if rnum not in fed_by:
                fed_by.append(rnum)
            found_any_leg = True
        # A bridge ends at its own closing balance. Blocks below it restate the
        # same movements as customer COUNTS ("(+) New (LTM)" = 5 logos) and as
        # LTM percentages, under labels carrying the same leg tokens — reading
        # on would add logos and shares into dollar legs.
        if matched_leg == "closing" and legs["opening"]:
            break

    if not found_any_leg or not legs["opening"]:
        return None

    return {
        "months": [m for _, m in months_sorted],
        "opening": legs["opening"],
        "new": legs["new"],
        "expansion": legs["expansion"],
        "contraction": legs["contraction"],
        "churn": legs["churn"],
        "acquired": legs["acquired"],
        "adjustments": legs["adjustments"],
        "closing": legs["closing"],
        "header_row": header_idx + 1,
        "month_cols": {m: get_column_letter(ci + 1) for ci, m in months_sorted},
        "leg_rows": leg_rows,
        "leg_all_rows": leg_all_rows,
    }


def build_bridge_from_rollup(rollup, opening_month, closing_month, only_organic_exclude_cohort=False):
    """Build an ARR bridge directly from a labeled entity/metric roll-up
    (Shape 2 of parse_retention_analysis), for the closing_month column.

    The roll-up gives each bridge leg (new/expansion/contraction/churn/
    acquired) as that MONTH's own movement (not opening->closing deltas), so
    unlike build_bridge() (which derives legs from customer-level deltas
    between two snapshots) this reads the closing_month column's legs
    directly and uses the SAME month's own opening/closing balance rows.

    Which basis the file's OWN closing row is on is settled by the file's own
    arithmetic rather than assumed: when opening plus every leg INCLUDING
    acquired ARR ties that row, the row is combined, so the organic closing is
    that row less acquired ARR and the combined closing stands as printed. When
    the legs tie it WITHOUT acquired, the row is organic, so the organic closing
    stands and the combined closing adds acquired ARR. Either way the acquired
    leg is folded into "new" on the combined basis, so the combined bridge
    foots -- mirroring the per-customer path's organic/combined split without
    needing customer-level cohort tags.
    Returns (bridge_dict, logos, deltas) with logos/deltas as None (not
    derivable from an entity-level file -- callers must treat logo retention
    and the churn register as unavailable/derived-elsewhere for this shape).
    """
    def _v(leg, month):
        return rollup.get(leg, {}).get(month, 0.0) or 0.0

    opening = _v("opening", closing_month)
    new = _v("new", closing_month)
    expansion = _v("expansion", closing_month)
    contraction = _v("contraction", closing_month)  # file stores as negative
    churn = _v("churn", closing_month)              # file stores as negative
    adjustments = _v("adjustments", closing_month)
    acquired = _v("acquired", closing_month)
    closing = _v("closing", closing_month)

    # Let the file's arithmetic say which basis its closing row is on.
    organic_legs = opening + new + expansion + contraction + churn + adjustments
    closing_includes_acquired = bool(acquired) and abs(
        organic_legs + acquired - closing) <= DEFAULT_TIE_TOLERANCE
    if closing_includes_acquired:
        if only_organic_exclude_cohort:
            closing = closing - acquired
        else:
            new = new + acquired
    elif acquired and not only_organic_exclude_cohort:
        new = new + acquired
        closing = closing + acquired

    bridge = {
        "opening": round(opening, 2),
        "new": round(new, 2),
        "expansion": round(expansion, 2),
        "contraction": round(contraction if contraction <= 0 else -contraction, 2),
        "churn": round(churn if churn <= 0 else -churn, 2),
        "adjustments": round(adjustments, 2),
        "closing": round(closing, 2),
        "derived": False,
        "closing_row_includes_acquired": closing_includes_acquired,
    }
    footed = round(bridge["opening"] + bridge["new"] + bridge["expansion"]
                   + bridge["contraction"] + bridge["churn"]
                   + bridge["adjustments"] - bridge["closing"], 2)
    bridge["foots"] = abs(footed) <= DEFAULT_TIE_TOLERANCE
    bridge["foot_difference"] = footed
    bridge["derivation_note"] = (
        "Derived from an entity/metric roll-up Retention Analysis (no customer-level "
        "detail in the source file) -- bridge legs read directly from the file's own "
        "labeled ARR-bridge rows for the closing month, not from customer deltas. "
        + ("The file's own closing row is on the COMBINED basis (its legs tie only "
           "with acquired ARR included), so the organic closing is that row less "
           "acquired ARR. " if closing_includes_acquired else "")
        + "Logo retention and a named churn register are NOT derivable from this file "
        "shape (no customer names given)."
    )
    return bridge, None, None


def _month_sort_key(label):
    y, m = ml._parse_label(label)
    return (y, m)


def _ref_fields(**fields):
    """Drop None-valued detail fields from a source ref (a text source has no
    sheet; a row may be unknown) so refs stay compact and honest."""
    return {k: v for k, v in fields.items() if v is not None}


def _is_acquired_cohort(cohort_text, acquisition_note):
    """Heuristic, config-informed acquired-cohort detector: true if the
    cohort/source column value looks like an acquisition tag. Matches common
    tokens ('acquired' or an acquired-entity name embedded in
    deal-context's basis_break_note) rather than one hardcoded name."""
    if not cohort_text:
        return False
    low = _norm(cohort_text)
    if "acqui" in low or "combined" in low or "inorganic" in low:
        return True
    if acquisition_note:
        # pull bracketed/parenthetical entity names out of the note, e.g.
        # "Combined (Example+Aquired)" -> tokens {"example","acquired"}
        tokens = re.findall(r"[A-Za-z][A-Za-z.&\-]{2,}", acquisition_note)
        for tok in tokens:
            if _norm(tok) and _norm(tok) in low and _norm(tok) not in ("combined", "organic"):
                return True
    return False


# ===========================================================================
# ARR bridge + retention math (from parsed customer-level snapshots)
# ===========================================================================
def build_bridge(customers, opening_month, closing_month, only_organic_exclude_cohort=False,
                  acquisition_note=None):
    """Derive an ARR bridge (opening/new/expansion/contraction/churn/closing)
    from customer-level ARR deltas between two month snapshots.

    When `only_organic_exclude_cohort` is True, customers whose cohort field
    looks acquired (per `_is_acquired_cohort`) are excluded entirely -- this
    produces the ORGANIC bridge. Otherwise all customers are included --
    the COMBINED bridge.

    Returns (bridge_dict, logo_counts, per_customer_deltas).
    """
    opening_total = new_total = expansion_total = contraction_total = churn_total = closing_total = 0.0
    logos_opening = logos_closing = logos_new = logos_churned = 0
    deltas = []

    for c in customers:
        if only_organic_exclude_cohort and _is_acquired_cohort(c.get("cohort"), acquisition_note):
            continue
        o = c["arr_by_month"].get(opening_month, 0.0) or 0.0
        n = c["arr_by_month"].get(closing_month, 0.0) or 0.0
        opening_total += o
        closing_total += n
        if o > 0:
            logos_opening += 1
        if n > 0:
            logos_closing += 1
        if o <= 0 and n > 0:
            new_total += n
            logos_new += 1
        elif o > 0 and n <= 0:
            churn_total += o  # store as positive dollars lost
            logos_churned += 1
        elif n > o:
            expansion_total += (n - o)
        elif n < o:
            contraction_total += (o - n)  # positive dollars lost
        deltas.append({"customer": c["customer"], "opening": round(o, 2),
                       "closing": round(n, 2), "delta": round(n - o, 2),
                       "cohort": c.get("cohort"), "status": c.get("status"),
                       "row": c.get("row")})

    bridge = {
        "opening": round(opening_total, 2),
        "new": round(new_total, 2),
        "expansion": round(expansion_total, 2),
        "contraction": round(-contraction_total, 2),   # signed negative
        "churn": round(-churn_total, 2),                # signed negative
        "closing": round(closing_total, 2),
        "derived": True,
    }
    footed = round(bridge["opening"] + bridge["new"] + bridge["expansion"]
                   + bridge["contraction"] + bridge["churn"] - bridge["closing"], 2)
    bridge["foots"] = abs(footed) <= DEFAULT_TIE_TOLERANCE
    bridge["foot_difference"] = footed

    logos = {
        "opening": logos_opening, "closing": logos_closing,
        "new": logos_new, "churned": logos_churned,
    }
    return bridge, logos, deltas


def retention_metrics(bridge, logos):
    """GRR / NRR / logo retention from a bridge + logo counts. None if opening
    base is zero (avoid divide-by-zero on a brand-new / all-organic-excluded
    book). `logos` is None when the source file has no customer-level detail
    (entity/metric roll-up shape) -- logo retention is then None/undeterminable,
    not zero, and callers should flag it as "not available from this file
    shape" rather than treating it as a data gap."""
    opening = bridge["opening"]
    if not opening:
        return {"grr_pct": None, "nrr_pct": None, "logo_retention_pct": None}
    grr = (opening + bridge["contraction"] + bridge["churn"]) / opening
    nrr = (opening + bridge["expansion"] + bridge["contraction"] + bridge["churn"]) / opening
    logo_ret = None
    if logos and logos.get("opening"):
        logo_ret = (logos["opening"] - logos["churned"]) / logos["opening"]
    return {
        "grr_pct": round(grr, 6),
        "nrr_pct": round(nrr, 6),
        "logo_retention_pct": round(logo_ret, 6) if logo_ret is not None else None,
    }


def churn_register(deltas, acquisition_note=None, opening_month=None, closing_month=None):
    """Churned/at-risk customer register from per-customer deltas. A customer
    is 'churned' if closing ARR hit zero; 'at-risk' if it contracted by more
    than AT_RISK_CONTRACTION_PCT of opening ARR (and didn't fully churn), OR
    if the file's own status/reason column flags it. Excludes pure-expansion
    and flat customers.

    Each entry carries a `calc` note (how its `arr` figure was derived from
    the customer's opening/closing snapshots) and the source-file `row` the
    customer sits on -- the caller turns the row into a per-entry source_ref
    and drops the raw row from the emitted record."""
    om = opening_month or "opening month"
    cm = closing_month or "closing month"
    out = []
    for d in deltas:
        opening = d["opening"]
        closing = d["closing"]
        status_text = (d.get("status") or "").strip()
        flagged_at_risk = bool(status_text) and any(
            tok in _norm(status_text) for tok in ("at-risk", "at risk", "risk", "watch"))
        flagged_churn = bool(status_text) and "churn" in _norm(status_text)
        if closing <= 0 and opening > 0:
            out.append({"customer": d["customer"], "arr": opening,
                       "status": "churned",
                       "reason": status_text or "closing ARR = 0",
                       "row": d.get("row"),
                       "calc": (f"per-customer ARR {opening:,.2f} at {om} -> 0 at {cm}; "
                                "arr = opening ARR lost (full churn)")})
        elif opening > 0 and closing > 0 and (opening - closing) / opening >= AT_RISK_CONTRACTION_PCT:
            pct = round((opening - closing) / opening * 100, 1)
            out.append({"customer": d["customer"], "arr": round(opening - closing, 2),
                       "status": "at-risk",
                       "reason": status_text or f"contracted {pct}% MoM",
                       "row": d.get("row"),
                       "calc": (f"per-customer ARR {opening:,.2f} at {om} -> {closing:,.2f} "
                                f"at {cm}: contracted {pct}% >= "
                                f"{AT_RISK_CONTRACTION_PCT:.0%} at-risk threshold; "
                                "arr = opening - closing (ARR at risk)")})
        elif flagged_at_risk or flagged_churn:
            out.append({"customer": d["customer"], "arr": round(max(opening, closing), 2),
                       "status": "churned" if flagged_churn else "at-risk",
                       "reason": status_text,
                       "row": d.get("row"),
                       "calc": (f"flagged by the file's own status column ({status_text!r}); "
                                f"per-customer ARR {opening:,.2f} at {om} -> {closing:,.2f} "
                                f"at {cm}; arr = max(opening, closing)")})
    return out


# ===========================================================================
# Deferred Revenue Schedule parsing + walk
# ===========================================================================
_DR_LABEL_TOKENS = {
    "opening": ("opening", "beginning", "bop"),
    "billings": ("billing", "invoiced", "additions"),
    "recognized": ("recogni", "revenue recognized", "released"),
    "closing": ("closing", "ending", "eop"),
}


def _dr_month_header(rows, prefer_month=None):
    """(header row index, {col_idx: month_label}) for the row of a
    deferred-revenue grid that heads its columns with months; (None, {}) when
    this sheet has no such row.

    A row holding `prefer_month` wins, since that is the period being read;
    otherwise the row holding the most months does. A title row or as-of stamp
    carrying one bare date parses as a month as well, so taking the first
    month-bearing row would hand back a one-column axis for the wrong month and
    the schedule below it would never be read."""
    cands = _month_header_candidates(rows)
    if prefer_month:
        for i, cand in cands:
            if prefer_month in cand.values():
                return i, cand
    return cands[0] if cands else (None, {})


def parse_deferred_revenue(path, period_month):
    """Parse a Deferred Revenue Schedule export into opening/billings/
    recognized/closing for `period_month`.

    Tries three shapes, in order:
      1. A labeled roll-forward: rows labeled Opening/Billings/Recognized/
         Closing with month columns (same header-detection approach as the
         retention parser). A month column may be headed a month name, a real
         date cell or a literal as-of date -- all resolve to the same month
         label via `monitor_lib.month_label`.
      2. A flat total-only shape: a single 'Deferred Revenue' balance column
         per month -- in that case only opening/closing can be read directly;
         billings/recognized are left None and flagged as not derivable from
         a balance-only export (still returns a partial, usable result).
      3. An entity-breakdown reconciliation shape: a SINGLE as-of-date column
         (no roll-forward legs at all) with per-entity closing-balance rows
         (e.g. "Deferred Revenue, <Entity A>", "Deferred Revenue, <Entity
         B>", ..., "Total") -- common when a borrower reports deferred
         revenue by legal entity post-acquisition rather than as a single
         consolidated roll-forward. Only a closing balance (the "Total" row,
         or the sum of the per-entity rows if no Total row is present) is
         derivable; opening/billings/recognized are None. The per-entity
         breakdown is preserved in `entity_breakdown` for the basis-break
         narrative (organic vs. acquired DR).

    The result also carries a `provenance` block for source refs:
    {"sheet", "header_row", "column" (the period's column letter),
     "rows": {leg: 1-based source row}, "entity_rows": {entity: row}} --
    consumed by the caller to build per-figure source refs, never emitted
    raw in the module output.
    """
    # Shape 1/2/3: a header row with month columns, on whichever tab carries the
    # period being reported — a schedule often sits behind a cover tab.
    rows, sheet, header_idx, month_cols = None, None, None, {}
    for cand_rows, cand_sheet in read_sheets(path):
        hi, mc = _dr_month_header(cand_rows, prefer_month=period_month)
        if hi is None:
            continue
        if period_month in mc.values():
            rows, sheet, header_idx, month_cols = cand_rows, cand_sheet, hi, mc
            break
        if header_idx is None:      # keep the first month-bearing tab as fallback
            rows, sheet, header_idx, month_cols = cand_rows, cand_sheet, hi, mc
    if header_idx is None:
        raise ValueError(f"could not locate a month header row in {path}")

    target_col = None
    for ci, mlabel in month_cols.items():
        if mlabel == period_month:
            target_col = ci
            break
    if target_col is None:
        raise ValueError(f"period {period_month!r} not found in {path}")

    label_vals = {}
    label_rows = {}
    entity_breakdown = {}
    entity_rows = {}
    total_row_val = None
    total_row_num = None
    for rnum, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not r:
            continue
        label_col = _label_col_before(r, target_col)
        raw_label = r[label_col] if label_col < len(r) else None
        label = _norm(raw_label) if raw_label is not None else ""
        if not label:
            continue
        v = ml.safe_float(r[target_col]) if target_col < len(r) else None
        if v is None:
            continue
        for key, tokens in _DR_LABEL_TOKENS.items():
            if key not in label_vals and any(tok in label for tok in tokens):
                label_vals[key] = v
                label_rows[key] = rnum
        # Shape 3 detection: "deferred revenue, <entity>" per-entity rows.
        m = re.match(r"^deferred revenue,\s*(.+)$", label)
        if m:
            ename = str(raw_label).split(",", 1)[1].strip()
            entity_breakdown[ename] = v
            entity_rows[ename] = rnum
        elif label == "total":
            total_row_val = v
            total_row_num = rnum

    result = {
        "opening": label_vals.get("opening"),
        "billings": label_vals.get("billings"),
        "recognized": label_vals.get("recognized"),
        "closing": label_vals.get("closing"),
        "derived": False,
    }
    if result["closing"] is None and (total_row_val is not None or entity_breakdown):
        # Shape 3: entity-breakdown reconciliation (e.g. "Deferred Revenue,
        # <Entity A>" / "Deferred Revenue, <Entity B>" / ... / "Total"). MUST
        # be checked BEFORE the single-row balance-only fallback below --
        # otherwise that fallback greedily matches the FIRST row containing
        # "deferred revenue" (e.g. "Deferred Revenue, Example", one entity's
        # balance only) and silently under-reports closing DR by however much
        # the other entities' balances are, which is exactly wrong for a
        # post-acquisition combined-entity DR schedule. Prefer an explicit
        # "Total" row; else sum the per-entity rows.
        result["closing"] = total_row_val if total_row_val is not None else round(
            sum(entity_breakdown.values()), 2)
        result["derived"] = result["derived"] or (total_row_val is None)
        if total_row_val is not None:
            label_rows["closing"] = total_row_num
        # (entity-sum case: no single closing row -- entity_rows carry the refs)
    if result["closing"] is None:
        # Fall back to treating the plain balance row (label containing
        # "deferred revenue") as the closing balance for this month -- only
        # reached when there is no entity breakdown/Total row at all (a
        # single-entity, single-balance-row file).
        for rnum, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            if not r:
                continue
            label_col = _label_col_before(r, target_col)
            raw_label = r[label_col] if label_col < len(r) else None
            label = _norm(raw_label) if raw_label is not None else ""
            if "deferred revenue" in label:
                v = ml.safe_float(r[target_col]) if target_col < len(r) else None
                if v is not None:
                    result["closing"] = v
                    label_rows["closing"] = rnum
                break
    if entity_breakdown:
        result["entity_breakdown"] = entity_breakdown
    result["provenance"] = {
        "sheet": sheet, "header_row": header_idx + 1,
        "column": get_column_letter(target_col + 1),
        "rows": label_rows, "entity_rows": entity_rows,
    }
    return result


def dr_walk(dr_parsed):
    """Foot the DR walk if all four legs are present; otherwise return what's
    available with foots=None (undetermined, not false)."""
    o, b, rec, c = (dr_parsed.get("opening"), dr_parsed.get("billings"),
                    dr_parsed.get("recognized"), dr_parsed.get("closing"))
    out = {"opening": o, "billings": b, "recognized": rec, "closing": c}
    if None not in (o, b, rec, c):
        diff = round(o + b - rec - c, 2)
        out["foots"] = abs(diff) <= DEFAULT_TIE_TOLERANCE
        out["foot_difference"] = diff
    else:
        out["foots"] = None
        out["foot_difference"] = None
    return out


# ===========================================================================
# Compliance-certificate text parsing (extracted-text/pdf-text fallback)
# ===========================================================================
_CERT_ARR_RE = re.compile(
    r"(annualized recurring revenue|arr)\D{0,40}?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_CERT_DEBT_RE = re.compile(
    r"(indebtedness|total debt)\D{0,40}?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
# Many borrower certificates report the ARR-denominated covenant as a ratio
# line item directly (Covenant Amount / Actual Amount / Result columns) and
# NEVER state the underlying Indebtedness or ARR dollar figures anywhere in
# the document -- _CERT_ARR_RE / _CERT_DEBT_RE will then correctly find
# nothing (there is nothing to find), and the caller must not silently swap
# in a materially different model-derived ARR without at least also trying
# to capture the certified RATIO itself. Matches a line naming a recurring-
# revenue-ratio-style covenant followed by two "N.NN:1.00"-style ratios (the
# covenant maximum, then the actual) -- generic covenant-name wording, not
# tied to one borrower's exact label.
_CERT_RATIO_LINE_RE = re.compile(
    r"(recurring revenue ratio|debt[\s\-]?to[\s\-]?arr|arr ratio)[^\n]{0,80}?"
    r"([\d.]+)\s*:\s*1(?:\.0+)?\D{0,40}?([\d.]+)\s*:\s*1(?:\.0+)?",
    re.IGNORECASE)


def parse_certificate_text(path):
    """Best-effort extraction of certified ARR / Indebtedness / ratio from a
    certificate source. Reads a pre-extracted .txt (or any text-sniffed file).

    A certificate delivered as a PDF is recognized as one (`_sniff_format`
    returns "pdf") and read ONLY through an extracted-text sidecar of the same
    name: regexing PDF bytes as if they were text matches stray digits out of
    the binary and would publish an invented figure as borrower-certified.
    No sidecar -> ({}, None), and the caller reports the certificate section as
    unavailable, per the input-gated philosophy.

    Returns (values, path_read): `values` is a dict with any of "arr",
    "indebtedness" (dollar figures, if the certificate states them) and/or
    "ratio_covenant_max" / "ratio_actual" (if the certificate instead states
    the ratio directly, which is the common case -- most compliance
    certificates disclose the tested ratio, not its dollar components).
    `path_read` is the file actually read (the sidecar .txt when the
    discovered certificate was binary) -- what source refs should point at
    -- or None when nothing readable was found (values is then {})."""
    fmt = _sniff_format(path)
    if fmt not in ("text",):
        # try a sidecar .txt with the same stem before giving up
        stem, _ = os.path.splitext(path)
        sidecar = stem + ".txt"
        if os.path.isfile(sidecar):
            path = sidecar
        else:
            return {}, None
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    out = {}
    m = _CERT_ARR_RE.search(text)
    if m:
        out["arr"] = ml.safe_float(m.group(2))
    m = _CERT_DEBT_RE.search(text)
    if m:
        out["indebtedness"] = ml.safe_float(m.group(2))
    # A certificate states a positive dollar figure or it states nothing. A
    # non-positive match came from text that only looked like the line we want,
    # and a figure this module publishes as borrower-certified has to earn it.
    for key in ("arr", "indebtedness"):
        if key in out and not (out[key] and out[key] > 0):
            del out[key]
    m = _CERT_RATIO_LINE_RE.search(re.sub(r"\s+", " ", text))
    if m:
        out["ratio_covenant_max"] = ml.safe_float(m.group(2))
        out["ratio_actual"] = ml.safe_float(m.group(3))
    return out, path


# ===========================================================================
# Covenant stress grid
# ===========================================================================
# Matched against a normalized ref, and a person writes a definition name with
# spaces -- "Recurring Revenue", "Annualized Recurring Revenue". `_norm` only
# lowercases and collapses whitespace, so the underscored spellings this used to
# look for could never match one, and the stress grid reported itself
# "not applicable" on a deal whose covenant was sitting right there. `arr` is a
# WHOLE word: as a substring it also hides inside carrying, warranty and arrears.
_ARR_DEF_RE = re.compile(r"\barr\b|recurring revenue")


def _is_arr_ref(ref):
    """Whether a covenant input points at recurring revenue / ARR, however the
    spec spells it (`Annualized Recurring Revenue`, `recurring_revenue`, `ARR`)."""
    return bool(_ARR_DEF_RE.search(_norm(str(ref or "").replace("_", " "))))


def find_arr_ratio_covenant(covenant_spec):
    """Locate a max_ratio covenant in covenant-spec.json whose denominator
    resolves to an ARR/recurring-revenue definition (matched by the
    definition's ref/name containing an ARR-like token -- not hardcoded to a
    single borrower's covenant id). Returns the covenant dict, or None."""
    for cov in covenant_spec.get("covenants", []):
        if cov.get("primitive") != "max_ratio":
            continue
        if _is_arr_ref((cov.get("denominator") or {}).get("ref")):
            return cov
    return None


def resolve_covenant_threshold(cov, test_date_iso, unit, covenant_spec=None):
    """Threshold in effect on the test date -- delegated to
    covenant_compliance.resolve_threshold (the single owner of the
    schedule-resolution rule). A not-yet-effective schedule or an explicit
    gap step (value: null) resolves to None, so the stress grid is skipped
    for that period instead of crashing on the gap marker.

    `unit` and the spec go through because resolve_threshold returns the
    threshold on the MODEL's unit, which is what it is compared against here."""
    return cc.resolve_threshold(cov, test_date_iso, unit, covenant_spec)[0]


def covenant_stress_grid(indebtedness, arr_actual, at_risk_churn, max_ratio,
                          pik_annual_rate=None, certified_ratio_actual=None):
    """Certified ratio at actual ARR, ratio post at-risk churn, breakeven ARR
    at the covenant max, and PIK drift on the numerator (Indebtedness) at
    1/3/6 months, per the spec.

    `certified_ratio_actual`, when given, is the ratio the borrower's own
    compliance certificate states directly for the period (most certificates
    disclose the tested ratio itself, not its dollar components). Many
    borrowers' agreements price Indebtedness/ARR using defined terms (e.g. a
    pro-forma acquisition adjustment to ARR, or a narrower Indebtedness
    definition than a simple debt-line sum) that this module cannot fully
    reconstruct from the model alone -- reconstructing `ratio_actual` purely
    from `indebtedness`/`arr_actual` in that case can diverge materially from
    what is actually certified. When `certified_ratio_actual` is available,
    it is reported as `ratio_actual` (the governing, borrower-certified
    figure); the model/config-derived reconstruction is still computed and
    returned separately as `ratio_actual_model_reconstruction` for a sanity
    check, and a flag is raised if the two diverge beyond a wide (>=0.03)
    tolerance so the discrepancy is visible rather than silently dropped."""
    if indebtedness is None or arr_actual is None or max_ratio is None:
        return None
    ratio_reconstructed = (indebtedness / arr_actual) if arr_actual else None
    ratio_actual = certified_ratio_actual if certified_ratio_actual is not None else ratio_reconstructed
    arr_post_churn = arr_actual - (at_risk_churn or 0.0)
    ratio_post = (indebtedness / arr_post_churn) if arr_post_churn else None
    if certified_ratio_actual is not None and ratio_reconstructed and arr_post_churn:
        # Scale the post-churn ratio off the CERTIFIED actual ratio (not the
        # model reconstruction) so the stress grid stays internally
        # consistent with the governing certified figure.
        ratio_post = certified_ratio_actual * (arr_actual / arr_post_churn)
    breakeven_arr = indebtedness / max_ratio if max_ratio else None

    pik_drift = None
    if pik_annual_rate is not None:
        monthly_rate = pik_annual_rate / 12.0
        pik_drift = {
            f"{n}m_indebtedness": round(indebtedness * ((1 + monthly_rate) ** n), 2)
            for n in (1, 3, 6)
        }
    out = {
        "indebtedness": round(indebtedness, 2),
        "arr_actual": round(arr_actual, 2),
        "at_risk_churn": round(at_risk_churn or 0.0, 2),
        "arr_post_at_risk_churn": round(arr_post_churn, 2),
        "max_ratio": max_ratio,
        "ratio_actual": round(ratio_actual, 6) if ratio_actual is not None else None,
        "ratio_post_atrisk_churn": round(ratio_post, 6) if ratio_post is not None else None,
        "breakeven_arr": round(breakeven_arr, 2) if breakeven_arr is not None else None,
        "pik_drift_indebtedness": pik_drift,
        "pik_drift_bps_per_month": (round(pik_annual_rate / 12.0 * 10000, 2)
                                    if pik_annual_rate is not None else None),
    }
    if certified_ratio_actual is not None:
        out["ratio_actual_source"] = "borrower-certified (compliance certificate)"
        out["ratio_actual_model_reconstruction"] = (
            round(ratio_reconstructed, 6) if ratio_reconstructed is not None else None)
        if ratio_reconstructed is not None and abs(ratio_reconstructed - certified_ratio_actual) >= 0.03:
            out["ratio_reconstruction_diverges"] = True
    else:
        out["ratio_actual_source"] = "reconstructed (indebtedness / arr_actual; no certified ratio found)"
    return out


# ===========================================================================
# Main compute
# ===========================================================================
def compute(cfg, period, unit, out_override=None, config_path=None,
            retention_file=None, deferred_revenue_file=None, certificate_file=None):
    """Run the full revenue-quality module for one borrower-config + period.

    `unit` is the borrower's ReportingUnit (monitor_lib.reporting_unit(cfg)).
    This module's three inputs are borrower documents stating DOLLARS -- the
    retention workbook, the deferred-revenue schedule, the certificate -- while
    the few figures it reads from the model are in the model's own unit. So the
    model side is converted to dollars as it is read and everything this module
    publishes is in dollars, which is what its output declares. Comparing an ARR
    figure off a workbook against an unconverted model row would put a
    thousands-scale deal's tie-outs a thousand-fold apart.

    The three monthly input files are supplied by the calling agent, which has
    already opened the borrower's reporting folder for the period and seen which
    file is which: `retention_file` (the ARR / retention workbook),
    `deferred_revenue_file` (the deferred-revenue schedule) and
    `certificate_file` (the compliance certificate). Pass each path AS
    CONFIGURED -- it is read through ml.resolve_path and recorded verbatim in
    `source_files`. A file the agent does not supply leaves its section
    unavailable with an `input_missing` exception, exactly as a file that had not
    arrived did.

    `period` is 'YYYY-MM'. Returns the standard module conclusion envelope
    (monitor_lib.conclusion): `results` is a list of section records
    (arr_bridge, retention, churn_register, deferred_revenue,
    covenant_stress, cross_module_checks, tie_outs), each memo-surfaceable
    record carrying a per-figure `source_ref` into the top-level
    `source_files` path index plus a plain-text `calc` note --
    validate_output.py enforces this. Every section is independently gated:
    a missing input marks that section unavailable and logs an exception,
    but never raises; an unusable period returns the standard skip record.
    `config_path` is the AS-GIVEN borrower-config path string, recorded in
    source refs when a config override supplies a figure.
    """
    exceptions = []
    borrower = cfg.get("borrower_name")
    paths = cfg.get("paths", {})
    module_inputs = cfg.get("module_inputs", {}).get("revenue-quality", {})
    model_lines = dict(DEFAULT_MODEL_LINES)
    model_lines.update(module_inputs.get("model_lines", {}) or {})
    debt_lines = module_inputs.get("debt_lines") or DEFAULT_DEBT_LINES
    tie_tol = module_inputs.get("tie_tolerance", DEFAULT_TIE_TOLERANCE)

    try:
        month_label = _month_label_from_period(period)
    except (ValueError, IndexError) as e:
        # Unusable period -> the standard skip record, never a traceback.
        return ml.conclusion(
            module="revenue-quality", period=None, results=[], exceptions=[],
            skipped=True, reason=f"unusable --period {period!r}: {e}")
    prior_month_label = _prior_month_label(month_label)
    reporting_root = paths.get("reporting_packages_folder")

    # One SourceIndex for the whole run: every figure's source_ref
    # points into the conclusion's deduped source_files list. Paths are
    # recorded AS CONFIGURED (pre-sandbox-resolution) so downstream
    # consumers can join store hyperlinks.
    _mp = (cfg.get("paths") or {}).get("model_output_path")
    src_idx = ml.SourceIndex(source_map=ml.model_source_map(_mp), model_path=_mp)
    config_file = config_path or "borrower-config.json"

    # The model is read by several sections -- open it once, lazily; every
    # caller keeps its own error gating (matching the old per-section opens).
    model_path = paths.get("model_output_path")
    _model_box = {}

    def _open_model_once():
        if "m" not in _model_box:
            _model_box["m"] = ml.open_model(model_path)
        return _model_box["m"]

    # ---- deal-context: acquisition / basis-break awareness -----------------
    deal_context = {}
    dctx_path = paths.get("deal_context_path")
    if dctx_path:
        try:
            deal_context = ml.load_config(dctx_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            exceptions.append({"section": "deal_context", "error": str(e)})
    acquisition_note = module_inputs.get("basis_break_note") or ""
    has_basis_break = bool(acquisition_note.strip())

    # ================================================================
    # 1) Retention Analysis -> ARR bridge + retention + churn register
    # ================================================================
    arr_bridge = {"available": False}
    retention = {"available": False}
    churn_reg = []
    full_churn_reg = []
    combined_bridge = organic_bridge = None
    retention_parsed = None
    ret_sheet, ret_cols = None, {}
    bridge_refs = []  # ARR-bridge source refs; reused by retention + tie-outs

    retention_given = retention_file
    retention_path = ml.resolve_path(retention_file) if retention_file else None
    if retention_path is None:
        exceptions.append({
            "section": "arr_bridge/retention", "type": "input_missing",
            "detail": "no ARR / retention workbook supplied for the period "
                      "(pass --retention-file)",
        })
    else:
        try:
            retention_parsed = parse_retention_analysis(retention_path)
            months = retention_parsed["months"]
            ret_sheet = retention_parsed.get("sheet")
            ret_cols = retention_parsed.get("month_cols") or {}
            if month_label not in months:
                raise ValueError(
                    f"period month {month_label!r} not present in {retention_path} "
                    f"(months found: {months})")
            # opening month = prior month if present in the file, else the
            # earliest month before the period present in the file.
            opening_month = prior_month_label if prior_month_label in months else (
                months[months.index(month_label) - 1] if months.index(month_label) > 0 else None)
            if opening_month is None:
                raise ValueError(
                    f"only one month ({month_label}) present in {retention_path}; "
                    "cannot derive a bridge without an opening snapshot")

            is_rollup = retention_parsed.get("rollup") is not None

            if is_rollup:
                # Shape 2: entity/metric roll-up (no customer-level detail).
                # Bridge legs come straight from the file's own labeled rows
                # for month_label; no customer deltas, so no logos/churn
                # register are derivable from this file.
                combined_bridge, combined_logos, combined_deltas = build_bridge_from_rollup(
                    retention_parsed["rollup"], opening_month, month_label,
                    only_organic_exclude_cohort=False)
                combined_ret = retention_metrics(combined_bridge, combined_logos)

                organic_vs_combined = {}
                basis = "combined"
                active_bridge, active_logos, active_deltas, active_ret = (
                    combined_bridge, combined_logos, combined_deltas, combined_ret)

                if has_basis_break:
                    organic_bridge, organic_logos, organic_deltas = build_bridge_from_rollup(
                        retention_parsed["rollup"], opening_month, month_label,
                        only_organic_exclude_cohort=True)
                    organic_ret = retention_metrics(organic_bridge, organic_logos)
                    organic_vs_combined = {
                        "organic": {"bridge": organic_bridge, "retention": organic_ret,
                                   "logos": organic_logos},
                        "combined": {"bridge": combined_bridge, "retention": combined_ret,
                                    "logos": combined_logos},
                        "acquisition_note": acquisition_note,
                    }
                    basis = "organic"
                    active_bridge, active_logos, active_deltas, active_ret = (
                        organic_bridge, organic_logos, organic_deltas, organic_ret)

                arr_bridge = dict(active_bridge)
                arr_bridge["basis"] = basis
                arr_bridge["available"] = True
                arr_bridge["opening_month"] = opening_month
                arr_bridge["closing_month"] = month_label
                arr_bridge["source"] = os.path.basename(retention_path)

                # Provenance: each bridge leg is read straight off the
                # file's own labeled row for the closing month.
                leg_rows = retention_parsed["rollup"].get("leg_rows") or {}
                leg_all_rows = retention_parsed["rollup"].get("leg_all_rows") or {}
                leg_col = ret_cols.get(month_label)

                def _leg_fed_by(leg):
                    """Every row that fed a leg — a movement can be stated on more
                    than one row, and each one is cited."""
                    return leg_all_rows.get(leg) or [leg_rows[leg]]

                bridge_refs = [
                    src_idx.ref(retention_given, **_ref_fields(
                        sheet=ret_sheet, row=lrow,
                        cells=(f"{leg_col}{lrow}" if leg_col else None),
                        period=ml.period_stamp(month_label), label=leg))
                    for leg in ("opening", "new", "expansion", "contraction",
                                "churn", "acquired", "adjustments", "closing")
                    if leg in leg_rows
                    for lrow in _leg_fed_by(leg)
                ]
                if not bridge_refs:
                    bridge_refs = [src_idx.ref(retention_given,
                                               **_ref_fields(sheet=ret_sheet))]
                acquired_at_month = (
                    retention_parsed["rollup"].get("acquired") or {}).get(month_label)
                if not acquired_at_month:
                    acquired_note = ""
                elif arr_bridge.get("closing_row_includes_acquired"):
                    acquired_note = (
                        "; the file's own closing row already carries acquired ARR"
                        + ("; organic closing = that row less acquired ARR"
                           if basis == "organic"
                           else "; acquired folded into new so the combined bridge foots"))
                else:
                    acquired_note = (
                        "; acquired-ARR row folded into new and closing (combined basis)"
                        if basis == "combined" else "")
                arr_bridge["source_ref"] = bridge_refs
                arr_bridge["calc"] = (
                    f"Bridge legs read directly from the roll-up file's own labeled "
                    f"rows at {month_label}"
                    + (f" (column {leg_col}; rows "
                       + ", ".join(
                           f"{leg}=" + "+".join(str(r) for r in _leg_fed_by(leg))
                           for leg in sorted(leg_rows, key=leg_rows.get)) + ")"
                       if leg_rows else "")
                    + acquired_note
                    + f"; basis {basis}; foot check opening+new+expansion+contraction"
                    f"+churn+adjustments-closing = {arr_bridge['foot_difference']}")

                retention = dict(active_ret)
                retention["available"] = True
                retention["organic_vs_combined"] = organic_vs_combined
                retention["logo_retention_note"] = (
                    "Not derivable: source Retention Analysis is an entity/metric roll-up "
                    "with no customer-level detail (no named churn register either)."
                )
                _b = active_bridge
                retention["source_ref"] = list(bridge_refs)
                retention["calc"] = (
                    f"From the {basis} ARR bridge at {month_label}: "
                    f"GRR = (opening+contraction+churn)/opening = ({_b['opening']:,.2f}"
                    f"{_b['contraction']:+,.2f}{_b['churn']:+,.2f})/{_b['opening']:,.2f}; "
                    f"NRR adds expansion {_b['expansion']:+,.2f}; logo retention not "
                    "derivable (no customer-level detail in this file shape)")

                churn_reg = []  # no customer names in this file shape
                full_churn_reg = []

                if not arr_bridge["foots"]:
                    exceptions.append({
                        "section": "arr_bridge", "type": "bridge_does_not_foot",
                        "detail": f"opening+new+expansion+contraction+churn+adjustments"
                                 f"-closing = {arr_bridge['foot_difference']}",
                    })
                exceptions.append({
                    "section": "churn_register", "type": "not_available",
                    "detail": "Retention Analysis file is an entity/metric roll-up (no "
                             "customer-level detail) -- no named churn/at-risk register "
                             "could be produced from this file. Cross-check named churn "
                             "against deal-context.our_view / carry_forward instead.",
                })
            else:
                combined_bridge, combined_logos, combined_deltas = build_bridge(
                    retention_parsed["customers"], opening_month, month_label,
                    only_organic_exclude_cohort=False)
                combined_ret = retention_metrics(combined_bridge, combined_logos)

                organic_vs_combined = {}
                basis = "combined"
                active_bridge, active_logos, active_deltas, active_ret = (
                    combined_bridge, combined_logos, combined_deltas, combined_ret)

                if has_basis_break:
                    organic_bridge, organic_logos, organic_deltas = build_bridge(
                        retention_parsed["customers"], opening_month, month_label,
                        only_organic_exclude_cohort=True, acquisition_note=acquisition_note)
                    organic_ret = retention_metrics(organic_bridge, organic_logos)
                    organic_vs_combined = {
                        "organic": {"bridge": organic_bridge, "retention": organic_ret,
                                   "logos": organic_logos},
                        "combined": {"bridge": combined_bridge, "retention": combined_ret,
                                    "logos": combined_logos},
                        "acquisition_note": acquisition_note,
                    }
                    # Per spec: NEVER report combined-only when a basis break
                    # exists -- organic is the primary reported basis.
                    basis = "organic"
                    active_bridge, active_logos, active_deltas, active_ret = (
                        organic_bridge, organic_logos, organic_deltas, organic_ret)

                arr_bridge = dict(active_bridge)
                arr_bridge["basis"] = basis
                arr_bridge["available"] = True
                arr_bridge["opening_month"] = opening_month
                arr_bridge["closing_month"] = month_label
                arr_bridge["source"] = os.path.basename(retention_path)

                # Provenance: the bridge is derived from the two month
                # columns of the customer table -- one ref per column read.
                cust_rows = [c["row"] for c in retention_parsed["customers"]
                             if c.get("row")]
                r0, r1 = (min(cust_rows), max(cust_rows)) if cust_rows else (None, None)
                open_col = ret_cols.get(opening_month)
                close_col = ret_cols.get(month_label)
                bridge_refs = []
                if r0 is not None:
                    for col, mon in ((open_col, opening_month), (close_col, month_label)):
                        if col:
                            bridge_refs.append(src_idx.ref(retention_given, **_ref_fields(
                                sheet=ret_sheet, row=r0, cells=f"{col}{r0}:{col}{r1}",
                                period=ml.period_stamp(mon),
                                label=f"{mon} ARR column (per-customer)")))
                if not bridge_refs:
                    bridge_refs = [src_idx.ref(retention_given,
                                               **_ref_fields(sheet=ret_sheet))]
                arr_bridge["source_ref"] = bridge_refs
                arr_bridge["calc"] = (
                    f"Derived from per-customer ARR deltas {opening_month} -> "
                    f"{month_label} across {len(retention_parsed['customers'])} customer "
                    "rows: opening/closing = column sums; new = 0 -> positive; "
                    "churn = positive -> 0 (signed negative); expansion/contraction = "
                    "MoM increase/decrease on continuing customers"
                    + ("; organic basis excludes acquired-cohort customers"
                       if basis == "organic" else "")
                    + f"; foot check = {arr_bridge['foot_difference']}")

                retention = dict(active_ret)
                retention["available"] = True
                retention["organic_vs_combined"] = organic_vs_combined
                _b, _lg = active_bridge, active_logos
                retention["source_ref"] = list(bridge_refs)
                retention["calc"] = (
                    f"From the {basis} ARR bridge {opening_month} -> {month_label}: "
                    f"GRR = (opening+contraction+churn)/opening = ({_b['opening']:,.2f}"
                    f"{_b['contraction']:+,.2f}{_b['churn']:+,.2f})/{_b['opening']:,.2f}; "
                    f"NRR adds expansion {_b['expansion']:+,.2f}; "
                    + (f"logo retention = (opening logos - churned)/opening logos = "
                       f"({_lg['opening']}-{_lg['churned']})/{_lg['opening']}"
                       if _lg and _lg.get("opening")
                       else "logo retention undefined (no opening logos)"))

                churn_reg = churn_register(active_deltas, acquisition_note,
                                           opening_month=opening_month,
                                           closing_month=month_label)
                # Also compute churn on the FULL (combined) population for the
                # covenant stress grid's at-risk-churn input when basis-broken,
                # so the covenant test (which is ARR-denominated on the combined
                # certified base) still reflects all at-risk logos, not just
                # organic ones.
                full_churn_reg = (churn_register(combined_deltas, acquisition_note,
                                                 opening_month=opening_month,
                                                 closing_month=month_label)
                                  if has_basis_break else churn_reg)

                # Per-entry provenance: each register entry points at the
                # customer's own row (opening + closing month cells).
                for entry in churn_reg:
                    rnum = entry.pop("row", None)
                    cells = (",".join(f"{col}{rnum}" for col in (open_col, close_col)
                                      if col) or None) if rnum else None
                    entry["source_ref"] = [src_idx.ref(retention_given, **_ref_fields(
                        sheet=ret_sheet, row=rnum, cells=cells,
                        period=ml.period_stamp(month_label)))]

                if not arr_bridge["foots"]:
                    exceptions.append({
                        "section": "arr_bridge", "type": "bridge_does_not_foot",
                        "detail": f"opening+new+expansion-contraction-churn-closing = "
                                 f"{arr_bridge['foot_difference']}",
                    })
        except (OSError, ValueError, KeyError) as e:
            exceptions.append({
                "section": "arr_bridge/retention", "type": "parse_error",
                "path": retention_path, "error": str(e),
            })

    # ================================================================
    # 2) Deferred revenue walk + BS tie-out
    # ================================================================
    deferred_revenue = {"available": False}
    dr_given = deferred_revenue_file
    dr_path = ml.resolve_path(deferred_revenue_file) if deferred_revenue_file else None
    if dr_path is None:
        exceptions.append({
            "section": "deferred_revenue", "type": "input_missing",
            "detail": "no deferred-revenue schedule supplied for the period "
                      "(pass --deferred-revenue-file)",
        })
    else:
        try:
            dr_parsed = parse_deferred_revenue(dr_path, month_label)
            walk = dr_walk(dr_parsed)
            deferred_revenue = dict(walk)
            deferred_revenue["available"] = True
            deferred_revenue["source"] = os.path.basename(dr_path)

            # Provenance: one ref per walk leg actually read (or the
            # per-entity closing rows when closing was summed from them).
            dr_prov = dr_parsed.get("provenance") or {}
            dr_sheet, dr_col = dr_prov.get("sheet"), dr_prov.get("column")
            dr_rows = dr_prov.get("rows") or {}
            dr_entity_rows = dr_prov.get("entity_rows") or {}
            dr_refs, dr_closing_refs = [], []
            for leg in ("opening", "billings", "recognized", "closing"):
                rnum = dr_rows.get(leg)
                if rnum is None or walk.get(leg) is None:
                    continue
                ref = src_idx.ref(dr_given, **_ref_fields(
                    sheet=dr_sheet, row=rnum,
                    cells=(f"{dr_col}{rnum}" if dr_col else None),
                    period=ml.period_stamp(month_label), label=leg))
                dr_refs.append(ref)
                if leg == "closing":
                    dr_closing_refs.append(ref)
            if walk.get("closing") is not None and "closing" not in dr_rows and dr_entity_rows:
                for ename, rnum in dr_entity_rows.items():
                    ref = src_idx.ref(dr_given, **_ref_fields(
                        sheet=dr_sheet, row=rnum,
                        cells=(f"{dr_col}{rnum}" if dr_col else None),
                        period=ml.period_stamp(month_label),
                        label=f"closing (entity: {ename})"))
                    dr_refs.append(ref)
                    dr_closing_refs.append(ref)
            if not dr_refs:
                dr_refs = [src_idx.ref(dr_given, **_ref_fields(sheet=dr_sheet))]
            legs_present = [leg for leg in ("opening", "billings", "recognized", "closing")
                            if walk.get(leg) is not None]
            leg_desc = ", ".join(
                (f"{leg} row {dr_rows[leg]}" if leg in dr_rows
                 else f"{leg} summed from per-entity rows")
                for leg in legs_present) or "no walk legs readable"
            deferred_revenue["source_ref"] = dr_refs
            deferred_revenue["calc"] = (
                f"DR walk read from the Deferred Revenue Schedule at {month_label}"
                + (f" (column {dr_col})" if dr_col else "") + f": {leg_desc}; "
                + (f"foot check opening+billings-recognized-closing = "
                   f"{walk['foot_difference']}"
                   if walk.get("foots") is not None
                   else "walk not footable (legs missing from this file shape)"))

            if walk["foots"] is False:
                exceptions.append({
                    "section": "deferred_revenue", "type": "walk_does_not_foot",
                    "detail": f"opening+billings-recognized-closing = {walk['foot_difference']}",
                })

            # BS tie-out via monitor_lib + the model workbook.
            if model_path:
                try:
                    model = _open_model_once()
                    bs_label = model_lines["deferred_revenue_bs"]
                    bs_val = unit.to_dollars(model.get_line(
                        bs_label, month=month_label, sheet="Balance Sheet"))
                    bs_used_label = bs_label if bs_val is not None else None
                    if bs_val is None:
                        hit = model.find_row(r"deferred\s*revenue", sheet="Balance Sheet")
                        bs_val = None
                        if hit:
                            idx = model.months.index(month_label) if month_label in model.months else None
                            if idx is not None and idx < len(hit[1]):
                                bs_val = unit.to_dollars(ml.safe_float(hit[1][idx]))
                                if bs_val is not None:
                                    bs_used_label = hit[0]
                    closing = walk.get("closing")
                    if bs_val is not None and closing is not None:
                        diff = round(closing - bs_val, 2)
                        # Provenance: the DR schedule's closing cell(s) + the
                        # model's balance-sheet row for the month.
                        bs_prov = (model.provenance(bs_used_label, sheet="Balance Sheet",
                                                    month=month_label)
                                   if bs_used_label else None)
                        tie_refs = list(dr_closing_refs)
                        if bs_prov:
                            tie_refs.append(src_idx.ref(model.given_path, **bs_prov))
                        deferred_revenue["bs_tie_out"] = {
                            "ties": abs(diff) <= tie_tol,
                            "dr_schedule_closing": round(closing, 2),
                            "balance_sheet": round(bs_val, 2),
                            "difference": diff,
                            "source_ref": tie_refs,
                            "calc": (f"DR schedule closing {closing:,.2f} minus model "
                                     f"Balance Sheet '{(bs_prov or {}).get('label', bs_used_label)}' "
                                     f"{bs_val:,.2f} at {month_label} = {diff:+,.2f}; "
                                     f"ties if |difference| <= {tie_tol}"),
                        }
                        if abs(diff) > tie_tol:
                            exceptions.append({
                                "section": "deferred_revenue", "type": "tie_out_mismatch",
                                "detail": f"DR schedule closing {closing} vs BS "
                                         f"{bs_label} {bs_val} (diff {diff})",
                            })
                    else:
                        deferred_revenue["bs_tie_out"] = {"ties": None,
                                                          "note": "insufficient data to tie"}
                except (OSError, ValueError, KeyError) as e:
                    deferred_revenue["bs_tie_out"] = {"ties": None, "note": f"model read failed: {e}"}
                    exceptions.append({"section": "deferred_revenue_tie_out",
                                       "type": "model_error", "error": str(e)})
            else:
                deferred_revenue["bs_tie_out"] = {"ties": None, "note": "no model_output_path configured"}

            # DR direction vs churn narrative.
            churn_dollars = sum(c["arr"] for c in full_churn_reg if c["status"] == "churned")
            dr_note = ""
            if walk.get("closing") is not None and walk.get("opening") is not None:
                dr_direction = "up" if walk["closing"] > walk["opening"] else (
                    "down" if walk["closing"] < walk["opening"] else "flat")
                if dr_direction == "up" and churn_dollars > 0:
                    dr_note = (f"Deferred revenue rose while ${churn_dollars:,.0f} of churn was "
                              "recorded in the same period -- consistent with annual-prepay "
                              "timing / notice-period lag or acquired DR, NOT necessarily "
                              "contradicting the churn story. Investigate billing cadence "
                              "before treating this as reassuring.")
                elif dr_direction == "down" and churn_dollars > 0:
                    dr_note = ("Deferred revenue declined in the same direction as recorded "
                              "churn -- corroborating (not contradicting) the retention story.")
                else:
                    dr_note = f"Deferred revenue {dr_direction} month-over-month; no churn recorded this period."
            deferred_revenue["vs_churn_narrative"] = dr_note
        except (OSError, ValueError, KeyError) as e:
            exceptions.append({
                "section": "deferred_revenue", "type": "parse_error",
                "path": dr_path, "error": str(e),
            })

    # ================================================================
    # 3) Covenant stress grid (only if an ARR-denominated ratio covenant exists)
    # ================================================================
    covenant_stress = {"available": False}
    cs_arr_refs = []    # refs behind arr_actual (reused by the ARR tie-out)
    cs_other_refs = []  # refs behind indebtedness / certified ratio / PIK rate
    spec_path = paths.get("covenant_spec_path")
    covenant_spec = {}
    if spec_path:
        try:
            covenant_spec = ml.load_config(spec_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            exceptions.append({"section": "covenant_stress", "type": "spec_load_error", "error": str(e)})

    arr_cov = find_arr_ratio_covenant(covenant_spec) if covenant_spec else None
    if arr_cov is None:
        exceptions.append({
            "section": "covenant_stress", "type": "not_applicable",
            "detail": "no ARR-denominated max_ratio covenant found in covenant-spec.json; "
                     "stress grid skipped (spec-compliant no-op, not an error).",
        })
    else:
        try:
            test_date_iso = ml.month_end_iso(month_label)
            max_ratio = resolve_covenant_threshold(arr_cov, test_date_iso, unit,
                                                   covenant_spec)
            overrides = module_inputs.get("covenant_stress_overrides", {}) or {}

            # Certified values: prefer a parsed certificate text; else config overrides.
            cert_given = certificate_file
            cert_path = ml.resolve_path(certificate_file) if certificate_file else None
            cert_vals = {}
            cert_read_path = None
            if cert_path:
                try:
                    cert_vals, cert_read_path = parse_certificate_text(cert_path)
                except (OSError, ValueError) as e:
                    exceptions.append({"section": "covenant_stress",
                                       "type": "certificate_parse_error", "error": str(e)})
            # As-given path of the file actually READ (the sidecar .txt when
            # the discovered certificate was binary) -- what source refs record.
            cert_read_given = None
            if cert_read_path is not None:
                cert_read_given = (cert_given if cert_read_path == cert_path
                                   else os.path.splitext(cert_given)[0] + ".txt")

            indebtedness = cert_vals.get("indebtedness")
            arr_actual = cert_vals.get("arr")
            # Most compliance certificates disclose the TESTED RATIO directly
            # (Covenant Amount / Actual Amount / Result columns) rather than
            # its Indebtedness/ARR dollar components -- capture it whenever
            # present so the stress grid reports the governing certified
            # ratio instead of silently falling back to a model-reconstructed
            # ARR/Indebtedness that may use a narrower/broader definition
            # (e.g. a pro-forma acquisition ARR adjustment) than the model
            # can reproduce.
            certified_ratio_actual = cert_vals.get("ratio_actual")
            source_notes = []
            if indebtedness is not None:
                source_notes.append(f"indebtedness from certificate text ({os.path.basename(cert_path)})")
                cs_other_refs.append(src_idx.ref(cert_read_given, kind="certificate-text",
                                                 label="Indebtedness"))
            if indebtedness is None:
                indebtedness = overrides.get("indebtedness")
                if indebtedness is not None:
                    source_notes.append("indebtedness from config override")
                    cs_other_refs.append(src_idx.ref(
                        config_file, section="module_inputs.revenue-quality."
                                             "covenant_stress_overrides.indebtedness"))
            if arr_actual is not None:
                source_notes.append(f"ARR from certificate text ({os.path.basename(cert_path)})")
                cs_arr_refs.append(src_idx.ref(cert_read_given, kind="certificate-text",
                                               label="Annualized Recurring Revenue"))
            if arr_actual is None:
                arr_actual = overrides.get("arr")
                if arr_actual is not None:
                    source_notes.append("ARR from config override")
                    cs_arr_refs.append(src_idx.ref(
                        config_file, section="module_inputs.revenue-quality."
                                             "covenant_stress_overrides.arr"))
            if certified_ratio_actual is not None:
                source_notes.append(
                    f"ratio_actual from certified ratio line in certificate text "
                    f"({os.path.basename(cert_path)}) -- governs over any dollar-"
                    "component reconstruction below")
                cs_other_refs.append(src_idx.ref(
                    cert_read_given, kind="certificate-text",
                    label="certified covenant ratio line (max, actual)"))

            # Fall back to the model's recurring-revenue line, annualized,
            # if neither a certificate nor an override supplied ARR.
            if arr_actual is None and model_path:
                try:
                    model = _open_model_once()
                    rr_label = model_lines["recurring_revenue_is"]
                    rr = unit.to_dollars(model.get_line(
                        rr_label, month=month_label, sheet="Income Statement"))
                    rr_used = rr_label if rr is not None else None
                    if rr is None:
                        hit = model.find_row(r"recurring\s*revenue", sheet="Income Statement")
                        if hit and month_label in model.months:
                            rr = unit.to_dollars(
                                ml.safe_float(hit[1][model.months.index(month_label)]))
                            if rr is not None:
                                rr_used = hit[0]
                    if rr is not None:
                        arr_actual = rr * ANNUALIZATION_MULTIPLE
                        source_notes.append(
                            f"ARR derived from model '{rr_label}' x {ANNUALIZATION_MULTIPLE}")
                        rr_prov = (model.provenance(rr_used, sheet="Income Statement",
                                                    month=month_label) if rr_used else None)
                        if rr_prov:
                            cs_arr_refs.append(src_idx.ref(model.given_path, **rr_prov))
                except (OSError, ValueError, KeyError):
                    pass

            # Fall back to summing debt lines from the model for Indebtedness.
            if indebtedness is None and model_path:
                try:
                    model = _open_model_once()
                    total = 0.0
                    found_any = False
                    used_debt_labels = []
                    for lab in ([model_lines["indebtedness_bs"]] if model_lines.get("indebtedness_bs")
                               else debt_lines):
                        if not lab:
                            continue
                        v = unit.to_dollars(model.get_line(
                            lab, month=month_label, sheet="Balance Sheet"))
                        if v is not None:
                            total += v
                            found_any = True
                            used_debt_labels.append(lab)
                    if found_any:
                        indebtedness = total
                        source_notes.append(f"indebtedness summed from model lines {debt_lines}")
                        for lab in used_debt_labels:
                            lab_prov = model.provenance(lab, sheet="Balance Sheet",
                                                        month=month_label)
                            if lab_prov:
                                cs_other_refs.append(src_idx.ref(model.given_path, **lab_prov))
                except (OSError, ValueError, KeyError):
                    pass

            at_risk_churn = sum(c["arr"] for c in full_churn_reg if c["status"] == "at-risk")
            pik_rate = overrides.get("pik_annual_rate")

            if indebtedness is None or arr_actual is None or max_ratio is None:
                exceptions.append({
                    "section": "covenant_stress", "type": "insufficient_inputs",
                    "detail": (f"indebtedness={indebtedness}, arr_actual={arr_actual}, "
                              f"max_ratio={max_ratio}; supply certificate text or "
                              "module_inputs.revenue-quality.covenant_stress_overrides"),
                })
            else:
                covenant_stress = covenant_stress_grid(
                    indebtedness, arr_actual, at_risk_churn, max_ratio, pik_annual_rate=pik_rate,
                    certified_ratio_actual=certified_ratio_actual)
                covenant_stress["available"] = True
                covenant_stress["covenant_id"] = arr_cov.get("id")
                covenant_stress["source_notes"] = source_notes

                # Provenance: every input that fed the grid -- certificate /
                # config-override / model refs collected above, the at-risk
                # churn's source file, and the covenant's own spec section
                # (threshold / schedule live there).
                cs_refs = cs_other_refs + cs_arr_refs
                if pik_rate is not None:
                    cs_refs.append(src_idx.ref(
                        config_file, section="module_inputs.revenue-quality."
                                             "covenant_stress_overrides.pik_annual_rate"))
                if retention_given and full_churn_reg:
                    cs_refs.append(src_idx.ref(retention_given, **_ref_fields(
                        sheet=ret_sheet,
                        label="at-risk entries of the churn register")))
                cs_refs.append(src_idx.ref(
                    spec_path,
                    section=f"covenants.{arr_cov.get('id') or 'arr-ratio-covenant'}"))
                covenant_stress["source_ref"] = cs_refs
                _cs = covenant_stress
                calc_pieces = [
                    ("ratio_actual = borrower-certified ratio from the certificate "
                     "(governs; model reconstruction indebtedness/ARR = "
                     f"{_cs.get('ratio_actual_model_reconstruction')})")
                    if certified_ratio_actual is not None else
                    (f"ratio_actual = indebtedness / arr_actual = "
                     f"{_cs['indebtedness']:,.2f} / {_cs['arr_actual']:,.2f}"),
                    (f"ratio_post_atrisk_churn re-tests at ARR - at-risk churn = "
                     f"{_cs['arr_actual']:,.2f} - {_cs['at_risk_churn']:,.2f} = "
                     f"{_cs['arr_post_at_risk_churn']:,.2f}"
                     + (" (scaled off the certified ratio)"
                        if certified_ratio_actual is not None else "")),
                    (f"breakeven_arr = indebtedness / max_ratio = "
                     f"{_cs['indebtedness']:,.2f} / {_cs['max_ratio']}"),
                    "at-risk churn = sum of at-risk entries in the (combined) churn register",
                ]
                if pik_rate is not None:
                    calc_pieces.append(
                        f"PIK drift compounds indebtedness at {pik_rate}/12 per month "
                        "over 1/3/6 months")
                if source_notes:
                    calc_pieces.append("inputs: " + "; ".join(source_notes))
                covenant_stress["calc"] = "; ".join(calc_pieces)

                if covenant_stress.get("ratio_reconstruction_diverges"):
                    exceptions.append({
                        "section": "covenant_stress", "type": "certified_vs_model_ratio_diverges",
                        "detail": (f"certified ratio ({covenant_stress['ratio_actual']}) vs "
                                  f"model-reconstructed ratio ({covenant_stress['ratio_actual_model_reconstruction']}) "
                                  "differ by >=0.03 -- certified figure is reported as ratio_actual "
                                  "per source-tier discipline (borrower-certified > model reconstruction); "
                                  "reconstruction gap likely reflects a covenant-defined-term adjustment "
                                  "(e.g. pro-forma acquisition ARR, or a narrower Indebtedness definition) "
                                  "that the model cannot fully reproduce."),
                    })
                if covenant_stress.get("ratio_post_atrisk_churn") is not None and \
                        covenant_stress["ratio_post_atrisk_churn"] > max_ratio and \
                        covenant_stress.get("ratio_actual", 0) <= max_ratio:
                    exceptions.append({
                        "section": "covenant_stress", "type": "at_risk_churn_would_breach",
                        "detail": (f"ratio would move from {covenant_stress['ratio_actual']} to "
                                  f"{covenant_stress['ratio_post_atrisk_churn']} "
                                  f"(max {max_ratio}) if at-risk churn materializes"),
                    })
        except (OSError, ValueError, KeyError) as e:
            exceptions.append({"section": "covenant_stress", "type": "compute_error", "error": str(e)})

    # ================================================================
    # 4) Cross-module checks (churn/contraction names vs AR aging placeholder)
    # ================================================================
    cross_module_checks = [
        {"customer": c["customer"], "flag": f"{c['status']} (${c['arr']:,.0f} ARR) -- "
                                            "cross-reference against AR aging top-N "
                                            "(leaving AND not paying signal)."}
        for c in churn_reg
    ]

    # ================================================================
    # 5) Tie-outs (retention closing ARR vs certificate vs model x12)
    # ================================================================
    tie_outs = []
    if arr_bridge.get("available") and covenant_stress.get("available"):
        diff = round(arr_bridge["closing"] - covenant_stress["arr_actual"], 2)
        tie_outs.append({
            "what": "Retention Analysis closing ARR vs certified/derived ARR (covenant basis)",
            "retention_closing": arr_bridge["closing"],
            "covenant_basis_arr": covenant_stress["arr_actual"],
            "difference": diff,
            "ties": abs(diff) <= max(tie_tol, abs(covenant_stress["arr_actual"]) * 0.02),
            "source_ref": list(bridge_refs) + list(cs_arr_refs),
            "calc": (f"retention closing ARR {arr_bridge['closing']:,.2f} minus "
                     f"covenant-basis ARR {covenant_stress['arr_actual']:,.2f} = "
                     f"{diff:+,.2f}; ties if |difference| <= "
                     f"max(tie_tolerance {tie_tol}, 2% of covenant ARR)"),
            "note": ("Retention basis is 'organic' when a basis break exists, while the "
                    "covenant/certificate ARR is typically combined/pro-forma -- a "
                    "difference here may be basis, not error." if arr_bridge.get("basis") == "organic"
                    else None),
        })
    if deferred_revenue.get("available") and deferred_revenue.get("bs_tie_out", {}).get("ties") is not None:
        tie_outs.append({
            "what": "Deferred Revenue schedule closing vs BS deferred revenue line",
            **{k: v for k, v in deferred_revenue["bs_tie_out"].items()},
        })

    results = [
        {"section": "arr_bridge", **arr_bridge},
        {"section": "retention", **retention},
        {"section": "churn_register", "entries": churn_reg},
        {"section": "deferred_revenue", **deferred_revenue},
        {"section": "covenant_stress", **covenant_stress},
        {"section": "cross_module_checks", "entries": cross_module_checks},
        {"section": "tie_outs", "entries": tie_outs},
    ]
    return ml.conclusion(
        module="revenue-quality",
        period=month_label,   # conclusion() normalizes to the ISO YYYY-MM stamp
        results=results,
        exceptions=exceptions,
        source_index=src_idx,
        # Every figure here is in dollars: the three inputs are borrower
        # documents stating dollars, and the model reads were converted. The
        # borrower's own caption is deliberately NOT carried over -- it describes
        # the model, and repeating "$ in thousands" beside scale 1 would say two
        # different things about the same figure.
        unit=ml.ReportingUnit(1, unit.currency),
        engine=("revenue-quality v2 (v1 math + conclusion envelope + "
                "per-figure source refs/calc)"),
        borrower=borrower,
        methodology=("Retention Analysis and Deferred Revenue Schedule are management-prepared/"
                     "unaudited; certificate values (where used) are borrower-certified. Model "
                     "figures (deferred revenue, recurring revenue, indebtedness) are read from "
                     "the canonical monthly financials model via monitor_lib. Organic ARR bridge "
                     "excludes the acquired cohort when deal-context indicates a basis break; "
                     "combined is reported alongside, never in place of, organic."),
    )


# ===========================================================================
# CLI
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="Revenue-quality (ARR / retention / deferred revenue) analysis")
    ap.add_argument("--config", "--borrower-config", dest="config", required=True,
                    help="borrower-config.json")
    ap.add_argument("--period", required=True, help='e.g. "2026-05"')
    ap.add_argument("--retention-file",
                    help="the period's ARR / retention workbook, path as configured")
    ap.add_argument("--deferred-revenue-file",
                    help="the period's deferred-revenue schedule, path as configured")
    ap.add_argument("--certificate-file",
                    help="the period's compliance certificate, path as configured")
    ap.add_argument("--out", help="output .json path (default: stdout only)")
    ap.add_argument("--run-id",
                    help="the run this output belongs to, minted once by the "
                         "orchestrator for the whole run (or set CREDIT_MONITOR_RUN_ID)")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id   # run_stamp() reads it

    cfg = ml.load_config(args.config)
    # Which unit the model's figures are in — this module reads a few of them and
    # states everything in dollars. Undeclared, it skips with a plain reason.
    try:
        unit = ml.reporting_unit(cfg)
    except ml.ConfigError as exc:
        out = ml.skip_record("revenue-quality", exc, period=args.period,
                             borrower=cfg.get("borrower_name"))
        if args.out:
            print(f"Wrote {ml.write_output(out, args.out)}: SKIPPED -- {exc}")
        else:
            print(json.dumps(out, indent=2))
        return

    out = compute(cfg, args.period, unit, config_path=args.config,
                  retention_file=args.retention_file,
                  deferred_revenue_file=args.deferred_revenue_file,
                  certificate_file=args.certificate_file)

    if args.out:
        out_path = ml.write_output(out, args.out)
        # The figures become rows beside the output, so a question that spans
        # the book can reach them without re-deriving this file's shape.
        rows_emit.write_rows(out, out_path, deal_name=cfg.get("borrower_name"))
        if out.get("skipped"):
            print(f"Wrote {out_path}: SKIPPED -- {out.get('reason')}")
        else:
            print(f"Wrote {out_path}: {len(out['results'])} result section(s), "
                  f"{len(out['exceptions'])} exceptions")
    else:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
