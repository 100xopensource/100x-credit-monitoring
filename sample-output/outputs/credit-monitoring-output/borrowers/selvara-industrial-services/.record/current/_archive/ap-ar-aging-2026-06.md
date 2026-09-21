# ap-ar-aging — Selvara Industrial Services, LLC, 2026-06

Run run-20260915-105656 · 2026-09-16T07:34:41+00:00

## What to keep in mind reading these figures

- DSO/DPO are single-month and volatile. DPO is suppressed this run: aging.dpo_basis is configured as 'operating_cogs', but no model_lines mapping exists for it, so there is no COGS/opex line to divide A/P by. AR-side DSO, aging buckets and concentration compute normally.

## How this period's figures were prepared

- Company-prepared aging exports (unaudited). Balance-sheet AR/AP, revenue, and the configured DPO-basis line are read from the canonical monthly financials model via monitor_lib.

## Flagged this period

- **DPO (days payable outstanding) not computable this run** (dpo_basis_unmapped) — aging.dpo_basis is configured as 'operating_cogs', but model_lines.operating_cogs is blank in borrower-config -- there is no COGS/opex line in this borrower's monthly package to use as the DPO denominator. AR-side DSO, aging buckets and concentration compute normally; only DPO is suppressed.
