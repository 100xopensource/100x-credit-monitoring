#!/usr/bin/env python3
"""Pre-compute the monitoring memo's Performance box for EVERY time basis, once, at render time.

WHY THIS EXISTS
The Performance box used to carry one fixed set of comparison columns. Which basis is the RIGHT
basis is company- and situation-specific: a seasonal borrower is misread month to month, a
borrower mid-inflection is misread on a full fiscal year, and a borrower whose prior-year
comparable month was unusual is flattered or punished by YoY alone. So the box now offers four
toggled views over the same rows.

  monthly     MoM | YoY (same month prior year) | T3M vs the SAME T3M WINDOW A YEAR EARLIER | vs Budget
  quarterly   the last N complete quarters, levels, each with its YoY comparison
  annual      the last N fiscal years, levels, plus the current year to date and that annualised
  annualised  LTM | L6M annualised | L3M annualised | YTD annualised, levels

EVERY FIGURE IS COMPUTED HERE, IN PYTHON, FROM THE MODEL. The renderer embeds this payload and the
toggle only swaps which view is displayed -- the browser performs NO arithmetic. Two reasons:
figures in this memo must be reproducible and source-traceable, and a number computed in a
<script> tag is neither gated by the validator nor traceable to a model cell.

PAYLOAD SHAPE -- the trace stores follow report-structure's rule (once per ROW, once per COLUMN,
operands per CELL) so the embedded JSON stays small:
  views[].rows[]     id, label, unit ("usd" | "pct" | "bps"), method  <- the ROW store
  views[].columns[]  id, label, window (plain-English), months[], base_months[], basis  <- the COLUMN store
  views[].rows[].cells[]  v (the value) + a/b (the operands) only     <- the CELL store

T3M vs pT3M -- THE FIX THIS MODULE CARRIES
pT3M is the same three calendar months a year earlier, NOT the immediately preceding three months.
Sequential trailing windows are not like-for-like for a seasonal borrower: Mar-May against Dec-Feb
reads a seasonal swing as performance. Same window, prior year, is the like-for-like comparison
report-structure's Comparison discipline already asks for everywhere else.
"""
import argparse, json, os, sys, datetime

MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def _plugin_lib():
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (os.path.join(here, "..", "..", "..", "lib"), os.path.join(here, "..", "..", "lib")):
        if os.path.isdir(up):
            sys.path.insert(0, os.path.abspath(up)); return
_plugin_lib()
import monitor_lib as ml  # noqa: E402


# ---------------------------------------------------------------- month helpers
def mlabel(y, m):
    return f"{MONTH_ABBR[m-1]} {y}"

def parse_month(label):
    a, y = label.split(); return int(y), MONTH_ABBR.index(a) + 1

def shift(y, m, k):
    t = (y * 12 + (m - 1)) + k; return t // 12, t % 12 + 1

def window(y, m, n):
    """The n months ENDING at (y, m), chronological."""
    return [mlabel(*shift(y, m, -k)) for k in range(n - 1, -1, -1)]

def quarter_of(m):
    return (m - 1) // 3 + 1

def quarter_months(y, q):
    st = (q - 1) * 3 + 1; return [mlabel(y, st + i) for i in range(3)]


