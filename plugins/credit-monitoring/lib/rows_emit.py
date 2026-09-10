"""rows_emit.py — the one place a module's output becomes rows.

Every question that spans the book is answered from four row shapes:
`test`, `series`, `composition`, and `judgment`. This file holds
the mapping from a module's own output to those rows, once, and it is called from
both ends of the same contract:

  * **By each engine, as it writes its output** (`attach`). The rows land in the
    archived file, carrying what only the engine knows — the unit its figures are
    in, its own run stamp, the exact refs it built. A module that emits its rows
    is queryable the day it ships, with no reader written for it anywhere else.
  * **By the translator, for an output that carries none** (`rows_for`). That is
    every file archived before engines emitted their own, and the
    certified-values file, which an analyst transcribes rather than an engine
    computing.

The two callers see the same rows because they run the same code. What differs is
only what each one knows:

  a module's output says     module, period, run stamp, engine version,
                             source files, the unit its figures are in
  the STORE says             which borrower's folder this is, which registered
                             deal that folder answers to, whether the file was
                             archived

So an engine fills the first set and leaves the second unset, and the translator
completes it from the registry when it collects the rows (`build_rows.collect`).
A row is never guessed at from a filename in either direction.

Stdlib only. Nothing here computes a figure or changes one.
"""
import json
import os
import re

try:
    import rows_lib as rl
except ModuleNotFoundError:  # pragma: no cover - package import path
    from . import rows_lib as rl

# The `unit` a three-statement figure carries is what KIND of quantity it is,
# not the borrower's reporting unit. These are the money kinds; a ratio, a
# multiple ("x") or a number of days has no scale and no currency.
MONEY_KINDS = ("usd", "$", "dollars", "money")

# The three-statement engine's deep `series` block, whose keys are fixed in that
# engine: which statement each line sits on, and whether it is money. The `results`
# figures beside it declare their own kind; these do not, so they are named here.
# An unmapped key keeps no statement and is treated as money -- the shape every
# key here but one has. Adding a ratio series means adding it to this map.
DEEP_SERIES = {"revenue": ("is", True), "gross_margin": ("is", False),
               "ebitda": ("is", True), "cash": ("bs", True),
               "total_equity": ("bs", True)}


# ---------------------------------------------------------------------------
# shape 1 — test
# ---------------------------------------------------------------------------
def read_covenant(out, ctx, rows, core):
    for result in out.get("results") or []:
        if not isinstance(result, dict):
            continue
        # A group row carries the group's own id; a leg points back at it.
        is_group = (result.get("kind") == "either_or_group"
                    or bool(result.get("members") or result.get("legs")))
        window, period_type = rl.window_type(result.get("window"))
        test_id = result.get("id")
        # A leverage or coverage covenant's actual and threshold are a multiple,
        # not money, so the borrower's scale does not describe them -- the same
        # rule covenant_compliance.threshold_stated_in applies to the threshold.
        ratio = result.get("primitive") in rl.RATIO_PRIMITIVES
        row = rl.row(
            "test", ctx,
            **(rl.NOT_MONEY if ratio else {}),
            test_id=test_id,
            test_kind="covenant",
            metric=rl.core_metric(test_id, core) or test_id,
            label_verbatim=result.get("name"),
            primitive=result.get("primitive"),
            window=window,
            period=rl.iso_month(result.get("test_date")) or ctx["period"],
            period_type=period_type,
            value=rl.num(result.get("actual")),
            threshold_value=rl.num(result.get("required")),
            # a threshold that resolved off a schedule is not a scalar
            threshold_kind=("schedule" if result.get("threshold_effective")
                            else "scalar" if rl.num(result.get("required")) is not None
                            else "unresolved"),
            threshold_effective=result.get("threshold_effective"),
            status=result.get("status"),
            headroom_abs=rl.num(result.get("headroom_abs")),
            headroom_pct=rl.num(result.get("headroom_pct")),
            group_id=(test_id if is_group else result.get("either_or_group")),
            is_group_row=is_group,
            group_rule=result.get("rule"),
            group_status=result.get("group_status") or (result.get("status") if is_group else None),
            group_members=result.get("members"),
            group_leg_statuses=(", ".join(
                f"{leg.get('id')}={leg.get('status')}"
                for leg in result.get("legs") or [] if isinstance(leg, dict)) or None),
            held_by_at_risk_leg=result.get("satisfied_only_by_at_risk_leg"),
            cert_value=rl.num(result.get("cert_value")),
            cert_status=result.get("cert_status"),
            cert_agrees=result.get("cert_agrees"),
            delta_model_vs_cert=rl.num(result.get("delta_model_vs_cert")),
            agreement_ref=result.get("agreement_ref"),
            source_ref=result.get("source_ref"),
            fidelity=rl.fidelity_of(result.get("name"), result.get("notes"),
                                    result.get("calc"), result.get("reason"),
                                    out.get("methodology"), out.get("basis")),
        )
        rows.test.append(rl.apply_declared(row, ctx.get("declared"), test_id))

        # a model/certificate disagreement is a judgment, not a number
        if result.get("cert_agrees") is False:
            rows.add("judgment", ctx, kind="divergence", subject=f"test:{test_id}",
                     period=rl.iso_month(result.get("test_date")) or ctx["period"],
                     call="the model disagrees with the certified figure",
                     reason=(f"model {result.get('actual')} against certified "
                             f"{result.get('cert_value')} "
                             f"(delta {result.get('delta_model_vs_cert')})"),
                     value=rl.num(result.get("delta_model_vs_cert")),
                     source_ref=result.get("source_ref"), decided_by="engine")


