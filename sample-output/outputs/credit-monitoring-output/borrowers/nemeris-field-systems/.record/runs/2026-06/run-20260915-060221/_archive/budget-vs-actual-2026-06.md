# budget-vs-actual — Nemeris Field Systems, LLC, 2026-06

Run run-20260915-060221 · 2026-09-15T06:04:50+00:00

## Comparability

- Actual EBITDA is sourced from the model's single EBITDA bridge row 'Reported EBITDA' -- the SAME row covenant-compliance reads -- so the two modules report identical actual EBITDA (one system-wide definition). Budget EBITDA is computed with the identical bridge formula (Net Income + Interest Expense + Financing Cost if enabled and present + Income Taxes + D&A - Interest Income) so it is like-for-like. The financing-cost add-back is DISABLED via ebitda.addback_financing_cost in borrower-config.
- Reporting basis note (2026-06 Close Notes): first-and-final package, no restatement or basis change.
- 2026 Q2 Portfolio Management folder contains a Lender_Query_Unsupported_Addback and Borrower_Correction_Notice plus both an ORIGINAL and CORRECTED Covenant_Calculation for Q2 2026 — covenant-setup and the covenant-compliance module should use the CORRECTED version and note the correction history.
- Budget (Monthly Operating Plan) is only built down to Budget EBITDA — it does not budget interest, taxes, D&A or net income, consistent with the model's own gap noted in model_lines.

## How this period's figures were prepared

- Actuals from the company-prepared/unaudited monthly financials model (read via monitor_lib). Budget is management-prepared/unaudited, extracted via budget-map.json. ACTUAL EBITDA is read from the model's single EBITDA bridge row (the same definition covenant-compliance uses); budget EBITDA mirrors that bridge formula for like-for-like comparison.

## Flagged this period

- **Revenue** (latest) — budget 11,115,786.65, actual 9,570,056.36
- **Revenue** (YTD) — budget 63,034,185.85, actual 55,574,230.48
- **Gross Profit (as-reported)** (latest) — budget 5,217,370.69, actual 4,353,418.64
- **Gross Profit (as-reported)** (YTD) — budget 29,122,152.60, actual 25,201,765.09
- **EBITDA (as-reported)** (latest) — budget 1,924,116.40, actual 1,238,610.25
- **EBITDA (as-reported)** (YTD) — budget 10,087,603.77, actual 6,996,371.34
