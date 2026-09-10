"""period_windows.py — deterministic reporting windows for the monthly monitor.

Two windows (never conflate them, see report-structure.md):
  * display window  = trailing 5 months ending at the current period (what the tables SHOW).
  * analysis window = calendar year-to-date, Jan of the current year -> current (what you READ).

Plus the comparison pairs and historical windows the Performance box toggles between
(see report-structure.md -> "Performance box"):

  * t3m_pair    trailing 3 months vs the SAME THREE CALENDAR MONTHS A YEAR EARLIER. A sequential
                prior window (Mar-May against Dec-Feb) is NOT like-for-like for a seasonal
                borrower -- it reads a seasonal swing as performance. Same window, prior year, is
                the comparison the Comparison discipline asks for everywhere else.
  * quarters    the last N COMPLETE quarters, oldest first, each paired with the same quarter a
                year earlier.
  * fiscal_years the last N complete fiscal years, each paired with the prior year, plus the
                current year to date and its annualisation factor.
  * trailing    LTM / L6M / L3M / YTD windows with the factor that scales each to a twelve-month
                rate, so a run-rate reads against a full year.

Plus the QoQ pair: latest COMPLETE quarter vs the SAME quarter of the
PRIOR YEAR (same-basis, seasonality-matched — Q1 2026 vs Q1 2025, never vs Q4 2025: sequential
quarters are not like-for-like for seasonal borrowers). A quarter only becomes "complete" on the
Jan/Apr/Jul/Oct run after its financials arrive. Between those runs the QoQ column carries the
last complete quarter (refresh_now=False). If the prior-year quarter predates the model's history,
the renderer shows "—" with a note (the pair is still returned).

Usage:
    from period_windows import windows
    w = windows("2026-05")   # -> dict; see keys below
"""

def _q_months(year, q):
    start = (q - 1) * 3 + 1
    return [f"{year}-{start + i:02d}" for i in range(3)]

def _q_label(year, q):
    return f"Q{q} {year}"

def _m(year, month):
    return f"{year}-{month:02d}"

def _shift(year, month, k):
    t = year * 12 + (month - 1) + k
    return t // 12, t % 12 + 1

def _win(year, month, n):
    """The n months ENDING at (year, month), chronological."""
    return [_m(*_shift(year, month, -k)) for k in range(n - 1, -1, -1)]

def _complete_quarter_before(year, month):
    """The most recent quarter that has fully closed as of (year, month)."""
    q = (month - 1) // 3 + 1
    if month % 3 != 0:                 # the current quarter is still open
        return (year, q - 1) if q > 1 else (year - 1, 4)
    return year, q

def windows(current_period, max_quarters=8, max_years=3):
    """current_period: 'YYYY-MM'. Returns display_months, ytd_months, qoq{}, t3m_pair{},
    quarters[], fiscal_years[], trailing[]. Callers gate each window on the model's own month
    coverage -- this module does the calendar arithmetic and nothing else."""
    y, m = int(current_period[:4]), int(current_period[5:7])

    # display = trailing 5 months inclusive of current
    disp, yy, mm = [], y, m
    for _ in range(5):
        disp.append(f"{yy}-{mm:02d}")
        mm -= 1
        if mm == 0:
            mm, yy = 12, yy - 1
    disp.reverse()

    # analysis = calendar YTD (Jan -> current)
    ytd = [f"{y}-{mo:02d}" for mo in range(1, m + 1)]

    # QoQ: which complete quarter is "in" as of this month
    refresh_now = m in (1, 4, 7, 10)
    if m in (1, 2, 3):      qy, q = y - 1, 4      # Jan-Mar -> last refresh Jan -> Q4 prior yr
    elif m in (4, 5, 6):    qy, q = y, 1          # Apr-Jun -> Q1 this yr
    elif m in (7, 8, 9):    qy, q = y, 2          # Jul-Sep -> Q2 this yr
    else:                   qy, q = y, 3          # Oct-Dec -> Q3 this yr
    # prior = SAME quarter, PRIOR YEAR (same-basis / seasonality-matched)
    pqy, pq = qy - 1, q

    # T3M vs the SAME window a year earlier (never the sequential prior three months)
    t3m = _win(y, m, 3)
    pt3m = _win(y - 1, m, 3)

    # the last complete quarters, oldest first, each against the same quarter a year earlier
    quarters = []
    _cy, _cq = _complete_quarter_before(y, m)
    seq = []
    for _ in range(max_quarters):
        seq.append((_cy, _cq))
        _cy, _cq = (_cy, _cq - 1) if _cq > 1 else (_cy - 1, 4)
    for (yy, qq) in reversed(seq):
        quarters.append({
            "label": _q_label(yy, qq),
            "months": _q_months(yy, qq),
            "prior_year": {"label": _q_label(yy - 1, qq), "months": _q_months(yy - 1, qq)},
        })

    # Complete fiscal years (calendar), then the current year to date and its annualisation
    # factor. On the DECEMBER run the current year is itself complete, so it is named FY<y>
    # and no year-to-date entry follows: a YTD covering all twelve months, annualised by a
    # factor of one, is the fiscal year already listed.
    last_complete_fy = y if m == 12 else y - 1
    fiscal_years = []
    for fy in range(last_complete_fy - max_years + 1, last_complete_fy + 1):
        fiscal_years.append({
            "label": f"FY{fy}",
            "months": [_m(fy, k) for k in range(1, 13)],
            "prior_year": {"label": f"FY{fy-1}", "months": [_m(fy - 1, k) for k in range(1, 13)]},
        })
    if m != 12:
        fiscal_years.append({
            "label": f"{y} YTD", "months": list(ytd), "annualise": 12.0 / m,
            "prior_year": {"label": f"{y-1} YTD", "months": [_m(y - 1, k) for k in range(1, m + 1)]},
        })

    # trailing windows, each with the factor that scales it to a twelve-month rate
    trailing = [
        {"id": "ltm", "label": "LTM", "months": _win(y, m, 12), "annualise": 1.0},
        {"id": "l6m", "label": "L6M annualised", "months": _win(y, m, 6), "annualise": 2.0},
        {"id": "l3m", "label": "L3M annualised", "months": _win(y, m, 3), "annualise": 4.0},
    ]
    if m != 12:      # in December the year to date annualises by one — that is the LTM above
        trailing.append({"id": "ytd", "label": f"{y} YTD annualised",
                         "months": list(ytd), "annualise": 12.0 / m})

    return {
        "current": current_period,
        "display_months": disp,
        "ytd_months": ytd,
        "t3m_pair": {
            "latest": {"label": f"T3M to {t3m[-1]}", "months": t3m},
            "prior":  {"label": f"T3M to {pt3m[-1]}", "months": pt3m},
            "basis":  "same three calendar months, prior year",
        },
        "quarters": quarters,
        "fiscal_years": fiscal_years,
        "trailing": trailing,
        "qoq": {
            "refresh_now": refresh_now,
            "latest": {"label": _q_label(qy, q), "months": _q_months(qy, q)},
            "prior":  {"label": _q_label(pqy, pq), "months": _q_months(pqy, pq)},
        },
    }
