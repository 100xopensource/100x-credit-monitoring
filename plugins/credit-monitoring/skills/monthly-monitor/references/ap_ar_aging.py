#!/usr/bin/env python3
"""
AP/AR aging-analysis module for the credit portfolio monitor.

Parses the borrower's A/R and A/P aging-summary exports for the latest month and
the prior month, and reports, for each:
    * total and bucket distribution (Current / 1-30 / 31-60 / 61-90 / >90)
    * % over 90 days
    * top-N counterparty concentration (name + $ + % of total)
    * a reconciliation of the counterparty rows counted against the export's own
      TOTAL row — the shares above divide by that total, so a gap between the
      two makes every share wrong by the gap and raises an exception
    * DSO (A/R) and DPO (A/P), with the month-over-month trend
    * a tie-out of the aging total against the balance sheet (model)

BORROWER-AGNOSTIC
=================
Nothing here is hardcoded to a particular borrower. Everything borrower-specific
is supplied by config (borrower-config.json):

  * model_lines            canonical metric key -> the borrower's model row label
                           (accounts_receivable, accounts_payable, revenue,
                           operating_cogs). Read from the model via monitor_lib.
  * aging.file_format      "auto" (sniff), "spreadsheetml" (Excel-2003 XML, e.g.
                           NetSuite exports) or "xlsx" (e.g. QuickBooks exports).
  * aging.dpo_basis        which model line to use as the DPO denominator:
                           "operating_cogs" (default) or "revenue" / any model_lines key.
  * aging.tie_tolerance    tolerance for the aging-vs-BS tie-out, in the MODEL's
                           unit (default 1.0 -- $1,000 on a model in thousands).
  * aging.top_n            counterparty concentration depth (default 5).
  * aging.amount_scale     divisor putting the aging export's dollars in the
                           model's units (default 1; use 1000 for an export in
                           raw USD against a model reported in USD '000s).
                           REQUIRED once model.scale is not 1 -- the export is on
                           the ledger's terms and the model on its own, and which
                           cannot be read off the file.

DESIGN
======
* Aging files are read through `_read_grid`, which handles BOTH SpreadsheetML
  (Excel-2003 XML) and .xlsx workbooks. Format is auto-sniffed from the file's
  magic bytes unless pinned in config.
* Every dollar figure is parsed with `ml.safe_float` so number handling stays
  consistent with the rest of the monitor.
* The balance-sheet AR/AP figures and the income-statement revenue / operating
  COGS used for the tie-out and for DSO/DPO come from the canonical monthly
  financials model THROUGH `monitor_lib`. This module never opens the model itself.
* Output is shaped with `ml.conclusion`, the one standard conclusion shape.
* PROVENANCE (trace-to-file + calculation records): every
  memo-surfaceable record carries a structured `source_ref` pointing into the
  output's top-level `source_files` path list, plus a plain-text `calc` note
  saying how the figure was arrived at. Aging-file reads are row-wise refs
  ({file, sheet, row} — 1-based rows as they appear in the export grid); model
  reads carry {file, sheet, row, cells, period} from Model.provenance. Paths
  are recorded AS GIVEN (the CLI/config strings, before sandbox resolution) so
  downstream consumers can join store hyperlinks. validate_output.py gates the
  output shape before archiving.

DSO/DPO basis (single month, volatile):
    DSO = A/R total / monthly revenue                 * days in the month
    DPO = A/P total / monthly DPO-basis line (COGS)   * days in the month
  The day count comes from `monitor_lib.days_in_month`, which is where the day
  basis for every days-metric is defined — the three-statement module's DSO/DPO
  read the same function, so one metric can never carry two bases in one memo.
  Operating COGS is the default DPO base rather than reported Cost of Sales:
  reported Cost of Sales can be distorted by one-time credits (e.g. a duty/tax
  refund flowing through COGS), which would give a nonsense / negative DPO.

THE AGENT SUPPLIES THE FILES
===========================
This engine is handed the aging files it must read. It does not look for them:
no filename patterns, no month-folder conventions, no directory listing. The
calling agent has already opened the borrower's reporting folder, seen which
export is A/R and which is A/P for the month being reported, and passes each
path on the command line. Every borrower's folders are shaped differently, so
the agent settles that once, where it can see the files and ask; the engine
reads exactly what it was given and does the arithmetic.

The path string passed in is recorded verbatim in `source_files` (see
PROVENANCE below), so pass the path as configured — the same string as in
borrower-config — not a /sessions/... sandbox mount, which is dead next run.

Usage:
    python ap_ar_aging.py \
        --borrower-config <borrower-config.json> \
        --ar-latest <AR aging> --ap-latest <AP aging> \
        [--ar-prior <AR aging> --ap-prior <AP aging>] \
        [--model <model workbook .xlsx>] \
        --latest-month "Apr 2026" [--prior-month "Mar 2026"] \
        --out <output .json> [--borrower "..."]
  --model defaults to paths.model_output_path from the borrower-config. A prior
  month is optional: without it, the month-over-month trend is omitted, which is
  the same behavior as a prior month whose files had not arrived.
"""

import argparse
import os
import re
import sys

# --- Resolve the shared lib robustly: walk up from this file to the plugin
# root, then lib/ (same pattern as covenant_compliance.py / budget_vs_actual.py). ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_LIB_DIR = os.path.join(_PLUGIN_ROOT, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import monitor_lib as ml
import rows_emit

import openpyxl

# Normalized aging buckets, in order.
BUCKETS = ["current", "1-30", "31-60", "61-90", ">90"]

# Rows that are not counterparties (header echoes / totals / blank labels).
_NON_PARTY = {"vendor", "customer", "total", "total - vendor", "total - customer", ""}

# Subtotal forms can open with "Total - Example Market", "Total: Vendors", or
# "Total, Northeast". A separator after the word marks a subtotal while preserving
# counterparties whose legal names begin with "Total".
_TOTAL_PREFIX_RE = re.compile(r"^total\s*[-–—:;,/|]")

# Roll-up tolerance: a parent row whose Total equals the rows beneath it within
# this many dollars is that group's subtotal, not a counterparty.
_ROLLUP_TOL = 1.0

