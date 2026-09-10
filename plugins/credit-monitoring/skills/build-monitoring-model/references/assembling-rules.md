# Statement Assembly Rules

Rules for building and maintaining a borrower's monthly Income Statement, Balance Sheet, and Cash Flow
in Excel from the company's own reporting packages. Goal: one canonical, validated, version-stable file
per borrower that grows month over month.

## Objective
- Reconstruct the monthly **Income Statement**, **Balance Sheet**, and **Cash Flow** at **full
  line-item detail**. Do not summarize, collapse, rename, or drop lines.
- Months run left → right as columns; line items are rows, preserving section headers and subtotals in
  source order.
- Then maintain it: each new package is merged in (see Merge-by-period).

## Scope / entity
Default to the **consolidated** entity. Where several consolidation levels exist (consolidated vs.
standalone divisions/subsidiaries), confirm scope before building; keep the bases unmixed.

## What counts as "actual" (source discipline)
- **Actuals only** — never proforma, forecast, or budget columns.
- Actuals come **only** from the workbook (or PDF) the company provided.
- The borrower's compliance / borrowing-base certificate is a cross-check elsewhere, **not a source** of
  statement figures.
- State the source for the build (file name, sheet, period) and that it is company-prepared / unaudited.

## Source files — locating, formats, access
- Identify the reporting package(s) in the borrower's **connected** deal folder; ignore models,
  appraisals, certificates, and prior-deal files. When the folder isn't connected, follow the access
  ladder (`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`): an unconnected folder is unknown,
  not empty, so never conclude "no packages" or "nothing filed" from an unconnected folder.
- **Formats:** `.xlsx`/`.xlsm` → openpyxl (zip-based). Old binary `.xls` → xlrd/pandas. **NetSuite/
  QuickBooks `.xls` exports are frequently SpreadsheetML XML** (the file starts with `<?xml`); parse
  them as XML, not as binary `.xls`.
- **Files that are not locally readable** never reach the parser as content; reading unavailable bytes
  can error or silently truncate. Return the local-availability diagnosis to the analyst session. It
  attaches the folder guide and offers retry, skip, or stop. Do not infer a provider, explain sync
  settings in chat, or force a download.
- **Encryption:** files may be "CDFV2 Encrypted." Try `msoffcrypto-tool` with Excel's default key
  **`VelvetSweatshop`** before assuming a real password. If a genuine password is required, ask the
  analyst plainly for it (or for an unlocked copy), use it once and never save it; if the decrypt tooling
  isn't available, say so plainly and move on rather than erroring.
- **Hidden columns & sheets hold the monthly series.** The printed report range usually shows only the
  current month + YTD. Unhide everything and scan the header row for **month-dated columns** (e.g.,
  Jan-25 … Dec-25) to get the true monthly actuals. Don't trust only the visible report.
- Guard numeric parsing: blank cells sometimes read as the string "nan"; treat as empty (never let
  `float('nan')` poison a sum).

## Merge-by-period (one rule covers append AND restatement)
Treat every update as: **merge the new package's periods into the existing file.**
- For each month in the package that **already exists** in the file: compare. If it changed, that's a
  **restatement** — refresh that month and **log old → new**. If identical, leave it.
- For each month **not yet** in the file: append it.
- The **latest / most-consolidated source wins** for any overlapping month.

## Restatement scan (run on EVERY build — incremental AND from-scratch)
Compare across all sources covering an overlapping period: the prior canonical file/snapshot (if any);
every monthly package vs. any consolidated/by-month/YTD file present; and any two packages whose ranges
overlap.
- Default: diff each overlapping month at **Net Income + key subtotals** (Total Revenue, Gross Profit,
  EBITDA, Total Assets, Total Equity, Net Change in Cash).
- If any moves by more than the **materiality threshold** (≥ the greater of **$25k or 1%** of the line),
  drill to **line level** and record which accounts moved.
- **Latest/consolidated wins** for the stored value, but **log old → new** (source, period, line, amount)
  in Sources & Notes. Flag a restatement prominently when it is large or flips a sign/trend.
- **Also write the log machine-readable:** maintain `restatements.json` next to the model (same
  folder), appending one entry per restated line per build:
  `{ "build": "YYYY-MM-DD", "period": "Mon YYYY", "line": "…", "old": 0, "new": 0,
  "source": "<package that restated it>", "material": true }`. Sources & Notes is for humans;
  `restatements.json` is what monthly-monitor reads to put restatements in the memo's data-quality
  section — modules cannot parse Sources & Notes (no month columns), so restatements reach the memo only
  through this file.
