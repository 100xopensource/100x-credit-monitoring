# Module spec — Three-Statement Analysis  (FAN-OUT TARGET)

Engine: `three_statement_analysis.py` (+ `trend_signals.py`). Depends only on build-monitoring-model
(the IS/BS/CF model) — no budget required — so it is effectively always-on once the model exists.
Optionally reads covenant-spec (cash floor) and deal-context `static` (conditioning) when present.

## Units — compare on the model's unit, print in dollars

This module carries more dollar figures than any other: the materiality floor, the cash floor, the
bridge tie tolerance, the thresholds behind the financing-inflow and cash-flow-anomaly flags. Two
rules keep them straight. Anything COMPARED against a model figure is put on the model's unit first.
Anything a PERSON reads is put back into dollars — the memo used to tell a borrower reporting in
thousands that its revenue was $9,750 when it meant $9,750,000, and that it would hit a $750,000 cash
floor in 0.0 months on cash of $1,100.

Numbers we hardcode are dollars and are converted (the $10,000 minimum under the materiality floor).
Numbers a person writes are on the model's unit: `analysis.cash_floor`, a pinned
`line_read.materiality_floor`, and a min-cash covenant threshold, which follows its spec's own
`threshold_unit`. The floor's disclosed basis states dollars either way, so the bar can be read
without knowing the scale. Unconverted, that floor sat 177x too high on a real thousands-scale deal —
$10,000,000 instead of $56,585 — and twenty material line moves were invisible.

The unit comes from `model.scale` + `model.currency` in borrower-config (required) and rides on the
output's envelope.

## Purpose

Give the memo its analytical backbone AND its commentary: metrics, trend, a complete per-line
trend read of BOTH statements with rule-based flags, sustained-drift detection, a
month-over-month Net Income bridge that is guaranteed to tie, revenue-mix and opex-group
trends, and liquidity/runway — all deterministic, so synthesis narrates guaranteed facts.

There is NO statistical screening — no z-scores, no median/MAD, no dispersion gates. Statistical
screens fail exactly where monitoring matters most: a 2σ screen on a young deal with four months of
balance-sheet history returns nothing, and outlier logic is structurally blind to zero-variance
anomalies, monotonic slides just under the bar, and composition shifts inside a stable total. Instead
this module does a PURE LINE READ: every leaf line on both statements is read and trended every month,
and flags are deterministic RULES on an absolute materiality floor that work from the SECOND month of
history. Nothing is gated; everything is emitted; flags annotate, synthesis triages.

## COVERAGE STATEMENT (what this module will and will not catch)

- **Every IS leaf line and every BS leaf line** is read and trended each month: level, prior,
  MoM, trailing-3m average, YTD (IS), consecutive same-direction run, months frozen at an
  identical value, last-observed value and gap for dormant lines, and common-size share
  (of revenue for IS lines, of total assets for BS lines) with the shift since segment start.
- **Rule flags** (single absolute materiality floor; NO statistical baseline; all tunable).
  The floor is PER BORROWER: pinned at monitor-setup (`analysis.line_read.materiality_floor`) or,
  when unpinned, derived each run as max($10,000, 5bps of trailing-12 same-basis annualized
  revenue) — never a universal constant. Precedence: line_read.materiality_floor >
  outlier_min_abs (legacy) > scale-aware default; the resolved floor and its basis are disclosed
  in `line_read.params.floor_basis`. Flags:
  - `frozen` — same nonzero value ≥ N consecutive month-ends (default 3). Catches unmeasured
    reserves and stale accruals.
  - `monotonic_run` — ≥ N consecutive same-direction MoM moves (default 3) with material
    cumulative change. Catches gradual slides and builds.
  - `sign_flip` — latest and comparison base have opposite signs (larger side material). The
    comparison base is the adjacent prior month, or the LAST OBSERVED value when the line was
    dormant.
  - `new` / `vanished` / `resumed_after_gap` — first material appearance; an active line
    stopping (adjacent prior month material, now zero/absent); a dormant line re-posting
    material activity.
  - `big_move` — material MoM move that is also large in % terms (default ≥ 25%); falls back
    to the last observed value for dormant lines. The noisiest tag — a ranking aid, not a memo
    flag list.
  - `share_shift` (BS) — share of total assets moved ≥ N points since segment start
    (default 5). Catches composition shifts inside stable totals (e.g. cash migrating
    between accounts).
  - `related_party` — row label contains a related-party keyword (officer / affiliate /
    related / due from / due to / shareholder / intercompany). Always reported.
