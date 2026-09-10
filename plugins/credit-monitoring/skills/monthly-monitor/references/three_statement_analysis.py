#!/usr/bin/env python3
"""
Three-statement analysis module for the credit portfolio monitor -- v3.

GENERALIZED, borrower-agnostic. Reads the canonical monthly IS/BS/CF model (build-monitoring-model)
plus borrower-config and returns ONE JSON conclusion: metrics + threshold flags PLUS an analysis pack
that lets synthesis write real commentary rather than a ratio dump.

v4 REMOVES ALL STATISTICAL SCREENING (z-scores / median-MAD). Every IS and BS leaf line is
read and trended each month (`read_lines`); flags are deterministic rules on an absolute
materiality floor (frozen / monotonic_run / sign_flip / new / vanished / big_move /
share_shift / related_party) that work from the SECOND month of history — short-history
borrowers get full coverage instead of suppressed screens. detect_standouts and
build_bs_screen are deleted; drift (rule-based) is retained; OCF-distortion flagging is
rule-based. Tunables: config.analysis.line_read {materiality_floor, run_min_months,
frozen_min_months, big_move_pct, share_shift_ppt}.

Segmentation, drift and trailing-average behavior:
  * Segmentation  -- `analysis.basis_breaks` splits the month series at entity/consolidation changes
                     (e.g. an acquisition). Standout baselines, drift, direction, trailing averages and
                     TTM aggregates are confined to the CURRENT (latest) same-basis segment. Below a
                     minimum same-basis history, standouts/drift are suppressed with a reason rather
                     than computed against a mixed-basis baseline. YoY across a break is still emitted
                     but tagged `crosses_basis_break: true`.
  * Bridge        -- NO hardcoded subtotal label ("Net Ordinary Income" or otherwise). The MoM Net
                     Income bridge resolves its own pre-financing subtotal from
                     `model_lines.pre_financing_subtotal` if configured, else derives it as
                     gross_profit - sum(opex_groups). The tie invariant (bridge components must sum to
                     the NI delta within $1) is enforced IN CODE; a non-tying bridge is never emitted as
                     fact -- it is marked `status: "unresolved"` plus an exception entry.
  * Ratio suppression -- DIO/CCC/inventory metrics are suppressed (with reason) when there is no
                     resolvable inventory line, or the configured business model doesn't warrant an
                     inventory suite. total_debt: explicit config wins; else it is AUTO-AGGREGATED
                     from debt-TAGGED balance-sheet leaves (ex-capital-leases; components disclosed in
                     `funded_debt`) -- never a blind "long term liabilities" regex. Leverage is reported
                     as Debt/TTM-Gross-Profit (always-on) plus Debt/TTM-EBITDA (when EBITDA > 0).
                     Every suppression is disclosed in `ratios_suppressed[]`.
  * Liquidity     -- burn (1m / 3m), OCF-distortion flags (financing-driven inflows, OCF sign anomalies
                     vs. the line's own same-basis history -- detected mechanically, no borrower facts
                     hardcoded), cash floor resolved from config or covenant-spec, months-to-floor at
                     1m and 3m burn.
  * Direction     -- sign of the MEDIAN of same-basis MoM deltas (never endpoint-to-endpoint).
  * Drift         -- per P&L leaf line, within the longest usable same-basis segment: sustained
                     multi-month moves that spike detection cannot see (a line bleeding out gradually).
  * Revenue mix   -- every revenue leaf trended (level, share, MoM, same-basis YoY, direction).
  * Opex-group trends -- per configured opex group: level, MoM, same-basis YTD vs prior same-basis
                     YTD, direction.
  * BS screen     -- leaf balance-sheet lines: new lines, spike/drop vs same-basis history, and
                     related-party keyword hits (officer / affiliate / related / due from / due to /
                     shareholder / intercompany) -- always reported even if none are found.
  * Context conditioning -- deal-context `static` and covenant-spec may be read to decide WHAT to
                     compute and WHICH thresholds apply (never to adjust a computed figure). Every
                     conditioning decision is recorded in `context_conditioning.applied[]`.

EBITDA is the model's single pinned bridge row (never recomputed). One-time items come from
config.comparability_items. IS lines from config.model_lines / opex_groups; BS/CF lines from
config.analysis_lines or default candidate labels (unresolved lines are flagged, never fatal).
Tunables live in config.analysis (outlier_min_abs, line_read{...}, trend_window, seasonality,
basis_breaks, business_model, cash_floor, drift{...}).

Usage:
    python three_statement_analysis.py --borrower-config <borrower-config.json> \
        --model <model workbook .xlsx> --out <output .json>
--model may be omitted if derivable from borrower-config.paths.model_output_path.
"""

import argparse
import os
import re
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "lib"))
if os.path.isdir(_LIB_DIR) and _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import monitor_lib as ml  # noqa: E402
import rows_emit # noqa: E402
try:
    import trend_signals  # scale-aware relational/ratio-trend signals (additive)
except Exception:
    trend_signals = None

_ENGINE_VERSION = ("3-statement v4.3 (pure line read; scale-aware materiality floor when not "
                   "pinned per borrower) + per-figure source refs/calc + unresolved-funded-debt flag")

# Default BS/CF candidate labels. NOTE: total_debt is intentionally NOT included here -- it is never
# taken from a blind "long term liabilities" regex. It comes from explicit config (model_lines /
# analysis_lines.total_debt) OR is auto-aggregated from debt-tagged leaves via derive_funded_debt().
_DEFAULT_LINES = {
    "cash":                      ["Total - 1000 - Cash", r"^total .*cash$", r"^total bank$", r"^cash$"],
    "accounts_receivable":       ["Total Accounts Receivable", r"total .*accounts receivable", r"total.*receivable$"],
    "inventory":                 ["Total - 1300 - Inventory", r"^total .*inventory$", r"total inventory"],
    "total_current_assets":      ["Total Current Assets", r"total current assets"],
    "total_assets":              ["Total ASSETS", r"^total assets$"],
    "accounts_payable":          ["Total Accounts Payable", r"total .*accounts payable", r"total.*payable$"],
    "total_current_liabilities": ["Total Current Liabilities", r"total current liab"],
    "total_equity":              ["Total Equity", r"^total equity$"],
    "operating_cf":              ["Total Operating Activities", r"total operating activ", r"^cash from operations$"],
    "investing_cf":              ["Total Investing Activities", r"total investing activ", r"^cash from investing$"],
    "financing_cf":              ["Total Financing Activities", r"total financing activ", r"^cash from financing$"],
    "net_change_cash":           ["Net Change in Cash for Period", r"net change in cash"],
}
_BS_KEYS = {"cash", "accounts_receivable", "inventory", "total_current_assets", "total_assets",
            "accounts_payable", "total_current_liabilities", "total_debt", "total_equity"}
_CF_KEYS = {"operating_cf", "investing_cf", "financing_cf", "net_change_cash"}
# grand P&L subtotals excluded from leaf-level standouts / line-movers / drift / BS screen
_SUBTOTALS = {"total - income", "gross profit", "gross margin", "net ordinary income", "net income",
              "net income (loss)", "total - expense", "total - cost of sales",
              "net ordinary income/expense", "total costs of revenue", "total operating expenses",
              "revenue", "costs of revenue", "operating expenses", "assets", "liabilities", "equity",
              "validation"}


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else None


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _pct(a, b):
    r = _safe_div(a, b)
    return round(r, 6) if r is not None else None


def _num(x):
    return ml.safe_float(x)


def fmt_usd(x, unit):
    """A model figure written out as money, for a person to read.

    Model figures are in the model's own unit, so a deal reporting in thousands
    holds 2,200 where it means $2.2m. Printed straight, every sentence in the
    memo understated that borrower a thousand-fold: "cash reaches the $750,000
    floor in ~0.0 months (cash $1,100, 1m burn $50)". So everything a person
    reads goes back to dollars first, and every comparison stays in the model's
    unit (monitor_lib.reporting_unit).
    """
    if not isinstance(x, (int, float)):
        return "n/a"
    d = unit.to_dollars(x)
    return f"(${abs(d):,.0f})" if d < 0 else f"${d:,.0f}"


def fmt_pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "n/a"


def word_dir(x):
    if not isinstance(x, (int, float)):
        return "moved"
    return "rose" if x > 0 else ("fell" if x < 0 else "was flat")


def _skip_line(nl, extra_norm=None):
    """True for subtotal / memo / bridge rows that should not be treated as leaf-account movers."""
    if nl in _SUBTOTALS:
        return True
    if extra_norm and (nl == extra_norm if isinstance(extra_norm, str) else nl in extra_norm):
        return True
    if nl.startswith(("total", "net ", "memo", "provisional", "subtotal", "ordinary income",
                       "ebitda", "note ", "note1", "note 1")):
        return True
    if nl in {"other income", "other expense", "income", "expense", "operating expenses",
              "cost of sales", "gross profit", "gross margin", "net income", "balance check",
              "cf-vs-bs check", "cf-vs-is check"}:
        return True
    if "%" in nl or "margin %" in nl:
        return True
    # Bridge/reconciliation add-back notation -- e.g. "(+) Interest / Taxes / D&A / other
    # add-backs per covenant-spec.json#/definitions/EBITDA" or "(-) <contra item>". These are
    # reconciliation-detail rows that mirror an amount already reflected elsewhere in the P&L (the
    # model's own EBITDA bridge plug), not an independent leaf account -- counting both would
    # double the apparent driver in standouts/drift/line_movers.
    if nl.startswith(("(+)", "(-)", "+ ", "- ")):
        return True
    return False


def _mark_memo_section_rows(sheet_rows_ordered):
    """Given the (normalized_label, values) pairs of a sheet IN SHEET ROW ORDER, return the set of
    normalized labels that fall on or after the first 'note'-prefixed row (a memo/reference section
    footer, e.g. a "Note 1 -- MRR / Covenant MRR..." block) THROUGH THE REST OF THE SHEET.
    Rows inside such a memo section (e.g. a covenant-MRR cross-reference line) are reconciliation
    detail, not leaf P&L accounts, even when their own label doesn't start with a recognized
    prefix -- catching them by section membership is more robust than enumerating every possible
    memo-row name."""
    out = set()
    in_memo = False
    for nl, _ in sheet_rows_ordered:
        if nl.startswith(("note ", "note1", "note 1", "memo")):
            in_memo = True
        if in_memo:
            out.add(nl)
    return out


def _median(vals):
    xs = sorted(v for v in vals if v is not None)
    return statistics.median(xs) if xs else None


def _median_direction(deltas, latest):
    """direction = sign of the median MoM delta in `deltas`, relative to abs(latest) (or 1.0 if falsy)."""
    med_delta = _median(deltas)
    if med_delta is None:
        return None
    base = abs(latest) if latest else 1.0
    return "rising" if med_delta / base > 0.01 else ("falling" if med_delta / base < -0.01 else "flat")


# ---------------------------------------------------------------------------
# Line resolution
# ---------------------------------------------------------------------------
def _sheet_for(key):
    if key in _CF_KEYS:
        return "Cash Flow Statement"
    if key in _BS_KEYS:
        return "Balance Sheet"
    return "Income Statement"


_ADDBACK_TAGS = ("miscellaneous", "misc expense", "one-time", "one time", "non-recurring",
                 "nonrecurring", "restructuring", "impairment", "write-off", "write off",
                 "write-down", "write down", "realized", "loss on", "transaction cost",
                 "management fee", "sponsor fee", "monitoring fee", "litigation", "settlement",
                 "severance")
def ebitda_addback_register(model, S, months, li, seg):
    """Quality-of-EBITDA: identify below-the-line / discretionary lines a borrower may add back to
    EBITDA that are NOT in the configured bridge (interest / tax / D&A). Quantifies each candidate
    (latest + TTM) and whether adding them back materially changes -- or flips the sign of -- EBITDA.
    Surfaces the kind of large opaque 'Miscellaneous Expenses' add-back covenant EBITDA can lean on."""
    rows = (getattr(model, "sheets", {}) or {}).get("Income Statement") or {}
    idx = seg[-12:] if len(seg) >= 12 else seg
    def _ttm(vals):
        tot = 0.0; seen = False
        for i in idx:
            if i < len(vals):
                v = _num(vals[i])
                if v is not None:
                    tot += v; seen = True
        return tot if seen else None
    cands = []
    for lab, vals in rows.items():
        l = lab.strip().lower()
        if l.startswith("total") or l.startswith("net ") or "ebitda" in l or "memo" in l:
            continue
        if any(t in l for t in _ADDBACK_TAGS):
            t_ttm = _ttm(vals); latest = _num(vals[li]) if li < len(vals) else None
            if (t_ttm not in (None, 0)) or (latest not in (None, 0)):
                cands.append({"line": lab, "latest": _round(latest) if latest is not None else None,
                              "ttm": _round(t_ttm) if t_ttm is not None else None})
    if not cands:
        return None, None
    eb = S.get("ebitda_bridge") or []
    ca_ttm = None
    if len(seg) >= 12:
        w = [eb[i] for i in idx if i < len(eb)]
        if len(w) == len(idx):
            ca_ttm = sum(v for v in w if v is not None)
    cand_total = sum(c["ttm"] or 0.0 for c in cands)
    adjusted = (ca_ttm + cand_total) if ca_ttm is not None else None
    flips = (ca_ttm is not None and adjusted is not None and ca_ttm < 0 <= adjusted)
    reliant = flips or (ca_ttm is not None and adjusted not in (None, 0)
                        and abs(cand_total) >= 0.25 * abs(adjusted))
    reg = {"ca_ebitda_ttm": _round(ca_ttm) if ca_ttm is not None else None,
           "addback_candidates": sorted(cands, key=lambda c: abs(c["ttm"] or 0), reverse=True),
           "candidate_total_ttm": _round(cand_total),
           "ebitda_with_addbacks_ttm": _round(adjusted) if adjusted is not None else None,
           "flips_sign": bool(flips), "addback_reliant": bool(reliant),
           "note": ("Reconstructed EBITDA is negative but turns positive once these below-the-line "
                    "items are added back -- covenant EBITDA leans on discretionary add-backs; verify each is permitted."
                    if flips else
                    "material discretionary add-backs relative to EBITDA -- verify each is permitted under the EBITDA definition."
                    if reliant else
                    "candidate below-the-line add-backs identified; not material to EBITDA this period.")}
    exc = None
    if reliant:
        exc = {"flag": "ebitda_quality_addback_reliant", "severity": "elevated" if flips else "watch",
               "detail": (f"EBITDA quality: reconstructed TTM EBITDA {reg['ca_ebitda_ttm']:,} vs "
                          f"{reg['ebitda_with_addbacks_ttm']:,} with {reg['candidate_total_ttm']:,} of "
                          f"discretionary add-backs ({', '.join(c['line'] for c in reg['addback_candidates'][:3])})")}
    return reg, exc


_DEBT_TAGS = ("notes payable", "note payable", "promissory", "loan", "convertible",
              "paid in kind", "pik", "line of credit", "revolver", "mortgage",
              "debenture", "bond payable", "bonds payable",
              # Two-word, so a balance sheet's own "facility" (an improvement, a
              # deposit) is not read as borrowing; "Senior Secured Term Facility"
              # is a representative debt label.
              "term facility", "credit facility")
# The word "debt" itself, however the borrower words the row around it -- "Long-term
# debt", "Debt - Long Term", "Debt, net of current portion". A substring list cannot
# carry this and stay honest: "current debt" misses "Debt - Current", and every new
# borrower spelling would otherwise need another tag.
_DEBT_WORD_RX = re.compile(r"\bdebt\b")
# Rows that carry a debt word without stating funded debt -- a capitalized cost
# sitting in assets, or an expense. Checked first, so they reach neither the sum nor
# the lease split.
_DEBT_FALSE_FRIENDS = ("debt issuance", "deferred debt", "unamortized debt",
                       "debt discount", "bad debt", "debt service reserve")
_LEASE_TAGS = ("lease liabilit",)
_MEMO_MARKS = ("memo:", "memo -", "memo –", "memo—", "(memo)", "memo item")


def _is_debt_label(label_lower):
    """Whether a balance-sheet row states funded debt: one of the tagged instruments,
    or the word "debt" in any wording -- minus the rows that only sound like debt."""
    if any(t in label_lower for t in _DEBT_FALSE_FRIENDS):
        return False
    return (bool(_DEBT_WORD_RX.search(label_lower))
            or any(t in label_lower for t in _DEBT_TAGS))


