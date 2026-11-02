#!/usr/bin/env python3
"""Pre-compute the monitoring memo's Performance box for EVERY time basis, once, at render time.

WHY THIS EXISTS
The Performance box used to carry one fixed set of comparison columns. Which basis is the RIGHT
basis is company- and situation-specific: a seasonal borrower is misread month to month, a
borrower mid-inflection is misread on a full fiscal year, and a borrower whose prior-year
comparable month was unusual is flattered or punished by YoY alone. So the box now offers four
toggled views over the same rows.

  monthly     the month | the month before | same month prior year | the month's plan | T3M |
              the SAME T3M A YEAR EARLIER -- levels; under each comparison column, the month's (or
              the T3M's) change against it
  quarterly   the last N complete quarters, levels, each with its YoY comparison
  annual      the last N fiscal years, levels, plus the current year to date, its plan over the
              plan's own months (Budget YTD), and the year to date annualised
  annualised  LTM | L6M annualised | L3M annualised | YTD annualised, levels

EVERY FIGURE IS COMPUTED HERE, IN PYTHON, FROM THE MODEL. The renderer embeds this payload and the
toggle only swaps which view is displayed -- the browser performs NO arithmetic. Two reasons:
figures in this memo must be reproducible and source-traceable, and a number computed in a
<script> tag is neither gated by the validator nor traceable to a model cell.

PAYLOAD SHAPE -- the trace stores follow report-structure's rule (once per ROW, once per COLUMN,
operands per CELL) so the embedded JSON stays small:
  views[].rows[]     id, label, unit ("usd" | "pct" | "bps"), method  <- the ROW store
  views[].columns[]  id, label, window (plain-English), months[], base_months[] or against[],
                     plan, factor, basis                                <- the COLUMN store
  views[].rows[].cells[]  v (the level) + b or cur (the other side) + cmp/flip/small_base/delta,
                     tone (the change's colour), na (why there is no figure)  <- the CELL store

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
        self.gross_profit = lines.get("gross_profit") or None
        self.ebitda = ml.require_line(lines, "ebitda_bridge", "performance-views")
        groups = cfg.get("opex_groups") or {}
        self.opex_lines = [v for k, v in groups.items() if not k.startswith("_") and v]
        # Opex summed over no group is no figure, not $0: the box shows none and says why.
        self.opex_na = (None if self.opex_lines else
                        "This borrower's settings name no operating expense group, so the box "
                        "shows no operating expense figure.")
        self.months = [str(x) for x in (self.m.months or [])]
        self.have = set(self.months)
        self._reported = {}
        # Every amount this reader returns is in DOLLARS, whatever unit the borrower's
        # model is kept in. The box sits on the same page as the income statement and
        # the covenant grid, which are in dollars. Mixing units would make the views
        # incomparable. A margin is a ratio and is unaffected either way.
        self.unit = ml.reporting_unit(cfg)

    def gross_profit_na(self, months=None):
        """No inferred GP, including when a configured row or window is unavailable."""
        if not self.gross_profit:
            return "No gross profit line is configured, so gross profit and gross margin are unavailable."
        wanted = self.months if months is None else months
        values = [self.m.get_line(self.gross_profit, month=month) for month in wanted]
        missing = (not any(v is not None for v in values) if months is None else
                   any(v is None for v in values))
        if missing:
            return (f"The configured gross profit line ({self.gross_profit}) has no complete figures "
                    "for the required months, so gross profit and gross margin are unavailable.")
        return None

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
                for lab in [self.revenue, self.gross_profit, self.ebitda] + self.opex_lines if lab)
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
        gp = None if self.gross_profit_na(months) else self._line(self.gross_profit, months)
        eb = self._line(self.ebitda, months)
        ox = sum(self._line(l, months) for l in self.opex_lines) if self.opex_lines else None
        out = {"revenue": rev, "gross_profit": gp, "ebitda": eb, "opex": ox}
        out["gross_margin"] = gp / rev if gp is not None and rev else None
        out["ebitda_margin"] = eb / rev if rev else None
        out["opex_pct"] = ox / rev if (rev and ox is not None) else None
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
# The lines each row is computed from, so a line with no figure empties every row it feeds.
NEEDS = {"revenue": ("revenue",), "gross_profit": ("gross_profit",),
         "gross_margin": ("gross_profit", "revenue"), "ebitda": ("ebitda",),
         "ebitda_margin": ("ebitda", "revenue"), "opex": ("opex",),
         "opex_pct": ("opex", "revenue")}

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
    window's own revenue. The level and dollar move are more informative than a percentage
    calculated against a near-zero denominator."""
    if base is None or scale in (None, 0):
        return False
    return abs(base) < abs(scale) * floor_frac


