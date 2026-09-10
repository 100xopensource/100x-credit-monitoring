---
name: monitor-setup
description: >
  Configure the credit-monitoring for a borrower. Run once per borrower (or to update paths,
  enabled modules, or the deal view). Writes TWO control files: borrower-config.json
  (mechanical — paths, pinned budget, module menu) and deal-context.json
  (interpretive — thesis, underwritten risks, our current view, carry-forward). Triggers: "set up
  portfolio monitor", "configure the monitor for {borrower}", "update monitor config / paths /
  deal context".
metadata:
  version: "0.15.0"
---

# Monitor Setup

Produce the two per-borrower control files every other stage reads: `borrower-config.json` (mechanical)
and `deal-context.json` (interpretive). Both are DATA and live in the analyst-CONNECTED output folder —
never inside the plugin, never inside the read-only reporting/deal folder. Templates are in `config/`.

When this skill is directly invoked by the analyst and `run-monitor` has not handed it resolved entry
context, first follow `${CLAUDE_PLUGIN_ROOT}/references/entry-routing.md` through its welcome and
permission menu. Preserve explicit setup intent. When `run-monitor` already supplied that context,
continue here without repeating the welcome or permission menu.

Before the first question, find what this session can reach through
`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`. If either source or store root is unavailable,
follow **store-setup** to obtain both folders.
Keep both connected roots in context and bind them on every plugin subprocess and worker brief; one
subprocess cannot export them to the next. Every later choice runs the same loop — look first, propose
what you found as options with the one you recommend leading, then act on the option the analyst picks
through `AskUserQuestion` (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`). Anything mechanical you can
see, just do.

**Run this skill in the session that talks to the analyst.** Every ASK here — the output folder, each
input path, the module menu, the Part B judgment fields — is a menu the analyst answers. A subagent
cannot ask, so a delegated setup writes control files with those answers blank and nobody notices until
the memo is missing the analyst's view. Setup itself runs here start to finish — the plugin's three
workers (`agents/`) belong to covenant-setup, build-monitoring-model and monthly-monitor, and setup
adds none of its own. Reading a budget tab or a deal document to fill a field is a few tool calls in
this session, not a dispatch.

**When a folder is missing**, name it in one line and ask with a menu, offering **[Open the folder
guide]** by following **open-folder-guide**. It is a help document for the analyst and never starts
setup or waits on the output folder. When every folder is there, say nothing about folders and get on
with setup.

## Contract
- INPUT: analyst answers + the borrower's deal documents + `config/borrower-config.template.json`
  and `config/deal-context.template.json`.
- OUTPUT: `borrower-config.json` and `deal-context.json`, both validated.

**Writing a control file:** start from the template, fill it, and write it with the Write tool — the
file's content IS JSON, so `true` / `false` / `null` are written as JSON. Reach for python only when the
values come from a file you just parsed, and then build a dict and `json.dump` it rather than pasting
JSON text into a script (JSON literals are not Python literals — that is what turns a config write into
a syntax error). Either way, `json.load` the file back from its final path afterwards: a write nobody
read back is not a saved file.

## Core principle (applies to both files)
Keep the OBJECTIVE layer and the INTERPRETIVE layer separate. Paths, the pinned budget, covenant math,
variances and aging govern. Deal context only changes how the memo is prioritized and read — it never
alters a computed figure.

## Part A — borrower-config.json (mechanical)

**Updating an existing borrower — check and fix, don't re-interview.** Read the existing
`borrower-config.json` and fill only what's missing or stale. If `model_output_path` still points into
`.record/current`, ask before changing it; verify the existing workbook and sidecars, copy them into
`<borrower root>/_model/`, verify the copies, then update the path. Leave historical runs and every
analyst setting unchanged.

### A-1. Identify the company only when none was named

The source may contain one or multiple companies. After the source folder is approved, list only its
visible top level and collect every apparent company-folder candidate. Do not assume the source folder
itself is the company. Sort candidates once in case insensitive order by displayed folder name. Do not
recommend a company: folder order is not credit evidence.

`AskUserQuestion` allows at most four choices, so use this bounded menu:

- With one or two candidates, show every name plus **[None of these]** and **[Not now]**.
- With three or more, show the next two candidates plus **[More companies]** and **[Not now]**.
  **More companies** advances to the next two candidates not previously shown; keep the same sorted
  list and current position across responses. On the final page, remove **[More companies]** and show
  the remaining one or two names plus **[None of these]** and **[Not now]**. Never wrap to the first
  page or repeat a page. This makes all candidates reachable without an infinite menu loop.
- **None of these** asks the single normal free text question allowed by `asking.md`. The Other box may
  also supply a company name at any page. **Not now** pauses without selecting a company.

A group or subsidiary name does not automatically define a separate borrower, so confirm the intended
scope when that is ambiguous. If the top level has no apparent candidates or is mixed and unclear, ask
the same normal free text company-name question.

Do not recursively search other company folders, inspect every borrower, or mix documents across
borrowers. An already named borrower skips this question and every candidate menu. The selected
company scope only limits what you inspect next; it does not select or bind any input path. Every saved
input still follows A1's analyst-selected path rules.

### A0. Choose the OUTPUT folder — ALWAYS ASK; never default (first after company identification)
Every borrower's work is saved in its own folder inside the monitoring folder (`store`). Do the
looking first: find the attached `store` folder, list what is already in it and
offer the borrower's own subfolder — the one that matches, or a new one named after the borrower —
ALWAYS plus **[Point me to a different folder]**. Proposing what you can see is not defaulting; the
analyst still picks. Until the folder is reachable, write nothing; never fall back to a temp or session
folder.

Every WRITABLE artifact lives UNDER that borrower folder. The stable builder workspace is
`<borrower root>/_model/`; lifecycle state and control files live under `<borrower root>/.record/`.
Nothing writable goes into the connected source/deal folder.

### A1. INPUT paths (read-only; live in the deal folder) — ALWAYS ASK; never auto-detect or guess
Each input path is analyst-SELECTED, never auto-detected or reused from another deal — guessing sends
setup hunting through the wrong folders.

0. **Write every path as its shared folder plus the path inside it** —
   `source:Example Borrower/6_Loan Monitoring/1_Reporting`, or
   `store:borrowers/example/.record/current/config/covenant-spec.json`. There are two shared folders:
   `source` is the deal
   documents folder (3_Current Portfolio) and `store` is the monitoring folder. That string works
   for whoever has the folder connected, however they connected it; the full path you are reading
   the file at works only here. When a file sits in neither shared folder, say so plainly in the
   analyst's words and ask them to put it in one.
1. `reporting_packages_folder`: ask the analyst to connect the deal's reporting folder. Offer it as a
   menu of the likely reporting subfolders you can see, ALWAYS plus "point me to it" — never a fixed
   two-option list. ONLY after it's connected, detect the latest month WITHIN that chosen folder and
   confirm it; ask about any gaps.
1a. `compliance_certificate_folder` (optional): ask where this deal files its certificates. Offer likely
   certificate subfolders you can see, ALWAYS plus **[Use the reporting folder]** and **[Point me to it]**.
   Keep the value blank when they choose the reporting folder; covenant compliance searches a configured
   folder first and falls back to reporting packages when the period's certificate is not there.
2. `credit_agreement_path` AND `ic_uw_memo_path`: ask the analyst to select each file. The credit
   agreement is OUTSIDE the reporting folder, elsewhere in the borrower's deal folder. Look through
   that folder yourself and offer the files that look right as a menu, ALWAYS plus **[Point me at
   it]** / **[Skip — I don't have it now]**. The file's own subfolder varies by deal. On skip,
   leave blank and warn plainly: no credit agreement → covenant-setup is blocked; no final/executed IC
   or UW memo → business_profile, credit_thesis and key_risks_at_uw come through as flagged drafts.
3. `budget_path`: ask the analyst to select the budget workbook (same menu) and confirm which tab/version
   IS the approved budget (`budget_version_note`).

### A2. OUTPUT paths and settings (writable; live in the CONNECTED output folder from A0)
4. Point `model_output_path` at `store:borrowers/<borrower key>/_model/<actual workbook>.xlsx`.
   Point `monitor_output_folder` at `store:borrowers/<borrower key>/.record/current`. Keep
   `covenant_spec_path`, `debt_service_spec_path`, `debt_service_schedule_path`, `deal_context_path`,
   `budget_map_path` and `collateral_map_path` under `.record/current/config/`. The model builder's
   source map, restatements, profile, and dated snapshots live beside the canonical workbook in
   `_model/`; completed runs snapshot the required files.
4a. **MAP the borrower's own statement row labels — `model_lines`, `analysis_lines`, `opex_groups`.**
   Open the borrower's income statement and balance sheet and read the labels off them, then write each
   canonical key to the label THIS borrower uses: `model_lines` (cash, accounts_receivable,
   accounts_payable, inventory, revenue, operating_cogs, ebitda_bridge, total_debt and the rest),
   `analysis_lines`, and each `opex_groups` entry. Charts of accounts differ per deal — one borrower's
   cash row reads `Total - 1000 - Cash`, another's `Cash and cash equivalents`. Confirm the mapping with
   the analyst before finishing, since every downstream figure is read through it. Leaving these at the
   template's values points three modules at the wrong rows, or at rows that do not exist: the template
   ships one borrower's labels as an illustration, not a default.

4b. **BUILD budget-map.json — this skill produces it, not just the path.** Open the pinned budget
   workbook (`budget_path` + the tab from `budget_version_note`) and fill in
   `config/budget-map.template.json`: `sheet`, `period_columns` (EVERY month the budget covers that the
   monitor may report on — a missing month makes budget-vs-actual fail that month), `metric_rows`
   (revenue, gross_profit, net_income, interest_expense, taxes, depreciation_amortization; plus
   financing_cost / interest_income only if the budget has them), `opex_groups` (the {add, subtract}
   shape reconciles a budget whose chart of accounts differs from the actuals'), and — when the pinned
   budget carries a balance-sheet section — `balance_sheet_rows` for the collateral components it plans,
   so the memo's Collateral box can show 'vs Budget'. Two key joins: `opex_groups` keys match
   borrower-config `opex_groups` EXACTLY, and `balance_sheet_rows` keys match collateral-map
   `pledged_components` labels EXACTLY.
   VERIFY before accepting: extract one known month through the map and eyeball revenue, gross profit,
   EBITDA, each opex group and any balance_sheet_rows against the budget tab; fix the map until they
   match.
4c. **BUILD collateral-map.json — this skill produces it too.** From the credit agreement's
   security/collateral provisions, fill in `config/collateral-map.template.json`: lien type, the pledged
   asset categories each mapped to its model balance-sheet row, the coverage cuts, and the funded-debt
   denominator. Liabilities are never collateral; include inventory only if the borrower has it. Write
   it to `collateral_map_path`; the report's Collateral metrics box reads it. This needs covenant-setup's
   parse of those security provisions first; on a standalone run, run covenant-setup first.
5. Set `modules[]` — the analysis modules to run for THIS deal (covenant-compliance, budget-vs-actual,
   ap-ar-aging, three-statement-analysis, revenue-quality, …). Modules are OPT-IN and INPUT-GATED: a
   module runs only if it is listed AND its inputs exist; otherwise the monitor omits that section and
   flags it. This is how deals without AP/AR aging — or with extra inputs like a borrowing-base cert —
   are handled.
5a. **READ THE MODEL'S UNIT OFF THE WORKBOOK — `model.scale`, `model.currency`.** Required: every
   module reads it, and a borrower that has not declared it gets a run where every module skips and says
   so. Open the borrower's income statement and look at the top of the columns: a caption reading
   "$ in thousands", "USD 000s" or "$'000" means `scale: 1000`; whole dollars mean `scale: 1`. Paste the
   caption's own wording into `unit_verbatim` — it is the evidence for the number. Set `currency` to the
   ledger's currency. Models commonly report in thousands, so this is not an edge case: left wrong, a
   $1,000,000 covenant floor is compared against a figure of 2,200 and the run publishes a
   breach on a compliant borrower.
   SANITY-CHECK IT with the analyst rather than only reading the caption: name one figure both ways —
   "revenue for the latest month reads 9,750 in the workbook, so $9,750,000 — is that right for this
   borrower?" A caption is often missing, and a scale nobody checked is the whole problem this field
   exists to remove.
   THEN, IF `scale` IS NOT 1, SET `aging.amount_scale` TOO. It is the same question asked of a different
   file: an A/R or A/P aging export comes off the ledger in whatever unit the ledger prints, usually raw
   dollars, while the model is in thousands. Open the export and compare its total against the balance
   sheet's receivables for that month — a factor of a thousand apart means `amount_scale: 1000`, the same
   figure means `1`. ap-ar-aging refuses to run without it once the model is not in dollars, because the
   file cannot say which it is.
6. Decide the LINE-READ MATERIALITY FLOOR (`analysis.line_read.materiality_floor`) — how big a move
   has to be before the memo mentions it. RECOMMENDED: leave null, so it scales with the borrower's own
   revenue; pin a figure only when the analyst wants a different bar for THIS credit.

   **Ask it in dollars, never by the name of the setting.** Work out what the derived floor comes to
   first (read it back from `floor_basis`, which states it in dollars), then put THAT to the analyst:
   "I'll flag anything that moves more than about [derived dollar floor] — that's sized off this
   borrower's revenue and moves with it. Want a different number?" Options are the derived figure (recommended), a
   round figure above it, and a round figure below it. A label like "engine default" describes our
   internals and leaves the analyst nothing to decide on; a dollar figure they can judge in
   a second. Same rule for every other setting on this page — say what it does to their memo, in their
   units, never what we call it.
7. Write borrower-config.json INTO the connected output folder. Verify every WRITABLE path resolves
   INSIDE that output folder and that NO writable path resolves inside the reporting/deal folder.
8. **CHECK the finished config before you finish.** Read it back and confirm every `*_path` and
   `*_folder` value starts with `source:` or `store:`. A value that starts with a drive letter,
   `/sessions/`, `/home/` or `/Users/` names one computer and works only where it was written. Rewrite
   each one through step A1.0.
9. **Register the borrower in the registry.** `python ${CLAUDE_PLUGIN_ROOT}/lib/fleet.py register --config "<this
   borrower-config.json>"` adds/updates this borrower in `credit-monitoring-registry.json` at the monitoring
   root — so `run-monitor` resolves it in ONE lookup next time.

## Part B — deal-context.json (interpretive)  ← the part that generalizes across deals
1. AUTO-DRAFT the factual `static` block from the borrower's OWN documents (borrower-agnostic sources):
   - **`facility` economics** ← executed loan docs (promissory note, loan/credit agreement, fee letter,
     disbursement letter): commitment, funded, draw type, rate (index + margin, cash/PIK), OID, fees,
     warrant/equity kicker, security, maturity. Cite the document each figure came from.
     **The FEE LETTER is MANDATORY reading when present** — fee letters routinely carry ADDITIVE
     economics on top of the agreement (extra PIK to the agent, extra success/exit fees) that are not
     conflicts but layers; sourcing fee economics from an IC/UW memo instead of the executed fee letter
     is a known failure mode (Example Borrower 2026-07: 1.00% agent PIK + 0.50% success fee both missed).
     **DISCREPANCY RULE — exhaust executed docs before flagging.** When any two sources state different
     terms or economics (memo vs agreement, cert vs model, any pair), text-sweep EVERY executed
     document in the deal folder for the conflicting term (pdftotext + grep takes minutes) BEFORE
     recording a discrepancy flag. A flag is for a genuinely unresolvable conflict, not an unread file —
     and the resolution is often a LAYERING structure in which both sources are right.
   - **`business_one_liner` / `vertical`** ← the IC / UW memo (`paths.ic_uw_memo_path`).
   - **`business_profile`** ← the same IC / UW memo, every field of the template's block filled: what the
     company actually does, revenue model and channel mix, end markets, customer and supplier
     concentration, geography, seasonality, competitive position, ownership, management, headcount,
     history, the operating KPIs that drive the P&L, working-capital profile. Cite the source for each
     claim and stamp its `source_tier` — and pick the tier honestly: concentration %, headcount and
     channel mix in a memo are usually `management-prepared` (the memo repeating borrower/CIM claims),
     not `internal-underwriting`. Where the memo is silent, leave that field a flagged draft rather than
     inventing it. This is the business context monthly-monitor reads to DIAGNOSE the financials — it is
     not restated in the memo each month.
     If `ic_uw_memo_path` is blank, check the `credit_agreement_path` folder root — executed/final docs
     are saved together — and confirm; still not found, leave the block a flagged draft rather than
     falling back to the borrower's CIM / marketing deck.
   - **`credit_thesis` pillars + `key_risks_at_uw`** ← the same IC / UW memo. Capture the thesis the
     deal was actually APPROVED on and the risks named there; map each thesis pillar to a monthly metric
     that tests it. No UW/IC memo found → leave the fields as flagged drafts for the analyst, never an
     invented thesis.
1b. PROPOSE the deal ARCHETYPE (`static.archetype`) — infer it from the IC/UW memo's business
   description (controlled vocabulary + framing blocks: monthly-monitor `references/archetypes.md`) and
   put it to the analyst as a confirm-or-override alongside the judgment fields below. Analyst skips →
   write the inferred value with `archetype_provenance` "inferred — unconfirmed"; nothing fits →
   "unclassified" (generic framing; never blocks onboarding). The archetype selects the memo's liquidity
   framing, KPI emphasis and collateral read; it never changes a computed figure.
1c. PROPOSE the DEAL TEAM (`static.deal_team`), then have the analyst CONFIRM it — these are the
   names the memo header shows, in display order. The deal team is the borrower's own people,
   and every person named in a deal folder is the borrower's: one setup read a borrower's CFO into
   the header, on the first line a partner reads. So score each candidate against the book, which
   already holds the answer — the other borrowers' deal records name who staffs credit deals:

       python ${CLAUDE_PLUGIN_ROOT}/references/deal_team.py propose \
           --exclude "<this borrower's folder>" --name "<candidate>" --name "<candidate>"

   Candidates are the names the IC/UW memo's deal-team page gives, plus any the analyst mentions.
   The engine returns `propose` (the same person is named on another credit deal — evidence attached),
   and `ask_about` for the rest: `officer` (carries an officer title — the borrower's own officer),
   `near` (one small edit from a name on the book, so likely that name misspelled), `unknown`
   (nobody on the book). It also returns `others_on_the_book`, the colleagues who appear on the
   most deals, so adding a missing name is one pick.
   PUT the proposal to the analyst as one `AskUserQuestion` confirm-or-edit, each name beside its
   evidence and every `ask_about` name unchecked. Write only what the analyst confirms, and stamp
   `static.deal_team_provenance` "analyst-confirmed YYYY-MM-DD".
   The analyst skips → leave the list empty and the stamp blank, and the memo simply omits its
   deal-team line — an empty list is a valid answer, never a blocker.
2. ASK THE ANALYST only the judgment fields (`our_view`), each as a menu: watchlist
   on/off, trajectory (improving / stable / deteriorating), one-line thesis status, and "what would
   change our mind" (the free-text box). ASK THE ONE-LINE THESIS STATUS LAST, after step 3, with the
   pillars and the statuses just given listed above it — that line summarises the pillars, so it
   cannot be written before they are graded. Surface the latest objective evidence (current covenant
   status, key variances, aging) to make the call fast — then leave the choice unrecommended. This is
   the credit judgment, the one place the monitor lays out the evidence and does not lead with an
   option (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`).
   Ask these HERE, in the session with the analyst; every one of them is theirs to give, and a
   blank `our_view` is what makes a first memo read like it has no house view. In a synthetic test,
   pinned test-persona judgments supplied by the test instructions stand in for analyst answers; they
   are not document facts, must not be inferred from the fixture, and must be recorded as supplied
   analyst judgments. Before finishing, read
   the file back and confirm each field carries the analyst's answer.
3. SET `credit_thesis[].status` (intact / pressured / broken) WITH the analyst, ONE PILLAR AT A TIME.
   FIRST READ THE DRAFTED PILLARS BACK — every one, in the memo's own words, each beside the monthly
   metric that tests it — and let the analyst correct or drop any before grading starts. A pillar the
   analyst has not read gets no status.
   THEN one question per pillar, and that pillar's own words go in the QUESTION TEXT with the period's
   evidence for it beneath them: "When we lent, the bet was ‹pillar, quoted›. ‹What the figures say
   about it.› Where does that stand now?" A five-word option label cannot carry a pillar, so intact /
   pressured / broken on their own ask the analyst to grade a bet they cannot see.
   Whether a pillar is pressured is credit judgment like step 2's: leave the three unrecommended.
   Flag each for sign-off.
4. SEED `carry_forward` from the most recent monitoring memo if one exists (open requests, unresolved
   flags, last headline); else leave empty. Thereafter monthly-monitor maintains it automatically.
4b. BUILD the **EBITDA add-back register** (`ebitda_addbacks`) — the analyst sets this ONCE, here.
   Covenant EBITDA turns on which costs get added back, and that is a judgment no engine can make from
   an account name. Sweep for candidates, then put them to the analyst as a menu, once:
   - **Where to sweep.** The limbs of the covenant-spec EBITDA definition; `accounting_basis`'s
     reference financials (what the parties themselves treated as addable, on the document's face);
     the IC/UW memo's adjusted-EBITDA analysis; the borrower's own compliance-certificate schedule if
     it shows one; the pinned plan; and the borrower's chart of accounts for the names that carry
     these costs — miscellaneous, restructuring, impairment, write-off, write-down, severance,
     transaction costs, legal settlement, one-time, non-recurring.
   - **What to show per candidate.** The line and its model row; the size this month and TTM; its
     month-by-month shape across the model's history, which is what separates a genuinely lumpy
     charge from a cost that recurs every month under a one-time name; the limb it would fall under,
     quoted; what the borrower did with it; and the covenant verdict computed both ways.
   - **What the analyst picks.** `treatment` (include / exclude / cap at $X / ask each month), one
     line of `reason`, the `source`, and `credit_view` — `discount` where a line is permitted for the
     covenant test but not believed for the credit read (a write-down that recurs annually is the
     standard case). Blank is not a default: an unanswered candidate is asked, not assumed.
   - **Two EBITDAs come out of it.** Covenant EBITDA — permitted by the agreement AND elected here —
     runs the test. Credit EBITDA additionally strips every `discount` line and is what leverage and
     trend commentary read. Report both, labelled.
   The register lives in deal-context, NOT in covenant-spec, on purpose: the spec records what the
   agreement permits and is re-derived on every amendment; this records what the analyst elected and
   survives one. On an amendment the elections carry forward and the analyst is told which ones the
   new agreement no longer permits.

5. Record `provenance` (auto-drafted vs analyst-signed, incl. `static.business_profile` as
   auto-draftable) and stamp `last_reviewed`.

## Definition of Done
- borrower-config.json in the connected OUTPUT folder; every writable path resolves inside it and none
  inside the reporting/deal folder; the four required inputs (reporting / credit agreement / IC-UW memo /
  budget) pinned, with the budget tab; the optional compliance-certificate folder choice recorded (blank
  means use reporting); budget-map.json written and verified against one known month;
  collateral-map.json written; `modules[]` set; **`model.scale` + `model.currency` read off the workbook
  and checked against one figure with the analyst**; line-read floor decided and stated to the analyst;
  every path written as `source:` or `store:`; the canonical model path is under the stable `_model/`
  workspace rather than `.record/current`; borrower registered.
- deal-context.json valid JSON: **`ebitda_addbacks` built with the analyst, every candidate given a treatment and a reason (blank is not a default)**; `facility` with per-figure sources; `business_profile` with per-field
  sources and source_tier stamps (silent fields flagged drafts); `credit_thesis` + `key_risks_at_uw`
  from the IC memo (or flagged absent), **every pillar read back to the analyst in the memo's own
  words before it was given a status**; `archetype` proposed with its provenance; `deal_team` scored
  against the book, confirmed by the analyst and stamped (empty is a valid answer); `our_view`
  captured from the analyst against the period's
  objective evidence; `provenance` and `last_reviewed` stamped.

## When you're done — suggest the next step
Running standalone, close with a menu: **[Set up covenants next]** (follow **covenant-setup** — the
model and collateral map depend on it) / **[Build the monitoring model]** / **[Run the monthly review]**
/ **[Done for now]**. When another setup wrapper invoked you, skip this — it owns the sequence.
