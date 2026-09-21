# debt-service-schedule — Veyrona Decision Systems, LLC, 2026-06

Run engine-f1287dbc490a · 2026-09-16T09:26:20+00:00

## How this period's figures were prepared

- Contractual schedule generated from the validated debt-service-spec (terms per the executed agreement; rates and grid steps per the spec's pinned observations, index held flat beyond the last observation). Decimal arithmetic quantized to the cent per accrual; the life-of-loan identity is asserted before writing. No figure here comes from a ledger.

## Flagged this period

- **flag** (ambiguity) — Day count is completely unstated (unlike a sibling facility in this portfolio whose agreement at least says 'actual days elapsed over a 360-day year' without specifying stub treatment -- Veyrona's agreement says nothing at all about day count). actual/360 with both endpoints counted is used here as the market-standard direct-lending default, purely so the generator can run; it is not sourced from the agreement. Because the interest RATE itself is also an undisclosed placeholder (see cash_interest.margin_grid), this convention choice has no real financial consequence today -- both gaps should be resolved together once actual pricing terms are confirmed with the Administrative Agent.
