# Monitoring store: files, saved runs and confirmation

This is the OS adaptation of the Monthly Monitor store structure. It uses the existing OS run snapshots and financial workbook/memo outputs. It does not add a multi-user approval system.

## Folder layout

```text
credit-monitoring-output/
  store-schema.json                 # identifies this new store format
  credit-monitoring-registry.json    # optional borrower registry
  artifacts/                        # optional existing OS dashboard
  borrowers/<borrower-key>/
    <financial workbook>            # visible working copy
    <monitoring memo>               # visible working copy
    <anything else>                 # the analyst's own; the plugin leaves it alone
    _model/                         # existing isolated model workspace
    .record/
      manifest.json                 # identifies the visible copy and its state
      published.json                # accepted run for each month, NOT an upload receipt
      changelog.jsonl               # acceptance/discard history
      edit-intake.json              # reviewed workbook edit; finish removes it, runs ignore a spent one
      store.lock                    # local process lock
      current/                      # working settings, outputs and workbook copy
        config/
      pages/                        # optional, rebuildable dashboard data
      runs/<YYYY-MM>/<run-id>/
        manifest.json               # hashes and output roles for the saved run
        run-ledger.json             # inputs, base, progress and delivery checks
        <financial workbook>
        <monitoring memo>
        config/
        <module results>
```

The per-run ledger is authoritative for that run; no second ledger is needed at the borrower root. OS keeps its existing `current/` and `_model/` paths. The borrower-level accepted pointer replaces the old shared pointer at the store root. There is no existing-store conversion in this release.

## What a borrower folder may hold

The plugin owns two parts of `borrowers/<borrower-key>/`: `.record/` and `_model/`. It also writes the visible working copies beside them. Anything else in the folder is the analyst's own, such as notes, an export or a folder of downloads. Store setup ignores it apart from the two refusals below, and no step writes, moves or deletes it. A file at the folder's top level does count as working state, though: adding or changing one while a run is open makes that run stale. A folder does not.

Store setup still refuses, anywhere in a borrower folder, the analyst's own folders included:

- a symbolic link;
- a `.record` folder other than `borrowers/<borrower-key>/.record`.

A borrower's documents stay in the source folder. The borrower-config cites each input where it sits there, as `source:` and its path, even a file with a name that says nothing, such as `1.pdf`. monitor-setup refuses an input path that starts with `store:`: a copy in the store is one nobody updates when the borrower files a new version.

## Versions and reconnecting

New stores get `store-schema.json` with `schema: credit-monitoring-store/v1` and `schema_version: 1`. Setup refuses nonempty unversioned stores, unsupported versions, the old root publication pointer, and flat borrower folders. It does not convert or clear them.

Run manifests use `credit-monitoring-run-manifest/v1`; ledgers use `credit-monitoring-run-ledger/v1`; accepted pointers use `credit-monitoring-published/v1`. These records carry `schema_version: 1`. Existing calculation outputs retain their own formats. No transaction journal is created.

Symlinks, path traversal and source/output overlap are not supported. An unknown file at the store root is not permission to take over the folder. Each refusal names the paths inside `credit-monitoring-output` it refused, up to ten, says why for each, and leaves the folder unchanged.

## Draft → inspect → accept or discard

**Finish:** Preserve the completed run, then show its workbook and memo as a draft. Do not change the accepted pointer. Before delivery, check both the previous delivered files and the state saved when this run opened. Newer work makes the run stale rather than being overwritten.

**Accept:** The user confirms the exact visible run. Verify its saved outputs and check that working files/settings have not changed. Write the accepted pointer, decision history and visible state in order. These are separate file writes, not one atomic transaction; retry the same select command if interrupted. Reconfirming an already accepted visible run does not duplicate the decision. The existing history event name is `current-result-selected`.

**Discard:** Name the exact visible draft. Preserve its saved run and record a `discarded` event, including an optional reason. Restore the latest accepted month's working files. If the working files/settings were edited since delivery, stop instead of erasing them.

**First review:** A borrower's first draft cannot be discarded, because no accepted result exists to restore. Discard refuses and changes nothing. The analyst accepts the draft or leaves it unpublished.

The visible files may be a draft while the dashboard shows an accepted run. Readers must use the accepted pointer and verified saved outputs, not treat the borrower-root files as accepted facts.

## Starting and resuming

`run_record.py open` records the current working state before computation. With no explicit base, it uses the latest accepted run at or before the review period for continuity. A same-month revision retains that month's accepted context/open items; financial comparison separately uses `base --before-period` and never treats the same-month continuity base as an earlier period. For an explicit draft continuation, provide `--draft-base --base-run <id> --base-period <month>`. A discarded run is not a draft continuation base.

