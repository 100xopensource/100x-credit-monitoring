# debt-service-schedule — Nemeris Field Systems, LLC, 2026-06

Run engine-a53883cda480 · 2026-09-15T05:43:11+00:00

## How this period's figures were prepared

- Contractual schedule generated from the validated debt-service-spec (terms per the executed agreement; rates and grid steps per the spec's pinned observations, index held flat beyond the last observation). Decimal arithmetic quantized to the cent per accrual; the life-of-loan identity is asserted before writing. No figure here comes from a ledger.

## Flagged this period

- **flag** (assumption) — Adjusted Term SOFR is held flat beyond the last pinned observation (2025-01-15) from 2025-02 onward — re-pin observations.index_rates and re-run when the index moves
- **flag** (ambiguity) — The Credit Agreement says only 'actual days elapsed over a 360-day year' -- it does not state, in so many words, whether both the first and last day of an accrual period are counted. count_both_endpoints is set to true here because that is the reading that makes a FULL calendar month count its true number of calendar days (e.g. 31 for January), which is the standard actual/360 bank-loan convention and is consistent with 'actual days elapsed'. The reading is untested at the one place it actually matters: the funding-month stub (January 15-31, 2025 -- 17 days inclusive vs. 16 days exclusive) and the maturity stub (through January 15, 2030). Neither stub is large enough to move the Net Leverage Ratio, but it does move the exact cash-interest cent amount in those two months. Recorded for step-7 sign-off; alternative reading is count_both_endpoints: false (16-day / exclusive stub).
