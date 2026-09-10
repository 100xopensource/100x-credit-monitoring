# Entering credit monitoring

This is the shared front door for setup, monthly monitoring, and resumption. Read it before emitting
user-visible text. The first visible text is the selected welcome below. Emit nothing before its first
sentence: no reasoning, plan, status line, tool narration, skill name, internal step, or journey-state
word. The classification is internal control data; never print or paraphrase the classification.

When a selected route specifies both a welcome and an initial menu, treat them as one atomic first
interaction: emit the complete welcome as assistant text, then immediately call `AskUserQuestion`
with the matching menu in the same assistant turn. The tool call does not replace the welcome. Never
send the menu alone or render its bracketed choices as assistant text.

## 1. Understand the request

Treat these as the same credit-monitoring front door, while preserving the user's intent:

| Intent | Examples |
|---|---|
| Start or setup | `get me started with credit monitoring`, `credit monitoring`, `start credit monitoring`, `/monitor-setup {name}` |
| Run | `run monitor for {name}`, `cred mon - {name}`, `{name} monthly monitor`, `monthly monitor {name}` |
| Resume | `where were we with {name}`, `where were we left at for {name}`, `resume {name} please`, `{name} continue` |
| Open prior result | `show me {name}'s last monitor`, `open the last {name} memo` |

A name in the request is the borrower name. Do not add a separate name-validation ritual. When no
name was supplied, offer one unfinished borrower first when the reachable records show exactly one;
otherwise ask which borrower through a menu. Never silently choose a borrower.

Use the generic onboarding menu only for an unqualified **Start or setup** request with no borrower
name and no reachable borrower records. A named borrower or explicit review intent preserves that
intent: continue the matching named or review route and never replace it with generic onboarding.

Use the resume variant only for **Resume** intent. Use the run variant for **Run** intent and for a
start request naming an existing borrower. **Open prior result** uses the ready-state results menu and
does not start a write. An explicit `/monitor-setup` keeps **Start or setup** intent even when the
borrower already exists.

## 2. Determine only what is reachable

Without writing, run `python ${CLAUDE_PLUGIN_ROOT}/lib/fleet.py context ["<borrower>"] --source-root
"<connected source root>" --store-root "<connected store root>" --fleet "<absolute store
root>/credit-monitoring-registry.json"` when both monitoring roots are already reachable. Use exactly
one journey state:

| State | Meaning |
|---|---|
| `unknown` | The monitoring store is not reachable in this Cowork conversation. Nothing is known yet. |
| `new` | The store is reachable and the borrower is not registered. |
| `setup_incomplete` | The borrower is registered but setup is unfinished. |
| `first_review_pending` | Setup is complete but no monthly review has been published. |
| `run_incomplete` | A monthly run has a resumable ledger and unfinished stages. |
| `ready` | At least one review is complete and no monthly run is unfinished. |

**Unknown is not clean.** Never call a borrower new, incomplete, or ready until the selected store is
reachable. Folder approval can be session scoped, so reconnecting folders does not repeat setup.

For a named borrower, also read `interrupted_run`, `unpublished_run`, `selected_run`,
`finalization_warnings`, and `lifecycle_warnings` as independent facts. They may coexist. An
unpublished result is finished work awaiting the analyst's selection; it is not “nothing unfinished.”
Open selected or unpublished results only from the exact workbook and memo paths returned for that
fact. If a warning says the roles are unavailable, explain that the older result cannot be opened
automatically; never guess a similarly named file.

Apply coexisting facts in this precedence: **finalization → interrupted → unpublished → selected**.
The first fact controls the write action offered, but it never hides a reachable read-only open action
for an unpublished or selected result. Always include **[Stop without making changes]**.

- For `finalization_warnings`, explain that finishing stopped after the result was saved. Offer
  **[Approve finalization]** before retrying the saved run's finalization. If that retry fails, offer
  **[Retry finalization]**. Both actions write and require that explicit approval. A stale-run,
  changed-file or recovery-journal refusal is not permission to force delivery. Preserve the files,
  explain the conflict and follow `references/store-contract.md`; do not remove the journal.
- For `interrupted_run`, offer **[Resume the interrupted review]**. Resuming writes and requires
  approval. When present, also offer **[Open the unpublished result]** and **[Open the selected
  result]**; these open only the exact returned role paths and are read only.