def read_budget(out, ctx, rows, core):
    for result in out.get("results") or []:
        if not isinstance(result, dict):
            continue
        metric = result.get("metric")
        scope = str(result.get("scope") or "latest").lower()
        # This module reports margins alongside the money lines, and a margin's
        # actual, budget and variance are all percentage points. The engine says
        # which kind each result is, the same way the three-statement one does; an
        # undeclared kind is money, which is what every money line here is.
        money = str(result.get("unit") or "usd").lower() in MONEY_KINDS
        rows.add("test", ctx,
                 test_id=f"budget:{metric}",
                 **({} if money else rl.NOT_MONEY),
                 test_kind="budget_variance",
                 metric=rl.core_metric(metric, core) or metric,
                 label_verbatim=metric,
                 period_type="ytd" if "ytd" in scope else "month",
                 value=rl.num(result.get("actual")),
                 threshold_value=rl.num(result.get("budget")),
                 threshold_kind="declared_budget",
                 threshold_source=out.get("budget_source"),
                 status="material" if result.get("material") else "immaterial",
                 headroom_abs=rl.num(result.get("var_abs")),
                 headroom_pct=rl.num(result.get("var_pct")),
                 favorable=result.get("favorable"),
                 source_ref=result.get("source_ref"),
                 fidelity=rl.fidelity_of(metric, result.get("calc"),
                                         out.get("materiality_rule"),
                                         out.get("methodology"), out.get("basis")))