def _is_memo(label_lower):
    """Whether a balance-sheet row is a memo line — a note the model's builder
    wrote for a reader, restating figures that are already on the sheet.

    A memo restating funded debt is the same dollars a second time, so counting
    one doubles the borrower's leverage. Labels such as "memo: long-term loans",
    "memo: short-term loan payable", and "memo: total funded debt" must therefore
    stay out of the funded-debt sum.
    """
    return any(m in label_lower for m in _MEMO_MARKS)


def _carries_balance(v):
    """Whether a row states a balance in this month. Zero says nothing: zero
    ties the sum of any set of zeros, so a run of empty debt rows would read as
    a perfect rollup of each other, cascading down a block and dropping every
    row after the first."""
    return v is not None and abs(v) > 1.0


def derive_funded_debt(model, unit, sheet="Balance Sheet"):
    """Disciplined funded-debt aggregation from debt-stating balance-sheet leaves -- a sum of the
    rows that name an instrument (notes/loans/convertible/PIK/mortgage/etc.) or carry the word
    "debt", EX capital leases (leases tracked separately) and EX the rows that only sound like debt
    (see _is_debt_label). NOT a generic 'long term liabilities' regex: a row that summarizes
    liabilities without saying debt is left to a person to pin. Components are disclosed so the
    debt figure states its basis.

    Section-rollup de-duplication: a debt-tagged row can itself be a SECTION HEADER that already
    carries its own children's total (e.g. a "Long-term Loans" row immediately followed by
    per-lender breakout rows -- "the lead lender", a convertible note -- that sum to it; the
    header matches a debt tag ("loan") purely by coincidence of wording, not because it is an
    independent balance). Counting the header AND a child that ALSO independently matches a debt
    tag (e.g. the convertible note, tagged via "convertible"/"note payable") double-counts the same
    dollars. Detected structurally: a matched row whose immediate sheet successors (up to the next
    'total'/'balance check' row, which is already excluded) sum to it for the large majority of
    populated months (tolerant of the kind of rounding/restatement noise this kind of monthly model
    can carry) is treated as the rollup.

    Header and block then stand in for each other MONTH BY MONTH. In a month where the header
    states a balance, the header alone is counted and its successors are skipped -- the header's
    total already reflects them. In a month where the header is blank, its successors are counted
    instead, so a block whose header the model stops carrying while its child rows continue still
    reaches the sum -- both the tagged children and the otherwise untagged lender names. The tie is therefore
    applied only over the months it was observed on. Never a hardcoded label -- a borrower whose
    debt row does not tie to its followers is left alone as a plain leaf."""
    rows_items = list(((getattr(model, "sheets", {}) or {}).get(sheet) or {}).items())
    n = len(model.months or [])
    lease_idx, matched_idx = [], []
    for i, (lab, vals) in enumerate(rows_items):
        l = lab.strip().lower()
        if l.startswith("total") or "balance check" in l or _is_memo(l):
            continue
        if any(t in l for t in _LEASE_TAGS):
            lease_idx.append(i)
        elif _is_debt_label(l):
            matched_idx.append(i)
    matched_set = set(matched_idx)

    rollup_parents, header_of = [], {}
    for pos in matched_idx:
        lab_p, vals_p = rows_items[pos]
        fv_p = [_num(v) for v in vals_p]
        followers = []
        j = pos + 1
        while j < len(rows_items):
            lab_j, vals_j = rows_items[j]
            lj = lab_j.strip().lower()
            if lj.startswith("total") or "balance check" in lj:
                break
            # A memo row sitting inside the block is skipped here for the same
            # reason it is skipped when tagging: it restates figures already on
            # the sheet. Counted as a follower it inflates the sum, the tie is
            # missed, and a genuine header keeps its children -- overstating
            # debt, which is the opposite of the error this detection prevents.
            if not _is_memo(lj):
                followers.append((j, lab_j, [_num(v) for v in vals_j]))
            j += 1
        if not followers:
            continue
        checked = matched = 0
        for k in range(n):
            pv = fv_p[k] if k < len(fv_p) else None
            if not _carries_balance(pv):
                continue
            fsum = sum((fv[k] if k < len(fv) else None) or 0.0 for _, _, fv in followers)
            checked += 1
            # $1,000 is a dollar figure; on a thousands model an unconverted
            # tolerance is $1,000,000, and a rollup that misses by up to a
            # million publishes as tied.
            if abs(fsum - pv) <= max(unit.from_dollars(1000.0), 0.02 * abs(pv)):
                matched += 1
        if checked and matched / checked >= 0.8:
            child_labels = [lab_j for j, lab_j, _ in followers if j in matched_set]
            rollup_parents.append({"label": lab_p, "children": [lab_j for _, lab_j, _ in followers],
                                   "double_counted_children": child_labels,
                                   "months_checked": checked, "months_tied": matched,
                                   "months_header_blank": n - checked})
            for j, _, _ in followers:
                header_of.setdefault(j, pos)

    exlease = [0.0] * n; lease = [0.0] * n; comps = []; lcomps = []
    for i, (lab, vals) in enumerate(rows_items):
        fv = [_num(v) for v in vals]
        if i in lease_idx:
            for k, v in enumerate(fv):
                if v is not None and k < n:
                    lease[k] += v
            lcomps.append(lab)
        elif i in header_of:
            # In a month the header carries, these dollars are already inside it.
            fv_h = [_num(v) for v in rows_items[header_of[i]][1]]
            stood_in = False
            for k, v in enumerate(fv):
                if v is not None and k < n and not _carries_balance(
                        fv_h[k] if k < len(fv_h) else None):
                    exlease[k] += v
                    stood_in = True
            if stood_in:
                comps.append(lab)
        elif i in matched_set:
            for k, v in enumerate(fv):
                if v is not None and k < n:
                    exlease[k] += v
            comps.append(lab)
    incl = [exlease[i] + lease[i] for i in range(n)]
    return {"exlease": exlease, "incl": incl, "lease": lease, "components": comps,
            "lease_components": lcomps,
            "rollup_parents_excluded": rollup_parents or None}


_DEBT_CANDIDATE_TAGS = ("debt", "liabilit", "note", "loan", "borrowing", "facility",
                        "credit", "payable to")


def _debt_row_candidates(model, sheet="Balance Sheet", limit=8):
    """Balance-sheet rows that could hold funded debt, largest first — what to
    offer when nothing was configured and nothing auto-aggregated, so the ask is
    "is it one of these?" rather than "leverage is unavailable".

    A borrower whose balance sheet summarizes everything into one
    long-term-liabilities line matches no debt tag by design (this engine never
    guesses debt off a generic label), and that is exactly the case that needs a
    person to point at the row."""
    rows = (getattr(model, "sheets", {}) or {}).get(sheet) or {}
    meta = (getattr(model, "_meta", {}) or {}).get(sheet) or {}
    out = []
    for lab, vals in rows.items():
        l = lab.strip().lower()
        if l.startswith("total") or "balance check" in l:
            continue
        if not any(t in l for t in _DEBT_CANDIDATE_TAGS):
            continue
        latest = next((_num(v) for v in reversed(list(vals)) if _num(v)), None)
        if latest:
            # The borrower's own spelling of the row, so the analyst can find it.
            display = (meta.get(lab) or (None, lab))[1] or lab
            out.append((abs(latest), display))
    out.sort(reverse=True)
    return [lab for _, lab in out[:limit]]


def resolve_series(model, cfg, key, notfound, allow_default=True):
    sheet = _sheet_for(key)
    cands = []
    al = (cfg.get("analysis_lines") or {}).get(key)
    if al:
        cands.append(al)
    mln = cfg.get("model_lines") or {}
    if key in mln and mln[key]:
        cands.append(mln[key])
    if allow_default:
        cands += _DEFAULT_LINES.get(key, [])
    for c in cands:
        hit = model.find_row(c, sheet=sheet) or model.find_row(c, sheet=None)
        if hit is not None:
            return hit[0], [_num(v) for v in hit[1]]
    # Always disclose an unresolved key -- including keys with NO default candidate at all (e.g.
    # revenue, gross_profit, net_income have no _DEFAULT_LINES entry, so an unconfigured borrower
    # would otherwise resolve to None with NOTHING recorded in lines_not_found; that would violate
    # the "unresolved lines always disclosed" invariant for exactly the core P&L lines a reader
    # most needs to know are missing).
    notfound.append(key if cands else f"{key} (not configured in model_lines/analysis_lines)")
    return None, None


def _at(series, idx):
    if series is None or idx is None or idx < 0 or idx >= len(series):
        return None
    return series[idx]


def _sum_idx(series, idxs):
    if series is None or idxs is None:
        return None
    vals = [series[i] for i in idxs if 0 <= i < len(series) and series[i] is not None]
    return sum(vals) if vals else 0.0


def orig_label_map(wb):
    """normalized label -> original (case-preserving) label, from the workbook column A."""
    m = {}
    for sn in wb.sheetnames:
        for row in wb[sn].iter_rows(min_col=1, max_col=1):
            v = row[0].value
            if isinstance(v, str) and v.strip():
                m.setdefault(ml.norm_label(v), v.strip())
    return m


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def ytd_indices(months, latest_idx, fy_start_month):
    ly, lm = ml._parse_label(months[latest_idx])
    latest_fy = ly if lm >= fy_start_month else ly - 1
    out = []
    for i in range(latest_idx + 1):
        y, m = ml._parse_label(months[i])
        fy = y if m >= fy_start_month else y - 1
        if fy == latest_fy:
            out.append(i)
    return out


def prior_ytd_indices(months, ytd_idxs):
    norm = {ml.norm_label(l): j for j, l in enumerate(months)}
    idxs = []
    for i in ytd_idxs:
        y, m = ml._parse_label(months[i])
        j = norm.get(ml.norm_label(ml._label_from(y - 1, m)))
        if j is None:
            return None
        idxs.append(j)
    return idxs


