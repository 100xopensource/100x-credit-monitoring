#!/usr/bin/env python3
"""
Spec-driven covenant-compliance module for the credit portfolio monitor.

GENERALIZED: computes ANY borrower's covenants from a standardized covenant-spec
(see config/covenant-spec.template.json). Nothing here is hardcoded to a particular
borrower or covenant. The three primitives the contract supports are:

    min_level     actual >= threshold                 (one quantity)
    max_ratio     numerator / denominator <= threshold
    min_coverage  numerator / denominator >= threshold

All MODEL READING, NUMBER PARSING, PERIOD MATH, and OUTPUT SHAPING come from the
shared library `monitor_lib` (lib/monitor_lib.py). This module keeps ONLY the
covenant-specific logic: the primitive comparators, threshold-schedule resolution
by test date, the at-risk band, and the certified-vs-model dual computation.

BASIS (flow vs spot) -- explicit, never guessed from compute shape:
    Every definition carries `basis: "flow" | "spot"` (validate_spec.py enforces
    it). A FLOW metric (revenue, EBITDA, interest expense -- period amounts)
    aggregates over the covenant's trailing window; a SPOT metric (cash, debt --
    point-in-time balances) is always read at the test month regardless of the
    window. Without an explicit basis the engine falls back to inferring from
    the compute shape (ebitda_bridge -> flow; model_line / formula -> spot) and
    records a warning -- inference cannot know that e.g. Interest Expense bound
    via model_line is a flow, which is exactly how a TTM coverage ratio would
    otherwise divide twelve months of EBITDA by ONE month of interest.

Window resolution for flow inputs:
    spot           -> the test month's column value
    t3m            -> trailing 3-month sum
    ttm            -> trailing 12-month sum
    {custom_months:N} -> trailing N-month sum

EBITDA is taken from the model's single pinned EBITDA bridge row (the one
definition) -- it is never recomputed here.

DEEMED MONTHS:
    An acquisition or LBO financing routinely fixes EBITDA for the pre-close
    year to a negotiated per-month schedule ("EBITDA shall be deemed to be the
    amount set forth below opposite such month"). A definition may carry that
    schedule as `deemed_schedule`, and those months GOVERN over anything the
    model computes for them -- they are a contractual fact, not a figure to
    reconstruct. Because most of them predate the model's first column, this is
    also what lets a TTM test reach back past the model and still answer.

PROVENANCE (per-figure trace-to-file + calculation records):
    Every result row carries `source_ref` (structured refs — model sheet/row/
    cells per input, window-aware; covenant-spec sections for definitions and
    the covenant itself; the certified-values entry when present — pointing
    into the output's deduped `source_files` list) and `calc` (a plain-text
    record of how the figure was arrived at: each side's rows, window, months,
    and the test applied). validate_output.py gates the output on both.

CERTIFIED vs MODEL (both sides computed):
    When a certified-values file is supplied (--certified; transcribed by the
    covenant subagent from the borrower's monthly compliance certificate -- see
    the module spec), every covenant test reports BOTH the borrower-certified
    actual and our model reconstruction, each with its own status and headroom,
    plus the delta checked against a tight, primitive-aware tolerance. A
    pass/fail disagreement between the two sides is raised as an exception.

Usage:
    python covenant_compliance.py \
        --borrower-config <borrower-config.json> \
        --spec <covenant-spec.standardized.json> \
        --model <model workbook .xlsx> \
        --certified <certified-values-YYYY-MM.json> \
        --out <output .json>

Any argument may be omitted if it can be derived from the borrower-config:
    spec   <- paths.covenant_spec_path  (or sibling covenant-spec.standardized.json)
    model  <- paths.model_output_path
"""

import argparse
from datetime import date
import json
import os
import re
import sys

# --- Resolve the shared lib robustly: walk up from this file to the plugin
# root, then lib/. The module lives at skills/monthly-monitor/references/ and
# the lib at lib/, so the plugin root is three levels up from this file. ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_LIB_DIR = os.path.join(_PLUGIN_ROOT, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import monitor_lib as ml
import rows_emit
import rows_lib as rl

EBITDA_BRIDGE_DEFAULT = "Provisional EBITDA (=sum above)"


# ----------------------------------------------------------------------------
# Definition / input resolution (uses the shared Model for all reads)
# ----------------------------------------------------------------------------
def _coverage_gap(model, label, series):
    """Warning text when a bound model row does not cover the model's months.

    A row that resolves is not the same as a row that answers. A model can rename
    "Cash" to "Cash and Cash Equivalents" mid-workbook while the spec still binds
    the old row, leaving later months empty.

    Names the empty span and any sibling row whose own filled months line up
    with the gap, which is what turns an unexplained blank into "this row was
    renamed". Returns None when the row covers every month.
    """
    blanks = [m for m, v in zip(model.month_ends, series) if v is None]
    if not blanks:
        return None
    blank_set = set(blanks)
    norm = model._norm(label)
    want = set(re.findall(r"[a-z0-9]+", norm))
    loc = model._locate(label)
    home_sheet = loc[0] if loc else None

    # Rank on rename EVIDENCE, not on coverage. Plenty of unrelated rows happen to
    # fill the same months: a blank span may be filled by "treasury stock at cost"
    # (same sheet, unrelated) and by "net cash increase
    # (decrease) for period" (shares the word cash, different sheet, and a flow
    # rather than a balance). Ordering by coverage or by token overlap alone names
    # one of those with confidence, which is worse than naming none. A renamed row
    # stays on its sheet and keeps the old name as a stem, so rank: same sheet,
    # then one label being a prefix of the other, then token overlap.
    candidates = []
    for sheet, rows in model.sheets.items():
        for other, vals in rows.items():
            if other == norm:
                continue
            filled = {m for m, v in zip(model.month_ends, vals)
                      if ml.safe_float(v) is not None}
            covered = len(blank_set & filled)
            if not covered:
                continue
            shared = want & set(re.findall(r"[a-z0-9]+", other))
            candidates.append((
                1 if sheet == home_sheet else 0,
                # EXTENDS, one direction only: 'Cash' -> 'Cash and Cash Equivalents'
                # is the rename this names. Accepting the reverse let a shorter
                # same-sheet stem be named as the row to repoint at, under a message
                # that claimed it extends the bound name.
                1 if other.startswith(norm) else 0,
                len(shared) / len(want) if want else 0.0,
                covered, other, sheet))
    candidates.sort(reverse=True)

    msg = (f"model line '{label}' is empty for {len(blanks)} of "
           f"{len(model.month_ends)} months ({blanks[0]}..{blanks[-1]}), so those "
           f"months have no value to test")
    exact = [c for c in candidates if c[3] == len(blank_set)]
    # Only claim a rename when the evidence is a same-sheet row whose name
    # extends this one. Anything weaker gets counted, not named.
    named = [c for c in exact if c[0] and c[1]]
    if named:
        other, sheet = named[0][4], named[0][5]
        msg += (f". Row '{other}' on {sheet} fills exactly those months and extends "
                f"this row's name -- it was likely renamed mid-workbook, and the "
                f"binding should point at it (or at both)")
    elif exact:
        msg += (f". {len(exact)} other row(s) fill exactly those months, but none is a "
                f"same-sheet row whose name extends this one, so which row this "
                f"binding should point at needs a person to decide")
    elif candidates:
        near = ", ".join(f"'{c[4]}' ({c[3]} of {len(blank_set)})" for c in candidates[:2])
        msg += f". Rows that partly cover the gap: {near}"
    return msg


def _definition_series(model, spec, def_name, warnings=None):
    """A named definition as the MODEL alone reads it -- (series, is_flow,
    source_str, lineage). resolve_definition_series wraps this to lay the
    agreement's deemed months over the result.

    lineage = {"def": def_name, "labels": [model row labels read]} — what the
    per-figure source refs are built from at each test date.

    is_flow distinguishes flow metrics (period amounts that aggregate over a
    trailing window) from spot metrics (point-in-time balances that are ALWAYS
    read at the test month, whatever the covenant window says).

    The definition's explicit `basis` field ("flow" | "spot") is authoritative.
    Without it, basis is INFERRED from the compute shape (ebitda_bridge -> flow;
    model_line / formula -> spot) and a warning is recorded via `warnings` --
    inference cannot know that e.g. Interest Expense bound via model_line is a
    flow. validate_spec.py requires `basis`, so warnings should only ever fire
    on a legacy spec.
    """
    defs = spec.get("definitions", {})
    if def_name not in defs:
        raise KeyError(f"definition '{def_name}' not found in spec")
    d = defs[def_name]
    comp = d.get("compute", {})
    explicit = d.get("basis")
    if explicit not in ("flow", "spot"):
        explicit = None

    def _basis(inferred):
        if explicit is not None:
            return explicit == "flow"
        if warnings is not None:
            warnings.append((
                "basis_inferred",
                f"definition '{def_name}' has no explicit basis; inferred "
                f"'{'flow' if inferred else 'spot'}' from its compute shape -- "
                'pin basis: "flow"|"spot" in the spec (validate_spec.py enforces it)'))
        return inferred

    if "ebitda_bridge" in comp:
        label = comp["ebitda_bridge"] or EBITDA_BRIDGE_DEFAULT
        series = model.get_line(label)
        if series is None:
            raise KeyError(f"EBITDA bridge row '{label}' not found in model")
        # get_line already returns float|None per month; an empty cell stays
        # None all the way to value_at, which decides what absence means.
        return (list(series), _basis(True),
                f"model:'{label}' (EBITDA bridge)",
                {"def": def_name, "labels": [label]})

    if "model_line" in comp:
        label = comp["model_line"]
        series = model.get_line(label)
        if series is None:
            raise KeyError(f"model line '{label}' not found in model")
        is_flow = _basis(False)
        # The double-window trap, caught loudly: a model row that is ITSELF
        # already a trailing aggregate (named TTM/LTM/trailing/T12) bound as
        # flow gets aggregated AGAIN over the covenant window. If the row is
        # pre-windowed, the correct binding is basis: "spot" (read the single
        # month's cell).
        if is_flow and warnings is not None and re.search(
                r"\b(ttm|ltm|t12|t3m|trailing)\b", str(label), re.I):
            # NOT a basis-inference fallback: this fires precisely when basis
            # IS explicit -- it gets its own machine tag so the memo can tell
            # a double-counting risk from a routine legacy-spec nudge.
            warnings.append((
                "double_window_risk",
                f"definition '{def_name}': model line '{label}' looks pre-windowed "
                "(TTM/LTM/trailing in the label) but is bound basis: \"flow\" -- a "
                "flow input is summed over the covenant window, which would window "
                "it twice. If the row is already a trailing aggregate, bind it "
                'basis: "spot".'))
        if warnings is not None:
            gap = _coverage_gap(model, label, series)
            if gap:
                warnings.append(("input_coverage_gap", gap))
        return (list(series), is_flow, f"model:'{label}'",
                {"def": def_name, "labels": [label]})

    if "formula" in comp:
        f = comp["formula"]
        op = f.get("op", "sum")
        series, used = _sum_lines(model, f["lines"], "formula")
        if op != "sum":
            raise ValueError(f"unsupported formula op '{op}'")
        return (series, _basis(False), f"model:sum({', '.join(used)})",
                {"def": def_name, "labels": list(used)})

    raise ValueError(f"definition '{def_name}' has no recognized compute method")


