---
name: monthly-monitor
description: >
  Run the monthly portfolio monitor for a borrower. Loads borrower-config and deal-context,
  asks the analyst what to watch, ensures the financials model is current, establishes continuity
  with the prior period, fans out the enabled analysis modules in parallel, synthesizes the
  monitoring memo, and writes carry-forward state back. Triggers: "run the monthly monitor",
  "build the monitoring memo for {borrower}", "monthly portfolio review".
metadata:
  version: "0.25.0"
---

# Monthly Monitor

Orchestrate the monthly review. State lives in files: read durable files, coordinate, write durable
files back. See `references/orchestration.md` and `references/continuity.py`.

When directly invoked without a resolved config from `run-monitor`, return through **run-monitor** and
`${CLAUDE_PLUGIN_ROOT}/references/entry-routing.md` first. When `run-monitor` handed in the resolved
config and completed the welcome and permission menu, continue here without repeating either one.

Every interaction follows the same loop: research first, propose options with a recommendation, act
on the option the analyst picks through `AskUserQuestion`
(`${CLAUDE_PLUGIN_ROOT}/references/asking.md`). Which borrower, the deal context, what to watch,
what's next: all menus grounded in what you already read, never a typed answer or a form.

## Step 0 — Load the resolved control files

Proceed only when `run-monitor` handed you the resolved `borrower-config.json` path after completing
entry routing and write permission. A borrower name by itself is not resolved context: return through
**run-monitor** rather than calling the legacy registry resolver here.

Read `borrower-config.json` and resolve `paths.deal_context_path` → load `deal-context.json`.
Missing deal-context → offer to fill it in now or continue without a deal context.

Keep the source and store roots handed off by run-monitor for the entire run. Prefix every plugin
subprocess below with `CREDIT_MONITOR_SOURCE_ROOT="<source root>"` and
`CREDIT_MONITOR_STORE_ROOT="<store root>"`, and include both bindings in every worker brief. The code
blocks omit that repeated prefix for readability; it is mandatory for each fresh process.

## Step 1 — Protect current work and establish accepted history

Before creating or changing a run, execute:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py check \
  --folder "<paths.monitor_output_folder>" --json
```

If it reports changed, missing, unreadable, repackaged, or unpublished work, state each item plainly and ask whether
to continue. Stop on no. Repackaged means the workbook container was re-wrapped (Excel/SharePoint) with the same cells — it does not block delivery. This concrete safety decision is required even when run-monitor already
received general permission to start. If there is nothing to protect, do not add another question.
Permission to continue does not bypass an edit, stale-run or recovery error. Preserve the files and
resolve that error first. Close editors during delivery, acceptance or discard; the local process lock
cannot coordinate writes from Excel or another computer.

A `changed` entry is not automatically an accident: it may be an intentional hand edit to the financial
workbook the analyst wants this borrower's next run to build on (the memo is rendered fresh every run and
is never carried forward this way — if the memo shows changed, that edit can only be preserved by reverting
it before continuing, never by edit-intake). Ask which. Read the exact `path` and `digest` `check --json`
reported under `comparisons` for that file — never retype or re-derive them — and review it with the
analyst before accepting:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py edit-intake \
  --deal "<borrower root>" --edit "<comparisons[].path>" "<comparisons[].digest>" \
  --reason "<why the analyst is accepting this edit>" --json
```

This only records a review decision — it does not by itself unblock anything. The digest is rechecked
against the file's current bytes: if it moved again since `check` ran, this refuses rather than accept
an edit nobody actually reviewed. `still_blocking` always still lists every real change, accepted or not
— accept, discard, and an ordinary finish keep refusing until the exact run below actually consumes it.

