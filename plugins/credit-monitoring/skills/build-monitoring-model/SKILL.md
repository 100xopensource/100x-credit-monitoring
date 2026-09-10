---
name: build-monitoring-model
description: >
  Build/refresh the canonical monthly financials model (Income Statement, Balance Sheet, Cash Flow)
  for a borrower from its reporting packages. Triggers: "build the monitoring model", "refresh the
  financials model", "append the new month", "rebuild after a restatement". Produces ONE validated
  workbook at the configured model_output_path. This is the data layer; downstream analysis reads it.
metadata:
  version: "0.9.0"
---

# Build Monitoring Model (data layer)

Produce the canonical, validated monthly IS/BS/CF workbook in the borrower’s stable `_model/`
workspace. If the configured model still points into `.record/current`, do not build there: after
write approval, follow **monitor-setup** to verify-copy-verify the workbook and sidecars into `_model/`,
reload the config, then continue. The build follows `references/assembling-rules.md` in full and runs
in the delegated worker below. During a monthly run, analysis reads the run-local snapshot created
after this build, never the mutable canonical workbook directly.

## Delegate the assembly, keep the talking here
Keep the connected source/store roots bound on every subprocess and include both in the worker brief;
logical `source:` and `store:` paths cannot resolve from a previous subprocess's environment.
Once the paths are in hand, hand the build to the plugin's **model-builder** agent (Task tool, subagent
type `credit-monitoring:model-builder`; where the runtime doesn't offer plugin agents, use a
general-purpose subagent, prepend the text of `${CLAUDE_PLUGIN_ROOT}/agents/model-builder.md`, and set
that dispatch to sonnet at xhigh as the agent file declares — prepended text carries no frontmatter). Its
brief carries the borrower-config path, the reporting folder and model output path in all three forms
(the connected-folder path to read, the `/sessions/…/mnt/…` sandbox path bash needs for the recalc and
the gate, and the borrower-config string for engine arguments), the covenant-spec path and the
debt-service schedule path when they exist,
and the prior model and profile on a refresh. It returns a short conclusion — workbook
path, months covered, gate result, restatements, skipped files — and the heavy statement reads never
touch this context.

The agent's job is the **Contract**, **Method** and **Definition of Done** below — that is the part its
brief points at. This section and the closing menu are yours: anything that speaks to the analyst stays
here, including an unavailable folder or file. Say in one plain line what is running while the build
works (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`).

**Answer the diagnosis it hands back, then send the build again.** The worker returns what it saw when
a file was not locally readable, a whole folder read back empty, a package was encrypted, or a path
was too long for Excel. For a locally unreadable file, do not infer a provider or explain sync settings.
Attach the folder guide and offer **[Try again]** / **[Skip this file]** / **[Stop for now]**. For a
genuinely empty folder, say that no files were found and offer the matching choices from
`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`. Once the analyst acts, dispatch the build
again with what changed and say in one plain line that you are picking it back up. A run whose reporting
folder never came back readable ends on that menu, not on an empty workbook.

## Contract
- INPUT: `config.paths.reporting_packages_folder`, `config.paths.covenant_spec_path`,
  `config.paths.debt_service_schedule_path` (when covenant-setup has built it), prior model + profile (if any).
- OUTPUT, all written under the stable `_model/` folder holding `config.paths.model_output_path`: the validated workbook
  (sheets Income Statement, Balance Sheet, Cash Flow Statement, Sources & Notes — laid out per
  assembling-rules → "Output layout contract", which is what every analysis module reads),
  `restatements.json` + `source-map.json` beside it, a dated snapshot, and the per-borrower profile.
- INTERMEDIATE WORK: build attempts and pre-recalc copies live only in ephemeral sandbox scratch.
  Never create `DELETE_ME`, superseded-attempt, or other cleanup-later files in `_model`; the only
  temporary allowed there is the hidden atomic-replace temporary used by the final safe save.

## Method — follow `references/assembling-rules.md` in full. In brief:
1. Inventory packages; identify IS/BS/CF sources (incl. consolidated by-month and YTD files). Sniff
   format (xlsx vs SpreadsheetML XML); gate files that are not locally readable; unhide hidden monthly
   columns. When the whole reporting folder has no usable files, tell the two cases apart from the
   listing right there — file names present but unreadable versus no file names anywhere — and report
   which one you saw rather than silently rereading it. The analyst's session handles unreadable files
   with the folder guide and handles a genuinely empty folder with
   `${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`.
2. Merge-by-period with the restatement scan; latest/consolidated wins; log old→new in Sources & Notes
   AND `restatements.json`. Record the per-column winner in `source-map.json` (assembling-rules →
   "Source map").
3. Full line-item detail; blue hardcoded inputs, black formula subtotals; FY/YTD columns; BS
   balance-check row; mind contra-revenue signs.
4. Cash flow as-reported; derive missing months only with explicit tagging + calibration; CF-vs-IS /
   CF-vs-BS memo rows.
5. Compute the EBITDA bridge using `covenant-spec.json#/definitions/EBITDA` so every module reads ONE
   EBITDA.
5a. Populate the covenant-support rows FROM the debt-service schedule (assembling-rules → "Covenant-
   support rows"): the schedule's contractual cash interest and scheduled principal per month, never
   the ledger's interest line — plus disclosed non-facility amortization. Without a schedule, build at
   lower precision and say so in the conclusion.
6. **Recalc as the LAST write** (assembling-rules → "Recalc") — make every openpyxl cell edit BEFORE
   it, or that cell ships with no cached value and downstream modules read blank subtotals.
7. Run the **validation gate** on the recalced, saved file: `references/validate_model.py <model>` must
   exit 0, plus the checks in assembling-rules → "Validation gate". On pass, save canonical + dated
   snapshot and write/update the profile — **save safely** per assembling-rules → "Versioning & output
   location" (temp-then-replace, no-collapse check, path budget + short copy). On failure, keep the
   last good file, stop, and flag what failed, by how much, which month.

## Definition of Done
- `model_output_path` is outside `.record/current`; workbook written + gated, restatement log in
  Sources & Notes AND `restatements.json`,
  `source-map.json` rewritten covering every statement × month column (loadable via
  `monitor_lib.load_source_map`), EBITDA bridge present, cached values readable,
  layout contract verified, per-borrower profile saved. Return the exact workbook and sidecar paths so
  monthly-monitor can snapshot them into its run before any module starts.

## When you're done — suggest the next step
On a standalone run, close with a menu (`${CLAUDE_PLUGIN_ROOT}/references/asking.md`): **[Run
‹borrower›'s monthly review now]** (follow **monthly-monitor**) / **[Done for now]**. When
Another setup wrapper invoked you, it owns the sequence.