# ---------------------------------------------------------------------------
# shape 2 — series, and shape 3 — composition
# ---------------------------------------------------------------------------
def read_three_statement(out, ctx, rows, core):
    # the deep monthly series — the only place a multi-year history exists
    for metric, points in (out.get("series") or {}).items():
        if not isinstance(points, list):
            continue
        statement, is_money = DEEP_SERIES.get(metric, (None, True))
        for point in points:
            if not isinstance(point, dict):
                continue
            period = rl.iso_month(point.get("m"))
            if period is None or rl.num(point.get("v")) is None:
                continue
            rows.add("series", ctx, period=period,
                     metric=rl.core_metric(metric, core) or metric,
                     label_verbatim=metric, statement=statement, basis="actual",
                     value=rl.num(point.get("v")),
                     **({} if is_money else {"scale": None, "currency": None}),
                     fidelity=rl.fidelity_of(out.get("methodology"), out.get("basis")))

    for result in out.get("results") or []:
        if not isinstance(result, dict):
            continue
        metric = result.get("metric")
        shared = dict(metric=rl.core_metric(metric, core) or metric,
                      label_verbatim=metric,
                      statement=rl.statement_of(result.get("section")),
                      section_verbatim=result.get("section"),
                      source_ref=result.get("source_ref"),
                      fidelity=rl.fidelity_of(metric, result.get("calc"),
                                              out.get("methodology"), out.get("basis")))
        # This module says what KIND of quantity each figure is -- money, a ratio,
        # a multiple, a number of days. A money figure takes the borrower's
        # declared unit off the envelope; anything else has no scale and no
        # currency, and a margin of 0.35 read as $0.35 of anything would be a lie.
        if str(result.get("unit") or "usd").lower() not in MONEY_KINDS:
            shared.update(scale=None, currency=None)
        if rl.num(result.get("latest")) is not None:
            rows.add("series", ctx, basis="actual",
                     value=rl.num(result["latest"]), **shared)
        if rl.num(result.get("ytd")) is not None:
            rows.add("series", ctx, basis="actual", period_type="ytd",
                     value=rl.num(result["ytd"]), **shared)

    liquidity = out.get("liquidity") or {}
    for key in ("cash", "burn_1m", "burn_3m_avg", "months_to_floor_1m", "cash_floor"):
        if rl.num(liquidity.get(key)) is None:
            continue
        # months of runway is a count of months, not money: given the borrower's
        # scale, seven months of cash left would read as $7,000.
        money = not key.startswith("months_to_floor")
        rows.add("series", ctx, metric=rl.core_metric(key, core) or key,
                 label_verbatim=key, statement="kpi", basis="actual",
                 **({} if money else {"scale": None, "currency": None}),
                 value=rl.num(liquidity[key]), source_ref=liquidity.get("source_ref"),
                 fidelity=rl.fidelity_of(liquidity.get("floor_source"),
                                         out.get("methodology"), out.get("basis")))

    # Compositions. `source_view` matters: this module reports the same
    # income-statement line through both `opex_composition` and `line_read`, so a
    # sum over the part's name alone counts it twice — payroll-to-revenue read
    # 7.1% that way against a true 3.5%. A query picks one view.
    for label, block in (out.get("opex_composition") or {}).items():
        if not isinstance(block, dict) or rl.num(block.get("latest")) is None:
            continue
        rows.add("composition", ctx, parent_metric="opex",
                 source_view="opex_composition",
                 part_label=rl.core_metric(label, core) or label,
                 part_verbatim=label, value=rl.num(block["latest"]),
                 source_ref=block.get("source_ref"),
                 # nobody has declared one yet, and the count of unclassified
                 # lines is the size of the declaration job
                 cost_behaviour="unclassified",
                 fidelity=rl.fidelity_of(block.get("calc"), out.get("basis")))

    for line in out.get("revenue_mix") or []:
        if not isinstance(line, dict) or rl.num(line.get("latest")) is None:
            continue
        rows.add("composition", ctx, parent_metric="revenue",
                 source_view="revenue_mix", part_label=line.get("line"),
                 part_verbatim=line.get("line"), value=rl.num(line["latest"]),
                 share_of_parent=rl.num(line.get("share")),
                 source_ref=line.get("source_ref"),
                 fidelity=rl.fidelity_of(line.get("calc"), out.get("basis")))

    for section, block in (out.get("line_read") or {}).items():
        if not isinstance(block, dict):
            continue
        for line in block.get("lines") or []:
            if not isinstance(line, dict) or rl.num(line.get("latest")) is None:
                continue
            rows.add("composition", ctx,
                     parent_metric=f"statement:{rl.statement_of(section) or 'unmapped'}",
                     section_verbatim=section, source_view="line_read",
                     part_label=rl.core_metric(line.get("line"), core) or line.get("line"),
                     part_verbatim=line.get("line"), value=rl.num(line["latest"]),
                     share_of_parent=rl.num(line.get("share")),
                     # cost behaviour belongs to the opex view alone: claimed here
                     # too, revenue and COGS would read as awaiting classification
                     flags=line.get("flags"),
                     fidelity=rl.fidelity_of(line.get("flags"), out.get("basis")))

    # what the engine suppressed, made queryable
    for key, kind in (("drift_suppressed", "reliability"),
                      ("ratios_suppressed", "reliability"),
                      ("lines_not_found", "coverage")):
        value = out.get(key)
        if not value:
            continue
        for item in (value if isinstance(value, list) else [value]):
            rows.add("judgment", ctx, kind=kind, subject=key,
                     call=f"{key.replace('_', ' ')} — not reported",
                     reason=item if isinstance(item, str) else json.dumps(item)[:600],
                     decided_by="engine")


