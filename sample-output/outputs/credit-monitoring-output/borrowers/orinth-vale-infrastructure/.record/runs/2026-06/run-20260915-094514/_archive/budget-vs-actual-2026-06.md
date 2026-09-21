# budget-vs-actual — Orinth Vale Infrastructure, LLC, 2026-06

Run run-20260915-094514 · 2026-09-15T09:47:55+00:00

## Comparability

- Actual EBITDA is sourced from the model's single EBITDA bridge row 'EBITDA' -- the SAME row covenant-compliance reads -- so the two modules report identical actual EBITDA (one system-wide definition). Budget EBITDA is computed with the identical bridge formula (Net Income + Interest Expense + Financing Cost if enabled and present + Income Taxes + D&A - Interest Income) so it is like-for-like. The financing-cost add-back is DISABLED via ebitda.addback_financing_cost in borrower-config.
- Reporting basis note: no restatement or basis change found across the full history checked (2024-09 through 2026-06); a/b/c letter mapping and all row labels are stable throughout.
- Budget (H1 2026 Monthly Plan) is only built down to 'Budget EBITDA' -- it does not budget interest, taxes, D&A or net income separately (see budget-map.json for the zero-proxy construction), even though the ACTUALS model does break these out natively. This is a budget-granularity gap, not an actuals gap.
- EBITDA add-back sweep (for the ebitda_addbacks register in deal-context.json) found no restructuring/impairment/write-off/severance/transaction-cost/settlement/one-time/miscellaneous line anywhere above or below the EBITDA line, in any month 2024-09 through 2026-06, the FY2022-2024 annual statements, or the underwriting model -- this borrower's TTM Covenant EBITDA (Debt & Covenant tab) ties EXACTLY to the sum of the 12 trailing monthly 'EBITDA' Income Statement rows with zero gap, unlike Asteron's lease add-back structure. ebitda_addbacks.entries is therefore empty.

## How this period's figures were prepared

- Actuals from the company-prepared/unaudited monthly financials model (read via monitor_lib). Budget is management-prepared/unaudited, extracted via budget-map.json. ACTUAL EBITDA is read from the model's single EBITDA bridge row (the same definition covenant-compliance uses); budget EBITDA mirrors that bridge formula for like-for-like comparison.
