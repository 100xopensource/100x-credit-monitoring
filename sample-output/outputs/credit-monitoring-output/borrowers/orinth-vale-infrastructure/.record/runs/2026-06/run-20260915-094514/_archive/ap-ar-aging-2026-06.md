# ap-ar-aging — Orinth Vale Infrastructure, LLC, 2026-06

Run run-20260915-094514 · 2026-09-15T09:54:51+00:00

## What to keep in mind reading these figures

- DSO/DPO are single-month and volatile. DPO uses configured basis 'operating_cogs' (model line 'Costs').

## How this period's figures were prepared

- Company-prepared aging exports (unaudited). Balance-sheet AR/AP, revenue, and the configured DPO-basis line are read from the canonical monthly financials model via monitor_lib.

## Flagged this period

- **June 2026 A/R top-5 concentration** (concentration_not_available) — The June 2026 A/R aging export is a pre-bucketed summary (one Amount per aging bucket, with a single illustrative 'representative' project/vendor label per bucket rather than a per-counterparty dollar breakdown) -- there is no genuine counterparty-level data in this export to rank. Bucket distribution, total, tie-out and days-metric are unaffected and reported normally; top-N concentration is reported as empty because none exists in the source, not because no concentration risk was found.
- **June 2026 A/P top-5 concentration** (concentration_not_available) — The June 2026 A/P aging export is a pre-bucketed summary (one Amount per aging bucket, with a single illustrative 'representative' project/vendor label per bucket rather than a per-counterparty dollar breakdown) -- there is no genuine counterparty-level data in this export to rank. Bucket distribution, total, tie-out and days-metric are unaffected and reported normally; top-N concentration is reported as empty because none exists in the source, not because no concentration risk was found.
