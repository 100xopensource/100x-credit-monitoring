# Orchestration — how monthly-monitor runs subagents

## Why subagents
Each module reads heavy inputs (hundreds of statement rows, aging files). Run each in its OWN context
via the Task tool, so the orchestrator holds only the short JSON conclusions and stays lean enough to
synthesize.

## The worker is a shipped agent
The plugin ships the module worker as `${CLAUDE_PLUGIN_ROOT}/agents/module-analyst.md`, so its
contract — path forms, engine discipline, the validator gate, the read-back, the return shape — is a
versioned file rather than prose retyped into every dispatch. Dispatch it by subagent type
`credit-monitoring:module-analyst`. Where the runtime does not offer plugin agents, dispatch a
general-purpose subagent, prepend the text of that file to the brief, and set that dispatch to the
model and effort the file's frontmatter declares (sonnet, xhigh) — prepended text carries the contract,
not the frontmatter, so a fallback dispatch left unset runs the worker on the orchestrator's own model.

## Fan-out pattern
- Modules are INDEPENDENT → launch them in a SINGLE message (multiple Task calls) so they run concurrently.
- **Collect every worker's result in the turn that dispatched it.** Dispatch with
  `run_in_background: false` — several calls in one message still run side by side, so this costs no
  time — and let the turn end only once each one has returned. A background dispatch waits on a
  completion notification, and a run nobody is watching has nothing to deliver one: on three real
  borrowers the run ended reporting success before the workbook existed, and one worker's output was
  lost with the session. Ending a turn with a dispatched worker outstanding is what breaks a scheduled
  run. If a worker does come back empty, do that stage's work directly or record it as a skip with the
  reason — never carry on as though it had reported.
- **The enabled-module roster is the cap.** One dispatch per module in `borrower-config.modules[]` whose
  inputs exist, and nothing beyond it: no second worker to re-read a module's inputs, no verifier
  subagent over a module's output (the `validate_output.py` gate is the check), no subagent for work the
  orchestrator does in a few tool calls.
- Each brief carries: (a) the module name and its module spec file, (b) the returned
  `execution_config_path`, (c) the bound source/store roots, (d) the input paths it needs, (e) the
  run's `current_period`, (f) the run folder's `_archive` path to write to — in all three forms like
  every other path, since the worker writes the output with Write, gates it through bash, and reads it
  back — and (g) the run id (below). Never brief the canonical config after snapshot.
- **Open the run once before the fan-out and put its returned run id in every brief.** Use
  `run_record.py open`; it mints `run-<YYYYMMDD>-<HHMMSS>` or resumes an unfinished period. Tell each worker to pass it
  to its engine as `--run-id <id>`. This is what ties one run's five module outputs together: an engine
  left to mint its own stamps `run_id_from: "engine"`, and a month's modules then read as separate runs
  that nothing can order or group.
- Pass paths, never raw statement data — the subagent reads the file itself.
- **Give every path in all three forms** — the connected-folder path, the `/sessions/.../mnt/...`
  sandbox path, and the execution-config string (`source:...` or run-local `store:...`) for engine
  arguments. Which tool takes which is the agent contract's "Three path forms". The worker prefixes
  every engine subprocess with the two bound roots; one subprocess cannot inherit them from another.

## Shared definitions
EBITDA / Total Debt / Liquidity come from `covenant-spec.json`. The build already computed the EBITDA
bridge into the workbook. Modules READ those; they must not recompute a different EBITDA.

Scratch files (a certified-values draft, an extracted-text sidecar, a probe script) go in a
subagent-writable scratch dir under the sandbox, and are read back with bash — never written into the
analyst's output folder, which is for delivered artifacts only.

