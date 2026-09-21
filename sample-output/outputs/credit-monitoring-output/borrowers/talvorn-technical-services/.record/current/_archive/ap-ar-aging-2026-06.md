# ap-ar-aging — Talvorn Technical Services, LLC, 2026-06

Run run-20260916-083949 · 2026-09-16T08:49:19+00:00

## What to keep in mind reading these figures

- DSO/DPO are single-month and volatile. DPO uses configured basis 'operating_cogs' (model line 'Cost of Revenue'). This borrower's A/R and A/P aging exports report Current/31-60/61-90/90+ bucket totals only, with no named customer or vendor rows anywhere in the file or the deal folder -- top-N concentration is empty for both sides by the export's own shape (genuinely no counterparty-level data available), not a config gap.

## How this period's figures were prepared

- Company-prepared aging exports (unaudited). Balance-sheet AR/AP, revenue, and the configured DPO-basis line are read from the canonical monthly financials model via monitor_lib.
