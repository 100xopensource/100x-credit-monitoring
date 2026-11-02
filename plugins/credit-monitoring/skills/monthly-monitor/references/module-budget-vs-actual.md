# Module spec — Budget vs. Actual (BvA)  (FAN-OUT TARGET)

Engine: `budget_vs_actual.py`. Depends on a PINNED budget (+ budget-map.json) + build-monitoring-model.

## Units

Actuals and budget both come off files on the borrower's own unit, so variances are on that unit. The
materiality bar is OURS and stated in dollars ($50,000), so it is put on the borrower's unit before
anything is compared and printed in dollars in `materiality_rule`. Unconverted on a model in
thousands the bar was $50,000,000: a 20% revenue overrun stopped being material and the flag list came
back empty while the output still printed the rule as "50,000". The unit comes from `model.scale` +
`model.currency` in borrower-config (required) and rides on the output's envelope.

## Purpose
Compare actuals (from the model) to the approved budget for the period and YTD; explain material variances.

## Inputs (paths + config from borrower-config.json)
- model_output_path (actuals) — read via monitor_lib.
- budget_path + the pinned tab/version (budget_version_note), extracted through **budget-map.json**
  (`paths.budget_map_path`; see `config/budget-map.template.json`): file, sheet, period→column,
  metric→row, opex groups.
- `model_lines` — canonical metric → the borrower's model row labels (revenue, ebitda_bridge;
  optional gross_profit, financing_cost). A model with no gross-profit row still compares revenue,
  EBITDA and opex; its gross-profit rows read n/a on the actual side.
- `opex_groups` — display name → model row label; keys align 1:1 with the budget-map opex_groups.
- `comparability_items` — OPTIONAL one-time items to also show ex-item (empty list = none).
- `ebitda.addback_financing_cost` — whether budget EBITDA adds the financing_cost line back.
- `analysis_notes` — OPTIONAL free-text (e.g. a COA reconciliation) appended to the notes.

## Method
1. Period + YTD: actual vs. budget for Revenue, Gross Profit/Margin, EBITDA (actual from the model's
   single EBITDA bridge row, the same definition covenant-compliance reads; plan from the rows that
   build it, or from the plan's own EBITDA row when the map names `metric_rows.ebitda`, one or the
   other, never both), key opex.
   A line the plan does not have is no plan: its budget, variance and favorable flag are null, never
   $0, and the notes say so. Gross profit and opex rows drop only when neither side has the line.
   YTD starts at the later of January and the plan's first month, then trims to the months the model
   carries; both sides sum the same months, and a short YTD leads the notes with why. An analyst's own
   `--ytd-start` wins, and one the model or the plan cannot cover is a skip, never narrowed.
2. Variance $ and %; flag material (default |var%| ≥ 10% AND |var$| ≥ $50k). Attribute drivers from line detail.
3. Comparability is DATA-DRIVEN: each configured `comparability_items` entry is stripped from
   as-reported GP/EBITDA to show an ex-item comparable. With no items configured, only as-reported
   lines are produced. COA differences between budget and actuals are reconciled in the budget-map.

## Output contract (return ONLY the engine's JSON)
`results` carries one record per metric per `scope` (`latest` / `YTD`) with `actual`, `budget`,
`var_abs`, `var_pct`, `favorable`, `material` and an optional `note`; the envelope adds
`comparability_notes` and `comparability_items_ytd`. `period` is the ISO `YYYY-MM` stamp (normalized by
`monitor_lib.conclusion`). The engine defines the exact shape; read it there.

Every figure is on the MODEL's unit and the envelope says which (`reporting_unit`), except the margin
records, which carry `"unit": "pct"` — a margin and its variance are percentage points, so a row of one
carries no money scale. That is why the field is there: a portfolio question puts money on one footing
by multiplying by the scale, and a margin multiplied by a thousand is a 350x gross margin.

Every result carries `source_ref` indexing into `source_files` — the model cells the actual came from,
the budget cells the budget came from, AND the budget-map section that decided which budget rows match —
plus a plain-English `calc` note saying how the figure was arrived at. On a known bad input (unmapped
budget month, missing config surface) the engine writes a skip record
(`{"skipped": true, "reason": "..."}`) instead of a traceback. **Gate the output with
`validate_output.py` before archiving.**

## Source tier & discipline
Actuals from the model (audited-first hierarchy already applied there). Budget is management-prepared — label it.

## Definition of Done
- Period + YTD BvA for the key metrics; material variances flagged with drivers; COA mapping documented
  in the budget-map; comparability/restatement caveats noted. Numbers reproduce on re-run.