- **Drift** (rule-based): per P&L leaf, sustained multi-month bleed within the longest usable
  same-basis segment — catches slow moves even when individual months are noisy.
- **Revenue mix / opex groups**: every revenue leaf trended; per-group opex trends.
- **CF**: configured totals only, plus rule-based one-time-OCF flagging in liquidity (any
  material financing inflow in the trailing window is listed as fact; OCF sign anomaly vs
  the majority sign of prior months).
- **NOT covered**: customer/counterparty detail (aging + revenue-quality modules), budget
  comparison (budget-vs-actual), covenant math (covenant-compliance), and any valuation view.

## Inputs (borrower-config)

- `model_output_path` — the only required input.
- `model_lines` (revenue, gross_profit, operating_cogs, ebitda_bridge, net_income,
  interest_expense, pre_financing_subtotal OPTIONAL); `opex_groups`; `revenue_components`
  OPTIONAL (else revenue leaves auto-detected above the revenue total).
- `comparability_items` — one-time items for normalized metrics and the bridge's ex-one-time walk.
- `analysis` — objective tuning ONLY:
  - `line_read {materiality_floor, run_min_months, frozen_min_months, big_move_pct,
    share_shift_ppt}` — floor default: scale-aware (see COVERAGE); others default 3, 3, 0.25, 5.0.
  - `basis_breaks: [{effective, reason}]` (legacy `{month, note}` also accepted) — entity/consolidation changes (e.g. an acquisition).
    Analyst-pinned at setup (machine-readable here, narrative in deal-context).
  - `business_model` — `recurring_revenue | product_inventory | services | mixed` (else sniffed
    from deal-context `business_one_liner`, else `unspecified`).
  - `cash_floor` — else resolved from a covenant-spec min-cash / liquidity covenant's
    `threshold_schedule`, else null.
  - `fiscal_year_start_month`, `trend_window`, `outlier_min_abs` (materiality floor),
    `drift {min_months, min_total_pct, min_same_sign_share}`, `seasonality`, `thresholds`.
- `paths.deal_context_path`, `paths.covenant_spec_path` — read if present (see Conditioning).

## Context conditioning (read-only; selection & parameterization, NEVER value adjustment)

The engine may read deal-context `static` and covenant-spec to decide WHAT to compute and WHICH
thresholds apply. It must NEVER alter a computed figure based on context. Every conditioning
decision is recorded in `context_conditioning.applied[]`. Uses: basis breaks → segmentation;
business model → ratio-suite selection (no inventory metrics for a SaaS company); covenant-spec →
cash floor; seasonality → flag suppression. Schema-driven suppression applies regardless of
context: a ratio whose input lines don't resolve (or resolve to zero/absent inventory) is
suppressed, never guessed.

## Method

1. **Segmentation first.** Split the month series at each basis break. The CURRENT SEGMENT
   (latest same-basis months) is the window for line-read lookbacks, drift, direction,
   trailing averages, and TTM aggregates. TTM spanning a break → n/m with reason. YoY across a
   break is still emitted but tagged `crosses_basis_break: true` — never as a clean growth read.
   Segmentation is comparability, not statistics.
