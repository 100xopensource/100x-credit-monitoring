# Reaching the user's folders in Cowork

> Tenant paths and catalog lookup are outside this public workflow.
> The same access and safety rules apply to local and synced folders.

The monitor reads borrower documents and writes results to folders the user chooses. A session
reaches those folders when the user approves them through Cowork's folder picker. Folder connections
can belong to one session, so a new session may ask again. Treat that as routine: folder approval can
be session scoped, so reconnecting folders does not repeat setup.

Where a skill says to request or reach a folder, in Cowork that means this folder picker. A stop
choice opens no picker. A session that runs in the cloud reaches the folders on the analyst's
computer instead: **When the folders are on a connected computer** below says how, and it applies
on top of everything else in this file.

## The folders you need

| What it is | How it is used |
|---|---|
| The folder containing the borrower's documents | Read only; never changed |
| The chosen output parent folder | Holds `credit-monitoring-output`, which the monitor writes |

The borrower source folder may have any name. Local folders are recommended and need no account.
Credit Monitoring also works directly with folders already on the computer through OneDrive,
SharePoint, or Google Drive for desktop. There is no separate manual upload or migration. Claude processes
connected content under your Anthropic terms, privacy controls, and retention settings. The source, output
parent, or both may be synced, and every choice uses the same store shape and persistence code.

## Request the folders in this order

**Store-setup owns this sequence.** Callers invoke it after consent and do not repeat its guide,
explanation, or picker actions.

1. Explain both folder roles before opening a picker. State that local folders are simplest and that
   existing OneDrive, SharePoint, or Google Drive folders work directly, without a separate manual upload or
   migration. State that the source is read without changes, only the separately approved output
   location is written, and Claude processes connected content under the user's Anthropic terms,
   privacy controls, and retention settings. The source, output parent, or both may be synced.
2. On first setup after consent, attach the rendered folder guide without automatically opening it.
   Do not ask the analyst to choose a provider or explain provider preparation in chat.
3. Use `AskUserQuestion` for the documents-folder readiness menu and wait for the answer before the
   source picker. Reading the guide or choosing **Not now** pauses this step and opens no picker.
4. After a successful source selection, acknowledge its readable basename and read-only role. Explain
   the separate results folder, use its readiness menu, and wait again before the output picker.
5. A cancelled picker pauses only its current step, retains existing saved progress and any approved
   source selection, and never retries automatically or skips ahead.
6. Say **output parent folder**, not just parent. Ask for a separate writable parent, not
   `credit-monitoring-output` itself. Source and output may share a higher-level folder but must not
   contain or alias one another.
7. Only after both selections, prepare the fixed child by calling `${CLAUDE_PLUGIN_ROOT}/lib/store.py`
   with an argument vector:
   `prepare`, `--parent`, the selected output parent value, `--source-root`, and the selected borrower
   source value. Pass picker values directly as arguments; never interpolate them into a shell command
   string.
8. After validation, acknowledge both roles with their readable basenames, say that original documents
   remain unchanged and no review started, then return to the caller for state classification.
