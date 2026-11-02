"""validate_spec.py — assert a covenant-spec.json conforms to the standardized contract.

Used by covenant-setup before a spec is accepted, and usable as a guard by covenant-compliance.
Exit 0 + "OK" if valid; exit 1 + the list of problems otherwise. Deterministic; no network.

Usage:  python validate_spec.py <covenant-spec.json>
"""
import sys
import json
import re

PRIMS = {"min_level", "max_ratio", "min_coverage"}
WINDOWS = {"spot", "ttm", "t3m"}
BASES = {"flow", "spot"}
COMPUTE_METHODS = {"model_line", "ebitda_bridge", "formula"}
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
REFERENCE_TYPES = {"affirmative", "negative", "reporting"}
# Deliberately spelled out here rather than imported: this validator runs
# standalone on one file path with no plugin lib on its path. Kept in step with
# monitor_lib.THRESHOLD_UNITS, which is what the engines read.
THRESHOLD_UNITS = {"dollars", "model_units", "unitless"}
FREQUENCIES = {"monthly", "quarterly", "continuous", "continuous; reported monthly", "annual"}


def _check_input(x, errs, where):
    if not isinstance(x, dict):
        errs.append(f"{where}: must be an object with 'ref' or 'lines'")
        return
    if "ref" not in x and "lines" not in x:
        errs.append(f"{where}: needs 'ref' (a definition name) or 'lines'")
    if "lines" in x and "ref" not in x:
        b = x.get("basis")
        if b is not None and b not in BASES:
            errs.append(f"{where}: basis must be 'flow' or 'spot' (got {b!r})")


def _check_definitions(defs, errs):
    """Each definition needs exactly one compute method and an explicit basis.

    basis: 'flow' (period amount -- aggregates over the covenant window) or
    'spot' (point-in-time balance -- always read at the test month). This is
    what keeps a TTM coverage ratio from dividing twelve months of EBITDA by
    one month of interest. ebitda_bridge implies flow, so basis may be omitted
    there (but if present must be 'flow')."""
    for name, d in defs.items():
        w = f"definition[{name}]"
        if not isinstance(d, dict):
            errs.append(f"{w}: must be an object with 'compute' (+ 'basis')")
            continue
        _check_deemed_schedule(name, d, errs)
        comp = d.get("compute")
        if not isinstance(comp, dict):
            errs.append(f"{w}: missing 'compute' object")
            continue
        methods = [k for k in comp if k in COMPUTE_METHODS]
        if len(methods) != 1:
            errs.append(f"{w}.compute: needs exactly one of {sorted(COMPUTE_METHODS)} "
                        f"(got {methods or 'none'})")
            continue
        basis = d.get("basis")
        if methods[0] == "ebitda_bridge":
            if basis is not None and basis != "flow":
                errs.append(f"{w}: ebitda_bridge implies basis 'flow' (got {basis!r})")
        elif basis not in BASES:
            errs.append(f"{w}: missing/invalid 'basis' -- must be 'flow' (period amount, "
                        f"aggregated over the covenant window) or 'spot' (point-in-time "
                        f"balance) (got {basis!r})")