2. Metrics: IS (latest / prior / same-basis YTD), BS (spot), CF (latest + YTD); margins
   as-reported AND ex-one-time; working-capital days (DSO / DPO; DIO & CCC only if inventory
   exists AND business model warrants); leverage & coverage TTM within segment (n/m otherwise).
   EBITDA is the model's single pinned bridge row — never recomputed. Every suppressed ratio →
   `ratios_suppressed: [{ratio, reason}]`. Days metrics multiply by the month's calendar days
   (`monitor_lib.days_in_month` — the same basis the aging module uses, so one metric carries one
   basis across the memo).
   Funded debt comes from configured `total_debt`, else from debt-tagged balance-sheet leaves. When
   neither resolves — a balance sheet that summarizes its debt into one liability line matches no
   debt tag by design — leverage and interest coverage are suppressed AND a
   `funded_debt_unresolved` exception names the candidate rows and the fix (set
   `model_lines.total_debt`). Carry that flag into the memo: it is a one-line setup fix, not a
   permanent gap.
3. Trend descriptors (revenue, EBITDA, net income, cash, gross margin): MoM, same-basis
   trailing average, direction = sign of the MEDIAN of same-basis MoM deltas (never
   endpoint-to-endpoint), series peak/trough (tagged if pre-break), YoY per rule 1.
4. **LINE READ.** Every leaf line on the Income Statement and the Balance Sheet: facts + rule
   flags per the COVERAGE STATEMENT. Subtotals, memo/bridge rows and reconciliation notation are
   excluded (they aggregate what leaves already carry). Dormant lines are compared against their
   last observed value, with the gap disclosed. Works from the second month of history.
5. **Drift**: per P&L leaf line within the longest usable same-basis segment (≥ `drift.min_months`,
   default 6): flag when ≥ `min_same_sign_share` (default 0.7) of MoM deltas share sign AND
   cumulative change ≥ `min_total_pct` (default 15%) AND ≥ the materiality floor.
6. **Revenue mix**: each revenue component's level, share, MoM, same-basis YoY, direction.
7. **Opex-group trends**: per group — latest, MoM, same-basis YTD vs prior same-basis YTD
   (or n/a), direction.
8. **MoM Net Income bridge**: decomposition into Revenue / COGS-GM / Opex / below-the-line,
   telescoping through the model's OWN subtotal chain resolved from `model_lines`
   (`pre_financing_subtotal` if given, else derived as gross_profit − sum(opex_groups)). NO
   hardcoded labels. INVARIANT ENFORCED IN CODE: components must sum to the NI delta within $1;
   otherwise `mom_bridge.status = "unresolved"` + an exception entry — a non-tying bridge is
   never emitted as fact. Plus the one-time-aware normalized walk.
9. **Liquidity**: cash, 1-month and 3-month burn, rule-based distortion listing (material
   financing inflows in the window; OCF sign anomaly vs majority sign), cash floor (per
   resolution order), months to floor at 1m and 3m burn. Null floor → burn only.
10. Headlines (deterministic): revenue trend (like-for-like comparison chosen per rule 1),
    EBITDA trend, margin quality, the top flagged IS line OR top drift (whichever is larger),
    the top flagged BS lines in plain business language (frozen / monotonic / share-shift
    phrasing), the MoM paradox with one-time explanation, AND a liquidity headline whenever
    months-to-floor at 1m burn < threshold (default 3).
11. Threshold flags: normalized GM floor, one-time reliance, normalized EBITDA negative,
    negative equity, payables stretch, inventory heavy [only if inventory suite active],
    current ratio [only if resolvable], revenue below trailing [same-basis, seasonal-aware],
    plus `liquidity_thin` and `bridge_unresolved`.

## Output contract (return ONLY the engine's JSON)

The envelope plus these collections: `results`, `exceptions`, `headlines`, `trend`, `mom_bridge`,
`series`, `opex_composition`, `lines_not_found`, `segments`, `drift`, `revenue_mix`, `opex_trends`,
`liquidity`, `ratios_suppressed`, `context_conditioning`, `line_read` (`income_statement` /
`balance_sheet`, each with per-line facts, `flags`, a `flagged` subset ranked by |MoM|, and the
resolved `params`). The engine defines the exact shape; read it there rather than reconstructing it.

