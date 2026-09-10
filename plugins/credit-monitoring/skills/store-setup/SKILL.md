---
name: store-setup
description: Choose or reconnect the read-only source and persistent output folders. Use during first setup or when either folder cannot be reached.
metadata:
  version: "0.2.0"
---

# Set up the monitoring folders

Locate the installed plugin root from `${CLAUDE_PLUGIN_ROOT}`. If it is unavailable in Cowork,
search only `$HOME/mnt/.remote-plugins` for this plugin's `references/render_guide.py`; never search
user-selected folders. Use that root for all commands below and do not mention its internal mount
path to the user.

If both valid folders are already reachable, skip all onboarding readiness gates, do not resend the
guide, and return both roots to the caller. This preserves the returning workflow.

Reuse each already-connected, approved role independently; do not require both to be reachable before
reusing either one. A connected arbitrary folder is not an approved role by itself — only a folder
already validated and bound to the source or output role counts as reachable for that role. Never
infer an ambiguous role and never trust chat history alone to decide it.

- **Only the source is reachable** (the output store is not): acknowledge the retained source using
  only its readable basename and its read-only role, then skip straight to the results-folder step
  below without repeating the documents-folder question or resending the guide.
- **Only the output store is reachable** (the source is not): acknowledge the retained results
  location using only its readable basename, then skip straight to the documents-folder step below
  without repeating the results-folder question or resending the guide.
- **Neither is reachable**: run both steps below in order, exactly as on first setup.

Pausing or cancelling one step never loses the other already-approved selection.

## First folder: company documents

On first use after the analyst consents, render and attach the illustrated guide without automatically opening it:

```bash
python ${CLAUDE_PLUGIN_ROOT}/references/render_guide.py
```

Say **Before asking for the folders** from `${CLAUDE_PLUGIN_ROOT}/references/asking.md`. Do not ask
which provider the analyst uses, infer one from a path, or explain provider setup in chat. The guide
owns those details. When no company name is known yet, substitute “the company” for `{name}`; do not
ask for or invent a name before source access.

After the explanation and guide are visible, call `AskUserQuestion` with:

> "Are you ready to choose the folder that holds {name}'s documents?"
>
> **[Choose documents folder]** / **[Read folder guide]** / **[Not now]**

Wait for the analyst's response. Do not open the source folder picker in the same turn as the
explanation and readiness question.

- **Choose documents folder** → open the source folder picker. The folder may have any name and is
  read only. Do not ask the user to type a path.
- **Read folder guide** → follow **open-folder-guide**, preserve the selected borrower, route intent,
  and pending documents-folder action, then pause the current turn. Do not automatically open a
  picker after help. Open it only after a later explicit “continue” or **Choose documents folder**.
- **Not now** → pause at the documents-folder step. Open no picker.

An explicit request to choose the documents folder does not need a duplicate readiness gate when the
same explanation was already given in the active route. If it was not, give the explanation first.

## Second folder: results

After the source picker succeeds, acknowledge the actual selection using only its readable basename:

> "I can read **{source basename}** for the company's documents. I will not change anything there."

Then explain, “The next step is to choose a separate output parent folder where results should live,
not `credit-monitoring-output` itself,” and call `AskUserQuestion` with:

> "Are you ready to choose the separate folder where the results should live?"
>
> **[Choose results folder]** / **[Read folder guide]** / **[Not now]**

Wait for the analyst's response before continuing.

- **Choose results folder** → open the output parent folder picker. Do not ask the user to type a path.
- **Read folder guide** → follow **open-folder-guide**, preserve the source selection and pending
  results-folder action, then pause the current turn. Do not automatically open a picker after help.
- **Not now** → pause at the results-folder step and retain the approved source selection.

An explicit request to choose the results folder does not need a duplicate readiness gate when that
explanation was already given. If it was not, give the explanation first.

If the analyst cancels either native picker, safely pause that current folder step. Existing saved
monitoring progress remains available. Do not automatically retry, do not skip ahead to the second
step after a cancelled source picker, and do not claim that nothing was written if earlier preparation
already wrote state. A cancelled results picker retains the approved source selection.

## Prepare and acknowledge

Once both roles are settled — freshly picked, retained from this session, or a mix of both — validate
that source and output do not contain or alias one another. Make no write until an appropriate,
separately approved destination is available.

The `--parent` value is always the output *parent* folder, never the prepared `credit-monitoring-output`
root itself. When the output role was retained rather than freshly picked, resolve `--parent` as that
retained root's parent directory. Passing the retained store root itself as `--parent` would nest a
second `credit-monitoring-output` inside the first one; never do that. Then run `store.py`
with an argument vector, passing the resolved parent and the source-folder value directly as the
`--parent` and `--source-root` argument values:

```text
[
  "python",
  "${CLAUDE_PLUGIN_ROOT}/lib/store.py",
  "prepare",
  "--parent", <selected parent>,
  "--source-root", <selected source folder when known>
]
```

Do not interpolate selected paths into a shell command string.

The command creates or reuses `credit-monitoring-output` and prints its root. Never use a temporary/session folder as a fallback.

After preparation and validation succeed, give a factual post-access acknowledgement using only each
readable basename, never an internal sandbox or absolute path:

> "Both folders are connected. I will only read company documents in **{source basename}**. I will
> keep results inside **{output parent basename}**. Your original documents stay unchanged. No review
> has started and no review files have been generated in this folder step."

When a company was named, say next that you will check its saved monitoring work and continue from the
right point. When none was named, say next that you will check saved monitoring work and then ask which
company to review. Do not imply that a review started or that files were generated. Return both roots
to the caller so it can run the normal state classification; do not classify from chat history.

If preparation refuses an existing folder, explain that it contains unknown data or an unsupported store layout and was left unchanged. Offer:

- **Choose a different parent folder**
- **Review the files myself, then retry**
- **Stop setup**

If the user chooses to review the files, pause without changing anything and retry only when they say they are ready. Never offer to delete, clean up, rename, move, or overwrite unknown data, and never create a suffixed alternative such as `credit-monitoring-output-2`.

Do not resend the guide when both valid folders are already reachable. Attach it again only when the analyst explicitly asks, is unsure, either folder cannot be reached, or files are not locally readable. Explain only the local access problem and use the provider-neutral wording in `references/asking.md`; do not troubleshoot the sync provider in chat.

This skill prepares only the store root. It does not create a deal, registry, model, monitoring run, or artifact.