# ---------------------------------------------------------------- model access
class Reader:
    """Reads the monitoring model's income statement by the borrower's OWN row labels."""

    def __init__(self, model_path, cfg):
        self.m = ml.open_model(model_path)
        lines = cfg.get("model_lines") or {}
        self.revenue = ml.require_line(lines, "revenue", "performance-views")
        self.gross_profit = ml.require_line(lines, "gross_profit", "performance-views")
        self.ebitda = ml.require_line(lines, "ebitda_bridge", "performance-views")
        groups = cfg.get("opex_groups") or {}
        self.opex_lines = [v for k, v in groups.items() if not k.startswith("_") and v]
        self.months = [str(x) for x in (self.m.months or [])]
        self.have = set(self.months)
        self._reported = {}
        # Every amount this reader returns is in DOLLARS, whatever unit the borrower's
        # model is kept in. The box sits on the same page as the income statement and
        # the covenant grid, which are in dollars: a model kept in thousands otherwise
        # showed quarterly revenue of 17,668 beside a monthly 6,018,099 and nothing on
        # the page said why. A margin is a ratio and is unaffected either way.
        self.unit = ml.reporting_unit(cfg)

    def _line(self, label, months):
        tot = 0.0
        for mm in months:
            v = self.m.get_line(label, month=mm)
            tot += 0.0 if v is None else float(v)
        return self.unit.to_dollars(tot)

    def reported(self, month):
        """Whether the model actually STATES this month, rather than merely carrying
        a header for it. A workbook is commonly built a column ahead of the reporting
        pack, and a header-only month is on the month axis with every cell blank. Read
        as reported it sums to zero, which is not "no data" — it is a month of no
        revenue, and it drags every trailing window and the MoM comparison with it."""
        if month not in self.have:
            return False
        if month not in self._reported:
            self._reported[month] = any(
                self.m.get_line(lab, month=month) is not None
                for lab in [self.revenue, self.gross_profit, self.ebitda] + self.opex_lines)
        return self._reported[month]

    def latest_reported(self):
        """The newest month the model states — what the memo's period is read off."""
        for mm in reversed(self.months):
            if self.reported(mm):
                return mm
        return None

    def complete(self, months):
        return bool(months) and all(self.reported(mm) for mm in months)

    def metrics(self, months):
        if not self.complete(months):
            return None
        rev = self._line(self.revenue, months)
        gp = self._line(self.gross_profit, months)
        eb = self._line(self.ebitda, months)
        ox = sum(self._line(l, months) for l in self.opex_lines)
        out = {"revenue": rev, "gross_profit": gp, "ebitda": eb, "opex": ox}
        out["gross_margin"] = gp / rev if rev else None
        out["ebitda_margin"] = eb / rev if rev else None
        out["opex_pct"] = ox / rev if rev else None
        return out


# ---------------------------------------------------------------- row contract
LEVEL_ROWS = [
    ("revenue",       "Net revenue",                              "usd"),
    ("gross_profit",  "Gross profit",                             "usd"),
    ("gross_margin",  "Gross margin",                             "pct"),
    ("ebitda",        "EBITDA",                                   "usd"),
    ("ebitda_margin", "EBITDA margin",                            "pct"),
    ("opex",          "Operating expense",                        "usd"),
    ("opex_pct",      "Operating expense % of revenue",           "pct"),
]
MARGINS = {"gross_margin", "ebitda_margin", "opex_pct"}

METHOD = {
    "revenue":       "Revenue as reported on the monthly income statement, summed over the column's months.",
    "gross_profit":  "Gross profit as reported, summed over the column's months.",
    "gross_margin":  "Gross profit divided by revenue for the column's months.",
    "ebitda":        "The income statement's own EBITDA line -- the same line the covenant is measured on -- summed over the column's months.",
    "ebitda_margin": "EBITDA divided by revenue for the column's months.",
    "opex":          "Operating expense: the borrower's operating expense groups summed over the column's months.",
    "opex_pct":      "Operating expense divided by revenue for the column's months.",
}


def method_for(rid, unit):
    """The row's method sentence, naming the dollars conversion when there is one.

    The trace box's model-cell hop shows the workbook's OWN figure, which for a
    borrower kept in thousands is a thousandth of the number printed above it. The
    method line is where the reader learns why the two differ.
    """
    m = METHOD[rid]
    if rid not in MARGINS and unit.scale != 1:
        m += f" The model states its figures in {unit.plain}; they are shown here in dollars."
    return m


def pct_change(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b)

def bps_change(a, b):
    if a is None or b is None:
        return None
    return (a - b) * 10000.0

def near_zero_base(base, scale, floor_frac=0.01):
    """True when a percentage change off `base` would be noise: the base is under `floor_frac` of the
    window's own revenue. Q4-2024 EBITDA of $66K on $31,489K of revenue turns a $1.2M improvement into
    "+1,755%", which tells a reader nothing. The level and the dollar move carry the meaning instead."""
    if base is None or scale in (None, 0):
        return False
    return abs(base) < abs(scale) * floor_frac


def sign_flip(a, b):
    return a is not None and b is not None and ((a < 0) != (b < 0)) and a != 0 and b != 0


