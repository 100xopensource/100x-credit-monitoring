"""validate_debt_service_spec.py — assert a debt-service-spec.json conforms to the contract.

Used by covenant-setup before a facility-terms spec is accepted, and by the
schedule generator (debt_service.py) before it computes anything. The spec is
what the contractual debt-service schedule — and through it the covenant
Fixed Charges — is generated from, so every refusal here is a figure that
would otherwise be silently wrong: a rate written as a percent instead of a
decimal is a schedule 100x off, a grid without a catch-all step is a month
with no rate, a payment convention the engine cannot model is interest
attributed to the wrong days.

Exit 0 + "OK" if valid; exit 1 + the list of problems otherwise.
Deterministic; stdlib only; no network.

Usage:  python validate_debt_service_spec.py <debt-service-spec.json>
"""
import json
import re
import sys

ISO_DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
DAY_COUNTS = {"actual/360", "actual/365"}
FEE_WHEN = {"close_and_payment_dates", "payment_dates", "maturity"}
FEE_TREATMENT = {"included", "excluded"}
ATTRIBUTIONS = {"accrued", "paid"}
# A credit-facility rate above 50% is almost certainly a percent written as a
# number (5.25 for 5.25%) — a silently 100x-wrong schedule, so it is refused.
RATE_MAX = 0.5


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_agreement_ref(ref, errs, w):
    """Same two shapes validate_spec.py accepts (kept in step by hand — this
    validator runs standalone): a prose citation string, or the located object
    {section, page, quote}. Required — the ref is what makes a contractual term
    checkable against the document."""
    if ref is None:
        errs.append(f"{w}: missing 'agreement_ref' — every contractual term "
                    "names where the agreement states it")
        return
    if isinstance(ref, str):
        if not ref.strip():
            errs.append(f"{w}: agreement_ref is empty")
        return
    if not isinstance(ref, dict):
        errs.append(f"{w}: agreement_ref must be a citation string or an object "
                    f"with section/page/quote (got {type(ref).__name__})")
        return
    if not str(ref.get("section") or "").strip():
        errs.append(f"{w}: agreement_ref needs 'section' — the clause number the "
                    "agreement itself uses")
    page = ref.get("page")
    if page is not None and not (isinstance(page, int) and not isinstance(page, bool)
                                 and page >= 1):
        errs.append(f"{w}: agreement_ref page must be a 1-based page number "
                    f"(got {page!r})")
    quote = ref.get("quote")
    if quote is not None and not str(quote).strip():
        errs.append(f"{w}: agreement_ref quote is present but empty — drop it or "
                    "quote the wording the term was read from")


def _check_date(v, errs, w, what="date"):
    if not (isinstance(v, str) and ISO_DATE.match(v)):
        errs.append(f"{w}: {what} must be an ISO YYYY-MM-DD date (got {v!r})")
        return None
    return v


def _check_rate(v, errs, w, allow_zero=True):
    if not _num(v):
        errs.append(f"{w}: rate must be a number (got {v!r})")
        return
    if v < 0 or (v == 0 and not allow_zero):
        errs.append(f"{w}: rate must be {'non-negative' if allow_zero else 'positive'} "
                    f"(got {v!r})")
    elif v >= RATE_MAX:
        errs.append(f"{w}: rate {v!r} reads as a percent written as a number — "
                    f"rates are decimals (5.25% is 0.0525), and a facility rate at "
                    f"or above {RATE_MAX:.0%} is refused rather than generating a "
                    f"schedule 100x off")


def _check_grid(grid, errs, w, allow_zero=True):
    """A grid is evaluated first-match: the first step whose 'above' is null or
    whose 'above' the observed metric strictly exceeds. So the steps must be
    strictly descending and end in a catch-all — otherwise some observed value
    selects no step and that month has no rate."""
    if not isinstance(grid, list) or not grid:
        errs.append(f"{w}: must be a non-empty list of steps "
                    f"[{{'above': <number|null>, 'rate': <decimal>, 'when': <text>}}]")
        return 0
    prev = None
    for i, step in enumerate(grid):
        sw = f"{w}[{i}]"
        if not isinstance(step, dict):
            errs.append(f"{sw}: must be an object with 'above' and 'rate'")
            continue
        _check_rate(step.get("rate"), errs, sw, allow_zero=allow_zero)
        above = step.get("above")
        if above is None:
            if i != len(grid) - 1:
                errs.append(f"{sw}: 'above': null is the catch-all and must be the "
                            f"LAST step — steps after it can never be selected")
        elif not _num(above):
            errs.append(f"{sw}: 'above' must be a number or null (got {above!r})")
        else:
            if prev is not None and above >= prev:
                errs.append(f"{sw}: 'above' values must be strictly descending "
                            f"({above!r} after {prev!r}) — first-match selection "
                            f"needs an unambiguous order")
            prev = above
    last = grid[-1] if isinstance(grid[-1], dict) else {}
    if last.get("above") is not None:
        errs.append(f"{w}: the last step must be the catch-all ('above': null) — "
                    f"without it a metric value at or below every threshold "
                    f"selects no step and that month has no rate")
    return len(grid)