def read_aging(out, ctx, rows, core):
    for result in out.get("results") or []:
        if not isinstance(result, dict):
            continue
        side = result.get("side")
        parent = "ar" if side == "AR" else "ap"
        latest = result.get("latest") or {}
        period = rl.iso_month(latest.get("month")) or ctx["period"]
        if rl.num(latest.get("total")) is not None:
            rows.add("series", ctx, period=period, metric=parent,
                     label_verbatim=f"{side} total", statement="bs", basis="actual",
                     value=rl.num(latest["total"]),
                     fidelity=rl.fidelity_of(out.get("note"), out.get("basis")))
        for bucket, block in (latest.get("bucket_distribution") or {}).items():
            if not isinstance(block, dict) or rl.num(block.get("amount")) is None:
                continue
            rows.add("composition", ctx, period=period, parent_metric=parent,
                     source_view="bucket_distribution", part_label=f"bucket:{bucket}",
                     part_verbatim=bucket, value=rl.num(block["amount"]),
                     share_of_parent=rl.num(block.get("pct_of_total")),
                     fidelity=rl.fidelity_of(out.get("note")))
        for party in latest.get("top5_concentration") or []:
            if not isinstance(party, dict) or rl.num(party.get("amount")) is None:
                continue
            rows.add("composition", ctx, period=period, parent_metric=parent,
                     source_view="top5_concentration", part_label="counterparty",
                     part_verbatim=party.get("name"), value=rl.num(party["amount"]),
                     share_of_parent=rl.num(party.get("pct_of_total")),
                     source_ref=party.get("source_ref"),
                     fidelity=rl.fidelity_of(party.get("calc"), out.get("note")))


def _rq_is_money(key):
    """Whether one of this engine's own result fields is a money amount.

    Unlike the three-statement block, this engine's fields are not a fixed list --
    it writes out every figure a section computed -- so the rule is its own naming
    convention rather than an enumeration. Today that separates the money fields
    (the ARR bridge's opening/new/churn/closing, indebtedness, arr_actual,
    breakeven_arr, deferred revenue) from the six that are not: grr_pct, nrr_pct,
    max_ratio, ratio_actual, ratio_post_atrisk_churn and
    ratio_actual_model_reconstruction. A field added later that is a share or a
    multiple is named the same way and is covered without anyone remembering to.
    """
    name = str(key).lower()
    return not (name.endswith("_pct") or "ratio" in name)


def read_revenue_quality(out, ctx, rows, core):
    for result in out.get("results") or []:
        if not isinstance(result, dict):
            continue
        section = result.get("section")
        fidelity = rl.fidelity_of(result.get("derivation_note"), result.get("basis"),
                                  out.get("methodology"), out.get("basis"))
        for key, value in result.items():
            if rl.num(value) is None or key == "foot_difference":
                continue
            label = f"{section}:{key}" if section else key
            rows.add("series", ctx, metric=rl.core_metric(key, core) or label,
                     **({} if _rq_is_money(key) else rl.NOT_MONEY),
                     label_verbatim=label, statement="kpi",
                     basis=rl.basis_of(result.get("basis")) or "actual",
                     basis_verbatim=result.get("basis"), value=rl.num(value),
                     source_ref=result.get("source_ref"), fidelity=fidelity)
        if result.get("foots") is False:
            rows.add("judgment", ctx, kind="reliability",
                     subject=f"revenue_quality:{section}",
                     call="the bridge does not foot",
                     reason=str(result.get("derivation_note"))[:600],
                     value=rl.num(result.get("foot_difference")), decided_by="engine")