# --- Config defaults (all overridable via borrower-config `aging` block) -----
DEFAULT_TIE_TOL = 1.0
DEFAULT_TOP_N = 5
DEFAULT_DPO_BASIS = "operating_cogs"
DEFAULT_AMOUNT_SCALE = 1.0

ConfigError = ml.ConfigError


def load_aging_config(cfg):
    """Pull the ap-ar-aging config surface out of a borrower-config dict."""
    model_lines = cfg.get("model_lines") or {}
    aging = cfg.get("aging") or {}
    _require_amount_scale(cfg, aging)
    dpo_key = aging.get("dpo_basis", DEFAULT_DPO_BASIS)
    lines = {
        "accounts_receivable": ml.require_line(model_lines, "accounts_receivable", "ap-ar-aging"),
        "accounts_payable": ml.require_line(model_lines, "accounts_payable", "ap-ar-aging"),
        "revenue": ml.require_line(model_lines, "revenue", "ap-ar-aging"),
        "dpo_basis": ml.require_line(model_lines, dpo_key, "ap-ar-aging"),
        "dpo_basis_key": dpo_key,
    }
    return {
        "lines": lines,
        "file_format": (aging.get("file_format") or "auto").lower(),
        "tie_tol": float(aging.get("tie_tolerance", DEFAULT_TIE_TOL)),
        "top_n": int(aging.get("top_n", DEFAULT_TOP_N)),
        "amount_scale": float(aging.get("amount_scale") or 0) or DEFAULT_AMOUNT_SCALE,
    }


def _require_amount_scale(cfg, aging):
    """A borrower whose model is not in whole dollars has to say what unit its
    aging export is in.

    The export comes out of the ledger, on its own terms, and the model is on
    theirs -- the two are only the same when the model is in whole dollars.
    Undeclared, the aging figures pass through as they stand and get published
    under the model's unit: a $2,400,000 receivable read as $2.4bn once the memo
    multiplies by the scale, and the aging-vs-balance-sheet tie-out fails by a
    factor of a thousand while every gate passes.
    """
    scale = (cfg.get("model") or {}).get("scale")
    if (not isinstance(scale, (int, float)) or isinstance(scale, bool)
            or scale == 1 or aging.get("amount_scale")):
        return
    raise ConfigError(
        f"aging.amount_scale is not set in borrower-config, and this model is not "
        f"in whole dollars (model.scale {scale:g}). Say what one figure in the "
        f"aging export stands for, as a divisor onto the model's unit: "
        f"{scale:g} for an export in raw dollars (the usual case -- a ledger "
        f"prints dollars whatever the model does), or 1 if the export is already "
        f"in the model's unit. It cannot be guessed from the file.")


