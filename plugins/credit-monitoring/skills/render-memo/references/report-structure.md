# Monitoring report — structure (synthesis output)

The monthly monitor renders ONE self-contained HTML report per period, in a FIXED five-tab structure so every
borrower and every month reads the same way. Mirror the fixed tab structure, layout, section order,
class names and inline CSS from the shipped memo template, and build the deal's report by replacing
the data. That replacement covers the `<head>` too: set the document `<title>` to `<Borrower> —
Monitoring Report — <Month YYYY>` — it labels the browser tab, and because it never shows on the page
it is the example datum most often left behind (the Header rule under Section order restates it where
it renders).

Two invariants hold throughout:
- Every fact lives in EXACTLY ONE place. The Overview states conclusions and DEEP-LINKS (anchor `href`,
  e.g. a Performance metric → `#sec-is`) to the detail section that holds the numbers — the template JS
  makes anchors work across tabs. A figure you repeat does not yet have a home. Detail sections carry
  PLAIN NAMES — no letter prefixes ("A ·", "F ·") in headings, prose references, or trace-box text.
- Surface & flag; NEVER a credit recommendation. The Evidence discipline below bounds every claim.

## Time windows — there are TWO; do not conflate them

- **Analysis window = calendar year-to-date** — January of the current year through the current period.
  You READ and reason across this whole path (direction, inflection, volatility, cumulative), not
  two-point MoM/YoY snapshots. It GROWS through the year (Oct → Jan–Oct).
- **Display window = trailing 5 months** ending at the current period (June → Feb–Jun). The statement
  tables SHOW these five monthly columns only.

Compute both with `references/period_windows.py` — `windows(current_period)` returns the display months,
the YTD analysis months, the `t3m_pair`, the complete quarters, the fiscal years and the trailing
windows with their annualisation factors. Never hand-roll the month math.

## Like-for-like comparison windows

Every comparison in the memo pairs a window with the window that is genuinely comparable to it. Two
rules govern, and both are resolved by `period_windows.py` — never hand-rolled:

- **A trailing window compares against the SAME CALENDAR WINDOW A YEAR EARLIER, never the sequential
  preceding window.** T3M (Mar–May) compares against Mar–May of the prior year, not Dec–Feb. Sequential
  trailing windows are not like-for-like for a seasonal borrower: they read a seasonal swing as
  performance. `period_windows.py` returns this as `t3m_pair`.
- **A quarter compares against the same quarter a year earlier** (Q1 2026 vs Q1 2025), for the same
  reason. Only COMPLETE quarters are shown; a quarter closes on its third month. If the prior-year
  comparable predates the model's history the comparison shows "—".

**A percentage is suppressed, not printed, when it would mislead.** Two cases, both handled in
`performance_views.py` and both rendered as words rather than a ratio:
- the comparison crosses zero (EBITDA +$5K to -$2,577K) → show "sign change" and the two levels;
- the base is below 1% of that window's own revenue (EBITDA $66K on $31,489K of revenue) → show the
  dollar move and "off a near-zero base", never "+1,755%".

A column is tagged not-like-for-like when its window SPANS a `basis_breaks` entry, or when the two
windows it compares sit on opposite sides of one. A window lying wholly on the old basis is a faithful
reading of that basis and carries no tag — tagging every window that merely reaches back before the
break buries the one column that actually mixes the two.

## Layout — five tabs on a wide page

The report renders as FIVE FIXED TABS in one file, switched by a sticky tab bar directly under the
header banner. Tabs, order and ids are fixed so `#tab=` hashes are stable across borrowers:

| Tab | id | Holds |
|---|---|---|
| Overview | `tab-overview` | covenant strip · performance + collateral boxes · short executive summary · 18-month trend |
| Deal summary | `tab-deal` | company · key terms · transaction summary · security summary · capital structure |
| Covenants | `tab-covenants` | covenant strip (repeated) · covenant detail & stress |
| Financial performance | `tab-financials` | income statement, cash flow and balance sheet reviews |
| Files & audit | `tab-files` | source-document registry · data quality & basis notes · flags & follow-ups |

Overview is the default. The template JS owns switching: a click on ANY in-page anchor first activates
the tab that CONTAINS its target, then the browser jumps — deep links keep working across tabs, so the
one-home-per-fact invariant holds across the whole file. Print shows every tab in order and hides the
bar.

The page uses the full width of a desktop screen (`.wrap` caps at 1760px). Sections pair side by side
in `.hrow` grid rows exactly where the example pairs them — Company beside Key terms, Transaction
summary beside Security summary, the two metric boxes, the statement reviews — and every pairing
collapses to one column below 1280px. A wide table scrolls inside its own `.tblwrap`; the page itself
never scrolls horizontally.

## Section order (fixed)

