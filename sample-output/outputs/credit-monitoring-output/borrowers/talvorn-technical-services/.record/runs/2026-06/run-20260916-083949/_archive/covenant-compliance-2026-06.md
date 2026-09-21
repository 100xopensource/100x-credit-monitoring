# covenant-compliance — Talvorn Technical Services, LLC, 2026-06

Run run-20260916-083949 · 2026-09-16T08:45:30+00:00

## Notes on what was read off the documents

- Transcribed the 'FCCR CALCULATION' section (Trailing Covenant EBITDA / Trailing Fixed Charges / Calculated FCCR / Minimum FCCR / Status) from SIX separate quarter-end compliance certificates, one per test date since the facility's first_test_date: certificate.pdf inside Finance Uploads/Monthly Close/2025/03, 2025/06, 2025/09, 2025/12, 2026/03 and 2026/06. Each is a distinct physical file that happens to share the identical filename 'certificate.pdf' across every month's folder (confirmed: 18 of 18 months 2025-01 through 2026-06 each carry their own certificate.pdf, per the execution config's own _compliance_certificate_folder_note). The 'value' transcribed per test date is the certificate's own 'Calculated FCCR' line -- a bare number stated by the borrower, not derived.
- JUDGMENT CALL (recorded per the module's 'source' field, which is singular): this file's top-level 'source' is set to the bare filename 'certificate.pdf' per the module spec's documented shape, which lets the engine locate ONE representative file via its own recursive search of reporting_packages_folder (compliance_certificate_folder is blank for this borrower). Because every month's certificate shares that same filename, the engine's search resolves to whichever one instance it encounters first (folder-walk order, not necessarily 2026-06's own certificate) -- the source_files entry the engine attaches to these cert refs may therefore point at a DIFFERENT month's certificate.pdf than the one a given test-date value was actually read from. To keep every value independently traceable regardless of which file the engine's search resolves to, each value's own 'quote' field embeds that certificate's own header and 'Reporting period' line, naming its exact source certificate in the transcription itself. Alternative reading: run six separate certified-values files (one per historical period) through six separate engine invocations, each with its own period-matched 'source' -- rejected because the task calls for one consolidated cross-check across all reachable test dates in a single module output, and the per-entry quote already disambiguates the physical file.
- The FCCR is tested QUARTERLY only -- interim (non-quarter-end) months in the borrower's monthly package also carry a running FCCR figure marked 'NOT TESTED' (informational only, per the execution config's own note) and are NOT transcribed here as certified test results, consistent with the covenant-spec's own note that only quarter-end dates are tested under a flat, non-stepped 1.20x minimum.
- No amendments or waivers exist for this facility (confirmed by covenant-spec.json's own full-text sweep) and Covenant EBITDA is contractually defined to equal Reported EBITDA with zero permitted add-backs, so no add-back reconciliation was needed while transcribing these six certificates.

## How this period's figures were prepared

- Company-prepared/unaudited model; EBITDA taken from the model's single pinned EBITDA bridge row (not recomputed). Thresholds and definitions per the standardized covenant-spec; each definition's flow/spot basis is explicit in the spec. cert_* fields are the borrower's own certified actuals transcribed from the compliance certificate -- reported alongside the model reconstruction, never merged with it.

## Flagged this period

- **min_fccr** (not_evaluable) — insufficient_history