# ===========================================================================
# File reading -- SpreadsheetML (Excel-2003 XML) or .xlsx, auto-sniffed
# ===========================================================================
def _sniff_format(path):
    """Sniff the aging-file format from its magic bytes: PK.. -> xlsx (zip),
    otherwise SpreadsheetML / XML text."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return "spreadsheetml"
    if head[:2] == b"PK":
        return "xlsx"
    return "spreadsheetml"


def _read_grid(path, file_format="auto"):
    """Read an aging export into (sheet_name, rows) — rows is a list of rows,
    each a list of cell values; sheet_name feeds the row-wise source refs.

    Handles SpreadsheetML (via monitor_lib) and .xlsx (via openpyxl). Format is
    auto-sniffed unless pinned. Only the first worksheet is used (aging exports
    are single-sheet)."""
    fmt = (file_format or "auto").lower()
    if fmt == "auto":
        fmt = _sniff_format(path)
    if fmt == "xlsx":
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        name = wb.sheetnames[0]
        ws = wb[name]
        return name, [[c.value for c in row] for row in ws.iter_rows()]
    sheets = ml.read_spreadsheetml(path)
    if not sheets:
        raise ValueError(f"no worksheet in {path}")
    name = next(iter(sheets))
    return name, sheets[name]


# ===========================================================================
# Aging-grid parsing
# ===========================================================================
def _classify_bucket(header_text):
    """Map an aging column header to a normalized bucket key, or None.

    Robust to rolling date ranges: detection keys off the parenthetical tag
    ("(30)"/"(60)"/"(90)"), ">90"/"over 90"/"Before ...", and "Current", which
    are the common aging-export conventions (NetSuite / QuickBooks and similar)."""
    h = ml.norm_label(header_text)
    if h == "" or h == "total" or "open balance" in h:
        return None
    if "current" in h:
        return "current"
    if h == "at date":
        # NetSuite Aged Receivable/Payable Summary artifact: the leftmost
        # bucket column (not-yet-due amounts) sometimes keeps this label
        # instead of "Current" when the saved search is pulled as a summary
        # report. Verified against the export's own arithmetic (this column
        # + the named buckets reconciles to the row's Total, to the cent) --
        # not a borrower-specific label match, a NetSuite report-type quirk.
        return "current"
    if (">90" in h or "> 90" in h or "over 90" in h or "90+" in h or "90 +" in h
            or "91+" in h or "91 and over" in h or h.startswith("before")
            or h.startswith("91") or h == "older" or "older" in h):
        return ">90"
    if "(90)" in h or "61-90" in h or "61 - 90" in h:
        return "61-90"
    if "(60)" in h or "31-60" in h or "31 - 60" in h:
        return "31-60"
    if "(30)" in h or "1-30" in h or "1 - 30" in h:
        return "1-30"
    return None


def _is_non_party(label):
    """True for a row that is not a counterparty: a header echo, a blank label,
    or any subtotal row — "Total", "Total - Vendor", "Total - Example Market",
    and the "<Group> Total" form exports use for group roll-ups.
    Counting one of these as a counterparty double-counts its members and can
    make one counterparty exceed the grand total.

    In the "Total …" prefix form a separator always follows the word. A vendor
    or customer whose legal name opens with it stays the counterparty it is;
    the bare "Total <name>"
    subtotal is caught by `_is_group_subtotal` on the evidence that <name> is a
    counterparty read above it."""
    l = ml.norm_label(label)
    return (l in _NON_PARTY or bool(_TOTAL_PREFIX_RE.match(l))
            or l.endswith("total") or l.endswith("total:"))


def _is_group_subtotal(label, seen):
    """True for a bare "Total <name>" row whose <name> is a counterparty already
    read above it — the subtotal a QuickBooks-style export prints beneath a
    parent's own members. A legal name beginning with "Total" and no matching
    parent row above it is read as a counterparty."""
    l = ml.norm_label(label)
    if not l.startswith("total "):
        return False
    rest = l[len("total "):].strip()
    return any(ml.norm_label(c["name"]) == rest for c in seen)


def _mark_rollups(cands, grand_total):
    """Flag each candidate row that is a group parent — its Total AND every
    bucket equal the contiguous rows beneath it — so group roll-ups and
    intercompany parents are not counted alongside their own members.

    Gated on the file's own arithmetic, so the heuristic only ever fires on a
    file that needs it: rows are stripped only when they overshoot the export's
    TOTAL row, and the strip is kept only when it brings them back to that total
    without going under it. On an export whose rows already add up, every row is
    a counterparty however the sums line up — two ordinary counterparties that
    happen to sum to a third are not a group — and an export with no TOTAL row
    to check against is read as it stands.

    Requiring the buckets to match too, not just the Total, narrows which rows
    are candidates once the gate is open. Returns the list of rolled-up
    (name, row) pairs, in file order."""
    if grand_total is None:
        return []
    if sum(c["total"] for c in cands) - grand_total <= _ROLLUP_TOL:
        return []
    rolled = []
    n = len(cands)
    for i, parent in enumerate(cands):
        if parent["rollup"] or not parent["total"]:
            continue
        running_total = 0.0
        running_buckets = {b: 0.0 for b in BUCKETS}
        for k in range(i + 1, n):
            child = cands[k]
            if child["rollup"]:
                break
            running_total += child["total"]
            for b in BUCKETS:
                running_buckets[b] += child["buckets"][b]
            if k - i < 2:
                continue          # a single row is a sibling, not a group
            if abs(running_total - parent["total"]) > _ROLLUP_TOL:
                continue
            if any(abs(running_buckets[b] - parent["buckets"][b]) > _ROLLUP_TOL
                   for b in BUCKETS):
                continue
            parent["rollup"] = True
            rolled.append((parent["name"], parent["row"]))
            break
    if rolled and sum(c["total"] for c in cands
                      if not c["rollup"]) < grand_total - _ROLLUP_TOL:
        # The strip took the rows past the TOTAL row, so the overshoot was not
        # these rows. Put them back and let the reconciliation exception speak.
        for c in cands:
            c["rollup"] = False
        return []
    return rolled


def parse_aging(path, file_format="auto", given_path=None,
                amount_scale=DEFAULT_AMOUNT_SCALE):
    """Parse an A/R or A/P aging-summary export into a structured dict.

    Returns:
        {
          "as_of": <str or None>,            # the "As of ..." line, if present
          "total": <float>,                  # grand total (from the TOTAL row)
          "buckets": {bucket: $},            # bucket distribution, summed rows
          "parties": [ {"name", "total", "buckets": {...}, "row"}, ... ],
          "parties_total": <float>,          # sum of the counterparty rows
          "rolled_up_rows": [ [name, row], ... ],  # group parents excluded
          "total_col": <int>,                # index of the Total column
          "sheet": <str>,                    # worksheet name (for source refs)
          "header_row": <int>,               # 1-based header-row number
          "total_row": <int or None>,        # 1-based TOTAL-row number
          "given_path": <str>,               # as-given path (what refs record)
          "amount_scale": <float>,           # divisor applied to every dollar
        }
    Every dollar returned is divided by `amount_scale`, so the caller reads the
    aging in the model's units (a NetSuite export in raw USD against a model in
    USD '000s carries a scale of 1000). The export's own arithmetic — the TOTAL
    row, the roll-up and implicit-grand-total detection — is read in the file's
    own dollars first, then the figures are scaled once on the way out.

    Row numbers are 1-based positions in the export grid (identical to workbook
    rows for .xlsx; SpreadsheetML rows are read in document order). The TOTAL
    row is identified by a label of "Total" / "Total - Vendor" /
    "Total - Customer"; its grand total is the authoritative total, and it is
    also what gates roll-up detection — a group parent is excluded only on a
    file whose rows overshoot it, so a file that already adds up keeps every row
    it lists. The parsed counterparty rows are summed separately so compute() can
    reconcile the two and flag a gap instead of publishing shares against a
    denominator the parts never added up to."""
    sheet, rows = _read_grid(path, file_format)
    if not rows:
        raise ValueError(f"no rows parsed from {path}")

    as_of = None
    # The "As of ..." line is read across the whole top row, not just its first
    # cell: an export that indents its date into column B otherwise returns no
    # as_of at all and the snapshot loses the date the export itself states.
    for r in rows[:6]:
        if not r:
            continue
        for c in r:
            if isinstance(c, str) and c.strip().lower().startswith("as of"):
                as_of = c.strip()
                break
        if as_of:
            break

    # Find the header row: the one whose first cell is Customer/Vendor and which
    # contains a "Total" column.
    header_idx = None
    for i, r in enumerate(rows):
        if not r:
            continue
        if not any(str(c).strip().lower() == "total" for c in r):
            continue
        first = "" if r[0] is None else ml.norm_label(r[0])
        has_bucket_col = any(_classify_bucket(c) is not None for c in r)
        # Some exports (NetSuite Aged Receivable/Payable Summary) print the
        # header row with a blank name-column cell rather than a literal
        # "Customer"/"Vendor" label -- the "As of ..." line above already
        # says which side it is. Accept that shape too, gated on the row
        # actually carrying recognizable aging-bucket columns so a stray
        # "...Total..." row elsewhere in the sheet cannot masquerade as one.
        # An empty name cell and a whitespace-only one are both blank to a
        # reader, so both open this shape: an empty cell (None from the grid)
        # is read as blank up front, and `first` normalizes the padded one.
        if first in ("customer", "vendor") or (not first and has_bucket_col):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"could not locate aging header row in {path}")

    header = rows[header_idx]
    total_col = next(i for i, c in enumerate(header)
                     if str(c).strip().lower() == "total")
    bucket_cols = {}  # col_index -> bucket_key
    for ci, h in enumerate(header):
        b = _classify_bucket(h)
        if b is not None:
            bucket_cols[ci] = b

    candidates = []
    total_row_vals = None
    total_row_num = None

    for rn, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not r:
            continue
        # An empty name cell (None from the grid) leaves the name empty, which
        # `_is_non_party` drops: the unlabeled rows a real export carries are
        # its own grand total and its adjustment lines, not counterparties.
        raw_name = "" if r[0] is None else str(r[0])
        name = re.sub(r"\s+", " ", raw_name).replace("\xa0", " ").strip()
        key = ml.norm_label(name)
        # A "Total"/"Total - Vendor"/"Total - Customer" row -> authoritative total
        if key in ("total", "total - vendor", "total - customer"):
            tv = ml.safe_float(r[total_col]) if total_col < len(r) else None
            if tv is not None:
                total_row_vals = r
                total_row_num = rn
            continue
        if _is_non_party(name) or _is_group_subtotal(name, candidates):
            continue
        ptot = ml.safe_float(r[total_col]) if total_col < len(r) else None
        if ptot is None:
            continue  # row with no parsable total (e.g. stray header echo)
        pbuckets = {b: 0.0 for b in BUCKETS}
        for ci, b in bucket_cols.items():
            v = ml.safe_float(r[ci]) if ci < len(r) else None
            if v is not None:
                pbuckets[b] += v
        candidates.append({"name": name, "total": ptot, "buckets": pbuckets,
                           "row": rn, "rollup": False})

    # Authoritative grand total: the TOTAL row's Total cell. It is also what
    # gates roll-up detection, so it is read before the rows are trimmed.
    total_row_grand = (ml.safe_float(total_row_vals[total_col])
                       if total_row_vals is not None else None)

    # Some exports (NetSuite Aged Receivable/Payable Summary) print no row
    # labeled "Total" at all: the very first row under the header (the
    # report's own name, e.g. "Aged Receivable") IS the grand total -- its
    # Total equals the sum of every row beneath it. Detect that shape on the
    # file's own arithmetic (not by matching the report title text, which
    # would be borrower-specific): when the first candidate row's Total sums
    # the ENTIRE remainder of the file, to the cent, it is the file's
    # implicit TOTAL row, not a counterparty -- pull it out before it is
    # counted twice (once as itself, once via the rows underneath it). A
    # coincidental match against every other row in the file (not a chosen
    # subset) is not a plausible accident -- EXCEPT when there are barely any
    # rows, where "the first row sums the rest" degenerates into "two
    # counterparties hold equal balances", which is an ordinary thing for a
    # ledger to contain. Getting that wrong drops the largest counterparty and
    # halves A/R, then rebases concentration and DSO on the remainder.
    #
    # So the Total tie is not read alone: a row that really is the file's grand
    # total also splits the same way across the aging buckets. Two equal
    # counterparties would have to match bucket for bucket as well, which is a
    # much longer coincidence. All but at most one bucket must agree -- a live
    # export has been seen whose own header-row bucket split disagreed in a
    # single bucket (an apparent booking inconsistency in the export) while its
    # Total still reconciled exactly. The per-counterparty rows are the more
    # granular, internally consistent source, so bucket_distribution is built
    # from them either way.
    if total_row_grand is None and len(candidates) >= 2:
        head, rest = candidates[0], candidates[1:]
        rest_total = sum(c["total"] for c in rest)
        buckets_off = sum(
            1 for b in BUCKETS
            if abs((head["buckets"] or {}).get(b, 0.0)
                   - sum((c["buckets"] or {}).get(b, 0.0) for c in rest)) > _ROLLUP_TOL)
        if abs(head["total"] - rest_total) <= _ROLLUP_TOL and buckets_off <= 1:
            total_row_num = head["row"]
            total_row_grand = head["total"]
            candidates = rest

    rolled = _mark_rollups(candidates, total_row_grand)

    # The export's own arithmetic is settled; put every figure in the model's
    # units on the way out so the tie-out, DSO/DPO and the shares all read on
    # one scale. Shares are ratios, so they are unchanged by it.
    scale = float(amount_scale or 0) or DEFAULT_AMOUNT_SCALE
    parties = [{"name": c["name"], "row": c["row"],
                "total": c["total"] / scale,
                "buckets": {b: c["buckets"][b] / scale for b in BUCKETS}}
               for c in candidates if not c["rollup"]]
    buckets_total = {b: round(sum(p["buckets"][b] for p in parties), 2)
                     for b in BUCKETS}
    parties_total = round(sum(p["total"] for p in parties), 2)

    grand_total = (total_row_grand / scale if total_row_grand is not None
                   else parties_total)

    return {
        "as_of": as_of,
        "total": grand_total,
        "buckets": buckets_total,
        "parties": parties,
        "parties_total": parties_total,
        "rolled_up_rows": [list(x) for x in rolled],
        "total_col": total_col,
        "sheet": sheet,
        "header_row": header_idx + 1,
        "total_row": total_row_num,
        "given_path": str(given_path) if given_path else str(path),
        "amount_scale": scale,
    }


# ===========================================================================
# Metrics
# ===========================================================================
def pct_over_90(parsed):
    t = parsed["total"]
    if not t:
        return None
    return parsed["buckets"].get(">90", 0.0) / t


def bucket_distribution(parsed):
    """Bucket distribution as {bucket: {"amount", "pct_of_total"}}."""
    t = parsed["total"] or 0.0
    out = {}
    for b in BUCKETS:
        amt = parsed["buckets"].get(b, 0.0)
        out[b] = {
            "amount": round(amt, 2),
            "pct_of_total": round(amt / t, 6) if t else None,
        }
    return out


def rows_vs_total(parsed, tol=DEFAULT_TIE_TOL):
    """Reconcile the counterparty rows this parse actually counted against the
    export's own TOTAL row — the check that decides whether the shares reported
    against that total mean anything. Every bucket share and concentration
    share divides by the TOTAL row's grand total while its numerator is summed
    from the rows; when the two disagree, the file holds rows the parse read
    differently than the export did (a group parent, an intercompany line, a
    column the header naming missed), and every share is off by the gap."""
    grand = parsed["total"] or 0.0
    parts = parsed["parties_total"]
    diff = round(parts - grand, 2)
    return {
        "grand_total": round(grand, 2),
        "counterparty_rows_total": parts,
        "difference": diff,
        "reconciles": abs(diff) <= tol,
        "n_counterparties": len(parsed["parties"]),
        "rolled_up_rows": parsed.get("rolled_up_rows") or [],
    }


def top_concentration(parsed, n=DEFAULT_TOP_N):
    """Top-n counterparties by total, each with name, $ and % of total.

    Each entry carries the counterparty's 1-based source row (`row`) so the
    caller can attach a row-wise source ref (the ref consumes the field)."""
    t = parsed["total"] or 0.0
    ranked = sorted(parsed["parties"], key=lambda p: p["total"], reverse=True)
    out = []
    for p in ranked[:n]:
        out.append({
            "name": p["name"],
            "amount": round(p["total"], 2),
            "pct_of_total": round(p["total"] / t, 6) if t else None,
            "row": p["row"],
        })
    return out


def dso(ar_total, monthly_revenue, days):
    """DSO = A/R / monthly revenue * the month's calendar days. None if revenue
    is missing/zero. The day basis comes from `monitor_lib.days_in_month`, the
    one place it is defined, so this DSO and the three-statement module's DSO
    are the same metric on the same basis."""
    if not monthly_revenue:
        return None
    return ar_total / monthly_revenue * float(days)


def dpo(ap_total, monthly_basis, days):
    """DPO = A/P / monthly DPO-basis line * the month's calendar days. None if
    the basis is missing/<=0. Same shared day basis as dso()."""
    if not monthly_basis or monthly_basis <= 0:
        return None
    return ap_total / monthly_basis * float(days)


def tie_out(aging_total, bs_value, label, tie_tol=DEFAULT_TIE_TOL):
    """Compare an aging total to the balance-sheet value. Returns a dict."""
    diff = None if bs_value is None else round(aging_total - bs_value, 2)
    ties = (bs_value is not None and abs(aging_total - bs_value) <= tie_tol)
    return {
        "what": label,
        "aging_total": round(aging_total, 2),
        "balance_sheet": round(bs_value, 2) if bs_value is not None else None,
        "difference": diff,
        "ties": ties,
    }


# ===========================================================================
# Per-figure provenance + calculation records
# ===========================================================================
def _party_rows_span(parsed):
    """'8-50' — the 1-based row span the counterparty rows were read from
    (a single row stays a single number). None when no parties parsed."""
    rows = [p["row"] for p in parsed["parties"]]
    if not rows:
        return None
    lo, hi = min(rows), max(rows)
    return str(lo) if lo == hi else f"{lo}-{hi}"


def _aging_refs(idx, parsed, include_span=True):
    """Row-wise refs into an aging export: the TOTAL row (grand total) and,
    when `include_span`, the counterparty-row span the buckets/parties were
    summed over. Never empty — a degenerate export falls back to its header
    row so the ref still locates the read."""
    refs = []
    if parsed.get("total_row"):
        refs.append(idx.ref(parsed["given_path"], sheet=parsed["sheet"],
                            row=parsed["total_row"]))
    span = _party_rows_span(parsed)
    if span and (include_span or not refs):
        refs.append(idx.ref(parsed["given_path"], sheet=parsed["sheet"], row=span))
    if not refs:
        refs.append(idx.ref(parsed["given_path"], sheet=parsed["sheet"],
                            row=parsed["header_row"]))
    return refs


def _total_src(parsed):
    """Plain-text fragment for calc notes: where an aging grand total came from,
    including the unit scaling that stands between the file and the figure."""
    scale = parsed.get("amount_scale") or DEFAULT_AMOUNT_SCALE
    scaled = ("" if scale == 1 else
              f", divided by the configured aging amount_scale of {scale:,.0f} "
              "so the aging reads in the model's units")
    if parsed.get("total_row"):
        return (f"the aging export's TOTAL row (row {parsed['total_row']}, "
                f"Total column){scaled}")
    span = _party_rows_span(parsed)
    if span:
        return (f"the sum of the Total column across counterparty rows {span} "
                f"(no TOTAL row found){scaled}")
    return "an empty aging grid (no TOTAL row or counterparty rows parsed)"


def _snapshot_block(side, parsed, month, flow_value, days_value,
                    days_metric_name, top_n, idx, model, flow_label,
                    tie_tol=DEFAULT_TIE_TOL):
    """One month's aging snapshot for one side, with record-level source refs
    + calc covering total / buckets / pct_over_90 / DSO-or-DPO, and per-entity
    row refs on each top-N concentration entry."""
    t = parsed["total"]
    p90 = pct_over_90(parsed)
    n_parties = len(parsed["parties"])
    span = _party_rows_span(parsed)
    recon = rows_vs_total(parsed, tie_tol)

    refs = _aging_refs(idx, parsed)
    flow_prov = model.provenance(flow_label, sheet="Income Statement", month=month)
    if flow_prov:
        refs.append(idx.ref(model.given_path, **flow_prov))

    flow_name = ("monthly revenue" if days_metric_name == "dso_days"
                 else "the monthly DPO basis")
    days = ml.days_in_month(month)
    bits = [f"total {t:,.2f} = {_total_src(parsed)}",
            f"bucket amounts = per-bucket columns summed across {n_parties} "
            f"counterparty rows" + (f" (rows {span})" if span else "")]
    if recon["reconciles"]:
        bits.append(f"those rows sum to {recon['counterparty_rows_total']:,.2f}, "
                    f"which reconciles to the grand total, so every share below "
                    f"divides by a total its parts add up to")
    else:
        bits.append(f"those rows sum to {recon['counterparty_rows_total']:,.2f} "
                    f"against a grand total of {recon['grand_total']:,.2f} "
                    f"({recon['difference']:,.2f} apart) — the shares below are "
                    f"off by that gap and the exception says so")
    if recon["rolled_up_rows"]:
        bits.append("group subtotal rows excluded from the counterparty list: "
                    + ", ".join(f"{name} (row {row})"
                                for name, row in recon["rolled_up_rows"]))
    if p90 is not None:
        bits.append(f"pct_over_90 = >90 bucket "
                    f"{parsed['buckets'].get('>90', 0.0):,.2f} / total")
    if days_value is not None:
        where = (f"model '{flow_prov['sheet']}' row {flow_prov['row']} "
                 f"cell {flow_prov['cells']}" if flow_prov else "the model")
        bits.append(f"{days_metric_name} {days_value:,.2f} = total / {flow_name} "
                    f"('{flow_label}' = {flow_value:,.2f} for {month}, {where}) "
                    f"* {days} (calendar days in {month})")
    else:
        bits.append(f"{days_metric_name} unavailable ({flow_name} "
                    f"'{flow_label}' missing or non-positive for {month})")
    calc = f"{month} {side} aging snapshot: " + "; ".join(bits)

    top = top_concentration(parsed, top_n)
    for entry in top:
        prow = entry.pop("row")
        entry["source_ref"] = [idx.ref(parsed["given_path"],
                                       sheet=parsed["sheet"], row=prow)]
        entry["calc"] = (f"amount = Total column of this counterparty's row {prow} "
                         f"in the {month} {side} aging export; pct_of_total = "
                         f"amount / {side} aging grand total {t:,.2f}")

    return {
        "month": month,
        "as_of": parsed["as_of"],
        "total": round(t, 2),
        "pct_over_90": round(p90, 6) if p90 is not None else None,
        "bucket_distribution": bucket_distribution(parsed),
        f"top{top_n}_concentration": top,
        days_metric_name: round(days_value, 2) if days_value is not None else None,
        "days_basis": days,
        "n_counterparties": n_parties,
        "rows_reconciliation": recon,
        "source_ref": refs,
        "calc": calc,
    }


def _tie_refs_calc(idx, model, parsed, tie, prov, line_label, month, tol):
    """source_ref + calc for a tie-out record: the aging export's TOTAL row
    against the model's balance-sheet cell for the same month."""
    refs = _aging_refs(idx, parsed, include_span=False)
    if prov:
        refs.append(idx.ref(model.given_path, **prov))
    if tie["balance_sheet"] is None:
        calc = (f"aging total {tie['aging_total']:,.2f} from {_total_src(parsed)}; "
                f"balance-sheet line '{line_label}' not found in the model for "
                f"{month}, so the tie-out is not verifiable")
    else:
        where = (f"model '{prov['sheet']}' row {prov['row']} cell {prov['cells']}"
                 if prov else "the model")
        calc = (f"difference {tie['difference']:,.2f} = aging total "
                f"{tie['aging_total']:,.2f} (from {_total_src(parsed)}) - "
                f"balance-sheet '{line_label}' {tie['balance_sheet']:,.2f} "
                f"({where}, {month}); ties when |difference| <= {tol:,.2f}")
    return refs, calc


