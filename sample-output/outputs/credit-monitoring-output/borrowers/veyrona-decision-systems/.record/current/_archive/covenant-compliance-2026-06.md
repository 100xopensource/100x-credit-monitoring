# covenant-compliance — Veyrona Decision Systems, LLC, 2026-06

Run run-20260916-094426 · 2026-09-16T09:53:57+00:00

## Action items

- No re-signed Q1 2026 compliance certificate exists after the March 2026 restatement was discovered on 2026-06-20 -- request one from the borrower for the file, even though the verdict does not change (5.1265x as-filed vs. 5.1197x corrected, both comfortably under the 5.25x threshold then in effect).

## Notes on what was read off the documents

- Per the run's request to report the full covenant history and cross-check every quarter, all six certified quarterly test dates (2025-03-31 through 2026-06-30) were transcribed from each quarter's own Compliance_Certificate_QX_20XX.pdf 'CALCULATION' section (each a one-page PDF; no page numbering beyond page 1 of 1), cross-checked against that quarter's Covenant_Calculation_QX_20XX.xlsx 'Leverage Summary' tab -- every quarter's Funded Debt, Unrestricted Cash, TTM Covenant EBITDA and Net Leverage figure tie EXACTLY between the certificate PDF and the matching workbook (Debt check / Ratio check residuals = 0.00 in every quarter's workbook).
- EXCEPTION (recorded for the orchestrator, same class of issue as a prior run in this batch, different root cause): the certified-values contract's top-level 'source' field names ONE certificate file; this borrower has six distinctly-named quarterly certificates (Compliance_Certificate_Q1_2025.pdf ... Compliance_Certificate_Q2_2026.pdf). 'source' above is set to the CURRENT period's file (Q2 2026). The engine binds ONE upstream document to the certified-values file as a whole, so the archived output's automatic 'upstream' hyperlink for the five PRIOR quarters' certified figures will resolve to the Q2 2026 PDF rather than each quarter's own certificate, even though this JSON's own per-date 'section' field names the correct file for each. A person following a historical row's link should use the 'section' text, not the row's auto-resolved upstream link, to find the actual document.
- Q1 2026 STALE CERTIFICATE (per covenant-spec.json step7_signoff decision 'q1_2026_stale_certificate_handling'): the value transcribed above for 2026-03-31 (5.1265x) is the AS-FILED, certified-of-record figure -- Compliance_Certificate_Q1_2026.pdf and Covenant_Calculation_Q1_2026_AS_FILED.xlsx were both signed/delivered 2026-04-12, using PRE-restatement Q1 2026 quarterly Reported EBITDA of $8,437,990.42 in the TTM build (TTM Covenant EBITDA $31,376,744.39). The March 2026 restatement (notice dated 2026-06-20; +$39,375.18 to March 2026 EBITDA, $8,437,990.42 -> $8,477,365.60) was discovered AFTER this certificate was signed. No re-signed Q1 2026 certificate or covenant-calculation workbook exists anywhere in the deal folder (confirmed: only the three original April-2026-dated files exist under Quarterly Compliance/2026 Q1/, none touched since). The CORRECTED Q1 2026 TTM Covenant EBITDA, transcribed for reference (NOT as a certified value -- nobody has certified it) from the Q2 2026 Covenant_Calculation workbook's own 'TTM Build' tab, is $31,416,119.57 (= $7,779,229.61 + $7,280,561.04 + $7,878,963.32 + $8,477,365.60 for 2025-Q2 through 2026-Q1), giving a corrected Net Leverage of 160,852,070.94 / 31,416,119.57 = 5.1197x -- comfortably under the 5.25x threshold in effect on 2026-03-31 either way. This corrected figure is intentionally NOT entered under 'values' above (it was never certified); the model side of the engine's 2026-03-31 row will independently reproduce it, because build-monitoring-model already sourced March 2026 from the restated Quarterly_Package_2026-06-25_REVISED_MARCH-MAY.xlsx, so the model's own TTM window carries the corrected EBITDA while the certified column carries the as-filed number -- both are reported side by side by design, not merged.

## How this period's figures were prepared

- Company-prepared/unaudited model; EBITDA taken from the model's single pinned EBITDA bridge row (not recomputed). Thresholds and definitions per the standardized covenant-spec; each definition's flow/spot basis is explicit in the spec. cert_* fields are the borrower's own certified actuals transcribed from the compliance certificate -- reported alongside the model reconstruction, never merged with it.

## Flagged this period

- **max_net_leverage** (not_evaluable) — insufficient_history
- **Maximum Net Leverage Ratio** (at_risk) — required 5.75, actual 5.28 [2025-09-30]
- **Maximum Net Leverage Ratio** (at_risk) — required 5.75, actual 5.31 [2025-12-31]
- **Maximum Net Leverage Ratio** (at_risk) — required 5.25, actual 5.12 [2026-03-31]
- **Maximum Net Leverage Ratio** (at_risk) — required 5.00, actual 4.90 [2026-06-30]
