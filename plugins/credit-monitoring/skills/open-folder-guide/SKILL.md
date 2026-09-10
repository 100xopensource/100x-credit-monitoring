---
name: open-folder-guide
description: >
  Open the illustrated folder setup guide without starting monitoring or requesting folder access.
  Use when the user says “open the folder guide”, asks how local or synced folders work, or wants
  pictures for folder setup.
metadata:
  version: "0.1.0"
---

# Open Folder Guide

Give one clearly labelled link to the public
[Folder guide](https://github.com/100xopensource/credit-monitoring-folder-guide). If its documentation
is unavailable, say so plainly and use the packaged guide below without inventing public steps.

Locate the installed plugin root from `${CLAUDE_PLUGIN_ROOT}`. If it is unavailable in Cowork, search
only `$HOME/mnt/.remote-plugins` for this plugin's `references/render_guide.py`; never search
user-selected folders or expose the internal mount path.

Render the self-contained guide and attach the printed output file:

```bash
python ${CLAUDE_PLUGIN_ROOT}/references/render_guide.py
```

Open the attachment when Cowork provides that action; otherwise tell the user that the attached guide
contains the Local, OneDrive / SharePoint, and Google Drive tabs. Do not ask for a borrower, request
folder access, write monitoring state, or interrupt an active run. This skill does not start folder
setup. Keep any selected borrower and the current run or setup intent unchanged; do not clear or reset
them. Preserve the pending documents-folder or results-folder action too. Pause after showing the
guide; do not automatically open anything or request folder access in that turn. Tell the analyst to
say “continue” when ready so the same intent can resume.
