#!/usr/bin/env python3
"""
Contractual debt-service schedule generator for the credit portfolio monitor.

GENERALIZED: computes ANY borrower's monthly facility debt service from a
validated debt-service-spec.json (config/debt-service-spec.template.json is the
shape; validate_debt_service_spec.py is the gate in front of this engine).
Nothing here is hardcoded to a borrower — every deal-specific fact (fundings,
rate grids, PIK mechanics, amortization, day count, fees, pinned observations)
lives in the spec.

WHY THIS EXISTS: covenant Fixed Charges mean CONTRACTUAL debt service — the facility's own cash interest and scheduled amortization —
not booked interest expense. A ledger's interest line sweeps in pre-close
borrowings and bridge payoffs across the whole trailing window, and no
statement line carries scheduled amortization at all, so a coverage covenant
computed off the ledger disagrees with the borrower's certificate in both
directions. This engine produces the schedule the model's covenant-support
rows are populated from, so the covenant engine's model side reconciles to the
contract.

MECHANICS (what the engine models — the validator refuses anything else):
  * Monthly accrual periods, day count actual/360 or actual/365, optionally
    counting BOTH the first and last day of each period (full months then
    count their calendar days; only the funding and maturity stubs differ).
  * Interest accrues from each funding's own date. Cash interest is paid in
    arrears on the next payment date (the 1st); it never compounds.
  * PIK accrues alongside and is added to principal on the first calendar day
    of the following month; the maturity stub's PIK capitalizes into the
    balloon.
  * Amortization: annual grid % of the base (aggregate original principal
    funded BEFORE the payment date) / 12, paid on the 1st from start_date;
    balloon at maturity for the remainder.
  * Rates follow the two pinned observation sets: the index's published
    history (floor applied; HELD FLAT beyond the last observation, disclosed
    as an assumption) and the grid metric's certified history (steps follow
    what was actually reported — never projected backward).

All arithmetic is decimal, quantized to the cent at each accrual — the
life-of-loan identity (total principal repaid = total funded + total
capitalized PIK, to the cent) is asserted before the output is written, and a
violation is an engine bug that must crash loudly.

Output: the standard module-conclusion JSON (monitor_lib.conclusion), one
result row per calendar month with per-figure `calc` notes and `source_ref`s
into the spec (upstream: the signed agreement), gated by validate_output.py.
Figures are the agreement's own DOLLARS (reporting_unit scale 1) —
build-monitoring-model converts onto the model's unit when it writes the
covenant-support rows.

Usage:
    python debt_service.py --spec <debt-service-spec.json> \
        [--borrower-config <borrower-config.json>] [--as-of YYYY-MM] \
        --out <debt-service-schedule.json>

Any argument may be omitted if derivable from the borrower-config:
    spec <- paths.debt_service_spec_path
    out  <- paths.debt_service_schedule_path
"""

import argparse
import calendar
import datetime
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