- When `unpublished_run` is present with a ready or selected result, offer **[Open the unpublished
  result]**, **[Select the unpublished result as current]**, and, when reachable, **[Open the selected
  result]**. Selecting writes and requires approval; either open action is read only.
- With only `selected_run`, use **[Open the selected result]** for the read-only open action. A new
  monthly review remains a separate approved write action.

An ambiguous borrower match gets a menu of at most three matching names plus **[None of these]**.
For an incomplete run, use the first pending step in the returned ordered list. If it starts with
`modules:`, translate it as `modules`; do not name the internal module or list every later step.
Translate `config` as “checking the saved setup,” `model` as “building the financial summary,”
`modules` as “reading through the numbers,” `coverage` or `memo` as “writing the memo,” and
`write_back` as “saving everything and finishing up.” Never show internal state names, paths,
commands, registry language, or step numbers.

## 3. Say one matching welcome

Fill in the borrower name where known. These are complete welcomes; do not add another summary.

### No borrower named, no reachable borrower records

> "Welcome to Credit Monitoring. I review a company's financial reporting and loan documents.
>
> Each review creates two files:
>
> - **Financial workbook** — organizes the financial history and calculations.
> - **Credit monitoring memo** — explains performance, liquidity, covenants, risks, and missing information.
>
> A review is complete when both files are attached here and I tell you where they were saved."

For an unqualified start request, immediately call `AskUserQuestion` with:

> "How would you like to begin?"
>
> **[Try sample data (recommended)]** / **[Use my own documents]** / **[Not now]**