- Pin the materiality threshold in the per-borrower profile so runs are reproducible.

## Source map (write on EVERY build — the machine-readable column lineage)
The build decides, per statement per month, WHICH package file supplies the stored values
("latest/consolidated wins" above). Record that decision machine-readable as `source-map.json` next to
the model (same folder, same pattern as `restatements.json`): Sources & Notes states it for humans;
`source-map.json` is what the monthly report reads to hyperlink each figure past the model to its
ORIGINAL package file (the two-hop trace implemented by `monitor_lib.load_source_map` / `upstream_sources`).
Shape:

```json
{
  "model_file": "<model_output_path, as the config writes it>",
  "columns": {
    "Income Statement": {
      "2026-01": { "file": "<package path, as the config writes it>", "sheet": "P&L" },
      "2026-03": { "file": "<...consolidated file>", "sheet": "IS by month", "restated": true }
    },
    "Balance Sheet":       { "...": {} },
    "Cash Flow Statement": { "2026-01": { "derived": true } }
  }
}
```

- One entry per statement sheet (the exact layout-contract names) per ISO `YYYY-MM` month column.
- `file` is the package that SUPPLIES the stored value after the restatement scan — the winning file,
  not necessarily the month's own folder — recorded as its shared folder plus the path inside it
  (`monitor_lib.unresolve_path` says it that way), never a
  sandbox mount). `sheet` is the tab within it. Mark `"restated": true` when a later file overrode the
  month's original package.
- A derived cash-flow month (no as-reported CF) is `{ "derived": true }` — readers follow it to the
  same month's IS + BS entries, so those entries stay present for a derived month.
- A month whose EBITDA the agreement DEEMS is recorded alongside `columns`, in `_ebitda_deemed_months`,
  because its figure comes from the credit agreement rather than from any package:

```json
{
  "_ebitda_deemed_months": {
    "2025-10": { "deemed": true, "value": 1200000,
                 "source": "LSA §1.1 EBITDA definition (deemed schedule)",
                 "reference": "<covenant_spec_path>#/definitions/EBITDA/deemed_schedule",
                 "note": "Bridge row hardcoded to the deemed figure, not computed from this model." }
  }
}
```

  `value` is on the MODEL's unit (what the cell holds); the spec keeps the agreement's own dollars.
- The covenant-support rows populated from the debt-service schedule are recorded alongside `columns`,
  in `_debt_service_rows`, because their figures come from the generated schedule (upstream the signed
  agreement) rather than from any reporting package:

```json
{
  "_debt_service_rows": {
    "Cash Interest Expense": { "source": "debt-service-schedule.json (contractual; LSA §2.5)",
                               "reference": "<debt_service_schedule_path>#/covenant_support",
                               "note": "Facility cash interest accrued per month, schedule dollars converted onto the model's unit." },
    "Scheduled Debt Principal Payments": { "source": "debt-service-schedule.json + borrower reporting (vehicle loan)",
                               "reference": "<debt_service_schedule_path>#/covenant_support",
                               "note": "Facility scheduled amortization on the spec's declared attribution, plus disclosed non-facility amortization." }
  }
}
```

- Rewrite the whole file each build: it mirrors the current workbook, where `restatements.json` appends.

## Output layout contract (the analysis layer reads EXACTLY this shape — do not deviate)
Every analysis module reads the workbook through `lib/monitor_lib.py`, which expects this layout.
A workbook that deviates is unreadable downstream even if every number in it is correct.
- **Sheet names, exactly:** `Income Statement`, `Balance Sheet`, `Cash Flow Statement`
  (plus `Sources & Notes`, which modules do not parse).
- **Month header row = row 6** on every statement sheet.
- **Month labels formatted `Mon YYYY`** — three-letter month, space, four-digit year, as TEXT
  (e.g. `Jan 2026`; never `Jan-26`, `2026-01`, or a date-typed cell).
- **Column A = line-item labels; monthly data starts in column B**, oldest → newest, contiguous.
- **FY / YTD / other total columns only AFTER the last month column** — readers stop at the
  first non-month header.