def _yoy_index(months, li):
    """Index of the same calendar month a year earlier, or None."""
    try:
        yy, mm = ml._parse_label(months[li])
        norm = {ml.norm_label(l): j for j, l in enumerate(months)}
        return norm.get(ml.norm_label(ml._label_from(yy - 1, mm)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Segmentation (v3)
# ---------------------------------------------------------------------------
def build_segments(months, basis_breaks):
    """Split the month index series into same-basis segments at each configured break.

    `basis_breaks`: [{"effective": "Apr 2026", "reason": "..."}] -- the pinned schema
    (matching the covenant vocabulary); legacy keys `month`/`note` are accepted as
    aliases. A break at month M means M itself starts the new segment (the
    entity/consolidation change is first reflected IN that month).

    Returns: {
      "breaks": [{"month","note","index"}] (index = the month index where the new segment starts,
                 or None if that break month is not present in the model),
      "segment_of": [seg_id per month index],
      "segments": [[idx,...], ...]  (ordered oldest-first; each a contiguous list of month indices),
      "invalid": [entry,...]  (entries with no recognized month key -- surfaced loudly
                 by the caller rather than dropped silently),
    }
    """
    norm = {ml.norm_label(l): j for j, l in enumerate(months)}
    break_idxs = []
    break_meta = []
    invalid = []
    for b in (basis_breaks or []):
        if not isinstance(b, dict):
            invalid.append(b)
            continue
        # 'effective'/'reason' is the pinned schema; 'month'/'note' is a legacy alias.
        when = b.get("effective") or b.get("month")
        if not when:
            invalid.append(b)
            continue
        reason = b.get("reason") or b.get("note") or ""
        j = norm.get(ml.norm_label(when))
        break_meta.append({"month": when, "note": reason, "index": j})
        if j is not None:
            break_idxs.append(j)
    break_idxs = sorted(set(break_idxs))

    segment_of = [0] * len(months)
    seg_id = 0
    for i in range(len(months)):
        while seg_id < len(break_idxs) and i >= break_idxs[seg_id]:
            seg_id += 1
        segment_of[i] = seg_id
    n_segs = (segment_of[-1] + 1) if months else 1
    segments = [[] for _ in range(n_segs)]
    for i, s in enumerate(segment_of):
        segments[s].append(i)
    return {"breaks": break_meta, "segment_of": segment_of, "segments": segments,
            "invalid": invalid}


def crosses_break(seg_info, i, j):
    """True if month indices i and j fall in different same-basis segments."""
    if i is None or j is None:
        return False
    so = seg_info["segment_of"]
    if i >= len(so) or j >= len(so):
        return False
    return so[i] != so[j]


# ---------------------------------------------------------------------------
# Analysis pack: trend, standouts, drift, MoM bridge, headlines
# ---------------------------------------------------------------------------
def trend_descriptor(series, months, li, window, seg_info, ratio=False):
    """Trend descriptor confined to the CURRENT SAME-BASIS SEGMENT for trailing avg / direction /
    peak / trough. YoY is still computed across the full series but tagged if it crosses a break."""
    if not series:
        return None
    vals = series
    rnd = (lambda x: round(x, 6) if isinstance(x, (int, float)) else None) if ratio else _round
    latest = _at(vals, li)
    prior = _at(vals, li - 1)
    mom_abs = (latest - prior) if (latest is not None and prior is not None) else None
    mom_pct = _pct(mom_abs, abs(prior)) if (mom_abs is not None and prior not in (None, 0)) else None

    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_start = cur_seg[0]
    # trailing average: same-basis months only, within the configured window
    win_idxs = [i for i in cur_seg if seg_start <= i < li][-window:] if window > 0 else []
    t = [vals[i] for i in win_idxs if vals[i] is not None]
    tavg = sum(t) / len(t) if t else None
    vs_tavg = (latest - tavg) if (latest is not None and tavg is not None) else None

    # direction = sign of the MEDIAN of same-basis MoM deltas over the trailing window (+ latest)
    dwin_idxs = [i for i in cur_seg if seg_start < i <= li][-window:] if window > 0 else []
    deltas = []
    for i in dwin_idxs:
        a, b = _at(vals, i), _at(vals, i - 1)
        if a is not None and b is not None:
            deltas.append(a - b)
    med_delta = _median(deltas)
    direction = None
    if med_delta is not None:
        base = abs(tavg) if tavg else (abs(latest) if latest else 1.0) or 1.0
        direction = "rising" if med_delta / base > 0.01 else ("falling" if med_delta / base < -0.01 else "flat")

    # peak/trough within the CURRENT SEGMENT only (same-basis, per spec). Separately note whether
    # a MORE EXTREME all-time value exists outside the current segment (necessarily pre-break,
    # since the current segment is by definition the latest/last segment) -- this is a caveat about
    # the wider series, not a property of the segment peak/trough itself (which can never be
    # "pre-break": it is, tautologically, IN the current segment).
    seg_iv = [(i, vals[i]) for i in cur_seg if vals[i] is not None]
    peak = max(seg_iv, key=lambda t: t[1]) if seg_iv else None
    trough = min(seg_iv, key=lambda t: t[1]) if seg_iv else None
    all_iv = [(i, vals[i]) for i in range(len(vals)) if vals[i] is not None]
    all_peak = max(all_iv, key=lambda t: t[1]) if all_iv else None
    all_trough = min(all_iv, key=lambda t: t[1]) if all_iv else None
    more_extreme_peak_pre_break = bool(all_peak and peak and all_peak[0] != peak[0] and all_peak[1] > peak[1])
    more_extreme_trough_pre_break = bool(all_trough and trough and all_trough[0] != trough[0] and all_trough[1] < trough[1])

    # same calendar month, prior year -- gated on availability; tagged if it crosses a basis break
    yoy_abs = yoy_pct = yoy_prior_month = yoy_prior_value = None
    yoy_crosses = False
    pj = _yoy_index(months, li)
    if pj is not None and _at(vals, pj) is not None and latest is not None:
        yoy_prior_month, yoy_prior_value = months[pj], vals[pj]
        yoy_abs = latest - vals[pj]
        yoy_pct = _pct(yoy_abs, abs(vals[pj])) if vals[pj] not in (None, 0) else None
        yoy_crosses = crosses_break(seg_info, li, pj)

    return {
        "latest": rnd(latest), "prior": rnd(prior), "mom_abs": rnd(mom_abs), "mom_pct": mom_pct,
        "trailing_avg": rnd(tavg), "vs_trailing_avg": rnd(vs_tavg), "direction": direction,
        "yoy_abs": rnd(yoy_abs), "yoy_pct": yoy_pct, "crosses_basis_break": yoy_crosses,
        "yoy_prior_month": yoy_prior_month, "yoy_prior_value": rnd(yoy_prior_value),
        "peak": ({"month": months[peak[0]], "value": rnd(peak[1]),
                  "more_extreme_value_pre_break": more_extreme_peak_pre_break}
                 if peak else None),
        "trough": ({"month": months[trough[0]], "value": rnd(trough[1]),
                    "more_extreme_value_pre_break": more_extreme_trough_pre_break}
                   if trough else None),
    }


def read_lines(model, sheet, months, li, seg_info, orig, params, skip_norms=None,
               ytd_idxs=None, denom_series=None, flag_share_shift=False,
               src=None, denom_label=None):
    """v4 PURE LINE READ (replaces the v3 statistical screens -- no z-scores, no median/MAD).

    EVERY leaf line on `sheet` is read and trended each month. Facts per line: latest, prior,
    MoM, trailing-3m average, YTD (when ytd_idxs given), consecutive same-direction run, months
    frozen at an identical value, new/vanished, sign flip, and share of `denom_series`
    (common-size) with the shift since segment start. Flags are DETERMINISTIC RULES on a single
    absolute materiality floor, so they work from the SECOND month of history -- no statistical
    baseline required:
      frozen         same nonzero value for >= frozen_min_months consecutive month-ends
      monotonic_run  >= run_min_months consecutive same-direction MoM moves, cumulative >= floor
      sign_flip      latest and prior have opposite signs (larger side >= floor)
      new            first material balance/activity this month (sheet has earlier history)
      vanished       materially populated history, zero/absent now
      big_move       |MoM| >= floor AND (|MoM %| >= big_move_pct OR prior == 0)
      share_shift    share of denominator moved >= share_shift_ppt since segment start
      related_party  row label contains a related-party keyword (always reported)
    Lookbacks are confined to the CURRENT same-basis segment (comparability, not statistics).
    """
    min_abs = float(params["materiality_floor"])
    run_min = int(params["run_min_months"])
    frozen_min = int(params["frozen_min_months"])
    big_pct = float(params["big_move_pct"])
    share_ppt = float(params["share_shift_ppt"])
    RELATED = ["officer", "affiliate", "related part", "due from", "due to",
               "shareholder", "stockholder", "intercompany", "related-party"]

    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_idxs = [i for i in cur_seg if i <= li]
    seg_start = seg_idxs[0] if seg_idxs else 0

    sheet_rows = list(model.sheets.get(sheet, {}).items())
    # does the sheet have ANY populated cell before the latest month (within the segment)?
    sheet_has_history = any(
        _num(series[i]) is not None
        for _, series in sheet_rows for i in seg_idxs if i < li and i < len(series))

    lines, flagged = [], []
    for nl, series in sheet_rows:
        if _skip_line(nl, skip_norms):
            continue
        vals = [_num(v) for v in series]
        pop = [i for i in seg_idxs if _at(vals, i) is not None]
        if not pop:
            continue
        label = orig.get(nl, nl)
        latest = _at(vals, li)
        prior = _at(vals, li - 1) if (li - 1) in seg_idxs else None
        mom_abs = (latest - prior) if (latest is not None and prior is not None) else None
        mom_pct = _pct(mom_abs, abs(prior)) if (mom_abs is not None and prior not in (None, 0)) else None
        t3 = [vals[i] for i in seg_idxs if i < li and vals[i] is not None][-3:]
        avg_3m = (sum(t3) / len(t3)) if t3 else None

        # consecutive same-direction run of MoM moves ending at the latest month
        run_months, run_total, run_dir = 0, 0.0, 0
        j = li
        while j - 1 >= seg_start and _at(vals, j) is not None and _at(vals, j - 1) is not None:
            d = vals[j] - vals[j - 1]
            s = 1 if d > 0 else (-1 if d < 0 else 0)
            if s == 0 or (run_dir and s != run_dir):
                break
            run_dir = s
            run_months += 1
            run_total += d
            j -= 1

        # months frozen at an identical value, ending at the latest month
        frozen_months = 0
        if latest is not None:
            j = li
            while j >= seg_start and _at(vals, j) == latest:
                frozen_months += 1
                j -= 1

        prior_pop = [vals[i] for i in pop if i < li]
        prior_material = any(v is not None and abs(v) >= min_abs for v in prior_pop)
        last_j = max((i for i in pop if i < li), default=None)
        last_val = _at(vals, last_j) if last_j is not None else None
        gap_months = (li - last_j) if last_j is not None else None
        # comparison base: the adjacent prior month when populated, else the LAST OBSERVED value
        # (a dormant line that re-posts must still be compared against something real)
        cmp_prior = prior if prior is not None else last_val
        is_new = (latest is not None and abs(latest) >= min_abs
                  and not prior_material and sheet_has_history)
        # vanished: an ACTIVE line stopped (adjacent prior month was material, now zero/absent) --
        # not merely a sporadic line skipping a month
        vanished = (prior is not None and abs(prior) >= min_abs and (latest is None or latest == 0))
        resumed = (latest is not None and abs(latest) >= min_abs and gap_months is not None
                   and gap_months > 1 and prior_material)
        sflip = (latest is not None and cmp_prior is not None and latest * cmp_prior < 0
                 and max(abs(latest), abs(cmp_prior)) >= min_abs)

        share = share_start = None
        if denom_series is not None:
            dl = _at(denom_series, li)
            share = _pct(latest, dl) if (latest is not None and dl not in (None, 0)) else None
            f0 = pop[0]
            v0, d0 = _at(vals, f0), _at(denom_series, f0)
            share_start = _pct(v0, d0) if (v0 is not None and d0 not in (None, 0)) else None
        share_shift = (share - share_start) if (share is not None and share_start is not None) else None

        flags = []
        if any(kw in nl for kw in RELATED):
            flags.append("related_party")
        if frozen_months >= frozen_min and latest not in (None, 0) and abs(latest) >= min_abs:
            flags.append("frozen")
        if run_months >= run_min and abs(run_total) >= min_abs:
            flags.append("monotonic_run")
        if sflip:
            flags.append("sign_flip")
        if is_new:
            flags.append("new")
        if vanished:
            flags.append("vanished")
        if resumed:
            flags.append("resumed_after_gap")
        move_abs = mom_abs if mom_abs is not None else (
            (latest - cmp_prior) if (latest is not None and cmp_prior is not None) else None)
        move_base = prior if prior is not None else cmp_prior
        move_pct = _pct(move_abs, abs(move_base)) if (move_abs is not None and move_base not in (None, 0)) else None
        if move_abs is not None and abs(move_abs) >= min_abs and \
                (move_base == 0 or (move_pct is not None and abs(move_pct) >= big_pct)):
            flags.append("big_move")
        if flag_share_shift and share_shift is not None and abs(share_shift) >= share_ppt / 100.0 \
                and (latest is not None and abs(latest) >= min_abs or prior_material):
            flags.append("share_shift")

        rec = {"line": label, "latest": _round(latest), "prior": _round(prior),
               "mom_abs": _round(mom_abs), "mom_pct": mom_pct, "avg_3m": _round(avg_3m),
               "run": ({"direction": ("up" if run_dir > 0 else "down"), "months": run_months,
                        "total": _round(run_total)} if run_months else None),
               "frozen_months": (frozen_months if frozen_months > 1 else None),
               "last_observed": ({"month": months[last_j], "value": _round(last_val),
                                   "gap_months": gap_months}
                                  if (gap_months is not None and gap_months > 1) else None),
               "share": share,
               "share_shift_since_start": (round(share_shift, 4) if share_shift is not None else None),
               "flags": (flags or None)}
        if ytd_idxs is not None:
            rec["ytd"] = _round(_sum_idx(vals, ytd_idxs))
        # Per-figure provenance: every FLAGGED record carries its own
        # source ref (the exact cell range the fired rules read) and a calc note
        # naming the rule(s) and the months/values involved -- the memo's
        # The statement reviews consume these (report-structure.md -> Appendix).
        if flags and src is not None:
            base_j = (li - 1) if prior is not None else last_j
            look = (li - base_j + 1) if base_j is not None else 1
            notes = []
            if "frozen" in flags:
                look = max(look, frozen_months)
                notes.append(f"frozen: unchanged {frozen_months} mo "
                             f"{months[li - frozen_months + 1]}..{months[li]} at {latest:,.6g}")
            if "monotonic_run" in flags:
                look = max(look, run_months + 1)
                notes.append(f"monotonic_run: {'up' if run_dir > 0 else 'down'} {run_months} mo in a "
                             f"row {months[li - run_months]}..{months[li]}, cumulative {run_total:+,.6g}")
            if "sign_flip" in flags:
                notes.append(f"sign_flip: {latest:,.6g} at {months[li]} vs {cmp_prior:,.6g} at "
                             f"{months[base_j] if base_j is not None else 'prior'}")
            if "new" in flags:
                look = max(look, li - seg_start + 1)
                notes.append(f"new: first material value {latest:,.6g} at {months[li]}; no prior >= "
                             f"{min_abs:,.0f} in segment (checked from {months[seg_start]})")
            if "vanished" in flags:
                notes.append(f"vanished: {prior:,.6g} at {months[li - 1]} -> zero/absent at {months[li]}")
            if "resumed_after_gap" in flags:
                notes.append(f"resumed_after_gap: {latest:,.6g} at {months[li]} after {gap_months}-mo "
                             f"gap (last observed {last_val:,.6g} in {months[last_j]})")
            if "big_move" in flags:
                pct_txt = f" ({move_pct:+.0%})" if move_pct is not None else ""
                notes.append(f"big_move: {move_abs:+,.6g}{pct_txt} vs "
                             f"{months[base_j] if base_j is not None else 'prior'}")
            if "share_shift" in flags:
                look = max(look, li - pop[0] + 1)
                notes.append(f"share_shift: {share_start:.1%} -> {share:.1%} of "
                             f"'{denom_label or 'denominator'}' since {months[pop[0]]}")
            if "related_party" in flags:
                notes.append("related_party: row label contains a related-party keyword")
            p = model.provenance(nl, sheet=sheet, month=months[li], window=look)
            if p:
                rrefs = [src.ref(model.given_path, **p)]
                if "share_shift" in flags and denom_label:
                    pd = model.provenance(denom_label, sheet=sheet, month=months[li],
                                          window=li - pop[0] + 1)
                    if pd:
                        rrefs.append(src.ref(model.given_path, **pd))
                rec["source_ref"] = rrefs
                rec["calc"] = "; ".join(notes) + f" ({p['sheet']} row {p['row']})"
        lines.append(rec)
        if flags:
            flagged.append(rec)

    flagged.sort(key=lambda r: -abs(r["mom_abs"] if r["mom_abs"] is not None else (r["latest"] or 0)))
    out = {
        "sheet": sheet, "months_read": len(seg_idxs),
        "lines": lines, "flagged": flagged,
        "params": {"materiality_floor": min_abs, "floor_basis": params.get("floor_basis"),
                   "run_min_months": run_min, "frozen_min_months": frozen_min,
                   "big_move_pct": big_pct, "share_shift_ppt": share_ppt},
        "note": ("every leaf line read and trended; flags are deterministic rules on an "
                 "absolute materiality floor -- no statistical gating; lookbacks confined "
                 "to the current same-basis segment"),
    }
    if src is not None and seg_idxs:
        # Section-level coverage for the full (unflagged) per-line table: one ref
        # describing the sweep; flagged records above carry their own exact refs.
        p0, p1 = ml.period_stamp(months[seg_idxs[0]]), ml.period_stamp(months[li])
        out["source_ref"] = [src.ref(model.given_path, sheet=sheet,
                                     period=(p1 if p0 == p1 else f"{p0}..{p1}"))]
        out["calc"] = (
            f"full leaf-line sweep of '{sheet}' {months[seg_idxs[0]]}..{months[li]}: "
            f"{len(lines)} leaf rows read (subtotal/memo rows excluded); per line: latest, prior, "
            f"MoM, trailing-3m avg, same-direction run, frozen months"
            + (f", share of '{denom_label}'" if denom_label else "")
            + f"; flags are deterministic rules on materiality floor {min_abs:,.0f}")
    return out


def detect_drift(model, months, li, orig, seg_info, min_abs, skip_norms=None,
                  min_months=6, min_same_sign_share=0.7, min_total_pct=0.15, top_n=8,
                  src=None):
    """Per P&L leaf line, within the LONGEST USABLE same-basis segment (>= min_months) -- NOT
    necessarily the current segment. Per spec: drift needs a long enough same-basis run to see a
    sustained move; if the current (latest) segment is short (e.g. 2 months right after an
    acquisition), the engine falls back to the longest earlier same-basis segment (bounded at
    `li`, so it never looks into the future) rather than suppressing outright. Only suppressed if
    NO segment (current or historical) reaches min_months. Flags when >= min_same_sign_share of
    MoM deltas share sign AND cumulative change >= min_total_pct (of the segment's starting value)
    AND the total change >= min_abs. Catches gradual multi-month bleed that spike detection
    (median/MAD vs recent history) cannot see."""
    candidates = []
    for seg in seg_info["segments"]:
        usable = [i for i in seg if i <= li]
        if usable:
            candidates.append(usable)
    usable = max(candidates, key=len) if candidates else []
    seg_start_i = usable[0] if usable else None
    if len(usable) < min_months:
        longest = max((len(c) for c in candidates), default=0)
        return [], {
            "suppressed": True,
            "reason": (f"longest usable same-basis segment has only {longest} month(s); "
                       f"need >= {min_months} for drift detection"),
        }

    out = []
    for nl, series in model.sheets.get("Income Statement", {}).items():
        if _skip_line(nl, skip_norms):
            continue
        vals = [_num(v) for v in series]
        window_vals = [vals[i] for i in usable]
        if any(v is None for v in window_vals):
            continue
        start_v, end_v = window_vals[0], window_vals[-1]
        total_change = end_v - start_v
        if total_change == 0:
            continue
        net_sign = 1 if total_change > 0 else -1
        # Same-sign-share: robust to single-month noise by checking level consistency (does the
        # back half of the window actually sit on the declining/growing side of the front-half
        # baseline most of the time), not raw adjacent-month pairwise sign -- a services/R&D line
        # that "bleeds out" whipsaws month to month even while the multi-month trend is unambiguous
        # (this is precisely the sustained-move-vs-noise distinction the spec's drift section
        # targets; a naive adjacent-pair sign count is dominated by that noise and would suppress
        # genuine gradual declines).
        mid = len(window_vals) // 2
        first_half_avg = sum(window_vals[:mid]) / mid if mid else window_vals[0]
        second_half = window_vals[mid:]
        if not second_half:
            continue
        consistent = sum(1 for v in second_half
                          if (v > first_half_avg if net_sign > 0 else v < first_half_avg))
        same_sign_share = consistent / len(second_half)
        base = abs(start_v) if start_v not in (None, 0) else (abs(end_v) or 1.0)
        total_pct = total_change / base
        if abs(total_change) < min_abs:
            continue
        if same_sign_share < min_same_sign_share:
            continue
        if abs(total_pct) < min_total_pct:
            continue
        direction = "growing" if total_change > 0 else "declining"
        n_months = len(usable) - 1
        rec = {
            "line": orig.get(nl, nl),
            "segment_months": n_months,
            "direction": direction,
            "total_pct": _round(total_pct, 4),
            "avg_monthly_pct": _round(total_pct / n_months, 4) if n_months else None,
            "start_value": _round(start_v),
            "latest_value": _round(end_v),
            "same_sign_share": _round(same_sign_share, 3),
        }
        if src is not None:
            p = model.provenance(nl, sheet="Income Statement",
                                 month=months[usable[-1]], window=len(usable))
            if p:
                rec["source_ref"] = [src.ref(model.given_path, **p)]
                rec["calc"] = (
                    f"drift: {direction} {months[usable[0]]}..{months[usable[-1]]} "
                    f"({n_months} MoM steps): {start_v:,.6g} -> {end_v:,.6g} "
                    f"({total_pct:+.1%} cumulative; {same_sign_share:.0%} of back-half months on "
                    f"the {'high' if net_sign > 0 else 'low'} side of the front-half average; "
                    f"floor {min_abs:,.0f}) (IS row {p['row']})")
        out.append(rec)
    out.sort(key=lambda r: -abs(r["total_pct"] or 0))
    return out[:top_n], {"suppressed": False, "reason": None}


def build_revenue_mix(model, cfg, months, li, seg_info, orig, revenue_total_norm, src=None):
    """Every revenue leaf (a line under the Revenue header, above the revenue total) trended:
    level, share of total, MoM, same-basis YoY, direction. Single-revenue-line models: empty list
    (headline/mix reporting falls back to total only, per spec)."""
    mln = cfg.get("model_lines") or {}
    configured = mln.get("revenue_components")
    leaves = []
    if configured:
        for label in configured:
            hit = model.find_row(label, sheet="Income Statement")
            if hit:
                leaves.append((hit[0], [_num(v) for v in hit[1]]))
    else:
        # auto-detect: IS rows between the "Revenue" section header and the revenue total row,
        # excluding the total itself and any blank/header rows.
        is_rows = list(model.sheets.get("Income Statement", {}).items())
        # Need source order; monitor_lib stores dict insertion order == sheet row order (Python 3.7+).
        in_section = False
        for nl, series in is_rows:
            if nl == "revenue":
                in_section = True
                continue
            if not in_section:
                continue
            if nl == revenue_total_norm:
                break
            vals = [_num(v) for v in series]
            if all(v is None for v in vals):
                continue
            leaves.append((nl, vals))

    if len(leaves) < 2:
        return []

    rev_total = None
    for nl, series in model.sheets.get("Income Statement", {}).items():
        if nl == revenue_total_norm:
            rev_total = [_num(v) for v in series]
            break

    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_start = cur_seg[0]
    out = []
    for nl, vals in leaves:
        label = orig.get(nl, nl)
        latest = _at(vals, li)
        prior = _at(vals, li - 1)
        mom_pct = _pct((latest - prior) if (latest is not None and prior is not None) else None,
                       abs(prior) if prior not in (None, 0) else None)
        total_l = _at(rev_total, li) if rev_total else None
        share = _pct(latest, total_l)
        pj = _yoy_index(months, li)
        yoy_pct = None
        yoy_crosses = False
        if pj is not None and _at(vals, pj) not in (None, 0) and latest is not None:
            yoy_pct = _pct(latest - vals[pj], abs(vals[pj]))
            yoy_crosses = crosses_break(seg_info, li, pj)
        win_idxs = [i for i in cur_seg if seg_start < i <= li]
        deltas = [vals[i] - vals[i - 1] for i in win_idxs
                  if vals[i] is not None and vals[i - 1] is not None]
        direction = _median_direction(deltas, latest)
        rec = {
            "line": label, "latest": _round(latest), "share": share, "mom_pct": mom_pct,
            "yoy_pct": yoy_pct, "yoy_crosses_break": yoy_crosses, "direction": direction,
        }
        if src is not None:
            rrefs = []
            p = model.provenance(nl, sheet="Income Statement")  # full row: MoM/YoY/direction read it
            if p:
                rrefs.append(src.ref(model.given_path, **p))
            if rev_total is not None:
                pt = model.provenance(revenue_total_norm, sheet="Income Statement",
                                      month=months[li], window="spot")
                if pt:
                    rrefs.append(src.ref(model.given_path, **pt))
            if rrefs:
                rec["source_ref"] = rrefs
                rec["calc"] = (f"revenue leaf: latest at {months[li]}; share = leaf / total revenue "
                               f"at {months[li]}; MoM vs prior month; YoY vs same month prior year; "
                               f"direction = sign of median same-basis MoM delta")
        out.append(rec)
    out.sort(key=lambda r: -(r["latest"] or 0))
    return out


def build_opex_trends(opex_found_series, months, li, ytd, pytd, seg_info,
                      model=None, labels=None, src=None):
    """Per opex group: latest, MoM, same-basis YTD vs prior same-basis YTD, direction."""
    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_start = cur_seg[0]
    out = []
    for disp, vals in opex_found_series.items():
        latest = _at(vals, li)
        prior = _at(vals, li - 1)
        mom_pct = _pct((latest - prior) if (latest is not None and prior is not None) else None,
                       abs(prior) if prior not in (None, 0) else None)
        ytd_v = _sum_idx(vals, ytd)
        # same-basis prior YTD: only if the whole prior-YTD window is in the SAME segment as latest
        pytd_same_basis = None
        if pytd is not None and all(not crosses_break(seg_info, li, j) for j in pytd):
            pytd_same_basis = _sum_idx(vals, pytd)
        win_idxs = [i for i in cur_seg if seg_start < i <= li]
        deltas = [vals[i] - vals[i - 1] for i in win_idxs
                  if i < len(vals) and vals[i] is not None and vals[i - 1] is not None]
        direction = _median_direction(deltas, latest)
        rec = {
            "group": disp, "latest": _round(latest), "mom_pct": mom_pct,
            "ytd_same_basis": _round(ytd_v), "prior_ytd_same_basis": _round(pytd_same_basis),
            "direction": direction,
        }
        if src is not None and model is not None and labels and disp in labels:
            p = model.provenance(labels[disp], sheet="Income Statement")  # full row: YTD + prior YTD
            if p:
                rec["source_ref"] = [src.ref(model.given_path, **p)]
                rec["calc"] = (f"opex group row: latest at {months[li]}; MoM vs prior month; "
                               f"ytd_same_basis = sum of current fiscal-year months; "
                               f"prior_ytd_same_basis = same months prior year (only when fully "
                               f"same-basis); direction = sign of median same-basis MoM delta")
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# MoM Net Income bridge (v3: resolved subtotal chain, enforced tie invariant)
# ---------------------------------------------------------------------------
def resolve_pre_financing_series(model, cfg, gross_profit_series, opex_series, notfound):
    """Resolve the model's own pre-financing subtotal (the P&L row between operating expenses and
    "below the line" items -- e.g. an EBITDA-as-reported row, or a Net Ordinary Income style
    subtotal). NO hardcoded label. Order:
      1. model_lines.pre_financing_subtotal, if configured.
      2. Derived: gross_profit - sum(opex_groups) (mechanical, always available once those resolve).
    Returns (label_or_None, series_or_None, source) where source in {"model_line","derived",None}.
    """
    mln = cfg.get("model_lines") or {}
    label = mln.get("pre_financing_subtotal")
    if label:
        hit = model.find_row(label, sheet="Income Statement") or model.find_row(label, sheet=None)
        if hit:
            return hit[0], [_num(v) for v in hit[1]], "model_line"
        notfound.append("pre_financing_subtotal")
    if gross_profit_series is not None and opex_series is not None:
        n = max(len(gross_profit_series), len(opex_series))
        derived = []
        for i in range(n):
            gp = gross_profit_series[i] if i < len(gross_profit_series) else None
            op = opex_series[i] if i < len(opex_series) else None
            derived.append((gp - op) if (gp is not None and op is not None) else None)
        return None, derived, "derived"
    return None, None, None


def mom_bridge(model, cfg, months, li, comp_items, orig, gross_profit_series, opex_series,
               notfound, ebitda_skip_norms, unit, src=None, gp_label=None,
               opex_labels=None, config_file=None):
    if li < 1:
        return None, None

    def dd(s):
        return (s[li] - s[li - 1]) if (s and _at(s, li) is not None and _at(s, li - 1) is not None) else None

    mln = cfg.get("model_lines") or {}

    def ser(key, default=None):
        label = mln.get(key, default)
        if not label:
            return None, None
        r = model.find_row(label, sheet="Income Statement") or model.find_row(label, sheet=None)
        return (r[0], [_num(x) for x in r[1]]) if r else (None, None)

    rev_label, rev = ser("revenue")
    ni_label, ni = ser("net_income")
    pf_label, pf_series, pf_source = resolve_pre_financing_series(
        model, cfg, gross_profit_series, opex_series, notfound)

    d_ni, d_rev, d_gp, d_opex, d_pf = dd(ni), dd(rev), dd(gross_profit_series), dd(opex_series), dd(pf_series)

    # -- per-figure provenance: every bridge component refs the two
    # month cells its delta was read from; derived subtotals ref every row summed.
    def bref(label, window=2):
        if src is None or not label:
            return None
        p = model.provenance(label, sheet="Income Statement", month=months[li], window=window)
        return src.ref(model.given_path, **p) if p else None

    def _prov(rec, refs_, note):
        rl = [r for r in (refs_ or []) if r]
        if rl:
            rec["source_ref"] = rl
            rec["calc"] = note
        return rec

    mspan = f"{months[li - 1]}..{months[li]}"
    rev_ref, gp_ref, ni_ref = bref(rev_label), bref(gp_label), bref(ni_label)
    if pf_label:
        pf_refs = [r for r in [bref(pf_label)] if r]
    else:
        pf_refs = [r for r in [gp_ref] + [bref(l) for l in (opex_labels or {}).values()] if r]
    pf_desc = (f"pre-financing subtotal '{pf_label}'" if pf_label
               else "derived pre-financing subtotal (gross_profit - sum of opex groups)")

    comps = []
    if d_rev is not None:
        comps.append(_prov({"driver": "Revenue", "delta": _round(d_rev)},
                           [rev_ref], f"MoM revenue delta {mspan}"))
    if d_gp is not None and d_rev is not None:
        comps.append(_prov({"driver": "Cost of sales / gross margin", "delta": _round(d_gp - d_rev)},
                           [gp_ref, rev_ref],
                           f"MoM gross-profit delta minus MoM revenue delta ({mspan})"))
    if d_pf is not None and d_gp is not None and d_opex is not None:
        # pre-financing subtotal delta net of GP delta = operating-expense contribution
        comps.append(_prov({"driver": "Operating expenses", "delta": _round(d_pf - d_gp)},
                           pf_refs + [gp_ref],
                           f"{pf_desc} MoM delta minus MoM gross-profit delta ({mspan})"))
    if d_ni is not None and d_pf is not None:
        comps.append(_prov({"driver": "Below pre-financing subtotal (interest / financing / tax / D&A / other)",
                            "delta": _round(d_ni - d_pf)},
                           [ni_ref] + pf_refs,
                           f"MoM net-income delta minus {pf_desc} MoM delta ({mspan})"))

    # tie invariant, enforced in code
    status = "unresolved"
    tie_gap = None
    if d_ni is not None and comps:
        comp_sum = sum(c["delta"] for c in comps if c["delta"] is not None)
        tie_gap = _round(comp_sum - d_ni, 4)
        # $1, OUR constant, so it converts: on a model in thousands an
        # unconverted 1.0 lets a $1,000 hole in the bridge read as tied.
        if abs(comp_sum - d_ni) <= unit.from_dollars(1.0):
            status = "tied"

    movers = []
    for nl, series in model.sheets.get("Income Statement", {}).items():
        if _skip_line(nl, ebitda_skip_norms):
            continue
        vals = [_num(x) for x in series]
        if _at(vals, li) is not None and _at(vals, li - 1) is not None:
            delta = vals[li] - vals[li - 1]
            if abs(delta) >= unit.from_dollars(1000):
                movers.append((delta, nl))
    movers.sort(key=lambda t: -abs(t[0]))
    line_movers = [_prov({"line": orig.get(nl, nl), "delta": _round(delta)},
                         [bref(nl)], f"MoM delta {mspan}")
                   for delta, nl in movers[:6]]

    adj_l = adj_p = 0.0
    ot_items = []
    for c in comp_items:
        r = model.find_row(c["model_line"], sheet="Income Statement") or model.find_row(c["model_line"], sheet=None)
        if not r:
            continue
        vals = [_num(x) or 0.0 for x in r[1]]
        vl, vp = _at(vals, li) or 0.0, _at(vals, li - 1) or 0.0
        op = (c.get("operation") or "add").lower()
        a_l = vl if op == "add" else -vl
        a_p = vp if op == "add" else -vp
        adj_l += a_l
        adj_p += a_p
        irefs = [bref(r[0])]
        if src is not None and config_file and irefs[0]:
            irefs.append(src.ref(config_file, section="comparability_items"))
        ot_items.append(_prov(
            {"name": c.get("name", c["model_line"]), "latest": _round(vl), "prior": _round(vp),
             "ni_contribution_delta": _round(a_p - a_l)},
            irefs,
            f"one-time item ({op}) per config.comparability_items, read {mspan}; "
            f"ni_contribution_delta = prior-month adjustment - latest-month adjustment"))
    ni_l, ni_p = _at(ni, li), _at(ni, li - 1)
    normalized = None
    if ni_l is not None and ni_p is not None:
        normalized = _prov(
            {"latest_ni_ex": _round(ni_l + adj_l), "prior_ni_ex": _round(ni_p + adj_p),
             "delta_ni_ex": _round((ni_l + adj_l) - (ni_p + adj_p))},
            [ni_ref] + [rr for it in ot_items for rr in (it.get("source_ref") or [])],
            f"net income {mspan} adjusted for one-time items (config.comparability_items); "
            f"delta_ni_ex = latest_ni_ex - prior_ni_ex")

    exception = None
    if status == "unresolved":
        exception = {"flag": "bridge_unresolved", "severity": "elevated",
                     "detail": (f"MoM Net Income bridge components did not tie to the NI delta "
                                f"within $1 (gap={tie_gap}); pre-financing subtotal source="
                                f"{pf_source or 'unavailable'}. Bridge suppressed as fact.")}

    bridge = {
        "status": status,
        "pre_financing_subtotal": {"label": pf_label, "source": pf_source},
        "latest_month": months[li], "prior_month": months[li - 1],
        "net_income": _prov({"latest": _round(ni_l), "prior": _round(ni_p), "delta": _round(d_ni)},
                            [ni_ref], f"net-income row read at both months ({mspan})"),
        "components": comps, "line_movers": line_movers, "tie_gap": tie_gap,
        "one_time": {"items": ot_items,
                     "net_ni_delta": _round(sum(it["ni_contribution_delta"] for it in ot_items)) if ot_items else 0},
        "normalized": normalized,
    }
    _prov(bridge, [ni_ref, rev_ref, gp_ref] + pf_refs,
          f"MoM Net Income bridge {mspan}: Revenue + (dGP-dRev) + (dPF-dGP) + (dNI-dPF) telescopes "
          f"to the NI delta; tie enforced within $1 (gap={tie_gap}); pre-financing subtotal source: "
          f"{pf_source or 'unavailable'}")
    return bridge, exception


def build_headlines(tr_rev, tr_eb, gm_ytd, gm_ytd_ex, flagged_is, flagged_bs, drift,
                    bridge, liquidity, unit, liq_threshold=3.0):
    """Deterministic sentences. Trend framing is same-month YoY (MoM is noise for a seasonal
    borrower); the sequential prior-month move is used ONLY in the bridge / paradox line.
    Line-level headlines come from the v4 line read's rule flags, in plain business language."""
    H = []

    def yoy_txt(tr):
        if not tr or tr.get("yoy_abs") is None:
            return None
        pv, a, m = tr.get("yoy_prior_value"), tr["yoy_abs"], tr.get("yoy_prior_month", "a year ago")
        suffix = " (crosses a basis break — not a like-for-like comparison)" if tr.get("crosses_basis_break") else ""
        if isinstance(pv, (int, float)) and pv > 0 and tr.get("yoy_pct") is not None:
            d = "up" if tr["yoy_pct"] >= 0 else "down"
            return f"{d} {fmt_pct(abs(tr['yoy_pct']))} YoY (vs {fmt_usd(pv, unit)} in {m}){suffix}"
        d = "improved" if a >= 0 else "declined"
        return f"{d} {fmt_usd(abs(a), unit)} YoY (from {fmt_usd(pv, unit)} in {m}){suffix}"

    if tr_rev and tr_rev.get("latest") is not None:
        pk = tr_rev.get("peak") or {}
        yc = yoy_txt(tr_rev)
        lead = (f"Revenue of {fmt_usd(tr_rev['latest'], unit)} — {yc}" if yc
                else f"Revenue of {fmt_usd(tr_rev['latest'], unit)} ({tr_rev.get('direction') or 'n/a'} over the trailing window; prior-year month not in model)")
        pre = " (a more extreme pre-break value exists earlier in the series)" if pk.get("more_extreme_value_pre_break") else ""
        H.append(f"{lead}; segment peak {fmt_usd(pk.get('value'), unit)} in {pk.get('month','n/a')}{pre}.")

    if tr_eb and tr_eb.get("latest") is not None:
        tro = tr_eb.get("trough") or {}
        yc = yoy_txt(tr_eb)
        lead = (f"EBITDA of {fmt_usd(tr_eb['latest'], unit)} — {yc}" if yc else f"EBITDA of {fmt_usd(tr_eb['latest'], unit)}")
        pre = " (a more extreme pre-break value exists earlier in the series)" if tro.get("more_extreme_value_pre_break") else ""
        H.append(f"{lead}; trailing trend {tr_eb.get('direction') or 'n/a'} (segment trough {fmt_usd(tro.get('value'), unit)} in {tro.get('month','n/a')}{pre}).")

    if gm_ytd is not None and gm_ytd_ex is not None and abs(gm_ytd - gm_ytd_ex) > 0.02:
        H.append(f"YTD gross margin is {fmt_pct(gm_ytd)} as-reported but {fmt_pct(gm_ytd_ex)} excluding one-time items — the reported figure is flattered.")

    def flag_txt(r):
        fl = r.get("flags") or []
        if "frozen" in fl and r.get("frozen_months"):
            return (f"{r['line']} has sat at exactly {fmt_usd(r['latest'], unit)} for "
                    f"{r['frozen_months']} consecutive month-ends — not being remeasured.")
        if "monotonic_run" in fl and r.get("run"):
            return (f"{r['line']} has moved {r['run']['direction']} {r['run']['months']} months in a row "
                    f"({fmt_usd(r['run']['total'], unit)} cumulative) to {fmt_usd(r['latest'], unit)}.")
        if "sign_flip" in fl:
            return f"{r['line']} flipped sign this month: {fmt_usd(r['latest'], unit)} vs {fmt_usd(r['prior'], unit)} prior."
        if "share_shift" in fl:
            return (f"{r['line']} is now {fmt_pct(r.get('share'))} of the balance sheet vs "
                    f"{fmt_pct((r.get('share') or 0) - (r.get('share_shift_since_start') or 0))} at segment start.")
        if "new" in fl:
            return f"{r['line']} appears for the first time this month at {fmt_usd(r['latest'], unit)}."
        if "vanished" in fl:
            return f"{r['line']} went to zero this month (prior {fmt_usd(r['prior'], unit)})."
        return f"{r['line']} moved to {fmt_usd(r['latest'], unit)} from {fmt_usd(r['prior'], unit)} this month."

    top_is = flagged_is[0] if flagged_is else None
    top_drift = drift[0] if drift else None
    if top_is and top_drift:
        is_mag = abs(top_is.get("mom_abs") or top_is.get("latest") or 0)
        dr_mag = abs((top_drift.get("latest_value") or 0) - (top_drift.get("start_value") or 0))
        if is_mag >= dr_mag:
            H.append(flag_txt(top_is))
        else:
            H.append(f"{top_drift['line']} has been {top_drift['direction']} for {top_drift['segment_months']} months ({fmt_pct(top_drift['total_pct'])} cumulative) — a sustained drift, not a one-month spike.")
    elif top_is:
        H.append(flag_txt(top_is))
    elif top_drift:
        H.append(f"{top_drift['line']} has been {top_drift['direction']} for {top_drift['segment_months']} months ({fmt_pct(top_drift['total_pct'])} cumulative) — a sustained drift, not a one-month spike.")

    if flagged_bs:
        H.append(flag_txt(flagged_bs[0]))
        nxt = [r for r in flagged_bs[1:] if "frozen" in (r.get("flags") or []) or "monotonic_run" in (r.get("flags") or [])]
        if nxt:
            H.append(flag_txt(nxt[0]))

    if bridge and bridge.get("status") == "tied" and bridge["net_income"]["delta"] is not None and \
            tr_rev and tr_rev.get("mom_abs") is not None:
        d_ni, d_rev, ot = bridge["net_income"]["delta"], tr_rev["mom_abs"], bridge["one_time"]["net_ni_delta"]
        if (d_rev > 0) != (d_ni > 0):
            pm, lm = bridge.get("prior_month", "prior month"), bridge.get("latest_month", "latest month")
            msg = (f"Sequentially ({pm} to {lm}), revenue {word_dir(d_rev)} {fmt_usd(abs(d_rev), unit)} yet net income "
                   f"{word_dir(d_ni)} to {fmt_usd(bridge['net_income']['latest'], unit)}")
            if ot and abs(ot) >= unit.from_dollars(25000) and bridge.get("normalized"):
                msg += (f" — largely because {pm} carried ~{fmt_usd(abs(ot), unit)} of one-time items that did not recur; "
                        f"excluding them, net income moved {fmt_usd(bridge['normalized']['delta_ni_ex'], unit)} "
                        f"(from {fmt_usd(bridge['normalized']['prior_ni_ex'], unit)} to {fmt_usd(bridge['normalized']['latest_ni_ex'], unit)}).")
            else:
                mv = bridge["line_movers"][0] if bridge["line_movers"] else None
                msg += (f" — driven mainly by {mv['line']} ({fmt_usd(mv['delta'], unit)})." if mv else ".")
            H.append(msg)
    elif bridge and bridge.get("status") == "unresolved":
        H.append("The month-over-month Net Income bridge did not tie within tolerance and is withheld pending reconciliation (see exceptions).")

    if liquidity and liquidity.get("months_to_floor_1m") is not None and liquidity["months_to_floor_1m"] < liq_threshold:
        H.append(f"At the current 1-month burn rate, cash reaches the "
                 f"{fmt_usd(liquidity['cash_floor'], unit)} floor in "
                 f"~{liquidity['months_to_floor_1m']:.1f} months (cash {fmt_usd(liquidity['cash'], unit)}, "
                 f"1m burn {fmt_usd(liquidity['burn_1m'], unit)}).")

    return H


# ---------------------------------------------------------------------------
# Liquidity (v3)
# ---------------------------------------------------------------------------
def _period_end_iso(month_label):
    """Month-end ISO date for a period label, or None if it can't be parsed."""
    try:
        return ml.month_end_iso(month_label)
    except (ValueError, TypeError):
        return None


def resolve_cash_floor(cfg, covenant_spec, unit, test_date_iso=None):
    """The cash floor this borrower is held to, on the MODEL's unit.

    Both sources are numbers a PERSON wrote -- analysis.cash_floor in
    borrower-config, and a min-cash covenant's threshold in the spec -- so both
    are read in whatever unit that file declares, defaulting to the model's own
    (which is what every file written so far means; see
    monitor_lib.stated_unit). The cash it is compared against
    comes off the model. Mixed, months-to-floor went 7.0 -> 0.0 and the memo
    announced a liquidity emergency that visibly mixed units: "cash reaches the
    $750,000 floor in ~0.0 months (cash $1,100, 1m burn $50)".

    Resolution order: analysis.cash_floor (explicit) -> covenant-spec min-cash
    covenant -> None. For a stepped threshold_schedule, pick the step in effect on
    test_date_iso (the latest step whose effective <= that date); an explicit gap
    step (value: null) in effect means NO floor is in force that period. With no
    date given, fall back to the last scheduled step."""
    an = cfg.get("analysis") or {}
    if an.get("cash_floor") is not None:
        try:
            return ml.on_model_unit(float(an["cash_floor"]), unit,
                                    ml.stated_unit(an.get("threshold_unit"))), "config"
        except (TypeError, ValueError):
            pass
    if covenant_spec:
        for cov in (covenant_spec.get("covenants") or []):
            if not isinstance(cov, dict):
                continue
            prim = (cov.get("primitive") or "").lower()
            name = (cov.get("name") or "").lower()
            metric_ref = ((cov.get("metric") or {}).get("ref") or "").lower()
            cid = (cov.get("id") or "").lower()
            is_cash_test = prim == "min_level" and (
                "cash" in name or "liquidity" in name
                or "cash" in metric_ref or "liquidity" in metric_ref
                or "cash" in cid or "liquidity" in cid)
            if is_cash_test:
                thr = cov.get("threshold")
                if thr is None and isinstance(cov.get("threshold_schedule"), list):
                    steps = [st for st in cov["threshold_schedule"]
                             if isinstance(st, dict) and st.get("effective")]
                    if test_date_iso:
                        steps = [st for st in steps
                                 if str(st["effective"]) <= test_date_iso]
                    if steps:
                        # Step in effect = latest effective (bounded by the test
                        # date). A gap step (value: null) in effect means no
                        # floor this period -- never fall back to a prior step
                        # (mirrors covenant_compliance.resolve_threshold).
                        thr = sorted(steps, key=lambda st: str(st["effective"]))[-1].get("value")
                if thr is not None:
                    try:
                        return ml.on_model_unit(
                            float(thr), unit,
                            ml.stated_unit(cov.get("threshold_unit"),
                                           covenant_spec.get("threshold_unit"))
                        ), "covenant-spec"
                    except (TypeError, ValueError):
                        continue
    return None, "none"


def detect_ocf_distortions(ocf_series, financing_series, months, li, seg_info, unit,
                           window=3):
    """v4 rule-based (no dispersion statistics): (a) ANY material financing inflow in the
    trailing window is listed as a fact — it distorts burn averages whether or not it is
    statistically 'anomalous'; (b) an OCF sign anomaly is flagged when the latest sign differs
    from the MAJORITY sign of the prior same-basis months and the swing is material."""
    out = []
    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_start = cur_seg[0]
    check_idxs = [i for i in cur_seg if seg_start <= i <= li][-window:]
    for i in check_idxs:
        if financing_series is not None and _at(financing_series, i) is not None:
            fin_v = financing_series[i]
            if fin_v >= unit.from_dollars(100000):
                out.append({"month": months[i], "kind": "financing_inflow",
                            "detail": f"Financing cash inflow of {fmt_usd(fin_v, unit)} in {months[i]} — a capital event, not operating cash generation; exclude it from any burn average it feeds."})
    if ocf_series is not None and _at(ocf_series, li) is not None:
        prior_hist = [ocf_series[j] for j in cur_seg if seg_start <= j < li and ocf_series[j] is not None]
        if len(prior_hist) >= 2:
            ocf_v = ocf_series[li]
            pos = sum(1 for v in prior_hist if v > 0)
            maj_pos = pos >= (len(prior_hist) - pos)
            havg = sum(prior_hist) / len(prior_hist)
            if (ocf_v > 0) != maj_pos and abs(ocf_v - havg) >= max(
                    abs(havg), unit.from_dollars(50000)):
                out.append({"month": months[li], "kind": "ocf_sign_anomaly",
                            "detail": f"Operating cash flow {fmt_usd(ocf_v, unit)} flips sign vs the prior same-basis months (avg {fmt_usd(havg, unit)}) — confirm it is not a one-off working-capital swing before basing runway on it."})
    return out


def build_liquidity(S, months, li, seg_info, cfg, covenant_spec, ratios_suppressed,
                    unit, model=None, src=None, s_label=None, config_file=None,
                    covenant_spec_file=None):
    cash_l = _at(S.get("cash"), li)
    ncc = S.get("net_change_cash")
    ocf = S.get("operating_cf")
    fin = S.get("financing_cf")

    cur_seg = seg_info["segments"][seg_info["segment_of"][li]]
    seg_start = cur_seg[0]

    burn_1m = None
    if ncc is not None and _at(ncc, li) is not None:
        burn_1m = -ncc[li]  # positive burn_1m means cash declined
    win3 = [i for i in cur_seg if seg_start <= i <= li][-3:]
    burn_3m_avg = None
    if ncc is not None and len(win3) >= 1:
        vals = [ncc[i] for i in win3 if i < len(ncc) and ncc[i] is not None]
        if vals:
            burn_3m_avg = -(sum(vals) / len(vals))

    distortions = detect_ocf_distortions(ocf, fin, months, li, seg_info, unit)

    cash_floor, floor_source = resolve_cash_floor(  # on the model's unit
        cfg, covenant_spec, unit, _period_end_iso(months[li]))

    def months_to_floor(burn):
        if cash_floor is None or cash_l is None or burn is None or burn <= 0:
            return None
        headroom = cash_l - cash_floor
        if headroom <= 0:
            return 0.0
        return round(headroom / burn, 2)

    out = {
        "cash": _round(cash_l), "burn_1m": _round(burn_1m), "burn_3m_avg": _round(burn_3m_avg),
        "ocf_distortions": distortions,
        "cash_floor": _round(cash_floor) if cash_floor is not None else None,
        "floor_source": floor_source,
        "months_to_floor_1m": months_to_floor(burn_1m),
        "months_to_floor_3m": months_to_floor(burn_3m_avg),
    }
    # Section-level provenance: the cash row (spot), the net-change-in-cash
    # row over the burn window, the CF rows the distortion rules swept, and wherever
    # the cash floor came from.
    if src is not None and model is not None and s_label:
        lrefs, notes = [], []
        if s_label.get("cash"):
            p = model.provenance(s_label["cash"], sheet="Balance Sheet",
                                 month=months[li], window="spot")
            if p:
                lrefs.append(src.ref(model.given_path, **p))
                notes.append(f"cash = spot balance at {months[li]}")
        if ncc is not None and s_label.get("net_change_cash"):
            p = model.provenance(s_label["net_change_cash"], sheet="Cash Flow Statement",
                                 month=months[li], window=(li - win3[0] + 1) if win3 else 1)
            if p:
                lrefs.append(src.ref(model.given_path, **p))
                notes.append(f"burn_1m = -net_change_cash at {months[li]}"
                             + (f"; burn_3m_avg = -mean(net_change_cash "
                                f"{months[win3[0]]}..{months[li]})" if win3 else ""))
        if ocf is not None and s_label.get("operating_cf"):
            p = model.provenance(s_label["operating_cf"], sheet="Cash Flow Statement",
                                 month=months[li], window=li - seg_start + 1)
            if p:
                lrefs.append(src.ref(model.given_path, **p))
        if fin is not None and s_label.get("financing_cf"):
            p = model.provenance(s_label["financing_cf"], sheet="Cash Flow Statement",
                                 month=months[li], window=min(3, li + 1))
            if p:
                lrefs.append(src.ref(model.given_path, **p))
        if distortions:
            notes.append("ocf_distortions: rule-based read of the operating/financing CF rows "
                         "(financing inflows >= $100k in the trailing 3 months listed; OCF sign "
                         "vs the majority of prior same-basis months)")
        if cash_floor is not None:
            if floor_source == "covenant-spec":
                lrefs.append(src.ref(covenant_spec_file or "covenant-spec.json",
                                     section="min-cash covenant (min_level threshold)"))
            else:
                lrefs.append(src.ref(config_file or "borrower-config.json",
                                     section="analysis.cash_floor"))
            notes.append(f"cash_floor per {floor_source}; months_to_floor = (cash - floor) / burn")
        else:
            notes.append("no cash floor resolvable (config.analysis.cash_floor or a covenant-spec "
                         "min-cash covenant); months_to_floor omitted")
        if lrefs:
            out["source_ref"] = lrefs
            out["calc"] = "; ".join(notes)
    return out


# ---------------------------------------------------------------------------
# Context conditioning (v3, read-only)
# ---------------------------------------------------------------------------
def load_json_if_exists(path):
    if not path:
        return None
    try:
        resolved = ml.resolve_path(path)
        if not os.path.exists(resolved):
            return None
        import json
        with open(resolved, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def condition_context(cfg, deal_context, covenant_spec, unit, test_date_iso=None):
    """Read deal-context `static` and covenant-spec to decide WHAT to compute and WHICH thresholds
    apply. NEVER alters a computed figure. Every decision recorded for audit.
    test_date_iso selects the stepped cash-floor step in effect on the latest period."""
    applied = []
    an = cfg.get("analysis") or {}

    business_model = an.get("business_model")
    if business_model:
        applied.append({"decision": "business_model", "value": business_model, "source": "config.analysis.business_model"})
    elif deal_context:
        one_liner = ((deal_context.get("static") or {}).get("business_one_liner") or "").lower()
        if any(k in one_liner for k in ["saas", "subscription", "recurring", "software"]):
            business_model = "recurring_revenue"
            applied.append({"decision": "business_model", "value": business_model,
                            "source": "sniffed from deal-context.static.business_one_liner"})
        else:
            business_model = "unspecified"
            applied.append({"decision": "business_model", "value": business_model,
                            "source": "no config value and no recognizable signal in deal-context"})
    else:
        business_model = "unspecified"
        applied.append({"decision": "business_model", "value": business_model,
                        "source": "no config value and no deal-context available"})

    cash_floor, floor_source = resolve_cash_floor(cfg, covenant_spec, unit, test_date_iso)
    if cash_floor is not None:
        applied.append({"decision": "cash_floor", "value": cash_floor, "source": floor_source})

    basis_breaks = an.get("basis_breaks") or []
    if basis_breaks:
        applied.append({"decision": "basis_breaks", "value": basis_breaks, "source": "config.analysis.basis_breaks"})

    return {
        "deal_context_read": deal_context is not None,
        "covenant_spec_read": covenant_spec is not None,
        "business_model": business_model,
        "applied": applied,
    }


# ---------------------------------------------------------------------------
# Main compute
# ---------------------------------------------------------------------------
def scale_floor(rev_series, seg_idxs, li, unit):
    """Scale-aware materiality floor: max($10,000, 5bps of trailing-12 same-basis revenue,
    annualized when fewer than 12 months exist). Used ONLY when no explicit floor is configured
    (analysis.line_read.materiality_floor, else analysis.outlier_min_abs). Deterministic; the
    basis is disclosed in line_read.params.floor_basis so every flag list states its own bar.

    Returns the floor in the MODEL's unit, because that is what the flag rules
    compare line values against. Only the $10,000 minimum is a dollar figure —
    5bps of revenue is computed off model figures and is already on the model's
    unit. Unconverted, the minimum became 10,000 model units ($10m on a
    thousands model), 405x too high, and every flag list came back empty while
    disclosing a basis that read like dollars.
    """
    floor_min = unit.from_dollars(10000.0)
    if not rev_series:
        return floor_min, "no revenue series resolvable — $10,000 minimum"
    idxs = [i for i in seg_idxs if i <= li][-12:]
    vals = [rev_series[i] for i in idxs if i < len(rev_series) and rev_series[i] is not None]
    if not vals:
        return floor_min, "no revenue observations in segment — $10,000 minimum"
    annualized = sum(vals) / len(vals) * 12.0
    fl = max(floor_min, float(round(0.0005 * annualized, 6)))
    return fl, (f"max($10,000, 5bps of trailing-{len(vals)}m annualized revenue "
                f"${unit.to_dollars(annualized):,.0f}) = "
                f"${unit.to_dollars(fl):,.0f}")


def compute(model, cfg, unit, borrower_name=None, config_file=None):
    """`unit` is the borrower's ReportingUnit (monitor_lib.reporting_unit(cfg)):
    what one figure in this model stands for. Every dollar figure that meets a
    model figure -- the cash floor, the materiality floor, the tie tolerances --
    is put on that unit before the comparison, and everything written out for a
    person to read is put back into dollars.

    `config_file` is the AS-CONFIGURED borrower-config path string -- recorded in
    source refs for config-sourced figures (opex groups, comparability items, cash
    floor) so the downstream artifact can join store hyperlinks."""
    months = model.months
    if not months:
        # A model with no readable month axis: a proper SKIP record (period stays
        # null), not a half-shaped conclusion -- validate_output.py accepts
        # {"skipped": true, "reason": ...} with a null period.
        return ml.conclusion("three-statement-analysis", None, [],
                             [{"flag": "no month columns in model", "severity": "error"}],
                             engine=_ENGINE_VERSION,
                             borrower=borrower_name or cfg.get("borrower_name"),
                             skipped=True,
                             reason="no month columns detected in the model workbook")
    li = len(months) - 1
    latest, prior = months[li], (months[li - 1] if li > 0 else None)
    # The day basis for DSO/DPO/DIO comes from the shared lib, the one place it
    # is defined, so this module and the aging module report one metric on one
    # basis (they read the same function).
    days_latest = ml.days_in_month(latest)

    an = cfg.get("analysis") or {}
    fy_start = int(an.get("fiscal_year_start_month", 1))
    window = int(an.get("trend_window", 3))
    min_abs = None  # resolved after the revenue series is available (scale-aware default)
    th = {**{"gross_margin_floor": 0.20, "one_time_reliance_ppt": 0.05, "dpo_days_high": 60.0,
             "dio_days_high": 120.0, "current_ratio_low": 1.0, "revenue_drop_vs_trailing": 0.20,
             "liquidity_months_to_floor": 3.0},
          **(an.get("thresholds") or {})}
    seasonality = an.get("seasonality") or {}
    low_months = set(seasonality.get("low_months") or [])
    drift_cfg = {**{"min_months": 6, "min_same_sign_share": 0.7, "min_total_pct": 0.15},
                 **(an.get("drift") or {})}
    business_model = (an.get("business_model") or "").strip().lower()

    ytd = ytd_indices(months, li, fy_start)
    pytd = prior_ytd_indices(months, ytd)
    notfound = []
    orig = orig_label_map(model.wb)

    # ---- per-figure provenance plumbing (trace-to-file + calculation records) ----
    # One deduped source_files list for the whole output. SourceIndex says each
    # path as its shared folder, so a mount path the driving agent passed (e.g.
    # the borrower-config path itself) still records as somewhere another
    # reader can open.
    _mp = getattr(model, "given_path", None)
    src = ml.SourceIndex(source_map=ml.model_source_map(_mp), model_path=_mp)
    config_file = config_file or "borrower-config.json"
    ytd_span = f"{months[ytd[0]]}..{months[li]}" if len(ytd) > 1 else months[li]
    pytd_span = (f"{months[pytd[0]]}..{months[pytd[-1]]}" if pytd and len(pytd) > 1
                 else (months[pytd[0]] if pytd else None))

    def mref(label, sheet=None, month=None, window="spot"):
        """Workbook source ref for a resolved row label (file/sheet/row/cells/period)."""
        if not label:
            return None
        p = model.provenance(label, sheet=sheet or "Income Statement",
                             month=month, window=window)
        return src.ref(model.given_path, **p) if p else None

    def refs(*rs):
        rl = [r for r in rs if r]
        return rl or None

    def _span(*groups):
        """Trailing-window length (months, ending at the latest month) covering every
        month index a record read -- so a ref's cell range spans the actual reads."""
        idxs = [li]
        for g in groups:
            if g is None:
                continue
            if isinstance(g, (list, tuple)):
                idxs.extend(i for i in g if isinstance(i, int) and 0 <= i <= li)
            elif isinstance(g, int) and 0 <= g <= li:
                idxs.append(g)
        return li - min(idxs) + 1

    # ---- segmentation (v3) ----
    seg_info = build_segments(months, an.get("basis_breaks"))
    cur_seg_idxs = seg_info["segments"][seg_info["segment_of"][li]]
    months_in_segment = sum(1 for i in cur_seg_idxs if i <= li)

    # ---- context conditioning (v3, read-only) ----
    paths = cfg.get("paths") or {}
    deal_context = load_json_if_exists(paths.get("deal_context_path"))
    covenant_spec = load_json_if_exists(paths.get("covenant_spec_path"))
    context_conditioning = condition_context(
        cfg, deal_context, covenant_spec, unit, _period_end_iso(latest))

    ratios_suppressed = []

    S, S_label = {}, {}
    for key in ["revenue", "gross_profit", "operating_cogs", "ebitda_bridge", "net_income", "interest_expense",
                "cash", "accounts_receivable", "total_current_assets", "total_assets",
                "accounts_payable", "total_current_liabilities", "total_equity",
                "operating_cf", "investing_cf", "financing_cf", "net_change_cash"]:
        S_label[key], S[key] = resolve_series(model, cfg, key, notfound)

    def key_ref(key, month=None, window="spot"):
        """Source ref for a resolved series by its config key."""
        return mref(S_label.get(key), _sheet_for(key), month, window)

    # ---- materiality floor (v4.1): explicit config wins; else SCALE-AWARE from the borrower's
    # own revenue — never a universal constant. Precedence: analysis.line_read.materiality_floor
    # (pinned at monitor-setup) > analysis.outlier_min_abs (legacy explicit) > scale_floor().
    lr_cfg_raw = an.get("line_read") or {}
    if lr_cfg_raw.get("materiality_floor") is not None:
        # A pinned floor is a number the analyst wrote while looking at the
        # model, so it is on the model's unit, like the line values it is
        # compared against. The floor_basis states it in dollars so the bar can
        # be read without knowing the scale.
        min_abs = float(lr_cfg_raw["materiality_floor"])
        floor_basis = ("pinned: analysis.line_read.materiality_floor "
                       f"(${unit.to_dollars(min_abs):,.0f})")
    elif "outlier_min_abs" in an:
        min_abs = float(an["outlier_min_abs"])
        floor_basis = ("pinned: analysis.outlier_min_abs (legacy key) "
                       f"(${unit.to_dollars(min_abs):,.0f})")
    else:
        min_abs, floor_basis = scale_floor(S["revenue"], cur_seg_idxs, li, unit)
    line_read_cfg = {**{"materiality_floor": min_abs, "run_min_months": 3, "frozen_min_months": 3,
                        "big_move_pct": 0.25, "share_shift_ppt": 5.0},
                     **lr_cfg_raw}
    line_read_cfg["materiality_floor"] = min_abs
    line_read_cfg["floor_basis"] = floor_basis

    # inventory: only resolved (never defaulted) if business model warrants it OR the config
    # explicitly names an inventory line; otherwise the whole DIO/CCC suite is suppressed.
    mln_all = cfg.get("model_lines") or {}
    al_all = cfg.get("analysis_lines") or {}
    inventory_configured = bool(mln_all.get("inventory") or al_all.get("inventory"))
    suppress_inventory_suite = (business_model == "recurring_revenue") and not inventory_configured
    if suppress_inventory_suite:
        S["inventory"] = None
        S_label["inventory"] = None
        ratios_suppressed.append({
            "ratio": "dio_days",
            "reason": f"no inventory line configured and business_model='{business_model}' does not warrant an inventory suite"})
        ratios_suppressed.append({
            "ratio": "cash_conversion_cycle_days",
            "reason": "depends on dio_days, which is suppressed (no inventory line / non-inventory business model)"})
    else:
        S_label["inventory"], S["inventory"] = resolve_series(
            model, cfg, "inventory", notfound, allow_default=not (business_model == "recurring_revenue"))
        if S["inventory"] is None:
            ratios_suppressed.append({"ratio": "dio_days", "reason": "inventory line configured but not found in model"})
            ratios_suppressed.append({"ratio": "cash_conversion_cycle_days", "reason": "depends on dio_days, which is suppressed"})

    # total_debt: explicit config wins. Absent that, it is aggregated from the rows that
    # state debt (derive_funded_debt) -- never from a generic "long term liabilities" row.
    funded_debt_meta = None
    funded_debt_unresolved = None
    total_debt_label = mln_all.get("total_debt") or al_all.get("total_debt")
    if total_debt_label:
        hit = model.find_row(total_debt_label, sheet="Balance Sheet") or model.find_row(total_debt_label, sheet=None)
        S["total_debt"] = [_num(v) for v in hit[1]] if hit else None
        S_label["total_debt"] = hit[0] if hit else None
        if S["total_debt"] is None:
            notfound.append("total_debt")
            ratios_suppressed.append({"ratio": "total_debt_to_ttm_ebitda",
                                      "reason": f"configured total_debt line '{total_debt_label}' not found in model"})
    else:
        fd = derive_funded_debt(model, unit)
        if fd and any(v not in (None, 0) for v in fd["exlease"]):
            S["total_debt"] = fd["exlease"]
            S_label["total_debt"] = "auto-aggregated funded debt (ex-capital-leases)"
            funded_debt_meta = fd
        else:
            S["total_debt"] = None
            S_label["total_debt"] = None
            ratios_suppressed.append({"ratio": "total_debt_to_ttm_ebitda",
                                      "reason": "no total_debt configured and no debt-tagged lines found to auto-aggregate"})
            # Leverage and interest coverage are the two ratios a credit memo is
            # read for. Losing both to an unmapped debt row is a SETUP gap with a
            # one-line fix, so name it and name the rows it could be, instead of
            # leaving the memo quietly short of a leverage figure.
            funded_debt_unresolved = _debt_row_candidates(model)

    def debt_refs(month=None, window="spot"):
        """Source refs for the total_debt figure. When funded debt is AUTO-AGGREGATED,
        S_label['total_debt'] holds a synthetic description ("auto-aggregated funded
        debt (ex-capital-leases)"), not a real row label -- key_ref() cannot resolve a
        row that doesn't exist under that name, which left the aggregate total_debt
        figure (and the leverage ratios built on it) with numeric values and no
        source_ref. Point instead at the actual debt-tagged component rows that were
        summed. When total_debt is explicit config (a single named row), key_ref()
        already resolves it correctly."""
        if funded_debt_meta is not None:
            return [r for r in (mref(c, "Balance Sheet", month, window)
                                for c in funded_debt_meta["components"]) if r]
        single = key_ref("total_debt", month, window)
        return [single] if single else []

    opex_groups = dict(ml.iter_label_map(cfg.get("opex_groups")))
    opex_series = [0.0] * len(months)
    opex_found = {}
    opex_found_series = {}
    opex_labels = {}
    for disp, label in opex_groups.items():
        hit = model.find_row(label, sheet="Income Statement") or model.find_row(label, sheet=None)
        if hit:
            vals = [_num(v) or 0.0 for v in hit[1]]
            opex_labels[disp] = hit[0]
            opex_found[disp] = {
                "latest": _round(_at(vals, li)), "ytd": _round(_sum_idx(vals, ytd)),
                "source_ref": refs(mref(hit[0], "Income Statement", latest, _span(li - 1, ytd)),
                                   src.ref(config_file, section=f"opex_groups.{disp}")),
                "calc": f"opex group row: latest at {latest}; ytd = sum {ytd_span}"}
            opex_found_series[disp] = vals
            for i in range(len(months)):
                opex_series[i] += vals[i] if i < len(vals) else 0.0
        else:
            notfound.append(f"opex_group:{disp}")

    comp = [c for c in (cfg.get("comparability_items") or []) if isinstance(c, dict) and c.get("model_line")]

    def comp_adjust(target, idxs_or_idx, is_ytd):
        adj = 0.0
        for c in comp:
            if target not in [a.lower() for a in (c.get("applies_to") or [])]:
                continue
            hit = model.find_row(c["model_line"], sheet="Income Statement") or model.find_row(c["model_line"], sheet=None)
            if not hit:
                continue
            vals = [_num(v) or 0.0 for v in hit[1]]
            amt = _sum_idx(vals, idxs_or_idx) if is_ytd else _at(vals, idxs_or_idx)
            if amt is None:
                continue
            adj += (amt if (c.get("operation") or "add").lower() == "add" else -amt)
        return adj

    def rnd_kind(v, kind):
        """How many decimals a figure of this KIND keeps -- 'usd', 'ratio',
        'x', 'days'. Not the borrower's reporting unit (that is `unit`)."""
        if v is None:
            return None
        if kind in ("ratio", "x"):
            return round(v, 6)
        if kind == "days":
            return round(v, 1)
        return round(v, 2)

    results = []

    def add(section, metric, latest_v, prior_v=None, ytd_v=None, kind="usd", note=None,
            source_ref=None, calc=None):
        row = {"section": section, "metric": metric,
               "latest": rnd_kind(latest_v, kind), "unit": kind}
        if prior_v is not None or kind != "ratio":
            row["prior"] = rnd_kind(prior_v, kind)
        if latest_v is not None and prior_v not in (None, 0):
            row["mom_pct"] = _pct(latest_v - prior_v, abs(prior_v))
        if ytd_v is not None:
            row["ytd"] = rnd_kind(ytd_v, kind)
        if note:
            row["note"] = note
        if source_ref and calc:  # always paired -- validate_output.py requires both
            row["source_ref"] = source_ref
            row["calc"] = calc
        results.append(row)

    def comp_refs(target, window_n):
        """Refs for the one-time rows a comparability adjustment read, plus the
        config section that defined them."""
        out = []
        for c in comp:
            if target not in [a.lower() for a in (c.get("applies_to") or [])]:
                continue
            r = mref(c["model_line"], "Income Statement", latest, window_n)
            if r:
                out.append(r)
        if out:
            out.append(src.ref(config_file, section="comparability_items"))
        return out

    n_flow = _span(li - 1, ytd)          # cells an IS/CF record reads: prior..latest + YTD window
    n_rev = _span(li - 1, ytd, pytd)     # revenue also reads the prior-YTD window
    prior_txt = f"; prior = {prior}" if prior else ""

    rev_l, rev_ytd = _at(S["revenue"], li), _sum_idx(S["revenue"], ytd)
    add("income", "revenue", rev_l, _at(S["revenue"], li - 1), rev_ytd,
        source_ref=refs(key_ref("revenue", latest, n_rev)),
        calc=(f"latest = revenue row at {latest}{prior_txt}; ytd = sum {ytd_span}"
              + (f"; prior_ytd = sum {pytd_span}" if pytd is not None else "")))
    if pytd is not None:
        results[-1]["prior_ytd"] = _round(_sum_idx(S["revenue"], pytd))
        results[-1]["prior_ytd_same_basis"] = all(not crosses_break(seg_info, li, j) for j in pytd)
    gp_l, gp_ytd = _at(S["gross_profit"], li), _sum_idx(S["gross_profit"], ytd)
    add("income", "gross_profit", gp_l, _at(S["gross_profit"], li - 1), gp_ytd,
        source_ref=refs(key_ref("gross_profit", latest, n_flow)),
        calc=f"latest = gross-profit row at {latest}{prior_txt}; ytd = sum {ytd_span}")
    add("income", "gross_margin", _pct(gp_l, rev_l), kind="ratio", ytd_v=_pct(gp_ytd, rev_ytd),
        source_ref=refs(key_ref("gross_profit", latest, n_flow), key_ref("revenue", latest, n_flow)),
        calc=(f"gross_margin = gross_profit / revenue at {latest}; "
              f"ytd = sum(gross_profit {ytd_span}) / sum(revenue {ytd_span})"))
    gp_l_ex = (gp_l + comp_adjust("gross_profit", li, False)) if gp_l is not None else None
    gp_ytd_ex = (gp_ytd + comp_adjust("gross_profit", ytd, True)) if gp_ytd is not None else None
    gm_ytd = _pct(gp_ytd, rev_ytd)
    gm_ytd_ex = _pct(gp_ytd_ex, rev_ytd) if comp else gm_ytd
    if comp:
        add("income", "gross_margin_ex_onetime", _pct(gp_l_ex, rev_l), kind="ratio",
            ytd_v=_pct(gp_ytd_ex, rev_ytd),
            source_ref=refs(key_ref("gross_profit", latest, n_flow),
                            key_ref("revenue", latest, n_flow),
                            *comp_refs("gross_profit", n_flow)),
            calc=(f"gross margin with one-time items (config.comparability_items, applies_to "
                  f"gross_profit) excluded; latest at {latest}, ytd {ytd_span}"))
    eb_l, eb_ytd = _at(S["ebitda_bridge"], li), _sum_idx(S["ebitda_bridge"], ytd)
    add("income", "ebitda", eb_l, _at(S["ebitda_bridge"], li - 1), eb_ytd,
        source_ref=refs(key_ref("ebitda_bridge", latest, n_flow)),
        calc=(f"latest = the model's pinned EBITDA bridge row at {latest} (never recomputed)"
              f"{prior_txt}; ytd = sum {ytd_span}"))
    add("income", "ebitda_margin", _pct(eb_l, rev_l), kind="ratio", ytd_v=_pct(eb_ytd, rev_ytd),
        source_ref=refs(key_ref("ebitda_bridge", latest, n_flow), key_ref("revenue", latest, n_flow)),
        calc=f"ebitda_margin = EBITDA bridge row / revenue at {latest}; ytd over {ytd_span}")
    eb_ytd_ex = (eb_ytd + comp_adjust("ebitda", ytd, True)) if eb_ytd is not None else None
    if comp:
        add("income", "ebitda_ex_onetime", (eb_l + comp_adjust("ebitda", li, False)) if eb_l is not None else None,
            ytd_v=eb_ytd_ex,
            source_ref=refs(key_ref("ebitda_bridge", latest, n_flow), *comp_refs("ebitda", n_flow)),
            calc=(f"EBITDA bridge row with one-time items (config.comparability_items, applies_to "
                  f"ebitda) excluded; latest at {latest}, ytd {ytd_span}"))
    opex_l, opex_ytd = _at(opex_series, li), _sum_idx(opex_series, ytd)
    opex_row_refs = [mref(l, "Income Statement", latest, n_flow) for l in opex_labels.values()]
    add("income", "total_opex", opex_l, _at(opex_series, li - 1), opex_ytd,
        source_ref=refs(*opex_row_refs, src.ref(config_file, section="opex_groups")),
        calc=((f"sum of the {len(opex_labels)} configured opex group rows "
               f"[{', '.join(sorted(opex_labels))}] at {latest}; ytd = sum {ytd_span}")
              if opex_labels else "no opex group rows resolved; sum is 0 by construction"))
    add("income", "opex_to_revenue", _pct(opex_l, rev_l), kind="ratio", ytd_v=_pct(opex_ytd, rev_ytd),
        source_ref=refs(*opex_row_refs, key_ref("revenue", latest, n_flow),
                        src.ref(config_file, section="opex_groups")),
        calc=f"opex_to_revenue = total_opex / revenue at {latest}; ytd over {ytd_span}")
    add("income", "net_income", _at(S["net_income"], li), _at(S["net_income"], li - 1),
        _sum_idx(S["net_income"], ytd),
        source_ref=refs(key_ref("net_income", latest, n_flow)),
        calc=f"latest = net-income row at {latest}{prior_txt}; ytd = sum {ytd_span}")

    n_bs = 2 if li >= 1 else 1
    bs_calc = f"spot balance at {latest}" + (f"; prior = {prior}" if prior else "")
    for key in ["cash", "accounts_receivable", "total_current_assets", "total_assets",
                "accounts_payable", "total_current_liabilities", "total_equity"]:
        if S.get(key) is None:
            continue  # unresolved (e.g. no current/non-current split on this Balance Sheet) --
                      # omitted from results rather than emitted as a noisy null row; the line is
                      # already recorded in lines_not_found, and any dependent ratio is suppressed
                      # with a reason in ratios_suppressed.
        add("balance_sheet", key, _at(S[key], li), _at(S[key], li - 1),
            source_ref=refs(key_ref(key, latest, n_bs)), calc=bs_calc)
    if S.get("inventory") is not None:
        add("balance_sheet", "inventory", _at(S["inventory"], li), _at(S["inventory"], li - 1),
            source_ref=refs(key_ref("inventory", latest, n_bs)), calc=bs_calc)
    if S.get("total_debt") is not None:
        if funded_debt_meta is not None:
            debt_calc = (f"funded debt = sum of debt-tagged BS rows "
                         f"[{', '.join(funded_debt_meta['components'])}] (ex-capital-leases) "
                         f"at {latest}" + (f"; prior = {prior}" if prior else ""))
        else:
            debt_calc = bs_calc
        add("balance_sheet", "total_debt", _at(S["total_debt"], li), _at(S["total_debt"], li - 1),
            source_ref=refs(*debt_refs(latest, n_bs)), calc=debt_calc)
    ca_l, cl_l = _at(S["total_current_assets"], li), _at(S["total_current_liabilities"], li)
    if S.get("total_current_assets") is not None and S.get("total_current_liabilities") is not None:
        cacl_refs = refs(key_ref("total_current_assets", latest, "spot"),
                         key_ref("total_current_liabilities", latest, "spot"))
        add("balance_sheet", "current_ratio", _pct(ca_l, cl_l), kind="ratio",
            source_ref=cacl_refs,
            calc=f"current_ratio = total_current_assets / total_current_liabilities at {latest}")
        add("balance_sheet", "working_capital", (ca_l - cl_l) if (ca_l is not None and cl_l is not None) else None,
            source_ref=cacl_refs,
            calc=f"working_capital = total_current_assets - total_current_liabilities at {latest}")
    else:
        ratios_suppressed.append({"ratio": "current_ratio",
                                  "reason": "total_current_assets/total_current_liabilities not resolvable in this model (no current/non-current split on the Balance Sheet)"})
        ratios_suppressed.append({"ratio": "working_capital",
                                  "reason": "depends on total_current_assets/total_current_liabilities, which are not resolvable"})

    for key in ["operating_cf", "investing_cf", "financing_cf", "net_change_cash"]:
        add("cash_flow", key, _at(S[key], li), _at(S[key], li - 1), _sum_idx(S[key], ytd),
            source_ref=refs(key_ref(key, latest, n_flow)),
            calc=f"latest = CF row at {latest}{prior_txt}; ytd = sum {ytd_span}")
    ocf_l, icf_l = _at(S["operating_cf"], li), _at(S["investing_cf"], li)
    add("cash_flow", "free_cash_flow", (ocf_l + icf_l) if (ocf_l is not None and icf_l is not None) else None,
        ytd_v=(_sum_idx(S["operating_cf"], ytd) + _sum_idx(S["investing_cf"], ytd))
        if (S["operating_cf"] is not None and S["investing_cf"] is not None) else None,
        source_ref=refs(key_ref("operating_cf", latest, n_flow),
                        key_ref("investing_cf", latest, n_flow)),
        calc=f"free_cash_flow = operating_cf + investing_cf at {latest}; ytd = sums over {ytd_span}")

    ar_l, ap_l = _at(S["accounts_receivable"], li), _at(S["accounts_payable"], li)
    cogs_l = _at(S["operating_cogs"], li)
    dso = _safe_div(ar_l, rev_l); dso = _round(dso * days_latest, 1) if dso is not None else None
    dpo = _safe_div(ap_l, cogs_l); dpo = _round(dpo * days_latest, 1) if dpo is not None else None
    add("working_capital", "dso_days", dso, kind="days",
        source_ref=refs(key_ref("accounts_receivable", latest, "spot"),
                        key_ref("revenue", latest, "spot")),
        calc=f"dso_days = accounts_receivable / revenue x {days_latest} days, at {latest}")
    add("working_capital", "dpo_days", dpo, kind="days",
        source_ref=refs(key_ref("accounts_payable", latest, "spot"),
                        key_ref("operating_cogs", latest, "spot")),
        calc=f"dpo_days = accounts_payable / operating COGS x {days_latest} days, at {latest}")
    dio = ccc = None
    if S.get("inventory") is not None:
        inv_l = _at(S["inventory"], li)
        dio = _safe_div(inv_l, cogs_l); dio = _round(dio * days_latest, 1) if dio is not None else None
        ccc = _round(sum(x for x in [dso, dio, (-dpo if dpo is not None else None)] if x is not None), 1) \
            if None not in (dso, dio, dpo) else None
        add("working_capital", "dio_days", dio, kind="days",
            source_ref=refs(key_ref("inventory", latest, "spot"),
                            key_ref("operating_cogs", latest, "spot")),
            calc=f"dio_days = inventory / operating COGS x {days_latest} days, at {latest}")
        add("working_capital", "cash_conversion_cycle_days", ccc, kind="days",
            source_ref=refs(key_ref("accounts_receivable", latest, "spot"),
                            key_ref("revenue", latest, "spot"),
                            key_ref("inventory", latest, "spot"),
                            key_ref("accounts_payable", latest, "spot"),
                            key_ref("operating_cogs", latest, "spot")),
            calc=f"cash_conversion_cycle_days = dso_days + dio_days - dpo_days at {latest}")

    # ---- leverage/coverage: TTM within the CURRENT SEGMENT (v3) ----
    seg_len = len(cur_seg_idxs)
    ttm_ok = seg_len >= 12
    ttm_eb = ttm_int = None
    int_matched = None
    if ttm_ok:
        eb_window = [S["ebitda_bridge"][i] for i in cur_seg_idxs[-12:] if S["ebitda_bridge"] and i < len(S["ebitda_bridge"])]
        if len(eb_window) == 12:
            ttm_eb = sum(v for v in eb_window if v is not None)
        int_label = (cfg.get("model_lines") or {}).get("interest_expense")
        if int_label:
            hit = model.find_row(int_label, sheet="Income Statement") or model.find_row(int_label, sheet=None)
            if hit:
                int_matched = hit[0]
                int_vals = [_num(v) for v in hit[1]]
                int_window = [int_vals[i] for i in cur_seg_idxs[-12:] if i < len(int_vals)]
                if len(int_window) == 12:
                    ttm_int = sum(v for v in int_window if v is not None)
    debt_l = _at(S["total_debt"], li) if S.get("total_debt") is not None else None
    lev = _round(_safe_div(debt_l, ttm_eb), 2) if (ttm_eb and ttm_eb > 0 and debt_l is not None) else None
    cov = _round(_safe_div(ttm_eb, ttm_int), 2) if (ttm_eb and ttm_int and ttm_eb > 0) else None
    ttm_span_txt = f"{months[cur_seg_idxs[-12]]}..{latest}" if ttm_ok else None
    add("leverage", "ttm_ebitda", ttm_eb, note=None if ttm_ok else f"n/m ({seg_len} months in current same-basis segment; need >=12)",
        source_ref=(refs(key_ref("ebitda_bridge", latest, 12)) if ttm_eb is not None else None),
        calc=(f"sum of the EBITDA bridge row over the last 12 same-basis months {ttm_span_txt}"
              if ttm_eb is not None else None))
    if S.get("total_debt") is not None:
        add("leverage", "total_debt_to_ttm_ebitda", lev, kind="x",
            note=None if lev is not None else "n/m (TTM EBITDA <= 0, or <12 same-basis months)",
            source_ref=(refs(*debt_refs(latest, "spot"),
                             key_ref("ebitda_bridge", latest, 12)) if lev is not None else None),
            calc=(f"total_debt at {latest} / TTM EBITDA (sum {ttm_span_txt})"
                  if lev is not None else None))
        if lev is None:
            ratios_suppressed.append({"ratio": "total_debt_to_ttm_ebitda",
                                      "reason": f"n/m — TTM EBITDA <= 0 or fewer than 12 same-basis months in the current segment ({seg_len})"})
        ttm_gp = None
        if ttm_ok and S.get("gross_profit"):
            gp_window = [S["gross_profit"][i] for i in cur_seg_idxs[-12:] if i < len(S["gross_profit"])]
            if len(gp_window) == 12:
                ttm_gp = sum(v for v in gp_window if v is not None)
        dgp = _round(_safe_div(debt_l, ttm_gp), 2) if (ttm_gp and ttm_gp > 0 and debt_l is not None) else None
        add("leverage", "total_debt_to_ttm_gross_profit", dgp, kind="x",
            note=None if dgp is not None else "n/m (<12 same-basis months or TTM gross profit <= 0)",
            source_ref=(refs(*debt_refs(latest, "spot"), key_ref("gross_profit", latest, 12)) if dgp is not None else None),
            calc=(f"total_debt at {latest} / TTM gross profit (sum {ttm_span_txt})" if dgp is not None else None))
        if dgp is None:
            ratios_suppressed.append({"ratio": "total_debt_to_ttm_gross_profit",
                                      "reason": f"n/m — TTM gross profit <= 0 or fewer than 12 same-basis months ({seg_len})"})
    add("leverage", "ttm_ebitda_interest_coverage", cov, kind="x",
        note=None if cov is not None else "n/m (TTM EBITDA <= 0, <12 same-basis months, or interest_expense not configured)",
        source_ref=(refs(key_ref("ebitda_bridge", latest, 12),
                         mref(int_matched, "Income Statement", latest, 12)) if cov is not None else None),
        calc=(f"TTM EBITDA / TTM interest expense (both summed {ttm_span_txt})"
              if cov is not None else None))
    if cov is None:
        ratios_suppressed.append({"ratio": "ttm_ebitda_interest_coverage",
                                  "reason": "n/m — TTM EBITDA <= 0, fewer than 12 same-basis months, or interest_expense not configured"})

    # ---- analysis pack ----
    gm_series = None
    if S["revenue"] and S["gross_profit"]:
        gm_series = [_pct(S["gross_profit"][i], S["revenue"][i]) for i in range(len(months))]
    trend = {
        "revenue": trend_descriptor(S["revenue"], months, li, window, seg_info),
        "ebitda": trend_descriptor(S["ebitda_bridge"], months, li, window, seg_info),
        "net_income": trend_descriptor(S["net_income"], months, li, window, seg_info),
        "cash": trend_descriptor(S["cash"], months, li, window, seg_info),
        "gross_margin": trend_descriptor(gm_series, months, li, window, seg_info, ratio=True) if gm_series else None,
    }
    # Descriptor-level provenance: a trend reads the whole row (peak/trough scan the
    # full series), so the ref spans every month column of the underlying row(s).
    for tname, tkeys in {"revenue": ("revenue", None), "ebitda": ("ebitda_bridge", None),
                         "net_income": ("net_income", None), "cash": ("cash", None),
                         "gross_margin": ("gross_profit", "revenue")}.items():
        t = trend.get(tname)
        if not t:
            continue
        tr = refs(key_ref(tkeys[0]), key_ref(tkeys[1]) if tkeys[1] else None)
        if not tr:
            continue
        t["source_ref"] = tr
        t["calc"] = (("gross_margin = gross_profit / revenue per month; " if tname == "gross_margin" else "")
                     + f"latest at {latest}" + (f", prior at {prior}" if prior else "")
                     + f"; trailing-{window}m same-basis average; direction = sign of the median "
                       f"same-basis MoM delta; YoY vs the same month a year earlier when present; "
                       f"peak/trough over the current same-basis segment")

    ebitda_bridge_label = (cfg.get("model_lines") or {}).get("ebitda_bridge", "")
    ebitda_norm = ml.norm_label(ebitda_bridge_label) if ebitda_bridge_label else None
    ebitda_skip = {ebitda_norm} if ebitda_norm else set()
    # Memo/reference sections (e.g. a trailing "Note 1 -- MRR / Covenant MRR..." block) are
    # reconciliation detail, not leaf P&L accounts -- exclude every row in such a section (by
    # sheet-row-order membership, not just the header row itself) from standouts/drift/line_movers.
    ebitda_skip |= _mark_memo_section_rows(list(model.sheets.get("Income Statement", {}).items()))
    revenue_total_label = (cfg.get("model_lines") or {}).get("revenue", "")
    revenue_total_norm = ml.norm_label(revenue_total_label) if revenue_total_label else None

    is_read = read_lines(model, "Income Statement", months, li, seg_info, orig, line_read_cfg,
                         skip_norms=ebitda_skip, ytd_idxs=ytd, denom_series=S["revenue"],
                         src=src, denom_label=S_label.get("revenue"))
    bs_read = read_lines(model, "Balance Sheet", months, li, seg_info, orig, line_read_cfg,
                         denom_series=S["total_assets"], flag_share_shift=True,
                         src=src, denom_label=S_label.get("total_assets"))
    drift, drift_meta = detect_drift(
        model, months, li, orig, seg_info, min_abs, skip_norms=ebitda_skip,
        min_months=int(drift_cfg["min_months"]), min_same_sign_share=float(drift_cfg["min_same_sign_share"]),
        min_total_pct=float(drift_cfg["min_total_pct"]), src=src)
    revenue_mix = build_revenue_mix(model, cfg, months, li, seg_info, orig, revenue_total_norm,
                                    src=src) if revenue_total_norm else []
    opex_trends = build_opex_trends(opex_found_series, months, li, ytd, pytd, seg_info,
                                    model=model, labels=opex_labels, src=src)

    bridge_notfound = []
    bridge, bridge_exception = mom_bridge(model, cfg, months, li, comp, orig, S["gross_profit"],
                                          opex_series, bridge_notfound, ebitda_skip, unit,
                                          src=src, gp_label=S_label.get("gross_profit"),
                                          opex_labels=opex_labels, config_file=config_file)
    notfound.extend(bridge_notfound)

    liquidity = build_liquidity(S, months, li, seg_info, cfg, covenant_spec, ratios_suppressed,
                                unit, model=model, src=src, s_label=S_label,
                                config_file=config_file,
                                covenant_spec_file=paths.get("covenant_spec_path"))

    headlines = build_headlines(trend["revenue"], trend["ebitda"], gm_ytd, gm_ytd_ex,
                                is_read["flagged"], bs_read["flagged"], drift,
                                bridge, liquidity, unit,
                                liq_threshold=float(th.get("liquidity_months_to_floor", 3.0)))

    # ---- exception flags ----
    exceptions = []
    if bridge_exception:
        exceptions.append(bridge_exception)
    for _bad in seg_info.get("invalid", []):
        exceptions.append({
            "flag": "basis_break_unrecognized", "severity": "warning",
            "detail": ("One of the accounting-basis change points in the setup is missing "
                       "its month, so it was skipped and the period split it was meant to "
                       "mark was not applied. Please add a month (for example \"Apr 2026\") "
                       "to that entry.")})

    if funded_debt_unresolved is not None:
        candidates = (" The balance sheet's own candidate rows, largest first: "
                      + "; ".join(funded_debt_unresolved) + "."
                      if funded_debt_unresolved else
                      " No balance-sheet row looks like debt at all — check that the"
                      " model's balance sheet carries the borrower's debt lines.")
        exceptions.append({
            "flag": "funded_debt_unresolved", "severity": "elevated",
            "detail": ("Leverage and interest coverage are not reported this period: "
                       "no funded-debt line is set for this borrower and none of the "
                       "balance-sheet rows carries a debt label to add up." + candidates
                       + " Set model_lines.total_debt (or analysis_lines.total_debt) to "
                         "that row in borrower-config and both ratios return next run.")})

    def flag(cond, name, detail, severity="watch"):
        if cond:
            exceptions.append({"flag": name, "severity": severity, "detail": detail})

    flag(gm_ytd_ex is not None and gm_ytd_ex < th["gross_margin_floor"], "gross_margin_below_floor",
         f"YTD normalized gross margin {gm_ytd_ex:.1%} < {th['gross_margin_floor']:.0%}" if gm_ytd_ex is not None else "")
    if comp and gm_ytd is not None and gm_ytd_ex is not None:
        flag((gm_ytd - gm_ytd_ex) > th["one_time_reliance_ppt"], "one_time_item_reliance",
             f"YTD as-reported GM {gm_ytd:.1%} vs ex-one-time {gm_ytd_ex:.1%} — reported margin flattered by one-time items")
    flag(eb_ytd_ex is not None and eb_ytd_ex < 0, "ebitda_negative_normalized",
         f"YTD normalized EBITDA {eb_ytd_ex:,.0f} < 0" if eb_ytd_ex is not None else "")
    te_l = _at(S["total_equity"], li)
    flag(te_l is not None and te_l < 0, "book_equity_negative",
         f"Book equity {te_l:,.0f} < 0" if te_l is not None else "", severity="elevated")
    flag(dpo is not None and dpo > th["dpo_days_high"], "payables_stretch", f"DPO {dpo} days > {th['dpo_days_high']}")
    flag(dio is not None and dio > th["dio_days_high"], "inventory_heavy", f"DIO {dio} days > {th['dio_days_high']}")
    if S.get("total_current_assets") is not None and S.get("total_current_liabilities") is not None:
        cur = _pct(ca_l, cl_l)
        flag(cur is not None and cur < th["current_ratio_low"], "current_ratio_below_1",
             f"Current ratio {cur:.2f} < {th['current_ratio_low']}")
    lm = ml._parse_label(latest)[1]
    if li >= window and S["revenue"] and lm not in low_months:
        trailing_idxs = [i for i in cur_seg_idxs if i < li][-window:]
        t3 = [S["revenue"][i] for i in trailing_idxs if S["revenue"][i] is not None]
        avg3 = sum(t3) / len(t3) if t3 else None
        if avg3 and rev_l is not None:
            flag((avg3 - rev_l) / avg3 > th["revenue_drop_vs_trailing"], "revenue_below_trailing",
                 f"Latest revenue {rev_l:,.0f} is >{th['revenue_drop_vs_trailing']:.0%} below same-basis trailing avg {avg3:,.0f}")
    flag(liquidity.get("months_to_floor_1m") is not None and
         liquidity["months_to_floor_1m"] < th["liquidity_months_to_floor"], "liquidity_thin",
         f"Months to cash floor at 1m burn = {liquidity['months_to_floor_1m']} < {th['liquidity_months_to_floor']}",
         severity="elevated")

    def pairs(key):
        s = S.get(key)
        return [{"m": months[i], "v": _round(s[i])} for i in range(len(months))] if s else None

    _ebq, _ebq_exc = ebitda_addback_register(model, S, months, li, cur_seg_idxs)
    if _ebq_exc:
        exceptions.append(_ebq_exc)
    # ---- performance review: consolidated lenses (MoM / YoY / YTD-vs-PY-YTD / TTM-vs-PY-TTM) ----
    _pj = _yoy_index(months, li)
    def _ttm_pair(key):
        s = S.get(key)
        if not s:
            return None, None
        cur_idx = cur_seg_idxs[-12:] if len(cur_seg_idxs) >= 12 else None
        prev_idx = cur_seg_idxs[-24:-12] if len(cur_seg_idxs) >= 24 else None
        ttm = sum(v for v in (s[i] for i in cur_idx) if v is not None) if cur_idx else None
        pttm = sum(v for v in (s[i] for i in prev_idx) if v is not None) if prev_idx else None
        return ttm, pttm
    performance_review = {}
    for _k, _lab in [("revenue", "Revenue"), ("gross_profit", "Gross Profit"),
                     ("ebitda_bridge", "EBITDA"), ("net_income", "Net Income")]:
        s = S.get(_k)
        if not s:
            continue
        cur = _at(s, li); prev = _at(s, li - 1); py = _at(s, _pj) if _pj is not None else None
        ytd_v = _sum_idx(s, ytd); pytd_v = _sum_idx(s, pytd) if pytd else None
        ttm, pttm = _ttm_pair(_k)
        performance_review[_lab] = {
            "latest": _round(cur),
            "mom_pct": _pct(cur - prev, abs(prev)) if (cur is not None and prev) else None,
            "yoy_pct": _pct(cur - py, abs(py)) if (cur is not None and py) else None,
            "yoy_prior_month": (months[_pj] if _pj is not None else None),
            "ytd": _round(ytd_v), "prior_ytd": (_round(pytd_v) if pytd_v is not None else None),
            "ytd_yoy_pct": (_pct(ytd_v - pytd_v, abs(pytd_v)) if pytd_v else None),
            "ttm": (_round(ttm) if ttm is not None else None),
            "prior_ttm": (_round(pttm) if pttm is not None else None),
            "ttm_yoy_pct": (_pct(ttm - pttm, abs(pttm)) if (ttm is not None and pttm) else None),
            "ttm_note": (None if pttm is not None else
                         f"prior-year TTM n/m -- need 24 same-basis months (segment has {len(cur_seg_idxs)})")}
    # ---- balance-sheet YoY levels (same-month prior year) ----
    balance_sheet_yoy = {}
    if _pj is not None:
        for _k, _lab in [("cash", "Cash"), ("accounts_receivable", "Accounts Receivable"),
                         ("inventory", "Inventory"), ("accounts_payable", "Accounts Payable"),
                         ("total_current_assets", "Total Current Assets"),
                         ("total_current_liabilities", "Total Current Liabilities"),
                         ("total_equity", "Total Equity")]:
            s = S.get(_k)
            if not s:
                continue
            cur = _at(s, li); py = _at(s, _pj)
            if cur is None:
                continue
            balance_sheet_yoy[_lab] = {"latest": _round(cur),
                "prior_year": (_round(py) if py is not None else None),
                "yoy_pct": (_pct(cur - py, abs(py)) if py else None),
                "prior_month": months[_pj]}
    # ---- scale-aware relational / ratio-trend signals (additive; never fatal) ----
    _ts = {}
    if trend_signals is not None:
        try:
            _ts = trend_signals.build(model, S, months, li, cur_seg_idxs, cfg, orig, min_abs)
            exceptions.extend(_ts.get("exceptions") or [])
        except Exception as _e:  # pragma: no cover
            _ts = {"error": repr(_e)}

    return ml.conclusion(
        module="three-statement-analysis", period=latest, results=results, exceptions=exceptions,
        source_index=src, unit=unit,
        # Meta collections that carry memo-surfaceable figures: declared so
        # validate_output.py walks them with the same source_ref+calc gate as
        # `results` (this module's figures mostly live outside results).
        figure_collections=["opex_composition", "trend", "mom_bridge", "drift",
                            "revenue_mix", "opex_trends", "line_read", "liquidity"],
        engine=_ENGINE_VERSION, borrower=borrower_name or cfg.get("borrower_name"),
        latest_month=latest, prior_month=prior, ytd_months=[months[i] for i in ytd],
        prior_ytd_available=(pytd is not None), lines_not_found=sorted(set(notfound)),
        opex_composition=opex_found, headlines=headlines, trend=trend, mom_bridge=bridge,
        series={"revenue": pairs("revenue"),
                "gross_margin": ([{"m": months[i], "v": gm_series[i]} for i in range(len(months))] if gm_series else None),
                "ebitda": pairs("ebitda_bridge"), "cash": pairs("cash"), "total_equity": pairs("total_equity")},
        seasonality_low_months=sorted(low_months) if low_months else None,
        segments={"breaks": seg_info["breaks"],
                  "current_basis_start": months[cur_seg_idxs[0]] if cur_seg_idxs else None,
                  "months_in_segment": months_in_segment},
        drift=drift, drift_suppressed=drift_meta,
        revenue_mix=revenue_mix, opex_trends=opex_trends,
        ebitda_addback_register=_ebq,
        performance_review=(performance_review or None),
        balance_sheet_yoy=(balance_sheet_yoy or None),
        ratio_trends=_ts.get("ratio_trends"),
        working_capital_divergence=_ts.get("working_capital_divergence"),
        reclassifications=_ts.get("reclassifications"),
        emerging_trends=_ts.get("emerging_trends"),
        trend_signal_params=_ts.get("params"),
        line_read={"income_statement": is_read, "balance_sheet": bs_read},
        liquidity=liquidity, ratios_suppressed=ratios_suppressed,
        funded_debt=((funded_debt_meta and {"basis": "auto-aggregated funded debt (ex-capital-leases)",
                      "ex_leases_latest": _round(_at(funded_debt_meta["exlease"], li)),
                      "incl_leases_latest": _round(_at(funded_debt_meta["incl"], li)),
                      "components": funded_debt_meta["components"],
                      "lease_components": funded_debt_meta["lease_components"],
                      "rollup_parents_excluded": funded_debt_meta.get("rollup_parents_excluded")}) or None),
        context_conditioning=context_conditioning,
        methodology=("Company-prepared/unaudited monthly financials model (read via monitor_lib). EBITDA is the "
                     "model's single pinned bridge row. Normalized metrics strip config.comparability_items. Ratios "
                     "are spot on latest-month flows; leverage/coverage are TTM within the current same-basis segment "
                     "when >=12 such months exist. Standouts, drift, trend and the MoM bridge are objective/mechanical "
                     "and confined to same-basis segments; the line read covers EVERY IS/BS leaf with rule-based flags (no statistical gating); narrative interpretation is left to synthesis."),
    )


def main():
    ap = argparse.ArgumentParser(description="Three-statement analysis (spec/model-driven)")
    ap.add_argument("--borrower-config")
    ap.add_argument("--model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id",
                    help="the run this output belongs to, minted once by the "
                         "orchestrator for the whole run (or set CREDIT_MONITOR_RUN_ID)")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id   # run_stamp() reads it

    model_path, borrower, cfg = args.model, None, {}
    if args.borrower_config:
        cfg = ml.load_config(args.borrower_config)
        borrower = cfg.get("borrower_name")
        if not model_path:
            model_path = (cfg.get("paths") or {}).get("model_output_path")
    if not model_path:
        ap.error("could not resolve --model (supply it or a borrower-config with paths.model_output_path)")

    # Which unit this model's figures are in. Undeclared, this module skips with
    # a plain reason rather than reading a model in thousands as dollars.
    try:
        unit = ml.reporting_unit(cfg)
    except ml.ConfigError as exc:
        out = ml.skip_record("three-statement-analysis", exc, borrower=borrower)
        out_path = ml.write_output(out, args.out)
        print(f"Wrote {out_path}: skipped -- {exc}")
        return

    try:
        model = ml.open_model(model_path)
    except Exception as exc:
        # A model workbook that fails to open/parse (e.g. no month columns found on the expected
        # header row) must still produce a disclosed JSON conclusion, never an uncaught stack
        # trace -- consistent with "skip and flag, never crash" for every other unresolved input
        # in this module.
        out = ml.conclusion(
            module="three-statement-analysis", period=None, results=[],
            exceptions=[{"flag": "model_open_failed", "severity": "error",
                        "detail": f"Could not open/parse model workbook at '{model_path}': {exc}"}],
            engine=_ENGINE_VERSION, borrower=borrower or cfg.get("borrower_name"),
            skipped=True,
            reason=f"model workbook could not be opened/parsed: {exc}")
        out_path = ml.write_output(out, args.out)
        print(f"Wrote {out_path}: model_open_failed -- {exc}")
        return

    out = compute(model, cfg, unit, borrower_name=borrower,
                  config_file=args.borrower_config)

    out_path = ml.write_output(out, args.out)
    # The figures become rows beside the output, so a question that spans the
    # book can reach them without re-deriving this file's shape.
    rows_emit.write_rows(out, out_path, deal_name=borrower)
    lr = out.get('line_read') or {}
    n_is = len((lr.get('income_statement') or {}).get('flagged') or [])
    n_bs = len((lr.get('balance_sheet') or {}).get('flagged') or [])
    print(f"Wrote {out_path}: {len(out['results'])} metrics, {len(out['exceptions'])} flags, "
          f"{len(out.get('headlines', []))} headlines, {n_is} IS / {n_bs} BS flagged lines, "
          f"{len(out.get('drift', []))} drift findings")


if __name__ == "__main__":
    main()
