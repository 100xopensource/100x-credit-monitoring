---
name: covenant-setup
description: >
  One-time per borrower: distill the credit agreement into a standardized, machine-readable
  covenant-spec.json — controlled-vocabulary covenants, shared definitions, thresholds/schedules, test
  dates, cure rights, and any covenant the borrower may satisfy one of two ways — plus the facility's
  contractual debt service (debt-service-spec.json: fundings, rate grids, PIK, amortization, day count,
  fees) and the generated per-month schedule covenant Fixed Charges read. Triggers: "set up covenants",
  "extract covenant spec", "parse the credit agreement covenants", "build the debt-service schedule".
  Run once; monthly compliance reads the spec, not the agreement. Needs the credit-agreement path (it
  lives outside the reporting folder).
metadata:
  version: "0.9.1"
---

# Covenant Setup (one-time)

Turn the credit agreement into a durable, machine-readable covenant spec in the standardized contract
that `covenant-compliance` consumes. Do this once per borrower; re-run only on an amendment.

## Contract
- INPUT: `config.paths.credit_agreement_path` (PDF/docx, external to the reporting folder); the latest
  compliance certificate(s) for cross-check; `config/covenant-spec.template.json` and
  `config/debt-service-spec.template.json` (the shapes).
- OUTPUT: `config.paths.covenant_spec_path` → covenant-spec.json, conforming to the template and **passing
  `references/validate_spec.py`**; `config.paths.debt_service_spec_path` → debt-service-spec.json
  **passing `references/validate_debt_service_spec.py`**; and the generated schedule at
  `config.paths.debt_service_schedule_path`, produced by `references/debt_service.py` and **passing the
  standard output gate** (the generator refuses an invalid spec on its own).

## The standardized shape
`config/covenant-spec.template.json` carries the full contract (`_contract`) plus a worked example of
each primitive. Every financial covenant maps to ONE primitive — `min_level` (actual ≥ threshold, one
quantity), `max_ratio` (numerator ÷ denominator ≤ threshold), or `min_coverage` (numerator ÷ denominator
≥ threshold) — and carries: `id`, `name`, `primitive`, inputs (`metric`, or `numerator` +
`denominator`; each input is `{"ref":"<definition>"}` or `{"lines":[...],"op":"sum"}`), `window`
(`spot`/`ttm`/`t3m`/`custom_months:N`), `threshold` **or** `threshold_schedule` ([{effective, value}]),
`frequency`, `first_test_date`, `cure_rights`, `agreement_ref`, optional `cert_ref`, optional
`at_risk_band`, optional `cert_tolerance`.

Shared `definitions` (EBITDA, Total Debt, Liquidity, …) are defined ONCE and referenced by inputs; each
carries an explicit `basis` (see step 3), and a term the agreement fixes to stated per-month amounts for a
run of months carries that table as `deemed_schedule` (see step 3a). Affirmative/negative/reporting covenants may be captured as
reference-only entries: no `primitive`, plus `type: affirmative|negative|reporting` — the type tag is
what tells the engine to skip them.

An agreement that lets the borrower satisfy **one of two tests** — "EBITDA of at least X, or Liquidity
of at least Y" — states ONE covenant with two legs. Each leg is an ordinary covenant entry carrying
`either_or_group: <group id>`, and the pair is declared in the top-level `either_or_groups` block
(`members`, `rule: any_pass`, `rule_plain`, `agreement_ref`). Recorded as two separate covenants it
still validates — and then every month either leg misses its own level is published as a breach on a
credit that is compliant. Step 2b is where that gets caught.

## Who does which step
Step 0 (reaching the agreement) and step 7 (sign-off) happen HERE, in the session with the analyst.
Steps 1–6 — the sweep of hundreds of pages, the mapping, the validator — go to the plugin's
**covenant-extractor** agent (Task tool, subagent type `credit-monitoring:covenant-extractor`;
where the runtime doesn't offer plugin agents, use a general-purpose subagent, prepend the text of
`${CLAUDE_PLUGIN_ROOT}/agents/covenant-extractor.md`, and set that dispatch to sonnet at xhigh as the
agent file declares — prepended text carries no frontmatter). Its brief carries the borrower, the
credit-agreement path, the deal folder, the compliance certificate(s) and the `covenant_spec_path`
— each in both forms, the connected-folder path and the `/sessions/…/mnt/…` sandbox path, since the
spec is written with one and gated with the other. It returns the spec path, the validator result, one line per covenant, the
ambiguities for step 7, and the security provisions the collateral map needs — so no second pass over
the agreement is required.

