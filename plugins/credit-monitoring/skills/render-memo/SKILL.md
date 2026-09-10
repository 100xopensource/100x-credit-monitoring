---
name: render-memo
description: >
  Render the monthly monitoring memo as ONE self-contained HTML file in the fixed Credit Monitoring format —
  five tabs (Overview, Deal summary, Covenants, Financial performance, Files & audit),
  figure-trace interaction (every number answers when clicked), and store links. Engines gather
  every figure and compose the page; you write the judgment into the memo's declared text boxes.
  Consumes a completed run's archived module JSONs + the model; runs NO analysis. Triggers:
  "render the monitoring memo", "rebuild the memo HTML", "re-render the memo for {borrower}",
  or invoked by monthly-monitor Step 4.
metadata:
  version: "0.15.0"
---

# Render memo

Every figure on the page comes from an engine, and every opinion comes from you. Gather the
payload, write the boxes, run three more commands, read the result. Four commands and your own
prose — the format itself belongs to the template.

`monthly-monitor` follows this at Step 4. It also runs standalone to re-render a month from an
already-archived run without re-running any analysis.

Set `PM=${CLAUDE_PLUGIN_ROOT}/skills/render-memo/references`. When monthly-monitor invokes this skill,
use its returned `execution_config_path` and `model_path`; never reopen the canonical config or
`_model/` workbook. Standalone, read the saved execution config and model from the requested manifest-checked
run but write payload, prose, and memo to a separate requested output folder. Missing saved inputs mean
the run cannot be reproduced safely: say so and stop.

All three files this skill writes — payload, prose, and memo — go in ONE folder: the folder the
caller names. `<input_run>` is the verified run holding `_archive`, execution config, and model.
Called by `monthly-monitor`, `<input_run>` and `<out>` are the same active run folder, so the freeze
covers all three. Called standalone, `<input_run>` is application-preserved and integrity-checked, while `<out>` is a separate requested
output folder.

The engines run in fresh shell processes. Bind the same `CREDIT_MONITOR_SOURCE_ROOT` and
`CREDIT_MONITOR_STORE_ROOT` supplied by run-monitor on every command and delegated brief, then confirm
the shell reaches `$PM` and the saved run. When either root is unavailable, ask for every needed folder
in one message (`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`).

## Step 1 — Gather the figures

```
python $PM/memo_payload.py --borrower-config <execution_config_path> --period <YYYY-MM> \
  --archive <input_run>/_archive --model <model_path> \
  --out <out>/memo-payload.json
```

It reads the period's archived module JSONs, the model, the covenant spec, the deal background,
the collateral and budget maps, and the document registry, and writes every figure with the source
it came from. It prints how many figures it carries and how many things the run could not cover.
Keep the payload beside the memo: the gate reads it, and a re-render months later reproduces the
same page from it.

## Step 2 — Read what this deal already knows

Read `deal-notes.md` through the execution config's run-local control path when there is one: the readings the
team has settled, the borrower's known mistakes, and the questions still open. Settled readings
are what your boxes must agree with, and the open questions belong in the `OPEN_QUESTIONS` box
naming whoever holds each answer.

A settled reading may still carry language from before `risk_rating` was retired — a grade, a
scale, "Rated 5" or similar; nobody rewrites old entries. Carry forward the trajectory and thesis
behind that reading, never the retired grade itself, and never invent a replacement scale for it.
This is not licence to soften a genuine covenant, liquidity or performance concern — those are
figures, not a grade, and belong in their box as plainly as ever.

## Step 3 — Write the boxes

```
python $PM/render_memo.py --prose-keys --payload <payload.json> \
  > <out>/prose.json
```

That prints exactly the boxes this borrower's month has — twenty-two for the page as a whole, and
three keyed by row (a covenant's delta note, a capital-structure row's pricing note, a document's
description), pre-seeded with this run's own ids. Keep the file beside the payload: a re-render
months later needs both. Fill it in where it sits.

The file's `_contract` key is the whole writing contract — which boxes take `<li>` items and
which take sentences, the deep-link markup and the section ids it may point at, and the open
items the gate will look for in your flags prose (each item's identifying token, or its dollar
amount). Read it there; the engine source has nothing more to say about the format.

