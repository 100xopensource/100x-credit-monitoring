# Files in a Claude Tag channel

A channel has no folder to attach or request. The borrower's documents and the monitoring store
live in one SharePoint site. A worker brings the files a step needs into a work area over the
channel's Microsoft 365 connection, runs the plugin's commands there, and saves the results back.
The thread session reads plugin files itself (Read, Glob, Grep) and runs no command.

## Where the files are

The channel's instructions name three things, and nothing in the plugin does:

- the SharePoint site, as `<host>:/sites/<name>`;
- the documents folder, which holds one folder per borrower;
- the monitoring folder, `credit-monitoring-output`.

Both folders are paths from the root of the site's document library. Copy all three into every
worker brief, word for word. Never ask the analyst for a path. When the channel's instructions name
none of them, tell the analyst credit monitoring isn't set up in this channel yet, change nothing,
and stop.

## The work area

Every worker begins its commands with these lines. The work area lasts as long as the thread's
session, so a later worker in the same thread finds what an earlier one brought down. A worker that
finds it missing brings the files down again, as **Bring the borrower's files down** says.

```bash
export CLAUDE_PLUGIN_ROOT="<the plugin folder>"
W="$HOME/credit-monitoring"
export CREDIT_MONITOR_SOURCE_ROOT="$W/source" CREDIT_MONITOR_STORE_ROOT="$W/store"
SP() { python "$CLAUDE_PLUGIN_ROOT/lib/sharepoint.py" --site "<site>" "$@"; }
export CREDIT_MONITOR_SOURCE_URL="$(SP --folder "<documents folder>" url)"
export CREDIT_MONITOR_STORE_URL="$(SP --folder "<monitoring folder>" url)"
```

The two addresses are what make each step in a memo's figure trace open its file in SharePoint.
Put them in every brief beside the two roots. When `url` fails, the run goes on and the memo names
those files without links.

`CLAUDE_PLUGIN_ROOT` is empty in channel Bash. The plugin folder is two folders above this file, and
the thread session puts its full path in the brief. Quote every path and every folder name: most
borrower names hold a space.

## Bring the borrower's files down

Before `check`, one worker brings down what a review of one borrower reads, and nothing more.

1. List the monitoring folder and its dashboard folder:

   ```bash
   SP --folder "<monitoring folder>" list
   SP --folder "<monitoring folder>" list borrowers
   SP --folder "<monitoring folder>" list artifacts/portfolio
   ```

   Match the borrower the request names to a folder under `borrowers`, the way **run-monitor**
   matches a name. That folder's name is the borrower key. No match, or several, is a question in
   the thread (`asking.md`).
2. Bring down the store's part, in one command, with the record that later saves compare against:

   ```bash
   SP --folder "<monitoring folder>" download --into "$W/store" --record "$W/read.json" \
     store-schema.json "borrowers/<borrower key>" artifacts/portfolio/Artifact
   ```

   Add `credit-monitoring-registry.json` and `artifacts/portfolio/hosted-artifact.json` when the
   listings show them. Never bring down another borrower's folder.
3. Read `$W/store/borrowers/<borrower key>/.record/current/config/borrower-config.json`. Each value
   under `paths` that starts with `source:` names a file or folder the run reads. Bring each down,
   the part after the colon, in one command:

   ```bash
   SP --folder "<documents folder>" download --into "$W/source" "<path>" "<path>" ...
   ```

The documents folder is only read. Nothing is ever saved into it.

## Save the results back

A run saves nothing before `manifest.py finish`. Then the worker that ran it saves the borrower's
folder once:

```bash
SP --folder "<monitoring folder>" upload --from "$W/store" --changed-since "$W/read.json" \
  "borrowers/<borrower key>"
```

It saves only the files the run added or changed, never a half-written temporary file, and each
save is read back before it counts. The borrower's own records go last. The answer lists only the
files it saved, so a file the run left as it was, often the workbook the analyst edited, has no
line there. Take the links from the borrower's folder after the save:

```bash
SP --folder "<monitoring folder>" list "borrowers/<borrower key>"
```

Each file in the listing carries its `web_url`, the link that opens it in the browser. Return the
`web_url` of the memo and of the workbook, the two files at the top of the borrower's folder, to the
thread session. Never link a copy under `.record/`. Those copies are the store's own records, and an
edit to one makes the next run stop.

After the analyst's answer to the delivery question, the worker that records it saves again with the
same command, adding `artifacts/portfolio` when the dashboard changed (`delivery.md`,
`dashboard.md`). When the analyst makes a draft the month's review or discards it before a run
(**A draft nobody chose** in `asking.md`), the worker that runs `select` or `discard` saves the same
way before the run starts.

The channel never deletes or renames a file in SharePoint. A file a command removes in the work
area, such as the reviewed-edit record or an older result in `.record/current/`, stays in
SharePoint, and the store check accepts it there.

## When a file can't be reached or saved

`sharepoint.py` exits 1 and names what failed. Give the analyst the plain words below, never the
command's text, and change nothing more.

- **Refused by the connection.** The message says only an admin can change the setting.

  > "I can't reach {Borrower}'s folder in SharePoint from this channel yet. That's a setting only an
  > admin can change. I stopped without changing your files."

- **Saved in SharePoint since this run read it.** Someone saved one of the files while the run
  worked, and nothing was saved over it. A file under `artifacts/portfolio` is the dashboard's:
  follow `dashboard.md` instead.

  > "Someone saved {file name} in {Borrower}'s folder while I was working, so I didn't save over it.
  > Ask again and I'll start from the new copy."

- **The copy read back is not the file sent.** Name the file, never say it was saved, and hand
  the files over in the thread all the same (`delivery.md`).
- **Not found.** A file the borrower's settings name is missing from their folder. Name it as
  **When something is missing but the work can carry on** in `../../asking.md` says.

A folder that could not be listed is not empty. Say it could not be reached.

## Paths a worker uses

- **Read, Grep, Glob, Edit, Write and bash** take the work-area path, `$W/store/...` or
  `$W/source/...`, written out in full in the brief.
- **Engine arguments** take the string from the execution config, `source:...` or `store:...`, as
  in any runtime.

Never show a work-area path in the thread. Name a file by its own name, and link it with its
`web_url` from a listing of its folder.