def change_view(reader, cur, budget, basis_tag):
    """The monthly view: four like-for-like comparisons, all expressed as change."""
    y, m = cur
    lat, prev = [mlabel(y, m)], [mlabel(*shift(y, m, -1))]
    yoy = [mlabel(y - 1, m)]
    t3, pt3 = window(y, m, 3), window(y - 1, m, 3)

    cols = [
        {"id": "mom", "label": "MoM", "months": lat, "base_months": prev,
         "window": f"{lat[0]} against {prev[0]}"},
        {"id": "yoy", "label": "YoY", "months": lat, "base_months": yoy,
         "window": f"{lat[0]} against the same month a year earlier, {yoy[0]}"},
        {"id": "t3m", "label": "T3M vs prior-year T3M", "months": t3, "base_months": pt3,
         "window": f"{t3[0]} to {t3[-1]} against the SAME three months a year earlier, {pt3[0]} to {pt3[-1]}"},
    ]
    if budget:
        cols.append({"id": "budget", "label": "vs Budget", "months": lat, "base_months": None,
                     "window": f"{lat[0]} against the plan for the same month"})

    for c in cols:
        c["basis"] = basis_tag(c["months"], c["base_months"])

    rows = []
    for rid, label, _unit in LEVEL_ROWS:
        unit = "bps" if rid in MARGINS else "pct"
        cells = []
        for c in cols:
            A = reader.metrics(c["months"])
            B = budget if c["id"] == "budget" else reader.metrics(c["base_months"])
            a = None if A is None else A.get(rid)
            b = None if B is None else B.get(rid)
            cell = {"v": (bps_change(a, b) if unit == "bps" else pct_change(a, b)), "a": a, "b": b}
            if unit == "pct":
                if sign_flip(a, b):
                    cell["flip"] = True      # a percentage across zero is not meaningful
                elif near_zero_base(b, (B or {}).get("revenue")):
                    cell["small_base"] = True
                    cell["delta"] = None if (a is None or b is None) else a - b
                    cell["v"] = None
            cells.append(cell)
        rows.append({"id": rid, "label": label, "unit": unit,
                     "method": method_for(rid, reader.unit), "cells": cells})
    return {"id": "monthly", "label": "Monthly", "kind": "change",
            "note": "Like-for-like change. Levels are in the income statement review.",
            "columns": cols, "rows": rows}


def level_view(vid, vlabel, cols, reader, note):
    """Quarterly / annual / annualised: levels, one column per window."""
    rows = []
    for rid, label, _u in LEVEL_ROWS:
        unit = "pct" if rid in MARGINS else "usd"
        cells = []
        for c in cols:
            M = reader.metrics(c["months"])
            v = None if M is None else M.get(rid)
            if v is not None and unit == "usd":
                v *= c.get("factor", 1)
            cell = {"v": v}
            if c.get("base_months"):
                Bs = reader.metrics(c["base_months"])
                b = None if Bs is None else Bs.get(rid)
                if b is not None:
                    if unit == "usd":
                        b *= c.get("factor", 1)
                        if sign_flip(v, b):
                            cell["flip"] = True   # e.g. EBITDA +$5K to -$2,577K: the ratio says nothing
                        elif near_zero_base(b, (Bs or {}).get("revenue")):
                            cell["small_base"] = True
                            cell["delta"] = None if v is None else v - b
                        else:
                            cell["cmp"] = pct_change(v, b)
                    else:
                        cell["cmp"] = bps_change(v, b)
                    cell["b"] = b
            cells.append(cell)
        rows.append({"id": rid, "label": label, "unit": unit,
                     "method": method_for(rid, reader.unit), "cells": cells})
    return {"id": vid, "label": vlabel, "kind": "level", "note": note, "columns": cols, "rows": rows}