# ===========================================================================
# Compute
# ===========================================================================
def _side_result(side, parsed_latest, parsed_prior, latest_month, prior_month,
                 flow_latest, flow_prior, days_metric_name, top_n,
                 idx, model, flow_label, tie_tol=DEFAULT_TIE_TOL):
    """Build a per-side (AR or AP) result block with record-level provenance."""
    days_fn = dso if side == "AR" else dpo
    days_latest = days_fn(parsed_latest["total"], flow_latest,
                          ml.days_in_month(latest_month))
    days_prior = (days_fn(parsed_prior["total"], flow_prior,
                          ml.days_in_month(prior_month))
                  if parsed_prior and prior_month else None)

    block = {
        "side": side,
        "latest": _snapshot_block(side, parsed_latest, latest_month, flow_latest,
                                  days_latest, days_metric_name, top_n,
                                  idx, model, flow_label, tie_tol),
    }
    if parsed_prior is not None:
        block["prior"] = _snapshot_block(side, parsed_prior, prior_month, flow_prior,
                                         days_prior, days_metric_name, top_n,
                                         idx, model, flow_label, tie_tol)
        block["trend"] = {
            "total_change": round(parsed_latest["total"] - parsed_prior["total"], 2),
            f"{days_metric_name}_prior": round(days_prior, 2) if days_prior is not None else None,
            f"{days_metric_name}_latest": round(days_latest, 2) if days_latest is not None else None,
            "note": ("Single-month aging snapshots; DSO/DPO use one month of "
                     "flow and are volatile month to month. Each month's days "
                     "metric uses that month's calendar days."),
            "source_ref": (_aging_refs(idx, parsed_latest, include_span=False)
                           + _aging_refs(idx, parsed_prior, include_span=False)),
            "calc": (f"total_change = {latest_month} {side} aging total "
                     f"{parsed_latest['total']:,.2f} (from {_total_src(parsed_latest)}) "
                     f"- {prior_month} total {parsed_prior['total']:,.2f} "
                     f"(from {_total_src(parsed_prior)}); "
                     f"{days_metric_name}_latest/_prior repeated from this side's "
                     f"latest/prior records"),
        }
    return block


