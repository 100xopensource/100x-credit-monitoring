# budget-vs-actual — Talvorn Technical Services, LLC, 2026-06

Run run-20260916-083949 · 2026-09-16T08:41:54+00:00

## Comparability

- Actual EBITDA is sourced from the model's single EBITDA bridge row 'Reported EBITDA' -- the SAME row covenant-compliance reads -- so the two modules report identical actual EBITDA (one system-wide definition). Budget EBITDA is computed with the identical bridge formula (Net Income + Interest Expense + Financing Cost if enabled and present + Income Taxes + D&A - Interest Income) so it is like-for-like. The financing-cost add-back is DISABLED via ebitda.addback_financing_cost in borrower-config.
- Reporting-consistency check: spot-checked file lists across all 22 Finance Uploads/Monthly Close months (2024-09 through 2026-06) -- every month holds exactly the expected file set with byte-identical filenames (4 files pre-close 2024-09 through 2024-12, 5 files 2025-01 onward adding certificate.pdf). No amendment/waiver, no restatement, no correction notice, and no schema deviation was found anywhere in the deal folder -- this is the cleanest file (no data-quality flags) of the five borrowers onboarded in this batch so far. analysis.basis_breaks and comparability_items are both intentionally empty.
- Covenant trend: TTM FCCR has risen MONOTONICALLY every single month for the full 18-month certified history, from 1.6680x (2025-01, first certificate) to 2.1564x (2026-06, latest) against a flat 1.2000x minimum -- cushion has nearly tripled (0.47x to 0.96x). All four quarterly tests to date (2025-Q1 through 2026-Q2) certified COMPLIANT with growing margin. See deal-context.json our_view for the full trajectory read.
- EBITDA add-back sweep: the credit agreement's own Accounting Notes state explicitly 'No EBITDA adjustments are permitted under this agreement; forecasts, synergies and owner contributions are not earnings' -- Covenant EBITDA is CONTRACTUALLY DEFINED to equal Reported EBITDA with zero addback flexibility (stronger than Selvara's 'none found by inspection' -- here none are even permitted by contract). No restructuring/impairment/write-off/severance/transaction-cost/one-time line was found in any monthly package, the IC Memo, or the Management Presentation either. ebitda_addbacks.entries in deal-context.json is therefore empty by contractual design, not merely by absence of candidates.

## How this period's figures were prepared

- Actuals from the company-prepared/unaudited monthly financials model (read via monitor_lib). Budget is management-prepared/unaudited, extracted via budget-map.json. ACTUAL EBITDA is read from the model's single EBITDA bridge row (the same definition covenant-compliance uses); budget EBITDA mirrors that bridge formula for like-for-like comparison.

## Flagged this period

- **Gross Profit (as-reported)** (latest) — budget 7,359,315.65, actual 6,232,030.41
- **Gross Profit (as-reported)** (YTD) — budget 40,098,149.03, actual 35,969,302.62
- **EBITDA (as-reported)** (latest) — budget 3,548,732.94, actual 2,517,745.05
- **EBITDA (as-reported)** (YTD) — budget 18,363,564.31, actual 14,531,936.86