Note: `monitor_lib` READERS detect the header row and tolerate the layout variants that exist in older
models (header row 4/6/8; `Mon YYYY`, `YYYY-MM`, or date-typed labels) — a safety net so legacy models
parse without ad-hoc drivers, NOT license to deviate. New and rebuilt models are written to exactly the
contract above.

## Build conventions (the Excel output)
- **Detail/source lines = hardcoded inputs, colored blue. Subtotals, ratios, totals, and the EBITDA
  bridge = Excel formulas, black.** Keep it a live model, not pasted numbers.
- Units stated in the header.
- **Mind sign conventions.** Common trap: Returns/Allowances and Discounts are contra-revenue → subtract
  them (Total Revenue = Product + Shipping − Returns − Discounts).
- Include FY / YTD total columns (SUM formulas) and a **Balance-check row** (Assets − Liabilities −
  Equity = 0) on the balance sheet.
- **EBITDA bridge:** compute using `covenant-spec.json#/definitions/EBITDA` so every downstream module
  reads ONE EBITDA. If covenant-spec is absent, compute a standard EBITDA and flag as provisional.
- **A month the agreement DEEMS is written as the deemed figure, blue.** When that EBITDA definition
  carries a `deemed_schedule` (common in an acquisition / LBO financing — the agreement fixes EBITDA for
  the pre-close run of months) and one of those months falls inside this model's range, hardcode the
  bridge row for that month to the schedule's amount rather than the computed formula: the agreement
  governs, and the covenant engine reads the schedule for that month either way, so a computed cell there
  makes the workbook and the covenant test disagree about the same month (covenant-compliance flags it as
  `deemed_model_disagreement`). Convert the amount onto this model's unit — agreements state deemed
  EBITDA in raw dollars, against models often kept in thousands. Record each such month in
  `source-map.json` under `_ebitda_deemed_months` (see the Source map section) and say it on Sources &
  Notes: the figure is sourced to the agreement's clause, not to a reporting package. Months of the run
  that fall outside the model's range need nothing here — the covenant engine reads them straight from
  the spec.
- **Covenant-support rows come from the debt-service schedule, never the ledger.** When
  borrower-config carries `debt_service_schedule_path` and the file exists, write the rows its
  `covenant_support.rows` block names (e.g. "Cash Interest Expense", "Scheduled Debt Principal
  Payments") from its `covenant_support.monthly` values — one hardcoded blue cell per month, converted
  from the schedule's dollars onto this model's unit. These are the rows the covenant-spec's Fixed
  Charges / coverage definitions bind, and the schedule is the contract: booked interest expense
  sweeps in pre-close and non-facility borrowings across the whole trailing window, and no statement
  line carries scheduled amortization at all, so **ledger interest expense is never a covenant
  Fixed-Charges input when a debt-service schedule exists**. The ledger's own interest-expense row
  stays where the P&L needs it — the two are different facts about the same month. A month the
  schedule covers but the model does not (or vice versa) is written for the months the model carries;
  a support-row month before the facility closed is 0, a contractual fact, not a blank. **Non-facility
  scheduled debt** (vehicle loans, SBA notes) is ADDED into the scheduled-principal row from the
  borrower's own reporting where disclosed, each with its own source note. Record the rows in
  `source-map.json` under `_debt_service_rows` (see the Source map section) and say on Sources & Notes
  that they are sourced to the generated schedule, upstream the signed agreement. Without a schedule,
  build as before at lower precision and say in the conclusion: "fixed charges on ledger proxy —
  debt-service schedule not yet built" (the covenant engine flags the same thing on every coverage
  covenant).
- Add a **Sources & Notes** sheet: entity, units, source files, conventions, validations, restatement
  notes, and any capital-structure / basis breaks.
- Cash flow: use as-reported where available. When the package ships only IS + BS (common), **derive**
  the statement with the shared **indirect builder** `references/cashflow.py` (`build_cashflow` /
  `from_model` → `write_sheet`), which classifies every balance-sheet leaf by SECTION (current assets
  / non-current / current liab / LT liab / equity) and emits Operating / Investing / Financing / Net
  Change. It ties to the balance-sheet cash change BY CONSTRUCTION via a disclosed **`Non-cash &
  unexplained (plug)`** row — which legitimately carries non-cash debt/equity accretion (PIK,
  capitalized fees/MOIC, non-cash note issuance) that IS+BS alone cannot separate from cash; a large
  plug is itself a flag. Use this builder rather than a bespoke per-borrower CF. Keep CF-vs-IS /
  CF-vs-BS memo rows.