def _transcribed(entry):
    """A transcribed certificate figure, as `(number, page, quote)`.

    A transcription is either the bare number or the number with WHERE on the
    certificate it was read from -- both are valid, and the covenant engine already
    reads both. Reading only the bare form dropped every located figure without
    saying so, which matters more each month: the module spec asks whoever
    transcribes to record the page and quote for every figure off a document, so
    the located form is the one a careful transcription produces.
    """
    if isinstance(entry, dict):
        return rl.num(entry.get("value")), entry.get("page"), entry.get("quote")
    return rl.num(entry), None, None


def read_certified(out, ctx, rows, core):
    """The borrower's own certified figures: series rows with basis='certified'.

    Also where the scale problem is most visible — whole dollars in some deals and
    thousands in others, with no field on either saying which.
    """
    values = out.get("values")
    if isinstance(values, dict) and values:
        for covenant, by_date in values.items():
            if not isinstance(by_date, dict):
                continue
            for when, entry in by_date.items():
                value, page, quote = _transcribed(entry)
                if value is None:
                    continue
                rows.add("series", ctx, period=rl.iso_month(when) or ctx["period"],
                         metric=covenant, label_verbatim=covenant, statement="kpi",
                         basis="certified", value=value,
                         source_ref=[{"document": str(out.get("source")),
                                      "page": page, "label": quote}],
                         fidelity=rl.fidelity_of(*(out.get("transcription_notes") or []),
                                                 out.get("note")))
    elif isinstance(values, dict):
        rows.add("judgment", ctx, kind="coverage", subject="certificate",
                 call="no certificate delivered",
                 reason=" ".join(out.get("transcription_notes") or [])[:900],
                 decided_by="engine")

    # a year-keyed file holds a month per top-level key
    for key, block in out.items():
        if not (rl.is_iso_month(key) and isinstance(block, dict)):
            continue
        for covenant, figures in block.items():
            if not isinstance(figures, dict):
                continue
            # the same two transcription shapes reach a year-keyed file
            result, page, quote = _transcribed(figures.get("result"))
            requirement, _p, _q = _transcribed(figures.get("requirement"))
            if result is None:
                continue
            rows.add("series", ctx, period=key, metric=covenant,
                     label_verbatim=covenant, statement="kpi", basis="certified",
                     value=result,
                     source_ref=[{"document": str(out.get("source")),
                                  "page": page, "label": quote}])
            if requirement is None:
                continue
            # The certificate states a level, never the direction of the test — a
            # max-ratio covenant passes BELOW its threshold. The direction is a
            # declaration, so it comes from the spec or the row goes out unscored.
            declared = ((ctx.get("declared") or {}).get("covenants") or {}).get(covenant, {})
            primitive = declared.get("primitive")
            status = headroom = None
            if primitive in ("min_level", "min_coverage"):
                status = "pass" if result >= requirement else "breach"
                headroom = result - requirement
            elif primitive == "max_ratio":
                status = "pass" if result <= requirement else "breach"
                headroom = requirement - result
            rows.add("test", ctx, period=key, test_id=f"certified:{covenant}",
                     **(rl.NOT_MONEY if primitive in rl.RATIO_PRIMITIVES else {}),
                     test_kind="certified_covenant", metric=covenant,
                     label_verbatim=covenant, primitive=primitive,
                     value=rl.num(result), threshold_value=rl.num(requirement),
                     threshold_kind="scalar", threshold_source=declared.get("declared_by"),
                     status=status, headroom_abs=headroom,
                     declared_by=declared.get("declared_by"),
                     unscored_reason=(None if status else
                                      "no direction is declared for this covenant, so "
                                      "whether passing is above or below the level is "
                                      "unknown"))

    for note in out.get("transcription_notes") or []:
        if re.search(r"discrepan|inconsist|no .*certificate|not deliver|misfiled",
                     str(note), re.I):
            rows.add("judgment", ctx, kind="reliability", subject="certificate",
                     call="the certificate was flagged by whoever transcribed it",
                     reason=str(note)[:900], decided_by="agent")