How synthesis is meant to use it: `line_read.*.flagged` MUST be read in full — that is the module's
noticing channel. The complete `lines` tables are the every-line record for lookups and the memo's
appendix; reference them, do not re-ingest them wholesale.

Invariants: the bridge ties or is marked unresolved; the line read covers every leaf on both
statements (subtotal/memo rows excluded); every suppressed ratio and every conditioning decision is
disclosed; numbers reproduce on re-run.

**Provenance.** The envelope carries `period` (the run's single ISO `YYYY-MM` stamp), `source_files`
(deduped source-path list) and `figure_collections` — the meta collections holding figures
(opex_composition, trend, mom_bridge, drift, revenue_mix, opex_trends, line_read, liquidity), which is
how the validator knows to walk them. Every memo-surfaceable record carries `source_ref` (into
`source_files`; config-sourced figures ref borrower-config sections) and a plain-text `calc` note —
the rule that fired with its months and values, or the formula and window. A flagged line's ref spans
exactly the cells its rules read (a 5-month frozen run refs that 5-cell range). **Gate the output with
`validate_output.py <out.json>` before archiving.** A model that fails to open writes the standard skip
record (`{"skipped": true, "reason": …}`), never a bare traceback.

## Engine vs driving agent (hard boundary)

The ENGINE computes everything in this spec. The driving agent runs it, reports failures, and
may add NOTHING to the output JSON — no custom keys, no hand-recomputed values. If the engine
cannot produce a section, the agent reports the gap. Engine bugs found mid-run are fixed IN THE
ENGINE, tested, and synced back — never patched in output.

**The engine file is long and exceeds a single file-read window. Copy it BYTE-FOR-BYTE to the
working directory (filesystem copy, or chunked reads with offset continuation until EOF), verify the
copy (byte count or checksum against the source), and run THAT file. A reconstructed engine is not the
engine — if a complete copy cannot be obtained, STOP and report the gap.**

## Source tier & discipline

Actuals from the company-prepared / unaudited model. All analysis objective/mechanical;
interpretation (credit meaning, outlook) belongs to synthesis, which reads deal-context. Rule
names and thresholds are internal reference — memo prose describes findings in plain business
language (the line, its level, the pattern, why it matters), never as tag jargon.

## Definition of Done

Metrics + ratios (or disclosed suppression) + flags; segmentation honored everywhere; the line
read covers EVERY IS and BS leaf with facts and rule flags (dormant lines compared to last
observed value); trend / drift / revenue_mix / opex_trends / mom_bridge (tied or unresolved) /
liquidity / headlines emitted; conditioning audit trail present; unresolved lines in
`lines_not_found`; numbers reproduce on re-run and tie to the model.

## Additive trend signals (`trend_signals.py`)

Beyond the per-leaf line read, the engine emits scale-aware **deterministic** signals that catch
developing / relational trends the fixed rules miss. Gates = `max(materiality floor, k_vol × the
series' own median absolute MoM delta)`; the `%` values are fallbacks; every gate is disclosed in
`trend_signal_params`. Tunables: `config.analysis.trend`.

- **`ratio_trends`** — DIO / DSO / DPO / CCC, current ratio, gross & EBITDA margin trended **monthly** over the current segment (same spot definitions as the ratio block); each carries `deteriorating` = true when it moves the adverse way by ≥ `ratio_rel` or for ≥ `trend_min_months`.
- **`working_capital_divergence`** — a working-capital asset (inventory / AR) building while revenue or COGS falls over the window (the overstock / collections signal).
- **`reclassifications`** — an offsetting move between sibling lines (same account stem, e.g. a loan's short-term vs long-term portion) that nets ~flat at the group level AND is large vs the group's balances — catches a whole balance moving classes (blank treated as zero); routine amortization is gated out.
- **`emerging_trends`** — per-leaf 3–5 month directional moves tolerating ≤1 reversal and/or ending within `run_end_tolerance` months of latest — surfaces interrupted or recently-ended trends the strict `monotonic_run` misses.