DEEMED_UNITS = {"dollars", "model_units"}
ISO_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _check_deemed_schedule(name, d, errs):
    """A `deemed_schedule` is the agreement's own per-month amounts for this
    definition, and the covenant is tested on them.

    So every part of it is checked here rather than discovered at run time: the
    engine reads these months INSTEAD of the model, including for months that
    predate the model entirely, and a schedule that is half-transcribed does not
    fail loudly — it quietly answers a covenant on a shorter window, or on
    figures a thousand times too large.

    Contiguity is required because a hole is indistinguishable from a month
    somebody skipped: the agreement's proviso lists a continuous run of months,
    and a missing one in the middle silently sends that month back to the model
    while its neighbours stay contractual.
    """
    w = f"definition[{name}].deemed_schedule"
    sched = d.get("deemed_schedule")
    if sched is None:
        return
    if not isinstance(sched, dict):
        errs.append(f"{w}: must be an object with 'months', 'stated_in' and "
                    f"'agreement_ref'")
        return
    unit = sched.get("stated_in")
    if unit not in DEEMED_UNITS:
        errs.append(f"{w}: 'stated_in' must be one of {sorted(DEEMED_UNITS)} "
                    f"(got {unit!r}) — 'dollars' for the agreement's own figures, "
                    f"which is what a deemed table states, or 'model_units' if they "
                    f"were converted by hand onto this model's scale")
    if not sched.get("agreement_ref"):
        errs.append(f"{w}: missing 'agreement_ref' — the clause that deems these "
                    f"months is what makes them govern over the model")
    _check_agreement_ref(sched.get("agreement_ref"), errs, w)

    months = sched.get("months")
    if not isinstance(months, dict) or not months:
        errs.append(f"{w}: needs a 'months' object mapping YYYY-MM to the amount "
                    f"the agreement deems for that month")
        return
    ordered = []
    for key, val in months.items():
        if not ISO_MONTH.match(str(key)):
            # The template ships this block with a '<YYYY-MM>' placeholder, so
            # the likeliest reason it is here is that the agreement deems
            # nothing and the skeleton was filled in around instead of removed.
            # Say that, rather than only naming the bad key.
            if "<" in str(key):
                errs.append(
                    f"{w}.months: '{key}' is the template's placeholder, not a "
                    f"month. Delete the whole 'deemed_schedule' block from "
                    f"definition[{name}] when the agreement deems no months — most "
                    f"agreements do not, and the block is only for one that fixes "
                    f"this term to stated per-month amounts.")
            else:
                errs.append(f"{w}.months: '{key}' is not an ISO YYYY-MM stamp")
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            errs.append(f"{w}.months['{key}']: must be the deemed amount as a "
                        f"number (got {val!r})")
            continue
        y, m = (int(x) for x in str(key).split("-"))
        ordered.append((y * 12 + m - 1, str(key)))
    ordered.sort()
    missing = []
    for (a, _ka), (b, kb) in zip(ordered, ordered[1:]):
        for gap in range(a + 1, b):
            missing.append(f"{gap // 12:04d}-{gap % 12 + 1:02d}")
        if len(missing) > 12:
            break
    if missing:
        errs.append(f"{w}.months: the deemed run has a hole at "
                    f"{', '.join(missing[:6])}"
                    f"{' ...' if len(missing) > 6 else ''} — transcribe every month "
                    f"the agreement's table lists, because a month left out is "
                    f"tested on the model while the months around it are not")


GROUP_RULES = {"any_pass"}


def _bad_rule(rule):
    return (f"rule must be one of {sorted(GROUP_RULES)} (got {rule!r}) -- the "
            f"engine cannot resolve a group under a rule it does not know, so the "
            f"group reports no verdict and each leg's miss reads as a breach")


def _check_groups(spec, covs, errs):
    """Either/or groups have to be resolvable, because an unresolvable one fails
    QUIETLY: a member id that does not exist leaves the group permanently
    undecidable, and a rule the engine does not know leaves it with no verdict, so
    each leg's miss reads as a covenant breach. Both look like a clean deal on the
    way past. The rule is checked on BOTH declaration shapes -- the top-level
    `either_or_groups` block and the per-covenant `either_or_rule` tag the engine
    reads when a group is declared by tag alone.

    A group is the covenant; its members are legs. Two legs minimum -- a group of
    one is a covenant that has been mislabelled, and the engine drops it.
    """
    financial = {c.get("id") for c in covs
                 if isinstance(c, dict) and c.get("primitive") and c.get("id")}
    declared = spec.get("either_or_groups")
    if declared is not None and not isinstance(declared, dict):
        errs.append("'either_or_groups' must be an object keyed by group id")
        declared = None

    members_by_gid = {}
    for gid, g in (declared or {}).items():
        w = f"either_or_groups.{gid}"
        if not isinstance(g, dict):
            errs.append(f"{w}: must be an object with 'members' and 'rule'")
            continue
        rule = g.get("rule") or "any_pass"
        if rule not in GROUP_RULES:
            errs.append(f"{w}: {_bad_rule(rule)}")
        ms = g.get("members")
        if not isinstance(ms, list) or not ms:
            errs.append(f"{w}: needs a 'members' list naming the covenants it tests")
            continue
        for m in ms:
            if m not in financial:
                errs.append(f"{w}: member '{m}' is not a financial covenant in this "
                            f"spec -- the group can never be resolved")
        members_by_gid.setdefault(gid, set()).update(m for m in ms if m in financial)

    # Membership can also come from per-covenant tags; either way the group needs
    # two legs to mean anything.
    for c in covs:
        if not isinstance(c, dict):
            continue
        gid = c.get("either_or_group")
        if gid and c.get("id") in financial:
            members_by_gid.setdefault(gid, set()).add(c["id"])
            rule = c.get("either_or_rule")
            if rule is not None and rule not in GROUP_RULES:
                errs.append(f"covenant({c['id']}).either_or_rule: {_bad_rule(rule)}")
        elif gid and c.get("primitive") is None:
            errs.append(f"covenant({c.get('id', '?')}): carries either_or_group "
                        f"'{gid}' but has no primitive, so it can never be a leg")

    for gid, ms in sorted(members_by_gid.items()):
        if len(ms) < 2:
            errs.append(f"either/or group '{gid}': resolves to {len(ms)} leg(s) "
                        f"({sorted(ms) or 'none'}) -- a group needs at least two, "
                        f"and one-leg groups are dropped by the engine")


