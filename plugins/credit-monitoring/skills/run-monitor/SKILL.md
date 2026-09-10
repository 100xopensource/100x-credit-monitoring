---
name: run-monitor
description: >
  The front door for portfolio monitoring. Resolves the borrower and journey state in one read-only
  lookup, then starts, resumes, or runs the right stage. Triggers: "run the monitor on {borrower}",
  "run credit monitoring for {borrower}", "run monitor for {borrower}", "cred mon - {borrower}",
  "{borrower} monthly monitor", "monthly monitor {borrower}", "credit monitoring", "start credit
  monitoring", "get me started with credit monitoring", "where were we with {borrower}", "where were
  we left at for {borrower}", "resume {borrower}", "{borrower} continue", "open the last {borrower}
  memo", "show me {borrower}'s last monitor", "is {borrower} set up for monitoring", "create my
  portfolio dashboard", "update my portfolio dashboard", "open my portfolio dashboard", "is my
  portfolio dashboard current".
metadata:
  version: "0.8.2"
---

# Run Monitor

Explain the result once, ask before writing, resolve the borrower once, then route.

## Portfolio dashboard requests

A direct request to open, share, or update the portfolio dashboard follows `portfolio-artifact`
immediately without requiring monitoring folders. The coordinator explains that same-link Update is
deferred; it never routes an Update request into borrower setup.

A direct Create or current/stale check needs local monitoring files. Check both roots through
`connecting-folders.md`; if either is unavailable, follow **store-setup** to obtain both connected roots.
Then bind and verify them exactly as Step 1 does before handing the source root, store root, and registry
to `portfolio-artifact`. Do not ask for a borrower, repeat the monitoring welcome, infer a root from chat
history, or pass an unbound store.

## Step 1 — Classify silently, without changing anything

Before emitting user-visible text, read `${CLAUDE_PLUGIN_ROOT}/references/entry-routing.md` and follow
its intent and journey-state decision tree. Find how this session reaches folders through
`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`. Treat the folder containing the borrower's
documents as the read-only source and the output parent as the destination; the source may have any
name.

Do not open a folder picker, prepare a store, create a folder, or write a file in this step. If both
connected roots and the registry are reachable, bind them explicitly for this invocation and classify
once:

```bash
CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
python ${CLAUDE_PLUGIN_ROOT}/lib/fleet.py context "<borrower>" \
  --fleet "<store root>/credit-monitoring-registry.json" \
  --source-root "<source root>" --store-root "<store root>"
```

Keep both roots in the resolved handoff. Prefix every later plugin subprocess with the same two
environment bindings and include them in every delegated worker brief; bindings made inside one
subprocess do not survive into the next one.

Use the returned primary state plus `interrupted_run`, `unpublished_run`, `selected_run`, lifecycle
warnings, finalization warnings, and `model_migration_required`. If the roots are not reachable, skip
classification and go straight to the neutral welcome and continue/stop menu in Step 2. Never search
the filesystem for `borrower-config.json` or infer lifecycle state from chat history.

## Step 2 — Say the state-aware welcome

Use exactly one matching welcome from `entry-routing.md`. Your first response must begin with the
first character of the quoted welcome and contain no preface. Do not announce which welcome you
selected or why. Do not print the classification or put a plan, status line, tool narration, skill
explanation, or second summary before or after it. Follow `entry-routing.md`'s atomic first-interaction
rule whenever the selected route includes a welcome and an initial menu. If no borrower was named,
follow the no-name branch and ask through one menu; never silently choose one. Use its onboarding
menu only for an unqualified start request with no reachable borrower records. Preserve a named
borrower or explicit review intent and continue the matching route instead.

## Step 3 — Ask before any write

For **Open the selected result** or **Open the unpublished result**, skip write permission and take
the matching read-only Step 4 branch. **Stop without making changes** stops. **Start and save a new
monthly review** already satisfies general write permission; monthly-monitor still asks separately if
its concrete overwrite check finds changed, missing, unreadable, or unpublished work. **Approve
finalization**, **Retry finalization**, **Resume the interrupted review**, and **Select the unpublished
result as current** each authorize only their named write.