# --- Resolve the shared lib: this file lives at skills/covenant-setup/
# references/, the lib at lib/ — plugin root is three levels up. ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_LIB_DIR = os.path.join(_PLUGIN_ROOT, "lib")
for _p in (_LIB_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import monitor_lib as ml
import validate_debt_service_spec as vds

CENT = Decimal("0.01")
ENGINE = ("debt-service v1 (decimal cents; monthly accrual, both-endpoint day "
          "counts, PIK capitalizing monthly_first, grid steps from pinned "
          "observations; life-of-loan identity asserted)")


def money(x):
    """A dollars-and-cents amount. Quantized HALF_UP at every accrual so the
    schedule reproduces to the penny run after run — floats round differently
    depending on the order they were summed in."""
    return Decimal(str(x)).quantize(CENT, ROUND_HALF_UP)


def _date(iso):
    return datetime.date.fromisoformat(str(iso))


def _month_end(y, m):
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def _days(start, end, both_endpoints):
    """Days in the accrual period [start..end]. Counting both endpoints, a
    full calendar month counts its calendar days and a 6/1-6/17 stub counts
    17; the exclusive read gives 16 — which convention applies is the spec's
    day_count.count_both_endpoints, extracted from the agreement."""
    return (end - start).days + (1 if both_endpoints else 0)


def _iter_months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def grid_rate(grid, value):
    """First-match step selection: the first step whose 'above' is null or
    whose 'above' the observed value strictly exceeds. The validator has
    already enforced descending steps ending in a catch-all."""
    for step in grid:
        above = step.get("above")
        if above is None or value > above:
            return Decimal(str(step["rate"])), step.get("when")
    raise ValueError(f"grid has no step for value {value!r}")  # gate prevents this


class Observations:
    """The two pinned input sets, resolved per accrual date."""

    def __init__(self, spec):
        obs = spec.get("observations") or {}
        self.rates = sorted(((str(r["date"]), Decimal(str(r["rate"])))
                             for r in obs.get("index_rates") or []),
                            key=lambda t: t[0])
        self.grid = sorted(((str(g["effective"]), float(g["value"]))
                            for g in obs.get("grid_state") or []),
                           key=lambda t: t[0])
        self.last_rate_date = self.rates[-1][0] if self.rates else None

    def index_rate_at(self, d):
        """(rate, held_flat) — the latest published rate on or before `d`;
        held_flat says `d` is beyond the last pinned observation, so the rate
        is an assumption, not a fact."""
        iso = d.isoformat()
        applicable = [r for dt, r in self.rates if dt <= iso]
        if not applicable:
            raise ValueError(f"no index-rate observation on or before {iso}")
        return applicable[-1], iso > self.last_rate_date

    def band_at(self, d):
        """The grid metric's observed value in effect at `d` — the latest
        certified state on or before it, never a projection backward."""
        iso = d.isoformat()
        applicable = [v for dt, v in self.grid if dt <= iso]
        if not applicable:
            raise ValueError(f"no grid-state observation on or before {iso}")
        return applicable[-1]


def _fee_occurrences(spec, close, payment_dates, maturity, cum_funded_le):
    """{date: [(name, amount, treatment), ...]} for every fee occurrence.
    A rate fee applies to the aggregate original principal funded on or
    before its date, so a DDTL draw grows the recurring fee from the next
    payment date on."""
    out = {}

    def _amount(fee, d):
        if fee.get("amount") is not None:
            return money(fee["amount"])
        return money(Decimal(str(fee["rate"])) * cum_funded_le(d))

    for fee in spec.get("fees") or []:
        when = fee["when"]
        dates = []
        if when == "close_and_payment_dates":
            dates = [close] + payment_dates
        elif when == "payment_dates":
            dates = list(payment_dates)
        elif when == "maturity":
            dates = [maturity]
        for d in dates:
            out.setdefault(d, []).append(
                (fee["name"], _amount(fee, d), fee["fixed_charges_treatment"]))
    return out


def generate(spec, unit=None, spec_file=None, agreement_file=None, as_of=None,
             borrower_name=None):
    """The full life-of-loan schedule as a standard module conclusion.

    `spec_file` / `agreement_file` are the AS-CONFIGURED path strings recorded
    in source refs (the spec is what the engine read; the agreement is its
    upstream). `as_of` stamps the output's period; it defaults to the month of
    the latest pinned observation — the month through which the inputs are
    facts rather than held-flat assumptions.
    """
    errs = vds.validate(spec)
    if errs:
        raise ml.ConfigError(
            "debt-service-spec does not pass its gate — fix the spec and rerun "
            "(validate_debt_service_spec.py): " + "; ".join(errs[:5]))

    spec_file = spec_file or "debt-service-spec.json"
    obs = Observations(spec)
    both = bool(spec["day_count"]["count_both_endpoints"])
    denom = Decimal(360 if spec["day_count"]["convention"] == "actual/360" else 365)
    maturity = _date(spec["facility"]["maturity_date"])
    ci = spec["cash_interest"]
    floor = Decimal(str(ci.get("floor"))) if ci.get("floor") is not None else None
    fixed_rate = ci.get("index") is None
    pik = spec.get("pik_interest")
    am = spec.get("amortization")
    amort_start = _date(am["start_date"]) if am else None
    attribution = (am or {}).get("fixed_charges_attribution", "accrued")

    fundings = sorted(
        ((_date(f["date"]), money(f["amount"]))
         for tr in spec["tranches"] for f in tr["fundings"]),
        key=lambda t: t[0])
    close = fundings[0][0]
    total_funded = sum((a for _d2, a in fundings), Decimal(0))

    def cum_funded(d, inclusive):
        return sum((a for fd, a in fundings
                    if (fd <= d if inclusive else fd < d)), Decimal(0))

    first_pay = _date(ci["first_payment_date"])
    payment_dates = [datetime.date(y, m, 1) for y, m in _iter_months(first_pay, maturity)
                     if datetime.date(y, m, 1) >= first_pay
                     and datetime.date(y, m, 1) <= maturity]
    fees_by_date = _fee_occurrences(spec, close, payment_dates, maturity,
                                    lambda d: cum_funded(d, True))

    def cash_rate_at(d):
        """(rate, note, held_flat): index floored + margin, or the fixed grid."""
        band = obs.band_at(d) if _needs_band(spec) else None
        margin, when = grid_rate(ci["margin_grid"],
                                 band if band is not None else 0)
        if fixed_rate:
            return margin, f"fixed rate {margin:.4%}", False
        idx, held = obs.index_rate_at(d)
        base = max(idx, floor) if floor is not None else idx
        note = (f"{ci['index']} {idx:.4%}"
                + (f" floor-bound to {floor:.4%}" if floor is not None and floor > idx
                   else "")
                + f" + margin {margin:.4%}"
                + (f" [{when}]" if when else ""))
        return base + margin, note, held

    def pik_rate_at(d):
        if not pik:
            return Decimal(0), None
        band = obs.band_at(d) if _needs_band(spec) else None
        rate, when = grid_rate(pik["rate_grid"], band if band is not None else 0)
        return rate, when

    def amort_due(pay_date):
        """The installment due on `pay_date`, on the base the agreement
        defines: the aggregate original principal funded BEFORE that date."""
        if not am or pay_date < amort_start or pay_date > maturity:
            return Decimal(0)
        base = cum_funded(pay_date, False)
        rate, _when = grid_rate(am["annual_pct_of_base_grid"], obs.band_at(pay_date)
                                if _needs_band(spec) else 0)
        return money(base * rate / Decimal(am["payments_per_year"]))

    idx = ml.SourceIndex(
        upstream_map={spec_file: agreement_file} if agreement_file else None)

    results = []
    assumptions = []
    exceptions = []
    support = {}
    balance = Decimal(0)          # principal incl. capitalized PIK
    pik_prior = Decimal(0)        # last month's PIK, capitalizes on the 1st
    cash_prior = Decimal(0)       # last month's cash interest, paid on the 1st
    total_pik = total_cash = total_principal = total_fees = Decimal(0)
    balloon = None
    held_flat_from = None

    for y, m in _iter_months(close, maturity):
        month_start = datetime.date(y, m, 1)
        is_maturity_month = (y, m) == (maturity.year, maturity.month)
        period_end = maturity if is_maturity_month else _month_end(y, m)
        month_fundings = [(fd, a) for fd, a in fundings
                          if (fd.year, fd.month) == (y, m)]

        # --- first-of-month events: capitalize PIK, then pay what is due ---
        capitalized = Decimal(0)
        if (y, m) != (close.year, close.month):
            capitalized, pik_prior = pik_prior, Decimal(0)
            balance += capitalized
        pay_date = month_start if month_start in set(payment_dates) else None
        principal_paid = Decimal(0)
        if pay_date:
            principal_paid = min(amort_due(pay_date), balance)
            balance -= principal_paid
        day_fees = fees_by_date.get(pay_date or month_start, []) if pay_date \
            else fees_by_date.get(close, []) if (y, m) == (close.year, close.month) else []
        fees_paid = sum((a for _n, a, _t in day_fees), Decimal(0))
        interest_paid = cash_prior if pay_date else Decimal(0)
        cash_prior = cash_prior - interest_paid if pay_date else cash_prior

        # --- accrual: the carried balance from the month start, plus each
        # funding from its own date ---
        accrual_start = month_start if balance > 0 else (
            month_fundings[0][0] if month_fundings else month_start)
        rate_date = accrual_start
        cash_rate, rate_note, held = cash_rate_at(rate_date)
        pik_rate, pik_when = pik_rate_at(rate_date)
        if held and held_flat_from is None:
            held_flat_from = f"{y:04d}-{m:02d}"

        segments = []
        if balance > 0:
            segments.append((balance, month_start))
        for fd, amount in month_fundings:
            segments.append((amount, fd))
        cash_accrued = money(sum(
            (amt * cash_rate * _days(s, period_end, both) / denom
             for amt, s in segments), Decimal(0)))
        pik_accrued = money(sum(
            (amt * pik_rate * _days(s, period_end, both) / denom
             for amt, s in segments), Decimal(0))) if pik else Decimal(0)

        for _fd, amount in month_fundings:
            balance += amount
        month_end_balance = balance + pik_accrued
        days = _days(accrual_start, period_end, both)

        # what this month owes Fixed Charges: the installment that services it
        # (due on the NEXT payment date), or the one paid inside it
        next_pay = (datetime.date(y + 1, 1, 1) if m == 12
                    else datetime.date(y, m + 1, 1))
        accrued_installment = (amort_due(next_pay)
                               if next_pay in set(payment_dates) else Decimal(0))
        scheduled_accrued = accrued_installment
        scheduled_paid = principal_paid

        cash_paid_total = interest_paid + principal_paid + fees_paid
        if is_maturity_month:
            balloon = balance + pik_accrued   # the stub's PIK capitalizes into it
            scheduled_accrued += balloon if attribution == "accrued" else Decimal(0)
            scheduled_paid += balloon
            maturity_fees = sum((a for _n, a, _t in fees_by_date.get(maturity, [])),
                                Decimal(0))
            fees_paid += maturity_fees
            cash_paid_total += balloon + cash_accrued + maturity_fees
            balance = Decimal(0)

        total_pik += pik_accrued
        total_cash += cash_accrued
        total_principal += principal_paid
        total_fees += fees_paid
        pik_prior = pik_accrued if not is_maturity_month else Decimal(0)
        cash_prior = cash_prior + cash_accrued if not is_maturity_month else Decimal(0)

        stamp = f"{y:04d}-{m:02d}"
        calc = (f"cash interest {cash_accrued:,.2f} = "
                + " + ".join(f"{amt:,.2f} x {cash_rate:.4%} x "
                             f"{_days(s, period_end, both)}/{denom}"
                             for amt, s in segments)
                + f" ({accrual_start.isoformat()}..{period_end.isoformat()}, "
                + ("both endpoints counted" if both else "exclusive count")
                + f"; {rate_note}"
                + (" — rate held flat beyond the last pinned observation" if held
                   else "") + ")")
        if pik:
            calc += (f"; PIK {pik_accrued:,.2f} at {pik_rate:.4%}"
                     + (f" [{pik_when}]" if pik_when else "")
                     + ", capitalizes on the first calendar day of the next month")
        if capitalized:
            calc += f"; {capitalized:,.2f} prior-month PIK capitalized {stamp}-01"
        if pay_date:
            calc += (f"; paid {pay_date.isoformat()}: prior-month cash interest "
                     f"{interest_paid:,.2f} + principal {principal_paid:,.2f} + "
                     f"fees {sum((a for _n, a, _t in day_fees), Decimal(0)):,.2f}")
        if is_maturity_month:
            calc += (f"; balloon {balloon:,.2f} = remaining principal incl. the "
                     f"stub's accrued PIK, due {maturity.isoformat()} with the "
                     f"stub cash interest and maturity fees")

        refs = [idx.ref(spec_file, section="cash_interest",
                        upstream_at=ci.get("agreement_ref")),
                idx.ref(spec_file, section="observations")]
        if principal_paid or scheduled_accrued:
            refs.append(idx.ref(spec_file, section="amortization",
                                upstream_at=(am or {}).get("agreement_ref")))

        row = {
            "month": stamp,
            "accrual_start": accrual_start.isoformat(),
            "accrual_end": period_end.isoformat(),
            "days": days,
            "beginning_balance": float(balance - sum(a for _fd, a in month_fundings)
                                       + sum(a for fd, a in month_fundings
                                             if fd == accrual_start)) if not is_maturity_month
                                 else float(month_end_balance - pik_accrued),
            "fundings": float(sum(a for _fd, a in month_fundings)) or 0.0,
            "pik_capitalized": float(capitalized),
            "cash_rate": float(cash_rate),
            "pik_rate": float(pik_rate) if pik else None,
            "rate_basis": "held_flat" if held else "observed",
            "cash_interest_accrued": float(cash_accrued),
            "pik_accrued": float(pik_accrued) if pik else 0.0,
            "payment_date": pay_date.isoformat() if pay_date else None,
            "interest_paid": float(interest_paid),
            "principal_paid": float(principal_paid),
            "fees_paid": float(fees_paid),
            "cash_paid_total": float(cash_paid_total),
            "scheduled_principal_accrued": float(scheduled_accrued),
            "scheduled_principal_paid": float(scheduled_paid),
            "month_end_balance": float(month_end_balance if not is_maturity_month
                                       else balloon),
            "source_ref": refs,
            "calc": calc,
        }
        if is_maturity_month:
            row["balloon"] = float(balloon)
            row["balloon_date"] = maturity.isoformat()
        results.append(row)
        support[stamp] = {
            "cash_interest": float(cash_accrued),
            "scheduled_principal": float(scheduled_accrued if attribution == "accrued"
                                         else scheduled_paid),
        }

    # --- life-of-loan identity: every funded dollar and every capitalized PIK
    # dollar comes back as principal, to the cent. A violation is an engine
    # bug, never something to absorb. ---
    total_repaid = total_principal + balloon
    if total_repaid != total_funded + total_pik:
        raise AssertionError(
            f"life-of-loan identity broken: principal repaid {total_repaid} != "
            f"funded {total_funded} + capitalized PIK {total_pik} — engine bug")

    if held_flat_from:
        assumptions.append(
            f"{ci['index'] or 'the rate'} is held flat beyond the last pinned "
            f"observation ({obs.last_rate_date}) from {held_flat_from} onward"
            + (" — the floor binds at or above every pinned observation, so the "
               "cash rate is unchanged by this assumption"
               if floor is not None and all(r <= floor for _d3, r in obs.rates)
               else " — re-pin observations.index_rates and re-run when the "
                    "index moves"))
        exceptions.append({"type": "assumption", "warning": assumptions[-1]})
    for amb in (spec.get("day_count") or {}).get("ambiguities") or []:
        exceptions.append({"type": "ambiguity", "warning": str(amb)})

    totals = {
        "total_funded": float(total_funded),
        "total_pik_accrued": float(total_pik),
        "total_cash_interest_accrued": float(total_cash),
        "total_scheduled_principal_paid": float(total_principal),
        "balloon": float(balloon),
        "total_principal_repaid": float(total_repaid),
        "total_fees": float(total_fees),
        "identity": "total principal repaid = total funded + total capitalized PIK",
        "source_ref": [idx.ref(spec_file, section="tranches")],
        "calc": (f"life of loan: repaid {total_repaid:,.2f} = funded "
                 f"{total_funded:,.2f} + capitalized PIK {total_pik:,.2f} "
                 f"(asserted to the cent); cash interest {total_cash:,.2f}; "
                 f"fees {total_fees:,.2f}"),
    }

    srows = spec.get("covenant_support_rows") or {}
    covenant_support = {
        "attribution": attribution,
        "rows": dict(srows),
        "stated_in": "dollars",
        "monthly": support,
        "source_ref": [idx.ref(spec_file, section="covenant_support_rows",
                               upstream_at=(am or {}).get("agreement_ref")
                               or ci.get("agreement_ref"))],
        "calc": (f"per-month covenant-support values on the '{attribution}' "
                 f"attribution: cash_interest = the month's accrued facility "
                 f"cash interest; scheduled_principal = the installment that "
                 f"services the month"
                 + (" (due on the next payment date)" if attribution == "accrued"
                    else " (paid inside it)")
                 + ", plus the balloon in the maturity month. Dollars; "
                   "build-monitoring-model converts onto the model's unit."),
    }

    period = as_of or max([d for d, _r in obs.rates]
                          + [d for d, _v in obs.grid] or [close.isoformat()])
    return ml.conclusion(
        module="debt-service-schedule",
        period=period,
        results=results,
        exceptions=exceptions,
        source_index=idx,
        unit=unit or ml.ReportingUnit(1.0, "USD",
                                      "contractual dollars (agreement figures)"),
        engine=ENGINE,
        borrower=borrower_name or spec.get("borrower"),
        facility=spec.get("facility", {}).get("name"),
        maturity_date=spec["facility"]["maturity_date"],
        assumptions=assumptions,
        totals=totals,
        covenant_support=covenant_support,
        figure_collections=["totals", "covenant_support"],
        methodology=("Contractual schedule generated from the validated "
                     "debt-service-spec (terms per the executed agreement; "
                     "rates and grid steps per the spec's pinned observations, "
                     "index held flat beyond the last observation). Decimal "
                     "arithmetic quantized to the cent per accrual; the "
                     "life-of-loan identity is asserted before writing. No "
                     "figure here comes from a ledger."),
    )


def _needs_band(spec):
    """Whether any grid steps — the validator then guarantees grid_state."""
    grids = [spec["cash_interest"].get("margin_grid") or []]
    if spec.get("pik_interest"):
        grids.append(spec["pik_interest"].get("rate_grid") or [])
    if spec.get("amortization"):
        grids.append(spec["amortization"].get("annual_pct_of_base_grid") or [])
    return any(len(g) > 1 for g in grids)


def main():
    ap = argparse.ArgumentParser(
        description="Generate the contractual debt-service schedule")
    ap.add_argument("--spec")
    ap.add_argument("--borrower-config")
    ap.add_argument("--as-of", help="ISO YYYY-MM period stamp for the output; "
                                    "defaults to the latest pinned observation's "
                                    "month")
    ap.add_argument("--out")
    ap.add_argument("--run-id")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id

    spec_path, out_path = args.spec, args.out
    spec_given = args.spec
    agreement_file = None
    borrower = None
    if args.borrower_config:
        cfg = ml.load_config(args.borrower_config)
        borrower = cfg.get("borrower_name")
        paths = cfg.get("paths", {})
        spec_path = spec_path or paths.get("debt_service_spec_path")
        spec_given = spec_given or paths.get("debt_service_spec_path")
        out_path = out_path or paths.get("debt_service_schedule_path")
        agreement_file = paths.get("credit_agreement_path")
    if not spec_path or not out_path:
        ap.error("could not resolve --spec and --out (supply them, or a "
                 "borrower-config whose paths carry debt_service_spec_path and "
                 "debt_service_schedule_path)")

    spec = ml.load_config(spec_path)
    errs = vds.validate(spec)
    if errs:
        print(f"INVALID debt-service-spec — {len(errs)} problem(s); fix the spec "
              f"and rerun (this is the setup gate, not a run-time skip):")
        for e in errs:
            print("  -", e)
        sys.exit(1)

    out = generate(spec, spec_file=spec_given or spec_path,
                   agreement_file=agreement_file, as_of=args.as_of,
                   borrower_name=borrower)
    written = ml.write_output(out, out_path)
    t = out["totals"]
    print(f"Wrote {written}: {len(out['results'])} month(s), "
          f"funded {t['total_funded']:,.2f}, cash interest "
          f"{t['total_cash_interest_accrued']:,.2f}, balloon {t['balloon']:,.2f}; "
          f"life-of-loan identity holds to the cent.")


if __name__ == "__main__":
    main()