def build(cfg_path, out_path, budget=None, max_quarters=8, max_years=3, period=None):
    cfg = ml.load_config(cfg_path)
    model_path = cfg["paths"]["model_output_path"]
    reader = Reader(model_path, cfg)
    if not reader.months:
        raise ValueError("model carries no months")
    # The plan arrives on the model's own unit -- the pinned budget keeps the
    # borrower's convention -- so it converts here, beside the actual it is
    # compared against. Both sides of the vs-Budget column then state dollars.
    # Margins were derived before this point and are ratios conversion cannot move.
    if budget:
        for k in ("revenue", "gross_profit", "ebitda", "opex"):
            if budget.get(k) is not None:
                budget[k] = reader.unit.to_dollars(budget[k])
    # The run names the period it is reporting; the model's own axis is the fallback,
    # and then the newest month the model STATES, never the newest column it carries.
    if period:
        cur = (int(str(period)[:4]), int(str(period)[5:7]))
    else:
        latest = reader.latest_reported()
        if latest is None:
            raise ValueError("model carries no reported month")
        cur = parse_month(latest)
    y, m = cur
    an = (cfg.get("analysis") or {})
    fy_start = int(an.get("fiscal_year_start_month", 1) or 1)
    if fy_start != 1:
        raise ValueError("performance-views currently assumes a calendar fiscal year")
    # basis_breaks uses the model-wide pinned schema: [{"effective": "Apr 2026", "reason": "..."}].
    break_ord = None
    for b in (an.get("basis_breaks") or []):
        eff = b.get("effective") if isinstance(b, dict) else b
        if not eff:
            continue
        try:
            by, bm = parse_month(str(eff))
        except Exception:
            continue
        break_ord = max(break_ord or 0, by * 12 + (bm - 1))

    def _side(month):
        """Which side of the reporting-basis change a month is reported on."""
        t = parse_month(month)
        return "pre" if t[0] * 12 + (t[1] - 1) < break_ord else "post"

    def basis_tag(months, base_months=None):
        """The tag a column earns from the reporting-basis change, or None.

        Two things earn it, and reaching back before the break is NOT one of them: a
        window wholly on the old basis is a faithful reading of the old basis, and
        starring every historical column buries the one that matters. What is not
        like-for-like is a window that SPANS the break — part of it on each basis — or
        a comparison whose two windows sit on OPPOSITE sides of it."""
        if break_ord is None or not months:
            return None
        sides = {_side(x) for x in months}
        if len(sides) > 1:
            return "not like-for-like: this window spans the reporting-basis change"
        if base_months:
            base_sides = {_side(x) for x in base_months}
            if len(base_sides) > 1 or base_sides != sides:
                return ("not like-for-like: this window and what it is compared "
                        "against are reported on either side of the basis change")
        return None

    # ---- quarterly: the last complete quarters, newest LAST, each against the prior year
    qcols, qy, qq = [], y, quarter_of(m)
    # the quarter holding the current month is complete only if the month closes it
    if m % 3 != 0:
        qy, qq = (y, qq - 1) if qq > 1 else (y - 1, 4)
    seq = []
    while len(seq) < max_quarters:
        if not reader.complete(quarter_months(qy, qq)):
            break
        seq.append((qy, qq))
        qy, qq = (qy, qq - 1) if qq > 1 else (qy - 1, 4)
    for (yy, q) in reversed(seq):
        base = quarter_months(yy - 1, q)
        qcols.append({"id": f"q{q}-{yy}", "label": f"Q{q} {yy}", "months": quarter_months(yy, q),
                      "base_months": base if reader.complete(base) else None,
                      "window": f"Q{q} {yy} ({MONTH_ABBR[(q-1)*3]} to {MONTH_ABBR[(q-1)*3+2]} {yy})"
                                + (f", compared with Q{q} {yy-1}" if reader.complete(base) else ""),
                      "basis": basis_tag(quarter_months(yy, q),
                                         base if reader.complete(base) else None)})

    # ---- annual: complete fiscal years, then the current year to date and that annualised
    acols = []
    yrs = []
    # The current year counts as a complete year on the December run, so a year-end memo
    # names FY<y> instead of showing it only as "YTD" and never labelling the year.
    yy = y
    while len(yrs) < max_years:
        ms = [mlabel(yy, k) for k in range(1, 13)]
        if not reader.complete(ms):
            if yy == y:
                yy -= 1        # the current year is not closed yet; keep looking back
                continue
            break
        yrs.append(yy); yy -= 1
    for fy in reversed(yrs):
        base = [mlabel(fy - 1, k) for k in range(1, 13)]
        acols.append({"id": f"fy{fy}", "label": f"FY{fy}", "months": [mlabel(fy, k) for k in range(1, 13)],
                      "base_months": base if reader.complete(base) else None,
                      "window": f"FY{fy} (January to December {fy})"
                                + (f", compared with FY{fy-1}" if reader.complete(base) else ""),
                      "basis": basis_tag([mlabel(fy, k) for k in range(1, 13)],
                                         base if reader.complete(base) else None)})
    ytd = [mlabel(y, k) for k in range(1, m + 1)]
    ytd_py = [mlabel(y - 1, k) for k in range(1, m + 1)]
    # In December the year to date IS the fiscal year already columned above, and
    # annualising twelve months of it scales by one — two more columns saying the
    # same thing a third time.
    if y not in yrs:
        acols.append({"id": "ytd", "label": f"{y} YTD", "months": ytd,
                      "base_months": ytd_py if reader.complete(ytd_py) else None,
                      "window": f"{MONTH_ABBR[0]} to {MONTH_ABBR[m-1]} {y} ({m} months)"
                                + (", compared with the same months a year earlier" if reader.complete(ytd_py) else ""),
                      "basis": basis_tag(ytd, ytd_py if reader.complete(ytd_py) else None)})
        acols.append({"id": "ytd_ann", "label": f"{y} YTD annualised", "months": ytd, "base_months": None,
                      "factor": 12.0 / m, "basis": basis_tag(ytd),
                      "window": f"{MONTH_ABBR[0]} to {MONTH_ABBR[m-1]} {y} scaled to twelve months (x 12/{m})"})

    # ---- annualised / trailing
    tcols = []
    for n, lab, fac in ((12, "LTM", 1.0), (6, "L6M annualised", 2.0), (3, "L3M annualised", 4.0)):
        ms = window(y, m, n)
        if not reader.complete(ms):
            continue
        tcols.append({"id": f"l{n}m", "label": lab, "months": ms, "base_months": None,
                      "factor": fac, "basis": basis_tag(ms),
                      "window": (f"the twelve months to {ms[-1]}" if n == 12 else
                                 f"the {n} months to {ms[-1]} scaled to twelve months (x {int(fac)})")})
    if m != 12:
        tcols.append({"id": "ytd_ann", "label": f"{y} YTD annualised", "months": ytd, "base_months": None,
                      "factor": 12.0 / m, "basis": basis_tag(ytd),
                      "window": f"{MONTH_ABBR[0]} to {MONTH_ABBR[m-1]} {y} scaled to twelve months (x 12/{m})"})

    views = [
        change_view(reader, cur, budget, basis_tag),
        level_view("quarterly", "Quarterly", qcols, reader,
                   "Complete quarters only, oldest first. The comparison under each level is the same quarter a year earlier."),
        level_view("annual", "Annual", acols, reader,
                   "Complete fiscal years, then the current year to date and that figure scaled to twelve months."),
        level_view("annualised", "Annualised", tcols, reader,
                   "Trailing windows scaled to a twelve-month rate, so a run-rate can be read against a full year."),
    ]
    payload = {
        "borrower": cfg.get("borrower_name"), "period": f"{y:04d}-{m:02d}",
        # The box states dollars whatever the model is kept in (see Reader), so this
        # says so rather than repeating the workbook's own caption.
        "units": f"{ml.reporting_unit(cfg).currency}, in whole units",
        "generated": datetime.date.today().isoformat(),
        "default_view": "monthly", "views": views,
        "basis_note": ("a window either spanning the reporting-basis change or compared against one "
                       "on the other side of it. Read those comparisons with that in mind."
                       if break_ord is not None else None),
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, out_path)
    return payload