## Method
0. **Reach the agreement.** It lives outside the reporting folder, inside the borrower's own folder in
   the deal documents folder. If that folder is not attached, ask for it with a menu
   (`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`), then read the agreement from inside it.
   Never conclude "no agreement" from a folder that isn't connected.
   If it is genuinely unavailable, offer a menu (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`):
   **[Connect the credit agreement] / [Skip covenants for now — I'll flag it]**; on skip, skip-and-flag
   (covenant-compliance is omitted downstream) rather than erroring.
1. **Extract** the agreement text (pdf skill / `pdftotext` / `pdfplumber`). Locate the Definitions section
   and the Financial / Affirmative / Negative covenant sections.
2. **Map each financial covenant to a primitive.** Identify the metric(s) and bind each to a shared
   definition (`ref`) or named statement lines (`lines`). Set the `window` (spot for balance-sheet items;
   ttm/t3m for flows, per the agreement). Capture the threshold — and if it steps by date, record the FULL
   `threshold_schedule` verbatim (every dated step). Record `frequency`, `first_test_date` (ISO),
   `cure_rights`, and the `agreement_ref` (§).
2a. **Write money thresholds as the agreement writes them.** If the agreement says
   $3,000,000, the spec says `3000000`, and the spec keeps `threshold_unit: "dollars"`
   (the template ships it set). The engine puts a dollar threshold on the borrower's own
   reporting unit before comparing — `model.scale` in borrower-config — so a model kept in
   thousands is handled for you. Do NOT do that arithmetic by hand: a threshold hand-divided
   into a model's unit cannot be checked against the document without redoing the sum, and
   whether it was converted stops being knowable. A ratio, a percentage or a number of days
   is not money and is never scaled.
2b. **Catch the covenants the borrower gets to choose between.** Read the financial covenant section for
   a choice: "either … or", "any one of the following", "so long as one of clauses (i) and (ii) is
   satisfied". Each choice is ONE covenant. Tag every leg `either_or_group: <group id>`, declare the
   pair in `either_or_groups` with `members`, `rule: "any_pass"`, the choice written out in one sentence
   in `rule_plain`, and the § in `agreement_ref` (worked example: `either_or_example` in the template).
   Two things that look like legs and are not:
   - A test the agreement applies **whichever** clause the borrower picks ("irrespective of Borrower's
     ability to choose between the foregoing clauses") carries NO tag — it is a covenant in its own
     right, and tagging it into the group would let another leg's pass excuse a real breach.
   - A choice the wording leaves genuinely open — available only in some periods, or a third test that
     may or may not sit inside the pair — is recorded as the reading the agreement best supports and
     listed as an ambiguity for step 7.
3. **Capture definitions verbatim** — EBITDA (with every add-back), Total Debt / Indebtedness, Liquidity /
   Unrestricted Cash (which accounts count). EBITDA is the single source of truth shared with the model's
   EBITDA bridge; no module may redefine it. **Stamp each definition's `basis`** — `flow` for period
   amounts (EBITDA, revenue, interest expense, fixed charges), `spot` for balances (cash, debt). Two traps
   here:
   - Interest expense computed as a single model line is still a FLOW — mark it `flow`, not `spot`;
     marked spot, a TTM coverage ratio divides twelve months of EBITDA by one month of interest and
     false-passes.
   - The inverse: a model row that is ITSELF already a trailing aggregate (named TTM / LTM / trailing)
     is bound `spot` — the row's cell already carries the windowed value, and binding it `flow` would
     window it twice (the engine warns loudly on this combination).
2c. **Capture how a DERIVED threshold was derived — not only the numbers it produced.**
   An agreement often sets each level by rule rather than by figure: "70% of the projected TTM
   EBITDA for each calendar month", "a 25% discount to plan", "the greater of $2,000,000 and 75% of
   the Approved Annual Projections". Record the rule in the covenant's `threshold_derivation`
   (`formula`, `basis_document`, `basis_note`, `roll_forward`) even though `threshold_schedule`
   already carries the resulting numbers. The numbers still govern the test; the rule is the only
   thing that says what BASIS they were calibrated on. A level set at a percentage of an ADJUSTED
   projection, tested against an unadjusted actual, publishes a breach on a compliant borrower. It also answers what happens when the tabulated months run out: name the
   clause that sets the next period's levels in `roll_forward`, so the monitor asks for next year's
   projections instead of going quiet. Where a level is simply stated, omit the block.

3c. **Capture the covenant section's PREAMBLE and any accounting-basis clause — `accounting_basis`.**
   REQUIRED on every spec, because it sits ABOVE the individual covenants and a per-covenant
   extraction walks straight past it. Many agreements bind the test to the borrower's OWN accounting
   rather than to GAAP in the abstract: "in accordance with Borrower's historic accounting practices
   as reflected in the most recent financial statements delivered by Borrower to Agent prior to the
   Closing Date", or "as calculated in the manner set out in the Model delivered on the Closing
   Date". That clause is what resolves a generic limb — a definition that permits "extraordinary
   charges" is settled by whatever the named document actually treated as extraordinary.
   - Quote the clause, cite it, and RESOLVE the document it points at. Agreements DESCRIBE this
     document rather than naming a file, so identifying it is the work: record `described_as`
     always, `document` when you find it, and `ebitda_build_note` for how that document itself
     builds EBITDA — the add-back lines on its face are what the monthly reconstruction gets
     checked against.
   - Cannot find it? Leave `resolved: false` and hand it up as an open request. The covenants that
     lean on it are then flagged as resting on an unverified basis; the run is not blocked.
   - No such clause in the agreement? Set `stated: false`. Most agreements carry none, and that is a
     fine answer — but a recorded "none" and a question nobody asked must not look the same, which
     is why `validate_spec.py` refuses a spec with the block missing entirely.
   - Feed the candidate add-backs you meet here to **monitor-setup**, which builds the analyst's
     `ebitda_addbacks` register in deal-context. This skill records what the agreement PERMITS; the
     analyst elects what is actually taken.

3a. **Capture the months the agreement DEEMS.** Read the EBITDA definition to its end. An acquisition or
   LBO financing commonly overrides the formula it just gave with fixed per-month amounts — "provided
   further that, notwithstanding anything to the contrary, for each of the calendar months set forth
   below, EBITDA shall be deemed to be the amount set forth below opposite such month" — usually covering
   the pre-close year, because no reconstruction of the target's own months under the new definition would
   be agreed by both sides. Those months govern over anything the model computes for them. Record the
   table on that definition as `deemed_schedule`: `months` keyed `YYYY-MM` to the amount, `stated_in:
   "dollars"` (the agreement's own figures — the engine scales them to the model's unit, same as a
   threshold), and the `agreement_ref` clause that deems them. Transcribe EVERY month the table lists, as
   one continuous run: `validate_spec.py` refuses a schedule with a hole, because a month left out is
   tested on the model while the months around it are contractual. Worth the care — most deemed months
   predate the model's first column, and they are what lets a trailing-window leverage test compute at all
   at its early test dates. Search the definitions and the financial-covenant sections for "deemed",
   "shall be deemed to be", "stipulated" and "agreed EBITDA" so a schedule stated somewhere other than the
   EBITDA definition is still found, and list a table whose months or figures read ambiguously as an
   ambiguity for step 7.
3b. **Extract the facility's contractual debt service into `debt-service-spec.json`.** Covenant Fixed
   Charges mean the facility's OWN cash interest and scheduled amortization — the contract, not the
   ledger — and this file is where the contract becomes machine-readable
   (`config/debt-service-spec.template.json` is the shape; every term carries its `agreement_ref`):
   - **Fundings per tranche** — amounts and dates, plus undrawn commitments (a DDTL) with their
     availability windows. Record the amortization-base definition VERBATIM (e.g. "percentage of the
     aggregate original principal amount of all Loans made prior to such date") — a draw enlarges the
     base, and the wording is what makes the engine's computation checkable.
   - **Cash interest** — index, floor, the margin grid keyed to the covenant metric (write each band's
     wording in `when`), adjustment cadence, first payment date. Rates are decimals: 5.25% is `0.0525`,
     and the gate refuses a rate that reads as a percent.
   - **PIK** — rate grid, capitalization mechanics verbatim, any borrower cash-pay election.
   - **Amortization** — start date, the annual grid % of base (÷ 12 monthly), balloon at maturity, and
     `fixed_charges_attribution`: an agreement whose Fixed Charges are amounts "paid or scheduled to be
     paid" counts an installment in the month it services (`accrued`) — the read that ties a borrower
     who runs the next 1st-of-month payment through the current test.
   - **Day count verbatim** — convention and whether BOTH the first and last day of a period count.
     Record any stub ambiguity (a maturity stub read two ways) in `day_count.ambiguities`; it becomes a
     step-7 sign-off question and rides into the generated schedule as a flagged exception.
   - **Recurring and exit fees** with their `fixed_charges_treatment` — a monitoring fee is usually a
     fee, NOT a Fixed Charge, but belongs in payoff and debt-service cash-flow views either way.
   - **The two pinned observation sets** — `observations.index_rates` (the index's published history,
     each entry with its source; at least the rate in effect at close) and `observations.grid_state`
     (the covenant metric per the certificates, starting with the closing state) — grid steps follow
     what was actually reported, never a projection.
   - **`covenant_support_rows`** — the model row labels the schedule feeds (e.g. "Cash Interest
     Expense", "Scheduled Debt Principal Payments"). They MUST match the labels the covenant-spec's
     Fixed Charges / coverage definitions bind, or the covenant engine reads rows nobody populated.
4. **Capture observable affirmative/reporting covenants** (e.g., monthly financials within N days) as
   reference-only entries.
4b. **Discrepancy rule — exhaust executed docs before flagging.** When the agreement and any other
   source (IC/UW memo, certificate, term sheet) state different terms, rates or fees, text-sweep the
   ENTIRE executed document set in the deal folder (fee letter, notes, disbursement letter, side
   letters, closing sets) before writing a discrepancy note. Fee letters in particular carry ADDITIVE
   economics (agent PIK, extra success fees) that reconcile apparent conflicts — both sources may be
   right at different layers. Flag only what a full sweep cannot resolve, and cite every layer's
   document and section.
5. **Cross-check the compliance certificate(s):** do the covenants/thresholds match? Set `cert_ref` — it is
   load-bearing: every month the covenant-compliance module uses it to find and transcribe the borrower's
   certified actuals off the certificate (see that module's spec). Note any discrepancy (the
   agreement governs unless amended). Capture borrower add-backs (e.g. a tariff add-back)
   only as a NOTE — an unsupported add-back stays out of the EBITDA definition.
6. **Validate, then generate.** Run `references/validate_spec.py covenant-spec.json` and
   `references/validate_debt_service_spec.py debt-service-spec.json`; fix each until it reports OK. Then
   generate the schedule:
   `python references/debt_service.py --spec <debt-service-spec.json> --as-of <YYYY-MM> --out <debt_service_schedule_path>`
   — it writes the per-month contractual schedule (validate_output-shaped, life-of-loan identity
   asserted to the cent) that build-monitoring-model reads to populate the covenant-support rows. When a
   later certificate moves the grid metric across a band, or the index publishes a new rate, append the
   observation to the spec and re-run this one command — the contractual blocks change only on an
   amendment.
7. **Human-review checkpoint:** covenants are high-stakes — put every ambiguity the extractor listed to
   the analyst before the spec is accepted. Each one is a menu (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`)
   naming what the agreement says and the two readings — **[Accept as parsed: <the reading recorded>] /
   [Use <the alternative>] / [Let me correct it] / [Leave this one open for now]** — with the reading
   the agreement best supports first and the § cited. Never an open-ended "does this look right?". The
   spec is already written by this point, so each pick lands in the file before the checkpoint closes:
   - **[Accept as parsed]** — the recorded reading stands as written.
   - **[Use <the alternative>]** or a correction they type — edit that entry in `covenant-spec.json` to
     the reading they picked, re-run `references/validate_spec.py` until it reports OK, and read the file
     back. The spec on disk carries what the analyst approved.
   - **[Leave this one open for now]** — the recorded reading stands so the spec still validates, and the
     closing summary lists that covenant or definition as still awaiting their sign-off.