## Recalc — populate cached values, as the LAST write
openpyxl writes formula strings with **no computed value**, so downstream readers (the analysis modules)
see blanks for every subtotal until a human opens the file in Excel. After writing, force a recalc so
values persist, using a LibreOffice user profile set to *always recalculate on load*. Make every openpyxl
cell edit BEFORE this step — a write after it drops that cell's cached value and reintroduces the
blank-subtotal bug (`validate_model.py` fails the build if this happens).

```bash
PROF=$(mktemp -d); mkdir -p "$PROF/user"
cat > "$PROF/user/registrymodifications.xcu" <<'XCU'
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
 <item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
XCU
libreoffice --headless --calc --convert-to xlsx:"Calc MS Excel 2007 XML" --outdir <OUTDIR> <FILE> -env:UserInstallation="file://$PROF"
```

Then reopen with `openpyxl(data_only=True)` and confirm subtotal / EBITDA / balance-check cells now carry
numeric cached values. **Fallback:** if LibreOffice is unavailable or recalc fails to populate values,
write the computed numeric values into those cells instead (still correct + machine-readable) and record
which mode was used.

## Validation gate (run on the RECALCED, SAVED file before it becomes canonical)
Only accept the file if all pass:
0. **`references/validate_model.py <model>` exits 0** — no formula subtotal shipped without a cached value
   (the recalc-ordering bug: a write after the recalc), and no duplicate row labels on a statement sheet.
1. **Balance sheet balances** every month (Assets = Liabilities + Equity; balance-check = 0).
2. **Income-statement subtotals foot** (component lines sum to their subtotals/totals).
2a. **Full detail** — the model carries EVERY source leaf line (no curated subset); a build that drops
   detail lines fails the gate. Derived CF sheet present with Operating/Investing/Financing + Net Change.
3. **Months tie to the package's own stated totals** (months sum to printed FY/YTD; current month matches
   the printed month column).
4. **Layout contract holds** (see "Output layout contract"): the three statement sheets exist under
   their exact names, row 6 carries `Mon YYYY` text labels starting at column B with no gaps, and
   FY/YTD columns sit after the last month. `monitor_lib.open_model()` succeeding is NOT sufficient —
   the reader tolerates legacy layout variants (header row 4/8, `YYYY-MM`, date cells) by design, so
   check explicitly: `open_model(path)` succeeds, `Model.months` lists every expected month, every
   statement sheet's `Model.layout[sheet]` reports `header_row: 6` / `first_data_col: 2`, and the raw
   row-6 header cells are TEXT matching `Mon YYYY` (not date-typed cells).
On failure: **do not overwrite.** Keep the last good file, stop, and flag what failed, by how much, which
month. Then ask for a look.

## Versioning & output location
- Overwrite the canonical file but keep a **dated snapshot** (`..._asof_YYYY-MM.xlsx`) so restatements
  stay auditable; maintain a short restatement log (old → new).
- Final deliverables write under the configured output folder.
- Build attempts and pre-recalc copies use ephemeral sandbox scratch and are removed after the gate;
  they never become `DELETE_ME` or superseded-attempt files in `_model`. The source folder is read-only,
  and scratch is not a place to
  "save anyway" — if the output folder isn't connected, hand back the folder steps
  (`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`).
- **Save safely (synced folders).** Write the gated workbook to a hidden atomic-replace temp file in the SAME output folder, then
  replace the canonical file — an interrupted save must never leave a truncated model in place. Before
  overwriting, check the new file isn't drastically smaller than the last good one (a size collapse means
  a bad read/build: keep the good file and stop). If the canonical path is longer than Excel can open
  (`monitor_lib.path_within_budget(path)` is False), also write a short-named copy to a shallow connected
  location with `monitor_lib.deliver_short_copy(...)` and tell the analyst plainly which file to
  double-click.

## Per-borrower profile (reuse to avoid re-discovery)
After the first successful build, record a small profile so future runs don't re-guess: file
location/pattern, statement sheet name(s), where the monthly columns live (and that they're hidden),
decrypt key, sign conventions, the EBITDA-bridge account map, and the pinned materiality threshold. Reuse
it next month; let the validation gate catch any template drift.