def sign_flip(a, b):
    return a is not None and b is not None and ((a < 0) != (b < 0)) and a != 0 and b != 0


def row_na(reader, rid, months=None):
    """Only declared, available GP and opex inputs may produce their dependent rows."""
    if "gross_profit" in NEEDS[rid]:
        return reader.gross_profit_na(months)
    return reader.opex_na if "opex" in NEEDS[rid] else None


def plan_na(budget, rid):
    """Why the plan has no figure for a row, or None: the first line it needs that the
    plan does not have (memo_payload.latest_month_budget's `gaps`)."""
    gaps = (budget or {}).get("gaps") or {}
    return next((gaps[k] for k in NEEDS[rid] if k in gaps), None)


def add_gap(gaps, text, column=None, row=None):
    """One line under the table per reason, naming the columns and rows it empties."""
    for g in gaps:
        if g["text"] == text:
            break
    else:
        g = {"text": text, "columns": [], "rows": []}
        gaps.append(g)
    if column and column not in g["columns"]:
        g["columns"].append(column)
    if row and row not in g["rows"]:
        g["rows"].append(row)


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def money_short(v):
    """A dollar figure as a sentence states it: $38.7M, $3.5K."""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return f"${v / size:.1f}{unit}"
    return f"${v:,.0f}"


def short_span(months):
    """A run of month labels as a column names it: "Jun 2026", "Apr-Jun 2026"."""
    first, last = months[0].split(), months[-1].split()
    if len(months) == 1:
        return months[0]
    return (f"{first[0]}-{last[0]} {last[1]}" if first[1] == last[1]
            else f"{months[0]}-{months[-1]}")


def monthly_view(reader, cur, budget, basis_tag):
    """The monthly view: six LEVEL columns.

    The month reported, then the windows it is read against -- the month before,
    the same month a year earlier, the month's plan -- then the three months
    reported and the SAME three months a year earlier. A comparison column prints
    its own level and, beneath it, the reported window's change against it, so
    the reader sees what the month is measured against beside how far it moved.
    The four changes are the ones the view always carried; the plan column exists
    only when a plan is on file.
    """
    y, m = cur
    lat, prev = [mlabel(y, m)], [mlabel(*shift(y, m, -1))]
    yoy = [mlabel(y - 1, m)]
    t3, pt3 = window(y, m, 3), window(y - 1, m, 3)

    cols = [
        {"id": "cur", "label": lat[0], "months": lat, "window": f"{lat[0]}, the month reported"},
        {"id": "mom", "label": prev[0], "months": prev, "against": lat,
         "window": f"{prev[0]}, the month before; the change beneath is {lat[0]} against it"},
        {"id": "yoy", "label": yoy[0], "months": yoy, "against": lat,
         "window": f"{yoy[0]}, the same month a year earlier; the change beneath is {lat[0]} "
                   f"against it"},
    ]
    if budget:
        cols.append({"id": "budget", "label": f"Budget {lat[0]}", "months": lat, "against": lat,
                     "plan": True,
                     "window": f"the plan for {lat[0]}, from the budget on file; the change "
                               f"beneath is {lat[0]} against it"})
    cols += [
        {"id": "t3m", "label": f"T3M {short_span(t3)}", "months": t3,
         "window": f"{t3[0]} to {t3[-1]}, the three months reported"},
        {"id": "pt3m", "label": f"T3M {short_span(pt3)}", "months": pt3, "against": t3,
         "window": f"{pt3[0]} to {pt3[-1]}, the SAME three months a year earlier; the change "
                   f"beneath is {t3[0]} to {t3[-1]} against it"},
    ]
    for c in cols:
        c["basis"] = basis_tag(c["months"] if not c.get("plan") else lat,
                               None if c.get("plan") else c.get("against"))
    return level_view("monthly", "Monthly", cols, reader,
                      "The month reported beside the month before, the same month a year "
                      "earlier and its plan; then the three months reported beside the same "
                      "three months a year earlier. Under each comparison column, the change "
                      "of the month (or the three months) against it.",
                      plans={"budget": budget})