def compute(ar_latest, ap_latest, ar_prior, ap_prior, model, lines,
            latest_month, prior_month, unit, tie_tol_model_units=DEFAULT_TIE_TOL,
            top_n=DEFAULT_TOP_N, borrower_name=None):
    """unit is the borrower's ReportingUnit -- what one model figure stands for.
    It is on the output's envelope so a reader knows what these totals are in, and
    it is what the note below states out loud.

    The aging figures arrive already on the model's unit (amount_scale did that
    when the export was parsed), and so is the tie-out tolerance: it is a number
    the analyst set while looking at the model, so a thousands-scale deal's
    default 1.0 is a $1,000 tie -- loose, and said out loud in the note rather
    than tightened behind their back."""
    tie_tol = tie_tol_model_units
    results = []
    exceptions = []

    # A prior month's aging is only readable as a month: every prior figure —
    # its DSO/DPO, its calc note, the trend — is stamped with `prior_month`.
    # Files without that label are set aside and the note says which flag brings
    # them back, so the latest month still reports.
    prior_files_without_month = not prior_month and (ar_prior is not None
                                                     or ap_prior is not None)
    if not prior_month:
        ar_prior = ap_prior = None

    # One source index for the whole output: every distinct as-given
    # path is stored once in `source_files`; per-figure refs point into it.
    # Register the aging exports up front, in reading order, so the file list
    # is stable however refs are emitted. SourceIndex says each path as its
    # shared folder, so a mount path the driving agent passed still records
    # as somewhere another reader can open.
    _mp = getattr(model, "given_path", None)
    src_idx = ml.SourceIndex(source_map=ml.model_source_map(_mp), model_path=_mp)
    for parsed in (ar_latest, ap_latest, ar_prior, ap_prior):
        if parsed is not None:
            src_idx.add(parsed["given_path"])

    # --- model-sourced inputs (through monitor_lib) ---
    bs_ar = model.get_line(lines["accounts_receivable"], month=latest_month, sheet="Balance Sheet")
    bs_ap = model.get_line(lines["accounts_payable"], month=latest_month, sheet="Balance Sheet")
    rev_latest = model.get_line(lines["revenue"], month=latest_month, sheet="Income Statement")
    rev_prior = model.get_line(lines["revenue"], month=prior_month, sheet="Income Statement") if prior_month else None
    dpo_latest = model.get_line(lines["dpo_basis"], month=latest_month, sheet="Income Statement")
    dpo_prior = model.get_line(lines["dpo_basis"], month=prior_month, sheet="Income Statement") if prior_month else None
    bs_ar_prov = model.provenance(lines["accounts_receivable"], sheet="Balance Sheet", month=latest_month)
    bs_ap_prov = model.provenance(lines["accounts_payable"], sheet="Balance Sheet", month=latest_month)

    # --- AR ---
    ar_block = _side_result(
        "AR", ar_latest, ar_prior, latest_month, prior_month,
        rev_latest, rev_prior, "dso_days", top_n,
        src_idx, model, lines["revenue"], tie_tol)
    ar_tie = tie_out(ar_latest["total"], bs_ar,
                     f"A/R aging total vs BS '{lines['accounts_receivable']}'", tie_tol)
    ar_tie["source_ref"], ar_tie["calc"] = _tie_refs_calc(
        src_idx, model, ar_latest, ar_tie, bs_ar_prov,
        lines["accounts_receivable"], latest_month, tie_tol)
    ar_block["tie_out"] = ar_tie
    results.append(ar_block)

    # --- AP ---
    ap_block = _side_result(
        "AP", ap_latest, ap_prior, latest_month, prior_month,
        dpo_latest, dpo_prior, "dpo_days", top_n,
        src_idx, model, lines["dpo_basis"], tie_tol)
    ap_block["latest"]["dpo_basis"] = (
        f"DPO denominator = configured model line '{lines['dpo_basis']}' "
        f"({lines['dpo_basis_key']}) = "
        f"{round(dpo_latest, 2) if dpo_latest is not None else None}.")
    ap_tie = tie_out(ap_latest["total"], bs_ap,
                     f"A/P aging total vs BS '{lines['accounts_payable']}'", tie_tol)
    ap_tie["source_ref"], ap_tie["calc"] = _tie_refs_calc(
        src_idx, model, ap_latest, ap_tie, bs_ap_prov,
        lines["accounts_payable"], latest_month, tie_tol)
    ap_block["tie_out"] = ap_tie
    results.append(ap_block)

    # --- exceptions / flags ---
    for tie in (ar_tie, ap_tie):
        if not tie["ties"]:
            exceptions.append({
                "type": "tie_out_mismatch",
                "what": tie["what"],
                "aging_total": tie["aging_total"],
                "balance_sheet": tie["balance_sheet"],
                "difference": tie["difference"],
            })

    # Every bucket share and concentration share divides by the export's grand
    # total, so a gap between that total and the rows counted makes all of them
    # wrong by the gap. Raise it as its own exception rather than letting the
    # shares stand unqualified.
    for side, parsed, month in (("A/R", ar_latest, latest_month),
                                ("A/P", ap_latest, latest_month),
                                ("A/R", ar_prior, prior_month),
                                ("A/P", ap_prior, prior_month)):
        if parsed is None:
            continue
        recon = rows_vs_total(parsed, tie_tol)
        if recon["reconciles"]:
            continue
        exceptions.append({
            "type": "aging_rows_do_not_sum_to_total",
            "what": f"{month} {side} aging: counterparty rows vs the export's TOTAL row",
            "counterparty_rows_total": recon["counterparty_rows_total"],
            "grand_total": recon["grand_total"],
            "difference": recon["difference"],
            "n_counterparties": recon["n_counterparties"],
            "detail": (f"The {recon['n_counterparties']} counterparty rows read from "
                       f"the {month} {side} aging export sum to "
                       f"{recon['counterparty_rows_total']:,.2f}, but the export's own "
                       f"TOTAL row says {recon['grand_total']:,.2f} — "
                       f"{recon['difference']:,.2f} apart. Every bucket share and "
                       f"concentration share for this side is off by that gap; the file "
                       f"holds rows this parse counted differently than the export did "
                       f"(a group or intercompany parent, or a column the bucket "
                       f"headers did not name). Read the shares as indicative until "
                       f"the file is reconciled."),
        })

    note = ("DSO/DPO are single-month and volatile. DPO uses configured basis "
            f"'{lines['dpo_basis_key']}' (model line '{lines['dpo_basis']}').")
    if unit.scale != 1:
        # Every figure here is on the model's unit, and so is the tie-out
        # tolerance -- so on a model in thousands a tolerance of 1.0 is a $1,000
        # tie. Said out loud, because a tie-out that "passes" at a bar nobody
        # stated is the kind of pass that gets trusted.
        note += (f" Figures are in {unit.plain}, as the model reports them; the "
                 f"aging-vs-balance-sheet tie-out allows {tie_tol:,.4g} "
                 f"(${unit.to_dollars(tie_tol):,.0f}).")
    aging_scale = ar_latest.get("amount_scale") or DEFAULT_AMOUNT_SCALE
    if aging_scale != 1:
        note += (f" The aging exports report in units {aging_scale:,.0f}x smaller "
                 "than the model, so every total, bucket and counterparty figure "
                 f"here is the export's own dollars divided by {aging_scale:,.0f} "
                 "and reads on the model's scale.")
    if prior_files_without_month:
        note += (" Prior-month aging files were supplied without a prior month label, "
                 "so they were set aside and the month-over-month trend is unavailable. "
                 "Pass --prior-month (e.g. \"Apr 2026\") with them to get the trend.")
    elif prior_month is None:
        note += " Prior-month aging was not found, so month-over-month trend is unavailable."

    return ml.conclusion(
        module="ap-ar-aging",
        period=ml.month_end_iso(latest_month),
        results=results,
        exceptions=exceptions,
        source_index=src_idx,
        unit=unit,
        engine=("ap-ar-aging v5 (config-driven, borrower-agnostic) "
                "+ per-figure source refs/calc + rows-vs-TOTAL reconciliation"),
        borrower=borrower_name,
        latest_month=latest_month,
        prior_month=prior_month,
        note=note,
        methodology=("Company-prepared aging exports (unaudited). Balance-sheet AR/AP, "
                     "revenue, and the configured DPO-basis line are read from the "
                     "canonical monthly financials model via monitor_lib."),
    )