def main():
    ap = argparse.ArgumentParser(description="Pre-compute the Performance box's toggled views")
    ap.add_argument("--borrower-config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-json", help='optional JSON of the latest month\'s plan: {"revenue":..,'
                                          '"gross_profit":..,"ebitda":..,"opex":..}, stated in the '
                                          "MODEL'S OWN UNITS as the pinned budget states them -- the "
                                          "engine converts it alongside the model.")
    ap.add_argument("--period", help='the month being reported, "YYYY-MM". Defaults to the '
                                     'newest month the model states.')
    ap.add_argument("--max-quarters", type=int, default=8)
    ap.add_argument("--max-years", type=int, default=3)
    a = ap.parse_args()
    budget = None
    if a.budget_json:
        budget = json.loads(open(a.budget_json, encoding="utf-8").read()) if os.path.exists(a.budget_json) else json.loads(a.budget_json)
        rev = budget.get("revenue")
        if rev:
            budget.setdefault("gross_margin", budget["gross_profit"] / rev)
            budget.setdefault("ebitda_margin", budget["ebitda"] / rev)
            budget.setdefault("opex_pct", budget["opex"] / rev)
    try:
        p = build(a.borrower_config, a.out, budget=budget,
                  max_quarters=a.max_quarters, max_years=a.max_years, period=a.period)
    except ml.ConfigError as e:
        # A config gap already carries a message written for the person who fixes
        # it; a traceback would bury it.
        sys.exit(f"performance-views: {e}")
    print(f"Wrote {a.out}: " + ", ".join(f"{v['label']} {len(v['columns'])} col" for v in p["views"]))


if __name__ == "__main__":
    main()