def compare(cell, a, b, unit, scale):
    """The change of `a` against `b`, written into `cell` the way every view says it.

    A percentage is suppressed where it would mislead: across zero it is a sign
    change; off a base under 1% of the window's revenue it is the dollar move. A
    margin moves in basis points.
    """
    if a is None or b is None:
        return
    if unit == "usd":
        if sign_flip(a, b):
            cell["flip"] = True      # A percentage across zero obscures the change.
        elif near_zero_base(b, scale):
            cell["small_base"] = True
            cell["delta"] = a - b
        else:
            cell["cmp"] = pct_change(a, b)
    else:
        cell["cmp"] = bps_change(a, b)


def set_tone(cell, rid, a, b, plan):
    """The change line's colour: "up" better, "down" worse, "neu" neither.

    Opex against an earlier period is neutral, because it grows with revenue.
    Against the plan, spend above plan is worse and below is better, judged on the
    SIZE of the spend: some workbooks store costs as negative numbers, and the
    sign of the change then runs the other way.
    """
    if cell.get("cmp") is None:
        return
    if rid in ("opex", "opex_pct"):
        if not plan or abs(a) == abs(b):
            cell["tone"] = "neu"
        else:
            cell["tone"] = "down" if abs(a) > abs(b) else "up"
    else:
        cell["tone"] = "up" if cell["cmp"] >= 0 else "down"


def level_view(vid, vlabel, cols, reader, note, plans=None):
    """Levels, one column per window. Three kinds of column:

      a window on its own              months        v
      a window against a year earlier  base_months   v, b, cmp = v against b
      a comparison column              against       v, cur, cmp = cur against v

    A comparison column is a window the reported figure is read against, so its
    change runs the other way: the reported window (`against`) against this
    column's own level. A `plan` column takes its level from `plans[column id]`
    rather than the model, and a line that plan does not have is empty with its
    reason (`na`).
    """
    plans = plans or {}
    rows, gaps = [], []
    for rid, label, _u in LEVEL_ROWS:
        unit = "pct" if rid in MARGINS else "usd"
        cells = []
        whole = row_na(reader, rid)
        if whole:
            add_gap(gaps, whole, row=rid)
            rows.append({"id": rid, "label": label, "unit": unit, "na": whole,
                         "method": method_for(rid, reader.unit),
                         "cells": [{"v": None, "na": whole} for _c in cols]})
            continue
        for c in cols:
            fac = c.get("factor", 1)
            plan = plans.get(c["id"]) if c.get("plan") else None
            actual_months = list(dict.fromkeys(
                month for key in ("months", "against", "base_months")
                if reader.complete(c.get(key) or [])
                for month in c[key]))
            na = row_na(reader, rid, actual_months)
            if not na and c.get("plan"):
                na = plan_na(plan, rid)
            if na:
                cells.append({"v": None, "na": na})
                add_gap(gaps, na, column=c["id"], row=rid)
                continue
            M = plan if c.get("plan") else reader.metrics(c["months"])
            v = None if M is None else M.get(rid)
            if v is not None and unit == "usd":
                v *= fac
            cell = {"v": v}
            if c.get("against"):
                A = reader.metrics(c["against"])
                a = None if A is None else A.get(rid)
                if a is not None and unit == "usd":
                    a *= fac
                if a is not None:
                    cell["cur"] = a
                    compare(cell, a, v, unit, (M or {}).get("revenue"))
                    set_tone(cell, rid, a, v, c.get("plan"))
            elif c.get("base_months"):
                Bs = reader.metrics(c["base_months"])
                b = None if Bs is None else Bs.get(rid)
                if b is not None:
                    if unit == "usd":
                        b *= fac
                    cell["b"] = b
                    compare(cell, v, b, unit, (Bs or {}).get("revenue"))
                    set_tone(cell, rid, v, b, False)
            cells.append(cell)
        rows.append({"id": rid, "label": label, "unit": unit,
                     "method": method_for(rid, reader.unit), "cells": cells})
    return {"id": vid, "label": vlabel, "kind": "level", "note": note, "columns": cols,
            "rows": rows, "gaps": gaps}