- **Try sample data (recommended)** → explain that the
  [Synthetic borrower dataset](https://github.com/100xopensource/100x-credit-monitoring-synthetic-dataset)
  supports the full synthetic borrower set or minified versions. Tell the analyst to use the
  repository documentation to choose, download, and extract one of those forms. Do not guess a file
  or release asset, auto-download anything, or promise speed or completeness. If the repository
  documentation is unavailable, say exactly:

  > "The sample download isn't available at that page yet. You can use your own documents, use a sample you already downloaded, or pause for now."
  >
  > **[Use my own documents]** / **[Use an already-downloaded sample]** / **[Not now]**

  Ask that as an `AskUserQuestion` menu. Either document choice continues to **store-setup**; do not
  recheck the remote page or block an already-downloaded sample. After the analyst confirms newly
  downloaded data is extracted, follow the same path without repeating the welcome. Then use the
  normal post-access company selection and monitor one borrower only.
- **Use my own documents** → preserve this as route intent and follow **store-setup** without asking
  for a company name first. It is not permission to open a native picker immediately: store-setup
  must explain both folders, attach the guide, ask its readiness question, and wait for the answer.
  After access, use the normal company selection. Do not ask the generic continue menu.
- **Not now** → stop without opening a picker or changing anything.

For explicit review intent with no company name, preserve that intent and use the generic
continue-or-stop menu in Section 4; never show the generic onboarding choices. After access, use the
normal company selection without repeating the welcome.

### Borrower named, monitoring store not reachable

> "Welcome to Credit Monitoring. I will review {name}'s financial reporting and loan documents.
>
> You will receive two files:
>
> - **Financial workbook** — organizes the financial history and calculations.
> - **Credit monitoring memo** — explains performance, liquidity, covenants, risks, and missing information.
>
> The review is complete when both files are attached here and I tell you where they were saved."

Use the requested borrower in place of {name}. This wording is neutral because disconnected records
do not prove whether the borrower is new or returning.

### `new`

> "Welcome to Credit Monitoring. I will review {name}'s financial reporting and loan documents.
>
> You will receive two files:
>
> - **Financial workbook** — organizes the financial history and calculations.
> - **Credit monitoring memo** — explains performance, liquidity, covenants, risks, and missing information.
>
> The review is complete when both files are attached here and I tell you where they were saved."

### `setup_incomplete`

For resume language:

> "Welcome back to Credit Monitoring. I found unfinished setup work for {name}. The work already completed
> is saved, and I can pick up where it stopped. When setup and this month's review are finished, you
> will receive a financial workbook and a monitoring memo."

For run language:

> "Welcome back to Credit Monitoring. The first setup for {name} is not finished yet, so I will need to
> complete it before running this month's review. Everything already completed is saved, and we can
> continue from where it stopped."

### `first_review_pending`

> "Welcome back to Credit Monitoring. Setup for {name} is complete, but the first monthly review has
> not finished. I can continue with the review and produce the financial workbook and monitoring memo."

When `unpublished_run` is present instead, say the review finished but has not been selected, then
offer **[Open the unpublished result]** / **[Select the unpublished result as current]** /
**[Stop without making changes]**. Do not use the “has not finished” wording.

### Finalization waiting

When `finalization_warnings` is non-empty, use this instead of the journey-state welcome:

> "Welcome back to Credit Monitoring. The latest result for {name} was saved, but preparing its return
> copy did not finish. I can check whether that exact saved result can be delivered without replacing newer work."
>
> **[Approve finalization]** / **[Open the unpublished result]** when reachable /
> **[Open the selected result]** when reachable / **[Stop without making changes]**

### `run_incomplete`

> "Welcome back to Credit Monitoring. I found an unfinished monthly review for {name}. The work already
> completed is saved, and I can continue with {plain-language next stage}."
>
> **[Resume the interrupted review]** / **[Open the unpublished result]** when reachable /
> **[Open the selected result]** when reachable / **[Stop without making changes]**

### `ready`

For explicit setup language:

> "Welcome back to Credit Monitoring. {name} is already set up. I can review and update its saved
> monitoring setup without starting a monthly review."

When `unpublished_run` is present:

> "Welcome back to Credit Monitoring. A newer result for {name} is finished but has not been selected.
> The previously selected result remains current. Would you like to review either result or select the
> newer one?"
>
> **[Open the unpublished result]** / **[Select the unpublished result as current]** /
> **[Open the selected result]** / **[Stop without making changes]**

For resume language:

> "Welcome back to Credit Monitoring. Setup and the most recent review for {name} are complete, so there
> is nothing unfinished to resume. Would you like to open the last results or start and save a new
> monthly review using the saved monitoring folders for {name}?"
>
> **[Open the selected result]** / **[Start and save a new monthly review]** /
> **[Stop without making changes]**

Use this “nothing unfinished” wording only when `unpublished_run`, `interrupted_run`, and
`finalization_warnings` are all empty.

For run language:

> "Welcome back to Credit Monitoring. {name} is already set up. I can update its financial workbook,
> review the latest reporting, check the loan promises, and prepare this month's monitoring memo."

When no borrower was named but reachable records show exactly one unfinished borrower, say:

> "Welcome back to Credit Monitoring. I found unfinished work for {name}. Would you like to continue
> with {name} or choose another borrower?"
>
> **[Continue {name}]** / **[Choose another borrower]** / **[Stop without making changes]**

Otherwise ask which borrower and offer reachable names. A completed borrower may be offered, but never
silently treated as the user's choice.

## 4. Ask before any write

Call `AskUserQuestion` for every menu in this reference. Never render bracketed choices as plain
assistant text; the user must receive clickable options.

The no-name, no-records onboarding menu above is the only exception for an unqualified start request.
Choosing **[Try sample data (recommended)]** or **[Use my own documents]** selects the route and
replaces this generic menu. It does not satisfy either folder-readiness gate in store-setup.

For every other route where the state is literally `unknown` or `new`, use this menu:

> "Before I open any folders, would you like me to continue and save this review in a folder you
> choose, or stop without changing anything?"
>
> **[Continue and choose folders]** / **[Stop without making changes]**

When Section 2 or 3 provides a fact-specific action menu, use that menu and do not follow it with the
generic continue menu. Otherwise, only for `setup_incomplete`, `first_review_pending`,
`run_incomplete`, or `ready`, ask:

> "Would you like me to continue with the saved monitoring folders for {name}, or stop without changing
> anything?"
>
> **[Continue with {name}]** / **[Stop without making changes]**

Choosing **[Start and save a new monthly review]** from the ready-state results menu also satisfies
the write permission; do not ask the returning-borrower permission menu again. Opening the last
results is read only and needs no write permission.

Only after a continue or start-and-save choice may the workflow request or reapprove folders, prepare
a store, open or resume a run, or write a file. A stop choice opens no picker and changes nothing.

## 5. Preserve direct commands

A user-invoked `/monitor-setup {name}` keeps setup intent but uses this entry contract first. A direct
monthly-monitor request with no resolved config returns through run-monitor. When run-monitor already
handed a skill resolved context, that skill continues without repeating the welcome or permission menu.