For the no-name onboarding menu, **Try sample data (recommended)** follows the exact repository route
in `entry-routing.md`; after the analyst confirms extraction, follow **store-setup** without repeating
the welcome or generic continue menu. **Use my own documents** goes directly to **store-setup** without
asking for a company name first or repeating the generic continue menu. That choice is route intent,
not permission to open a picker: store-setup must show its guide and explanation, ask the readiness
question, and wait. Both paths use monitor-setup's post-access company selection and scope one
borrower. **Not now** has the same no-picker, no-change behavior as **Stop without making changes**.

For every other route, call `AskUserQuestion` to ask the matching permission menu from
`entry-routing.md` word for word. Never substitute bracketed choices in plain assistant text.

- **Stop without making changes** → stop without opening a picker, preparing a store, or changing any file.
- The matching **Continue** choice → continue.

When the source or monitoring root is not reachable, follow **store-setup** to obtain both folders.
Store-setup owns the guide attachment, capability copy, and both picker requests; do not deliver any of
those separately. It also owns the factual post-access acknowledgement of both selected folder roles.
After the store is reachable and that acknowledgement is visible, run the same explicitly bound
`fleet.py context` lookup described in Step 1 and use its state without repeating the welcome or
permission menu.

## Step 4 — Route

Apply facts in `entry-routing.md` precedence and perform only the chosen action:

- **Approve finalization / Retry finalization** → find the completed fact whose period/run id matches
  the warning and require its validated workbook and memo roles. Retry exactly:

  ```bash
  CREDIT_MONITOR_SOURCE_ROOT="<source root>" CREDIT_MONITOR_STORE_ROOT="<store root>" \
  python ${CLAUDE_PLUGIN_ROOT}/lib/manifest.py finish \
    --run "<matched run folder>" --deal "<borrower root>" --period "<matched period>" \
    --run-id "<matched run id>" --borrower-key "<borrower key>" \
    --workbook "<roles.financial_workbook>" --memo "<roles.memo>"
  ```

  Reclassify once. Missing roles mean stop plainly; never guess or open a replacement run.
- **Resume the interrupted review** → follow **monthly-monitor** with the resolved config path plus
  `interrupted_run.period` and `interrupted_run.run_id`; it must resume that ledger.
- **Open the unpublished result** → attach the exact verified `unpublished_run.workbook_path` and
  `memo_path`, label them unpublished, and stop without writing.
- **Select the unpublished result as current** → run `run_record.py select` for the exact
  `unpublished_run.folder`, borrower root, and period, then reclassify once. This changes only selection
  metadata.
- **Open the selected result** → attach the exact verified `selected_run.workbook_path` and `memo_path`
  and stop without writing. Missing paths mean automatic opening is unavailable; never guess names.
- **Ready / first review pending** → for explicit setup intent, follow **monitor-setup** with the
  resolved context and do not start **monthly-monitor**. Otherwise, if `model_migration_required` is
  true, route through the approved **monitor-setup** verify-copy-verify path before building or opening
  a new run. Otherwise follow **monthly-monitor** with the resolved config and all lifecycle facts.
- **Registered but incomplete** (`setup_incomplete`) → say **Before the first time setup** from
  `asking.md`, then resume **monitor-setup**, **covenant-setup**, and **build-monitoring-model** before
  monthly monitoring.
- **Ambiguous** → a menu of the matching names (at most three) plus **[None of these]**.
- **New** (`new`) → say **Before the first time setup** from `asking.md`, then follow **monitor-setup**,
  **covenant-setup**, and **build-monitoring-model**. Register the validated borrower config with
  `fleet.py register`, then continue to **monthly-monitor**.
- **Store not reachable** → reconnect the two folders as Step 3 describes, classify once, and take the
  resulting route without repeating the welcome or permission menu.

## Rules

- Never search the filesystem for `borrower-config.json`.
- Never pretend a missing borrower is already monitoring.
- Never require the source folder to have a particular name.
- Never mutate the filesystem before the user chooses to continue.
- Keep the analyst-facing wording plain and short.

## Done

The borrower was resolved or not resolved in one registry lookup, the run was routed, and the user
knows to expect one financial workbook and one monitoring memo.