# ----------------------------------------------------------------------------
# Deemed months: the amounts the AGREEMENT stipulates
#
# An acquisition or LBO financing may fix EBITDA for a pre-close period to a
# negotiated monthly schedule. The borrower's compliance certificate then tests
# leverage on those stipulated figures.
#
# Two things follow. The deemed months govern: the covenant is tested on them
# whatever the model computes. They may also predate the model's first column,
# so without them a TTM leverage test cannot be computed at all.
#
# The schedule lives in the covenant spec, on the definition it belongs to, so
# EBITDA is still defined in exactly one place. Its months carry the agreement's
# own unit -- raw dollars, typically, against a model in thousands -- so they
# come through the same `stated_in` conversion a threshold does.
# ----------------------------------------------------------------------------
ISO_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def deemed_overlay(defs, def_name, unit, warnings=None):
    """The months this definition is deemed at by the agreement, on the model's unit.

    Returns None when the definition deems nothing, else
    {"by_label": {canonical month label: value}, "months": [ISO YYYY-MM, ...],
     "agreement_ref": ..., "stated_in": word}.
    """
    d = (defs or {}).get(def_name) or {}
    sched = d.get("deemed_schedule")
    if not isinstance(sched, dict):
        return None
    months = sched.get("months")
    if not isinstance(months, dict) or not months:
        if warnings is not None:
            warnings.append((
                "deemed_schedule_empty",
                f"definition '{def_name}' carries a deemed_schedule that names no "
                f"months, so every window month is read from the model -- transcribe "
                f"the agreement's table into it, or drop the block"))
        return None
    stated = ml.stated_unit(sched.get("stated_in"))
    if stated == "dollars" and unit is None:
        # Silence here is a figure wrong by a thousand, on the side that decides
        # the covenant. Raising surfaces as a loud input_unresolved row instead.
        raise ValueError(
            f"definition '{def_name}' deems months in dollars, but no reporting "
            f"unit was supplied to put them on the model's scale")
    by_label, iso = {}, []
    for key, val in months.items():
        if not ISO_MONTH.match(str(key)):
            raise ValueError(
                f"definition '{def_name}' deemed_schedule month '{key}' is not an "
                f"ISO YYYY-MM stamp")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(
                f"definition '{def_name}' deemed_schedule month '{key}' is not a "
                f"number ({val!r})")
        by_label[ml.month_label(str(key))] = ml.on_model_unit(
            float(val), unit, stated)
        iso.append(str(key))
    return {"by_label": by_label, "months": sorted(iso),
            "agreement_ref": sched.get("agreement_ref"), "stated_in": stated}


def _deemed_disagreements(model, series, deemed, def_name, warnings):
    """Warn where the model's own figure for a deemed month is not the deemed one.

    The agreement governs either way -- the schedule is what the covenant is
    tested on. But a model month that disagrees means the workbook was built
    without the schedule, or against a different one, and the model's row still
    feeds every OTHER read of that month, so the two sides are saying different
    things about the same month and only one of them is contractual.
    """
    if warnings is None:
        return
    off = []
    for label, val in zip(model.months, series):
        want = deemed["by_label"].get(label)
        if want is None or val is None:
            continue
        if abs(val - want) > max(abs(want) * 0.005, 1e-9):
            off.append(f"{label}: model {val:,.6g} vs deemed {want:,.6g}")
    if off:
        warnings.append((
            "deemed_model_disagreement",
            f"definition '{def_name}' is deemed by the agreement for "
            f"{len(deemed['months'])} month(s) and the model disagrees on "
            f"{len(off)} of them ({'; '.join(off[:3])}). The covenant is tested on "
            f"the deemed figure; pin the same figure into the model's bridge row so "
            f"every other read of that month agrees with it (see "
            f"build-monitoring-model references/assembling-rules.md)."))