## Definition of Done
covenant-spec.json written AND passing `validate_spec.py`; **`accounting_basis` recorded — the clause verbatim with its reference document resolved, or `stated: false`**; **every derived threshold carrying its `threshold_derivation`**; every financial covenant has a primitive +
inputs + window + threshold/schedule + frequency + first_test_date + § ref; every definition carries a
`basis` reviewed against what the metric actually is; EBITDA / Total Debt / Liquidity verbatim;
`cert_ref` set per covenant; **every either/or choice in the covenant section recorded as one group with
its legs tagged**; **every month the agreement deems recorded in that definition's `deemed_schedule`, the
full run, with its clause ref**; reference-only entries carry their `type` tag;
**debt-service-spec.json written AND passing `validate_debt_service_spec.py`, with both observation sets
pinned and `covenant_support_rows` matching the covenant-spec's bound labels, and the schedule generated
at `debt_service_schedule_path`**; ambiguities flagged for review.

## When you're done — suggest the next step
Running standalone, close with a menu (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`):
**[Build/refresh the monitoring model]** (now that the EBITDA definition and security provisions are
pinned — follow **build-monitoring-model**, then the collateral map) / **[Run the monthly review]** /
**[Done for now]**. When another setup wrapper invoked you, skip this — it owns the sequence.
