# budget-vs-actual — Asteron Care Partners, LLC, 2026-06

Run run-20260915-071646 · 2026-09-15T07:30:04+00:00

## Comparability

- Financing cost (model line 'Debt issuance cost amortization'): -60,000 YTD. Confirm whether it sits above or below the EBITDA bridge per the credit agreement's definition.
- Actual EBITDA is sourced from the model's single EBITDA bridge row 'Reported EBITDA' -- the SAME row covenant-compliance reads -- so the two modules report identical actual EBITDA (one system-wide definition). Budget EBITDA is computed with the identical bridge formula (Net Income + Interest Expense + Financing Cost if enabled and present + Income Taxes + D&A - Interest Income) so it is like-for-like. The financing-cost add-back is ENABLED via ebitda.addback_financing_cost in borrower-config.
- Restructuring accounting is PROVISIONAL across every monthly package through June 2026 (TDR framework, instrument-level 10% modification test, CODI, Section 382, and goodwill interim reassessment all remain open per each month's 'Status' tab) -- successor Balance Sheet, gain/loss, and debt carrying-value figures could still restate once the workpapers are concluded.
- Weak-site count peaked at 9 (of 15 total sites) from 2025-Q3 through 2026-Q1 and improved to 8 by June 2026; agency/contract-labor cost as a % of revenue peaked at 17.6% in Q4 2025 and improved to 10.0% by June 2026 (Site Operations / Labor tabs of 'operating-support.xlsx').
- See paths._ebitda_definition_gap_note for the constant $553,353.26/month operating-lease add-back embedded in the credit agreement's Covenant EBITDA (vs the model's own 'Reported EBITDA' row) -- covenant-setup should confirm this against the A&R Agreement's actual EBITDA/Fixed Charges definitions.

## How this period's figures were prepared

- Actuals from the company-prepared/unaudited monthly financials model (read via monitor_lib). Budget is management-prepared/unaudited, extracted via budget-map.json. ACTUAL EBITDA is read from the model's single EBITDA bridge row (the same definition covenant-compliance uses); budget EBITDA mirrors that bridge formula for like-for-like comparison.

## Flagged this period

- **Revenue** (latest) — budget 15,939,567.00, actual 13,164,599.73
- **Revenue** (YTD) — budget 46,632,267.00, actual 38,694,877.56
- **Gross Profit (as-reported)** (latest) — budget 1,291,104.93, actual 4,410,295.39
- **Gross Profit (as-reported)** (YTD) — budget 3,639,685.53, actual 12,365,599.92
- **EBITDA (as-reported)** (latest) — budget 1,291,104.93, actual 1,547,741.28