Read the payload before writing a word — the whole point of Step 1 is that every number you cite
is already in it, with its trace. Then follow `references/report-structure.md` for what belongs in
each box, its length, and the language contract (lead with the fact and its number; the banned
words are listed there and they are banned because they read as commentary rather than credit
work).

What earns a place in a box:

- The executive summary's five blocks (`SUMMARY_*`) read like a portfolio review to partners: the
  figure, its comparator (vs Budget, vs prior year), then the driver — 2–5 sentences each, rounded
  to $X.XM. Bad news sits in its home block with the same syntax as a beat, and a fired concern is
  never left out of its block; the full block contract rides in the prose file's
  `_contract.summary_blocks`.
- Each statement's observations say what the year-to-date path shows, not what the rows already
  say. A sentence that would read the same in any month is a method statement: it belongs in the
  data-quality notes, once.
- A covenant's delta note says in one clause why the certified figure and ours differ, and which
  one governs.
- The flags box covers every open item the run carried in from the deal background — the gate
  names any it cannot find in your prose.

Leave a box empty when the month gives it nothing, and say so in your report. The memo prints a
plain line where an empty box sits; `--empty-boxes refuse` on either the renderer or the gate
turns that into a stop, and that switch is how the credit team's answer on empty boxes gets
applied.

## Step 4 — Compose, brand, gate

```
python $PM/render_memo.py --payload <payload.json> --template $PM/memo-template.html \
  --prose <prose.json> --run-date <today YYYY-MM-DD> \
  --out "<out>/<borrower> Monitoring Memo.html"
python $PM/inline_assets.py "<memo.html>" && python $PM/inline_assets.py --check "<memo.html>"
python $PM/validate_memo.py --payload <payload.json> --memo "<memo.html>"
```

The renderer fills the template's tokens from the payload and your prose. The gate holds the
finished page to the run behind it: every printed and drawn figure against the payload, one
borrower and one period, parts that foot, every gap the run recorded reaching the reader, every
anchor landing, and the five tabs intact.

A refusal names what disagrees. Fix the cause and re-run the chain: a figure on the page that the
run did not produce is a defect in an engine or in something a box says, and the last good memo
stays in place until the new one passes.

## Step 5 — Read it as the analyst will

Open the memo and read your own prose against the tables beside it. Check the covenant verdict
matches the strip, that each observation still holds against the figures the engine printed, and
that no sentence names a file the team keeps for itself rather than a document the borrower sent.
Then report: the memo's path, which boxes you wrote, which you left empty and why, and anything
the run could not cover.

## What stays with the engines

The page's structure, its CSS and JS, the number and date styles, every figure and its trace, the
document registry, the mechanical data-quality notes, and the ordering of the flags. Adapting to a
deal happens in the config, the covenant spec and the deal notes — never by editing an engine or
the template, and never by typing a number into the page (ADR-0002).

## Output

Exactly ONE self-contained `.html` file named `<borrower> Monitoring Memo.html`. When called by
`monthly-monitor`, write `<run_folder>/<borrower> Monitoring Memo.html`; the application-preserved run folder is
the period record. Write to a temp file in the SAME folder and replace, then re-read the final file
before reporting done. If the output folder is not connected,
ask for it with a menu (`${CLAUDE_PLUGIN_ROOT}/references/connecting-folders.md`). Asked to put
the memo somewhere OTHER than the borrower's own monitoring folder, write the memo, its payload
and its prose there together — the three travel as one record — and read the borrower's config,
model and archives where they already are, writing nothing into the borrower's folder. When
`monthly-monitor` invokes this skill it owns the completion wording; standalone, say the same in
plain words. The memo is saved for analyst review and goes nowhere else.

## Definition of Done

- `validate_memo.py` prints OK, and `inline_assets.py --check` prints OK.
- Every box you meant to write is written, and every one you left empty is named in your report.
- The memo carries the borrower name beside the workbook, and every figure came from the saved
  execution config, model, and archive rather than mutable canonical state.
- A standalone re-render left the frozen run unchanged and touched only the separate requested outputs.
- You read the finished page, not only the command output.