def _check_agreement_ref(ref, errs, w):
    """`agreement_ref` says where the agreement states this covenant.

    Two shapes are valid. A plain string is the prose citation every existing
    spec holds ("LSA §6.8(a), as amended by Third Amendment §8") — a person can
    chase it, so it stays valid. An object is the located form the extractor
    should now write: `section` as the agreement's own clause number, plus the
    `page` it is on and a short `quote` of the wording the threshold was read
    from. The quote is what stays checkable when a document is re-paginated or
    superseded by an amendment.
    """
    if ref is None or isinstance(ref, str):
        return
    if not isinstance(ref, dict):
        errs.append(f"{w}: agreement_ref must be the agreement's citation — a "
                    f"string, or an object with section/page/quote "
                    f"(got {type(ref).__name__})")
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
                    "quote the wording the figure was read from")


def _check_accounting_basis(spec, errs):
    """The covenant section's preamble / accounting-basis clause.

    Many agreements bind the test to the borrower's OWN accounting rather than
    to GAAP in the abstract -- "in accordance with Borrower's historic
    accounting practices as reflected in the most recent financial statements
    delivered ... prior to the Closing Date", or "as calculated in the manner
    set out in the Model". The clause sits in the PREAMBLE above the individual
    covenants, so a per-covenant extraction walks past it and nobody notices:
    the definition's generic "extraordinary charges" limb is then resolved by
    whoever is reading, instead of by the document the parties pointed at.

    So the block is REQUIRED, and `stated: false` is a valid answer. What is
    not allowed is silence -- a spec with no block cannot be told apart from
    one where the question was never asked.
    """
    ab = spec.get("accounting_basis")
    if ab is None:
        errs.append(
            "missing 'accounting_basis' at the top of the spec. Read the covenant "
            "section's preamble and any clause tying the test to the borrower's own "
            "accounting practices or to a named model/financial statements. Record it "
            "verbatim with the document it points at, or set "
            '{\"stated\": false} to say the agreement carries no such clause.')
        return
    if not isinstance(ab, dict):
        errs.append("accounting_basis must be an object")
        return
    if not isinstance(ab.get("stated"), bool):
        errs.append("accounting_basis.stated must be true or false")
        return
    if not ab.get("stated"):
        return
    if not str(ab.get("clause_verbatim", "")).strip():
        errs.append("accounting_basis.stated is true but 'clause_verbatim' is empty "
                    "-- quote the clause the covenants are bound to")
    _check_agreement_ref(ab.get("agreement_ref"), errs, "accounting_basis")
    rf = ab.get("reference_financials")
    if not isinstance(rf, dict):
        errs.append("accounting_basis.reference_financials must be an object naming the "
                    "statements or model the clause points at")
        return
    if not str(rf.get("described_as", "")).strip():
        errs.append("accounting_basis.reference_financials.described_as is empty -- record "
                    "how the agreement describes the document, even when it names no file")
    if rf.get("resolved") and not str(rf.get("document", "")).strip():
        errs.append("accounting_basis.reference_financials is marked resolved but carries no "
                    "'document' -- name the file it was resolved to")