def read_exceptions(out, ctx, rows, core):
    """Every module's `exceptions` list, whatever else it holds."""
    for exception in out.get("exceptions") or []:
        if not isinstance(exception, dict):
            continue
        rows.add("judgment", ctx, kind="exception",
                 subject=(exception.get("covenant") or exception.get("metric")
                          or exception.get("name") or ctx["module"]),
                 period=rl.iso_month(exception.get("test_date")) or ctx["period"],
                 call=exception.get("flag") or exception.get("status") or "flagged",
                 severity=exception.get("severity"),
                 reason=(exception.get("detail") or exception.get("name") or "")[:900],
                 value=rl.num(exception.get("actual")), decided_by="engine",
                 fidelity=rl.fidelity_of(exception.get("detail"), exception.get("name")))

MAPPERS = {
    "covenant-compliance": read_covenant,
    "budget-vs-actual": read_budget,
    "three-statement-analysis": read_three_statement,
    "ap-ar-aging": read_aging,
    "revenue-quality": read_revenue_quality,
    "certified-values": read_certified,
}


# ---------------------------------------------------------------------------
# The two ways in
# ---------------------------------------------------------------------------
def unit_fields(out):
    """What every figure in this output is denominated in, off the envelope
    (`reporting_unit`, written by monitor_lib.conclusion).

    A row's `scale` is how many dollars its value stands for, so a portfolio
    question can put two borrowers on one footing without knowing either
    workbook. An output that does not declare it leaves the fields null rather
    than assuming dollars — an assumed scale is the bug this field exists to
    close.
    """
    unit = out.get("reporting_unit")
    if not isinstance(unit, dict):
        return {}
    scale = unit.get("scale")
    fields = {}
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        fields["scale"] = float(scale)
    if str(unit.get("currency") or "").strip():
        fields["currency"] = str(unit["currency"]).upper()
    if str(unit.get("verbatim") or unit.get("plain") or "").strip():
        fields["unit_verbatim"] = str(unit.get("verbatim") or unit["plain"])
    return fields


def context(out, deal_name=None, deal_id=None, store_folder=None, archived=True,
            declared=None, store_leaves=None, monitoring_store="store",
            period=None, module=None):
    """The row context an output can supply about itself.

    Callers pass the store facts they know; an engine knows none of them beyond
    the borrower's own name.
    """
    produced_by = out.get("produced_by")
    ctx = {
        "deal_id": deal_id,
        "deal_name": deal_name or out.get("borrower"),
        "store_folder": store_folder,
        "period": period or out.get("period"),
        "module": module or out.get("module"),
        "archived": archived,
        "run_id": out.get("run_id"),
        "run_id_from": out.get("run_id_from"),
        "produced_at": out.get("produced_at"),
        "produced_by": (json.dumps(produced_by) if isinstance(produced_by, dict)
                        else produced_by),
        "run_stamp_from": "the output's own run stamp" if out.get("run_id") else None,
        "engine_version": engine_version(out),
        "source_files": out.get("source_files"),
        "store_leaves": store_leaves if store_leaves is not None else rl.load_store_leaves(),
        "monitoring_store": monitoring_store,
        "declared": declared,
    }
    ctx.update(unit_fields(out))
    return ctx


def engine_version(out):
    """Which build wrote this output: the plugin version a current engine stamps,
    else the engine string an older one wrote, else nothing."""
    produced_by = out.get("produced_by")
    if isinstance(produced_by, dict) and produced_by.get("plugin_version"):
        return produced_by["plugin_version"]
    return out.get("engine")


def rows_for(out, ctx, core=None, rows=None):
    """Every row this output holds, into `rows` (a new rl.Rows if none given).

    A module with no mapper still gets its exceptions read, because an exception
    has the same shape whatever computed it — so a new module's flags reach a
    question before anyone writes a mapper for its figures.
    """
    rows = rows if rows is not None else rl.Rows()
    core = core if core is not None else rl.load_metric_core()
    mapper = MAPPERS.get(ctx.get("module"))
    if mapper is not None:
        mapper(out, ctx, rows, core)
    read_exceptions(out, ctx, rows, core)
    return rows