def _shift_label(label, k):
    """The month label k months after `label`."""
    year, mnum = ml._parse_label(label)
    total = year * 12 + (mnum - 1) + k
    return ml._label_from(total // 12, total % 12 + 1)


def _deemed_map(lineage):
    """One input side's deemed months, or None when it has none."""
    return ((lineage or {}).get("deemed") or {}).get("by_label") or None


def resolve_definition_series(model, spec, def_name, warnings=None, unit=None):
    """A named definition as a per-month series, with the agreement's deemed
    months laid over the model's own figures.

    Returns (series, is_flow, source_str, lineage); lineage carries `deemed`
    (see deemed_overlay) when the definition is deemed for any month, which is
    what tells value_at those months govern and the refs which document says so.
    """
    series, is_flow, src, lineage = _definition_series(
        model, spec, def_name, warnings=warnings)
    deemed = deemed_overlay(spec.get("definitions", {}), def_name, unit, warnings)
    if deemed:
        _deemed_disagreements(model, series, deemed, def_name, warnings)
        lineage["deemed"] = deemed
        src = f"{src} + agreement-deemed months"
    return series, is_flow, src, lineage


def resolve_input(model, spec, inp, warnings=None, unit=None):
    """Resolve a covenant input ({ref:...} or {lines/op}) to a per-month series.

    Returns (series, is_flow, source_str, lineage) — see
    resolve_definition_series for the lineage shape. An inline lines input may
    carry its own `basis: "flow"|"spot"`; without one it defaults to flow
    (window sum) with a warning, since a balance-sheet amount summed over a
    TTM window would be counted twelve times.

    `unit` is the borrower's ReportingUnit, needed to put an agreement-deemed
    month (stated in the agreement's dollars) on the model's own scale.
    """
    if "ref" in inp:
        return resolve_definition_series(model, spec, inp["ref"],
                                        warnings=warnings, unit=unit)
    if "lines" in inp:
        op = inp.get("op", "sum")
        series, _used = _sum_lines(model, inp["lines"], "input")
        if op != "sum":
            raise ValueError(f"unsupported input op '{op}'")
        explicit = inp.get("basis")
        if explicit in ("flow", "spot"):
            is_flow = explicit == "flow"
        else:
            is_flow = True
            if warnings is not None:
                warnings.append((
                    "basis_inferred",
                    f"inline lines input {inp['lines']} has no explicit basis; "
                    "defaulting to 'flow' (summed over the covenant window) -- pin "
                    'basis: "flow"|"spot" on the input if this is a balance-sheet amount'))
        return (series, is_flow, f"model:sum({', '.join(inp['lines'])})",
                {"def": None, "labels": list(inp["lines"])})
    raise ValueError(f"input must have 'ref' or 'lines': {inp}")


def _sum_lines(model, labels, kind):
    """Sum a list of model rows into one per-month series. `kind` ("formula" |
    "input") only shapes the KeyError text on a missing row. Returns
    (series, used_labels).

    A month no leg carries a value for stays None rather than becoming 0.0 --
    an absent tranche and a zero balance are different facts, and only the
    caller knows whether the difference decides a covenant.
    """
    n_months = len(model.month_ends)
    series = [None] * n_months
    used = []
    for lab in labels:
        s = model.get_line(lab)
        if s is None:
            raise KeyError(f"{kind} line '{lab}' not found in model")
        used.append(lab)
        for i in range(n_months):
            if s[i] is not None:
                series[i] = (series[i] or 0.0) + s[i]
    return series, used


def _eff_n(window, is_flow):
    """Effective trailing count: a flow input's window length, else 1 (spot
    inputs and the spot window read the single test month)."""
    return ml._window_n(window) if is_flow else 1


def value_at(series, is_flow, window, idx, months=None, deemed=None):
    """Resolve one input's value at month index `idx` honoring its window.

    Returns (value, reason, blanks, n_deemed):
      value     the windowed number, or None when it cannot be formed
      reason    None on success, else the not_evaluable reason that explains the
                None -- "insufficient_history" (the window reaches back past
                everything that can fill it) or "input_blank" (the cells it
                needs carry no value at all)
      blanks    how many cells inside the window were empty, so a caller can
                warn about a partially-filled aggregate without changing the
                verdict
      n_deemed  how many window months came from the agreement's deemed
                schedule rather than the model

    Flow inputs aggregate over the trailing window; spot inputs (and the spot
    window) read the single test month. A blank cell inside an otherwise
    populated flow window still contributes nothing to the sum, which is how a
    month with no activity has always been treated -- but a window that is
    blank end to end is no data at all, and saying "0" there is a fabrication.
    If a cash row is renamed mid-workbook, coercing the old row's later blanks to
    zero can report a false liquidity breach.

    `deemed` maps a canonical month label to the amount the AGREEMENT stipulates
    for it (see deemed_overlay), and it wins over the model for that month --
    those months govern by contract, so they are read from the schedule whether
    or not the model covers them. That is also what lets a window reach back
    past the model's first column and still answer; insufficient_history now
    means the schedule did not cover the gap either. `months` is the model's own
    month labels, which the window's month names are counted back from.
    """
    n = _eff_n(window, is_flow)
    have_deemed = bool(months and deemed)
    labels = ml.trailing_months(months[idx], n) if have_deemed else None

    def _cell(j):
        """(value, came_from_the_schedule) for window position j, oldest first."""
        if labels is not None:
            d = deemed.get(labels[j])
            if d is not None:
                return d, True
        k = idx - (n - 1 - j)
        return (series[k] if k >= 0 else None), False

    if n == 1:
        v, was_deemed = _cell(0)
        if v is None:
            return None, "input_blank", 1, 0
        return v, None, 0, (1 if was_deemed else 0)
    cells = [_cell(j) for j in range(n)]
    n_deemed = sum(1 for _v, d in cells if d)
    # A window month that predates the model and that the schedule does not
    # cover is absent, not zero, and no aggregate over it can be honest.
    if any(v is None and idx - (n - 1 - j) < 0
           for j, (v, _d) in enumerate(cells)):
        return None, "insufficient_history", 0, n_deemed
    vals = [v for v, _d in cells]
    blanks = sum(1 for v in vals if v is None)
    if blanks == n:
        return None, "input_blank", blanks, n_deemed
    return sum(v for v in vals if v is not None), None, blanks, n_deemed


# ----------------------------------------------------------------------------
# What a threshold is stated in
#
# A threshold and a certified value are numbers a PERSON wrote into a file: the
# covenant spec, transcribed from the agreement, and the certified-values file,
# transcribed from the borrower's certificate. The model's figures are in the
# model's own unit, which may be thousands. The two sides only meet safely if
# the file says which unit it used.
#
#   threshold_unit: "dollars"       as the agreement states it ($3,000,000) --
#                                   put on the model's unit before comparing
#   threshold_unit: "model_units"   already on the model's unit (3000 against a
#                                   model in thousands) -- used as it stands
#   threshold_unit: "unitless"      a ratio, a percentage, a number of days --
#                                   nothing to convert
#
# It can be declared once for the whole spec and overridden per covenant. Absent,
# the default is what every spec written so far MEANS: a money threshold is
# already on the model's unit (Example Borrower's floors read 1500 and 3000 against a model in
# thousands), and a ratio is unitless. So nothing silently changes for a deal
# already set up, and a spec that would rather quote the agreement's own figure
# can say so and be read correctly.
#
# The engine's OWN constants are a different matter and are always dollars,
# because we wrote them: see cert_tolerance below, and monitor_lib.reporting_unit.
# ----------------------------------------------------------------------------
def threshold_stated_in(cov, spec=None):
    """Which unit this covenant's threshold and certified figures are written in.

    A ratio primitive is unitless whatever anything declares -- 3.13x is 3.13x on
    any scale, and both its sides come off the model. So a spec-level "dollars",
    which is a statement about the money thresholds in it, cannot reach a leverage
    or coverage covenant and scale it by a thousand. The vocabulary and the
    conversion are monitor_lib's, so every engine reads a written figure the same
    way; the list of ratio primitives is rows_lib's, so the row this covenant
    writes says the same thing about its unit that this function does.
    """
    if (cov or {}).get("primitive") in rl.RATIO_PRIMITIVES:
        return "unitless"
    return ml.stated_unit((cov or {}).get("threshold_unit"),
                          (spec or {}).get("threshold_unit"))


def on_model_unit(value, cov, unit, spec=None):
    """A COVENANT's threshold or certified figure, on the model's unit."""
    return ml.on_model_unit(value, unit, threshold_stated_in(cov, spec))


# ----------------------------------------------------------------------------
# Covenant-specific logic: threshold-schedule resolution by test date
# ----------------------------------------------------------------------------
def resolve_threshold(cov, test_date_iso, unit, spec=None):
    """Scalar threshold, or the schedule step in effect ON the test date, on the
    MODEL's unit (the spec states money thresholds in dollars — see above).

    Returns (threshold, effective_date, reason):
      - (value, effective, None)          a scalar threshold or the schedule
                                          step whose effective <= test date
                                          (the latest such step) is in effect
      - (None, None, "not_yet_effective") a schedule exists but no step takes
                                          effect on or before the test date
      - (None, effective, "gap")          the step in effect is an explicit gap
                                          marker (value: null) -- the covenant
                                          is deliberately not tested this period
      - (None, None, None)                no threshold and no schedule at all

    The reason lets the caller flag a not-evaluable period loudly instead of
    dropping the test silently (a not_yet_effective/gap period is distinct from
    a missing-data skip)."""
    sched = cov.get("threshold_schedule")
    if isinstance(sched, list) and sched:
        applicable = [e for e in sched
                      if isinstance(e, dict) and e.get("effective")
                      and e["effective"] <= test_date_iso]
        if not applicable:
            return None, None, "not_yet_effective"
        chosen = max(applicable, key=lambda e: e["effective"])
        if chosen.get("value") is None:  # explicit gap marker
            return None, chosen.get("effective"), "gap"
        return (on_model_unit(float(chosen["value"]), cov, unit, spec),
                chosen.get("effective"), None)
    if cov.get("threshold") is not None:
        return on_model_unit(float(cov["threshold"]), cov, unit, spec), None, None
    return None, None, None


def _flag_dated(exceptions, seen, cid, exc_type, reason_key, message, test_date=None,
                side=None):
    """Append a de-duplicated dated warning (one per covenant + reason), growing
    its `test_dates` list on every repeat.

    The warning NAMES every period it covers. "not tested for one or more
    periods" told a reader a month was missing without telling them which one,
    which is how a month whose covenant genuinely could not be computed read as
    an ordinary quiet month.

    `side` separates two warnings that share a reason but describe different
    inputs -- the numerator and the denominator of one ratio. Both sides keep the
    same `reason` for anything filtering on it, and each keeps its own row name
    and its own reading, instead of the first one to fire freezing the text and
    the other silently folding into it.
    """
    key = (cid, reason_key, side)
    exc = seen.get(key)
    if exc is None:
        exc = {"covenant": cid, "type": exc_type, "reason": reason_key,
               "warning": message, "test_dates": []}
        if side:
            exc["side"] = side
        seen[key] = exc
        exceptions.append(exc)
    if test_date and test_date not in exc["test_dates"]:
        exc["test_dates"].append(test_date)


def _flag_not_evaluable(exceptions, seen, cid, reason_key, message, test_date=None):
    """A dated warning for a covenant month that produced NO verdict, so a
    covenant that should be tested surfaces loudly instead of vanishing from the
    memo. Every one of these has a matching not_evaluable row."""
    _flag_dated(exceptions, seen, cid, "not_evaluable", reason_key, message, test_date)


def _partial_window_message(name, src, window, primitive, side):
    """The warning for a flow window that sums across some empty months, and which
    way that bends the verdict.

    A partly-blank window always makes THAT side's total lower than a full window
    would give. Whether a lower figure flatters the result or errs on the safe side
    depends on the test and the side the blank sits on: a short numerator understates
    a level or a coverage ratio (harder to pass) but understates leverage (easier),
    and a short denominator does the reverse. Telling every reader to treat the
    figure as understated answered the wrong question -- what a credit reader needs
    is whether a pass here can be trusted.
    """
    flatters = ((primitive == "max_ratio" and side == "numerator")
                or (primitive == "min_coverage" and side == "denominator"))
    bend = ("makes this test easier to pass than the borrower's real position, so a "
            "pass here may not be a real one"
            if flatters else
            "makes this test harder to pass than the borrower's real position, so the "
            "verdict errs on the safe side")
    return (f"Covenant '{name}' aggregates {src} (the {side}) over a {window} window "
            f"in which some months are empty, so the total covers fewer months than "
            f"the window implies. The test still ran; the short total {bend}.")


def _window_gap(model, window, idx, is_flow, deemed):
    """The window months that predate the model and that nothing fills."""
    n = _eff_n(window, is_flow)
    labels = ml.trailing_months(model.months[idx], n)
    return [lab for j, lab in enumerate(labels)
            if idx - (n - 1 - j) < 0 and (deemed or {}).get(lab) is None]


def _deemed_window_message(name, lineage, window, side):
    """The dated note that this test rests partly on the agreement's own figures.

    Carries NO count, because one warning covers every period it is true of and
    the count differs in each: a deemed run leaves a trailing window one month
    at a time, so the same covenant reading nine deemed months at its first test
    date reads four of them six months later. Each period's own `calc` note says
    how many of ITS months were deemed. What every period shares is the fact and
    the roll-off date, which is the part a reader acts on -- the test moves onto
    computed figures, usually weaker ones, since a stipulated EBITDA is the
    number the parties agreed to in order to make the metrics work.
    """
    d = lineage["deemed"]
    n = _eff_n(window, True)
    last = ml.month_label(d["months"][-1])
    ref = d.get("agreement_ref") or "the agreement"
    return (f"Covenant '{name}' reads part of its {n}-month {window} window for "
            f"{lineage.get('def')} (the {side}) from the amounts the agreement deems, "
            f"per {_ref_text(ref)}, not from the model — those months govern by "
            f"contract, and each period's calc note says how many of its own months "
            f"they are. The deemed run ends at {last}, so a {n}-month window is clear "
            f"of it from {_shift_label(last, n)} onward; the test moves onto computed "
            f"figures as it rolls off, one month at a time.")


def _ref_text(ref):
    """An agreement_ref as one readable phrase, in either shape it is written."""
    if isinstance(ref, dict):
        bits = [str(ref.get("section") or "").strip()]
        if ref.get("page"):
            bits.append(f"p. {ref['page']}")
        return " ".join(b for b in bits if b) or "the agreement"
    return str(ref)


def _threshold_gate_message(thr_reason, name):
    """The dated warning for a period the threshold schedule takes out of testing.

    Shared by the two paths that meet this gate -- a computable covenant and one
    whose inputs never resolved -- so the same period reads the same way whether
    or not the binding behind it happens to work."""
    if thr_reason == "not_yet_effective":
        return (f"Covenant '{name}' isn't tested yet for its earliest periods — "
                f"its threshold schedule starts on a later date, so no limit is "
                f"in effect before then. Those periods are reported as not "
                f"evaluable, not as a pass or breach.")
    return (f"Covenant '{name}' is marked as not tested for one or more "
            f"periods in its threshold schedule. Those periods are reported "
            f"as not evaluable, not as a pass or breach.")


def _uncomputable_texts(reason, name, window, model, src, gap=None):
    """(warning message, row detail) for the reason value_at could not form a
    number. Keeps the two call sites (numerator, denominator) saying the same
    thing about the same cause.

    `gap` names the window months that predate the model AND are not in the
    agreement's deemed schedule -- the months to transcribe, on a covenant whose
    definition is deemed for some of its window but not all of it."""
    if reason == "insufficient_history":
        detail = f"the model starts at {model.month_ends[0]}"
        if gap:
            detail += (f"; {len(gap)} earlier window month(s) are not in the "
                       f"agreement's deemed schedule ({gap[0]}..{gap[-1]})")
        return (f"Covenant '{name}' cannot be computed for its earliest periods — "
                f"its {window} window reaches back further than the model's first "
                f"month" + (f", and the agreement's deemed schedule does not cover "
                            f"every month before it ({gap[0]}..{gap[-1]})"
                            if gap else "") + f". Those periods are reported as not "
                f"evaluable, not as a pass or breach.",
                detail)
    return (f"Covenant '{name}' reads a model row that is empty for one or more "
            f"periods, so there is nothing to test there. Those periods are "
            f"reported as not evaluable rather than as zero — an empty cell is not "
            f"a balance of zero. Check whether the row was renamed or moved.",
            f"{src} has no value at this period")


NOT_EVALUABLE_REASONS = {
    "gap": "the threshold schedule in covenant-spec marks this period as not tested",
    "insufficient_history": ("the model does not reach back far enough to fill this "
                             "covenant's trailing window"),
    "denominator_zero": ("the denominator of this ratio is zero for this period, so "
                         "the ratio is undefined"),
    "input_unresolved": "this covenant's inputs do not resolve against the model",
    "input_blank": ("the model row this covenant reads is empty for this period, so "
                    "there is no value to test -- distinct from a value of zero"),
}


def _not_evaluable_row(cov, cid, name, primitive, window, test_date, reason,
                       src_idx, spec_file, unit, detail=None, certified=None,
                       spec=None, at_risk_band=None, certified_file=None):
    """One DATED row saying this covenant could not be tested at this date.

    A covenant past its first test date that cannot be computed belongs in
    `results` as a row, never as an absence: silence is indistinguishable from a
    quiet, compliant month, and a covenant that is a leg of an either/or has to be
    present at the date for its group to resolve at all. The row asserts no
    pass and no breach -- `required` and `actual` are null and `reason` says why.

    The CERTIFIED side is carried anyway when the certificate reports this
    covenant at this date. It is transcribed from the borrower's own document, so
    it exists independently of whether the model can compute -- and these are
    exactly the months a reader most needs it, since the model side has nothing to
    say. It never changes `status`: the certificate is a cross-check column, not a
    substitute verdict, so the model side stays not_evaluable and `cert_agrees`
    stays null because there is no model figure to agree with.
    """
    why = NOT_EVALUABLE_REASONS.get(reason, reason)
    row = {
        "id": cid, "name": name, "primitive": primitive,
        "test_date": test_date, "window": window,
        "required": None, "actual": None,
        "headroom_abs": None, "headroom_pct": None,
        "status": "not_evaluable", "reason": reason,
        "source_ref": [src_idx.ref(spec_file, section=f"covenants.{cid}",
                                   upstream_at=cov.get("agreement_ref"))],
        "calc": (f"not evaluable at {test_date}: {why} (covenants.{cid})"
                 + (f"; {detail}" if detail else "")
                 + ". No pass/breach is asserted."),
    }
    cert_value = (_cert_value(certified, spec, cid, test_date, unit)
                  if certified is not None and spec is not None else None)
    cert_status = cert_h_abs = cert_h_pct = None
    if cert_value is not None:
        threshold, _eff, _r = resolve_threshold(cov, test_date, unit, spec)
        if threshold is not None:
            cert_status, cert_h_abs, cert_h_pct = evaluate(
                primitive, cert_value, threshold, at_risk_band)
        row["calc"] += (f" The borrower's certificate reports {cert_value:,.6g} for "
                        f"this date; it is shown as a cross-check and does not stand "
                        f"in for the model.")
        if certified_file:
            row["source_ref"] = list(row["source_ref"]) + [
                src_idx.ref(certified_file, section=f"values.{cid}.{test_date}",
                            upstream_at=_cert_location(certified, cid, test_date))]
    row.update({
        "cert_value": cert_value, "cert_status": cert_status,
        "cert_headroom_abs": cert_h_abs, "cert_headroom_pct": cert_h_pct,
        "cert_agrees": None, "delta_model_vs_cert": None,
    })
    if cov.get("either_or_group"):
        row["either_or_group"] = cov["either_or_group"]
    return row


# ----------------------------------------------------------------------------
# Covenant-specific logic: primitive comparators + at-risk band
# ----------------------------------------------------------------------------
def read_groups(spec):
    """The covenant groups this spec declares, as {gid: {...}}.

    An agreement that tests two covenants as alternatives ("maintain X OR Y")
    is ONE covenant with two legs, and a leg missing its own level is not a
    breach. Two declaration shapes are read, in this order:

      1. a top-level `either_or_groups` object -- authoritative, and the only
         shape that can carry an explicit `rule`
      2. otherwise, membership derived from each covenant's `either_or_group`
         tag, defaulting to `any_pass`

    A covenant that carries no tag is outside every group, which is how an
    overlay test ("irrespective of Borrower's ability to choose between the
    foregoing clauses") stays a covenant in its own right.
    """
    declared = spec.get("either_or_groups") or {}
    groups = {}
    for gid, g in declared.items():
        members = [m for m in (g.get("members") or [])]
        if not members:
            continue
        groups[gid] = {
            "id": gid,
            "name": g.get("name") or gid,
            "rule": g.get("rule") or "any_pass",
            "members": members,
            "agreement_ref": g.get("agreement_ref"),
            "rule_plain": g.get("rule_plain"),
            "declared_by": "either_or_groups",
        }
    for cov in spec.get("covenants") or []:
        gid = cov.get("either_or_group")
        if not gid or not cov.get("primitive"):
            continue
        if gid in groups:
            if cov["id"] not in groups[gid]["members"]:
                groups[gid]["members"].append(cov["id"])
            continue
        groups[gid] = {
            "id": gid,
            "name": gid,
            "rule": cov.get("either_or_rule") or "any_pass",
            "members": [cov["id"]],
            "agreement_ref": cov.get("agreement_ref"),
            "rule_plain": None,
            "declared_by": "covenant_tag",
        }
    return {gid: g for gid, g in groups.items() if len(g["members"]) > 1}


# A leg status that settles nothing: either the leg was never evaluated at this
# date, or it was evaluated and found un-testable (no threshold in effect).
# Neither is a leg that failed, so neither can help assert "every leg failed".
NO_DEFINITIVE_RESULT = (None, "not_evaluable")


# A not_evaluable leg the SCHEDULE took out of testing, as opposed to one the model
# could not compute. Nobody asks whether a borrower complied in a period its own
# schedule excludes, so a group whose every unfailed leg is one of these was not
# tested -- which is not the same claim as "compliance here is unknown".
NOT_TESTED_REASONS = ("gap", "not_yet_effective")


def resolve_group(rule, leg_statuses, leg_reasons=None):
    """Resolve one group at one test date from its legs' statuses.

    Returns (status, satisfied_only_by_at_risk_leg).

    `any_pass`: the group is BREACHED only when every leg fails, so one leg
    missing its own level while another holds is a pass. `at_risk` counts as
    satisfied -- it is a level the borrower met -- but when the only thing
    holding the group up is an at-risk leg the flag says so, otherwise the
    warning would vanish into a clean pass.

    One satisfied leg settles the group whatever the other legs did: "maintain X
    OR Y" is met by X alone, so Y being un-evaluable is irrelevant. Only when NO
    leg is satisfied does an un-evaluable leg matter -- and then the group is
    indeterminate, because "every leg failed" cannot be asserted from a leg that
    was never tested.

    `leg_reasons` (per-leg `reason`, positionally matched to `leg_statuses`) tells
    the two kinds of un-evaluable apart. A period the threshold schedule marks as
    not tested is not a hole in the evidence, so a group whose legs were ALL taken
    out of testing that period reports not_evaluable rather than claiming its
    compliance is unknown. A leg that genuinely could not be computed still makes
    the group indeterminate -- including alongside a failing leg, where the honest
    answer is that the leg which might have satisfied the group was never tested.
    """
    if rule != "any_pass":
        return None, False
    if not leg_statuses:
        return "indeterminate", False
    if "pass" in leg_statuses:
        return "pass", False
    if "at_risk" in leg_statuses:
        return "pass", True
    reasons = list(leg_reasons or [])
    reasons += [None] * (len(leg_statuses) - len(reasons))
    undecided = [(s, r) for s, r in zip(leg_statuses, reasons)
                 if s in NO_DEFINITIVE_RESULT]
    if undecided:
        if (len(undecided) == len(leg_statuses)
                and all(r in NOT_TESTED_REASONS for _s, r in undecided)):
            return "not_evaluable", False
        return "indeterminate", False
    return "breach", False


def evaluate(primitive, actual, threshold, at_risk_band):
    """Return (status, headroom_abs, headroom_pct).

    headroom_abs is signed in the 'good' direction: positive = compliant slack.
        min_level / min_coverage: actual - threshold
        max_ratio:                threshold - actual
    """
    if primitive in ("min_level", "min_coverage"):
        headroom_abs = actual - threshold
        breached = actual < threshold
    elif primitive == "max_ratio":
        headroom_abs = threshold - actual
        breached = actual > threshold
    else:
        raise ValueError(f"unknown primitive: {primitive}")

    denom = abs(threshold) if threshold != 0 else None
    headroom_pct = (headroom_abs / denom) if denom else None

    if breached:
        status = "breach"
    else:
        band = at_risk_band if at_risk_band is not None else 0.10
        if denom and (headroom_abs / denom) <= band:
            status = "at_risk"
        else:
            status = "pass"
    return status, headroom_abs, headroom_pct


# ----------------------------------------------------------------------------
# Covenant-specific logic: certified values (borrower's compliance certificate)
# ----------------------------------------------------------------------------
def load_certified_values(path):
    """Load the certified-values file the covenant subagent transcribes from the
    borrower's monthly compliance certificate (see the module spec):

        { "source": "<certificate filename>", "period": "YYYY-MM",
          "values": { "<covenant_id>": { "YYYY-MM-DD": <number>, ... }, ... } }

    The values are the borrower's OWN stated actuals per covenant per test date
    -- transcription from the certificate, never computation. Returns {} when
    path is None or the file does not exist (certified columns then report as
    not delivered)."""
    if not path:
        return {}
    resolved = ml.resolve_path(path)
    if not os.path.exists(resolved):
        return {}
    with open(resolved, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _cert_value(certified, spec, cid, test_date, unit):
    """Borrower-stated actual for (covenant id, test date), on the MODEL's unit:
    the transcribed certified-values file first, else the legacy
    cov['cert_values'] block.

    The certificate states money in dollars, so a money covenant's certified
    figure is converted the same way its threshold is — otherwise the certified
    and model columns sit side by side a thousand-fold apart and every deal
    reports the borrower's own certificate as disagreeing with us.
    """
    cov_by_id = {c.get("id"): c for c in spec.get("covenants", [])}
    cov = cov_by_id.get(cid) or {}
    vals = (certified.get("values") or {}).get(cid) or {}
    v = vals.get(test_date)
    # A transcribed entry is either the bare number, or the number with WHERE on
    # the certificate it was read from -- both are valid transcription.
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return on_model_unit(float(v), cov, unit, spec)
    cv = cov.get("cert_values")
    if isinstance(cv, dict) and test_date in cv:
        vv = cv[test_date]
        return (on_model_unit(float(vv), cov, unit, spec)
                if isinstance(vv, (int, float)) else None)
    return None


def _cert_location(certified, cid, test_date):
    """Where on the compliance certificate a transcribed figure was read from —
    the page and the wording, when the transcriber recorded them."""
    v = ((certified.get("values") or {}).get(cid) or {}).get(test_date)
    return v if isinstance(v, dict) else None


NO_CURE_WORDS = {"", "none", "no", "n/a", "na", "not applicable", "no cure",
                 "no cure rights", "-", "--"}

# Punctuation that follows the leading word of a cure paragraph: "none -",
# "None.", "NONE —", "none (typical)".
CURE_WORD_PUNCT = ".,;:—–-()[]\"'"


def names_a_cure(value):
    """Whether a covenant's `cure_rights` actually names a cure.

    The field is prose, and a covenant with no cure says so in a SENTENCE --
    "none - immediate Event of Default under LSA Sec 8.1(b); no grace period, no
    equity cure", "None. The Cure Right in Section 6.7(c) applies only to ..." --
    so the verdict comes off the leading word, with the whole string still read
    for the bare forms ("none", "n/a"). Matching only whole strings spells every
    such breach curable, which is the wrong way for this to be wrong: an analyst
    reads a cure as time to fix it.
    """
    if not isinstance(value, str):
        return bool(value)
    text = value.strip().strip(".").casefold()
    if text in NO_CURE_WORDS:
        return False
    lead = next((w for w in (t.strip(CURE_WORD_PUNCT) for t in text.split()) if w), "")
    return lead not in NO_CURE_WORDS


def cert_tolerance(cov, primitive, threshold, unit, spec=None):
    """Delta tolerance between the certified actual and the model
    reconstruction, on the MODEL's unit. A per-covenant `cert_tolerance`
    override wins; defaults are primitive-aware: 0.05 absolute for ratio
    primitives (a leverage/coverage turn is meaningful at the second decimal),
    max(1% of threshold, $1,000) for dollar-level covenants.

    The threshold arrives already on the model's unit, so 1% of it needs no
    conversion. The $1,000 floor is OUR constant, always dollars, and does: on a
    model in thousands an unconverted floor makes the tolerance $1,000,000, so a
    certificate a million dollars away from our own figure reports as agreeing.
    """
    t = cov.get("cert_tolerance")
    if isinstance(t, (int, float)) and t > 0:
        return float(t)
    if primitive in ("max_ratio", "min_coverage"):
        return 0.05
    floor = (unit.from_dollars(1000.0)
             if threshold_stated_in(cov, spec) != "unitless" else 1000.0)
    return max(abs(threshold) * 0.01, floor)


# ----------------------------------------------------------------------------
# Per-figure provenance + calculation records
# ----------------------------------------------------------------------------
def _side_refs(idx, model, spec_file, lineage, test_date, window, is_flow,
               n_deemed=0):
    """Source refs for one input side at one test date: a ref per model row
    read (file/sheet/row/cells/period, window-aware) plus, when the input came
    through a spec definition, a ref into the covenant-spec section that
    defined it.

    A side that read any agreement-deemed month also cites the schedule, whose
    upstream is the signed agreement itself — for those months the workbook is
    not where the figure came from."""
    refs = []
    win = window if is_flow else "spot"
    for lab in lineage["labels"]:
        p = model.provenance(lab, month=test_date, window=win)
        if p:
            refs.append(idx.ref(model.given_path, **p))
    if lineage.get("def"):
        refs.append(idx.ref(spec_file, section=f"definitions.{lineage['def']}"))
        if n_deemed and lineage.get("deemed"):
            refs.append(idx.ref(
                spec_file,
                section=f"definitions.{lineage['def']}.deemed_schedule",
                kind="agreement-deemed", deemed_months=n_deemed,
                upstream_at=lineage["deemed"].get("agreement_ref")))
    return refs


def _component_note(model, lineage, is_flow, window, idx, side):
    """One input side's model rows, each with its own windowed value — what
    grounds a certified-vs-model delta note in components instead of a guess.

    A summed denominator can hide whether cash-interest scope or scheduled
    principal drives a certified-vs-model difference. The archived row therefore
    carries each component instead of asking the memo writer to guess.

    Emitted only for a side summing 2+ rows whose window months come from the
    model alone — a single row's windowed value is already in the notes, and a
    deemed side mixes the agreement's months into the window, which per-row
    model reads would misattribute.
    """
    labels = (lineage or {}).get("labels") or []
    if len(labels) < 2 or (lineage or {}).get("deemed"):
        return None
    parts = []
    for label in labels:
        series = model.get_line(label)
        if series is None:
            continue
        v, _reason, _blanks, _nd = value_at(series, is_flow, window, idx)
        parts.append(f"'{label}'=" + (f"{v:,.6g}" if v is not None else "no value"))
    if not parts:
        return None
    n = _eff_n(window, is_flow)
    how = "at the test month" if n == 1 else f"summed over the {n}-mo window"
    return f"model {side} by component ({how}): " + "; ".join(parts)


def _side_calc(src, val, window, is_flow, months_used, n_deemed=0):
    """One side of the calc note: value, source, and the exact window read."""
    n = _eff_n(window, is_flow)
    if n == 1:
        how = f"at {months_used[-1]}"
    else:
        how = f"summed {months_used[0]}..{months_used[-1]} ({n} mo)"
    note = f"{src} {how} = {val:,.6g}"
    if n_deemed:
        note += f", {n_deemed} of {n} mo deemed by the agreement"
    return note


# ----------------------------------------------------------------------------
# Main compute
# ----------------------------------------------------------------------------
def _certificate_path(name, reporting_folder=None,
                      compliance_certificate_folder=None):
    """Resolve a bare certified-source filename through configured folders.

    The dedicated certificate folder wins; reporting packages are the fallback.
    Returns the name unchanged when it is already a path or no configured folder
    contains it, so an unreachable source costs the provenance hop, not the run.
    """
    if not name or re.search(r"[\\/]", str(name)):
        return name
    target = str(name).strip().lower()
    folders = dict.fromkeys((compliance_certificate_folder, reporting_folder))
    for folder in filter(None, folders):
        try:
            root = ml.resolve_path(folder)
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for filename in files:
                    if filename.lower() == target:
                        rel = os.path.relpath(os.path.join(dirpath, filename), root)
                        return (str(folder).rstrip("/\\") + "/"
                                + rel.replace(os.sep, "/"))
        except (OSError, ml.ConfigError):
            continue
    return name


# --- the reconciliation gate -------------------------------------
#
# A certified figure that disagrees with ours by a wide margin is not a note to
# carry into the memo -- it is the run telling us one of the two sides is on the
# wrong basis, and until that is settled no verdict on that covenant is worth
# publishing. A status divergence can reflect a different accounting basis;
# detecting the mismatch is insufficient unless publication stops for reconciliation.
#
# So a divergence past this multiple of the tolerance sets `reconciliation_required`
# on the output, and monthly-monitor refuses to render a covenant verdict while it
# is set. Run with --allow-unreconciled to publish anyway, which records the
# override on the output rather than letting it pass silently.
RECONCILE_TOLERANCE_MULTIPLE = 3.0
# ...and materially large against the figure itself. The tolerance alone is too
# tight to gate on: a small restatement residual can exceed the absolute tolerance
# while both sides still agree on status. Blocking that case encourages habitual
# overrides, so the relative threshold is required too.
RECONCILE_MIN_RELATIVE = 0.10


def reconciliation_blockers(results, allow=False):
    """Covenants whose certified and model figures diverge too far to publish.

    Two triggers, either alone sufficient:
      * the delta exceeds RECONCILE_TOLERANCE_MULTIPLE x the per-covenant
        tolerance -- the figures are not the same measurement; and
      * the two sides disagree on the pass/at-risk/breach call, at any size,
        because that is the disagreement that reaches a credit decision.
    """
    blockers = []
    for r in results:
        if r.get("cert_value") is None or r.get("actual") is None:
            continue
        delta = r.get("delta_model_vs_cert")
        tol = r.get("cert_tolerance_applied")
        actual = r.get("actual")
        # A verdict disagreement blocks at ANY size: the two sides do not agree
        # on whether this borrower complied, and no margin makes that publishable.
        verdict_split = (r.get("cert_status") is not None
                         and r.get("cert_status") != r.get("status"))
        # A delta with no verdict disagreement has to clear BOTH bars -- well past
        # the tolerance, and material against the figure -- so a small residual
        # that both sides pass on is reported and not blocked on.
        floor = max(RECONCILE_TOLERANCE_MULTIPLE * abs(tol) if tol else 0.0,
                    RECONCILE_MIN_RELATIVE * abs(actual))
        wide = delta is not None and floor > 0 and abs(delta) > floor
        if not (wide or verdict_split):
            continue
        blockers.append({
            "covenant": r.get("id"),
            "test_date": r.get("test_date"),
            "model_actual": r.get("actual"),
            "cert_actual": r.get("cert_value"),
            "delta": delta,
            "tolerance": tol,
            "model_status": r.get("status"),
            "cert_status": r.get("cert_status"),
            "trigger": ("verdict disagreement" if verdict_split and not wide
                        else "delta beyond tolerance" if wide and not verdict_split
                        else "verdict disagreement and delta beyond tolerance"),
            "resolve_by": (
                "Settle the BASIS before the verdict. Read covenant-spec "
                "accounting_basis -- the covenant section's preamble often binds the "
                "test to the borrower's own accounting as shown in named reference "
                "financials, and a generic 'extraordinary charges' limb is resolved by "
                "what that document actually treated as extraordinary. Then read the "
                "covenant's threshold_derivation: a level set at a percentage of an "
                "ADJUSTED projection cannot be tested against an unadjusted actual. "
                "Then check deal-context ebitda_addbacks for a line elected in or out. "
                "If the borrower's basis is right, correct the spec and re-run; if ours "
                "is, take it to them with the component decomposition in this record."),
        })
    return blockers


def is_test_date(test_date, first_test_date, frequency):
    """Return whether a model month-end is a contractual covenant test date."""
    current = date.fromisoformat(test_date)
    first = date.fromisoformat(first_test_date)
    months = (current.year - first.year) * 12 + current.month - first.month
    normalized = str(frequency).strip().lower()
    if normalized.startswith("continuous") or normalized == "monthly":
        return current >= first
    interval = {"quarterly": 3, "annual": 12}[normalized]
    return current >= first and months % interval == 0


def compute(model, spec, unit, borrower_name=None, certified=None,
            allow_unreconciled=False,
            spec_file=None, certified_file=None,
            agreement_file=None, reporting_folder=None, debt_service=None,
            compliance_certificate_folder=None):
    """unit is the borrower's ReportingUnit (monitor_lib.reporting_unit(cfg)):
    what one figure in this model stands for. Thresholds and certified figures
    are stated in dollars and are put on that unit as they enter — without it a
    dollar covenant against a thousands-scale model reports a false breach.

    spec_file / certified_file are the AS-CONFIGURED path strings of the
    covenant spec and certified-values file — recorded in source refs so the
    downstream artifact can join store hyperlinks. They default to
    generic placeholders so legacy callers still produce a valid output.

    debt_service says whether this borrower's contractual debt-service
    schedule exists (True), has not been built (False), or is unknown (None —
    no borrower-config in play). Covenant Fixed Charges mean contractual debt
    service, and a coverage covenant computed off ledger-populated rows can
    disagree with the borrower's certificate in either direction when booked
    interest includes non-facility borrowings or scheduled amortization is absent.
    False adds
    a fixed_charges_ledger_proxy warning to every coverage covenant so the
    memo flags the proxy instead of publishing it as the model read."""
    results = []
    exceptions = []
    certified = certified or {}
    seen_basis_warnings = set()
    seen_not_evaluable = {}
    _mp = getattr(model, "given_path", None)
    # Resolve the file names FIRST: the source index keys its upstream map on
    # them, so a default applied afterwards would leave the map pointing at None.
    spec_file = spec_file or "covenant-spec.standardized.json"
    if certified_file is None and certified.get("source"):
        certified_file = certified["source"]
    # A covenant's threshold and definitions are recorded in our spec, but they
    # COME FROM the signed agreement -- so a spec ref carries that document as
    # its upstream, and the spec's section text rides along as the section
    # within it.
    src_idx = ml.SourceIndex(
        source_map=ml.model_source_map(_mp), model_path=_mp,
        upstream_map={k: v for k, v in (
            (spec_file, agreement_file),
            # the certified figures were transcribed FROM the borrower's own
            # compliance certificate, which the certified file names
            (certified_file, _certificate_path(
                certified.get("source"), reporting_folder,
                compliance_certificate_folder)),
        ) if k and v})

    # A group declared ONLY at the top level names its members in
    # either_or_groups[gid].members, and read_groups honours that -- but every
    # per-row and per-exception read below asks the COVENANT for its tag. Without
    # this, a spec using the top-level shape alone validates clean, emits no group
    # row, leaves group_status null, and archives each leg's miss as a covenant
    # breach on a compliant credit: exactly the defect either/or grouping exists to
    # fix. Fill the tag in on a local copy so the two shapes behave identically and
    # a spec never has to say the same thing twice.
    member_of = {m: gid for gid, g in read_groups(spec).items() for m in g["members"]}
    if member_of:
        spec = dict(spec)
        spec["covenants"] = [
            (dict(c, either_or_group=member_of[c.get("id")])
             if c.get("id") in member_of and not c.get("either_or_group") else c)
            for c in (spec.get("covenants") or [])]

    cov_by_id = {c["id"]: c for c in (spec.get("covenants") or []) if c.get("id")}

    for ci, cov in enumerate(spec.get("covenants", [])):
        primitive = cov.get("primitive")
        # Reference-only entries (affirmative / negative / reporting covenants)
        # are documentation, not math -- covenant-setup captures them with no
        # primitive and a type tag. Skip them cleanly.
        if primitive is None and cov.get("type") in ("affirmative", "negative", "reporting"):
            continue
        cid = cov.get("id") or f"covenant_{ci}"
        name = cov.get("name", cid)
        if primitive not in ("min_level", "max_ratio", "min_coverage"):
            exceptions.append({
                "covenant": cid,
                "error": (f"unrecognized primitive {primitive!r}; entry skipped. "
                          "Reference-only entries must carry "
                          "type: affirmative|negative|reporting.")})
            continue
        window = cov.get("window", "spot")
        band = cov.get("at_risk_band", 0.10)

        # A coverage covenant's denominator is debt service, and without a
        # built schedule the model rows behind it carry the ledger's figures.
        # The verdict still computes — today's behavior — but the proxy is
        # said out loud, once per covenant, so the memo can flag it rather
        # than publish it as the model read.
        if primitive == "min_coverage" and debt_service is False:
            exceptions.append({
                "covenant": cid, "type": "fixed_charges_ledger_proxy",
                "warning": (
                    f"Covenant '{name}' is a coverage test whose denominator "
                    f"means contractual debt service, but no debt-service "
                    f"schedule has been built for this borrower, so the model "
                    f"side reads whatever the ledger put in the bound rows — "
                    f"booked interest expense sweeps in non-facility "
                    f"borrowings, and scheduled amortization appears on no "
                    f"statement line. Say in the memo: fixed charges on "
                    f"ledger proxy — debt-service schedule not yet built. To "
                    f"build it, run covenant-setup's facility-terms step "
                    f"(debt-service-spec.json + the schedule generator), then "
                    f"refresh the model so the covenant-support rows carry "
                    f"the contractual figures.")})

        # The covenant's own first test date, read before the inputs so that a
        # covenant whose inputs never resolve still knows which dates it owed an
        # answer for.
        first_test = cov.get("first_test_date")
        # accept either a clean ISO date or a descriptive string; extract a date if present
        first_iso = None
        if isinstance(first_test, str):
            m = re.search(r"\d{4}-\d{2}-\d{2}", first_test)
            first_iso = m.group(0) if m else None

        # Resolve the input series for this covenant.
        basis_warnings = []
        try:
            if primitive == "min_level":
                num_series, num_flow, num_src, num_lineage = resolve_input(
                    model, spec, cov["metric"], warnings=basis_warnings, unit=unit)
                den_series = den_flow = den_src = den_lineage = None
            else:
                num_series, num_flow, num_src, num_lineage = resolve_input(
                    model, spec, cov["numerator"], warnings=basis_warnings,
                    unit=unit)
                den_series, den_flow, den_src, den_lineage = resolve_input(
                    model, spec, cov["denominator"], warnings=basis_warnings,
                    unit=unit)
        except (KeyError, ValueError) as e:
            # The spec points at something the model does not have. One blanket
            # exception can leave a covenant absent from every test date, so the
            # output reads as if the covenant does not exist. Give every date it owed an answer for a row that says so.
            #
            # The threshold schedule decides WHICH dates those are, exactly as it
            # does for a covenant whose inputs did resolve. A month before the
            # schedule takes effect owes no answer at all, and a month the schedule
            # marks untested is a `gap` -- calling either one `input_unresolved`
            # names the wrong cause for a month this binding was never asked about,
            # and puts a row where the covenant was not yet in force.
            err = {"covenant": cid, "error": f"input resolution failed: {e}",
                   "test_dates": []}
            exceptions.append(err)
            for test_date in model.month_ends:
                if first_iso and not is_test_date(test_date, first_iso, cov.get("frequency")):
                    continue
                _threshold, _eff, thr_reason = resolve_threshold(cov, test_date, unit, spec)
                if thr_reason in ("not_yet_effective", "gap"):
                    _flag_not_evaluable(
                        exceptions, seen_not_evaluable, cid, thr_reason,
                        _threshold_gate_message(thr_reason, name), test_date)
                    if thr_reason == "not_yet_effective":
                        continue
                    reason, detail = "gap", None
                else:
                    reason, detail = "input_unresolved", str(e)
                    err["test_dates"].append(test_date)
                results.append(_not_evaluable_row(
                    cov, cid, name, primitive, window, test_date,
                    reason, src_idx, spec_file, unit, detail=detail,
                    certified=certified, spec=spec, at_risk_band=band,
                    certified_file=certified_file))
            continue
        for wtype, wmsg in basis_warnings:
            if wmsg not in seen_basis_warnings:
                seen_basis_warnings.add(wmsg)
                exceptions.append({"covenant": cid, "type": wtype,
                                   "warning": wmsg})

        for idx, test_date in enumerate(model.month_ends):
            if first_iso and not is_test_date(test_date, first_iso, cov.get("frequency")):
                continue

            threshold, eff, thr_reason = resolve_threshold(cov, test_date, unit, spec)
            if threshold is None:
                # A covenant past its first test date with no threshold in
                # effect is flagged loud, not dropped silently. (Missing input
                # data is handled separately below as a data-absent skip.)
                if thr_reason in ("not_yet_effective", "gap"):
                    _flag_not_evaluable(
                        exceptions, seen_not_evaluable, cid, thr_reason,
                        _threshold_gate_message(thr_reason, name), test_date)
                if thr_reason == "gap":
                    # A DATED row for a gap. The covenant IS in force here; the
                    # schedule just marks this period untested, so "which month could
                    # not be tested" is a live credit question the warning alone does
                    # not answer.
                    #
                    # not_yet_effective gets no row: those are months before the
                    # covenant was ever tested, and nobody asks whether a borrower
                    # complied with a limit that did not yet apply. Its periods are
                    # named on the warning above.
                    results.append(_not_evaluable_row(
                        cov, cid, name, primitive, window, test_date, "gap",
                        src_idx, spec_file, unit, certified=certified, spec=spec,
                        at_risk_band=band, certified_file=certified_file))
                continue

            num_deemed_map = _deemed_map(num_lineage)
            num_val, num_reason, num_blanks, num_deemed = value_at(
                num_series, num_flow, window, idx,
                months=model.months, deemed=num_deemed_map)
            if num_val is None:
                # Two different causes, and the reader acts on them differently.
                # insufficient_history resolves itself as the model grows: Round 2's
                # two covenants carrying the Event-of-Default teeth were invisible
                # for the deal's whole first year behind a bare skip here, and the
                # output read "3 passes, 0 exceptions". input_blank does NOT resolve
                # itself -- the bound row is empty and someone has to repoint it.
                msg, detail = _uncomputable_texts(
                    num_reason, name, window, model, num_src,
                    gap=(_window_gap(model, window, idx, num_flow, num_deemed_map)
                         if num_deemed_map and num_reason == "insufficient_history"
                         else None))
                _flag_not_evaluable(exceptions, seen_not_evaluable, cid, num_reason,
                                    msg, test_date)
                results.append(_not_evaluable_row(
                    cov, cid, name, primitive, window, test_date,
                    num_reason, src_idx, spec_file, unit, detail=detail,
                    certified=certified, spec=spec, at_risk_band=band,
                    certified_file=certified_file))
                continue
            if num_blanks:
                # A partly-filled window still sums, so the verdict STANDS and this is
                # not a not_evaluable -- but the total covers fewer months than the
                # window implies, and which way that bends the test is the thing a
                # reader acts on, so it is said out loud rather than buried in the
                # calc note.
                _flag_dated(
                    exceptions, seen_not_evaluable, cid, "input_partial_window",
                    "input_partial_window",
                    _partial_window_message(name, num_src, window, primitive, "numerator"),
                    test_date, side="numerator")
            if num_deemed:
                _flag_dated(
                    exceptions, seen_not_evaluable, cid, "deemed_window_mix",
                    "deemed_window_mix",
                    _deemed_window_message(name, num_lineage, window,
                                           "numerator" if primitive != "min_level"
                                           else "metric"),
                    test_date, side="numerator")

            notes = []
            # Months each side actually read (for the calc note): flow inputs
            # cover the trailing window; spot inputs read the test month only.
            # Counted back from the test month rather than sliced out of the
            # model, because a deemed window legitimately reaches back further
            # than the workbook does.
            def _months_used(is_flow):
                return ml.trailing_months(model.months[idx], _eff_n(window, is_flow))

            if primitive == "min_level":
                actual = num_val
                source = f"{num_src} [{window}]"
                calc = (f"actual = {_side_calc(num_src, num_val, window, num_flow, _months_used(num_flow), num_deemed)}; "
                        f"test: min_level {actual:,.6g} >= {threshold:,.6g}")
                source_ref = _side_refs(src_idx, model, spec_file,
                                        num_lineage, test_date, window, num_flow,
                                        num_deemed)
            else:
                den_deemed_map = _deemed_map(den_lineage)
                den_val, den_reason, den_blanks, den_deemed = value_at(
                    den_series, den_flow, window, idx,
                    months=model.months, deemed=den_deemed_map)
                if den_val is None:
                    msg, detail = _uncomputable_texts(
                        den_reason, name, window, model, den_src,
                        gap=(_window_gap(model, window, idx, den_flow, den_deemed_map)
                             if den_deemed_map and den_reason == "insufficient_history"
                             else None))
                    _flag_not_evaluable(exceptions, seen_not_evaluable, cid,
                                        den_reason, msg, test_date)
                    results.append(_not_evaluable_row(
                        cov, cid, name, primitive, window, test_date,
                        den_reason, src_idx, spec_file, unit, detail=detail,
                        certified=certified, spec=spec, at_risk_band=band,
                        certified_file=certified_file))
                    continue
                if den_blanks:
                    _flag_dated(
                        exceptions, seen_not_evaluable, cid, "input_partial_window",
                        "input_partial_window",
                        _partial_window_message(name, den_src, window, primitive,
                                                "denominator"),
                        test_date, side="denominator")
                if den_deemed:
                    _flag_dated(
                        exceptions, seen_not_evaluable, cid, "deemed_window_mix",
                        "deemed_window_mix",
                        _deemed_window_message(name, den_lineage, window,
                                               "denominator"),
                        test_date, side="denominator")
                if den_val == 0:
                    # A blank input the model coerced to zero, not a real zero
                    # denominator: an early recurring-revenue ratio may have no
                    # annualized revenue and must retain its dated gaps.
                    _flag_not_evaluable(
                        exceptions, seen_not_evaluable, cid, "denominator_zero",
                        f"Covenant '{name}' has a zero denominator for one or more "
                        f"periods, so its ratio is undefined there. Those periods are "
                        f"reported as not evaluable, not as a pass or breach.",
                        test_date)
                    results.append(_not_evaluable_row(
                        cov, cid, name, primitive, window, test_date,
                        "denominator_zero", src_idx, spec_file, unit,
                        detail=f"denominator {den_src} is zero",
                        certified=certified, spec=spec, at_risk_band=band,
                        certified_file=certified_file))
                    continue
                actual = num_val / den_val
                source = f"({num_src})/({den_src}) [{window}]"
                notes.append(f"numerator={num_val:.0f}, denominator={den_val:.0f}")
                op = "<=" if primitive == "max_ratio" else ">="
                calc = (f"actual {actual:,.6g} = "
                        f"[{_side_calc(num_src, num_val, window, num_flow, _months_used(num_flow), num_deemed)}] / "
                        f"[{_side_calc(den_src, den_val, window, den_flow, _months_used(den_flow), den_deemed)}]; "
                        f"test: {primitive} {actual:,.6g} {op} {threshold:,.6g}")
                source_ref = (_side_refs(src_idx, model, spec_file,
                                         num_lineage, test_date, window, num_flow,
                                         num_deemed)
                              + _side_refs(src_idx, model, spec_file,
                                           den_lineage, test_date, window, den_flow,
                                           den_deemed))
            # Threshold provenance: the covenant's own spec section (threshold /
            # schedule, window, cure rights all live there).
            source_ref.append(src_idx.ref(spec_file, section=f"covenants.{cid}",
                                          upstream_at=cov.get("agreement_ref")))
            calc += (f" (threshold per covenant-spec covenants.{cid}"
                     + (f", schedule effective {eff}" if eff else "") + ")")

            status, headroom_abs, headroom_pct = evaluate(
                primitive, actual, threshold, band)

            # Certified vs model: BOTH sides are computed and reported -- the
            # borrower-certified actual (transcribed from the compliance
            # certificate) and our model reconstruction each get a status and
            # headroom; the delta is checked against a tight, primitive-aware
            # tolerance, and a pass/fail disagreement is raised as an exception.
            cert_value = _cert_value(certified, spec, cid, test_date, unit)
            cert_status = cert_headroom_abs = cert_headroom_pct = None
            cert_agrees = None
            if cert_value is not None:
                cert_status, cert_headroom_abs, cert_headroom_pct = evaluate(
                    primitive, cert_value, threshold, band)
                tol = cert_tolerance(cov, primitive, threshold, unit, spec)
                cert_agrees = abs(cert_value - actual) <= tol
                if not cert_agrees:
                    notes.append(
                        f"certified {cert_value:g} vs model {actual:g} "
                        f"(delta {actual - cert_value:+g}, tolerance {tol:g})")
                    # The delta note's grounding: each side's per-row windowed
                    # values, so the write-up attributes the gap to the
                    # component that carries it instead of guessing.
                    sides = ((("metric", num_lineage, num_flow),)
                             if primitive == "min_level" else
                             (("numerator", num_lineage, num_flow),
                              ("denominator", den_lineage, den_flow)))
                    for side_name, lineage, flow in sides:
                        comp = _component_note(model, lineage, flow, window,
                                               idx, side_name)
                        if comp:
                            notes.append(comp)
                # The certified side's own provenance: transcribed, never computed.
                source_ref.append(src_idx.ref(
                    certified_file or "compliance-certificate",
                    section=f"values.{cid}.{test_date}",
                    kind="certified-transcription"))

            rec = {
                "id": cid,
                "name": name,
                "primitive": primitive,
                "test_date": test_date,
                "window": window,
                "required": round(threshold, 6),
                "actual": round(actual, 6),
                "headroom_abs": round(headroom_abs, 6),
                "headroom_pct": round(headroom_pct, 6) if headroom_pct is not None else None,
                "status": status,
                "source": source,
                "source_ref": source_ref,
                "calc": calc,
                "cert_value": cert_value,
                "cert_status": cert_status,
                "cert_headroom_abs": (round(cert_headroom_abs, 6)
                                      if cert_headroom_abs is not None else None),
                "cert_headroom_pct": (round(cert_headroom_pct, 6)
                                      if cert_headroom_pct is not None else None),
                "delta_model_vs_cert": (round(actual - cert_value, 6)
                                        if cert_value is not None else None),
                "cert_agrees": cert_agrees,
                "cert_tolerance_applied": (round(cert_tolerance(cov, primitive, threshold, unit, spec), 6)
                                          if cert_value is not None else None),
                "notes": "; ".join(notes) if notes else None,
            }
            if eff:
                rec["threshold_effective"] = eff
            if cov.get("either_or_group"):
                rec["either_or_group"] = cov["either_or_group"]
            results.append(rec)

            if cert_status is not None and cert_status != status:
                exceptions.append({
                    "covenant": cid, "name": name, "test_date": test_date,
                    "type": "cert_model_status_divergence",
                    "model_status": status, "cert_status": cert_status,
                    "model_actual": round(actual, 6),
                    "cert_actual": round(cert_value, 6),
                    "detail": ("certified and model reconstructions disagree on the "
                               "pass/at-risk/breach call -- reconcile before reporting"),
                })

            if status == "breach":
                exceptions.append({
                    "covenant": cid, "name": name, "test_date": test_date,
                    "status": "breach", "required": round(threshold, 2),
                    "actual": round(actual, 2),
                    "has_cure_rights": names_a_cure(cov.get("cure_rights")),
                    "agreement_ref": cov.get("agreement_ref"),
                })
            elif status == "at_risk":
                exceptions.append({
                    "covenant": cid, "name": name, "test_date": test_date,
                    "status": "at_risk", "required": round(threshold, 2),
                    "actual": round(actual, 2),
                })

    # ------------------------------------------------------------------
    # Groups: resolve each either/or ONCE, here, so the memo, the archive and
    # any portfolio reader all get the same answer. Before this, the group
    # existed only as a tag plus prose, every reader re-derived it, and a leg
    # that missed its own level was reported as a covenant breach.
    #
    # A leg's own row and its own exception both stay -- the leg really did
    # miss its level, and that is worth seeing. What changes is that each now
    # carries the group's resolved status, so nothing has to infer it.
    # ------------------------------------------------------------------
    groups = read_groups(spec)
    for gid, g in sorted(groups.items()):
        by_date = {}
        for r in results:
            if r.get("either_or_group") == gid:
                by_date.setdefault(r["test_date"], {})[r["id"]] = r
        for test_date in sorted(by_date):
            legs = by_date[test_date]
            statuses = [legs.get(m, {}).get("status") for m in g["members"]]
            reasons = [legs.get(m, {}).get("reason") for m in g["members"]]
            status, only_at_risk = resolve_group(g["rule"], statuses, reasons)
            # A rule this engine cannot apply still gets a row, reported as the
            # unknown it is. Dropping the group here published each leg's miss as
            # a covenant breach on a credit the group may hold compliant, and the
            # spec gate reads the rule only on a top-level either_or_groups block.
            unknown_rule = status is None
            if unknown_rule:
                status = "indeterminate"
            # The group's headroom is the best SATISFYING leg's: that is the room the
            # group actually has, and it is what "within 10% of breaching" must read
            # for a deal whose covenant IS the group. A leg that missed is not a
            # source of room. Taking the max over every leg made that mistake
            # reachable: a satisfying leg tested against a threshold of zero has a
            # null headroom_pct (there is no percentage of zero), so the max fell
            # through to a breached leg and handed a PASSING group that miss's
            # negative headroom, with headroom_from_leg pointing at it.
            #
            # On a breach no leg is satisfied, so there the best of the misses IS the
            # answer -- the leg nearest to compliance.
            satisfying = {"pass", "at_risk"}
            sources = [m for m in g["members"]
                       if m in legs and legs[m].get("headroom_pct") is not None
                       and (status not in satisfying or legs[m].get("status") in satisfying)]
            best_leg = max(sources, key=lambda m: legs[m]["headroom_pct"], default=None)
            grec = {
                "id": gid,
                "name": g["name"],
                "kind": "either_or_group",
                "rule": g["rule"],
                "test_date": test_date,
                "status": status,
                "headroom_pct": (round(legs[best_leg]["headroom_pct"], 6)
                                 if best_leg else None),
                "headroom_from_leg": best_leg,
                "members": list(g["members"]),
                "legs": [{"id": m,
                          "status": legs.get(m, {}).get("status"),
                          "required": legs.get(m, {}).get("required"),
                          "actual": legs.get(m, {}).get("actual")}
                         for m in g["members"]],
                "satisfied_only_by_at_risk_leg": only_at_risk,
                # Either way the group was declared, its refs carry the hop to
                # the signed agreement — a group tagged on its member
                # covenants reaches it through each member's own agreement_ref.
                "source_ref": [src_idx.ref(spec_file, section=f"either_or_groups.{gid}",
                                           upstream_at=g.get("agreement_ref"))]
                              if g["declared_by"] == "either_or_groups" else
                              [src_idx.ref(spec_file, section=f"covenants.{m}",
                                           upstream_at=(cov_by_id.get(m) or {}).get(
                                               "agreement_ref") or g.get("agreement_ref"))
                               for m in g["members"]],
                "agreement_ref": g.get("agreement_ref"),
                "calc": (f"group {gid} resolved by rule '{g['rule']}' over legs "
                         + ", ".join(f"{m}={legs.get(m, {}).get('status') or 'no result'}"
                                     for m in g["members"])
                         + f" -> {status}"
                         + (" (held only by an at-risk leg)" if only_at_risk else "")),
            }
            if status == "not_evaluable":
                # Every leg was taken out of testing this period, so the group row
                # says which kind of absence this is and asserts no verdict, exactly
                # as a leg row does.
                grec["reason"] = next((r for r in reasons if r in NOT_TESTED_REASONS),
                                      "gap")
                grec["required"] = grec["actual"] = None
                _flag_not_evaluable(
                    exceptions, seen_not_evaluable, gid, grec["reason"],
                    f"{g['name']} is not tested for one or more periods: every leg's "
                    f"threshold schedule takes those periods out of testing. They are "
                    f"reported as not evaluable, not as a pass or breach.", test_date)
            if g.get("rule_plain"):
                grec["rule_plain"] = g["rule_plain"]
            results.append(grec)

            # Carry the resolved answer onto the legs and their exceptions.
            for m in g["members"]:
                if m in legs:
                    legs[m]["group_status"] = status
            for e in exceptions:
                if e.get("covenant") in g["members"] and e.get("test_date") == test_date:
                    e["either_or_group"] = gid
                    e["group_status"] = status
                    # The note describes a LEG THAT MISSED. Applied to every
                    # exception it mislabels the ones that did not: an at-risk leg
                    # met its level and is only inside its warning band, and a
                    # certified-vs-model divergence is a disagreement about a
                    # figure, not a miss at all.
                    if status != "pass":
                        continue
                    if e.get("status") == "breach":
                        e["group_note"] = (
                            f"{g['name']} is satisfied at {test_date} by another leg -- "
                            "this row is a leg missing its own level, not a covenant breach")
                    elif e.get("status") == "at_risk":
                        e["group_note"] = (
                            f"{g['name']} is satisfied at {test_date} -- this leg met its "
                            "own level and sits inside its warning band, so it is neither "
                            "a miss nor a covenant breach")

            if status == "breach":
                exceptions.append({
                    "covenant": gid, "name": g["name"], "test_date": test_date,
                    "kind": "either_or_group", "status": "breach",
                    "members": list(g["members"]),
                    "detail": "every leg of this either/or failed",
                    "agreement_ref": g.get("agreement_ref"),
                })
            elif only_at_risk:
                exceptions.append({
                    "covenant": gid, "name": g["name"], "test_date": test_date,
                    "kind": "either_or_group", "status": "at_risk",
                    "members": list(g["members"]),
                    "detail": ("satisfied, but only by a leg that is itself at risk -- "
                               "one more month of slippage breaches the group"),
                })
            elif status == "indeterminate":
                # Unknown is a finding. Without this the row exists but nothing
                # points at it, and a reader filtering pass/at-risk/breach reads
                # "no answer" as "no problem".
                exceptions.append({
                    "covenant": gid, "name": g["name"], "test_date": test_date,
                    "kind": "either_or_group", "status": "indeterminate",
                    "members": list(g["members"]),
                    "detail": (f"this either/or is declared with rule "
                               f"'{g['rule']}', which this engine cannot apply, so "
                               f"compliance at this date is unknown -- correct the "
                               f"rule in the covenant spec and rerun"
                               if unknown_rule else
                               "compliance at this date is unknown: no leg is "
                               "satisfied and at least one could not be evaluated, "
                               "so this is neither a pass nor a breach"),
                    "legs": [{"id": m, "status": legs.get(m, {}).get("status")}
                             for m in g["members"]],
                    "agreement_ref": g.get("agreement_ref"),
                })

    blockers = reconciliation_blockers(results)
    for b in blockers:
        exceptions.append({
            "covenant": b["covenant"], "test_date": b["test_date"],
            "type": "reconciliation_required",
            "detail": (f"certified {b['cert_actual']:g} vs model {b['model_actual']:g} "
                       f"({b['trigger']}) -- the verdict is withheld until the basis is settled"),
        })

    return ml.conclusion(
        module="covenant-compliance",
        period=model.month_ends,   # normalized to the latest month's ISO YYYY-MM stamp
        results=results,
        exceptions=exceptions,
        source_index=src_idx,
        unit=unit,
        engine=("spec-driven v5 (per-figure source refs + calc records; "
                "explicit flow/spot basis; certified-vs-model dual columns "
                "with per-component delta decomposition; dollar thresholds "
                "put on the model's declared unit; ledger-proxy flag on "
                "coverage covenants without a debt-service schedule)"),
        borrower=borrower_name or spec.get("borrower"),
        # The gate. True means at least one covenant's certified and model
        # figures are too far apart to publish a verdict on; monthly-monitor
        # stops rather than rendering one. An explicit override is recorded
        # here too, so a published verdict always says which it was.
        reconciliation_required=bool(blockers) and not allow_unreconciled,
        reconciliation_blockers=blockers,
        reconciliation_override=bool(blockers) and allow_unreconciled,
        spec_agreement_ref=spec.get("agreement"),
        # The cure terms named ONCE per covenant that actually flagged, not
        # copied onto every breach date. They are the credit
        # agreement's words and live in covenant-spec.json; a breach points at
        # them by covenant id. Example Borrower's June output carried one covenant's cure
        # paragraph twelve times, once per test date.
        # Only covenants that grant one: a covenant whose terms say "none" is
        # not a cure, and listing it here reads as though there were time to fix.
        cure_rights={c["id"]: c["cure_rights"]
                     for c in (spec.get("covenants") or [])
                     if c.get("id") and names_a_cure(c.get("cure_rights"))
                     and any(e.get("covenant") == c.get("id") for e in exceptions)},
        model_month_ends=model.month_ends,
        certified_source=certified.get("source"),
        certified_period=certified.get("period"),
        # The transcriber's own notes and action items travel INTO the archived
        # output. Left in the certified-values file they reach nobody:
        # A certificate can restate an earlier period and require a corrected filing;
        # the archived answer must carry that action item.
        transcription_notes=certified.get("transcription_notes"),
        action_items=certified.get("action_items"),
        methodology=("Company-prepared/unaudited model; EBITDA taken from the model's single "
                     "pinned EBITDA bridge row (not recomputed). Thresholds and definitions per "
                     "the standardized covenant-spec; each definition's flow/spot basis is "
                     "explicit in the spec. cert_* fields are the borrower's own certified "
                     "actuals transcribed from the compliance certificate -- reported alongside "
                     "the model reconstruction, never merged with it."),
    )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Spec-driven covenant compliance")
    ap.add_argument("--borrower-config")
    ap.add_argument("--spec")
    ap.add_argument("--model")
    ap.add_argument("--certified",
                    help="certified-values JSON transcribed from the period's "
                         "compliance certificate by the covenant subagent "
                         "(optional; see the module spec for the shape)")
    ap.add_argument("--allow-unreconciled", action="store_true",
                    help="publish covenant verdicts even where the certified and model "
                         "figures diverge too far to reconcile. The override is recorded "
                         "on the output; without it the run stops and asks which basis is right.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id",
                    help="the run this output belongs to, minted once by the "
                         "orchestrator for the whole run (or set CREDIT_MONITOR_RUN_ID)")
    args = ap.parse_args()
    if args.run_id:
        os.environ["CREDIT_MONITOR_RUN_ID"] = args.run_id   # run_stamp() reads it

    spec_path = args.spec
    model_path = args.model
    borrower = None
    cfg = None
    # Whether this borrower's contractual debt-service schedule exists. None
    # until a borrower-config is in play — a direct --spec/--model caller has
    # no path contract to read, and unknown is not the same claim as absent.
    debt_service = None
    # spec_given: the AS-CONFIGURED path string (pre-sandbox-resolution) that
    # source refs record, so the downstream artifact can join store hyperlinks.
    spec_given = args.spec

    if args.borrower_config:
        cfg = ml.load_config(args.borrower_config)
        borrower = cfg.get("borrower_name")
        paths = cfg.get("paths", {})
        if not model_path:
            model_path = paths.get("model_output_path")
        if not spec_path:
            sp = paths.get("covenant_spec_path")
            if sp:
                sp_resolved = ml.resolve_path(sp)
                cand = os.path.join(os.path.dirname(sp_resolved),
                                    "covenant-spec.standardized.json")
                if os.path.exists(cand):
                    spec_path = cand
                    # same substitution on the configured string (separator-agnostic)
                    spec_given = re.sub(r"[^\\/]+$",
                                        "covenant-spec.standardized.json", str(sp))
                else:
                    spec_path = sp
                    spec_given = sp
        # With a config in play, the schedule's absence is a fact worth
        # flagging (fixed charges on ledger proxy), not an unknown. A store
        # that cannot be reached right now stays unknown — unreachable is not
        # absent.
        ds = paths.get("debt_service_schedule_path")
        debt_service = False
        if ds:
            try:
                debt_service = os.path.exists(ml.resolve_path(ds))
            except (ml.ConfigError, OSError):
                debt_service = None

    if not spec_path or not model_path:
        ap.error("could not resolve --spec and --model (supply them or a borrower-config)")

    # Input surface (spec / model / certified-values loads) is guarded: a known
    # bad input writes the standard skip record instead of a traceback, matching
    # the sibling engines and orchestration.md's skip contract. compute() is
    # deliberately OUTSIDE the guard -- an engine bug must crash loudly.
    try:
        spec = ml.load_config(spec_path)
        model = ml.open_model(model_path)
        certified = load_certified_values(args.certified)
        # Which unit this model's figures are in. Undeclared, this is a skip with
        # a plain reason -- never a comparison of dollars against thousands.
        unit = ml.reporting_unit(cfg)
    except (ValueError, OSError) as e:
        out = ml.skip_record("covenant-compliance", e, borrower=borrower)
    else:
        out = compute(model, spec, unit, borrower_name=borrower, certified=certified,
                      allow_unreconciled=args.allow_unreconciled,
                      spec_file=spec_given, certified_file=args.certified,
                      agreement_file=((cfg or {}).get("paths") or {}).get(
                          "credit_agreement_path"),
                      reporting_folder=((cfg or {}).get("paths") or {}).get(
                          "reporting_packages_folder"),
                      debt_service=debt_service,
                      compliance_certificate_folder=((cfg or {}).get("paths") or {}).get(
                          "compliance_certificate_folder"))

    out_path = ml.write_output(out, args.out)
    # The figures become rows beside the output, so a question that spans the
    # book can reach them without re-deriving this file's shape.
    rows_emit.write_rows(out, out_path, deal_name=borrower,
                         declared=rl.read_covenant_spec(spec_path))
    if out.get("reconciliation_required"):
        print(f"Wrote {out_path}: RECONCILIATION REQUIRED -- "
              f"{len(out['reconciliation_blockers'])} covenant(s) where the borrower's "
              f"certified figure and ours are too far apart to publish a verdict:")
        for b in out["reconciliation_blockers"]:
            print(f"  - {b['covenant']} {b['test_date']}: certified {b['cert_actual']:g} "
                  f"vs model {b['model_actual']:g} ({b['trigger']})")
        print("  Settle which basis is right before rendering a memo "
              "(--allow-unreconciled publishes anyway and records the override).")
    elif out.get("skipped"):
        print(f"Wrote {out_path}: SKIPPED (not computed) -- {out['reason']}")
    else:
        print(f"Wrote {out_path}: {len(out['results'])} results, "
              f"{len(out['exceptions'])} exceptions")


if __name__ == "__main__":
    main()
