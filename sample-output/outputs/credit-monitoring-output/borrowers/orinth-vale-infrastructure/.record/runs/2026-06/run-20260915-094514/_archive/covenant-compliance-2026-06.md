# covenant-compliance — Orinth Vale Infrastructure, LLC, 2026-06

Run run-20260915-094514 · 2026-09-15T09:50:11+00:00

## Notes on what was read off the documents

- Current-period certificate (2026-06, a quarter-end): 06 Monitoring/Portfolio Management/2026 Q2/Covenant Calculation.xlsx 'Summary' tab reports Total Leverage Ratio 2.3911x vs Maximum 5.5x, Status COMPLIANT, test date 2026-06-30. This ties exactly to 04 Reporting/Period Archive/2026/06/certificate.html, which independently states the same Funded Debt ($58,650,506.80), Trailing Covenant EBITDA ($24,529,022.34), Total Leverage Ratio (2.3911x) and Status (COMPLIANT) for 2026-06-30 -- the certificate.html's own text confirms 'The covenant status below is reported only when a formal quarter-end test is required', and June 2026 is a quarter-end.
- Per the run's request to report the full historical trend and cross-check every quarter, all 6 certified quarterly test dates (2025-03-31 through 2026-06-30) were transcribed from each quarter's own Covenant Calculation.xlsx, not only the current period's certificate -- one entry per test date, each citing its own quarter's file in 'section'.
- All 6 values are the bare 'Total Leverage Ratio' cell as displayed on each certificate (already rounded to 4 decimals by the borrower's own workbook); Funded Debt and TTM Covenant EBITDA are quoted alongside for context but are not separately certified line items under this covenant's cert_ref beyond what is shown here.
- EXCEPTION (recorded for the orchestrator): the certified-values contract's 'source' field names one certificate file, but the borrower's 6 quarterly workpapers are all identically named 'Covenant Calculation.xlsx' in different '<YYYY Qn>' subfolders. 'source' above is set to the CURRENT period's file (2026 Q2); the engine's upstream-document resolution for that file name may therefore land on whichever same-named file its folder search encounters, so the automatically-resolved 'upstream' hyperlink on the five PRIOR quarters' rows may not point at their own quarter's specific file even though this JSON's own 'section' field correctly names it. Alternative reading: split the 5 historical quarters into their own certified-values files (one per quarter) so each gets an unambiguous 'source' -- not done here since the contract's shape is one certified-values file per module run/period, and the per-date 'section' field carries the correct locator regardless.

## How this period's figures were prepared

- Company-prepared/unaudited model; EBITDA taken from the model's single pinned EBITDA bridge row (not recomputed). Thresholds and definitions per the standardized covenant-spec; each definition's flow/spot basis is explicit in the spec. cert_* fields are the borrower's own certified actuals transcribed from the compliance certificate -- reported alongside the model reconstruction, never merged with it.

## Flagged this period

- **max_total_leverage** (not_evaluable) — insufficient_history