## Sequencing & dependencies
- covenant-compliance: needs covenant-spec.json (else SKIPPED + flagged "no covenant spec — run
  covenant-setup"). Its worker transcribes the period's compliance certificate into certified-values
  JSON BEFORE running the engine (see its module spec).
- budget-vs-actual: needs pinned budget + budget-map.json (written by monitor-setup).
- ap-ar-aging: standalone — its worker identifies the period's A/R and A/P aging exports and passes
  them by path.
- revenue-quality: its worker passes the period's retention workbook, deferred-revenue schedule and
  compliance certificate by path. Each is independent — a file that has not arrived leaves only its own
  section unavailable.
- three-statement-analysis: needs only the monitoring model (always-on once the model exists).
- All can run in parallel; synthesis waits for all.

## Model routing — declared in the agent files
Each shipped agent carries its own `model` and `effort` in frontmatter, so routing is set once per
worker instead of per dispatch:

| Agent | Model | Effort | Why |
|---|---|---|---|
| `module-analyst` | sonnet | xhigh | The engine is deterministic; getting to it is not — finding the period's files in a folder shaped differently every deal, reading a title row to tell A/R from A/P, fixing an engine mid-run. |
| `model-builder` | sonnet | xhigh | Long, fiddly, and correctness-critical: merge-by-period, the restatement scan, recalc order, then a gate that either passes or costs a rebuild. |
| `covenant-extractor` | sonnet | xhigh | Hundreds of pages of definitions, a `basis` call that silently false-passes a covenant when wrong, and a discrepancy rule that means sweeping the whole executed set. |

Every worker runs at `xhigh`: each of these is a hard problem wearing a contract, and lower effort buys
fewer tool calls exactly where the work is finding the right file and noticing the thing that doesn't
reconcile. Sonnet is the model floor — live-file parsing and mid-run engine fixes need it. The memo's
synthesis is what stays on the orchestrator's strong model.

**Synthesis stays on the orchestrator's own model.** Choosing "the issue," rating the RAG grid
forward-looking, deal-context prioritization and memo prose are judgment work and never leave the
orchestrator's context. Run monthly-monitor in a session on the strongest model available (Claude Opus
5 or Fable 5 class), not a worker tier. Leave the agents' frontmatter alone rather than overriding
`model` per dispatch — one declared route is what keeps two runs comparable.

## Module output contract — placement, naming, validation (one convention, no variants)
- **Placement + name:** every module output is written to
  `<run_folder>/_archive/<module>-<YYYY-MM>.json` — `<module>` exactly as the output's
  `module` field, `<YYYY-MM>` the run's current_period.
- **Run stamp:** every output carries `run_id`, `produced_at` and `produced_by`, added by the lib's
  `conclusion()`. All modules of one run share the `run_id` the orchestrator minted.
- **Followable source refs:** each ref carries `store` (a shared-folder id like `store` or
  `source`) and `path_in_store` alongside the `file` index, so a figure lifted out of the output
  still says where it came from — the index alone means nothing away from `source_files`, and a
  recorded absolute path is one analyst's own location. The envelope names each store's `url_base`
  once under `stores`, and `source_ref_reach` counts how many refs reach a cell, stop at a file, or
  land in no configured store. Engines get this from `conclusion(..., source_index=idx)`.
- **A ref reaches the BORROWER's document, not just ours.** Most figures read from the workbook we
  assemble or our record of the covenant terms, and neither is a document anyone outside the run
  opens. So a ref carries `upstream` — the borrower's reporting pack, signed agreement, budget or
  compliance certificate, with the tab or section inside it. The engine resolves that hop when it
  produces the figure, which is why the memo reads it rather than working it out again;
  `source_ref_reach.borrower_doc` counts how many made it.
- **A completed run is never a worker's output directory.** Each new review writes into its own
  run folder. A retry may replace a module result only while that run is unfinished; `write_output`
  retires the matching result/skip counterpart. It does not create a separate `_history` copy.
  Once the run has a manifest, do not change its results, inputs or ledger. Revisions use a new run.
  Wait for every worker to finish before freezing. A stale-run or recovery refusal is not a module
  skip: stop delivery, preserve the run and explain the conflict. Finish creates a visible draft;
  only the user's later confirmation changes the accepted selection.