An interrupted run retains its original inputs and base. A newer acceptance does not silently change those inputs. However, if working state changed meanwhile, the old run cannot deliver over it: preserve it and start a fresh run. A repeated run ID cannot overwrite an existing folder.

## Interrupted changes

Finish, accept and discard share a local lock and use ordered, single-file replacements. There is **no transaction journal and no multi-file atomicity**. During an interruption, working copies may be partially updated. Acquiring a lock does not replay or repair anything.

Resolve the reported filesystem or permission problem, then retry the **same command with the same run ID and period**. Finish verifies the saved run and compares working files with the hashes pinned when it opened and the intended saved outputs. A partial copy can complete; a third, edited version is not overwritten. Discard records its decision before restoration and uses the saved draft/accepted outputs to recognize a partial restoration. Acceptance and discard retries avoid duplicate decision entries. The working manifest is written last.

Claude should handle supported filesystem permissions as `runtime/<runtime>/files.md` says and retry the plugin command, not ask the user to edit internal records. Rerun the original command, not a hand-written replacement. No deletion permission is needed merely to clear a journal after a first delivery.

If the saved run is damaged or a working file matches neither the original nor intended content, report the specific ambiguity and preserve the files. Do not reconstruct metadata or overwrite the edit to force completion. A leftover journal from an earlier experimental candidate is not read, replayed or deleted by this version; preserve that store for investigation and use a fresh folder for candidate QA.

## Workbook edits and local dashboard

Raw hashes still verify saved copies. Content comparison reads what a person sees: the cells (a formula by what it means), defined names and external-link presence, then, with identical cells, the look: formats, notes, charts and the rest the workbook shows. A re-wrap that changes none of these is same content. A look this plugin's openpyxl cannot read leaves the cell reading to decide, and `check` says the look was not checked and why (`look_unchecked`). Changed settings remain protected.

Use `run_record.py check --json` to review each changed workbook's comparison digest and what moved: `moved` lists each changed cell with its line and column (`what`) and its old and new value (`was`, `now`), and an added or removed row or sheet as one entry; `look` names the first other thing that looks different, such as a note or a fill, next to the moved cells or alone, and `unread` says why that could not be compared. Then `edit-intake --edit <path> <digest>` to record the exact reviewed bytes. A workbook whose look alone changed is taken as saved by the run that consumes it. Intake is not acceptance and does not permit accept/discard to overwrite edits. Open a new run with `--consume-edit <path>` and snapshot its reported source workbook; sidecars come from the verified base run. The run must consume that exact workbook before delivery, recompute analysis and memo, and retain provenance in its frozen manifest. Further edits or unselected changes block delivery. Memo intake is not supported; preserve those edits rather than erase them.

For register-backed workbooks, manual saves do not trigger propagation. On an explicit request,
intake classifies numeric changes to trusted inputs or register values, and number fills in an
existing monthly input row. A completely blank row requires an explicit `rows` hit with `type: input`;
otherwise a row must already have another register-backed input and no calculation formulas.
New rows, section headings, subtotals and arbitrary formula/structure changes remain unsupported.
Direct edits to generated calculation outputs are retained but report incomplete propagation; they
are not accepted then overwritten by regeneration. Generated schedule values with a precise pointer
remain eligible numeric inputs, distinguished from generated calculations. Snapshot preserves
the exact pinned workbook under `edited-original/` and recalculates a distinct run-local output with
its matching source map. The ledger records `inputs.edited_original` and `inputs.refreshed_digest`;
finish checks the preserved pin and refreshed output separately. Prior source values are context for
analyst edits, not validation. Failed refresh (including a values-only input without a formula-bearing
build) reports incomplete propagation without completing the model step, spending the edit, or
replacing the human's workbook. Existing non-register snapshots and historical ledgers are unchanged.

Local dashboard rows use accepted results. The currently visible draft has a separate preview; prior accepted periods use their own verified outputs. This is not a complete browser for every saved version. Rebuilding these derived pages neither accepts a draft nor uploads anything.

## Limits and recovery

- Local macOS/Linux process locking only; not coordination between computers through cloud sync.
- Close editors while delivery, acceptance or discard is running. An external editor does not take the plugin lock; file checks are not a cross-application filesystem transaction.
- Manual refresh can regenerate derived dashboard pages from verified runs. It cannot reconstruct a corrupt or missing saved output, silently choose another accepted result, or convert an old store.
- Candidate packaging and manual Cowork QA are separate release gates; passing source tests is not proof of either.
- Updating local records does not update a hosted dashboard. Only claim an upload after actual publication evidence.
