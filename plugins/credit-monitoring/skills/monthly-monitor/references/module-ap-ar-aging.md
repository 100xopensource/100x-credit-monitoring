# Module spec — AP / AR Aging Analysis  (FAN-OUT TARGET)

Engine: `ap_ar_aging.py`. Standalone (uses the monthly aging files); reads BS/IS tie-out lines from the
model.

## Units — `aging.amount_scale`, and what the output says it is in

An aging export is often reported in RAW DOLLARS while the monitoring model reports in thousands. Set
`aging.amount_scale` in borrower-config to the divisor that brings the export onto the model's unit
(1000 for a raw-dollar export against a model in thousands; 1 when they already agree). **The engine
refuses to run without it once `model.scale` is not 1** — it writes the standard skip record naming the
field, because which unit an export is in cannot be read off the file, and the wrong guess publishes
receivables a thousand-fold out. On a model in whole dollars it stays optional and defaults to 1. The
engine applies it to every parsed amount BEFORE any tie-out, ratio or concentration is computed, so
DSO/DPO and the balance-sheet tie-out are on one unit. Left unset against a raw-dollar export, the
tie-out fails and DPO reads roughly a thousand times high. For a raw-dollar export this is the same
number as the model's own `model.scale`.

Every figure this module publishes is on the MODEL's unit, and the output says which on its envelope
(`reporting_unit`, from `model.scale` + `model.currency` — required, see the borrower-config template).
`aging.tie_tolerance` is on that same unit, so a default of 1.0 against a model in thousands is a
$1,000 tie; the note in the output states it rather than tightening it behind the analyst's back.

## Export shapes the parser handles

Header row detection first looks for a first cell reading Customer/Vendor alongside a Total column; when
that does not match it identifies the header by its COLUMNS instead — any row carrying a Total column
and two or more recognised aging buckets. That is what lets an export whose label column has a BLANK
header still parse. Bucket labels cover the parenthetical tags, explicit ranges, `Current`, `>90`/`91+`,
plus `At Date` (not yet due) and `Older`. The grand-total row is recognised as `Total`, `Total - Vendor`,
`Total - Customer`, `Aged Receivable` or `Aged Payable`. The `As of ...` caption is read from anywhere in
the top rows, not only the first cell.

## Purpose
Assess receivables/payables quality and liquidity stress: bucket aging, concentration, trend.

## You supply the aging files
The engine reads the files you hand it and does the arithmetic. Finding them is your job, because every
borrower's folder is shaped differently and you are the one who can look.

Open `reporting_packages_folder`, find the month being reported, and see which export is A/R and which
is A/P — read the title row or the column headings if the filenames are ambiguous. Do the same for the
prior month if it is there. Then run the engine with those paths:

```
python ap_ar_aging.py --borrower-config <borrower-config.json> --out <output .json> \
  --ar-latest <A/R export> --ap-latest <A/P export> \
  [--ar-prior <A/R export> --ap-prior <A/P export>] \
  --latest-month "May 2026" [--prior-month "Apr 2026"]
```

Pass each path **as the borrower-config writes it** (`source:...`) — the same string as in borrower-config, not a
`/sessions/…` path. That string is what the memo's figure links are built from.
To open an export yourself first, use the connected-folder path with Read (the file tools refuse a
`/sessions/…/mnt/…` string) and the sandbox path with bash — your agent contract's "Three path forms"
(`${CLAUDE_PLUGIN_ROOT}/agents/module-analyst.md`).

A file that has not arrived: leave the flag off. Without a prior month the trend is omitted;
without a latest A/R or A/P the module reports as not applicable this month and the run
carries on. Report which file was absent so the memo can say so.

## Config it reads (borrower-config.json)
- model_output_path (for AR/AP tie-out and DSO/DPO via revenue / operating COGS) — read via monitor_lib.
- `model_lines` — accounts_receivable, accounts_payable, revenue, operating_cogs (all required).
- `aging` block — OPTIONAL parse settings (defaults in the template): `file_format`, `dpo_basis`,
  `tie_tolerance`, `top_n`. Leave `file_format` at `auto` and the format is read off the file's own
  bytes, so a NetSuite export shipping SpreadsheetML XML as `.xls` still reads.

## Method
1. Parse current + prior month aging; normalize buckets (Current / 1-30 / 31-60 / 61-90 / 90+).
2. Concentration: top-N counterparties as % of AR (and AP); flag single-name concentration.
3. Trend: >90-day AR$ and %, DSO and DPO month-over-month; flag deterioration. Both days metrics
   multiply by that month's calendar days (`monitor_lib.days_in_month`) — the same basis the
   three-statement module uses, so the memo carries one DSO, not two.
4. Tie aging totals to the BS AR / AP balances in the model; flag mismatches.
   DPO uses operating COGS (per `aging.dpo_basis`), which avoids distortion when reported Cost of
   Sales carries one-time credits (e.g. a refund flowing through COGS).
5. Reconcile the counterparty rows counted against the export's own TOTAL row. Every bucket and
   concentration share divides by that total, so a gap makes all of them wrong by the gap: the engine
   excludes group-subtotal rows, reports `rows_reconciliation`, and raises
   `aging_rows_do_not_sum_to_total` when the two still disagree. Carry that flag into the memo and read
   the shares as indicative when it fires.

## Output contract (return ONLY the engine's JSON)
`results` carries one record per `side` (AR / AP), each holding `latest` (total, `pct_over_90`,
DSO/DPO days, bucket distribution, top-N concentration), `prior`, `trend` and `tie_out`. `period` is the
ISO `YYYY-MM` stamp. The engine defines the exact shape; read it there.

Every memo-surfaceable record carries `source_ref` (into `source_files`; aging reads are row-wise
`{file, sheet, row}` with 1-based rows, model reads add `cells`/`period`) and a plain-text `calc` note.
Top-N concentration entries carry their own counterparty-row refs; aggregates (totals, buckets,
DSO/DPO, tie-out, trend) are covered at the record level. **Gate the output with `validate_output.py
<output.json>` before archiving.**

## Source tier & discipline
Aging is management/system-generated; tie to the BS. Flag where aging total ≠ BS balance.

## Definition of Done
- Buckets + concentration + DSO/DPO trend for AP and AR; tie-out to BS; deterioration flagged.
  Works on SpreadsheetML and .xlsx aging exports. Numbers reproduce on re-run.