- **One period stamp per run:** every module output of one run carries `period` = the SAME ISO
  `YYYY-MM` (the lib's `conclusion()` normalizes it — never "May 2026"-style stamps).
- **Paths name a shared folder:** during a monthly run, pass the run's returned
  `execution_config_path` to every engine. Its writable control/model paths are logical `store:` paths
  into that run snapshot; source inputs remain logical `source:` paths. Never substitute the canonical
  `_model/` or `.record/current` config after the snapshot, and never pass a pre-resolved
  `/sessions/*/mnt/*` mount — engines resolve logical paths against the roots bound for this invocation.
  `source_files` records the same portable form, and `validate_output.py` rejects machine-local paths.
- **Skip record:** a module that is listed but input-gated off writes
  `_archive/<module>-<YYYY-MM>.skipped.json` containing
  `{"module": ..., "period": "YYYY-MM", "skipped": true, "reason": "<what input was missing, or what
  failed>", "results": [], "exceptions": [], "source_files": []}` — one spelling, machine-checkable,
  and the ONE shape a module hands back when it has no figures to report, whatever the cause. An ENGINE
  that skips itself mid-run (bad/unparseable input it discovered) writes the same shape to its `--out`
  path, with `period` null only when the period was genuinely underivable; the orchestrator then
  archives it under the `.skipped.json` name (`<YYYY-MM>` = the run's current_period), never under the
  normal output name.
- **Every module skipping with the same reason means one missing field, not five broken modules.** A
  borrower that has not declared what unit its model reports in — `model.scale` + `model.currency` in
  borrower-config — makes every module skip with that reason, because comparing a dollar threshold
  against an undeclared figure is how a compliant deal reports a breach. Read the caption at the top of
  the borrower's statement columns, set the two fields (monitor-setup step 5a says how, including the
  one figure to check with the analyst), and re-run. Do not work around it and do not run a module
  without it.
- **Rows beside each output.** An engine writes `<output>.rows.json` next to its output — the same
  figures in the four row shapes a portfolio question reads. Archive it alongside the output if it is
  there; nothing breaks when it is not (the translator derives them), and it is never the module's
  conclusion.
- **Validate on write:** run `references/validate_output.py --period <current_period> <output.json>` on
  EVERY module output BEFORE archiving; it fails loudly and names the offending figure. A validation
  failure is an ENGINE problem — fix the engine and rerun the module; never hand-edit the output JSON to
  pass. If the engine cannot be fixed within the run, the module writes the skip record above with
  `reason` naming the failure and quoting what the gate said ("engine failure — validate_output: …"),
  archives it under the `.skipped.json` name, and returns that record as its conclusion — the invalid
  output stays unarchived. Failure isolation then applies: the memo states the module unavailable with
  that reason, and the rest of the run ships.
- **Writing meant for a person is written as a note, and what a module flagged reaches the memo.**
  Every output gets a markdown note beside it holding its prose — commentary, what was read off a
  document, anything flagged — written by `write_output`, so nothing readable stays buried in the
  JSON. An output's `action_items` are things a person must DO: carry each one into the memo. The
  gate prints them, and a note is not where a request to the credit team is delivered. Its
  `exceptions` are what the engine warned about and the reading a worker took where its input was
  ambiguous: read them before synthesizing, and land each one — stated in the memo, put to the
  analyst as a menu (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`) when the call is theirs, or left
  out because the memo already carries it. A memo sentence stands only where the `exceptions` behind
  it agree with it.
- **Record what the month covered, after the last module lands.** Run
  `references/coverage_record.py --archive <_archive> --period <current_period> --borrower-config
  <execution_config_path> --run-id <run id> --out <run_folder>/coverage.json`. It reads the archive and states,
  per roster module, which of five things happened — `reported`, `empty` (archived carrying no
  figures), `skipped` (quoting the record's own reason), `missing` (enabled, nothing archived) or
  `unlisted` (archived, not in the roster) — plus `readable`, whether every enabled module produced.
  This is what tells a clean month apart from a borrower whose files moved: an empty result and an
  unreadable one look identical without it. Gate and read it back like any other output, and carry
  `readable: false` into the memo's data-quality section with the states that caused it.
- **Read back after archiving:** a reported-successful write is not a durable write (a control file has
  shipped truncated mid-word before). After the archive write, re-open the file AT ITS FINAL
  `_archive/...` PATH and `json.load` it; only then is the module output done — never stamp OK from an
  in-session copy. The same read-back applies to every published file, the model and the memo included.

## Failure isolation
If one module errors, still ship the rest. Never let one module's failure abort the run.

Say what was lost in the memo, and to the analyst, in their words — never as "module X unavailable".
Name the section of their memo that is affected, say why, and say what would fill it in next time:
"I could not check the promises in the loan agreement this month, because the compliance certificate
for June is not in the folder yet. Everything else in this memo is complete. Once that certificate
arrives I can add this section in." The wording rules are in
`${CLAUDE_PLUGIN_ROOT}/references/asking.md`.