def budget_ytd_window(plan, ytd):
    """Keep an aggregated plan's full window; never clip its months without its total."""
    months = [ml.month_label(mo) or str(mo) for mo in (plan or {}).get("months") or []]
    if months and not set(months).issubset(ytd or []):
        span = f"{ytd[0]} to {ytd[-1]}" if ytd else "the calendar year to date"
        return months, (f"The aggregated plan covers {months[0]} to {months[-1]}, which is not "
                        f"wholly within the calendar year-to-date window {span}. Its total cannot "
                        "be split across those windows, so the Annual view has no Budget YTD column.")
    return months, None


def budget_ytd_column(reader, y, ytd, plan, basis_tag):
    """The Annual view's Budget YTD column, only when its full window fits the YTD.

    It compares the plan over the plan's own months (`plan["months"]`, the months
    budget-vs-actual summed on both sides) against the actual over the same months.
    A plan covering fewer months than the year to date keeps the column's name, is
    flagged like a basis change, and its note names the months and the actual.
    """
    months, mismatch = budget_ytd_window(plan, ytd)
    if not months or mismatch:
        return None
    col = {"id": "budget_ytd", "label": f"Budget {y} YTD", "months": months, "against": months,
           "plan": True,
           "window": f"the plan for {months[0]} to {months[-1]} ({len(months)} months), from the "
                     f"budget on file; the change beneath is the actual over the same months "
                     f"against it"}
    if months == ytd:
        col["basis"] = basis_tag(months)
        return col
    first = MONTH_NAMES[parse_month(months[0])[1] - 1]
    last = MONTH_NAMES[parse_month(months[-1])[1] - 1]
    why = (f"the plan starts in {first}" if months[0] != ytd[0]
           else f"the plan covers {first} to {last}")
    actual = (reader.metrics(months) or {}).get("revenue")
    col["basis"] = "not like-for-like: the plan covers fewer months than the year to date"
    col["note"] = (f"{why}, so this compares {first} to {last} only"
                   + (f", {money_short(actual)} actual against a {money_short(plan['revenue'])} plan"
                      if actual is not None and plan.get("revenue") is not None else ""))
    return col


def build(cfg_path, out_path, budget=None, max_quarters=8, max_years=3, period=None, model_path=None,
          budget_ytd=None):
    cfg = ml.load_config(cfg_path)
    model_path = model_path or cfg["paths"]["model_output_path"]
    reader = Reader(model_path, cfg)
    if not reader.months:
        raise ValueError("model carries no months")
    # The plan arrives on the model's own unit -- the pinned budget keeps the
    # borrower's convention -- so it converts here, beside the actual it is
    # compared against. Both sides of the vs-Budget column then state dollars.
    # Margins were derived before this point and are ratios conversion cannot move.
    if budget:
        budget = dict(budget)
        for k in ("revenue", "gross_profit", "ebitda", "opex"):
            if budget.get(k) is not None:
                budget[k] = reader.unit.to_dollars(budget[k])
    if budget_ytd:
        budget_ytd = dict(budget_ytd)
        for k in ("revenue", "gross_profit", "ebitda", "opex"):
            if budget_ytd.get(k) is not None:
                budget_ytd[k] = reader.unit.to_dollars(budget_ytd[k])
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
        plan_col = budget_ytd_column(reader, y, ytd, budget_ytd, basis_tag)
        if plan_col:
            acols.append(plan_col)
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
        monthly_view(reader, cur, budget, basis_tag),
        level_view("quarterly", "Quarterly", qcols, reader,
                   "Complete quarters only, oldest first. The comparison under each level is the same quarter a year earlier."),
        level_view("annual", "Annual", acols, reader,
                   "Complete fiscal years, then the current year to date"
                   + (", its plan over the plan's own months," if any(c["id"] == "budget_ytd" for c in acols)
                      else "")
                   + " and that year to date scaled to twelve months.",
                   plans={"budget_ytd": budget_ytd}),
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
    ml.prerequisites.require_workbook()
    budget = None
    if a.budget_json:
        budget = json.loads(open(a.budget_json, encoding="utf-8").read()) if os.path.exists(a.budget_json) else json.loads(a.budget_json)
        rev = budget.get("revenue")
        if rev:
            for line, margin in (("gross_profit", "gross_margin"), ("ebitda", "ebitda_margin"),
                                 ("opex", "opex_pct")):
                if budget.get(line) is not None:
                    budget.setdefault(margin, budget[line] / rev)
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