def _check_threshold_derivation(c, errs, w):
    """A derived threshold keeps the rule that produced it.

    threshold / threshold_schedule still govern the test. The derivation says
    what BASIS those numbers were calibrated on, which is the thing that
    decides how the ACTUAL must be measured: a level set at a percentage of an
    adjusted projection, tested against an unadjusted actual, publishes a
    breach on a compliant borrower.
    """
    td = c.get("threshold_derivation")
    if td is None:
        return
    if not isinstance(td, dict):
        errs.append(f"{w}: threshold_derivation must be an object")
        return
    if not str(td.get("formula", "")).strip():
        errs.append(f"{w}: threshold_derivation carries no 'formula' -- state the rule "
                    f"the agreement gives, e.g. '70% of projected TTM EBITDA'")
    if not str(td.get("basis_document", "")).strip():
        errs.append(f"{w}: threshold_derivation carries no 'basis_document' -- name the "
                    f"projection, plan or model the levels were calibrated against, or say "
                    f"plainly that the agreement does not identify one")


def validate(spec):
    errs = []
    if not isinstance(spec, dict):
        return ["spec is not a JSON object"]
    defs = spec.get("definitions", {})
    if not isinstance(defs, dict) or not defs:
        errs.append("missing or empty 'definitions' block")
    else:
        _check_definitions(defs, errs)
    _check_accounting_basis(spec, errs)
    covs = spec.get("covenants")
    if not isinstance(covs, list) or not covs:
        return errs + ["missing or empty 'covenants' list"]

    # What the MONEY thresholds in this spec are written in. This is the
    # level the template ships and the extractor writes, and an undeclared or
    # misspelled value reads as "already on the model's unit" -- so a spec that
    # meant dollars is compared against a model in thousands and a compliant
    # borrower reports a breach. Silent is the one thing it must not be.
    stu = spec.get("threshold_unit")
    has_money_covenant = any(
        isinstance(c, dict) and c.get("primitive") == "min_level"
        and c.get("threshold_unit") is None for c in covs)
    if stu is not None and stu not in THRESHOLD_UNITS:
        errs.append(f"threshold_unit at the top of the spec must be one of "
                    f"{sorted(THRESHOLD_UNITS)} (got {stu!r})")
    elif stu is None and has_money_covenant:
        errs.append(
            "missing 'threshold_unit' at the top of the spec, and it has a "
            "min_level covenant that does not declare its own. Say which unit "
            "the money thresholds are written in: 'dollars' as the agreement "
            "states them (what covenant-setup asks for, and what the template "
            "ships), or 'model_units' if they were converted by hand onto this "
            "model's scale.")

    for i, c in enumerate(covs):
        w = f"covenant[{i}]({c.get('id', '?')})"
        # reference-only covenants (affirmative/negative/reporting) skip the financial checks
        if c.get("primitive") is None and c.get("type") in REFERENCE_TYPES:
            continue
        p = c.get("primitive")
        if p not in PRIMS:
            errs.append(f"{w}: primitive must be one of {sorted(PRIMS)} (got {p!r})")
            continue
        for f in ("id", "name", "agreement_ref", "frequency", "first_test_date"):
            if not c.get(f):
                errs.append(f"{w}: missing '{f}'")
        _check_agreement_ref(c.get("agreement_ref"), errs, w)
        if not ISO.search(str(c.get("first_test_date", ""))):
            errs.append(f"{w}: first_test_date needs an ISO date YYYY-MM-DD")
        if str(c.get("frequency", "")).strip().lower() not in FREQUENCIES:
            errs.append(f"{w}: frequency must be one of {sorted(FREQUENCIES)} "
                        f"(got {c.get('frequency')!r})")

        # inputs by primitive
        if p == "min_level":
            if "metric" not in c:
                errs.append(f"{w}: min_level needs 'metric'")
            else:
                _check_input(c["metric"], errs, f"{w}.metric")
        else:
            for side in ("numerator", "denominator"):
                if side not in c:
                    errs.append(f"{w}: {p} needs '{side}'")
                else:
                    _check_input(c[side], errs, f"{w}.{side}")

        # window -- custom_months is only valid INSIDE window ({"custom_months": N});
        # a covenant-level custom_months key is rejected because the engine ignores it
        # (it would silently compute as spot).
        win = c.get("window")
        ok_win = (isinstance(win, str) and win in WINDOWS) or (
            isinstance(win, dict) and isinstance(win.get("custom_months"), int)
            and win["custom_months"] > 0)
        if not ok_win:
            errs.append(f"{w}: window must be one of {sorted(WINDOWS)} or "
                        f"{{\"custom_months\": N}} (got {win!r})")
        if "custom_months" in c:
            errs.append(f"{w}: 'custom_months' must live inside 'window' "
                        f"({{\"custom_months\": N}}), not at the covenant level")

        # exactly one of threshold / threshold_schedule
        has_t = c.get("threshold") is not None
        sched = c.get("threshold_schedule")
        has_s = isinstance(sched, list) and len(sched) > 0
        if has_t == has_s:
            errs.append(f"{w}: provide exactly one of 'threshold' or 'threshold_schedule'")
        if has_s:
            for j, s in enumerate(sched):
                # value must be PRESENT: numeric, or an explicit null to mark a
                # not-tested gap period. A step that merely omits the key is a
                # typo, not a gap -- accepting it would silently un-test periods.
                val_ok = isinstance(s, dict) and "value" in s and (
                    isinstance(s["value"], (int, float)) or s["value"] is None)
                if not (isinstance(s, dict) and ISO.search(str(s.get("effective", ""))) and val_ok):
                    errs.append(f"{w}.threshold_schedule[{j}]: needs effective(YYYY-MM-DD) "
                                f"+ an explicit value (numeric, or null to mark a "
                                f"not-tested period)")

        # refs must resolve to a defined term
        for side in ("metric", "numerator", "denominator"):
            v = c.get(side)
            if isinstance(v, dict) and "ref" in v and v["ref"] not in defs:
                errs.append(f"{w}.{side}: ref '{v['ref']}' not found in definitions")

        # What this ONE covenant's threshold is stated in, overriding the
        # spec-level word above. A money threshold comes off the agreement in
        # dollars and is put on the model's unit before it is compared; a ratio is
        # unitless and must not be touched. Undeclared at both levels means the
        # model's own unit, so this is set to say a min_level is dollars, or that
        # it is not money at all — a percentage, a headcount, days.
        tu = c.get("threshold_unit")
        if tu is not None and tu not in THRESHOLD_UNITS:
            errs.append(f"{w}: threshold_unit must be one of "
                        f"{sorted(THRESHOLD_UNITS)} (got {tu!r}) — 'dollars' as the "
                        f"agreement states it, 'model_units' if it was already "
                        f"converted onto this model's scale, 'unitless' for a "
                        f"ratio, a percentage or a number of days")
        elif tu == "dollars" and p in ("max_ratio", "min_coverage"):
            errs.append(f"{w}: threshold_unit 'dollars' on a {p} — a ratio has no "
                        f"unit and is never scaled; drop it or say 'unitless'")

        _check_threshold_derivation(c, errs, w)

        ab = c.get("at_risk_band")
        if ab is not None and not (isinstance(ab, (int, float)) and 0 <= ab <= 1):
            errs.append(f"{w}: at_risk_band must be a fraction between 0 and 1")

    _check_groups(spec, covs, errs)
    return errs


def main():
    if len(sys.argv) != 2:
        print("usage: python validate_spec.py <covenant-spec.json>")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    errs = validate(spec)
    if errs:
        print(f"INVALID — {len(errs)} problem(s):")
        for e in errs:
            print("  -", e)
        sys.exit(1)
    fin = [c for c in spec["covenants"] if c.get("primitive") in PRIMS]
    print(f"OK — {len(fin)} financial covenant(s) conform to the contract.")
    sys.exit(0)


if __name__ == "__main__":
    main()