To make a new run consume an accepted edit, pin it at `open` (below) with `--consume-edit "<same path>"`;
`open` fails if that path is not still accepted and still live. That run's `snapshot` step must then be given
`--model` pointing at the accepted file itself (the `open` response's `consume_edit.source_path`), not the
canonical model — `snapshot` verifies the given file's digest against the pin and refuses a mismatch. No
new recomputation code exists for this: the run reruns against that file exactly like any other model
input, and only THAT run's finish is exempted from the block, bound to its own pinned digest — a different
or unrelated run, and accept/discard, still refuse over the same unconsumed edit. The finished draft's
manifest records which file was consumed and why.

For a `run_incomplete` handoff, call `open` now with the exact context-selected period. It is a
read-only resume, and returns the ledger's recorded base, analyst focus, run-local model path, and
pending steps. Confirm its `run_id` matches `interrupted_run.run_id`; stop on any mismatch or lifecycle
warning. Otherwise validate the review period from current reporting, then resolve its historical base
before any run write:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py base \
  --folder "<paths.monitor_output_folder>" --before-period "<YYYY-MM>" --json
```

A same-period revision therefore reads continuity from the prior selected month. Treat the returned
folder, manifest roles, workbook path, and memo path as authoritative; `seed` means no selected base.
Read the returned base folder's manifest and call
`continuity.py load_prior_state(base_folder, manifest,
current_context_path="<resolved paths.deal_context_path>")`. Never scan for a memo filename. On
resume, use the base folder returned by `open`. Keep the starting-base fact for the closing reminder:
`from` on a new base lookup and `base.from` on a resumed `open`; `seed` means no prior selected review
existed when this run opened, while `run` means one did.

## Step 2 — Settle what to watch this run

Use the verified prior state plus current deal context to propose candidates rather than asking a blank
question. Remind the analyst of prior focus, headline, open requests, and unresolved flags; offer the
strongest candidates first plus **[Nothing specific — use the deal context]**; confirm forward-stress
scenarios in the same menu. For a resume, preserve the ledger's existing analyst focus.

After the analyst answers, say **Before the long quiet** from `asking.md`. Only then begin the quiet
work; never tell the analyst they can walk away while a question is waiting.

## Step 3 — Open the exact run and snapshot its inputs

For a new run, pass the base result and each focus item into `open`. If the historical lookup returned
`seed`, pass `--seed-base` explicitly. With no base flags, `open` uses the latest accepted run. For an
explicit draft continuation, use `--draft-base` with its exact `--base-run` and `--base-period`:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py open \
  --folder "<paths.monitor_output_folder>" --period "<YYYY-MM>" \
  [--base-run "<base run id>" --base-period "<base period>"] \
  [--consume-edit "<accepted edit-intake path, from Step 1>"] \
  --modules "<comma-separated modules>" [--focus "<item>" ...] --json
```

If Step 1 accepted a workbook edit and this is a new run meant to build on it, pass `--consume-edit` with
that exact path; a resumed run cannot take one. The response's `consume_edit.source_path`, when present, is
what `snapshot` below must be given as `--model` instead of the canonical model.

For `run_incomplete`, continue from the already verified `open` result from Step 1; never silently open a
replacement run. If delivery later reports that this run is stale, preserve it and explain that a fresh
run is needed against the changed state. Do not repin its ledger or overwrite the newer result.

If `model_migration_required` is true and the run has no completed model snapshot, stop here and follow
**monitor-setup**'s approved verify-copy-verify migration, then reload the config. If pending steps
include `model`, refresh the canonical workbook through **build-monitoring-model** at the configured
`_model/model_output_path` — unless `open` returned `consume_edit`, in which case that pinned
`source_path` is this run's model input instead. Then snapshot the complete config, workbook, and required
sidecars:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py snapshot \
  --run "<run_folder>" --config "<resolved borrower-config.json>" \
  --model "<consume_edit.source_path, or resolved paths.model_output_path>"
```

`snapshot` verifies a pinned run's `--model` against its `consume_edit` digest and refuses a mismatch —
never substitute a different file when a run is pinned. A pinned run's stable-model sidecars come from the
SAME verified prior run `open` resolved as this run's base, never from the borrower root or beside the
edited file itself — neither holds them. This means a pinned run needs a real prior run as its base; `open
--consume-edit` against a `seed` base (no accepted run yet) fails for exactly that reason.

The snapshot command is safe to retry. After `model` is done, require its returned `model_path`,
`execution_config_path`, `config_folder`, and `deal_context_path`; a resumed run missing these saved
inputs stops rather than rereading mutable files. The execution config keeps source paths portable but
repoints the model and copied controls into this run. If `covenant_spec_path` is missing there, skip
covenant-compliance and say why.

## Step 4 — Fan out modules and synthesize

Run configured modules whose inputs exist. Pass `execution_config_path` for every borrower-config
argument and `model_path` where an engine accepts an explicit model argument. Follow
`references/orchestration.md`; never pass the canonical config or `_model/` workbook after snapshot.
Archive validated outputs under `<run_folder>/_archive/` and record each module `done` or `skipped`
with `run_record.py step`.

Write `<run_folder>/coverage.json` using `execution_config_path` and mark `coverage` done. Follow
**render-memo** with `execution_config_path`, `model_path`, the run archive, and the verified prior
state. Prioritize by current focus, then fact-check against the same run-local model and archives.

## Step 5 — Write exact outputs and freeze

The snapshotted `model_path` is the human-facing workbook role; do not create a second workbook after
rendering. Write the memo into `<run_folder>`, retain both exact run-relative names, and re-read both.
Mark `memo` done. Call `continuity.py write_back(...)` against
`<run_folder>/config/deal-context.json` with
`carry_forward_path="<run_folder>/carry-forward.json"`, then re-read both JSON files and mark
`write_back` done.

Freeze only after every ledger step is done:

```bash
python ${CLAUDE_PLUGIN_ROOT}/lib/manifest.py finish \
  --run "<run_folder>" --deal "<borrower root>" --period "<YYYY-MM>" \
  --run-id "<run id>" --borrower-key "<borrower key>" \
  --workbook "<actual run-relative workbook>" --memo "<actual run-relative memo>"
```

The validated manifest roles control refresh and future opening; never substitute names derived from
the borrower folder key. Finish produces a draft, not an accepted result. Offer links to its actual
workbook and memo before the choice below, so the user can inspect the materialized changes. If this run
was opened with `--consume-edit`, its manifest now carries which file and why as `edit_intake`; mention it
in the same preview so the analyst sees their edit was actually recomputed into this draft, not just noted.
Writes are ordered, not a multi-file transaction; no recovery journal is created. If a filesystem
or permission error interrupts finish, resolve that error and retry this same command for the same
saved run. For an actual file-deletion restriction, use Cowork's supported deletion-permission tool
for the named output file, explain the scope it grants, then retry the plugin command. Do not truncate
files, reconstruct state, or replace the plugin retry with manual deletion. A stale/edited-file or
corrupt-saved-run refusal needs the specific conflicting evidence resolved, not broader permission.
See `${CLAUDE_PLUGIN_ROOT}/references/store-contract.md`.

## Step 6 — Current-result choice, then close

Present exactly:

1. **Use this run as the current result — default**
2. **Keep it as an unpublished run**
3. **Discard this run**

For choice 1, run:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py select \
  --run "<run_folder>" --deal "<borrower root>" --period "<YYYY-MM>"
```

For choice 1, require the `select` result to carry `required_next_step: refresh-dashboard`, then follow
`refresh-dashboard` only after `current-result-selected` is written. Pass the borrower key so the
artifact refresh reads the same verified, manifest-checked run the analyst selected. **Do not emit a free-form completion, answer “what next,” or stop after selection:** a selected run is not closed until local refresh, portfolio status, and the closing menu below have all completed. Artifact pages are derived views; the run still has exactly two person-facing monitoring outputs.

After that local refresh, follow `portfolio-artifact` only through its local check, candidate build,
and status steps. Do not make a platform call yet. Remember the dashboard action for the closing menu:
**[Create portfolio dashboard]** when status is `create`, **[Open portfolio dashboard]** when status is
`current` or `stale`, and no dashboard action for `recover`. Follow the full `portfolio-artifact` flow
only when the analyst chooses that dashboard action.

For choice 2, run the same command with `--keep-unpublished`. State which run remains selected.
Selection is metadata, not a third person-facing output, and an unpublished run must not refresh the
artifact.

For choice 3, name the exact run being discarded -- read its run id from the draft's own manifest, never
a phrase like "the latest run":

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/monthly-monitor/references/run_record.py discard \
  --deal "<borrower root>" --run-id "<exact run id from the draft manifest>" \
  --period "<YYYY-MM>" --reason "<why the analyst is discarding it>"
```

Discard never deletes the frozen run under `runs/<period>/<run-id>/`; only the visible working copy
changes. When this borrower has a prior accepted run, discard restores the visible workbook and memo
from it and reports `status: accepted`. When there is no prior accepted run yet, discard refuses: the first review cannot be discarded because there is no accepted result to restore. The borrower's settings and model inputs remain untouched -- state plainly that no result is currently accepted for this borrower. If the visible
files were hand-edited since this draft was delivered, the command refuses rather than erasing that
edit; surface the named conflict. If discard was interrupted by a permission or I/O error, resolve
that problem and rerun the same discard command. It checks the saved files, completes a partial
restoration, and does not repeat an already recorded decision.



For choice 1, close with **[Open the memo]**, the remembered dashboard action when one exists, then
**[Run another borrower]** / **[Done for now]**. For choice 2, close with **[Open the memo] /
[Run another borrower] / [Done for now]**. For choice 3, close with **[Open the memo]** only when
discard reported `status: accepted` (the memo it links to is the restored prior result, not the
discarded draft), otherwise **[Run another borrower] / [Done for now]**. The first review cannot be discarded.

## Definition of Done

- Overwrite/unpublished protection ran before any run write; no confirmation bypassed a safety refusal.
- A hand edit to the workbook was either reviewed through `edit-intake` and consumed by a run pinned to it
  with `--consume-edit`, or left blocking; none was silently overwritten, discarded, or exempted by any
  other run, accept, or discard. A memo edit was reverted or left blocking, never carried through.
- New runs recorded the verified before-period base; resumed runs retained their ledger-recorded base.
- Complete config and model inputs were snapshotted, and every downstream stage used the run-local model.
- Every module output passed validation and is archived in the application-preserved run folder.
- Exact workbook and memo roles verify against the integrity-checked manifest; no ledger step remains pending.
- The analyst selected the run or kept it unpublished and was told what remains selected; a selected
  run refreshed its local artifact page and an unpublished run did not.
- Carry-forward, current focus, and `last_reviewed` were written to run-local durable state.
- The financial workbook and memo were re-read and saved into the output folder.
