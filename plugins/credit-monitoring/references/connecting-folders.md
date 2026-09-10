# Reaching the user's folders

> Tenant paths, remote-computer tools, and catalog lookup are outside this public workflow.
> The same access and safety rules apply to local and synced folders.

The monitor reads borrower documents and writes results to folders the user chooses. A session
reaches those folders when the user approves them through Cowork's folder picker. Folder connections
can belong to one session, so a new session may ask again. Treat that as routine.

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

If `credit-monitoring-output` contains data that is not a valid store, say it was left unchanged and offer:

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
- Never write outputs to a temporary/session folder or any folder the user did not choose.
- Never ask the user to type or paste a path.
- Never show a sandbox mount path or save an absolute host path in store data.
- Never call an unconnected folder empty.
- Never silently create `credit-monitoring-output-2` when the fixed child conflicts.
- Never send the guide on its own. Words first; the guide supports them.

Plain local source and output folders work without OneDrive, SharePoint, or Google Drive for desktop and are the recommended first-run choice.