# ===========================================================================
# CLI
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="AP/AR aging analysis")
    ap.add_argument("--borrower-config",
                    help="borrower-config.json; supplies model_lines + aging config "
                         "and auto-discovers aging files + model (REQUIRED for model_lines)")
    ap.add_argument("--ar-latest")
    ap.add_argument("--ap-latest")
    ap.add_argument("--ar-prior")
    ap.add_argument("--ap-prior")
    ap.add_argument("--model")
    ap.add_argument("--latest-month", help='e.g. "Apr 2026"')
    ap.add_argument("--prior-month", help='e.g. "Mar 2026"')
    ap.add_argument("--out", required=True)
    ap.add_argument("--borrower")
    ap.add_argument("--run-id",
                    help="the run this output belongs to, minted once by the "
                         "orchestrator for the whole run (or set CREDIT_MONITOR_RUN_ID)")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id   # run_stamp() reads it

    if not args.borrower_config:
        ap.error("--borrower-config is required (it supplies model_lines + the aging config).")

    cfg = ml.load_config(args.borrower_config)
    borrower = args.borrower or cfg.get("borrower_name")
    paths = cfg.get("paths", {})
    # The agent supplies the aging files. Each path string is used two ways: read
    # through ml.resolve_path (the sandbox mount it appears at), and recorded in
    # source_files as its shared folder plus the path inside it, so downstream
    # consumers can join store hyperlinks onto it.
    ar_latest_path = args.ar_latest
    ap_latest_path = args.ap_latest
    ar_prior_path = args.ar_prior
    ap_prior_path = args.ap_prior
    model_path = args.model or paths.get("model_output_path")
    latest_month = args.latest_month
    prior_month = args.prior_month

    # Input surface (parse / model open) is guarded: a known bad input writes the
    # standard skip record instead of a traceback, matching the sibling engines
    # and orchestration.md's skip contract. compute() is deliberately OUTSIDE the
    # guard -- an engine bug must crash loudly, not wear a skip record's costume.
    try:
        acfg = load_aging_config(cfg)   # ConfigError (a ValueError) on a config gap
        unit = ml.reporting_unit(cfg)   # what one model figure stands for

        # An input the agent did not supply is a skip, not a crash: the module is
        # not applicable this run and the memo says so.
        missing = [name for name, val in (("--ar-latest", ar_latest_path),
                                          ("--ap-latest", ap_latest_path),
                                          ("--latest-month", latest_month))
                   if not val]
        if not model_path:
            missing.append("--model (or paths.model_output_path)")
        if missing:
            raise ValueError(
                "ap-ar-aging needs " + ", ".join(missing) + ". Open the borrower's "
                "reporting folder for the month, pass the A/R and A/P aging exports "
                "by path, and name the month.")

        fmt = acfg["file_format"]
        scale = acfg["amount_scale"]
        ar_latest = parse_aging(ml.resolve_path(ar_latest_path), fmt,
                                given_path=ar_latest_path, amount_scale=scale)
        ap_latest = parse_aging(ml.resolve_path(ap_latest_path), fmt,
                                given_path=ap_latest_path, amount_scale=scale)
        ar_prior = (parse_aging(ml.resolve_path(ar_prior_path), fmt,
                                given_path=ar_prior_path, amount_scale=scale)
                    if ar_prior_path else None)
        ap_prior = (parse_aging(ml.resolve_path(ap_prior_path), fmt,
                                given_path=ap_prior_path, amount_scale=scale)
                    if ap_prior_path else None)
        model = ml.open_model(model_path)
    except (ValueError, OSError) as e:
        # A skip with no aging file has no --latest-month to stamp, and a skip
        # record with period null is invisible to everything that reads the
        # archive by period. The output filename the worker chose carries it.
        period = latest_month
        if not period:
            m = re.search(r"-(\d{4}-\d{2})\.(?:skipped\.)?json$",
                          os.path.basename(args.out or ""))
            period = m.group(1) if m else None
        out = ml.skip_record("ap-ar-aging", e, period=period, borrower=borrower)
    else:
        out = compute(ar_latest, ap_latest, ar_prior, ap_prior, model, acfg["lines"],
                      latest_month, prior_month, unit,
                      tie_tol_model_units=acfg["tie_tol"],
                      top_n=acfg["top_n"], borrower_name=borrower)

    out_path = ml.write_output(out, args.out)
    rows_emit.write_rows(out, out_path, deal_name=borrower)
    if out.get("skipped"):
        print(f"Wrote {out_path}: SKIPPED (not computed) -- {out['reason']}")
    else:
        print(f"Wrote {out_path}: {len(out['results'])} results, "
              f"{len(out['exceptions'])} exceptions")


if __name__ == "__main__":
    main()