### Header — above the tab bar, visible on every tab
Borrower, period (latest reporting month), fund, prepared date, deal team. The prepared line STOPS at
the date — the plugin version is carried in an HTML comment, never printed for the deal team.
Pills carry the house view at a glance, from `our_view`: the watchlist pill with its trajectory ("On
watchlist · Stable", or "Not on watch · Improving"). The banner states the view; the Executive summary
carries the reasoning. A field the deal context does not carry renders no pill — never a guess. The
  header is a plain teal band (`.wavebg`) behind a neutral eyebrow line ("Portfolio Monitoring · Credit Monitoring
  Fund") above the title.

The document `<title>` is always `<Borrower> — Monitoring Report — <Month YYYY>` — it is the first
thing the reader's browser shows, and the example's own title is example data like any other figure.

The deal team renders as one line under the prepared line, from deal-context `static.deal_team`
(proposed at setup and confirmed by the analyst). Render it only when the list is present and
non-empty; omit the whole line otherwise — never an empty "Deal team:" label. These are the deal
names the memo is allowed to show. `memo_payload.settled_deal_team` drops a list whose
`static.deal_team_provenance` says the analyst did not confirm it, and drops any entry carrying an
officer title; the run records the removal, so the data-quality card says why the line is missing.

The header is plain text and does not ship a logo chip. Keep the memo neutral and let the content
carry the report.

### Tab 1 — Overview: the monthly monitoring view

#### 1.1 Covenant status strip — FIRST on the tab, never hidden
The strip sits OUTSIDE any collapsible so covenant status never hides. Order within the strip:
- ONE chip per covenant of the agreement, all the SAME size and weight. An either/or group's
  governing chip (e.g. "§6.8(b) either/or — Compliant") and a covenant tested on its own (a
  liquidity minimum, a backstop) are peers, so both get the large status-bordered chip. A
  minimum-cash test rendered smaller or plainer than its neighbours reads as the lesser covenant,
  and it is not one.
- The group's chip carries the GROUP's verdict as the engine settled it — never a leg's "Breach", and
  never a verdict the strip derives for itself while the detail section prints another.
- Name a group by its SECTION of the agreement ("§6.7(b)"), which is what the reader holds. The group's
  id in `covenant-spec.json` (`s6_7_b`) never reaches the page — not on a chip, not in the Key terms
  covenant row, not as a covenant's sub-label on the detail grid.
- Leg-level chips (Pass / At-risk / Breach per leg) NEST beneath their group's chip, smaller and
  indented. The smaller size says "part of the chip above"; it never means "less important".
- ONE governing chip, never two. The compliance certificate's own verdict — and how many tests our
  figures do not reconcile to it — is the strip's HEADER clause ("— June 2026 · borrower certified
  compliant · one test unreconciled"), not a second large chip beside the group's; on a borrower whose
  covenants all sit in one section the two said the same word about the same section.
- Include ONLY covenants whose next scheduled test falls within 12 months of the current period.
  Far-out covenants (next test > 12 months away, e.g. a leverage test first measured next year) appear
  ONLY in the Covenant detail section's closing block, never in the strip. Links to the Covenant
  detail & stress section.
- A chip states the covenant, not its formula: the payload shortens each name for the chip (the
  "either/or leg" tag and a trailing parenthetical come off), and keeps the full name for the grid.

#### 1.2 Performance & collateral metrics — the two boxes side by side, OPEN
The two boxes sit in one `.hrow` (Performance left, Collateral right) and render OPEN by default —
the persistent header bar with its ONE teaser figure stays, a reader may still collapse a box, and
print force-expands (see Style). ALL content is kept — the box pattern hides nothing. Teasers:
Performance → YTD revenue vs prior year %; Collateral → the senior hard-asset coverage ratio.

- **Performance** — the box title is the single word "Performance"; no qualifier, no
  parenthetical, no basis note beside it, and NO footnote block beneath the table. What each column
  means belongs in its trace box, where the reader can click for it.

  **FOUR TOGGLED VIEWS over the same rows.** Which time basis is the right one is company- and
  situation-specific: a seasonal borrower is misread month to month, a borrower mid-inflection is
  misread on a full fiscal year, and a borrower whose prior-year comparable month happened to be
  unusual is flattered or punished by YoY alone. So the box offers all four and lets the reader choose.
  A segmented control switches views; `monthly` is the default.

  | View | Columns | Shows |
  |---|---|---|
  | Monthly | MoM · YoY · T3M vs prior-year T3M · vs Budget | change |
  | Quarterly | the last 8 complete quarters, oldest first | levels, each with its prior-year comparison |
  | Annual | the last complete fiscal years · current YTD · YTD annualised | levels, each with its prior-year comparison |
  | Annualised | LTM · L6M annualised · L3M annualised · YTD annualised | levels at a twelve-month rate |

  Rows are the same in every view: net revenue, gross profit, EBITDA and operating expense, each margin
  following its parent as a sub-row (basis points in the change view, per cent in the level views).

  **Pre-compute every view in Python; the toggle only swaps what is displayed.** Run
  `references/performance_views.py --borrower-config <cfg> --out <perf.json>` and embed the payload.
  The browser performs NO arithmetic: a number computed in a `<script>` tag is neither gated by the
  validator nor traceable to a model cell, and the same inputs must reproduce the same memo. The
  payload follows the usual trace stores — method once per ROW, window definition once per COLUMN,
  operands per CELL — so it stays a few tens of KB even with four views. Its money figures are already
  in dollars — the engine converts from the model's declared unit, and each converted row's method line
  says so — as is the plan behind the vs-Budget column, which the engine converts alongside them. Embed
  the payload unchanged; scaling it again is the thousand-fold error in the other direction.

  No shading on any column, in any view: shading implied a "primary" basis, which is the judgment this
  toggle hands back to the reader. Colour green/red = favourable/unfavourable; opex GROWTH is neutral
  (it grows with revenue). Budget cells are "—" and the column is omitted when no budget is on file.
  Links to the Income statement review.

- **Collateral metrics** — per-deal, driven by `collateral-map.json`. Track the pledged
  asset lines that appear on the financials + coverage ratios vs funded debt. Component columns:
  level · MoM · YoY · vs Budget — moves shown as DOLLAR deltas WITH the percentage alongside, e.g.
  "+6,796 (+174%)" (dollar first: collateral is recovery math and dollar deltas are additive across
  components; a % on a de-minimis base reads as noise). The vs-Budget column compares each pledged
  component to the pinned budget's balance-sheet plan for the same month, read by `memo_payload.py`
  through budget-map `balance_sheet_rows` (keys match the collateral-map component labels) and traced
  to the budget workbook's own cells; INPUT-GATED — when the pinned budget has no balance-sheet section
  or budget-map carries no `balance_sheet_rows`, the column shows "—" and the box carries ONE note
  saying so, never a per-cell flag. A budget that plans only some of the components shows those and one
  note naming the rest; the total row is compared against plan only when every component it counts is
  planned. Links to the Balance sheet review. Liabilities
  (deferred revenue, etc.) are NEVER collateral; show inventory only if the borrower has it.

#### 1.3 Executive summary — the analyst's one-pager for the deal team

ONE card, five labelled prose blocks in a FIXED order — the period update, then Liquidity,
Collateral, Covenants, Outlook — written the way a portfolio review reads to partners: the figure,
its comparator (vs Budget, vs prior year), then the driver. Prose, not bullets. Each block runs 2–5
sentences; each label deep-links to the section holding its detail, and anything longer belongs in
that detail section. The template owns the labels and the renderer sets the first block's cadence —
the writer writes only the prose.

- **Monthly update / Quarterly update** (`SUMMARY_UPDATE`) — the renderer titles this block from the
  period: a March/June/September/December period reads "Quarterly update" and its prose covers the
  quarter and the year so far; any other month reads "Monthly update" and covers the month and the
  year so far. Content: revenue and EBITDA vs Budget and prior year — the period first, the
  cumulative line second — then the driver attribution: which segment or brand moved, mix or margin,
  at the finest level the package reports (a package with brand-level revenue names brands; one
  without names mix and margin).
- **Liquidity** (`SUMMARY_LIQUIDITY`) — period-end cash against the floor, WHERE the cash came from
  (earned, borrowed, working capital — a balance can rise while every dollar is financing), months of
  runway when burning, and the expected direction with its basis (the plan, or the borrower's own
  guidance, named as such).
- **Collateral** (`SUMMARY_COLLATERAL`) — pledged components and their total, coverage against funded
  debt, and the prior-year or plan comparison where one is on file.
- **Covenants** (`SUMMARY_COVENANTS`) — one sentence when everything passes with room. Otherwise: the
  failing or governing leg with this period's figures, the certified-vs-model gap and which basis
  governs, and the nearest tight test with what clearing it takes.
- **Outlook** (`SUMMARY_OUTLOOK`) — the forward indicators the deal file carries (backlog, order
  book, seasonality and its timing), the governing question for the credit over the next test dates,
  and the open asks. Never a concern manufactured to fill the space.

**Numbers in these five blocks round to $X.XM / $XXXK.** Full precision lives in the tables, where
every figure is click-traceable; eight digits in a summary sentence is noise.

**Bad news lives in its home block, with the same syntax as good news.** There is no separate
red-flags list: a covenant miss is written in Covenants, a burn problem in Liquidity, a revenue miss
in the update — stated plainly, with figures and one consequence clause, exactly as a beat would be.

**Diagnosis is the summary's purpose.** Each block carries at least one read the tables do not state
directly — the quality of a comparator, a plan-shape artifact (months that cannot miss because their
plan columns are actuals), a move the reporting cannot yet explain and what would resolve it. A
summary that only restates table rows has failed, whatever its length.

**Analyst focus items are answered INSIDE these blocks, unlabeled.** No "you asked" list, no separate
focus block, no marker chips — the answer sits where the subject lives, as ordinary prose. An item the
period's data cannot answer says so in its natural block and goes to Flags as an open question. The
write-back still records each item's one-line finding for next run's continuity.

**The quiet-month pattern:** a performing credit with wide headroom produces SHORT blocks — one or
two sentences each — and stops. A quiet month must look quiet; that is what makes a loud month
legible. A concern is never manufactured to fill the space.

**Neutral worked example** — match this register and density:

> "**Covenants:** The model and certificate agree that all tests pass with more than 20% headroom."
> · "**Liquidity:** Cash is $5.0M against a $2.0M floor; $2.0M came from this month's draw, so
> operating cash generation remains the next test."

**Monitoring lenses — the completeness rule for the whole card.** Four lenses, FORWARD-looking
over the next 12 months / next two test dates: **Covenant · Liquidity · Performance · Revenue base**.
A fired trigger MUST be written into its home block — Covenant → Covenants, Liquidity → Liquidity,
Performance and Revenue base → the update (or Outlook when the concern is forward) — and a lens whose
colour CHANGED month over month leads its block's sentence with the movement ("liquidity moved from
amber to red this month: ..."). Colours come from this table — the same facts must fire the same
triggers every month:

| Lens | Red | Amber | Green |
|---|---|---|---|
| Covenant | Actual uncured breach; or projected breach within the next 2 test dates on current run-rate (for an either/or: BOTH legs projected to fail) | Headroom < 15% on any covenant at the latest test; or a no-cure test within 12 months that the current run-rate fails | All pass with ≥ 15% headroom and no projected breach within 12 months on run-rate |
| Liquidity | Cash below floor; or months-to-floor < 6 on BOTH burn bases; or an obligation inside 6 months that run-rate cash cannot cover | Months-to-floor < 12 on EITHER burn basis; or the cash build is financing-driven; or amort/maturity inside 12 months not covered by run-rate FCF | Months-to-floor > 12 on both bases, no uncovered obligation inside 12 months |
| Performance | T3M EBITDA (or the deal's primary profitability metric) negative AND worse than the prior-year window | Behind plan YTD on revenue or EBITDA beyond the materiality floor; or margin trend negative ≥ 3 months | At or ahead of plan, margins stable |
| Revenue base | Like-for-like trailing revenue contracting (T3M vs prior-year T3M, or LTM below prior FY) AND a planned growth driver absent or concentration rising | Trailing revenue flat; or > 50% concentration in one brand/customer/counterparty; or revenue behind plan beyond floor | Growing like-for-like, concentration stable, on plan |

An either/or covenant passing through one leg is the structure WORKING — single-leg reliance alone
never fires a trigger; what matters is whether the surviving leg is projected to hold. Lens colours are
model-proposed from these rules; the name-level watchlist and trajectory stay analyst-owned in
`our_view` and render as the header pills. No RAG grid renders anywhere.

#### 1.4 18-month trend chart
ONE chart, TWO toggled views — **Absolute ($)** and **Margins (%)** — never both line families on one axis.
Absolute: net-revenue bars + gross-profit / EBITDA / operating-expense lines. Margins: gross-margin /
EBITDA-margin / opex-%-of-revenue lines, with faint net-revenue bars for scale context. Mark any basis
break with a dashed vertical rule.

**Quarter callouts.** Every COMPLETE calendar quarter in the window gets one box, in a band above the
plot, over the month that closes the quarter, carrying that quarter's figures for the four series the
current view draws — same order as the legend. A quarter the window does not hold all three months of
gets NO box: a clipped quarter totalled anyway reads as a weak quarter, and that is an error the reader
cannot see. Same rule per series — a series missing a month inside the quarter shows a dash in the box,
never a two-month total. **A quarter level is the three months added up; a quarter margin is the
quarter's numerator over the quarter's revenue, NEVER the average of three monthly margins.** All of it
is computed in `memo_payload.py` and gated by `validate_memo.py` before it reaches the page — the chart
positions and formats, and adds nothing up. A quarter whose months straddle a basis break is still
shown, marked `†`, and the axis caption says what the mark means. The band is reserved above the plot
rather than drawn over it, so the boxes never cover the months they summarise.

### Tab 2 — Deal summary: the static deal record

The deal as underwritten and documented — it changes on amendment or restructure, not month to month.
Sources: deal-context `static.*` plus the executed documents, and every element cites its document. A
field the sources do not carry renders "not on file" (muted text, or the amber `.notice` for the
security summary) — NEVER invented, never estimated, never padded from general knowledge. Layout:
Company beside Key terms, Transaction summary beside Security summary, Capital structure full width.

- **Company** — one paragraph from `static.business_profile` (what the company does, how it makes
  money, the scale and retention or equivalent economics of the base), citing the underwriting memo
  once, then two compact fact tables: ownership / management / footprint on the left; end markets /
  concentration / key metrics on the right.
- **Key terms** — the deal's economics as label:value rows from `static.facility`, each row citing the
  executed document that sets it: borrowers, lenders / agent, guarantors, closing date, facility and
  accordions, maturity, amortization, cash interest rate, PIK rate, OID / closing fees, ongoing fees,
  exit or success fee, prepayment protection, ECF sweep, distributions basket, collateral, borrowing
  base (or "none" with the underwriting basis), and ONE financial-covenants summary row that
  deep-links to the Covenants tab — thresholds live there, not here. This card replaces the old
  collapsed Exposure box.
- **Transaction summary** — ONE paragraph in the fixed house pattern: facility and structure (each
  tranche and accordion with its fee mechanics) → lenders and agent → use of proceeds → pro forma
  leverage at close with its EBITDA basis → security and guarantees. From `static.facility`, citing
  the executed documents and the underwriting memo. An element the context does not carry (use of
  proceeds is the common gap) is named as not on file in a `.notice`.
- **Security summary** — who holds the loan, at what basis, as of period end. The holder rows restate
  the EXECUTED LOAN DOCUMENTS — the agreement's parties clause and any assignment — never inferred
  from fund names; the split across fund vehicles is LEFT UNDETERMINED unless a document
  states it. Columns: hold at close (par) · cost as % of par (par net of any OID per the fee letter;
  warrants are a separate instrument, not a discount) · amortized cost at close · principal
  outstanding incl. PIK at period end (from the borrower's debt schedule or the model when the
  loan-level balance is separable; otherwise it is an open request, said so) · fair value · enterprise
  value · loan-to-EV. Marks and enterprise value come from fund accounting / the valuation process;
  when absent they render "not on file" inside the amber sourcing notice that closes the card.
- **Capital structure** — DEDUCED, and the heading says so. Rows in order: our instrument(s) from the
  executed documents (rate, maturity, undrawn accordions noted); every other lender from the
  borrower's debt schedule when one is delivered, each with its ranking against our lien — a surviving
  prior lien is NAMED, with the collateral it sits on; an explicit UNATTRIBUTED RESIDUAL row bridging
  the identified instruments to the model's funded-debt line — a balance the package does not explain
  is a finding, stated, never hidden; the funded-debt total the Leverage numerator uses; memo rows
  below the total for debt-like items that are not funded debt (tax liabilities, deferred revenue);
  equity at book with its caveat named (restatement, goodwill share); total capitalization. Close with
  one leverage-context line deep-linking the Covenants tab.

### Tab 3 — Covenants

The covenant status strip repeats at the top of the tab (same chips as Overview — the strip is
template-identical in both places), then:

- **Covenant detail & stress** — FINANCIAL COVENANTS ONLY. The grid seats the covenants that carry a
  `primitive` in the spec; reference-only entries (reporting, affirmative, negative obligations) get no
  grid seat, no chip and no list. An obligation appears in the memo only when BROKEN, and then in Flags
  & follow-ups (a missed deliverable in tier 2, a breached negative covenant in tier 1). One closing
  line may say that is where obligations are tracked.

  The grid: certified-vs-model, BOTH sides assessed, each with its own status and headroom (engine
  emits `status`/`headroom_*` and `cert_status`/`cert_headroom_*`), a Δ column explaining each gap in
  one clause (definitional vs unreconciled), OR-logic explicit. Breaches and at-risk tests first. A
  missing certificate or a covenant absent from it is stated as a finding, not skipped. Covenants whose
  next scheduled test is MORE THAN 12 MONTHS out render only in a compact closing block titled "Not
  tested within the next 12 months" — one row each, no stress rows, no strip chip.

  **Covenant history — its own COLLAPSED block inside this section.** One matrix: a row per financial
  covenant, a column per test date from that covenant's first test, each cell a pass / at-risk / breach
  chip tracing to its test's required and actual. Lead with the tally per covenant ("breached 11 of 18
  tests since Sep 2024"). The model side always shows; the certified side appears for months whose
  certificate has been transcribed, and when history is model-side only the block says so once.
  Not-evaluable months render "n/e", never pass or breach. The matrix is history, not news — collapsed
  by default, and nothing in it seats the strip.

  **Forward stress — SITUATION-AWARE, from a confirmed menu.** Scenarios are DERIVED, never invented:
  (a) unchanged run-rate; (b) the plan met exactly; (c) one scenario per PRESSURED thesis pillar in
  deal-context, built by removing that pillar's planned contribution from the plan; (d) the breakevens.
  Cap: four. The monthly run puts this menu to the analyst in its run-start question (monthly-monitor
  owns that ask) and renders what was confirmed. Assumption discipline: every scenario names its
  assumptions; where a needed input exists in NO document (e.g. marketing spend by brand), the scenario
  shows BOUNDS, each computed from the plan's own ratios (gross-margin flow-through vs
  contribution-margin flow-through), both disclosed — never one silently-assumed number. Every scenario
  row traces its arithmetic.

### Tab 4 — Financial performance

The income statement and cash flow reviews stack in the left column; the balance sheet review takes
the right. Each review keeps its full content:

- **Income statement review** (folds Revenue quality) — the trailing-5-month table (plus a YTD column;
  flows only) then **Key observations (year-to-date)** — a handful of substantive bullets read across the
  whole YTD path, NOT a line-by-line table. For recurring-revenue deals include the ARR bridge, retention
  (organic vs combined across any basis break) and the deferred-revenue read.

- **Cash flow review** — the trailing-5-month table (plus a YTD column) then Key observations (YTD). State
  it is a derived indirect statement; ending cash ties to the balance sheet by construction; flag any
  period the derived net change does not reconcile to the balance-sheet cash move.

- **Balance sheet review** (folds Liquidity & runway + collateral detail) — the trailing-5-month table
  (NO YTD column — balances do not sum) then Key observations (YTD), then liquidity/runway (burn on the
  YTD operating figure, months-to-floor, deferred-revenue cushion, one-time distortions flagged) and the
  collateral coverage detail.

### Tab 5 — Files & audit

- **Source documents** — the audit registry: EVERY original resource the report rests on, grouped —
  loan documentation · underwriting & diligence · monthly reporting · budget & forecast · working
  analysis artifacts — one row per document: the linked name (the same store URL
  the figure traces already resolve — no new URL derivation), what it is in one sentence written for
  a credit reader, and what it is dated or covers. A document the report relies on but cannot link is
  still listed, with its location described in words. The registry lists sources; it repeats no
  figures.
- **Data quality & basis notes** — restatements, extraction/format limits, definition gaps (certificate
  vs model), basis breaks, budget absence, derived-CF reconciliation, capitalization policy. Honest but
  quarantined — the Overview only inherits a caveat if it changes a conclusion.
  Each note is written for the credit reader: name the figures it affects and what to trust, in plain
  words. File names and month labels are fine; engine, module, config-key and file-format names are not
  (a reader cannot act on "path_in_store" or "aging_rows_do_not_sum_to_total"). When a note has a
  fix, say who does what in one clause — "ask the borrower for the signed certificate", "the monitor
  needs the folder connected" — never a config edit or a command.

- **Flags & follow-ups** — numbered open items, each a borrower ask or an unresolved question, in THREE
  TIERS: (1) items touching covenant compliance or covenant trajectory; (2) missing REQUIRED deliverables
  (agreement-mandated reporting not received); (3) everything else. Within each tier, items with a dollar
  amount sort DESCENDING by amount; unvalued items follow the valued ones in severity order. Sort keys come
  from the carry-forward items' `category` and `amount_usd` fields when present; where absent, classify
  from the item text. Numbering runs continuously across tiers.

## Language contract — every reader-facing sentence

The memo is read by credit professionals under time pressure. The register is concise business
prose: facts first, numbers attached, one consequence clause, stop. The contract governs ALL prose —
summary, Key observations, covenant clauses, Data quality notes, Flags.

- Lead every sentence with the fact and its number; the consequence follows in at most one clause.
- Executive summary budget: five blocks of 2–5 sentences each; a quiet month runs one or two per
  block. If it runs longer, cut commentary, never facts.
- BANNED — intensifiers and emotion: "notably", "critically", "importantly", "concerning",
  "alarming", "comfort", "impressive", "striking". The number carries the weight; adverbs add none.
- BANNED — contrastive antithesis and negative parallelism: "not X, but Y", "X — but it is also Y",
  "passing today, however...", "it is less about X than Y". State each fact once, directly. Two
  facts that pull in opposite directions are two sentences; the reader can hold both.
- BANNED — preamble and throat-clearing: "it is worth noting", "the real story is", "what matters
  here". Delete and start with the fact.
- BANNED — commentary ABOUT a number in place of the number: "flattering", "artifact", "masks",
  "overstates", "understates", "the one bright spot", "paints a picture". State the two facts and let
  the reader collide them. Before: "May's +16.8% YoY is the one flattering number and it's an
  artifact — May 2025 was last year's trough." After: "May is +16.8% YoY, however last May was a
  trough month."
- BANNED — desk shorthand that does not decode literally: "the ask", "understates the ask", "prints",
  "lands", "paces to", "run-rate math". Write what is meant. Before: "the -4.7% YTD miss understates
  the ask." After: "January–March plan columns are actuals, so only April (-2.2%) and May (-18.9%)
  were real forecast tests; both missed."
- No corporate doublespeak: "headwinds", "softness", "challenged" — name the actual movement and
  its size instead.
- Worked example. Before: "Cash is not the near-term issue, but it is borrowed cash." After:
  "Cash $5.0M vs the $2.0M floor; $2.0M of the balance is this month's draw."

**Trend corroboration (applies to every trend claim).** A sentence asserting a trend (rising, falling,
five straight months, drifting) pairs with a corroborating fact from ANOTHER statement, module, or the
covenant schedule when one exists — the driver line behind it, the cash consequence, the covenant test it
approaches. Where the outputs surface no companion fact, the plain trend statement stands alone; a
companion is never invented to fill the pairing. Causes are still cited or asked, never asserted
(Evidence discipline).

## Statement reviews — how to write Key observations

**The admission test — every bullet passes all three or it does not render:**
1. It states a MOVEMENT or a LEVEL against a benchmark (plan, covenant, own history, prior year).
2. It clears the materiality floor.
3. It carries a consequence or an ask in the same breath.

**The evergreen test:** a sentence that could appear unchanged in ANY month's memo regardless of the
numbers is not an observation — it is a methodology statement, and it lives in Data quality & basis
notes, stated once. "Ending cash ties to the balance sheet by construction" and "this is a derived
indirect statement" are the canonical offenders: true by definition, informative never. A borderline
fact ("investing was zero all five months") earns its seat only with the implication attached ("no
capex while burning — the entire outflow is opex and working capital"). The Step 4b fact-check pass
applies this test and deletes what fails it.

Read the FULL analysis-window series for every line of the statement, then surface only the handful that
matter, each a real analytical read grounded in the path — for example: a line negative or positive EVERY
month with no inflection; the single line that IS the driver (revenue up but EBITDA flat because R&D/S&M
absorbed the gross-profit gain); a monotonic build (deferred revenue rising every month); a within-year
trough a MoM view misses (cash near the covenant floor in month X); the TRUE run-rate on a YTD basis (e.g.
operating burn) versus a misleading single month; a cost capitalized rather than expensed showing up
across statements. Keep it to the material few, not every line.

Cross-statement articulation belongs here, in this prose — net income → cash flow & equity;
Δ working capital → operating cash; ending cash → balance-sheet cash — not in separate tie-note boxes.

## Comparison discipline

LEAD with the YTD path (the analysis window). When you must compare two points, choose the LIKE-FOR-LIKE
one in this order: same-month prior year (no basis break) → sequential month (same basis) → trailing
window (same basis). When an acquisition/divestiture breaks the basis, SAY SO ONCE in Data quality, tag
affected comparisons inline ("basis break"), and never lead with a number the reader must discount.
Seasonal borrowers: sequential moves are noise — prefer YoY or seasonal-window comparisons.

## Evidence discipline (what synthesis may assert)

- **Causes are cited or asked — never invented.** Every causal claim ("driven by", "due to", "consistent
  with PIK/timing/seasonality") points at a specific module-output fact (a line-read record, a bridge
  component, a variance row, a covenant figure). If no output contains the cause, ASK: state the
  unexplained move, name candidates as candidates, and put the question in Flags & follow-ups.
- **Aggregate deltas reconcile before they get a story.** Any MoM/YoY/YTD move you narrate is first
  decomposed against per-line records (line_read for BS/IS aggregates; mom_bridge for net income; CF
  totals for cash), naming the lines that cover the bulk. A delta whose components cannot be identified is
  reported as UNRECONCILED — a finding, not a licence to guess.
- **"New"/"first-time" claims carry their evidence** — only from a module flag, after checking that
  record's `prior`/`last_observed`/`run`. If history is short, write "first appearance in the model window
  (model starts <month>)", never "nil previously" off a zero baseline.
- **"Debt" states its basis** — name the rows behind any figure labelled debt and, where covenant-spec
  defines Indebtedness/Total_Debt, reconcile to it (both figures, gap explained). A debt figure that
  ignores an existing covenant definition may not appear in the Overview.

## Judgment pass over the full series (not just fired flags)

Deterministic line-read flags are a FLOOR, not the ceiling. Before writing, read the per-line monthly
`series`, the full `line_read` records, and the additive signal blocks — `ratio_trends`,
`working_capital_divergence`, `reclassifications`, `emerging_trends`, `performance_review`,
`balance_sheet_yoy`, `ebitda_addback_register`, `funded_debt` — and narrate the developing / interrupted /
relational stories point-in-time rules miss, each still bounded by the Evidence discipline. Routing:
- **Covenant detail & stress (covenant/leverage):** `funded_debt`, leverage incl. Debt/Gross-Profit, `ebitda_addback_register`.
- **Balance sheet review:** `balance_sheet_yoy` levels, `working_capital_divergence`.
- **Income statement review:** `performance_review` (MoM / YoY / YTD-vs-PY / TTM-vs-PY), led by the
  like-for-like lens; revenue-mix and margin `ratio_trends`.
- **Cash flow / statement notes:** `emerging_trends`, `reclassifications` — as business facts.

## Style
- **Palette & look (fixed for every borrower):** the teal family carried in the example's `:root`
  — ink `#0a2a3f`, muted `#5c6f79`, hairline `#e4eaec`, page/wash `#f3f6f7`, navy `#0a2a3f`,
  accent `#137488`, accent-soft `#e9f2f4`, spot highlight `#eef4f5`, header wave gradient
  `#2a8ea1`→`#0c4763`; status greens/ambers/reds in the muted tones (`#1f7a4d` / `#a8720f` / `#b23a2c`).
  Type is Helvetica Neue; section headings are small uppercase letter-spaced eyebrow labels with a short
  accent dash rule (not large bordered headings); cards use tight 5–6px radii with a subtle shadow;
  tables have transparent headers with thin rules; stat chips carry a top accent border; covenant chips
  lift on hover. Trend-chart series follow the same palette (net-revenue bars `#b9dde3`, gross-profit
  `#0a2a3f`, EBITDA `#1f7a4d`, opex `#a8720f`, margin lines in accent/green/grey). Copy the example's
  CSS/JS verbatim; never restyle per deal.
- **Collapse mechanics:** the two Overview metric boxes are collapsible cards (persistent header bar + teaser figure,
  chevron, body hidden until click) per the example's pattern. The chevron is a VISIBLE affordance —
  at least 16px, accent-coloured, rotating on expand — not a decorative dot; a reader who cannot see
  that a box opens never opens it. The covenant strip is NEVER collapsible.
  `@media print` force-expands every collapsible (IC memos get printed); the trace-init JS runs on the
  full DOM so collapsed content keeps working traces when expanded.
- deal-context `static.business_profile` is a diagnostic LENS everywhere, never a section.
- Findings are business facts ("the warranty reserve has not moved in five months"), never rule/tag jargon.
- Source every figure (file + period); where certified and model values both exist, label which is which.
- **Write every MONEY figure in dollars.** Three of the borrowers keep their model in thousands, so a
  money figure in a module's `results` may be a thousandth of what it means: 9,750 for $9,750,000. Each
  output's envelope says which — `reporting_unit.scale`, how many dollars one figure stands for — so
  multiply a money figure by it before it reaches the page. **Multiply nothing else.** A result says what
  kind of quantity it is in its own `unit` field: `usd` is money and scales, while `ratio`, `%`, `x` and
  `days` do not — a gross margin of 0.35 multiplied by a thousand claims a 350x margin, which is the same
  class of error in the other direction. The engines' own prose (headlines, notes) is already in dollars,
  whatever the model's unit. A memo that understates a borrower a thousand-fold reads as a collapsing
  business. This rule governs figures written in PROSE. The engine-rendered statement tables display in
  the borrower's own reporting unit and say so in the corner header ("($000)") and the basis line, and
  the collateral table displays in $MM — the payload behind them, and every trace, stays exact dollars.

### Figure traces — ONE interaction, THREE visual patterns

The archived module JSONs carry per-figure `source_ref` and `calc` notes. Surfaced figures USE that
trace, all the way to the ORIGINAL files. Nothing traceable ever looks like a hyperlink.

- Trace data lives in INVISIBLE spans (no underline, no color — plain text): `span.trc` for a computed
  figure (`data-calc` context, `data-steps` labeled value lines, `data-hops`), `span.srclink` for a
  direct-source figure (`data-href` + sheet/cells detail in `title`; NEVER a raw `<a href>` — host pages
  restyle and navigate real anchors), `span.srcmiss` when the source is known but its store link is
  unresolved.
- The CONTAINER is the control; init JS classifies each span's context:
  - in a TABLE CELL → the whole td/th is clickable (`.tcell`): hover tints the cell, click opens the
    box, the open cell stays highlighted;
  - in a STAT CHIP (coverage chips) → the whole chip is the control, same behavior;
  - in PROSE (a sentence or bullet — no cell to highlight) → the span itself carries a thin neutral
    dashed mark (`.pmark`), the ONLY text-level marker in the memo.
  Selection feedback is per CONTAINER, not per row: hovering highlights the individual cell / chip
  (prose marks tint on hover), and the container whose box is open keeps a `.trc-sel` highlight until
  the box closes (click-away or Esc).
- The box is WRITTEN FOR THE MEMO READER — an analyst who knows the borrower's source files and knows
  NOTHING about how the memo is produced. Cite what the READER can open: the source document, the model
  workbook, the memo's own sections. Never name plugin internals in box text: no config/spec/map/archive
  file names (borrower-config, covenant-spec.json, certified-values, source-map, module JSONs), no engine
  or script names, no run mechanics ("at setup", "at synthesis", "module", "transcribed to...").
- Two box variants, one design:
  - COMPUTED figure — heading "How this figure was arrived at": a one-line context sentence, then THE
    CALCULATION AS A FEW LABELED VALUE LINES (component → value, result line last, never more than ~4
    lines), plain business language from the module's `calc` note — never formula/tag jargon; then each
    hop — the model cell (sheet!cells from `source_ref`), then the borrower's own document, READ OFF
    the ref's `upstream` entries (`store` + `path_in_store`, and `sheet` or `section` where present).
    The module resolved that hop when it produced the figure; rendering re-derives nothing.
  - DIRECT-SOURCE figure — heading "Source": the CITATION ONLY — file + where in it (sheet/section/page,
    from the span's `title`) — and the file link. No boilerplate sentence; when the location detail is
    uncertain, the file link alone is enough.
- URLs via `monitor_lib.store_link` — the envelope's `stores[<store>].url_base` and the ref's
  `path_in_store`, as the SharePoint address that OPENS the file (`monitor_lib.store_url` for a
  path). A memo link opens the file the way a dashboard link does; the plain path address
  downloads it, and put the workbook in the reader's Downloads folder. The box's hop links
  ("Open: <file>") and the section jump chips are the ONLY real anchors in the memo; nothing
  bare-navigates — a reader always gets the box first, the file one click deeper.

### Coverage is a contract — EVERY number answers when clicked

A number that answers nothing when clicked is a DEFECT, not a style choice. The data is stored once per
ROW and once per COLUMN so the payload stays small; the template JS composes each cell's answer.

- Statement tables (`table.traced`): clicking ANY cell composes its trace from two stores — the row
  (`<tr data-method>` states HOW the line is computed; computed rows — subtotals, ratios, bridges, every
  derived-CF line — also carry `data-steps`, the CURRENT month's arithmetic as labeled value lines,
  result last) and the month column header (a `trc` naming the model column + the package file that
  supplied it, from source-map). A row with no `data-method` is transcribed — the renderer states "as
  reported, transcribed from the package below". Never store per-cell payloads.
- The YTD header states the sum rule and defers to the month headers for provenance.
- A figure COMPUTED outside the tables — covenant model values, stress breakevens, headroom, burn,
  months-to-floor, coverage ratios, retention — carries its own standalone `trc` with its calculation
  steps; certified/package-stated values are `srclink`s to their file.
- SNAPSHOT boxes: Exposure value cells hold `srclink`s to the executed documents deal-context names for
  them (per-figure sources captured at setup — LSA, fee letter, IC memo); the funded debt cell is ONE
  trace whose box shows the model sum AND the certified Indebtedness with both hops (one cell = one box —
  a cell never holds two competing traces). The Performance box is a traced table in every view — COLUMN
  headers carry the window definition (which months, and what it is compared against, from
  period_windows.py) + the model hop, metric rows carry `data-method`, and each cell carries its own
  operands from the pre-computed payload. Switching view re-composes the cells from that payload and
  re-binds the traces; a view the reader has not opened yet still answers when they do. The Collateral component table is
  traced the same way (rows = collateral-map component reads; columns = level / MoM / YoY / vs Budget,
  the moves as dollar-with-% pairs; the vs-Budget cells hop to the pinned budget workbook via budget-map
  `balance_sheet_rows`); its coverage chips are chip-pattern traces. Covenant chips stay internal links —
  their values live in the Covenant detail & stress section.
- Covenant THRESHOLDS and stress schedules `srclink` to the credit-agreement/amendment section they come
  from (the same terms covenant-setup distilled into covenant-spec.json). The trend chart carries a
  caption source line to the model.
- A prose figure that merely RESTATES a table cell inherits the table's coverage — do not re-link it
  (every fact lives in exactly one place).

### Degradation — never a broken or guessed link

- An output whose envelope carries no `stores` → no link resolves; Data quality & basis notes carries
  ONE note, written for the reader: "Figures name their source file but do not open it — this
  borrower's files are in a folder the monitor does not know." Instead of flagging every figure.
- A figure whose ref names no store (the envelope has others) → `srcmiss`: the container still answers (the box names the
  file, no link) + a Data quality & basis notes flag.
- A ref with no `upstream` → the trace stops at the model cell + ONE Data quality & basis notes note:
  "Figures trace to the model but not through to the borrower's own monthly file for this month." The
  module records the gap; the renderer reports it rather than trying to close it.
- A trace hop whose file cannot be resolved renders as a PLAIN-TEXT hop line naming the file (`h: null` —
  the calculation steps still show).

The report never restates lineage prose — the trace answers "which file?" and "how computed?" directly.
