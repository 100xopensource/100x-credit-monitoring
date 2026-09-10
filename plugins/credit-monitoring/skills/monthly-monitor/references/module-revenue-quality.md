# Module spec — Revenue Quality (ARR / Retention / Deferred Revenue)  (FAN-OUT TARGET)

Engine: `revenue_quality.py`. Standalone (uses the monthly Retention Analysis + Deferred Revenue
Schedule files); reads recurring revenue and deferred revenue tie-out lines from the model; reads
certified ARR/Indebtedness from the compliance certificate when a recurring-revenue covenant exists in
covenant-spec.json.

## Units — this module publishes dollars

Its three inputs are borrower documents stating dollars (the Retention Analysis, the Deferred Revenue
Schedule, the certificate), while the few figures it reads from the model are on the model's unit. The
model side is converted to dollars as it is read, so every figure here — and every tie-out between a
workbook and the model — is in dollars, which is what the output's envelope declares. Without that, a
thousands-scale deal's ARR-to-model tie-outs sat a thousand-fold apart. The borrower's unit comes from
`model.scale` + `model.currency` in borrower-config (required).

## Purpose
Test the recurring-revenue base directly — the thesis pillar most SaaS deals are approved on and,
where an ARR-denominated covenant exists, the base the covenant stands on. Answers: is ARR holding,
what churned, is deferred revenue corroborating or contradicting the retention story, and how much
ARR erosion the covenant can absorb before breach.

## You supply the three input files
The engine reads the files you hand it and does the arithmetic. Finding them is your job, because every
borrower's folder is shaped differently and you are the one who can look.

Open `reporting_packages_folder`, find the month being reported, and identify each file by opening it —
a retention workbook has customers down the side and months across the top; a deferred-revenue schedule
walks an opening balance to a closing one. Then:

```
python revenue_quality.py --config <borrower-config.json> --period 2026-05 --out <output .json> \
  [--retention-file <ARR / retention workbook>] \
  [--deferred-revenue-file <deferred-revenue schedule>] \
  [--certificate-file <compliance certificate>]
```

Pass each path **as the borrower-config writes it** (`source:...`) — the same string as in borrower-config, not a `/sessions/…`
path. That string is what the memo's figure links are built from. Format is read off the file's
own bytes (.xlsx / SpreadsheetML / text), so an export that mislabels its extension reads fine.
To open a file yourself first, use the connected-folder path with Read (the file tools refuse a
`/sessions/…/mnt/…` string) and the sandbox path with bash — your agent contract's "Three path forms"
(`${CLAUDE_PLUGIN_ROOT}/agents/module-analyst.md`).
The engine reads every tab, so a workbook whose detail sits behind a cover tab is fine as-is.

The certificate is read as TEXT. When the period's certificate is a PDF, use an already-existing
`.txt` file with the same stem beside it; the engine picks that sidecar up on its own. Never create a
sidecar in the borrower source folder. Pass the PDF's own path so the memo's link points at the
document the borrower signed. Without an existing sidecar, the certificate figures report as
unavailable, which is the honest answer: the engine will not read a figure out of PDF bytes.

Each flag is optional and independent. Withhold one and only its section reports as unavailable —
every other section still computes. Report which file was absent so the memo can say so.

- model_output_path — recurring revenue and deferred revenue lines for tie-out (via monitor_lib).
- covenant-spec.json — if a recurring-revenue ratio covenant exists: threshold schedule + certified
  Indebtedness/ARR from the latest compliance certificate for the stress grid.
- deal-context.json `static` — acquisition/basis-break dates (acquired ARR must be separated so
  retention math is organic, not flattered by acquired revenue) and named churn concerns from
  `our_view` / `carry_forward` to reconcile against.

## Method
1. **ARR bridge** — opening ARR → new / expansion / contraction / churn → closing ARR, current and
   prior month (and YTD where the file supports it), reporting whether the bridge foots (`foots` /
   `foot_difference`). If the borrower file gives snapshots only, derive the bridge from
   customer-level deltas and mark derivation.
2. **Retention metrics** — gross revenue retention, net revenue retention, logo retention; dollar vs
   logo churn split; cohort/tenure view where the file provides it. Post-acquisition months: compute
   organic (pre-acquisition base) and combined separately; NEVER report combined-only retention.
3. **Churned/at-risk customer register** — names, ARR, reason where given. Cross-reference top
   churned/contracting names against the AR aging top-N (signal: leaving AND not paying) — emit as
   `cross_module_checks` for the aging module / synthesis to join.
4. **Deferred revenue reconciliation** — DR walk (opening + billings − revenue recognized = closing)
   vs the model's BS deferred revenue line; explain DR direction vs churn narrative (annual prepay
   timing, notice periods, acquired DR). Flag when DR and retention tell opposite stories.
5. **Covenant stress grid** (only if an ARR-denominated covenant exists) — certified ratio at actual
   ARR, then ratio at ARR − known at-risk churn, and the breakeven ARR at the covenant max. Include
   PIK drift: numerator growth at the contractual PIK rate over the next 1/3/6 months.
6. **Tie-outs** — retention-file closing ARR vs certificate ARR vs model recurring revenue × 12
   (annualization convention per file); DR schedule closing vs BS deferred revenue. Flag mismatches
   beyond `tie_tolerance`.

## Output contract (return ONLY the engine's JSON)
`results` is a list of SECTION records (`section` key) — one per Method step: `arr_bridge`,
`retention`, `churn_register`, `deferred_revenue`, `covenant_stress`, `cross_module_checks`,
`tie_outs`. Each is independently input-gated by its own `available` flag, and `exceptions` carries one
entry per unavailable section with the reason, so a missing file costs only its own section. `period` is
the run's ISO `YYYY-MM` stamp; `source_files` is the deduped source-path list every ref points into.
The engine defines the exact shape; read it there.

Every memo-surfaceable record carries `source_ref` (file/sheet/row/cells into `source_files`; paths
recorded as its shared folder so the artifact can join store hyperlinks) and a plain-text `calc` note. An
unusable `--period` writes the standard skip record (`{"skipped": true, "reason": …}`). **Gate the
output with `validate_output.py <out.json>` before archiving.**

## Source tier & discipline
Retention Analysis and DR Schedule are management-prepared; certificate values are borrower-certified.
Tie everything to the model and certificate; disclose every derivation; never average away a basis
break. Names in the churn register are facts from the borrower's own file — cite file + month.

## Definition of Done
- ARR bridge + GRR/NRR/logo retention (organic vs combined where an acquisition exists); churn
  register; DR walk tied to BS; covenant stress grid when an ARR covenant exists; cross-module
  checks emitted; every figure sourced to file + period. Numbers reproduce on re-run.