def validate(spec):
    errs = []
    if not isinstance(spec, dict):
        return ["spec is not a JSON object"]

    if not str(spec.get("borrower") or "").strip():
        errs.append("missing 'borrower'")

    fac = spec.get("facility")
    maturity = None
    if not isinstance(fac, dict):
        errs.append("missing 'facility' block (name, maturity_date, agreement_ref)")
    else:
        maturity = _check_date(fac.get("maturity_date"), errs,
                               "facility", "maturity_date")
        _check_agreement_ref(fac.get("agreement_ref"), errs, "facility")

    # --- tranches & fundings -------------------------------------------------
    first_funding = None
    tranches = spec.get("tranches")
    if not isinstance(tranches, list) or not tranches:
        errs.append("missing 'tranches' (list with at least one tranche)")
        tranches = []
    for ti, tr in enumerate(tranches):
        w = f"tranches[{ti}]({(tr or {}).get('id', '?') if isinstance(tr, dict) else '?'})"
        if not isinstance(tr, dict):
            errs.append(f"{w}: must be an object with 'id' and 'fundings'")
            continue
        if not str(tr.get("id") or "").strip():
            errs.append(f"{w}: missing 'id'")
        fundings = tr.get("fundings")
        if not isinstance(fundings, list) or not fundings:
            errs.append(f"{w}: needs a non-empty 'fundings' list — a tranche with "
                        f"nothing funded yet still records its commitment under "
                        f"'undrawn_commitment', not an empty fundings list")
            continue
        for fi, f in enumerate(fundings):
            fw = f"{w}.fundings[{fi}]"
            if not isinstance(f, dict):
                errs.append(f"{fw}: must be an object with 'date' and 'amount'")
                continue
            d = _check_date(f.get("date"), errs, fw)
            if d and (first_funding is None or d < first_funding):
                first_funding = d
            if not (_num(f.get("amount")) and f["amount"] > 0):
                errs.append(f"{fw}: 'amount' must be a positive number in the "
                            f"agreement's dollars (got {f.get('amount')!r})")
            _check_agreement_ref(f.get("agreement_ref"), errs, fw)

    if first_funding and maturity and maturity <= first_funding:
        errs.append(f"facility.maturity_date {maturity} is not after the first "
                    f"funding {first_funding}")

    base = spec.get("amortization_base")
    if spec.get("amortization") is not None:
        if not (isinstance(base, dict)
                and str(base.get("definition_verbatim") or "").strip()):
            errs.append("missing 'amortization_base.definition_verbatim' — the "
                        "engine computes the base as aggregate original principal "
                        "funded before each payment date, and the agreement's own "
                        "wording is what makes that computation checkable")
        elif isinstance(base, dict):
            _check_agreement_ref(base.get("agreement_ref"), errs,
                                 "amortization_base")

    grid_steps = 0

    # --- cash interest -------------------------------------------------------
    ci = spec.get("cash_interest")
    has_index = False
    if not isinstance(ci, dict):
        errs.append("missing 'cash_interest' block")
    else:
        w = "cash_interest"
        has_index = ci.get("index") is not None
        if has_index and not str(ci.get("index") or "").strip():
            errs.append(f"{w}: 'index' must name the index (e.g. 'WSJ Prime') or "
                        f"be null for a fixed-rate facility")
        floor = ci.get("floor")
        if floor is not None:
            _check_rate(floor, errs, f"{w}.floor")
        # On a floating facility the margin may genuinely be zero (the index is
        # the rate); on a fixed-rate facility the one-step grid IS the rate and
        # zero would mean an interest-free loan — refused as a placeholder.
        grid_steps += max(0, _check_grid(ci.get("margin_grid"), errs,
                                         f"{w}.margin_grid",
                                         allow_zero=has_index) - 1)
        _check_date(ci.get("first_payment_date"), errs, w, "first_payment_date")
        if ci.get("payment_day") != 1:
            errs.append(f"{w}: payment_day must be 1 — the engine models payments "
                        f"on the first calendar day of the month; a different "
                        f"convention needs an engine extension, not a workaround "
                        f"(got {ci.get('payment_day')!r})")
        _check_agreement_ref(ci.get("agreement_ref"), errs, w)

    # --- PIK (optional) ------------------------------------------------------
    pik = spec.get("pik_interest")
    if pik is not None:
        w = "pik_interest"
        if not isinstance(pik, dict):
            errs.append(f"{w}: must be an object (or absent for a facility "
                        f"without PIK)")
        else:
            grid_steps += max(0, _check_grid(pik.get("rate_grid"), errs,
                                             f"{w}.rate_grid") - 1)
            if pik.get("capitalizes") != "monthly_first":
                errs.append(f"{w}: capitalizes must be 'monthly_first' (accrued "
                            f"PIK added to principal on the first calendar day of "
                            f"each month) — the one mechanic the engine models; "
                            f"anything else must fail here, not generate a "
                            f"schedule that compounds on the wrong days "
                            f"(got {pik.get('capitalizes')!r})")
            _check_agreement_ref(pik.get("agreement_ref"), errs, w)

    # --- amortization (optional) --------------------------------------------
    am = spec.get("amortization")
    if am is not None:
        w = "amortization"
        if not isinstance(am, dict):
            errs.append(f"{w}: must be an object (or absent for a bullet loan)")
        else:
            start = _check_date(am.get("start_date"), errs, w, "start_date")
            if start and first_funding and start < first_funding:
                errs.append(f"{w}: start_date {start} predates the first funding "
                            f"{first_funding}")
            if start and maturity and start > maturity:
                errs.append(f"{w}: start_date {start} is after maturity {maturity}")
            grid_steps += max(0, _check_grid(am.get("annual_pct_of_base_grid"),
                                             errs,
                                             f"{w}.annual_pct_of_base_grid") - 1)
            if am.get("payments_per_year") != 12:
                errs.append(f"{w}: payments_per_year must be 12 — the engine "
                            f"models monthly installments (got "
                            f"{am.get('payments_per_year')!r})")
            attr = am.get("fixed_charges_attribution")
            if attr not in ATTRIBUTIONS:
                errs.append(f"{w}: fixed_charges_attribution must be one of "
                            f"{sorted(ATTRIBUTIONS)} — 'accrued' counts a payment "
                            f"in the month it services (the 'paid or scheduled to "
                            f"be paid' read), 'paid' in its payment-date month "
                            f"(got {attr!r})")
            _check_agreement_ref(am.get("agreement_ref"), errs, w)

    # --- day count -----------------------------------------------------------
    dc = spec.get("day_count")
    if not isinstance(dc, dict):
        errs.append("missing 'day_count' block")
    else:
        w = "day_count"
        if dc.get("convention") not in DAY_COUNTS:
            errs.append(f"{w}: convention must be one of {sorted(DAY_COUNTS)} "
                        f"(got {dc.get('convention')!r}) — what the engine "
                        f"supports; a different convention must fail loudly here")
        if not isinstance(dc.get("count_both_endpoints"), bool):
            errs.append(f"{w}: count_both_endpoints must be true or false — "
                        f"whether the agreement counts BOTH the first and last "
                        f"day of a period decides every stub month's interest")
        _check_agreement_ref(dc.get("agreement_ref"), errs, w)

    # --- fees ----------------------------------------------------------------
    fees = spec.get("fees")
    if fees is not None:
        if not isinstance(fees, list):
            errs.append("'fees' must be a list")
            fees = []
        for i, fee in enumerate(fees):
            w = f"fees[{i}]({(fee or {}).get('name', '?') if isinstance(fee, dict) else '?'})"
            if not isinstance(fee, dict):
                errs.append(f"{w}: must be an object")
                continue
            if not str(fee.get("name") or "").strip():
                errs.append(f"{w}: missing 'name'")
            has_rate, has_amount = _num(fee.get("rate")), _num(fee.get("amount"))
            if has_rate == has_amount:
                errs.append(f"{w}: provide exactly one of 'rate' (of aggregate "
                            f"original principal) or 'amount' (flat per occurrence)")
            if has_rate:
                _check_rate(fee["rate"], errs, w)
            if fee.get("when") not in FEE_WHEN:
                errs.append(f"{w}: 'when' must be one of {sorted(FEE_WHEN)} "
                            f"(got {fee.get('when')!r})")
            if fee.get("fixed_charges_treatment") not in FEE_TREATMENT:
                errs.append(f"{w}: 'fixed_charges_treatment' must be 'included' or "
                            f"'excluded' — whether this fee counts in covenant "
                            f"Fixed Charges is a contractual reading, not a "
                            f"default (got {fee.get('fixed_charges_treatment')!r})")
            _check_agreement_ref(fee.get("agreement_ref"), errs, w)

    # --- observations ----------------------------------------------------------
    obs = spec.get("observations")
    obs = obs if isinstance(obs, dict) else {}
    if not isinstance(spec.get("observations"), dict):
        errs.append("missing 'observations' block (index_rates + grid_state)")

    rates = obs.get("index_rates")
    if has_index:
        if not isinstance(rates, list) or not rates:
            errs.append("observations.index_rates: a floating-rate facility needs "
                        "the index's pinned history — at least the rate in effect "
                        "at the first funding, with its source")
        else:
            earliest = None
            for i, r in enumerate(rates):
                w = f"observations.index_rates[{i}]"
                if not isinstance(r, dict):
                    errs.append(f"{w}: must be an object with date/rate/source")
                    continue
                d = _check_date(r.get("date"), errs, w)
                if d and (earliest is None or d < earliest):
                    earliest = d
                _check_rate(r.get("rate"), errs, w)
                if not str(r.get("source") or "").strip():
                    errs.append(f"{w}: missing 'source' — a pinned observation "
                                f"says where it was read")
            if earliest and first_funding and earliest > first_funding:
                errs.append(f"observations.index_rates: earliest observation "
                            f"{earliest} is after the first funding {first_funding} "
                            f"— the engine cannot price the first accrual period")

    gs = obs.get("grid_state")
    if grid_steps > 0:
        if not isinstance(gs, list) or not gs:
            errs.append("observations.grid_state: a spec whose grids step needs "
                        "the grid metric's observed history — at least the state "
                        "at the first funding (the closing state), with its source")
        else:
            earliest = None
            for i, g in enumerate(gs):
                w = f"observations.grid_state[{i}]"
                if not isinstance(g, dict):
                    errs.append(f"{w}: must be an object with effective/value/source")
                    continue
                d = _check_date(g.get("effective"), errs, w, "effective")
                if d and (earliest is None or d < earliest):
                    earliest = d
                if not _num(g.get("value")):
                    errs.append(f"{w}: 'value' must be the grid metric's observed "
                                f"number (got {g.get('value')!r})")
                if not str(g.get("source") or "").strip():
                    errs.append(f"{w}: missing 'source' — grid steps follow what "
                                f"was actually reported, so each observation names "
                                f"the certificate or record it came from")
            if earliest and first_funding and earliest > first_funding:
                errs.append(f"observations.grid_state: earliest observation "
                            f"{earliest} is after the first funding {first_funding} "
                            f"— months before it would have no band to select")
        gm = spec.get("grid_metric")
        if not (isinstance(gm, dict) and str(gm.get("name") or "").strip()):
            errs.append("missing 'grid_metric' — grids with more than one step "
                        "key on a covenant metric, and the spec names it once "
                        "(name + cert_ref + agreement_ref)")

    # --- covenant support rows -------------------------------------------------
    rows = spec.get("covenant_support_rows")
    if not isinstance(rows, dict):
        errs.append("missing 'covenant_support_rows' — the model row labels "
                    "build-monitoring-model writes from the generated schedule; "
                    "they must match the labels the covenant-spec binds")
    else:
        for key in ("cash_interest", "scheduled_principal"):
            v = rows.get(key)
            if not (isinstance(v, str) and v.strip() and not v.startswith("<")):
                errs.append(f"covenant_support_rows.{key}: must be the model row "
                            f"label the covenant-spec binds (got {v!r})")

    return errs


def main():
    if len(sys.argv) != 2:
        print("usage: python validate_debt_service_spec.py <debt-service-spec.json>")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    errs = validate(spec)
    if errs:
        print(f"INVALID — {len(errs)} problem(s):")
        for e in errs:
            print("  -", e)
        sys.exit(1)
    n_fund = sum(len(t.get("fundings", [])) for t in spec.get("tranches", []))
    print(f"OK — facility terms conform to the contract ({n_fund} funding(s), "
          f"maturity {spec['facility']['maturity_date']}).")
    sys.exit(0)


if __name__ == "__main__":
    main()