9. If the request fails or the user is unsure, attach the folder guide in the same message:

   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/references/render_guide.py
   ```

Never end the turn hoping they come back. Offer: **[Choose the folder] / [Show the guide] / [Stop for now]**.

## Unknown existing data

If `credit-monitoring-output` contains data that is not a valid store, say it was left unchanged, name
each path the store check refused and why, and offer:

- **Choose a different parent folder**
- **Review the files myself, then retry**
- **Stop setup**

On review, pause until the user says they are ready. Never delete, clean up, rename, move, or overwrite
unknown data for them. Never create a suffixed or temporary fallback.

## Empty is not the same as not connected

- **Not connected** — the session cannot reach the folder. You know nothing about what is inside.
  Never report it as empty.
- **Connected and lists no files** — the folder is genuinely empty. Say so plainly, naming it.
- **Connected and one file is absent** — name the missing file. The folder itself is fine.

## A file that is not locally readable

A file can show its name while its contents are not available on the computer. Attach the folder guide
and explain that the file cannot be read locally yet. Do not infer a provider or walk the analyst
through sync settings in chat. Retry only after the analyst says the files are available.

## Never

- Never write to the borrower source folder.
- Never write outputs to a temporary/session folder or any folder the user did not choose. The one
  exception is the work area of a session on a connected computer, which is written back to the
  chosen folders before any result reaches the analyst.
- Never ask the user to type or paste a path.
- Never show a sandbox mount path or save an absolute host path in store data.
- Never call an unconnected folder empty.
- Never silently create `credit-monitoring-output-2` when the fixed child conflicts.
- Never send the guide on its own. Words first; the guide supports them.

Plain local source and output folders work without OneDrive, SharePoint, or Google Drive for desktop and are the recommended first-run choice.

## Before opening any folders

For a route where the state is literally `unknown` or `new`, the permission menu says what
continuing means:

> "Before I open any folders, would you like me to continue and save this review in a folder you
> choose, or stop without changing anything?"
>
> **[Continue and choose folders]** / **[Stop without making changes]**

## When a folder is not reachable

When the source or monitoring root is not reachable, follow **store-setup** to obtain both folders
without asking the user to type a path. Store-setup owns the guide attachment, capability copy, and
both picker requests; do not deliver any of those separately. It also owns the factual post-access
acknowledgement of both selected folder roles. After the store is reachable and that acknowledgement
is visible, return to the caller.

**Use my own documents** from the onboarding menu goes directly to **store-setup**. That choice is
route intent, not permission to open a native picker immediately: store-setup must explain both
folders, attach the guide, ask its readiness question, and wait for the answer.

When **render-memo** finds either root unavailable, ask for every needed folder in one message. When
its output folder is not connected, ask for it with a menu.

## What to say about the folders

Say what is in each folder and what you will do with it. Never name them by their job.

> "There are two folders I need. One holds the company's paperwork, the statements and reports they
> send us. I only read from that one and I never change anything in it. The other is where I keep my
> work for this company. Your finished financial workbook and monitoring memo will be saved there so
> the team can find them."

Explaining folder approval when it comes up:

> "Credit Monitoring works directly with folders already on your computer, including folders synced
> through OneDrive, SharePoint, or Google Drive for desktop. There is no separate manual upload or migration. Claude processes connected content under your Anthropic terms, privacy controls, and retention settings.
> The source, output parent, or both may be synced. A new Cowork conversation may ask you to approve
> the folders again. That is normal and does not repeat the borrower setup."

| Never say | Say this |
|---|---|
| connect a folder, grant access, a mount | let me open a folder, give me permission to look inside |
| the file is a cloud only placeholder | the file is still online and has not come down to your computer yet |

Say these as written and fill in the blanks.

**Before asking for the folders** (attach the folder guide on first setup, then say this before the
readiness question and before opening either folder picker):
Say the following before opening either folder picker:
> "Credit Monitoring works directly with folders already on your computer, including folders synced
> through OneDrive, SharePoint, or Google Drive for desktop. There is no separate manual upload or
> migration. Claude processes connected content under your Anthropic terms, privacy controls, and
> retention settings. Local folders are the simplest option.
>
> I need two folders:
>
> - **Company documents:** the folder holding {name}'s financial reporting and loan documents. I only read it and never change it.
> - **Results:** a separate folder you approve for the financial workbook and credit monitoring memo.
>
> [Read the folder guide](https://github.com/100xopensource/credit-monitoring-folder-guide). I have
> also attached a packaged copy. Cowork will ask you to approve each folder, and nothing on your
> computer is moved."

**When the request does not work**, send the folder guide in the same message:
> "That did not work, and it is not something you did wrong. I could not open that folder in this
> Cowork conversation. This folder step is paused, your original documents are unchanged, and any
> saved monitoring progress remains available. I have attached a short guide for making a local or
> synced folder available."
> **[Try choosing again]** / **[Choose another folder]** / **[Stop for now]**

**When files are not locally readable**, send the same guide:
> "I can see {name}'s folder, but one or more files are not available on this computer yet, so I
> cannot read them. Your original documents are unchanged, and any saved monitoring progress remains
> available. Use the relevant section of the attached guide, then retry when the files are available."
> **[Try again]** / **[Choose another folder]** / **[Stop for now]**

## A file Cowork will not let you delete

A plugin command never stops because a folder refused to remove a file. The file stays where it
was, the store accepts it there, and `manifest.py finish` lists it under `not_removed`. Ask for
Cowork's deletion permission only when that file is the workbook or memo directly inside the
borrower's folder: name the file, explain the scope Cowork grants, then remove it.

## Paths a worker uses

One file has three path strings, and each caller takes exactly one:

- **Read / Grep / Glob / Edit / Write** take the path as the analyst's folders present it — the
  connected-folder path the brief gives as "to read it yourself". The file tools refuse a
  `/sessions/…/mnt/…` string.
- **bash** (`python`, `ls`, `pdftotext`) takes the sandbox path, the `/sessions/…/mnt/…` form.
- **Engine arguments** take the string from the execution config (`source:...` or run-local
  `store:...`).

A worker brief gives every path in all three forms, including the `_archive` path, since the worker
writes its output with Write, gates it through bash, and reads it back. In a session on a connected
computer, the file tools and bash both take the path under `<work area>`.

## When the folders are on a connected computer

A Cowork session that runs in the cloud has the `mcp__remote-devices__*` tools and nothing attached
under `/sessions/*/mnt/`. The plugin runs in the cloud. The analyst's folders stay on their
computer and are reached through the Claude Desktop app there, which must stay open. Check for both
kinds every session, because a folder can also be attached later, and folder grants end with the
session.

The engines read and write ordinary folders, so the run works on copies. Stage what it reads into
`<work area>`, a new folder in this session's own home folder, run everything there unchanged, then
write the results back. The folders on the computer are reached only through these tools:

| Tool | Use |
|---|---|
| `get_device_info` | `connectedFolders`, each with its full path on the computer |
| `device_list_dir` | List a folder. Works only inside a connected folder |
| `device_request_folder_access` | Ask the analyst to approve one exact path. A wrong path fails without showing them anything |
| `device_stage_files` | Copy files from the computer into this session, under `/mnt/user-data/uploads/`. Reaches a file at most 7 folders below the connected folder it is in |
| `device_commit_files` | Write session files to exact paths on the computer, replacing a file already there. Answers `written` or `rejected` for each file |
| `device_request_delete_permission` | Ask the analyst to allow removing files. Nothing can be removed on the computer until they do. Only for step 4 below |
| `device_bash` | Only to list a connected folder's files and for step 4 below. A connected folder is at `$HOME/mnt/<its folder name>` there, not at its full path |

### Getting the folders

Where this file says to open the folder picker, ask the analyst to add that folder with the **Add
folder** button in Claude Desktop. The notice that the folder connected carries its full path; use
that path for every later tool call this session. When the analyst names a folder in words, request
exactly that path with `device_request_folder_access`. Never build a path from the device name or
from names seen in the home folder, and never request a folder the analyst did not add or name.

A borrower's saved runs sit too deep to stage through the output parent:
`credit-monitoring-output/borrowers/<borrower key>/.record/runs/` holds files 8 and 9 folders below it.
`device_request_folder_access` can't fix that, because a folder inside a connected one counts as
already connected. So once the output parent is connected and `credit-monitoring-output/borrowers`
exists in it, ask the analyst to add that folder too, once per session, with the Add folder button:

> "One more folder, please: the one where I keep each company's saved reviews. It's inside your
> results folder, at credit-monitoring-output › borrowers. Add it with the Add folder button in
> Claude Desktop, and I'll carry on."

Stage every file under it only after the notice that it connected. Its files are at most 7 folders
below it.

### Run on copies

1. **Stage** as soon as the folders are connected, before store-setup prepares the store. List the
   folders with `device_list_dir`, stage with `device_stage_files`, and copy each file from the
   `stagedPath` the result gives to its own path in the work area, keeping each folder's layout:
   - `<work area>/source/` mirrors the source folder: the borrower's folders the run reads,
     including the reporting files for the period.
   - `<work area>/output/` mirrors the output parent: the files directly inside
     `credit-monitoring-output`, its `artifacts/` folder, and the borrower's folder under
     `borrowers/` with its `.record/`. When `artifacts/` does not exist yet, stage every borrower's
     folder, because the first dashboard build covers the whole portfolio.

   Then record the path and SHA-256 of every staged file. Step 3 finds what changed from that list,
   and step 4 what the run removed.
2. **Run** with `<work area>/source` as the source root and `<work area>/output` as the output
   parent. Store-setup's `prepare`, the run record, every engine and every worker brief take
   these paths wherever they would take the connected folders. Engine arguments still take the
   execution-config strings, so nothing in the store names the work area.
3. **Write back** before every message that hands the analyst a result or ends the run: the
   delivery, and the store change that follows their final choice. Commit every new or changed file
   under `<work area>/output/` to the same relative path in the output parent on the computer. A
   commit replaces the file there, so saving a review over older copies needs no delete permission.
   A file is saved only when it appears in `written`. Commit each `rejected` file once more, and if
   it is rejected again, tell the analyst which files did not save. Never commit `store.lock`, a
   temporary file (`.store-write-*`, or a name ending in `.tmp`) or anything outside
   `<work area>/output/`. Transfer and scratch files stay in `<work area>`.
4. **Leave what the run removed.** When a review replaces another, the store retires older copies
   under `.record/current/` and the reviewed-edit record `.record/edit-intake.json`. Leave those on
   the computer: the store check accepts them there, as it does in a channel's SharePoint folder.
   The one removal to carry over is a workbook or memo directly inside the borrower's folder that the
   run removed, which happens only when the delivered names change. The analyst would otherwise open
   an outdated copy. After step 3 saved everything, ask with `device_request_delete_permission`,
   naming that file and why it goes, then remove it with `device_bash`. If the analyst says no, tell
   them the old file stays and is no longer updated.

Never copy plugin code, a plugin archive or an engine onto the computer, never download code, and
never run an engine with `device_bash`. The plugin that runs is the one installed in this session.
