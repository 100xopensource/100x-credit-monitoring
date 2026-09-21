# budget-vs-actual — Veyrona Decision Systems, LLC, 2026-06

Run run-20260916-094426 · 2026-09-16T09:53:20+00:00

## Comparability

- Actual EBITDA is sourced from the model's single EBITDA bridge row 'Reported EBITDA' -- the SAME row covenant-compliance reads -- so the two modules report identical actual EBITDA (one system-wide definition). Budget EBITDA is computed with the identical bridge formula (Net Income + Interest Expense + Financing Cost if enabled and present + Income Taxes + D&A - Interest Income) so it is like-for-like. The financing-cost add-back is DISABLED via ebitda.addback_financing_cost in borrower-config.
- No acquisition/consolidation basis change found across the full history checked (2024-09 through 2026-06) -- analysis.basis_breaks is intentionally empty.
- Budget-side gap: Approved_Budget_FY2025_H1_2026.xlsx's 'Monthly Operating Plan' tab provides ONLY a monthly Budget EBITDA figure (no monthly Revenue/Gross Profit/Opex breakdown exists anywhere in the workbook -- those exist only as two ANNUAL/H1 aggregate points on the 'Approved Plan' tab, which cannot support a monthly period join). budget-map.json therefore omits 'revenue' and 'gross_profit' from metric_rows rather than guessing a monthly split from the annual total -- the EBITDA-level budget-vs-actual comparison is fully supported; the Revenue-variance and Gross-Margin-variance lines will show as unavailable for this borrower, a genuine and disclosed gap (see budget-map.json _gap_note), not a misconfiguration.
- EBITDA add-back sweep (for deal-context.json ebitda_addbacks) found NO restructuring/impairment/write-off/severance/transaction-cost/settlement/one-time/miscellaneous line anywhere in the Original Credit Agreement, any of the three Amendments, the FY2022-2024 Reviewed Financial Statements, the Underwriting Memorandum, or the Management Presentation -- and the Underwriting Memorandum explicitly disclaims addbacks ('no credit given to unsupported pipeline conversion, hypothetical headcount savings, or prospective margin actions'). Reported EBITDA ties exactly to the certified Covenant EBITDA figure at every point checked. ebitda_addbacks.entries is therefore empty. SEPARATE GAP FOR COVENANT-SETUP: the Credit Agreement never actually spells out the EBITDA addback-limb definition text (no '(i) interest, (ii) taxes, (iii) D&A...' language exists anywhere in the executed documents) -- covenant-setup should flag this as a legal-extraction gap rather than assume a standard addback stack.

## How this period's figures were prepared

- Actuals from the company-prepared/unaudited monthly financials model (read via monitor_lib). Budget is management-prepared/unaudited, extracted via budget-map.json. ACTUAL EBITDA is read from the model's single EBITDA bridge row (the same definition covenant-compliance uses); budget EBITDA mirrors that bridge formula for like-for-like comparison.

## Flagged this period

- **The shipped budget_vs_actual.py's budget_metrics() called reader.metric()/.metric_sum() for 'revenue' and 'gross_profit' unconditionally; when a budget-map genuinely omits those rows (a documented data gap, not a misconfiguration -- this borrower's approved budget only carries annual/H1 aggregates for Revenue/Gross Profit, no monthly figure), it raised ConfigError and turned the WHOLE module into a skip, losing even the EBITDA-vs-budget comparison the budget fully supports.** (engine_patch)