# The rows sit BESIDE the output, not inside it. Inside, they grew one covenant
# month from 86 KB to 540 KB -- the same figures written twice, in a file the memo
# pipeline and every reviewing agent read whole. Beside it, the output is exactly
# the size it always was and the rows are there for whoever wants them.
ROWS_SUFFIX = ".rows.json"


def _discard_rows(path):
    """Remove a rows file that no longer describes the output beside it."""
    try:
        os.remove(path)
    except OSError:
        pass


def write_rows(out, output_path, deal_name=None, declared=None):
    """Write this output's rows beside it, as `<output>.rows.json`. Returns the
    path written, or None when there was nothing to write.

    Called by an engine's CLI once its output is on disk: the figures become
    queryable straight away, carrying the unit and the run stamp the engine knew
    first-hand. A skip record has no figures, so it gets no rows file — and any
    rows an earlier run of that month left there are removed, because they
    describe an answer this output has replaced.

    NEVER RAISES. The output is already durably written by the time this is
    called, and rows are a derivation anything can rebuild: letting a rows
    failure escape would fail a module whose real work is on disk and correct.
    A rows file that could not be written is removed rather than left stale, the
    same contract `write_output`'s two sibling writes keep.

    The store facts stay unset — which folder, which registered borrower, whether
    the file was archived are properties of where the file ENDS UP, and the
    translator fills them in when it reads the store.
    """
    if not output_path:
        return None
    path = str(output_path) + ROWS_SUFFIX
    if not isinstance(out, dict) or out.get("skipped") is True:
        _discard_rows(path)
        return None
    tmp = path + ".tmp"
    try:
        ctx = context(out, deal_name=deal_name, declared=declared, archived=False)
        rows = rows_for(out, ctx)
        payload = {
            "module": out.get("module"),
            "period": out.get("period"),
            "run_id": out.get("run_id"),
            "produced_at": out.get("produced_at"),
            "reporting_unit": out.get("reporting_unit"),
            "counts": rows.counts(),
            "rows": {shape: getattr(rows, shape) for shape in rl.SHAPES},
            "_comment": ("The four row shapes of this one output, written by the engine "
                         "that computed it. A question that spans the book reads these; "
                         "the output beside it stays the record. Throw these away and "
                         "the translator rebuilds them."),
        }
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)      # atomic on one volume; safe on a synced folder
        return path
    except Exception as exc:       # noqa: BLE001 — a derivation never fails a module
        print(f"note: rows not written beside {os.path.basename(str(output_path))} "
              f"({type(exc).__name__}: {exc}); the translator will rebuild them")
        _discard_rows(tmp)
        _discard_rows(path)
        return None


def read_rows(output_path, out=None):
    """The rows an engine wrote beside this output — (rows, problem).

    `rows` is the four shapes as the engine wrote them, or None. `problem` is a
    plain sentence when a rows file was there and could not be used, for the
    reader to record as a coverage fact rather than swallow.

    Pass `out` — the output actually on disk — and the two are checked against
    each other. They are two files written at two moments, and the record is the
    output: a worker that fixes a gate failure by writing the output itself
    (which the module contract allows) regenerates no rows, so the file beside it
    still describes the answer it replaced. Trusting it would answer a portfolio
    question from figures nothing published.
    """
    path = str(output_path) + ROWS_SUFFIX
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError:
        return None, None
    except ValueError as exc:
        return None, f"its rows file is not readable JSON ({exc})"
    if not isinstance(payload, dict):
        return None, "its rows file holds no rows record"
    if isinstance(out, dict):
        for field in ("run_id", "period", "module"):
            if payload.get(field) != out.get(field):
                return None, (
                    f"its rows file was written for {field} "
                    f"{payload.get(field)!r}, but the output beside it says "
                    f"{out.get(field)!r} — the output has been rewritten since, "
                    f"so the rows were rebuilt from it")
    rows = payload.get("rows")
    if not isinstance(rows, dict):
        return None, "its rows file carries no rows"
    return rows, None
