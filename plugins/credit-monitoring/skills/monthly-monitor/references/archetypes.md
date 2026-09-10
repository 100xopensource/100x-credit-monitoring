# Deal archetypes — the synthesis lens selector

`deal-context.static.archetype` names what kind of business the credit is, and therefore which
failure mode the memo watches first. monthly-monitor reads it at synthesis (Step 4) to select the
liquidity framing, the KPI emphasis, and the collateral read. It is INTERPRETIVE — it never changes
a computed figure, and it never overrides the objective ratio selection (`analysis.business_model`
in borrower-config still drives which ratios the engines compute).

Set at onboarding by monitor-setup: Claude infers an archetype from the IC/UW memo's business
description and puts it to the analyst as a confirm-or-override alongside the other judgment
fields. Analyst skips → the inferred value is written with `"provenance": "inferred — unconfirmed"`
and the memo's lens notes the framing is unconfirmed. Nothing fits → `"unclassified"`, which keeps
today's generic framing. The vocabulary is OPEN: add an archetype when a real deal demands one, and
write its framing block against that deal, not a hypothetical.

## Controlled vocabulary (initial)

### consumer-inventory
Sells physical product to consumers (D2C or retail). Cash lives in the inventory cycle.
- **Liquidity framing:** runway framed on inventory-to-cash conversion and customer-advance
  funding, seasonality-adjusted; customer advances read BOTH as forward demand and as a liability
  owed in product; refund rates as brand health.
- **KPI emphasis:** marketing efficiency (MER/ROAS) vs plan, revenue by brand/SKU concentration,
  refund %, customer-advance balance and conversion, inventory turns and build vs season.
- **Collateral read:** led by inventory quality, turns, and any prior-ranking claims on in-transit
  or financed stock; AR is usually de minimis.
- **Watch first:** marketing efficiency decaying while spend rises; inventory building ahead of
  unproven demand; advances converting slower than the season needs.

### services-project
Revenue from people delivering work (BPO, agencies, project/contract services).
- **Liquidity framing:** payroll is the burn floor — runway framed on committed payroll vs
  contracted/backlog revenue; unbilled AR and WIP as the swing factor.
- **KPI emphasis:** backlog and bookings coverage of the plan, utilization, contract and customer
  concentration, unbilled-to-billed conversion, DSO.
- **Collateral read:** receivables-led; recovery framing is cash-flow based — say so rather than
  pretending hard assets cover.
- **Watch first:** a top-contract loss or renewal cliff; utilization sliding while headcount holds;
  unbilled AR aging past its billing cycle.

### asset-heavy-industrial
Manufacturers and industrials with real plant, equipment and order books.
- **Liquidity framing:** runway framed on order-book conversion and working-capital absorption
  (raw-material buys ahead of shipments); fixed-cost absorption drives margin reads.
- **KPI emphasis:** backlog and book-to-bill, gross margin vs volume (absorption), on-time
  shipment/percent-complete where reported, input-cost pass-through.
- **Collateral read:** equipment and inventory led, appraisal-anchored where one exists; a
  borrowing-base mentality even without a formal borrowing base.
- **Watch first:** backlog thinning while fixed costs hold; margin compression at flat volume
  (input costs eating pass-through lag); inventory building without matching backlog.

### unclassified
Fallback. Generic framing (today's behavior): liquidity as cash vs floor with burn math, KPIs from
deal-context `key_operating_kpis`, collateral per collateral-map without archetype emphasis. The
memo's lens block notes "archetype unclassified — generic framing".

## How the memo consumes it (Step 4)
- The liquidity/runway passage in the Balance sheet review opens with the archetype's framing, not
  the generic burn line, wherever the framing's inputs exist in the model.
- The lens block's Liquidity and Revenue-base clauses are written against the archetype's "watch
  first" list.
- KPI emphasis orders the Income statement Key observations — the archetype's KPIs come first when
  the month's data speaks to them.
- The collateral read in the Balance sheet review leads with the archetype's collateral logic.
- Framing NEVER suppresses a fired flag or alters a figure; it chooses emphasis and vocabulary.
